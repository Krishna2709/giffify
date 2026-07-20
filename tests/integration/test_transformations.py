"""Integration: version 0.3.0 transformations against real ffmpeg (spec §22.7).

Every assertion here is a *measurement* taken from the bytes the engine wrote --
ffprobe geometry, container duration, decoded frame counts, PNG IHDR -- not the
engine's own report of what it did. The structured result is checked too, but
only alongside the measured value, so a reporting bug cannot mask a pipeline bug.

Determinism notes (no timing races anywhere in this file):

* Frame counts, not wall-clock: every source is generated at 30 fps so each
  profile's target frame rate (10/15/20) divides the source rate exactly and the
  GIF's centisecond frame delay is exact (10 fps -> 10 cs, 15 fps -> 6.67 cs
  quantized identically on every run). Durations are asserted with the §15.4
  tolerance (one output frame or 100 ms, whichever is greater).
* Sizes are compared only as "different" / "identical", never against a
  hard-coded byte count, so an ffmpeg build change cannot make the suite flaky.
* The speed cases use ranges whose speed-adjusted duration is a whole number of
  output frames, so no rounding boundary is ever straddled.
"""

import json
import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import media
from fixtures.geometry import (
    WARN_TRANSFORMATION_NOT_APPLICABLE,
    WARN_UPSCALE_NOT_ALLOWED,
    TransformEngineTestCase,
    probe_duration_ms,
)


class TransformIntegrationCase(TransformEngineTestCase):
    """Shared invocation helpers for the transformation integration tests."""

    def create(self, src, name, *flags, start="0", end="1", profile="balanced", expect=0):
        args = [
            "create",
            "--input",
            src,
            "--start",
            start,
            "--end",
            end,
            "--profile",
            profile,
            "--output-name",
            name,
            *flags,
        ]
        res = self.run_engine(args)
        self.assert_exit(res, expect)
        return res

    def write_manifest(self, data, filename="clips.json"):
        path = os.path.join(self.project, filename)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path


# ---------------------------------------------------------------------------
# Cropping (FR-025, §15.2 step 4 before step 7)
# ---------------------------------------------------------------------------
class TestCropGeometry(TransformIntegrationCase):
    wide: ClassVar[str]
    rotated: ClassVar[str]

    @classmethod
    def generate_media(cls):
        # 1920x1080 @ 30 fps: wide enough that every profile downscales, so the
        # cropped rectangle -- not the source frame -- must drive the output.
        cls.wide = cls.media_file("wide.mp4")
        media.generate_landscape(cls.wide, size="1920x1080", fps=30, duration=2.0)
        # Landscape pixels + 90-degree display matrix -> 360x640 display geometry.
        cls.rotated = cls.media_file("rot.mp4")
        media.generate_rotated(cls.rotated, rotation=90, size="640x360", fps=30, duration=2.0)

    def test_crop_produces_exact_requested_dimensions(self):
        # FR-025: the rectangle is applied exactly -- never rounded, clamped or
        # re-centered. 400x250 is below the balanced 640 maximum, so it is kept
        # verbatim, and the odd-ish 100:50 offsets are honored as given.
        res = self.create(self.wide, "crop-exact.gif", "--crop", "100:50:400:250")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (400, 250))
        self.assertEqual(
            clip["transformations"]["crop"], {"x": 100, "y": 50, "width": 400, "height": 250}
        )
        self.assert_gif_geometry(self.output_path("crop-exact.gif"), 400, 250)

    def test_crop_with_odd_offsets_and_sizes_is_not_adjusted(self):
        # FR-025: an odd offset/size cannot be expressed on a subsampled chroma
        # grid, so the engine must convert the pixel format rather than move the
        # rectangle. The measured output proves the rectangle survived intact.
        res = self.create(self.wide, "crop-odd.gif", "--crop", "101:51:401:251")
        self.assertEqual((res.created[0]["width"], res.created[0]["height"]), (401, 251))
        self.assert_gif_geometry(self.output_path("crop-odd.gif"), 401, 251)

    def test_crop_before_scale_proven_by_measured_dimensions(self):
        # §15.2: crop (step 4) MUST precede scale (step 7). The crop is 4:1
        # (1200x300) while the source frame is 16:9. Scaling to width 600:
        #   crop-then-scale -> 600x150  (aspect 4.00, from the cropped rectangle)
        #   scale-then-crop -> 600x338  (aspect 1.78, from the source frame)
        # Measuring 600x150 is therefore direct evidence of the ordering.
        res = self.create(self.wide, "order.gif", "--crop", "100:200:1200:300", "--width", "600")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (600, 150))
        self.assert_gif_geometry(self.output_path("order.gif"), 600, 150)
        self.assertAlmostEqual(600 / 150, 1200 / 300, places=6)  # cropped aspect preserved
        self.assertNotEqual(clip["height"], 338)  # the source-aspect height
        self.assertEqual(clip["transformations"]["effectiveSourceWidth"], 1200)
        self.assertEqual(clip["transformations"]["effectiveSourceHeight"], 300)
        self.assertEqual(clip["transformations"]["sourceWidth"], 1920)
        self.assertEqual(clip["transformations"]["sourceHeight"], 1080)

    def test_profile_maximum_applies_to_cropped_rectangle(self):
        # FR-025: the profile maximum width applies to the cropped width, and
        # aspect preservation is evaluated on the cropped rectangle. small caps
        # at 480, so a 1280x720 crop becomes 480x270 -- not 480x270 derived from
        # the 1920x1080 frame by coincidence: the 4:3 crop below disambiguates.
        res = self.create(self.wide, "crop-profile.gif", "--crop", "0:0:800:600", profile="small")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (480, 360))  # 4:3, not 16:9
        self.assert_gif_geometry(self.output_path("crop-profile.gif"), 480, 360)

    def test_crop_smaller_than_profile_width_is_not_upscaled(self):
        # FR-025: when the cropped width is already at or below the effective
        # maximum, the cropped width is retained and no upscaling occurs.
        res = self.create(self.wide, "crop-small.gif", "--crop", "0:0:320:240", profile="high")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (320, 240))
        self.assertFalse(clip["transformations"]["upscaled"])
        self.assert_no_warning_token(res, WARN_UPSCALE_NOT_ALLOWED)
        self.assert_gif_geometry(self.output_path("crop-small.gif"), 320, 240)

    def test_crop_on_rotated_source_uses_orientation_normalized_bounds(self):
        # FR-025: the rectangle is validated against the orientation-normalized
        # (display) dimensions reported by inspection, not the coded dimensions.
        inspect = self.run_engine(["inspect", "--input", self.rotated])
        self.assert_exit(inspect, 0)
        self.assertEqual(
            (inspect.result["source"]["width"], inspect.result["source"]["height"]), (360, 640)
        )
        # A rectangle that fits the 360x640 display frame is accepted and applied
        # in display geometry.
        res = self.create(self.rotated, "rot-crop.gif", "--crop", "0:100:360:320")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (360, 320))
        self.assertEqual(clip["transformations"]["sourceWidth"], 360)
        self.assertEqual(clip["transformations"]["sourceHeight"], 640)
        self.assert_gif_geometry(self.output_path("rot-crop.gif"), 360, 320)

    def test_crop_matching_coded_geometry_of_rotated_source_is_rejected(self):
        # The 640x360 *coded* rectangle does not fit the 360x640 display frame,
        # so it must be rejected -- proof the bounds check is not using the raw
        # coded dimensions.
        res = self.create(self.rotated, "rot-bad.gif", "--crop", "0:0:640:360", expect=6)
        self.assert_error_code(res, "INVALID_CROP")
        self.assert_status(res, "validation_failed")
        self.assert_no_media_produced()


# ---------------------------------------------------------------------------
# Explicit resizing (FR-026)
# ---------------------------------------------------------------------------
class TestExplicitDimensions(TransformIntegrationCase):
    wide: ClassVar[str]
    small: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.wide = cls.media_file("wide.mp4")
        media.generate_landscape(cls.wide, size="1920x1080", fps=30, duration=2.0)
        cls.small = cls.media_file("small.mp4")
        media.generate_landscape(cls.small, size="640x360", fps=30, duration=2.0)

    def test_explicit_odd_width_is_honored_exactly(self):
        # FR-026 parity rule: an explicitly supplied bound is honored exactly,
        # odd values included (GIF has no even-dimension constraint), and the
        # derived side rounds to the nearest integer, odd or even, exactly as
        # v0.1.0/v0.2.0 did: 1080 * 601 / 1920 = 338.06 -> 338.
        res = self.create(self.wide, "odd-width.gif", "--width", "601")
        clip = res.created[0]
        self.assertEqual(clip["width"], 601)
        self.assertEqual(clip["width"] % 2, 1, "explicit odd width must not be rounded")
        self.assertEqual(clip["height"], round(1080 * 601 / 1920))
        self.assertEqual(clip["height"], 338)
        self.assert_gif_geometry(self.output_path("odd-width.gif"), 601, 338)

    def test_explicit_odd_height_is_honored_exactly(self):
        # The derived width here is ODD (1920 * 301 / 1080 = 535.19 -> 535).
        # Under the retired even-rounding rule it became 536; FR-026 now
        # mandates round() on every path, so 535 is the correct answer and the
        # one v0.1.0/v0.2.0 arithmetic produces.
        res = self.create(self.wide, "odd-height.gif", "--height", "301")
        clip = res.created[0]
        self.assertEqual(clip["height"], 301)
        self.assertEqual(clip["width"], round(1920 * 301 / 1080))
        self.assertEqual(clip["width"], 535)
        self.assert_gif_geometry(self.output_path("odd-height.gif"), 535, 301)

    def test_both_bounds_fit_inside_the_box_preserving_aspect(self):
        # FR-026: with both bounds the frame is scaled to the largest size that
        # satisfies both while preserving the aspect ratio. 1920x1080 into an
        # 800x200 box is height-limited: 200 * 16/9 = 355.6 -> 356.
        res = self.create(self.wide, "box.gif", "--width", "800", "--height", "200")
        clip = res.created[0]
        self.assertLessEqual(clip["width"], 800)
        self.assertLessEqual(clip["height"], 200)
        self.assertEqual((clip["width"], clip["height"]), (356, 200))
        self.assertAlmostEqual(clip["width"] / clip["height"], 1920 / 1080, delta=0.02)
        self.assert_gif_geometry(self.output_path("box.gif"), 356, 200)

    def test_explicit_width_overrides_profile_maximum(self):
        # FR-026: a profile maximum is a default bound, not a ceiling. small
        # caps at 480; --width 800 must win on an 1920-wide source.
        res = self.create(self.wide, "over-w.gif", "--width", "800", profile="small")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (800, 450))
        self.assert_gif_geometry(self.output_path("over-w.gif"), 800, 450)

    def test_explicit_height_overrides_profile_maximum(self):
        # The other direction: an explicit height also displaces the profile's
        # maximum width entirely. 1920 * 700 / 1080 = 1244.4 -> 1244 (nearest).
        res = self.create(self.wide, "over-h.gif", "--height", "700", profile="small")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (1244, 700))
        self.assert_gif_geometry(self.output_path("over-h.gif"), 1244, 700)

    def test_upscale_blocked_without_flag_and_warns(self):
        # FR-026: without allowUpscale the output is clamped to the effective
        # source dimensions and UPSCALE_NOT_ALLOWED is emitted, because the
        # clamped bound was explicitly supplied.
        res = self.create(self.small, "no-up.gif", "--width", "1280")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (640, 360))
        self.assertFalse(clip["transformations"]["upscaled"])
        warning = self.assert_warning_token(res, WARN_UPSCALE_NOT_ALLOWED)
        self.assertIn("640x360", warning)
        self.assert_gif_geometry(self.output_path("no-up.gif"), 640, 360)

    def test_upscale_honored_with_flag_and_does_not_warn(self):
        res = self.create(self.small, "up.gif", "--width", "1280", "--allow-upscale")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (1280, 720))
        self.assertTrue(clip["transformations"]["upscaled"])
        self.assert_no_warning_token(res, WARN_UPSCALE_NOT_ALLOWED)
        self.assert_gif_geometry(self.output_path("up.gif"), 1280, 720)

    def test_profile_only_undersized_source_does_not_warn(self):
        # FR-026: a profile maximum that simply exceeds a small source MUST NOT
        # produce a warning -- profile-only jobs warn exactly as in v0.2.0.
        res = self.create(self.small, "profile-only.gif", profile="high")  # high wants 960
        self.assertEqual((res.created[0]["width"], res.created[0]["height"]), (640, 360))
        self.assert_no_warning_token(res, WARN_UPSCALE_NOT_ALLOWED)


# ---------------------------------------------------------------------------
# Backward-compatibility regression: every derivation path (FR-026, AC-0.3.13)
# ---------------------------------------------------------------------------
class TestProfileOnlyRegression(TransformIntegrationCase):
    """Lock in the v0.1.0/v0.2.0 derived-dimension arithmetic.

    The source is 1000x502 -- deliberately chosen so that the profile-only
    derivation ``round(502 * maxWidth / 1000)`` yields an ODD height for two of
    the three profiles:

        small    480 -> round(240.96) = 241  (odd)
        balanced 640 -> round(321.28) = 321  (odd)
        high     960 -> round(481.92) = 482  (even)

    The same arithmetic governs an explicit ``--width``, which existed in
    v0.1.0. An earlier 0.3.0 draft rounded the derived side of an explicit
    bound to an even value, silently shifting 320x161 to 320x160 and 500x251 to
    500x252 for this very source. FR-026 "Dimension parity" now mandates
    round()-to-nearest on EVERY path -- profile-only, width-only, height-only
    and both-bounds -- so these expectations are the ones the released 0.2.0
    engine produces, verified by executing it side by side with 0.3.0.
    """

    odd: ClassVar[str]

    EXPECTED: ClassVar[dict] = {
        "small": (480, 241, 10),
        "balanced": (640, 321, 15),
        "high": (960, 482, 20),
    }

    @classmethod
    def generate_media(cls):
        cls.odd = cls.media_file("odd-1000x502.mp4")
        media.generate_landscape(cls.odd, size="1000x502", fps=30, duration=2.0)

    def _assert_profile_only(self, profile):
        width, height, fps = self.EXPECTED[profile]
        name = f"legacy-{profile}.gif"
        res = self.create(self.odd, name, profile=profile)
        clip = res.created[0]
        self.assertEqual(
            (clip["width"], clip["height"]),
            (width, height),
            f"{profile}: profile-only dimensions changed from the v0.2.0 contract",
        )
        self.assertEqual(clip["fps"], fps)
        # Measured, not merely reported.
        self.assert_gif_geometry(self.output_path(name), width, height)
        # A profile-only job applies no transformation and warns about nothing.
        tx = clip["transformations"]
        self.assertIsNone(tx["crop"])
        self.assertEqual(tx["speed"], 1.0)
        self.assertFalse(tx["upscaled"])
        self.assertEqual(tx["effectiveSourceWidth"], 1000)
        self.assertEqual(tx["effectiveSourceHeight"], 502)
        self.assertEqual(clip["outputDurationMs"], clip["durationMs"])
        self.assertEqual(self.warnings_of(res), [])
        return clip

    def test_profile_only_small_keeps_legacy_odd_height(self):
        clip = self._assert_profile_only("small")
        self.assertEqual(clip["height"] % 2, 1, "the legacy round() path must survive")
        # §15.5: small's default dither reproduces the v0.1.0/v0.2.0 behavior.
        self.assertEqual(clip["transformations"]["dither"], "bayer")
        self.assertEqual(clip["transformations"]["bayerScale"], 5)

    def test_profile_only_balanced_keeps_legacy_odd_height(self):
        clip = self._assert_profile_only("balanced")
        self.assertEqual(clip["height"] % 2, 1, "the legacy round() path must survive")
        self.assertEqual(clip["transformations"]["dither"], "sierra2_4a")
        self.assertIsNone(clip["transformations"]["bayerScale"])

    def test_profile_only_high_keeps_legacy_derived_height(self):
        clip = self._assert_profile_only("high")
        self.assertEqual(clip["height"], 482)
        self.assertEqual(clip["transformations"]["dither"], "sierra2_4a")

    # The exact dimensions the released v0.2.0 engine produced for an explicit
    # --width on this source, captured by executing it. Two of the three were
    # measured regressions in the 0.3.0 review.
    V020_EXPLICIT_WIDTH: ClassVar[dict] = {320: 161, 500: 251, 640: 321}

    def test_explicit_width_keeps_v0_2_0_dimensions(self):
        for width, height in sorted(self.V020_EXPLICIT_WIDTH.items()):
            with self.subTest(width=width):
                name = f"explicit-{width}.gif"
                res = self.create(self.odd, name, "--width", str(width))
                clip = res.created[0]
                self.assertEqual(
                    (clip["width"], clip["height"]),
                    (width, height),
                    f"--width {width} diverged from the v0.2.0 contract",
                )
                self.assertEqual(clip["height"], round(502 * width / 1000))
                # Measured off the produced GIF, not merely reported.
                self.assert_gif_geometry(self.output_path(name), width, height)

    def test_manifest_width_keeps_v0_2_0_dimensions(self):
        # The same regression reached the manifest top-level and clip-level
        # width fields, so lock those paths too.
        manifest = os.path.join(self.project, "widths.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schemaVersion": 1,
                    "input": self.odd,
                    "width": 320,
                    "clips": [
                        {"name": "top", "start": "0", "end": "1"},
                        {"name": "clip", "start": "0", "end": "1", "width": 500},
                    ],
                },
                fh,
            )
        res = self.run_engine(["batch", "--manifest", manifest])
        self.assert_exit(res, 0)
        by_name = {c["name"]: c for c in res.created}
        self.assertEqual((by_name["top"]["width"], by_name["top"]["height"]), (320, 161))
        self.assertEqual((by_name["clip"]["width"], by_name["clip"]["height"]), (500, 251))
        self.assert_gif_geometry(self.output_path("top.gif"), 320, 161)
        self.assert_gif_geometry(self.output_path("clip.gif"), 500, 251)

    def test_explicit_width_derivation_equals_profile_only_derivation(self):
        # The two paths share one helper, so the same driving width must give
        # the same derived height whichever supplied it (balanced max = 640).
        explicit = self.create(self.odd, "same-explicit.gif", "--width", "640").created[0]
        profile = self.create(self.odd, "same-profile.gif", profile="balanced").created[0]
        self.assertEqual(
            (explicit["width"], explicit["height"]), (profile["width"], profile["height"])
        )
        self.assertEqual((explicit["width"], explicit["height"]), (640, 321))

    def test_profile_only_output_is_byte_identical_across_runs(self):
        # NFR-002: same source, range, profile and settings -> identical output.
        first = self.create(self.odd, "det-a.gif", profile="balanced")
        second = self.create(self.odd, "det-b.gif", profile="balanced")
        self.assertEqual(first.created[0]["sizeBytes"], second.created[0]["sizeBytes"])
        with open(self.output_path("det-a.gif"), "rb") as fh:
            a = fh.read()
        with open(self.output_path("det-b.gif"), "rb") as fh:
            b = fh.read()
        self.assertEqual(a, b, "profile-only output is not deterministic")


# ---------------------------------------------------------------------------
# Playback speed (FR-027, §15.4)
# ---------------------------------------------------------------------------
class TestSpeed(TransformIntegrationCase):
    """Durations are exact by construction.

    The source is 640x360 @ 30 fps. Profile ``small`` targets 10 fps, which is
    below the 30 fps source cap for every speed >= 1.0, so the effective output
    frame rate is exactly 10 fps and each frame's GIF delay is exactly 10 cs.
    A 4000 ms range therefore yields 40 / 20 / 80 frames at 1.0 / 2.0 / 0.5.
    """

    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("speed.mp4")
        media.generate_landscape(cls.src, size="640x360", fps=30, duration=4.0)

    def _speed_case(self, speed, name, expected_out_ms, expected_frames, fps=10.0):
        res = self.create(self.src, name, "--speed", speed, start="0", end="4", profile="small")
        clip = res.created[0]
        self.assertEqual(clip["durationMs"], 4000, "the selected source range must not change")
        self.assertEqual(clip["outputDurationMs"], expected_out_ms)
        self.assertEqual(clip["transformations"]["speed"], float(speed))
        path = self.output_path(name)
        self.assert_gif_duration_ms(path, expected_out_ms, fps=fps)
        self.assert_gif_frames(path, expected_frames)
        return res

    def test_speed_1_0_leaves_duration_unchanged(self):
        res = self._speed_case("1.0", "sp-1.gif", 4000, 40)
        self.assertEqual(res.created[0]["fps"], 10)

    def test_speed_2_0_halves_output_duration(self):
        self._speed_case("2.0", "sp-2.gif", 2000, 20)

    def test_speed_0_5_doubles_output_duration(self):
        self._speed_case("0.5", "sp-05.gif", 8000, 80)

    def test_speed_below_one_caps_fps_to_retimed_source_rate(self):
        # FR-027: below 1.0 the retimed stream's intrinsic rate is sourceFps *
        # speed, and the effective output rate must not exceed it. Profile high
        # asks for 20 fps; 30 * 0.25 = 7.5 wins.
        res = self.create(
            self.src, "sp-cap.gif", "--speed", "0.25", start="0", end="2", profile="high"
        )
        clip = res.created[0]
        self.assertEqual(clip["fps"], 7.5)
        self.assertEqual(clip["durationMs"], 2000)
        self.assertEqual(clip["outputDurationMs"], 8000)
        path = self.output_path("sp-cap.gif")
        self.assert_gif_frames(path, 60)  # 8.0 s at 7.5 fps
        self.assert_gif_duration_ms(path, 8000, fps=7.5)

    def test_speed_does_not_change_output_geometry(self):
        # FR-027 is purely temporal: geometry is untouched.
        slow = self.create(self.src, "geo-slow.gif", "--speed", "0.5", end="1", profile="small")
        fast = self.create(self.src, "geo-fast.gif", "--speed", "2.0", end="1", profile="small")
        self.assertEqual(
            (slow.created[0]["width"], slow.created[0]["height"]),
            (fast.created[0]["width"], fast.created[0]["height"]),
        )
        self.assert_gif_geometry(self.output_path("geo-slow.gif"), 480, 270)
        self.assert_gif_geometry(self.output_path("geo-fast.gif"), 480, 270)

    def test_speed_combines_with_crop_and_explicit_width(self):
        # The full step 4 -> 5 -> 6 -> 7 chain in one job: crop 800x200 (4:1),
        # width 400 -> 400x100, speed 2.0 over a 4 s range -> 2 s at 10 fps.
        res = self.create(
            self.src,
            "combo.gif",
            "--crop",
            "0:0:400:100",
            "--width",
            "200",
            "--speed",
            "2.0",
            start="0",
            end="4",
            profile="small",
        )
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (200, 50))
        self.assertEqual(clip["outputDurationMs"], 2000)
        path = self.output_path("combo.gif")
        self.assert_gif_geometry(path, 200, 50)
        self.assert_gif_frames(path, 20)
        self.assert_gif_duration_ms(path, 2000, fps=10.0)


# ---------------------------------------------------------------------------
# Dithering (FR-028, §15.5)
# ---------------------------------------------------------------------------
class TestDither(TransformIntegrationCase):
    src: ClassVar[str]

    MODES: ClassVar[tuple] = ("none", "bayer", "floyd_steinberg", "sierra2", "sierra2_4a")

    @classmethod
    def generate_media(cls):
        # testsrc has gradients and colour ramps, so the dither modes genuinely
        # differ in the encoded bytes rather than collapsing to the same output.
        cls.src = cls.media_file("dither.mp4")
        media.generate_landscape(cls.src, size="640x360", fps=30, duration=2.0)

    def test_every_dither_mode_produces_a_valid_gif(self):
        for mode in self.MODES:
            with self.subTest(dither=mode):
                name = f"dither-{mode}.gif"
                res = self.create(self.src, name, "--dither", mode, profile="balanced")
                clip = res.created[0]
                self.assertEqual(clip["transformations"]["dither"], mode)
                self.assert_gif_geometry(self.output_path(name), 640, 360)
                self.assert_gif_frames(self.output_path(name), 15)

    def test_dither_none_differs_measurably_from_the_default(self):
        # §15.5: balanced defaults to sierra2_4a. Turning dithering off must
        # change the encoded bytes -- a silently ignored --dither would produce
        # an identical file.
        default = self.create(self.src, "d-default.gif", profile="balanced")
        none = self.create(self.src, "d-none.gif", "--dither", "none", profile="balanced")
        with open(self.output_path("d-default.gif"), "rb") as fh:
            default_bytes = fh.read()
        with open(self.output_path("d-none.gif"), "rb") as fh:
            none_bytes = fh.read()
        self.assertNotEqual(default_bytes, none_bytes, "--dither none changed nothing")
        self.assertNotEqual(
            default.created[0]["sizeBytes"],
            none.created[0]["sizeBytes"],
            "--dither none produced an identically sized file",
        )
        self.assertEqual(default.created[0]["transformations"]["dither"], "sierra2_4a")

    def test_profile_default_matches_the_explicit_equivalent(self):
        # §15.5 / NFR-006: the documented profile default must be exactly what a
        # job with no dither value already produced in v0.2.0.
        implicit = self.create(self.src, "imp.gif", profile="balanced")
        explicit = self.create(self.src, "exp.gif", "--dither", "sierra2_4a", profile="balanced")
        with open(self.output_path("imp.gif"), "rb") as fh:
            a = fh.read()
        with open(self.output_path("exp.gif"), "rb") as fh:
            b = fh.read()
        self.assertEqual(a, b, "balanced's default is not sierra2_4a in practice")
        self.assertEqual(implicit.created[0]["sizeBytes"], explicit.created[0]["sizeBytes"])

    def test_small_profile_default_is_bayer_scale_5(self):
        implicit = self.create(self.src, "small-imp.gif", profile="small")
        explicit = self.create(
            self.src, "small-exp.gif", "--dither", "bayer", "--bayer-scale", "5", profile="small"
        )
        self.assertEqual(implicit.created[0]["transformations"]["dither"], "bayer")
        self.assertEqual(implicit.created[0]["transformations"]["bayerScale"], 5)
        self.assertEqual(explicit.created[0]["transformations"]["bayerScale"], 5)
        with open(self.output_path("small-imp.gif"), "rb") as fh:
            a = fh.read()
        with open(self.output_path("small-exp.gif"), "rb") as fh:
            b = fh.read()
        self.assertEqual(a, b, "small's default is not bayer/5 in practice")

    def test_bayer_scale_changes_the_encoded_bytes(self):
        coarse = self.create(
            self.src, "b5.gif", "--dither", "bayer", "--bayer-scale", "5", profile="balanced"
        )
        fine = self.create(
            self.src, "b0.gif", "--dither", "bayer", "--bayer-scale", "0", profile="balanced"
        )
        self.assertEqual(coarse.created[0]["transformations"]["bayerScale"], 5)
        self.assertEqual(fine.created[0]["transformations"]["bayerScale"], 0)
        with open(self.output_path("b5.gif"), "rb") as fh:
            a = fh.read()
        with open(self.output_path("b0.gif"), "rb") as fh:
            b = fh.read()
        self.assertNotEqual(a, b, "bayerScale had no effect on the encoded GIF")

    def test_same_dither_settings_are_deterministic(self):
        # NFR-002 for a transformed job.
        first = self.create(self.src, "det1.gif", "--dither", "sierra2", profile="balanced")
        second = self.create(self.src, "det2.gif", "--dither", "sierra2", profile="balanced")
        with open(self.output_path("det1.gif"), "rb") as fh:
            a = fh.read()
        with open(self.output_path("det2.gif"), "rb") as fh:
            b = fh.read()
        self.assertEqual(a, b)
        self.assertEqual(first.created[0]["sizeBytes"], second.created[0]["sizeBytes"])


# ---------------------------------------------------------------------------
# Preview frames (FR-029, §13.4)
# ---------------------------------------------------------------------------
class TestPreview(TransformIntegrationCase):
    wide: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.wide = cls.media_file("preview.mp4")
        media.generate_landscape(cls.wide, size="1920x1080", fps=30, duration=2.0)

    def preview(self, *flags, expect=0):
        res = self.run_engine(["preview", "--input", self.wide, *flags])
        self.assert_exit(res, expect)
        return res

    def test_preview_writes_one_png_and_no_gif(self):
        res = self.preview("--at", "00:00:01.000", "--output-name", "frame.png")
        self.assert_status(res, "success")
        self.assertEqual(res.created, [], "created MUST be empty for preview (§13.4)")
        self.assertEqual(res.summary["created"], 0)
        self.assertEqual(res.summary["previews"], 1)
        previews = self.previews_of(res)
        self.assertEqual(len(previews), 1)
        self.assertEqual(self.list_output(), ["frame.png"])
        self.assertEqual([n for n in self.list_output() if n.endswith(".gif")], [])
        entry = previews[0]
        self.assertEqual(entry["atMs"], 1000)
        self.assertGreater(entry["sizeBytes"], 0)
        # balanced caps at 640 -> 640x360 still, verified from the PNG itself.
        self.assertEqual((entry["width"], entry["height"]), (640, 360))
        self.assert_truecolour_png(self.output_path("frame.png"), 640, 360)
        self.assertEqual(os.path.getsize(self.output_path("frame.png")), entry["sizeBytes"])

    def test_preview_applies_crop_and_explicit_resize(self):
        res = self.preview(
            "--at", "1", "--crop", "320:180:1280:720", "--width", "640", "--output-name", "c.png"
        )
        entry = self.previews_of(res)[0]
        self.assertEqual((entry["width"], entry["height"]), (640, 360))
        self.assert_truecolour_png(self.output_path("c.png"), 640, 360)
        tx = entry["transformations"]
        self.assertEqual(tx["crop"], {"x": 320, "y": 180, "width": 1280, "height": 720})
        self.assertEqual((tx["effectiveSourceWidth"], tx["effectiveSourceHeight"]), (1280, 720))
        # FR-030: a preview reports speed 1.0 and null dither/bayerScale.
        self.assertEqual(tx["speed"], 1.0)
        self.assertIsNone(tx["dither"])
        self.assertIsNone(tx["bayerScale"])

    def test_preview_crop_uses_cropped_aspect_ratio(self):
        # The same crop-before-scale proof as for GIFs, on the still path
        # (§15.2: preview uses steps 1-4 and 7).
        res = self.preview(
            "--at", "1", "--crop", "0:0:1200:300", "--width", "600", "--output-name", "ar.png"
        )
        entry = self.previews_of(res)[0]
        self.assertEqual((entry["width"], entry["height"]), (600, 150))
        self.assert_truecolour_png(self.output_path("ar.png"), 600, 150)

    def test_preview_framing_is_independent_of_temporal_settings(self):
        # FR-029: speed/fps/loop/colors/dither/bayerScale are accepted, MUST NOT
        # change the extracted image, and produce one TRANSFORMATION_NOT_
        # APPLICABLE warning naming the ignored settings.
        plain = self.preview(
            "--at", "1", "--crop", "0:0:640:360", "--width", "320", "--output-name", "p.png"
        )
        self.assertEqual(self.warnings_of(plain), [])
        noisy = self.preview(
            "--at",
            "1",
            "--crop",
            "0:0:640:360",
            "--width",
            "320",
            "--speed",
            "4.0",
            "--fps",
            "3",
            "--dither",
            "none",
            "--output-name",
            "q.png",
        )
        warning = self.assert_warning_token(noisy, WARN_TRANSFORMATION_NOT_APPLICABLE)
        for name in ("speed", "fps", "dither"):
            self.assertIn(name, warning)
        self.assertEqual(
            len([w for w in self.warnings_of(noisy) if w.startswith("TRANSFORMATION")]),
            1,
            "FR-029 requires exactly one warning per invocation",
        )
        with open(self.output_path("p.png"), "rb") as fh:
            plain_bytes = fh.read()
        with open(self.output_path("q.png"), "rb") as fh:
            noisy_bytes = fh.read()
        self.assertEqual(plain_bytes, noisy_bytes, "a temporal setting changed the still")

    def test_preview_upscale_rules_match_the_gif_path(self):
        # FR-029: resizing, including the profile maximum and the upscale rules,
        # is applied exactly as it would be for a GIF of the same clip.
        res = self.preview(
            "--at", "1", "--crop", "0:0:320:180", "--width", "800", "--output-name", "u.png"
        )
        entry = self.previews_of(res)[0]
        self.assertEqual((entry["width"], entry["height"]), (320, 180))
        self.assert_warning_token(res, WARN_UPSCALE_NOT_ALLOWED)
        self.assert_truecolour_png(self.output_path("u.png"), 320, 180)

    def test_preview_manifest_form_produces_one_png_per_clip(self):
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": self.wide,
                "profile": "small",
                "clips": [
                    {"name": "opening", "start": "0.5", "end": "1"},
                    {
                        "name": "reaction",
                        "start": "1.5",
                        "end": "2",
                        "crop": "0:0:400:400",
                        "width": "200",
                    },
                ],
            }
        )
        res = self.run_engine(["preview", "--manifest", manifest])
        self.assert_exit(res, 0)
        self.assertEqual(res.created, [])
        self.assertEqual(res.summary["previews"], 2)
        previews = self.previews_of(res)
        self.assertEqual(
            sorted(self.list_output()),
            ["opening_00-00-00.500.png", "reaction_00-00-01.500.png"],
        )
        first, second = previews
        self.assertEqual(first["atMs"], 500)
        self.assertEqual(second["atMs"], 1500)
        # Clip 1: no crop, small profile caps at 480 -> 480x270.
        self.assert_truecolour_png(
            self.output_path("opening_00-00-00.500.png"), first["width"], first["height"]
        )
        self.assertEqual((first["width"], first["height"]), (480, 270))
        # Clip 2: square crop scaled to width 200 -> 200x200 (clip-level values).
        self.assertEqual((second["width"], second["height"]), (200, 200))
        self.assert_truecolour_png(self.output_path("reaction_00-00-01.500.png"), 200, 200)

    def test_create_reports_an_empty_previews_array(self):
        # §13.4: previews MUST be present and empty for create/batch.
        res = self.create(self.wide, "novel.gif", profile="small")
        self.assertIn("previews", res.result)
        self.assertEqual(res.result["previews"], [])
        self.assertEqual(res.summary["previews"], 0)


# ---------------------------------------------------------------------------
# Manifest transformations and precedence (FR-024, §10.4)
# ---------------------------------------------------------------------------
class TestManifestTransformations(TransformIntegrationCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("manifest.mp4")
        media.generate_landscape(cls.src, size="640x360", fps=30, duration=4.0)

    def test_clip_level_beats_top_level_and_command_line_flag(self):
        # FR-024 / §9.3 refinement: clip-level > CLI flag > top-level manifest.
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": self.src,
                "profile": "balanced",
                "width": 500,  # top-level: lowest of the three
                "dither": "floyd_steinberg",
                "clips": [
                    {
                        "name": "specific",
                        "start": "0",
                        "end": "1",
                        "width": 300,  # clip-level: must win over --width 400
                        "speed": 2.0,
                        "dither": "none",
                    },
                    {"name": "generic", "start": "0", "end": "1"},
                ],
            }
        )
        res = self.run_engine(
            ["batch", "--manifest", manifest, "--width", "400", "--dither", "bayer"]
        )
        self.assert_exit(res, 0)
        by_name = {c["name"]: c for c in res.created}
        specific, generic = by_name["specific"], by_name["generic"]

        # Clip-level width/speed/dither win over the CLI flags and the top level.
        self.assertEqual(specific["width"], 300)
        self.assertEqual(specific["transformations"]["speed"], 2.0)
        self.assertEqual(specific["transformations"]["dither"], "none")
        self.assertEqual(specific["outputDurationMs"], 500)
        self.assert_gif_geometry(self.output_path("specific.gif"), 300, specific["height"])

        # The clip that specifies nothing takes the CLI flags, which in turn beat
        # the top-level manifest values (500 / floyd_steinberg).
        self.assertEqual(generic["width"], 400)
        self.assertEqual(generic["transformations"]["dither"], "bayer")
        self.assertEqual(generic["transformations"]["speed"], 1.0)
        self.assertEqual(generic["outputDurationMs"], 1000)
        self.assert_gif_geometry(self.output_path("generic.gif"), 400, generic["height"])

    def test_top_level_manifest_beats_project_configuration(self):
        with open(os.path.join(self.project, ".video-to-gif.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {"schemaVersion": 1, "transformations": {"width": 200, "dither": "sierra2"}}, fh
            )
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": self.src,
                "profile": "balanced",
                "width": 320,
                "dither": "none",
                "clips": [{"name": "only", "start": "0", "end": "1"}],
            }
        )
        res = self.run_engine(["batch", "--manifest", manifest])
        self.assert_exit(res, 0)
        clip = res.created[0]
        self.assertEqual(clip["width"], 320)
        self.assertEqual(clip["transformations"]["dither"], "none")
        self.assert_gif_geometry(self.output_path("only.gif"), 320, clip["height"])

    def test_project_configuration_supplies_defaults(self):
        with open(os.path.join(self.project, ".video-to-gif.json"), "w", encoding="utf-8") as fh:
            json.dump({"schemaVersion": 1, "transformations": {"width": 240, "speed": 2.0}}, fh)
        res = self.create(self.src, "cfg.gif", end="4", profile="balanced")
        clip = res.created[0]
        self.assertEqual(clip["width"], 240)
        self.assertEqual(clip["transformations"]["speed"], 2.0)
        self.assertEqual(clip["outputDurationMs"], 2000)
        self.assert_gif_geometry(self.output_path("cfg.gif"), 240, clip["height"])
        self.assert_gif_duration_ms(self.output_path("cfg.gif"), 2000, fps=15.0)

    def test_per_clip_geometry_and_duration_from_json_manifest(self):
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": self.src,
                "profile": "small",
                "clips": [
                    {
                        "name": "cropped",
                        "start": "0",
                        "end": "2",
                        "crop": {"x": 0, "y": 0, "width": 400, "height": 200},
                        "width": 200,
                    },
                    {"name": "fast", "start": "0", "end": "2", "speed": 2.0, "dither": "none"},
                    {"name": "slow", "start": "0", "end": "1", "speed": 0.5, "width": 160},
                ],
            }
        )
        res = self.run_engine(["batch", "--manifest", manifest])
        self.assert_exit(res, 0)
        self.assertEqual(res.summary["created"], 3)
        by_name = {c["name"]: c for c in res.created}

        cropped = by_name["cropped"]
        self.assertEqual((cropped["width"], cropped["height"]), (200, 100))
        self.assert_gif_geometry(self.output_path("cropped.gif"), 200, 100)

        fast = by_name["fast"]
        self.assertEqual(fast["durationMs"], 2000)
        self.assertEqual(fast["outputDurationMs"], 1000)
        self.assertEqual(fast["transformations"]["dither"], "none")
        self.assert_gif_frames(self.output_path("fast.gif"), 10)  # 1.0 s at 10 fps
        self.assert_gif_duration_ms(self.output_path("fast.gif"), 1000, fps=10.0)

        slow = by_name["slow"]
        self.assertEqual(slow["outputDurationMs"], 2000)
        self.assertEqual(slow["width"], 160)
        self.assert_gif_duration_ms(self.output_path("slow.gif"), 2000, fps=10.0)

    def test_csv_manifest_transformation_columns(self):
        csv_path = os.path.join(self.project, "clips.csv")
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write("name,start,end,crop,width,speed,dither\n")
            fh.write("wide,0,2,0:0:600:150,300,1.0,sierra2_4a\n")
            fh.write("quick,0,2,,240,2.0,none\n")
        res = self.run_engine(["batch", "--manifest", csv_path, "--input", self.src])
        self.assert_exit(res, 0)
        by_name = {c["name"]: c for c in res.created}
        # Derived heights round to nearest (FR-026): 150 * 300 / 600 = 75 for
        # the cropped clip, 720 * 240 / 1280 = 135 for the uncropped one.
        self.assertEqual((by_name["wide"]["width"], by_name["wide"]["height"]), (300, 75))
        self.assert_gif_geometry(self.output_path("wide.gif"), 300, 75)
        self.assertEqual(by_name["quick"]["outputDurationMs"], 1000)
        self.assertEqual(by_name["quick"]["transformations"]["dither"], "none")
        self.assert_gif_geometry(self.output_path("quick.gif"), 240, 135)

    def test_csv_manifest_tolerates_padded_transformation_cells(self):
        # M-1 regression: a leading space in a CSV cell is a spreadsheet
        # artifact. v0.2.0 accepted " 480" for width and every other column
        # still does, so the 0.3.0 transformation columns must not fail the
        # whole batch with INVALID_DIMENSIONS over padding.
        csv_path = os.path.join(self.project, "padded.csv")
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write("name,start,end,crop,width,speed,dither\n")
            fh.write("padded,0,2, 0:0:600:150 , 300 , 1.0 , sierra2_4a \n")
        res = self.run_engine(["batch", "--manifest", csv_path, "--input", self.src])
        self.assert_exit(res, 0)
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (300, 75))
        self.assertEqual(clip["transformations"]["dither"], "sierra2_4a")
        self.assert_gif_geometry(self.output_path("padded.gif"), 300, 75)


# ---------------------------------------------------------------------------
# Reporting (FR-030)
# ---------------------------------------------------------------------------
class TestTransformationReporting(TransformIntegrationCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("report.mp4")
        media.generate_landscape(cls.src, size="1280x720", fps=30, duration=4.0)

    def test_created_entry_reports_every_transformation_field(self):
        res = self.create(
            self.src,
            "report.gif",
            "--crop",
            "40:20:800:400",
            "--width",
            "401",
            "--speed",
            "2.0",
            "--dither",
            "bayer",
            "--bayer-scale",
            "3",
            start="0",
            end="4",
            profile="balanced",
        )
        clip = res.created[0]
        self.assertEqual(clip["durationMs"], 4000)
        self.assertEqual(clip["outputDurationMs"], 2000)
        self.assertEqual(clip["width"], 401)
        self.assertEqual(clip["height"], 200)  # 400 * 401 / 800 = 200.5 -> 200 (nearest)
        self.assertEqual(
            clip["transformations"],
            {
                "crop": {"x": 40, "y": 20, "width": 800, "height": 400},
                "sourceWidth": 1280,
                "sourceHeight": 720,
                "effectiveSourceWidth": 800,
                "effectiveSourceHeight": 400,
                "speed": 2.0,
                "dither": "bayer",
                "bayerScale": 3,
                "upscaled": False,
            },
        )
        # The report matches the bytes.
        path = self.output_path("report.gif")
        self.assert_gif_geometry(path, 401, 200)
        self.assert_gif_duration_ms(path, 2000, fps=15.0)
        self.assertEqual(os.path.getsize(path), clip["sizeBytes"])
        measured = probe_duration_ms(path)
        self.assertIsNotNone(measured)

    def test_result_schema_version_is_unchanged(self):
        # FR-030 / AC-0.3.13: every 0.3.0 field is additive; schemaVersion is 1.
        res = self.create(self.src, "schema.gif", "--speed", "1.5", profile="small")
        self.assertEqual(res.result["schemaVersion"], 1)
        self.assertIn("previews", res.result)
        self.assertIn("previews", res.result["summary"])
        self.assertIn("outputDurationMs", res.created[0])
        self.assertIn("transformations", res.created[0])

    def test_upscaled_flag_is_reported_true_only_under_allow_upscale(self):
        clamped = self.create(self.src, "clamp.gif", "--width", "2000")
        self.assertFalse(clamped.created[0]["transformations"]["upscaled"])
        self.assertEqual(clamped.created[0]["width"], 1280)
        raised = self.create(self.src, "raise.gif", "--width", "2000", "--allow-upscale")
        self.assertTrue(raised.created[0]["transformations"]["upscaled"])
        self.assertEqual(raised.created[0]["width"], 2000)
        # 720 * 2000 / 1280 = 1125.0 exactly; round() keeps the odd value.
        self.assert_gif_geometry(self.output_path("raise.gif"), 2000, 1125)


if __name__ == "__main__":
    unittest.main()
