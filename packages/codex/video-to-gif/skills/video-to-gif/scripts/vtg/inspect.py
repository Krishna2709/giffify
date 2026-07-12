"""Source media inspection via ffprobe (spec FR-002, FR-003, SEC-010).

ffprobe is always invoked with ``-protocol_whitelist file,pipe`` and any
detected reference-following container (HLS/DASH/concat and similar) is rejected
as UNSUPPORTED_MEDIA_CONTAINER before any decoding happens.
"""

from __future__ import annotations

import json
import os
import subprocess
from fractions import Fraction
from typing import Any

from . import errors
from .models import SourceInfo

# Container/demuxer names that can follow references to other resources
# (including network URLs). Detected in ffprobe's format_name (SEC-010).
_REFERENCE_FOLLOWING = {
    "hls",
    "applehttp",
    "dash",
    "concat",
    "sdp",
    "rtsp",
    "rtp",
    "mpegts_hls",
    "webvtt",
    "m3u8",
}

PROBE_ENTRIES = (
    "format=format_name,duration:"
    "stream=index,codec_type,codec_name,width,height,avg_frame_rate,"
    "r_frame_rate,duration,disposition:"
    "stream_tags=rotate:"
    "stream_side_data=rotation,displaymatrix"
)


# Reference-following container signatures for content-based detection. These
# formats can pull in additional (possibly network) resources, so we detect them
# by extension and content before FFmpeg/ffprobe ever opens the file (SEC-010).
_REF_EXTENSIONS = {
    ".m3u8": "hls",
    ".m3u": "hls",
    ".mpd": "dash",
    ".sdp": "sdp",
}


def sniff_reference_container(path: str) -> str | None:
    """Detect a reference-following container by extension/content (SEC-010).

    Returns the format token (e.g. ``"hls"``) when detected, else ``None``.
    Reads only a small header; never opens referenced resources.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in _REF_EXTENSIONS:
        return _REF_EXTENSIONS[ext]
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    text = head.decode("utf-8", "ignore").lstrip("﻿ \t\r\n")
    lowered = text.lower()
    if text.startswith("#EXTM3U"):
        return "hls"
    if "<mpd" in lowered or "urn:mpeg:dash" in lowered:
        return "dash"
    if text.startswith("ffconcat"):
        return "concat"
    for line in text.splitlines()[:10]:
        stripped = line.strip()
        if stripped.startswith(("file '", 'file "', "file /")):
            return "concat"
    if text.startswith("v=0") and "m=" in lowered:
        return "sdp"
    return None


def _raise_reference_container(token: str) -> None:
    raise errors.EngineError(
        errors.UNSUPPORTED_MEDIA_CONTAINER,
        f"Refusing reference-following container ({token}). These formats can "
        "pull in external or network resources and are not supported in version "
        "0.1.0.",
        exit_code=errors.EXIT_INVALID_MEDIA,
        status=errors.STATUS_FAILED,
        stage="inspect",
        remediation="Provide a self-contained local video file.",
    )


def build_ffprobe_command(ffprobe: str, source: str) -> list[str]:
    """Build the ffprobe inspection command (JSON output, protocol-restricted)."""
    return [
        ffprobe,
        "-hide_banner",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        source,
    ]


def run_ffprobe(ffprobe: str, source: str, *, timeout: float = 60.0) -> dict[str, Any]:
    cmd = build_ffprobe_command(ffprobe, source)
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise errors.EngineError(
            errors.UNSUPPORTED_MEDIA,
            "ffprobe timed out while inspecting the source.",
            exit_code=errors.EXIT_INVALID_MEDIA,
            status=errors.STATUS_FAILED,
            stage="inspect",
        ) from exc
    except OSError as exc:
        raise errors.EngineError(
            errors.DEPENDENCY_MISSING,
            f"Failed to execute ffprobe: {exc}",
            exit_code=errors.EXIT_DEPENDENCY_MISSING,
            status=errors.STATUS_DEPENDENCY_MISSING,
            stage="inspect",
        ) from exc
    if proc.returncode != 0:
        raise errors.EngineError(
            errors.UNSUPPORTED_MEDIA,
            f"ffprobe could not read the source: {proc.stderr.strip()[:400]}",
            exit_code=errors.EXIT_INVALID_MEDIA,
            status=errors.STATUS_FAILED,
            stage="inspect",
            remediation="Confirm the file is a valid, supported video.",
        )
    try:
        return json.loads(proc.stdout)  # type: ignore[no-any-return]  # ffprobe JSON is Any
    except json.JSONDecodeError as exc:
        raise errors.EngineError(
            errors.UNSUPPORTED_MEDIA,
            "ffprobe returned output that could not be parsed as JSON.",
            exit_code=errors.EXIT_INVALID_MEDIA,
            status=errors.STATUS_FAILED,
            stage="inspect",
        ) from exc


def _reject_reference_container(format_name: str) -> None:
    tokens = {t.strip().lower() for t in format_name.split(",") if t.strip()}
    hostile = tokens & _REFERENCE_FOLLOWING
    if hostile:
        _raise_reference_container(", ".join(sorted(hostile)))


def _parse_fps(stream: dict[str, Any]) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = stream.get(key)
        if val and val not in ("0/0", "N/A"):
            try:
                frac = Fraction(val)
                if frac > 0:
                    return float(frac)
            except (ValueError, ZeroDivisionError):
                continue
    return 0.0


def _duration_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return round(seconds * 1000)


def _rotation(stream: dict[str, Any]) -> int:
    # tags.rotate (older) or side_data_list displaymatrix rotation (newer).
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(float(tags["rotate"])) % 360
        except (TypeError, ValueError):
            pass
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            try:
                # ffmpeg reports displaymatrix rotation in degrees (may be negative).
                return round(float(side["rotation"])) % 360
            except (TypeError, ValueError):
                continue
    return 0


def _is_thumbnail(stream: dict[str, Any]) -> bool:
    disp = stream.get("disposition") or {}
    if disp.get("attached_pic") == 1:
        return True
    # Image codecs used as cover art with no real frame rate. Only treat as a
    # thumbnail if flagged; a real MJPEG video has fps.
    return (
        stream.get("codec_name") in ("mjpeg", "png", "bmp", "gif")
        and _parse_fps(stream) == 0.0
        and disp.get("attached_pic") == 1
    )


def select_video_stream(streams: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Select the video stream per FR-003. Returns (stream, warnings)."""
    warnings: list[str] = []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        raise errors.EngineError(
            errors.NO_VIDEO_STREAM,
            "No video stream found in the source.",
            exit_code=errors.EXIT_INVALID_MEDIA,
            status=errors.STATUS_FAILED,
            stage="inspect",
            remediation="Provide a file that contains a video stream.",
        )

    non_thumb = [s for s in video_streams if not _is_thumbnail(s)]
    candidates = non_thumb or video_streams
    if len(candidates) == 1:
        return candidates[0], warnings

    # Multiple candidates: prefer a stream marked default.
    default_streams = [s for s in candidates if (s.get("disposition") or {}).get("default") == 1]
    if len(default_streams) == 1:
        warnings.append("Multiple video streams present; selected the default-flagged stream.")
        return default_streams[0], warnings

    # Ambiguous — the agent must ask which stream to use.
    indices = [s.get("index") for s in candidates]
    raise errors.EngineError(
        errors.AMBIGUOUS_VIDEO_STREAM,
        f"Multiple candidate video streams found (indices {indices}); cannot choose automatically.",
        exit_code=errors.EXIT_INVALID_MEDIA,
        status=errors.STATUS_VALIDATION_FAILED,
        stage="inspect",
        remediation="Specify which video stream to use.",
        details={"candidateStreamIndices": indices},
    )


def inspect_source(ffprobe: str, source: str, *, timeout: float = 60.0) -> SourceInfo:
    """Inspect a source and return a :class:`SourceInfo` (FR-002/FR-003/SEC-010)."""
    # Detect reference-following containers before ffprobe runs, so a hostile
    # playlist never reaches FFmpeg at all (defense in depth for SEC-010).
    token = sniff_reference_container(source)
    if token:
        _raise_reference_container(token)

    data = run_ffprobe(ffprobe, source, timeout=timeout)

    fmt = data.get("format") or {}
    format_name = fmt.get("format_name", "") or ""
    _reject_reference_container(format_name)

    streams = data.get("streams") or []
    stream, warnings = select_video_stream(streams)

    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise errors.EngineError(
            errors.UNSUPPORTED_MEDIA,
            "Video stream has invalid dimensions.",
            exit_code=errors.EXIT_INVALID_MEDIA,
            status=errors.STATUS_FAILED,
            stage="inspect",
        )

    # SEC-011: reject implausibly large sources before decoding.
    if width > 16384 or height > 16384 or (width * height) > 200_000_000:
        raise errors.EngineError(
            errors.RESOURCE_LIMIT_EXCEEDED,
            f"Source dimensions {width}x{height} exceed the safe processing limit.",
            exit_code=errors.EXIT_RESOURCE_LIMIT,
            status=errors.STATUS_FAILED,
            stage="inspect",
            remediation="Downscale the source before converting.",
        )

    fps = _parse_fps(stream)
    codec = stream.get("codec_name", "unknown")
    stream_index = int(stream.get("index") or 0)
    rotation = _rotation(stream)

    container_ms = _duration_ms(fmt.get("duration"))
    stream_ms = _duration_ms(stream.get("duration"))

    # Prefer the valid video-stream duration when they disagree (FR-002).
    duration_ms = stream_ms or container_ms
    if container_ms and stream_ms and abs(container_ms - stream_ms) > 1000:
        warnings.append(
            f"Container duration ({container_ms} ms) and stream duration "
            f"({stream_ms} ms) disagree; using the video-stream duration."
        )
        duration_ms = stream_ms
    if not duration_ms:
        raise errors.EngineError(
            errors.UNSUPPORTED_MEDIA,
            "Could not determine source duration.",
            exit_code=errors.EXIT_INVALID_MEDIA,
            status=errors.STATUS_FAILED,
            stage="inspect",
            remediation="The file may be corrupt or use an unsupported container.",
        )

    # Display dimensions account for rotation (90/270 swap w/h).
    if rotation in (90, 270):
        display_w, display_h = height, width
    else:
        display_w, display_h = width, height

    return SourceInfo(
        path=source,
        duration_ms=duration_ms,
        width=width,
        height=height,
        display_width=display_w,
        display_height=display_h,
        fps=fps,
        codec=codec,
        stream_index=stream_index,
        rotation=rotation,
        container_duration_ms=container_ms,
        stream_duration_ms=stream_ms,
        disposition=stream.get("disposition") or {},
        format_name=format_name,
        warnings=warnings,
    )
