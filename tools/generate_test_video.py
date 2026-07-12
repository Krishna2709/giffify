#!/usr/bin/env python3
"""Synthetic test-media generator for the video-to-gif test suites.

Standard-library only. Wraps the real ``ffmpeg`` executable (never ``shell=True``,
always argument arrays) to produce small, deterministic synthetic clips for the
integration, security, and acceptance suites. No media file is ever committed to
the repository: callers generate into a temp directory at test time.

Capabilities (spec section 22.2 test-media catalogue):
  * Constant-color video and ``testsrc``/``testsrc2`` moving-pattern sources.
  * Portrait and landscape geometries.
  * With-audio and no-audio variants.
  * Multi-stream (two video streams) sources.
  * Rotation metadata via a display matrix (``-display_rotation``).
  * Corrupted / truncated files and zero-byte files.
  * Arbitrary output paths, including names with spaces and Unicode.

Every builder is parameterized (duration, size, fps, output path), importable,
and usable from the command line::

    python tools/generate_test_video.py landscape /tmp/out.mp4 --duration 2 --size 320x240 --fps 15
    python tools/generate_test_video.py rotated   /tmp/rot.mp4 --rotation 90
    python tools/generate_test_video.py --list
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any

# Common homebrew location, used as a fallback when ffmpeg is not on PATH.
_FALLBACK_BIN = "/opt/homebrew/bin"


class FFmpegUnavailable(RuntimeError):
    """Raised when no usable ffmpeg executable can be located."""


def find_ffmpeg() -> str:
    """Locate a real ffmpeg executable (honours the ``VTG_FFMPEG`` override)."""
    override = os.environ.get("VTG_FFMPEG")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidate = os.path.join(_FALLBACK_BIN, "ffmpeg")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    raise FFmpegUnavailable("ffmpeg executable not found on PATH")


def _run(args: list[str]) -> None:
    """Run an ffmpeg command as an argument array (never shell=True)."""
    proc = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}) for {args!r}:\n{proc.stderr[-800:]}")


def _lavfi_source(source: str, size: str, fps: int, duration: float) -> str:
    """Build a lavfi source description string.

    ``source`` is one of ``testsrc``, ``testsrc2`` (moving patterns / rapid scene
    changes), or ``color=<name>`` for a constant color.
    """
    if source.startswith("color"):
        # e.g. "color=red" -> color=c=red:...
        _, _, name = source.partition("=")
        name = name or "red"
        return f"color=c={name}:size={size}:rate={fps}:duration={duration}"
    return f"{source}=size={size}:rate={fps}:duration={duration}"


def generate_video(
    path: str,
    *,
    duration: float = 2.0,
    size: str = "320x240",
    fps: int = 15,
    source: str = "testsrc",
    with_audio: bool = False,
    codec: str = "libx264",
) -> str:
    """Generate a single-stream video at ``path`` and return ``path``.

    Deterministic given identical parameters and ffmpeg build.
    """
    ff = find_ffmpeg()
    args: list[str] = [ff, "-y", "-hide_banner", "-v", "error", "-nostdin"]
    args += ["-f", "lavfi", "-i", _lavfi_source(source, size, fps, duration)]
    maps = ["-map", "0:v"]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
        maps += ["-map", "1:a"]
    args += maps
    args += ["-c:v", codec, "-pix_fmt", "yuv420p"]
    if with_audio:
        args += ["-c:a", "aac", "-shortest"]
    args += ["-t", f"{duration}", path]
    _run(args)
    return path


def generate_landscape(path: str, *, size: str = "640x360", **kw: Any) -> str:
    """Landscape (wider than tall) video."""
    return generate_video(path, size=size, **kw)


def generate_portrait(path: str, *, size: str = "240x320", **kw: Any) -> str:
    """Portrait (taller than wide) video encoded natively at that geometry."""
    return generate_video(path, size=size, **kw)


def generate_with_audio(path: str, **kw: Any) -> str:
    """Video that carries an audio stream (used to prove GIF output is silent)."""
    return generate_video(path, with_audio=True, **kw)


def generate_constant_color(path: str, *, color: str = "red", **kw: Any) -> str:
    """Constant-color video (spec 22.2 'constant-color video')."""
    return generate_video(path, source=f"color={color}", **kw)


def generate_multistream(
    path: str,
    *,
    duration: float = 2.0,
    size: str = "320x240",
    fps: int = 15,
    codec: str = "libx264",
) -> str:
    """Video with two video streams; the first is flagged as the default stream."""
    ff = find_ffmpeg()
    args = [
        ff,
        "-y",
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        _lavfi_source("testsrc", size, fps, duration),
        "-f",
        "lavfi",
        "-i",
        _lavfi_source("testsrc2", size, fps, duration),
        "-map",
        "0:v",
        "-map",
        "1:v",
        "-c:v",
        codec,
        "-pix_fmt",
        "yuv420p",
        "-t",
        f"{duration}",
        path,
    ]
    _run(args)
    return path


def generate_rotated(
    path: str,
    *,
    rotation: int = 90,
    size: str = "640x360",
    duration: float = 2.0,
    fps: int = 15,
    source: str = "testsrc",
    codec: str = "libx264",
) -> str:
    """Video whose display matrix declares ``rotation`` degrees.

    The pixels are encoded landscape (``size``); the display matrix makes the
    intended orientation portrait for a 90/270 rotation. Implemented as a
    two-step encode-then-remux because ``-display_rotation`` is applied to a
    real input stream, not to a lavfi generator.
    """
    ff = find_ffmpeg()
    tmp_fd, tmp_base = tempfile.mkstemp(prefix="vtg-rotbase-", suffix=".mp4")
    os.close(tmp_fd)
    try:
        generate_video(tmp_base, duration=duration, size=size, fps=fps, source=source, codec=codec)
        # Re-mux, applying the display matrix as an input option.
        _run(
            [
                ff,
                "-y",
                "-hide_banner",
                "-v",
                "error",
                "-nostdin",
                "-display_rotation",
                str(rotation),
                "-i",
                tmp_base,
                "-c",
                "copy",
                path,
            ]
        )
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp_base)
    return path


def generate_corrupted(
    path: str,
    *,
    keep_bytes: int = 2000,
    size: str = "320x240",
    duration: float = 1.0,
    fps: int = 15,
) -> str:
    """Produce a truncated/corrupted video (header present, moov atom missing)."""
    tmp_fd, tmp_full = tempfile.mkstemp(prefix="vtg-corruptsrc-", suffix=".mp4")
    os.close(tmp_fd)
    try:
        generate_video(tmp_full, duration=duration, size=size, fps=fps)
        with open(tmp_full, "rb") as fh:
            head = fh.read(keep_bytes)
        with open(path, "wb") as fh:
            fh.write(head)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp_full)
    return path


def generate_zero_byte(path: str) -> str:
    """Create a zero-byte file that exists and is readable but is not media."""
    with open(path, "wb"):
        pass
    return path


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

_KINDS: dict[str, Callable[..., str]] = {
    "video": generate_video,
    "landscape": generate_landscape,
    "portrait": generate_portrait,
    "with-audio": generate_with_audio,
    "color": generate_constant_color,
    "multistream": generate_multistream,
    "rotated": generate_rotated,
    "corrupted": generate_corrupted,
    "zero-byte": generate_zero_byte,
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate synthetic test media (stdlib only).")
    p.add_argument("--list", action="store_true", help="List available media kinds and exit.")
    p.add_argument("kind", nargs="?", choices=sorted(_KINDS), help="Kind of media to generate.")
    p.add_argument("output", nargs="?", help="Output file path.")
    p.add_argument("--duration", type=float, default=2.0)
    p.add_argument("--size", default="320x240")
    p.add_argument("--fps", type=int, default=15)
    p.add_argument(
        "--source", default="testsrc", help="lavfi source (testsrc, testsrc2, color=red)."
    )
    p.add_argument("--rotation", type=int, default=90)
    p.add_argument("--color", default="red")
    p.add_argument("--keep-bytes", type=int, default=2000)
    p.add_argument("--with-audio", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.list:
        print("Available kinds: " + ", ".join(sorted(_KINDS)))
        return 0
    if not args.kind or not args.output:
        print("error: kind and output are required (or use --list)", file=sys.stderr)
        return 2

    fn = _KINDS[args.kind]
    kwargs: dict = {}
    if args.kind in (
        "video",
        "landscape",
        "portrait",
        "with-audio",
        "color",
        "multistream",
        "rotated",
        "corrupted",
    ):
        kwargs["duration"] = args.duration
        kwargs["fps"] = args.fps
    if args.kind in (
        "video",
        "landscape",
        "portrait",
        "with-audio",
        "color",
        "multistream",
        "rotated",
        "corrupted",
    ):
        kwargs["size"] = args.size
    if args.kind in ("video", "with-audio"):
        kwargs["source"] = args.source
    if args.kind == "video" and args.with_audio:
        kwargs["with_audio"] = True
    if args.kind == "color":
        kwargs["color"] = args.color
    if args.kind == "rotated":
        kwargs["rotation"] = args.rotation
        kwargs["source"] = args.source
    if args.kind == "corrupted":
        kwargs["keep_bytes"] = args.keep_bytes
    if args.kind == "zero-byte":
        kwargs = {}

    out = fn(args.output, **kwargs)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
