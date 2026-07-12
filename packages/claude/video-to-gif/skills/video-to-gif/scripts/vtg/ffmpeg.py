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
from typing import Any

from . import cleanup, errors
from .models import EffectiveSettings, LoopValue, SourceInfo, loop_to_ffmpeg
from .progress import NULL_REPORTER, ProgressReporter
from .timestamps import format_hhmmss, seconds_str

# Dithering per profile (section 15.5). balanced/high use FFmpeg's default
# (sierra2_4a); small uses a compression-oriented ordered dither.
_DITHER_BY_PROFILE = {
    "small": "bayer:bayer_scale=5",
    "balanced": "sierra2_4a",
    "high": "sierra2_4a",
}
_DEFAULT_DITHER = "sierra2_4a"

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
) -> EffectiveSettings:
    """Compute deterministic output dimensions/fps/colors (FR-014)."""
    disp_w = source.display_width
    disp_h = source.display_height

    # Fallbacks for custom profiles missing a value.
    if max_width is None:
        max_width = disp_w  # no cap -> keep source width (no upscale)
    if target_fps is None:
        target_fps = 15
    if colors is None:
        colors = 256
    colors = max(2, min(256, int(colors)))

    out_w = int(max_width) if allow_upscale else min(disp_w, int(max_width))
    out_w = max(1, out_w)
    out_h = max(1, round(disp_h * out_w / disp_w))

    # Effective fps must not exceed the source frame rate (FR-014).
    effective_fps: float = float(target_fps)
    if source.fps and source.fps > 0:
        effective_fps = min(float(target_fps), source.fps)
    # Normalize to a clean number where possible.
    if abs(effective_fps - round(effective_fps)) < 1e-6:
        effective_fps = float(round(effective_fps))

    return EffectiveSettings(
        width=out_w,
        height=out_h,
        fps=effective_fps,
        colors=colors,
        loop=loop,
        profile_name=profile_name,
    )


def _fps_arg(fps: float) -> str:
    if abs(fps - round(fps)) < 1e-6:
        return str(round(fps))
    return f"{fps:.5f}".rstrip("0").rstrip(".")


def build_palettegen_command(
    ffmpeg: str,
    source: str,
    start_ms: int,
    duration_ms: int,
    settings: EffectiveSettings,
    palette_path: str,
) -> list[str]:
    vf = (
        f"fps={_fps_arg(settings.fps)},"
        f"scale={settings.width}:{settings.height}:flags=lanczos,"
        f"palettegen=max_colors={settings.colors}:stats_mode=diff"
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
    dither = _DITHER_BY_PROFILE.get(settings.profile_name, _DEFAULT_DITHER)
    lavfi = (
        f"fps={_fps_arg(settings.fps)},"
        f"scale={settings.width}:{settings.height}:flags=lanczos[x];"
        f"[x][1:v]paletteuse=dither={dither}"
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


def _verify_gif(path: str) -> int:
    if not os.path.exists(path):
        raise errors.EngineError(
            errors.FFMPEG_FAILED,
            "FFmpeg did not produce an output file.",
            exit_code=errors.EXIT_FFMPEG_FAILED,
            status=errors.STATUS_FAILED,
            stage="encode",
        )
    size = os.path.getsize(path)
    if size <= 0:
        raise errors.EngineError(
            errors.FFMPEG_FAILED,
            "FFmpeg produced an empty output file.",
            exit_code=errors.EXIT_FFMPEG_FAILED,
            status=errors.STATUS_FAILED,
            stage="encode",
        )
    with open(path, "rb") as fh:
        header = fh.read(6)
    if not header.startswith(b"GIF8"):
        raise errors.EngineError(
            errors.FFMPEG_FAILED,
            "Output file is not a valid GIF.",
            exit_code=errors.EXIT_FFMPEG_FAILED,
            status=errors.STATUS_FAILED,
            stage="encode",
        )
    return size


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
