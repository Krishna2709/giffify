"""Clip timestamp validation and invalid-timestamp policies (FR-006, FR-007)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest

from vtg import errors
from vtg.cli import _make_settings_resolver, validate_and_filter
from vtg.ffmpeg import resolve_effective_settings
from vtg.models import BUILTIN_PROFILES, ClipSpec


def clip(index=0, start_ms=0, end_ms=1000, **kw):
    return ClipSpec(index=index, start_ms=start_ms, end_ms=end_ms, **kw)


DURATION = 10_000  # 10s source


class TestValidateAndFilter(unittest.TestCase):
    def test_valid_clip_passes(self):
        valid, skipped = validate_and_filter([clip(end_ms=5000)], DURATION, "fail")
        self.assertEqual(len(valid), 1)
        self.assertEqual(skipped, [])

    def test_start_plus_duration_equivalent(self):
        # start=1s, end=6s (as if duration 5) is valid.
        valid, _ = validate_and_filter([clip(start_ms=1000, end_ms=6000)], DURATION, "fail")
        self.assertEqual(valid[0].duration_ms, 5000)

    def test_end_before_start_rejected(self):
        with self.assertRaises(errors.EngineError) as ctx:
            validate_and_filter([clip(start_ms=5000, end_ms=3000)], DURATION, "fail")
        self.assertEqual(ctx.exception.code, errors.INVALID_TIMESTAMP)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_TIMESTAMP)

    def test_end_equals_start_rejected(self):
        with self.assertRaises(errors.EngineError):
            validate_and_filter([clip(start_ms=3000, end_ms=3000)], DURATION, "fail")

    def test_start_at_duration_rejected(self):
        with self.assertRaises(errors.EngineError):
            validate_and_filter([clip(start_ms=DURATION, end_ms=DURATION + 1000)], DURATION, "fail")

    def test_end_beyond_duration_fail(self):
        with self.assertRaises(errors.EngineError):
            validate_and_filter([clip(start_ms=1000, end_ms=DURATION + 5000)], DURATION, "fail")

    def test_end_beyond_duration_skip(self):
        valid, skipped = validate_and_filter(
            [clip(start_ms=1000, end_ms=DURATION + 5000)], DURATION, "skip"
        )
        self.assertEqual(valid, [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["clipIndex"], 0)

    def test_end_beyond_duration_clamp(self):
        valid, skipped = validate_and_filter(
            [clip(start_ms=1000, end_ms=DURATION + 5000)], DURATION, "clamp"
        )
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0].end_ms, DURATION)  # clamped to source duration
        self.assertEqual(skipped, [])

    def test_clamp_does_not_fix_non_clampable(self):
        # start beyond duration is not clampable; clamp policy still fails.
        with self.assertRaises(errors.EngineError):
            validate_and_filter(
                [clip(start_ms=DURATION + 1, end_ms=DURATION + 5)], DURATION, "clamp"
            )

    def test_skip_keeps_valid_clips(self):
        clips = [clip(index=0, end_ms=2000), clip(index=1, start_ms=1000, end_ms=DURATION + 9000)]
        valid, skipped = validate_and_filter(clips, DURATION, "skip")
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0].index, 0)
        self.assertEqual(len(skipped), 1)


class TestEffectiveSettings(unittest.TestCase):
    def test_no_upscale_by_default(self):
        src = vtgtest.make_source(width=320, height=240, fps=25.0)
        s = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
        )
        self.assertEqual(s.width, 320)  # not upscaled to 640
        self.assertEqual(s.height, 240)

    def test_downscale_preserves_aspect(self):
        src = vtgtest.make_source(width=1920, height=1080, fps=30.0)
        s = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
        )
        self.assertEqual(s.width, 640)
        self.assertEqual(s.height, 360)  # 1080 * 640/1920

    def test_fps_capped_at_source(self):
        src = vtgtest.make_source(width=640, height=480, fps=10.0)
        s = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=20,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="high",
        )
        self.assertEqual(s.fps, 10.0)  # source fps < target

    def test_rotation_swaps_display_dims(self):
        src = vtgtest.make_source(width=1920, height=1080, fps=30.0, rotation=90)
        s = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
        )
        # Display is portrait 1080x1920; width capped to 640.
        self.assertEqual(s.width, 640)
        self.assertEqual(s.height, 1138)  # round(1920 * 640/1080)

    def test_colors_clamped(self):
        src = vtgtest.make_source(width=320, height=240)
        s = resolve_effective_settings(
            src,
            max_width=480,
            target_fps=10,
            colors=1000,
            loop="forever",
            allow_upscale=False,
            profile_name="small",
        )
        self.assertEqual(s.colors, 256)


class TestSettingsPrecedence(unittest.TestCase):
    def test_clip_over_top_over_profile(self):
        src = vtgtest.make_source(width=1920, height=1080, fps=60.0)
        # top-level width=800 overrides profile; clip fps overrides top.
        resolve = _make_settings_resolver(
            src,
            top_profile="balanced",
            top_width=800,
            top_fps=None,
            top_colors=None,
            top_loop="forever",
            allow_upscale=False,
        )
        c = ClipSpec(index=0, start_ms=0, end_ms=1000, fps=24)
        s = resolve(c)
        self.assertEqual(s.width, 800)  # from top-level
        self.assertEqual(s.fps, 24.0)  # from clip
        self.assertEqual(s.colors, 256)  # from balanced profile
        self.assertEqual(s.profile_name, "balanced")

    def test_clip_profile_overrides_top_profile(self):
        src = vtgtest.make_source(width=1920, height=1080, fps=60.0)
        resolve = _make_settings_resolver(
            src,
            top_profile="balanced",
            top_width=None,
            top_fps=None,
            top_colors=None,
            top_loop="forever",
            allow_upscale=False,
        )
        c = ClipSpec(index=0, start_ms=0, end_ms=1000, profile="small")
        s = resolve(c)
        self.assertEqual(s.profile_name, "small")
        self.assertEqual(s.width, BUILTIN_PROFILES["small"].max_width)
        self.assertEqual(s.colors, 128)


if __name__ == "__main__":
    unittest.main()
