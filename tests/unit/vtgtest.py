"""Shared test support: import-path setup and small fixtures.

Adds the engine ``scripts`` directory to ``sys.path`` so tests can import the
``vtg`` package, and provides helpers for building fake media info without any
real ffmpeg/ffprobe.
"""

from __future__ import annotations

import os
import sys

_SCRIPTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "skill", "video-to-gif", "scripts")
)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from vtg.models import EffectiveSettings, LoopValue, SourceInfo  # noqa: E402


def make_source(
    path: str = "/proj/demo.mp4",
    duration_ms: int = 900_000,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    rotation: int = 0,
) -> SourceInfo:
    if rotation in (90, 270):
        dw, dh = height, width
    else:
        dw, dh = width, height
    return SourceInfo(
        path=path,
        duration_ms=duration_ms,
        width=width,
        height=height,
        display_width=dw,
        display_height=dh,
        fps=fps,
        codec="h264",
        stream_index=0,
        rotation=rotation,
    )


def make_settings(
    width: int = 640,
    height: int = 360,
    fps: float = 15.0,
    colors: int = 256,
    loop: LoopValue = "forever",
    profile_name: str = "balanced",
) -> EffectiveSettings:
    return EffectiveSettings(
        width=width, height=height, fps=fps, colors=colors, loop=loop, profile_name=profile_name
    )
