"""Geometry / duration / still-frame probing helpers for the 0.3.0 suites.

The version 0.3.0 transformation criteria (spec FR-025..FR-030, §15.2, §15.4 and
§22.7) are almost all *measurable* claims: a cropped GIF has exact dimensions, a
speed-adjusted GIF has a specific duration, a preview is a real PNG. This module
supplies the measurement primitives so the integration, security, and acceptance
suites assert on bytes produced by the engine rather than on the engine's own
report of what it did.

Contents
--------
* :func:`probe_video_stream` / :func:`probe_duration_ms` / :func:`probe_frames` --
  thin, typed ffprobe wrappers (argument arrays, never ``shell=True``).
* :func:`png_header` -- stdlib PNG signature + IHDR geometry reader, so a preview
  can be verified as a genuine PNG without trusting ffprobe alone.
* :class:`TransformEngineTestCase` -- an :class:`~fixtures.base.EngineTestCase`
  specialization adding geometry/duration/frame-count/PNG assertions and the
  "engine produced no media at all" assertion the security suite needs.
* :class:`FFmpegSpy` -- shim executables injected through the engine's documented
  ``VTG_FFMPEG`` / ``VTG_FFPROBE`` overrides that record every invocation and
  then exec the real binary. This turns "no FFmpeg process was started"
  (SEC-018, AC-0.3.11) into a directly observable fact instead of an inference
  from the absence of output files.

Everything here is standard-library only and fully annotated (fixtures are held
to the annotated bar even though test methods may stay bare).
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import struct
import subprocess
import sys
from typing import Any

from fixtures.base import FFMPEG, FFPROBE, EngineTestCase

#: PNG signature (RFC 2083 §3.1). A preview MUST be a full-colour PNG (FR-029).
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: Stable warning tokens defined by version 0.3.0 (spec §13.4).
WARN_UPSCALE_NOT_ALLOWED = "UPSCALE_NOT_ALLOWED"
WARN_TRANSFORMATION_NOT_APPLICABLE = "TRANSFORMATION_NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# ffprobe wrappers
# ---------------------------------------------------------------------------
def _ffprobe_json(args: list[str]) -> dict[str, Any]:
    assert FFPROBE is not None  # callers are guarded by EngineTestCase's skipUnless
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def probe_video_stream(path: str) -> dict[str, Any]:
    """Return the first video stream's geometry/codec facts for ``path``.

    Keys: ``width``, ``height``, ``codec_name``, ``pix_fmt``, ``nb_frames``
    (``None`` when the container does not declare one), ``duration_ms``
    (``None`` when the stream declares no duration).
    """
    data = _ffprobe_json(["-show_streams", "-select_streams", "v:0", path])
    streams = data.get("streams") or []
    stream: dict[str, Any] = streams[0] if streams else {}
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "codec_name": stream.get("codec_name"),
        "pix_fmt": stream.get("pix_fmt"),
        "nb_frames": _opt_int(stream.get("nb_frames")),
        "duration_ms": _opt_ms(stream.get("duration")),
    }


def probe_duration_ms(path: str) -> float | None:
    """Return the container duration of ``path`` in milliseconds.

    Falls back to the video stream's own duration when the container declares
    none. Used for the FR-027 output-duration assertions (§15.4 tolerance).
    """
    data = _ffprobe_json(["-show_entries", "format=duration", path])
    ms = _opt_ms((data.get("format") or {}).get("duration"))
    if ms is not None:
        return ms
    stream_ms = probe_video_stream(path)["duration_ms"]
    return float(stream_ms) if stream_ms is not None else None


def probe_frames(path: str) -> int:
    """Return the exact number of decoded video frames in ``path``.

    ``nb_frames`` from the container header is preferred (GIF declares it and it
    is exact); when absent the frames are counted by decoding, so the value is
    always deterministic rather than derived from a declared frame rate.
    """
    declared = probe_video_stream(path)["nb_frames"]
    if declared is not None:
        return int(declared)
    data = _ffprobe_json(["-count_frames", "-show_entries", "stream=nb_read_frames", path])
    streams = data.get("streams") or []
    counted = _opt_int(streams[0].get("nb_read_frames")) if streams else None
    return int(counted or 0)


def _opt_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_ms(value: Any) -> float | None:
    try:
        return float(value) * 1000.0
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# PNG inspection (stdlib only)
# ---------------------------------------------------------------------------
def png_header(path: str) -> dict[str, Any]:
    """Parse a PNG signature and IHDR chunk without third-party dependencies.

    Returns ``is_png``, ``width``, ``height``, ``bit_depth`` and ``color_type``.
    Color type 2 is truecolour RGB, which is what FR-029 requires of a preview
    (a still MUST NOT be palette-quantized; palette would be color type 3).
    """
    with open(path, "rb") as fh:
        head = fh.read(33)
    if not head.startswith(PNG_MAGIC) or len(head) < 33 or head[12:16] != b"IHDR":
        return {"is_png": False, "width": 0, "height": 0, "bit_depth": 0, "color_type": None}
    width, height = struct.unpack(">II", head[16:24])
    return {
        "is_png": True,
        "width": int(width),
        "height": int(height),
        "bit_depth": head[24],
        "color_type": head[25],
    }


# ---------------------------------------------------------------------------
# FFmpeg invocation spy (SEC-018 / AC-0.3.11)
# ---------------------------------------------------------------------------
#: The spy uses POSIX shell shims; Windows has no equivalent one-liner that the
#: engine's ``os.access(..., X_OK)`` check would accept, so spy-based tests skip
#: there. The rejection itself is asserted on every platform by the non-spy
#: tests; only the "zero processes started" evidence is POSIX-only.
SPY_SUPPORTED = sys.platform != "win32"

_SHIM_TEMPLATE = """#!/bin/sh
printf '%s\\n' {name} >> {log}
exec {real} "$@"
"""


class FFmpegSpy:
    """Record every ffmpeg/ffprobe invocation an engine run makes.

    Installs shim executables into ``directory`` and exposes the environment
    overrides (:attr:`env`) that point the engine's documented ``VTG_FFMPEG`` /
    ``VTG_FFPROBE`` discovery at them. Each shim appends its own name to a log
    file and then ``exec``s the real binary, so behaviour is unchanged and the
    count of started processes is exact.
    """

    def __init__(self, directory: str) -> None:
        assert FFMPEG is not None and FFPROBE is not None
        os.makedirs(directory, exist_ok=True)
        self.directory = directory
        self.log_path = os.path.join(directory, "invocations.log")
        self._shims: dict[str, str] = {}
        for name, real in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
            shim = os.path.join(directory, name)
            with open(shim, "w", encoding="utf-8") as fh:
                fh.write(
                    _SHIM_TEMPLATE.format(
                        name=shlex.quote(name),
                        log=shlex.quote(self.log_path),
                        real=shlex.quote(real),
                    )
                )
            os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            self._shims[name] = shim

    @property
    def env(self) -> dict[str, str]:
        """A full environment for ``run_engine(env=...)`` pointing at the shims."""
        env = os.environ.copy()
        env["VTG_FFMPEG"] = self._shims["ffmpeg"]
        env["VTG_FFPROBE"] = self._shims["ffprobe"]
        return env

    def invocations(self) -> list[str]:
        """Names of the executables invoked so far, in call order."""
        if not os.path.isfile(self.log_path):
            return []
        with open(self.log_path, encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()]

    def count(self, name: str) -> int:
        return sum(1 for entry in self.invocations() if entry == name)

    def reset(self) -> None:
        if os.path.isfile(self.log_path):
            os.remove(self.log_path)


# ---------------------------------------------------------------------------
# Test case
# ---------------------------------------------------------------------------
class TransformEngineTestCase(EngineTestCase):
    """EngineTestCase plus measured-geometry assertions for version 0.3.0."""

    #: §15.4 duration tolerance floor: one output frame or 100 ms, whichever is
    #: greater. Callers pass the effective fps so the frame term is exact.
    MIN_DURATION_TOLERANCE_MS = 100.0

    # -- media assertions --------------------------------------------------
    def assert_gif_geometry(self, path: str, width: int, height: int) -> None:
        """Assert the GIF at ``path`` really is ``width`` x ``height``."""
        self.assert_valid_gif(path)
        info = probe_video_stream(path)
        self.assertEqual(
            (info["width"], info["height"]),
            (width, height),
            f"{os.path.basename(path)}: ffprobe reports {info['width']}x{info['height']}, "
            f"expected {width}x{height}",
        )

    def assert_gif_frames(self, path: str, expected: int, *, tolerance: int = 1) -> None:
        actual = probe_frames(path)
        self.assertLessEqual(
            abs(actual - expected),
            tolerance,
            f"{os.path.basename(path)}: {actual} frames, expected ~{expected} "
            f"(tolerance {tolerance})",
        )

    def assert_gif_duration_ms(self, path: str, expected_ms: float, *, fps: float) -> None:
        """Assert output duration within the §15.4 tolerance (one frame or 100 ms)."""
        actual = probe_duration_ms(path)
        self.assertIsNotNone(actual, f"{os.path.basename(path)}: ffprobe reported no duration")
        assert actual is not None  # for mypy; asserted above
        tolerance = max(self.MIN_DURATION_TOLERANCE_MS, 1000.0 / fps if fps > 0 else 0.0)
        self.assertLessEqual(
            abs(actual - expected_ms),
            tolerance,
            f"{os.path.basename(path)}: duration {actual:.1f} ms, expected "
            f"{expected_ms:.1f} ms +/- {tolerance:.1f} ms",
        )

    def assert_truecolour_png(self, path: str, width: int, height: int) -> None:
        """Assert ``path`` is a genuine, non-palette PNG of the given geometry."""
        self.assertTrue(os.path.isfile(path), f"PNG not found: {path}")
        self.assertGreater(os.path.getsize(path), 0, "PNG is empty")
        header = png_header(path)
        self.assertTrue(header["is_png"], "file does not start with the PNG signature")
        self.assertEqual(
            (header["width"], header["height"]),
            (width, height),
            f"PNG IHDR reports {header['width']}x{header['height']}, expected {width}x{height}",
        )
        # FR-029: full colour, never palette-quantized (color type 3 is palette).
        self.assertEqual(header["color_type"], 2, "preview PNG must be truecolour RGB")
        info = probe_video_stream(path)
        self.assertEqual(info["codec_name"], "png")
        self.assertEqual((info["width"], info["height"]), (width, height))

    # -- "nothing was produced" -------------------------------------------
    def assert_no_media_produced(self) -> None:
        """Assert the run wrote no GIF/PNG and left no partial temp artifact."""
        produced = [n for n in self.list_output() if n.lower().endswith((".gif", ".png"))]
        self.assertEqual(produced, [], f"engine produced media it should not have: {produced}")
        self.assertEqual(
            self.temp_gif_leftovers(), [], "engine left a partial temporary file behind"
        )

    def assert_no_traceback(self, res: Any) -> None:
        """A rejected value must never surface a Python traceback (spec §14)."""
        self.assertNotIn("Traceback (most recent call last)", res.stderr)
        self.assertNotIn("Traceback (most recent call last)", res.stdout)

    # -- result-contract helpers ------------------------------------------
    @staticmethod
    def warnings_of(res: Any) -> list[str]:
        return list(res.result.get("warnings") or [])

    def assert_warning_token(self, res: Any, token: str) -> str:
        matches = [w for w in self.warnings_of(res) if w.startswith(token + ": ")]
        self.assertTrue(matches, f"expected a {token} warning, got {self.warnings_of(res)}")
        return matches[0]

    def assert_no_warning_token(self, res: Any, token: str) -> None:
        matches = [w for w in self.warnings_of(res) if w.startswith(token)]
        self.assertEqual(matches, [], f"unexpected {token} warning: {matches}")

    @staticmethod
    def previews_of(res: Any) -> list[dict[str, Any]]:
        return list(res.result.get("previews") or [])

    def spy(self) -> FFmpegSpy:
        """Create an :class:`FFmpegSpy` inside this test's project directory."""
        return FFmpegSpy(os.path.join(self.project, ".ffmpeg-spy"))
