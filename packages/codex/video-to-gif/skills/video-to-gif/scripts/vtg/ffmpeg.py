"""FFmpeg two-pass GIF conversion pipeline (spec section 15, 16, SEC-010/011).

palettegen -> paletteuse with lanczos scaling. Every clip is written to an
unpredictable temporary file, verified to be a non-empty GIF, and atomically
moved into place. Per-clip wall-clock timeouts and a temp-disk ceiling are
enforced; on breach or cancellation the FFmpeg process group is terminated and
partial artifacts are removed.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from . import cleanup, errors, transforms
from .models import EffectiveSettings, LoopValue, SourceInfo, loop_to_ffmpeg
from .progress import NULL_REPORTER, ProgressReporter
from .timestamps import format_hhmmss, seconds_str
from .transforms import CropRect

_GRACE_SECONDS = 5.0
_POLL_INTERVAL = 0.2

# Temp-artifact deletion (bounded retry to absorb Windows handle-release lag)
# lives in vtg.cleanup so vtg.remote can share the exact same implementation for
# partial-download cleanup (spec section 16). The module-level aliases below keep
# the historical names/behavior for any in-tree callers.
_CLEANUP_RETRY_SECONDS = cleanup.CLEANUP_RETRY_SECONDS
_CLEANUP_RETRY_INTERVAL = cleanup.CLEANUP_RETRY_INTERVAL


def resolve_effective_settings(
    source: SourceInfo,
    *,
    max_width: int | None,
    target_fps: int | None,
    colors: int | None,
    loop: LoopValue,
    allow_upscale: bool,
    profile_name: str,
    crop: CropRect | None = None,
    explicit_width: int | None = None,
    explicit_height: int | None = None,
    speed: Decimal | None = None,
    dither: str | None = None,
    bayer_scale: int | None = None,
    warnings: list[str] | None = None,
) -> EffectiveSettings:
    """Compute deterministic output dimensions/fps/colors (FR-014, FR-024..028).

    ``max_width`` is the quality profile's maximum width and applies only when
    neither ``explicit_width`` nor ``explicit_height`` is supplied; an explicit
    bound overrides it (FR-026). ``crop`` supplies the effective source geometry
    for aspect ratio, the width cap, and the no-upscale rule (FR-025). Any
    UPSCALE_NOT_ALLOWED warning is appended to ``warnings``.
    """
    disp_w = source.display_width
    disp_h = source.display_height
    eff_speed = transforms.DEFAULT_SPEED if speed is None else speed

    # The cropped rectangle becomes the effective source geometry (FR-025).
    if crop is not None:
        transforms.validate_crop_bounds(crop, disp_w, disp_h)
        eff_w, eff_h = crop.width, crop.height
    else:
        eff_w, eff_h = disp_w, disp_h

    # Fallbacks for custom profiles missing a value.
    if target_fps is None:
        target_fps = 15
    if colors is None:
        colors = 256
    colors = max(2, min(256, int(colors)))

    dims = transforms.resolve_output_dimensions(
        eff_w,
        eff_h,
        width=explicit_width,
        height=explicit_height,
        profile_max_width=max_width,
        allow_upscale=allow_upscale,
    )
    if dims.warning is not None and warnings is not None and dims.warning not in warnings:
        warnings.append(dims.warning)

    # Effective fps must not exceed the source frame rate (FR-014); below 1.0x
    # the ceiling is the retimed stream's intrinsic rate (FR-027).
    effective_fps: float = float(target_fps)
    if source.fps and source.fps > 0:
        fps_ceiling = transforms.effective_source_fps(source.fps, eff_speed)
        effective_fps = min(float(target_fps), fps_ceiling)
    # Normalize to a clean number where possible.
    if abs(effective_fps - round(effective_fps)) < 1e-6:
        effective_fps = float(round(effective_fps))

    mode, scale = transforms.resolve_dither(
        dither=dither, bayer_scale=bayer_scale, profile_name=profile_name
    )

    return EffectiveSettings(
        width=dims.width,
        height=dims.height,
        fps=effective_fps,
        colors=colors,
        loop=loop,
        profile_name=profile_name,
        crop=crop,
        speed=eff_speed,
        dither=mode,
        bayer_scale=scale,
        upscaled=dims.upscaled,
        source_width=disp_w,
        source_height=disp_h,
        effective_source_width=eff_w,
        effective_source_height=eff_h,
    )


# Retained alias: the frame-rate serializer now lives in vtg.transforms so the
# filter chain is built in exactly one place (SEC-018).
_fps_arg = transforms.fps_arg


def _chain(settings: EffectiveSettings) -> str:
    """The shared crop/setpts/fps/scale chain for both palette passes (15.2)."""
    return transforms.build_filter_chain(
        crop=settings.crop,
        speed=settings.speed,
        fps=settings.fps,
        width=settings.width,
        height=settings.height,
    )


def build_palettegen_command(
    ffmpeg: str,
    source: str,
    start_ms: int,
    duration_ms: int,
    settings: EffectiveSettings,
    palette_path: str,
) -> list[str]:
    vf = f"{_chain(settings)},palettegen=max_colors={settings.colors}:stats_mode=diff"
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-ss",
        format_hhmmss(start_ms),
        "-t",
        seconds_str(duration_ms),
        "-i",
        source,
        "-an",
        "-vf",
        vf,
        "-frames:v",
        "1",
        palette_path,
    ]


def build_paletteuse_command(
    ffmpeg: str,
    source: str,
    start_ms: int,
    duration_ms: int,
    settings: EffectiveSettings,
    palette_path: str,
    out_path: str,
) -> list[str]:
    mode, scale = settings.effective_dither
    dither = transforms.dither_filter_arg(mode, scale)
    # Steps 4-7 are byte-identical to the palettegen pass so the palette is
    # derived from exactly the frames that are encoded (SEC-018, section 15.2).
    lavfi = f"{_chain(settings)}[x];[x][1:v]paletteuse=dither={dither}"
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-ss",
        format_hhmmss(start_ms),
        "-t",
        seconds_str(duration_ms),
        "-i",
        source,
        "-i",
        palette_path,
        "-lavfi",
        lavfi,
        "-an",
        "-loop",
        str(loop_to_ffmpeg(settings.loop)),
        "-f",
        "gif",
        out_path,
    ]


def build_preview_command(
    ffmpeg: str,
    source: str,
    at_ms: int,
    settings: EffectiveSettings,
    out_path: str,
) -> list[str]:
    """Build the single-frame PNG extraction command (FR-029, section 15.2).

    Uses steps 1-4 and 7 only: seek, decode, orientation normalization, crop,
    and scale. No frame-rate conversion, no retiming, and no palette pass, so
    the still is full colour and never palette-quantized.
    """
    vf = transforms.build_preview_filter_chain(
        crop=settings.crop, width=settings.width, height=settings.height
    )
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-ss",
        format_hhmmss(at_ms),
        "-i",
        source,
        "-an",
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-update",
        "1",
        "-pix_fmt",
        "rgb24",
        "-c:v",
        "png",
        "-f",
        "image2",
        out_path,
    ]


@dataclass
class ConversionResult:
    path: str
    size_bytes: int
    width: int
    height: int
    fps: float


def _popen_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "text": True,
        # FFmpeg writes UTF-8 diagnostics that quote the input path verbatim, so
        # a CJK/Cyrillic/emoji filename would fail to decode under the locale
        # default on Windows. Decode as UTF-8 and never raise (section 13.5).
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True  # own process group for group signals
    elif os.name == "nt":  # pragma: no cover - platform specific
        # CREATE_NEW_PROCESS_GROUP exists only in the Windows subprocess stub.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    return kwargs


def _terminate_group(proc: subprocess.Popen) -> None:
    """Terminate the FFmpeg process group gracefully, then force-kill (section 16)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:  # pragma: no cover - platform specific
            proc.terminate()
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - platform specific
            proc.kill()
    except (ProcessLookupError, OSError):
        pass
    with contextlib.suppress(subprocess.TimeoutExpired):  # pragma: no cover
        proc.wait(timeout=_GRACE_SECONDS)


def _dir_size(paths: list[str]) -> int:
    total = 0
    for p in paths:
        try:
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for name in files:
                        with contextlib.suppress(OSError):
                            total += os.path.getsize(os.path.join(root, name))
            elif os.path.exists(p):
                total += os.path.getsize(p)
        except OSError:
            pass
    return total


# Thin aliases onto the shared implementation in vtg.cleanup (see the note near
# the retry constants above). Kept so existing references stay valid.
_remove_path = cleanup.remove_path
_remove_paths = cleanup.remove_paths


def run_guarded(
    cmd: list[str],
    *,
    stage: str,
    clip_index: int,
    timeout_seconds: float,
    temp_paths: list[str],
    max_temp_bytes: int,
    cancel_event: threading.Event | None,
) -> None:
    """Run an FFmpeg command with timeout, temp ceiling, and cancellation.

    Raises CancelledError on cancel, RESOURCE_LIMIT_EXCEEDED on timeout/disk
    breach, or FFMPEG_FAILED on a non-zero exit.
    """
    start = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, **_popen_kwargs())
    except OSError as exc:
        raise errors.EngineError(
            errors.DEPENDENCY_MISSING,
            f"Failed to execute ffmpeg: {exc}",
            exit_code=errors.EXIT_DEPENDENCY_MISSING,
            status=errors.STATUS_DEPENDENCY_MISSING,
            stage=stage,
            clip_index=clip_index,
        ) from exc

    breach: str | None = None
    while True:
        if proc.poll() is not None:
            break
        if cancel_event is not None and cancel_event.is_set():
            breach = "cancelled"
            break
        elapsed = time.monotonic() - start
        if elapsed > timeout_seconds:
            breach = "timeout"
            break
        if _dir_size(temp_paths) > max_temp_bytes:
            breach = "disk"
            break
        time.sleep(_POLL_INTERVAL)

    if breach is not None:
        try:
            _terminate_group(proc)
            if breach == "cancelled":
                raise errors.CancelledError(stage=stage, clip_index=clip_index)
            if breach == "timeout":
                raise errors.EngineError(
                    errors.RESOURCE_LIMIT_EXCEEDED,
                    f"Clip exceeded the {timeout_seconds:g}s processing limit and was terminated.",
                    exit_code=errors.EXIT_RESOURCE_LIMIT,
                    status=errors.STATUS_FAILED,
                    stage=stage,
                    clip_index=clip_index,
                    remediation="Increase limits.maxClipProcessingSeconds or shorten the clip.",
                )
            raise errors.EngineError(
                errors.RESOURCE_LIMIT_EXCEEDED,
                f"Clip exceeded the temporary-disk ceiling ({max_temp_bytes} bytes) "
                "and was terminated.",
                exit_code=errors.EXIT_RESOURCE_LIMIT,
                status=errors.STATUS_FAILED,
                stage=stage,
                clip_index=clip_index,
                remediation="Increase limits.maxTemporaryBytes or reduce clip size/quality.",
            )
        finally:
            # The process group was terminated without draining its pipes; close
            # them so the interpreter does not emit a ResourceWarning in-process.
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    with contextlib.suppress(OSError):
                        pipe.close()

    stderr = ""
    try:
        _out, stderr = proc.communicate(timeout=_GRACE_SECONDS)
    except subprocess.TimeoutExpired:  # pragma: no cover
        _terminate_group(proc)
        _out, stderr = proc.communicate()

    if proc.returncode != 0:
        raise errors.EngineError(
            errors.FFMPEG_FAILED,
            f"FFmpeg exited with status {proc.returncode} during {stage}: "
            f"{(stderr or '').strip()[:400]}",
            exit_code=errors.EXIT_FFMPEG_FAILED,
            status=errors.STATUS_FAILED,
            stage=stage,
            clip_index=clip_index,
            remediation="Check that the source is valid and the range is in range.",
        )


def _verify_output(path: str, *, magic: bytes, label: str, stage: str) -> int:
    if not os.path.exists(path):
        raise errors.EngineError(
            errors.FFMPEG_FAILED,
            "FFmpeg did not produce an output file.",
            exit_code=errors.EXIT_FFMPEG_FAILED,
            status=errors.STATUS_FAILED,
            stage=stage,
        )
    size = os.path.getsize(path)
    if size <= 0:
        raise errors.EngineError(
            errors.FFMPEG_FAILED,
            "FFmpeg produced an empty output file.",
            exit_code=errors.EXIT_FFMPEG_FAILED,
            status=errors.STATUS_FAILED,
            stage=stage,
        )
    with open(path, "rb") as fh:
        header = fh.read(len(magic))
    if not header.startswith(magic):
        raise errors.EngineError(
            errors.FFMPEG_FAILED,
            f"Output file is not a valid {label}.",
            exit_code=errors.EXIT_FFMPEG_FAILED,
            status=errors.STATUS_FAILED,
            stage=stage,
        )
    return size


def _verify_gif(path: str) -> int:
    return _verify_output(path, magic=b"GIF8", label="GIF", stage="encode")


def _verify_png(path: str) -> int:
    """Verify a non-empty PNG (section 15.2 step 11, adapted for FR-029)."""
    return _verify_output(path, magic=b"\x89PNG\r\n\x1a\n", label="PNG", stage="preview")


def convert_clip(
    ffmpeg: str,
    source: SourceInfo,
    *,
    start_ms: int,
    duration_ms: int,
    settings: EffectiveSettings,
    dest_path: str,
    output_dir: str,
    timeout_seconds: float,
    max_temp_bytes: int,
    keep_temporary_files: bool = False,
    clip_index: int = 0,
    cancel_event: threading.Event | None = None,
    reporter: ProgressReporter = NULL_REPORTER,
) -> ConversionResult:
    """Run the full two-pass conversion for one clip (section 15.2/15.3)."""
    temp_dir = tempfile.mkdtemp(prefix="vtg-")
    palette_path = os.path.join(temp_dir, "palette.png")
    # Temp GIF lives in the output directory to guarantee same-filesystem
    # atomic replacement (section 15.3). Unpredictable name (section 16).
    fd, temp_out = tempfile.mkstemp(prefix=".vtg-", suffix=".gif.tmp", dir=output_dir)
    os.close(fd)

    temp_paths = [temp_dir, temp_out]

    def _cleanup() -> None:
        # Cleanup runs only after run_guarded has terminated AND waited for the
        # FFmpeg process (breach/cancel via _terminate_group, failure via
        # communicate()), so the OS has begun releasing its file handles. The
        # in-place temp GIF and the palette temp dir are then removed with a
        # bounded retry to absorb Windows' non-synchronous handle release.
        if keep_temporary_files:
            return
        _remove_paths([temp_out, temp_dir])

    try:
        reporter.stage_progress(clip_index, "palette", 0.0)
        run_guarded(
            build_palettegen_command(
                ffmpeg, source.path, start_ms, duration_ms, settings, palette_path
            ),
            stage="palette",
            clip_index=clip_index,
            timeout_seconds=timeout_seconds,
            temp_paths=temp_paths,
            max_temp_bytes=max_temp_bytes,
            cancel_event=cancel_event,
        )
        reporter.stage_progress(clip_index, "palette", 100.0)

        reporter.stage_progress(clip_index, "encode", 0.0)
        run_guarded(
            build_paletteuse_command(
                ffmpeg, source.path, start_ms, duration_ms, settings, palette_path, temp_out
            ),
            stage="encode",
            clip_index=clip_index,
            timeout_seconds=timeout_seconds,
            temp_paths=temp_paths,
            max_temp_bytes=max_temp_bytes,
            cancel_event=cancel_event,
        )
        size = _verify_gif(temp_out)
        reporter.stage_progress(clip_index, "encode", 100.0)

        # Atomic move to destination (same filesystem).
        os.replace(temp_out, dest_path)
    except BaseException:
        _cleanup()
        raise
    else:
        # Success: remove the palette temp dir (temp_out already moved into
        # place). The same bounded retry keeps this robust on Windows.
        if not keep_temporary_files:
            _remove_paths([temp_dir])

    return ConversionResult(
        path=dest_path,
        size_bytes=size,
        width=settings.width,
        height=settings.height,
        fps=settings.fps,
    )


@dataclass
class PreviewResult:
    path: str
    size_bytes: int
    width: int
    height: int
    at_ms: int


def extract_preview(
    ffmpeg: str,
    source: SourceInfo,
    *,
    at_ms: int,
    settings: EffectiveSettings,
    dest_path: str,
    output_dir: str,
    timeout_seconds: float,
    max_temp_bytes: int,
    keep_temporary_files: bool = False,
    clip_index: int = 0,
    cancel_event: threading.Event | None = None,
    reporter: ProgressReporter = NULL_REPORTER,
) -> PreviewResult:
    """Extract one full-colour PNG still (FR-029, sections 15.2/15.3).

    Temporary output, verification, atomic move, cancellation, cleanup, and the
    resource limits of SEC-011 all apply exactly as they do for a GIF.
    """
    fd, temp_out = tempfile.mkstemp(prefix=".vtg-", suffix=".png.tmp", dir=output_dir)
    os.close(fd)
    temp_paths = [temp_out]

    try:
        reporter.stage_progress(clip_index, "preview", 0.0)
        run_guarded(
            build_preview_command(ffmpeg, source.path, at_ms, settings, temp_out),
            stage="preview",
            clip_index=clip_index,
            timeout_seconds=timeout_seconds,
            temp_paths=temp_paths,
            max_temp_bytes=max_temp_bytes,
            cancel_event=cancel_event,
        )
        size = _verify_png(temp_out)
        reporter.stage_progress(clip_index, "preview", 100.0)
        os.replace(temp_out, dest_path)
    except BaseException:
        if not keep_temporary_files:
            _remove_paths([temp_out])
        raise

    return PreviewResult(
        path=dest_path,
        size_bytes=size,
        width=settings.width,
        height=settings.height,
        at_ms=at_ms,
    )
