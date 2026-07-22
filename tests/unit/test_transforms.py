"""Unit tests for the v0.3.0 transformation layer (spec section 22.7).

Hermetic: no ffmpeg, no media, no subprocess. Covers crop parsing and bounds,
dimension bounds and resolution, upscale gating, dimension parity, speed
parsing and duration arithmetic, dither enumeration and profile defaults, the
filter-chain order of section 15.2, and the SEC-018 injection surface.
"""

import os
import sys
import unittest
from decimal import Decimal
from typing import ClassVar

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest

from vtg import errors, transforms
from vtg.ffmpeg import (
    build_palettegen_command,
    build_paletteuse_command,
    build_preview_command,
    resolve_effective_settings,
)
from vtg.transforms import CropRect


class TransformTestCase(unittest.TestCase):
    def assert_engine_error(self, code, exit_code=errors.EXIT_INVALID_TIMESTAMP):
        """Assert the block raises an EngineError with ``code`` and exit code."""
        ctx = self.assertRaises(errors.EngineError)
        outer = self

        class _Wrapper:
            def __enter__(self):
                self.cm = ctx.__enter__()
                return self.cm

            def __exit__(self, *exc):
                handled = ctx.__exit__(*exc)
                if handled:
                    outer.assertEqual(self.cm.exception.code, code)
                    outer.assertEqual(self.cm.exception.exit_code, exit_code)
                    outer.assertEqual(self.cm.exception.status, errors.STATUS_VALIDATION_FAILED)
                return handled

        return _Wrapper()


# ---------------------------------------------------------------------------
# FR-025: cropping
# ---------------------------------------------------------------------------


class TestCropParsing(TransformTestCase):
    def test_string_form(self):
        rect = transforms.parse_crop("320:180:1280:720")
        self.assertEqual(rect, CropRect(x=320, y=180, width=1280, height=720))

    def test_object_form(self):
        rect = transforms.parse_crop({"x": 320, "y": 180, "width": 1280, "height": 720})
        self.assertEqual(rect, CropRect(x=320, y=180, width=1280, height=720))

    def test_string_and_object_forms_agree(self):
        self.assertEqual(
            transforms.parse_crop("0:0:640:360"),
            transforms.parse_crop({"x": 0, "y": 0, "width": 640, "height": 360}),
        )

    def test_zero_offsets_allowed(self):
        self.assertEqual(transforms.parse_crop("0:0:2:2"), CropRect(0, 0, 2, 2))

    def test_malformed_string_forms_rejected(self):
        for bad in (
            "320:180:1280",  # three fields
            "320:180:1280:720:5",  # five fields
            "320,180,1280,720",  # wrong separator
            "320:180:1280:",  # empty field
            ":0:10:10",
            "320 : 180 : 1280 : 720",  # internal whitespace
            "320:180:1280:720\n",  # trailing newline (SEC-018: not trimmed)
            " 320:180:1280:720",  # leading whitespace
            "0x10:0:10:10",
            "+1:0:10:10",
            "-1:0:10:10",
            "1.5:0:10:10",
            "1e2:0:10:10",
            "",
        ):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_CROP):
                transforms.parse_crop(bad)

    def test_unicode_digits_rejected(self):
        # Arabic-Indic digits are decimal but outside the documented grammar.
        with self.assert_engine_error(errors.INVALID_CROP):
            # The Arabic-Indic digits are the point of the test; RUF001 flags
            # them as ASCII lookalikes, which is exactly why they are here.
            transforms.parse_crop("٢:٠:١٠:١٠")  # noqa: RUF001

    def test_non_string_non_object_rejected(self):
        for bad in (None, 123, [0, 0, 10, 10], True, 1.5):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_CROP):
                transforms.parse_crop(bad)

    def test_object_missing_key_rejected(self):
        with self.assert_engine_error(errors.INVALID_CROP):
            transforms.parse_crop({"x": 0, "y": 0, "width": 10})

    def test_object_extra_key_rejected(self):
        with self.assert_engine_error(errors.INVALID_CROP):
            transforms.parse_crop({"x": 0, "y": 0, "width": 10, "height": 10, "z": 1})

    def test_object_non_integer_values_rejected(self):
        for bad in (
            {"x": "0", "y": 0, "width": 10, "height": 10},
            {"x": 1.5, "y": 0, "width": 10, "height": 10},
        ):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_CROP):
                transforms.parse_crop(bad)

    def test_negative_object_value_rejected(self):
        with self.assert_engine_error(errors.INVALID_CROP):
            transforms.parse_crop({"x": -1, "y": 0, "width": 10, "height": 10})

    def test_value_above_65535_rejected(self):
        for bad in ("0:0:70000:10", {"x": 70000, "y": 0, "width": 10, "height": 10}):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_CROP):
                transforms.parse_crop(bad)

    def test_zero_or_one_pixel_side_rejected(self):
        for bad in ("0:0:0:10", "0:0:10:0", "0:0:1:10", "0:0:10:1"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_CROP):
                transforms.parse_crop(bad)


class TestCropBounds(TransformTestCase):
    def test_rectangle_inside_source_accepted(self):
        transforms.validate_crop_bounds(CropRect(320, 180, 1280, 720), 1920, 1080)

    def test_rectangle_flush_with_edges_accepted(self):
        transforms.validate_crop_bounds(CropRect(640, 360, 1280, 720), 1920, 1080)

    def test_rectangle_past_right_edge_rejected(self):
        with self.assert_engine_error(errors.INVALID_CROP):
            transforms.validate_crop_bounds(CropRect(641, 360, 1280, 720), 1920, 1080)

    def test_rectangle_past_bottom_edge_rejected(self):
        with self.assert_engine_error(errors.INVALID_CROP):
            transforms.validate_crop_bounds(CropRect(0, 361, 1280, 720), 1920, 1080)

    def test_bounds_use_orientation_normalized_dimensions(self):
        # A 1920x1080 coded frame rotated 90 degrees displays as 1080x1920, so a
        # 1000x1800 rectangle fits the DISPLAY geometry but not the coded one.
        src = vtgtest.make_source(width=1920, height=1080, rotation=90)
        self.assertEqual((src.display_width, src.display_height), (1080, 1920))
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
            crop=CropRect(0, 0, 1000, 1800),
        )
        self.assertEqual(settings.effective_source_width, 1000)
        self.assertEqual(settings.effective_source_height, 1800)

    def test_rotated_source_rejects_rectangle_outside_display_geometry(self):
        src = vtgtest.make_source(width=1920, height=1080, rotation=90)
        with self.assert_engine_error(errors.INVALID_CROP):
            resolve_effective_settings(
                src,
                max_width=640,
                target_fps=15,
                colors=256,
                loop="forever",
                allow_upscale=False,
                profile_name="balanced",
                # 1500 wide exceeds the 1080-wide display frame.
                crop=CropRect(0, 0, 1500, 900),
            )


class TestCropGeometry(TransformTestCase):
    def test_crop_supplies_effective_source_geometry(self):
        # AC-0.3.1: 1920x1080 source, crop 1280x720, profile max width 640.
        src = vtgtest.make_source(width=1920, height=1080)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
            crop=CropRect(320, 180, 1280, 720),
        )
        self.assertEqual((settings.width, settings.height), (640, 360))
        self.assertEqual(settings.source_width, 1920)
        self.assertEqual(settings.source_height, 1080)
        self.assertEqual(settings.effective_source_width, 1280)
        self.assertEqual(settings.effective_source_height, 720)

    def test_crop_changes_output_aspect_ratio(self):
        # Cropping is the only supported way to change the output aspect ratio.
        src = vtgtest.make_source(width=1920, height=1080)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
            crop=CropRect(0, 0, 800, 800),
        )
        self.assertEqual((settings.width, settings.height), (640, 640))

    def test_cropped_width_below_profile_max_is_retained(self):
        src = vtgtest.make_source(width=1920, height=1080)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
            crop=CropRect(0, 0, 400, 200),
        )
        self.assertEqual((settings.width, settings.height), (400, 200))
        self.assertFalse(settings.upscaled)


# ---------------------------------------------------------------------------
# FR-026: explicit resizing
# ---------------------------------------------------------------------------


class TestDimensionParsing(TransformTestCase):
    def test_valid_bounds(self):
        self.assertEqual(transforms.parse_dimension(2, field_path="width"), 2)
        self.assertEqual(transforms.parse_dimension(8192, field_path="width"), 8192)
        self.assertEqual(transforms.parse_dimension("640", field_path="width"), 640)

    def test_out_of_range_rejected(self):
        for bad in (1, 0, -1, 8193, 100000):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_DIMENSIONS):
                transforms.parse_dimension(bad, field_path="width")

    def test_non_integer_rejected(self):
        for bad in ("640.0", "6e2", "abc", "", "640px", True, None, 1.5, "٦٤٠"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_DIMENSIONS):
                transforms.parse_dimension(bad, field_path="width")


class TestDimensionResolution(TransformTestCase):
    def resolve(self, eff_w, eff_h, **kw):
        kw.setdefault("width", None)
        kw.setdefault("height", None)
        kw.setdefault("profile_max_width", None)
        kw.setdefault("allow_upscale", False)
        return transforms.resolve_output_dimensions(eff_w, eff_h, **kw)

    def test_width_only_derives_height(self):
        r = self.resolve(1920, 1080, width=640)
        self.assertEqual((r.width, r.height), (640, 360))

    def test_height_only_derives_width(self):
        r = self.resolve(1920, 1080, height=360)
        self.assertEqual((r.width, r.height), (640, 360))

    def test_both_bounds_fit_inside_box(self):
        # AC-0.3.5: 16:9 source in an 800x200 box -> height binds.
        r = self.resolve(1920, 1080, width=800, height=200)
        self.assertLessEqual(r.width, 800)
        self.assertLessEqual(r.height, 200)
        self.assertEqual((r.width, r.height), (356, 200))
        # Aspect ratio is preserved within a pixel of rounding.
        self.assertAlmostEqual(r.width / r.height, 1920 / 1080, delta=0.02)

    def test_both_bounds_width_binds(self):
        r = self.resolve(1920, 1080, width=400, height=1000)
        self.assertEqual((r.width, r.height), (400, round(1080 * 400 / 1920)))
        self.assertEqual((r.width, r.height), (400, 225))
        self.assertLessEqual(r.height, 1000)

    def test_explicit_width_overrides_profile_maximum_upward(self):
        # AC-0.3.3: profile small (480) with --width 800 on an 1920 source.
        r = self.resolve(1920, 1080, width=800, profile_max_width=480)
        self.assertEqual(r.width, 800)

    def test_explicit_width_overrides_profile_maximum_downward(self):
        r = self.resolve(1920, 1080, width=200, profile_max_width=960)
        self.assertEqual(r.width, 200)

    def test_explicit_height_overrides_profile_maximum(self):
        r = self.resolve(1920, 1080, height=900, profile_max_width=480)
        self.assertEqual(r.height, 900)
        self.assertEqual(r.width, 1600)

    def test_profile_only_clamps_to_source_without_warning(self):
        # Regression guard: a profile-only job emits exactly the warnings it
        # emitted in v0.2.0, i.e. none for an over-wide profile maximum.
        r = self.resolve(320, 240, profile_max_width=640)
        self.assertEqual((r.width, r.height), (320, 240))
        self.assertIsNone(r.warning)
        self.assertFalse(r.upscaled)

    def test_profile_only_derivation_matches_v0_2_0(self):
        # v0.2.0 derived the height with plain rounding; FR-026 keeps the
        # profile-only path unchanged, so an odd derived height is preserved.
        r = self.resolve(1000, 502, profile_max_width=640)
        self.assertEqual((r.width, r.height), (640, round(502 * 640 / 1000)))
        self.assertEqual(r.height, 321)

    def test_explicit_odd_width_honored_exactly(self):
        r = self.resolve(1920, 1080, width=641)
        self.assertEqual(r.width, 641)  # no rounding to even

    def test_explicit_odd_height_honored_exactly(self):
        r = self.resolve(1920, 1080, height=361)
        self.assertEqual(r.height, 361)

    def test_derived_dimension_rounds_to_nearest_and_may_be_odd(self):
        # FR-026 "Dimension parity": 1280x720 at width 500 derives 281.25, which
        # rounds to the nearest integer (281) and is deliberately left odd. GIF
        # is palette-based with no chroma subsampling, so no even constraint
        # applies -- the same reason an explicit odd bound is honored exactly.
        r = self.resolve(1280, 720, width=500)
        self.assertEqual(r.width, 500)
        self.assertEqual(r.height, round(720 * 500 / 1280))
        self.assertEqual(r.height, 281)

    def test_derived_dimension_from_height_bound_rounds_to_nearest(self):
        # 1280x721 at height 500 derives 887.65 -> 888 (odd input, even result);
        # 1000x502 at height 200 derives 398.4 -> 398; 502x334 at height 250
        # derives 375.7 -> 376. The rule is round(), never a parity adjustment.
        r = self.resolve(1280, 721, height=500)
        self.assertEqual((r.width, r.height), (round(1280 * 500 / 721), 500))
        self.assertEqual(r.width, 888)
        r = self.resolve(4000, 3000, height=501)
        self.assertEqual((r.width, r.height), (668, 501))
        self.assertEqual(r.width, round(4000 * 501 / 3000))

    def test_derivation_identical_on_every_path(self):
        # The regression this locks: profile-only, width-only, and both-bounds
        # must all use one derivation rule. A single helper backs all three, so
        # the same driving width must produce the same derived height whichever
        # path supplied it.
        profile_only = self.resolve(1000, 502, profile_max_width=640)
        width_only = self.resolve(1000, 502, width=640)
        both_bounds = self.resolve(1000, 502, width=640, height=8192)
        self.assertEqual((profile_only.width, profile_only.height), (640, 321))
        self.assertEqual(
            (width_only.width, width_only.height), (profile_only.width, profile_only.height)
        )
        self.assertEqual(
            (both_bounds.width, both_bounds.height), (profile_only.width, profile_only.height)
        )

    def test_both_bounds_always_fit_inside_the_box(self):
        # Sweep the both-bounds path: neither resolved dimension may exceed its
        # explicit bound, whichever side binds and however the derived side
        # rounds. Under round()-to-nearest the derived side can land exactly on
        # the other bound but never past it.
        for eff_w, eff_h in ((1000, 999), (999, 1000), (1920, 1080), (502, 334), (1000, 502)):
            for w in (99, 100, 251, 500, 501):
                for h in (99, 100, 249, 499, 500):
                    r = self.resolve(eff_w, eff_h, width=w, height=h, allow_upscale=True)
                    self.assertLessEqual(r.width, w, f"{eff_w}x{eff_h} -> {w}x{h}")
                    self.assertLessEqual(r.height, h, f"{eff_w}x{eff_h} -> {w}x{h}")
                    self.assertGreaterEqual(min(r.width, r.height), 2)

    def test_no_output_dimension_below_two(self):
        r = self.resolve(4000, 10, width=100)
        self.assertGreaterEqual(r.height, 2)

    def test_deterministic(self):
        first = self.resolve(1920, 1080, width=777, profile_max_width=640)
        second = self.resolve(1920, 1080, width=777, profile_max_width=640)
        self.assertEqual(first, second)


class TestVersion020DimensionParity(TransformTestCase):
    """Lock the exact dimensions the version 0.2.0 engine produced (NFR-006).

    Every expectation below was captured by EXECUTING the released 0.2.0 engine
    against synthetic sources of the stated geometry, then re-verified against
    0.3.0 with byte-identical GIF output. An adversarial review of 0.3.0 caught
    an even-rounding rule silently shifting the derived side of an explicit
    --width by one pixel; FR-026 "Dimension parity" now mandates round() on
    every path, and this table is the guard against that drifting again.

    Do not "fix" a failure here by editing the expected values: a mismatch means
    0.3.0 has diverged from 0.1.0/0.2.0 output and the engine is what is wrong.
    """

    # (effective source, explicit width, profile max width) -> (out_w, out_h)
    V020_WIDTH_ONLY: ClassVar[dict] = {
        # The four dimension regressions the 0.3.0 review measured.
        (1000, 502, 320): (320, 161),
        (1000, 502, 500): (500, 251),
        (1280, 534, 500): (500, 209),
        (502, 334, 500): (500, 333),
        # The rest of the executed comparison matrix.
        (1000, 502, 640): (640, 321),
        (1280, 534, 320): (320, 134),
        (1280, 534, 640): (640, 267),
        (502, 334, 320): (320, 213),
        (1920, 1080, 320): (320, 180),
        (1920, 1080, 500): (500, 281),
        (1920, 1080, 640): (640, 360),
    }

    V020_PROFILE_ONLY: ClassVar[dict] = {
        (1000, 502, 480): (480, 241),
        (1000, 502, 640): (640, 321),
        (1000, 502, 960): (960, 482),
        (1280, 534, 480): (480, 200),
        (1280, 534, 640): (640, 267),
        (1280, 534, 960): (960, 400),
        (502, 334, 480): (480, 319),
        (1920, 1080, 480): (480, 270),
        (1920, 1080, 640): (640, 360),
        (1920, 1080, 960): (960, 540),
    }

    def test_explicit_width_matches_v0_2_0_exactly(self):
        for (eff_w, eff_h, width), expected in sorted(self.V020_WIDTH_ONLY.items()):
            with self.subTest(source=f"{eff_w}x{eff_h}", width=width):
                r = transforms.resolve_output_dimensions(
                    eff_w,
                    eff_h,
                    width=width,
                    height=None,
                    profile_max_width=None,
                    allow_upscale=False,
                )
                self.assertEqual((r.width, r.height), expected)

    def test_explicit_width_unaffected_by_a_profile_maximum(self):
        # An explicit bound overrides the profile maximum (FR-026) and must
        # still land on the v0.2.0 dimensions.
        for (eff_w, eff_h, width), expected in sorted(self.V020_WIDTH_ONLY.items()):
            with self.subTest(source=f"{eff_w}x{eff_h}", width=width):
                r = transforms.resolve_output_dimensions(
                    eff_w,
                    eff_h,
                    width=width,
                    height=None,
                    profile_max_width=960,
                    allow_upscale=False,
                )
                self.assertEqual((r.width, r.height), expected)

    def test_profile_only_matches_v0_2_0_exactly(self):
        for (eff_w, eff_h, max_width), expected in sorted(self.V020_PROFILE_ONLY.items()):
            with self.subTest(source=f"{eff_w}x{eff_h}", profile_max_width=max_width):
                r = transforms.resolve_output_dimensions(
                    eff_w,
                    eff_h,
                    width=None,
                    height=None,
                    profile_max_width=max_width,
                    allow_upscale=False,
                )
                self.assertEqual((r.width, r.height), expected)

    def test_derived_side_is_never_forced_even(self):
        # Each regression case derives an ODD companion dimension. If any of
        # these comes back even, even-rounding has returned somewhere.
        for (eff_w, eff_h, width), (_, out_h) in self.V020_WIDTH_ONLY.items():
            if out_h % 2 == 0:
                continue
            r = transforms.resolve_output_dimensions(
                eff_w, eff_h, width=width, height=None, profile_max_width=None, allow_upscale=False
            )
            self.assertEqual(r.height % 2, 1, f"{eff_w}x{eff_h} --width {width}")

    def test_derivation_is_plain_round_to_nearest(self):
        # The rule stated as an equation, over the whole matrix.
        for eff_w, eff_h, width in self.V020_WIDTH_ONLY:
            r = transforms.resolve_output_dimensions(
                eff_w, eff_h, width=width, height=None, profile_max_width=None, allow_upscale=False
            )
            self.assertEqual(r.height, round(eff_h * width / eff_w))


class TestUpscaleGating(TransformTestCase):
    def resolve(self, **kw):
        kw.setdefault("width", None)
        kw.setdefault("height", None)
        kw.setdefault("profile_max_width", None)
        kw.setdefault("allow_upscale", False)
        return transforms.resolve_output_dimensions(640, 360, **kw)

    def test_clamped_with_warning_when_bound_explicit(self):
        # AC-0.3.4: 640-wide source, --width 1280, no --allow-upscale.
        r = self.resolve(width=1280)
        self.assertEqual((r.width, r.height), (640, 360))
        self.assertIsNotNone(r.warning)
        self.assertTrue(r.warning.startswith("UPSCALE_NOT_ALLOWED: "))
        self.assertFalse(r.upscaled)

    def test_honored_with_allow_upscale(self):
        r = self.resolve(width=1280, allow_upscale=True)
        self.assertEqual((r.width, r.height), (1280, 720))
        self.assertIsNone(r.warning)
        self.assertTrue(r.upscaled)

    def test_explicit_height_bound_also_warns(self):
        r = self.resolve(height=720)
        self.assertEqual((r.width, r.height), (640, 360))
        self.assertTrue(r.warning.startswith("UPSCALE_NOT_ALLOWED: "))

    def test_profile_maximum_exceeding_source_never_warns(self):
        r = self.resolve(profile_max_width=960)
        self.assertIsNone(r.warning)
        self.assertEqual((r.width, r.height), (640, 360))

    def test_warning_reaches_the_settings_warning_list(self):
        src = vtgtest.make_source(width=640, height=360)
        warnings: list[str] = []
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
            explicit_width=1280,
            warnings=warnings,
        )
        self.assertEqual(settings.width, 640)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith(transforms.WARN_UPSCALE_NOT_ALLOWED + ": "))


# ---------------------------------------------------------------------------
# FR-027: playback speed
# ---------------------------------------------------------------------------


class TestSpeedParsing(TransformTestCase):
    def test_valid_values(self):
        for value, expected in (
            ("1", Decimal("1")),
            ("1.0", Decimal("1.0")),
            (2.0, Decimal("2.0")),
            (2, Decimal("2")),
            ("0.25", Decimal("0.25")),
            ("4.0", Decimal("4.0")),
            ("1.001", Decimal("1.001")),
        ):
            with self.subTest(value=value):
                self.assertEqual(transforms.parse_speed(value), expected)

    def test_out_of_range_rejected(self):
        # AC-0.3.7: 0, negative, 0.1, and 5.0 are each rejected.
        for bad in (0, "0", "0.0", -1, "-1", 0.1, "0.1", "0.249", 5.0, "5.0", "4.001"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_SPEED):
                transforms.parse_speed(bad)

    def test_non_numeric_rejected(self):
        for bad in ("fast", "", None, True, "2x", "2.0f", "٢"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_SPEED):
                transforms.parse_speed(bad)

    def test_exponent_notation_rejected(self):
        for bad in ("1e0", "2E0", "1e-1", "0.5e1"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_SPEED):
                transforms.parse_speed(bad)

    def test_more_than_three_fractional_digits_rejected(self):
        for bad in ("1.0001", "2.12345", 1.00005):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_SPEED):
                transforms.parse_speed(bad)

    def test_padding_whitespace_rejected(self):
        # SEC-018: whitespace is outside the grammar, so it is rejected rather
        # than trimmed. Only `dither` trims, because FR-028 requires it.
        for bad in (" 2.0", "2.0 ", "2.0\n", "\t2.0", "2 .0"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_SPEED):
                transforms.parse_speed(bad)

    def test_signs_and_separators_rejected(self):
        for bad in ("+2.0", "2,0", "2.0;", "2.0'", '2.0"', "2.0 3.0"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_SPEED):
                transforms.parse_speed(bad)


class TestSpeedArithmetic(TransformTestCase):
    def test_output_duration(self):
        # AC-0.3.6 and the FR-027 examples.
        cases = [
            (4000, "1", 4000),
            (4000, "2.0", 2000),
            (4000, "0.5", 8000),
            (5000, "1.5", 3333),
            (5000, "3", 1667),
            (1000, "0.25", 4000),
            (1000, "4.0", 250),
        ]
        for duration, speed, expected in cases:
            with self.subTest(duration=duration, speed=speed):
                self.assertEqual(transforms.output_duration_ms(duration, Decimal(speed)), expected)

    def test_speed_one_is_identity(self):
        self.assertEqual(transforms.output_duration_ms(7777, Decimal("1.0")), 7777)

    def test_source_range_is_unaffected_by_speed(self):
        src = vtgtest.make_source(width=640, height=360, fps=30.0)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
            speed=Decimal("2.0"),
        )
        # Speed changes neither dimensions nor the selected range.
        self.assertEqual((settings.width, settings.height), (640, 360))
        self.assertEqual(settings.speed, Decimal("2.0"))

    def test_slow_motion_caps_frame_rate_at_retimed_source_rate(self):
        # FR-014/FR-027: below 1.0x the ceiling is sourceFps * speed.
        src = vtgtest.make_source(width=640, height=360, fps=30.0)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=20,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="high",
            speed=Decimal("0.5"),
        )
        self.assertEqual(settings.fps, 15.0)  # min(20, 30 * 0.5)

    def test_speed_above_one_keeps_source_frame_rate_ceiling(self):
        src = vtgtest.make_source(width=640, height=360, fps=10.0)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=20,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="high",
            speed=Decimal("2.0"),
        )
        self.assertEqual(settings.fps, 10.0)


# ---------------------------------------------------------------------------
# FR-028 / section 15.5: dithering
# ---------------------------------------------------------------------------


class TestDither(TransformTestCase):
    def test_every_enumeration_member_accepted(self):
        for mode in ("none", "bayer", "floyd_steinberg", "sierra2", "sierra2_4a"):
            with self.subTest(mode=mode):
                self.assertEqual(transforms.parse_dither(mode), mode)

    def test_surrounding_whitespace_trimmed(self):
        self.assertEqual(transforms.parse_dither("  bayer  "), "bayer")

    def test_comparison_is_case_sensitive(self):
        for bad in ("NONE", "Bayer", "SIERRA2_4A"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_DITHER):
                transforms.parse_dither(bad)

    def test_unknown_mode_rejected_with_permitted_values_listed(self):
        with self.assertRaises(errors.EngineError) as cm:
            transforms.parse_dither("wobble")
        self.assertEqual(cm.exception.code, errors.INVALID_DITHER)
        self.assertEqual(cm.exception.exit_code, errors.EXIT_INVALID_TIMESTAMP)
        for mode in transforms.DITHER_MODES:
            self.assertIn(mode, cm.exception.message)

    def test_filter_graph_fragment_rejected(self):
        for bad in ("none[a];[a]movie=/etc/passwd", "bayer,drawtext=text=x", "none:x=1"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_DITHER):
                transforms.parse_dither(bad)

    def test_bayer_scale_range(self):
        for good in (0, 1, 5, "3"):
            with self.subTest(good=good):
                self.assertIsInstance(transforms.parse_bayer_scale(good), int)
        for bad in (-1, 6, 100, "x", "", None, True, 2.5, "٣"):
            with self.subTest(bad=bad), self.assert_engine_error(errors.INVALID_DITHER):
                transforms.parse_bayer_scale(bad)

    def test_bayer_scale_with_non_bayer_mode_rejected(self):
        with self.assert_engine_error(errors.INVALID_DITHER):
            transforms.resolve_dither(dither="sierra2", bayer_scale=3, profile_name="balanced")

    def test_bayer_scale_with_non_bayer_profile_default_rejected(self):
        with self.assert_engine_error(errors.INVALID_DITHER):
            transforms.resolve_dither(dither=None, bayer_scale=3, profile_name="balanced")

    def test_profile_defaults(self):
        # Section 15.5, reproducing the shipped v0.1.0/v0.2.0 behavior.
        self.assertEqual(
            transforms.resolve_dither(dither=None, bayer_scale=None, profile_name="small"),
            ("bayer", 5),
        )
        for profile in ("balanced", "high", "custom"):
            with self.subTest(profile=profile):
                self.assertEqual(
                    transforms.resolve_dither(dither=None, bayer_scale=None, profile_name=profile),
                    ("sierra2_4a", None),
                )

    def test_explicit_bayer_without_scale_uses_profile_default(self):
        self.assertEqual(
            transforms.resolve_dither(dither="bayer", bayer_scale=None, profile_name="small"),
            ("bayer", 5),
        )

    def test_explicit_bayer_on_non_bayer_profile_uses_fallback_scale(self):
        self.assertEqual(
            transforms.resolve_dither(dither="bayer", bayer_scale=None, profile_name="balanced"),
            ("bayer", transforms.DEFAULT_BAYER_SCALE),
        )

    def test_explicit_scale_wins_over_profile_default(self):
        self.assertEqual(
            transforms.resolve_dither(dither="bayer", bayer_scale=0, profile_name="small"),
            ("bayer", 0),
        )

    def test_filter_argument_serialization(self):
        self.assertEqual(transforms.dither_filter_arg("bayer", 5), "bayer:bayer_scale=5")
        self.assertEqual(transforms.dither_filter_arg("none", None), "none")
        self.assertEqual(transforms.dither_filter_arg("sierra2_4a", None), "sierra2_4a")


# ---------------------------------------------------------------------------
# FR-024: precedence
# ---------------------------------------------------------------------------


class TestPrecedence(TransformTestCase):
    def test_highest_priority_level_wins_per_field(self):
        clip = transforms.TransformSpec(width=100)
        cli = transforms.TransformSpec(width=200, height=201)
        top = transforms.TransformSpec(width=300, height=301, speed=Decimal("2.0"))
        cfg = transforms.TransformSpec(
            width=400, height=401, speed=Decimal("3.0"), dither="sierra2"
        )
        merged = transforms.merge_transforms(clip, cli, top, cfg)
        self.assertEqual(merged.width, 100)  # clip beats the CLI flag
        self.assertEqual(merged.height, 201)  # CLI beats the top-level manifest
        self.assertEqual(merged.speed, Decimal("2.0"))  # manifest top beats config
        self.assertEqual(merged.dither, "sierra2")  # config supplies the rest
        self.assertIsNone(merged.bayer_scale)  # built-in default

    def test_absent_levels_are_skipped(self):
        merged = transforms.merge_transforms(None, transforms.TransformSpec(speed=Decimal("0.5")))
        self.assertEqual(merged.speed, Decimal("0.5"))

    def test_empty_merge_is_all_none(self):
        merged = transforms.merge_transforms(transforms.EMPTY_TRANSFORMS)
        self.assertEqual(merged.supplied_fields(), [])


# ---------------------------------------------------------------------------
# Section 15.2 / SEC-018: filter-chain order and injection surface
# ---------------------------------------------------------------------------


class TestFilterChain(TransformTestCase):
    def test_default_chain_matches_v0_2_0(self):
        chain = transforms.build_filter_chain(
            crop=None, speed=None, fps=15.0, width=640, height=360
        )
        self.assertEqual(chain, "fps=15,scale=640:360:flags=lanczos")

    def test_order_is_crop_setpts_fps_scale(self):
        chain = transforms.build_filter_chain(
            crop=CropRect(10, 20, 640, 360),
            speed=Decimal("2.0"),
            fps=15.0,
            width=320,
            height=180,
        )
        self.assertEqual(
            chain,
            "crop=640:360:10:20,setpts=PTS/2.0,fps=15,scale=320:180:flags=lanczos",
        )
        steps = chain.split(",")
        self.assertLess(steps.index("crop=640:360:10:20"), steps.index("fps=15"))
        self.assertLess(steps.index("setpts=PTS/2.0"), steps.index("fps=15"))
        self.assertLess(steps.index("fps=15"), steps.index("scale=320:180:flags=lanczos"))

    def test_speed_one_emits_no_setpts(self):
        chain = transforms.build_filter_chain(
            crop=None, speed=Decimal("1.0"), fps=15.0, width=640, height=360
        )
        self.assertNotIn("setpts", chain)

    def test_odd_crop_converts_pixel_format_instead_of_adjusting(self):
        chain = transforms.build_filter_chain(
            crop=CropRect(1, 3, 641, 361), speed=None, fps=10.0, width=320, height=180
        )
        self.assertTrue(chain.startswith("format=yuv444p,crop=641:361:1:3,"))

    def test_even_crop_needs_no_conversion(self):
        chain = transforms.build_filter_chain(
            crop=CropRect(2, 4, 640, 360), speed=None, fps=10.0, width=320, height=180
        )
        self.assertFalse(chain.startswith("format="))

    def test_both_palette_passes_share_the_identical_chain(self):
        src = vtgtest.make_source(width=1920, height=1080, fps=30.0)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=128,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
            crop=CropRect(10, 20, 1280, 720),
            speed=Decimal("2.0"),
            dither="bayer",
            bayer_scale=3,
        )
        gen = build_palettegen_command("ffmpeg", "s.mp4", 0, 1000, settings, "p.png")
        use = build_paletteuse_command("ffmpeg", "s.mp4", 0, 1000, settings, "p.png", "o.gif")
        chain = "crop=1280:720:10:20,setpts=PTS/2.0,fps=15,scale=640:360:flags=lanczos"
        self.assertEqual(
            gen[gen.index("-vf") + 1], f"{chain},palettegen=max_colors=128:stats_mode=diff"
        )
        self.assertEqual(
            use[use.index("-lavfi") + 1],
            f"{chain}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3",
        )

    def test_commands_never_use_a_shell_and_keep_the_protocol_whitelist(self):
        settings = vtgtest.make_settings()
        for cmd in (
            build_palettegen_command("ffmpeg", "s.mp4", 0, 1000, settings, "p.png"),
            build_paletteuse_command("ffmpeg", "s.mp4", 0, 1000, settings, "p.png", "o.gif"),
            build_preview_command("ffmpeg", "s.mp4", 0, settings, "o.png"),
        ):
            self.assertIsInstance(cmd, list)
            self.assertIn("-protocol_whitelist", cmd)
            self.assertEqual(cmd[cmd.index("-protocol_whitelist") + 1], "file,pipe")

    def test_preview_chain_skips_temporal_filters(self):
        chain = transforms.build_preview_filter_chain(
            crop=CropRect(10, 20, 640, 360), width=320, height=180
        )
        self.assertEqual(chain, "crop=640:360:10:20,scale=320:180:flags=lanczos")
        self.assertNotIn("fps=", chain)
        self.assertNotIn("setpts", chain)

    def test_preview_command_is_full_colour_png(self):
        settings = vtgtest.make_settings()
        cmd = build_preview_command("ffmpeg", "s.mp4", 1500, settings, "o.png")
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "rgb24")
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "png")
        self.assertEqual(cmd[cmd.index("-frames:v") + 1], "1")
        self.assertNotIn("palettegen", " ".join(cmd))
        self.assertNotIn("paletteuse", " ".join(cmd))

    def test_speed_serialization_contains_only_digits_and_a_point(self):
        for value in ("0.25", "1.5", "2", "4.0", "1.001"):
            with self.subTest(value=value):
                text = transforms.speed_str(Decimal(value))
                self.assertRegex(text, r"^[0-9]+(\.[0-9]+)?$")


class TestReportingSerialization(TransformTestCase):
    def test_transformations_object_shape(self):
        src = vtgtest.make_source(width=1920, height=1080, fps=30.0)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="balanced",
            crop=CropRect(320, 180, 1280, 720),
            speed=Decimal("2.0"),
        )
        public = settings.transformations_public()
        self.assertEqual(
            public,
            {
                "crop": {"x": 320, "y": 180, "width": 1280, "height": 720},
                "sourceWidth": 1920,
                "sourceHeight": 1080,
                "effectiveSourceWidth": 1280,
                "effectiveSourceHeight": 720,
                "speed": 2.0,
                "dither": "sierra2_4a",
                "bayerScale": None,
                "upscaled": False,
            },
        )

    def test_no_crop_reports_null_and_equal_effective_dimensions(self):
        src = vtgtest.make_source(width=640, height=360)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="small",
        )
        public = settings.transformations_public()
        self.assertIsNone(public["crop"])
        self.assertEqual(public["effectiveSourceWidth"], public["sourceWidth"])
        self.assertEqual(public["effectiveSourceHeight"], public["sourceHeight"])
        self.assertEqual(public["dither"], "bayer")
        self.assertEqual(public["bayerScale"], 5)

    def test_still_frame_reporting_neutralizes_temporal_fields(self):
        src = vtgtest.make_source(width=640, height=360)
        settings = resolve_effective_settings(
            src,
            max_width=640,
            target_fps=15,
            colors=256,
            loop="forever",
            allow_upscale=False,
            profile_name="small",
            speed=Decimal("2.0"),
        )
        public = settings.transformations_public(still_frame=True)
        self.assertEqual(public["speed"], 1.0)
        self.assertIsNone(public["dither"])
        self.assertIsNone(public["bayerScale"])

    def test_legacy_settings_report_the_profile_dither(self):
        # EffectiveSettings built without the v0.3.0 fields still report a mode.
        settings = vtgtest.make_settings(profile_name="small")
        self.assertEqual(settings.effective_dither, ("bayer", 5))
        self.assertEqual(settings.transformations_public()["dither"], "bayer")

    def test_not_applicable_warning_wording(self):
        one = transforms.not_applicable_warning(["speed"])
        self.assertTrue(one.startswith("TRANSFORMATION_NOT_APPLICABLE: "))
        self.assertIn("does not apply", one)
        many = transforms.not_applicable_warning(["speed", "fps"])
        self.assertTrue(many.startswith("TRANSFORMATION_NOT_APPLICABLE: "))
        self.assertIn("do not apply", many)


if __name__ == "__main__":
    unittest.main()
