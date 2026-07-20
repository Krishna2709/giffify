"""Executable acceptance suite for version 0.3.0 transformations (spec §23).

One test per acceptance criterion (AC-0.3.1 .. AC-0.3.13), named
``test_ac_0_3_NN_*`` with the criterion text quoted in the docstring. All media
is synthetic and generated at runtime outside the repository; the engine is
exercised end to end as a subprocess with real ffmpeg; and every claim is checked
both against the structured JSON result contract (spec §13) and against the bytes
the engine actually wrote (ffprobe geometry/duration, PNG IHDR, filesystem side
effects), so a criterion cannot pass on the engine's own say-so.

Source geometry note: the criteria name a 1920x1080 source, a 16:9 source, a
640-pixel-wide source and a four-second range. Each is generated exactly as
specified; where a criterion says "a source at least 800 pixels wide" the
1920x1080 fixture is used.
"""

import json
import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import media
from fixtures.geometry import (
    WARN_UPSCALE_NOT_ALLOWED,
    TransformEngineTestCase,
)

DITHER_MODES = ("none", "bayer", "floyd_steinberg", "sierra2", "sierra2_4a")


class TestAcceptanceTransformations(TransformEngineTestCase):
    hd: ClassVar[str]
    sd: ClassVar[str]
    odd: ClassVar[str]

    @classmethod
    def generate_media(cls):
        # 1920x1080 @ 30 fps -- the geometry AC-0.3.1, AC-0.3.3 and AC-0.3.5 name.
        cls.hd = cls.media_file("hd-1920x1080.mp4")
        media.generate_landscape(cls.hd, size="1920x1080", fps=30, duration=2.0)
        # 640 pixels wide, four seconds long -- AC-0.3.4 and AC-0.3.6.
        cls.sd = cls.media_file("sd-640x360.mp4")
        media.generate_landscape(cls.sd, size="640x360", fps=30, duration=4.0)
        # Derived height is odd for two profiles -- the AC-0.3.13 regression lock.
        cls.odd = cls.media_file("odd-1000x502.mp4")
        media.generate_landscape(cls.odd, size="1000x502", fps=30, duration=2.0)

    # -- helpers -----------------------------------------------------------
    def create(self, src, name, *flags, start="0", end="1", profile="balanced", expect=0):
        res = self.run_engine(
            [
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
        )
        self.assert_exit(res, expect)
        return res

    def write_manifest(self, data, filename="clips.json"):
        path = os.path.join(self.project, filename)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    # -- AC-0.3.1 ----------------------------------------------------------
    def test_ac_0_3_01_crop_applied(self):
        """AC-0.3.1: Given a 1920x1080 source and --crop 320:180:1280:720 with a
        profile whose maximum width is 640, the output GIF is 640x360, confirming
        that cropping occurred before scaling and that the aspect ratio of the
        cropped rectangle was preserved."""
        res = self.create(self.hd, "ac1.gif", "--crop", "320:180:1280:720", profile="balanced")
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (640, 360))
        self.assert_gif_geometry(self.output_path("ac1.gif"), 640, 360)
        # The cropped rectangle -- not the source frame -- drove the result.
        self.assertEqual(
            clip["transformations"]["crop"], {"x": 320, "y": 180, "width": 1280, "height": 720}
        )
        self.assertEqual(clip["transformations"]["effectiveSourceWidth"], 1280)
        self.assertEqual(clip["transformations"]["effectiveSourceHeight"], 720)
        self.assertAlmostEqual(640 / 360, 1280 / 720, places=6)

    # -- AC-0.3.2 ----------------------------------------------------------
    def test_ac_0_3_02_crop_bounds_rejected(self):
        """AC-0.3.2: A crop rectangle that is negative, zero-sized, non-integer,
        or extends beyond the orientation-normalized source dimensions is
        rejected during preflight with INVALID_CROP and exit code 6, and no GIF
        is produced."""
        cases = {
            "negative": "-1:0:100:100",
            "zero-sized": "0:0:0:0",
            "non-integer": "0:0:100.5:100",
            "out-of-bounds": "0:0:1921:1080",
            "out-of-bounds-offset": "1900:0:100:100",
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                res = self.create(self.hd, f"ac2-{label}.gif", "--crop", value, expect=6)
                self.assert_status(res, "validation_failed")
                self.assert_error_code(res, "INVALID_CROP")
                # Preflight, not encode (§15.1 step 10).
                self.assertEqual((res.result.get("error") or {}).get("stage"), "validate")
                self.assertEqual(self.list_output(), [])
                self.assert_no_media_produced()

    # -- AC-0.3.3 ----------------------------------------------------------
    def test_ac_0_3_03_explicit_resize_overrides_the_profile(self):
        """AC-0.3.3: With profile small, whose maximum width is 480, an explicit
        --width 800 on a source at least 800 pixels wide produces an
        800-pixel-wide GIF."""
        res = self.create(self.hd, "ac3.gif", "--width", "800", profile="small")
        clip = res.created[0]
        self.assertEqual(clip["width"], 800)
        self.assertEqual(clip["height"], 450)  # aspect preserved
        self.assert_gif_geometry(self.output_path("ac3.gif"), 800, 450)
        # The profile is still small in every other respect (10 fps).
        self.assertEqual(clip["fps"], 10)

    # -- AC-0.3.4 ----------------------------------------------------------
    def test_ac_0_3_04_upscale_gating(self):
        """AC-0.3.4: For a 640-pixel-wide source, --width 1280 without
        --allow-upscale produces a 640-pixel-wide GIF and an UPSCALE_NOT_ALLOWED
        warning; the same request with --allow-upscale produces a
        1280-pixel-wide GIF."""
        blocked = self.create(self.sd, "ac4-blocked.gif", "--width", "1280")
        clip = blocked.created[0]
        self.assertEqual((clip["width"], clip["height"]), (640, 360))
        self.assertFalse(clip["transformations"]["upscaled"])
        self.assert_warning_token(blocked, WARN_UPSCALE_NOT_ALLOWED)
        self.assert_gif_geometry(self.output_path("ac4-blocked.gif"), 640, 360)

        allowed = self.create(self.sd, "ac4-allowed.gif", "--width", "1280", "--allow-upscale")
        clip = allowed.created[0]
        self.assertEqual((clip["width"], clip["height"]), (1280, 720))
        self.assertTrue(clip["transformations"]["upscaled"])
        self.assert_no_warning_token(allowed, WARN_UPSCALE_NOT_ALLOWED)
        self.assert_gif_geometry(self.output_path("ac4-allowed.gif"), 1280, 720)

    # -- AC-0.3.5 ----------------------------------------------------------
    def test_ac_0_3_05_dimension_box(self):
        """AC-0.3.5: With both --width 800 and --height 200 on a 16:9 source, the
        output fits inside the box, preserves the aspect ratio, and is not
        distorted."""
        res = self.create(self.hd, "ac5.gif", "--width", "800", "--height", "200")
        clip = res.created[0]
        self.assertLessEqual(clip["width"], 800)
        self.assertLessEqual(clip["height"], 200)
        # Largest size that satisfies both bounds: height-limited at 200.
        self.assertEqual(clip["height"], 200)
        self.assertEqual((clip["width"], clip["height"]), (356, 200))
        # Not distorted: the output aspect matches the 16:9 source aspect.
        self.assertAlmostEqual(clip["width"] / clip["height"], 1920 / 1080, delta=0.02)
        self.assert_gif_geometry(self.output_path("ac5.gif"), 356, 200)

    # -- AC-0.3.6 ----------------------------------------------------------
    def test_ac_0_3_06_speed_duration(self):
        """AC-0.3.6: A four-second range at --speed 2.0 produces a GIF of
        approximately two seconds, and the same range at --speed 0.5 produces a
        GIF of approximately eight seconds, each within the section 15.4
        tolerance. Both durationMs and outputDurationMs are reported."""
        fast = self.create(
            self.sd, "ac6-fast.gif", "--speed", "2.0", start="0", end="4", profile="small"
        )
        clip = fast.created[0]
        self.assertEqual(clip["durationMs"], 4000)  # the source range is unchanged
        self.assertEqual(clip["outputDurationMs"], 2000)
        self.assertEqual(clip["transformations"]["speed"], 2.0)
        self.assert_gif_duration_ms(self.output_path("ac6-fast.gif"), 2000, fps=10.0)
        self.assert_gif_frames(self.output_path("ac6-fast.gif"), 20)

        slow = self.create(
            self.sd, "ac6-slow.gif", "--speed", "0.5", start="0", end="4", profile="small"
        )
        clip = slow.created[0]
        self.assertEqual(clip["durationMs"], 4000)
        self.assertEqual(clip["outputDurationMs"], 8000)
        self.assertEqual(clip["transformations"]["speed"], 0.5)
        self.assert_gif_duration_ms(self.output_path("ac6-slow.gif"), 8000, fps=10.0)
        self.assert_gif_frames(self.output_path("ac6-slow.gif"), 80)

    # -- AC-0.3.7 ----------------------------------------------------------
    def test_ac_0_3_07_speed_bounds(self):
        """AC-0.3.7: Speed values of 0, a negative number, 0.1, and 5.0 are each
        rejected with INVALID_SPEED and exit code 6 before any conversion
        starts."""
        for value in ("0", "-1", "0.1", "5.0"):
            with self.subTest(speed=value):
                res = self.create(self.sd, "ac7.gif", "--speed", value, expect=6)
                self.assert_status(res, "validation_failed")
                self.assert_error_code(res, "INVALID_SPEED")
                self.assertEqual((res.result.get("error") or {}).get("stage"), "validate")
                self.assertEqual(self.list_output(), [])
                self.assert_no_media_produced()

    # -- AC-0.3.8 ----------------------------------------------------------
    def test_ac_0_3_08_dither_enumeration(self):
        """AC-0.3.8: Each of none, bayer, floyd_steinberg, sierra2, and
        sierra2_4a produces a valid GIF, and an unrecognized dither value is
        rejected with INVALID_DITHER and exit code 6 with a message listing the
        permitted values."""
        for mode in DITHER_MODES:
            with self.subTest(dither=mode):
                name = f"ac8-{mode}.gif"
                res = self.create(self.sd, name, "--dither", mode)
                self.assertEqual(res.created[0]["transformations"]["dither"], mode)
                self.assert_valid_gif(self.output_path(name))
                self.assert_gif_geometry(self.output_path(name), 640, 360)

        rejected = self.create(self.sd, "ac8-bad.gif", "--dither", "ordered", expect=6)
        self.assert_status(rejected, "validation_failed")
        self.assert_error_code(rejected, "INVALID_DITHER")
        message = (rejected.result.get("error") or {}).get("message", "")
        for mode in DITHER_MODES:
            self.assertIn(mode, message, f"the error message must list {mode}")

    # -- AC-0.3.9 ----------------------------------------------------------
    def test_ac_0_3_09_preview_frame(self):
        """AC-0.3.9: A preview invocation writes exactly one PNG to the output
        directory, produces no GIF, applies the requested crop and resize, and
        returns a result where created is empty, previews contains one entry, and
        summary.created is 0."""
        res = self.run_engine(
            [
                "preview",
                "--input",
                self.hd,
                "--at",
                "00:00:01.000",
                "--crop",
                "320:180:1280:720",
                "--width",
                "640",
            ]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        # Exactly one PNG, no GIF.
        self.assertEqual(len(self.list_output()), 1)
        self.assertEqual([n for n in self.list_output() if n.endswith(".gif")], [])
        png_name = self.list_output()[0]
        self.assertTrue(png_name.endswith(".png"))
        # created empty, previews has one entry, summary.created is 0.
        self.assertEqual(res.created, [])
        self.assertEqual(res.summary["created"], 0)
        self.assertEqual(res.summary["previews"], 1)
        previews = self.previews_of(res)
        self.assertEqual(len(previews), 1)
        entry = previews[0]
        # The crop and resize were applied, verified from the PNG itself.
        self.assertEqual((entry["width"], entry["height"]), (640, 360))
        self.assertEqual(entry["atMs"], 1000)
        self.assertEqual(
            entry["transformations"]["crop"], {"x": 320, "y": 180, "width": 1280, "height": 720}
        )
        self.assert_truecolour_png(self.output_path(png_name), 640, 360)

    # -- AC-0.3.10 ---------------------------------------------------------
    def test_ac_0_3_10_per_clip_transformations(self):
        """AC-0.3.10: A manifest whose clips specify different crop, width,
        speed, and dither values produces one GIF per clip whose reported
        dimensions, output duration, and dither match that clip's settings, and a
        clip-level value overrides both the top-level manifest value and the
        equivalent command-line flag."""
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": self.sd,
                "profile": "small",
                "width": 120,  # top-level: must lose to both the flag and the clips
                "dither": "floyd_steinberg",
                "clips": [
                    {
                        "name": "cropped",
                        "start": "0",
                        "end": "2",
                        "crop": "0:0:400:200",
                        "width": 200,
                        "dither": "none",
                    },
                    {
                        "name": "quick",
                        "start": "0",
                        "end": "2",
                        "speed": 2.0,
                        "width": 320,
                        "dither": "sierra2",
                    },
                    {"name": "inherited", "start": "0", "end": "2"},
                ],
            }
        )
        res = self.run_engine(
            ["batch", "--manifest", manifest, "--width", "240", "--dither", "bayer"]
        )
        self.assert_exit(res, 0)
        self.assertEqual(res.summary["created"], 3)
        by_name = {c["name"]: c for c in res.created}

        cropped = by_name["cropped"]
        self.assertEqual((cropped["width"], cropped["height"]), (200, 100))
        self.assertEqual(cropped["transformations"]["dither"], "none")
        self.assertEqual(cropped["outputDurationMs"], 2000)
        self.assert_gif_geometry(self.output_path("cropped.gif"), 200, 100)

        quick = by_name["quick"]
        self.assertEqual(quick["width"], 320)
        self.assertEqual(quick["transformations"]["dither"], "sierra2")
        self.assertEqual(quick["durationMs"], 2000)
        self.assertEqual(quick["outputDurationMs"], 1000)
        self.assert_gif_duration_ms(self.output_path("quick.gif"), 1000, fps=10.0)

        # The clip that specifies nothing takes the command-line flags, which in
        # turn beat the top-level manifest values (120 / floyd_steinberg).
        inherited = by_name["inherited"]
        self.assertEqual(inherited["width"], 240)
        self.assertEqual(inherited["transformations"]["dither"], "bayer")
        self.assert_gif_geometry(self.output_path("inherited.gif"), 240, inherited["height"])

    # -- AC-0.3.11 ---------------------------------------------------------
    def test_ac_0_3_11_no_filter_injection(self):
        """AC-0.3.11: Transformation values containing filter-graph
        metacharacters are rejected during preflight, no FFmpeg process is
        started, and the filter graph cannot be altered."""
        cases = (
            ("--crop", "0:0:100:100,drawtext=text=x", "INVALID_CROP"),
            ("--dither", "none[a];[a]movie=/etc/passwd", "INVALID_DITHER"),
            ("--width", "640;drawtext=text=x", "INVALID_DIMENSIONS"),
            ("--speed", "2.0,setpts=PTS/8", "INVALID_SPEED"),
        )
        for flag, value, code in cases:
            with self.subTest(flag=flag):
                res = self.create(self.sd, "ac11.gif", flag, value, expect=6)
                self.assert_status(res, "validation_failed")
                self.assert_error_code(res, code)
                self.assertEqual((res.result.get("error") or {}).get("stage"), "validate")
                self.assertEqual(self.list_output(), [])
                self.assert_no_media_produced()
                self.assert_no_traceback(res)
        # The same values are rejected when they arrive through a manifest.
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": self.sd,
                "profile": "small",
                "clips": [
                    {
                        "name": "evil",
                        "start": "0",
                        "end": "1",
                        "crop": "0:0:10:10;movie=/etc/passwd",
                    }
                ],
            }
        )
        res = self.run_engine(["batch", "--manifest", manifest])
        self.assert_exit(res, 6)
        self.assert_error_code(res, "INVALID_CROP")
        self.assertEqual(self.list_output(), [])

    # -- AC-0.3.12 ---------------------------------------------------------
    def test_ac_0_3_12_transformation_reporting(self):
        """AC-0.3.12: Every created entry reports the applied crop rectangle,
        effective source and output dimensions, speed, dither mode, bayerScale,
        upscaled, and outputDurationMs, and the agent's summary reflects them
        without re-deriving them."""
        res = self.create(
            self.hd,
            "ac12.gif",
            "--crop",
            "10:20:900:300",
            "--width",
            "450",
            "--speed",
            "2.0",
            "--dither",
            "bayer",
            "--bayer-scale",
            "4",
            start="0",
            end="2",
            profile="balanced",
        )
        clip = res.created[0]
        self.assertEqual(
            clip["transformations"],
            {
                "crop": {"x": 10, "y": 20, "width": 900, "height": 300},
                "sourceWidth": 1920,
                "sourceHeight": 1080,
                "effectiveSourceWidth": 900,
                "effectiveSourceHeight": 300,
                "speed": 2.0,
                "dither": "bayer",
                "bayerScale": 4,
                "upscaled": False,
            },
        )
        self.assertEqual(clip["durationMs"], 2000)
        self.assertEqual(clip["outputDurationMs"], 1000)
        self.assertEqual((clip["width"], clip["height"]), (450, 150))
        # Every reported value matches the file on disk, so an agent can quote
        # the report without re-deriving anything.
        self.assert_gif_geometry(self.output_path("ac12.gif"), 450, 150)
        self.assert_gif_duration_ms(self.output_path("ac12.gif"), 1000, fps=15.0)
        self.assertEqual(os.path.getsize(self.output_path("ac12.gif")), clip["sizeBytes"])
        self.assertEqual(res.summary["previews"], 0)
        self.assertEqual(res.result["previews"], [])

    # -- AC-0.3.13 ---------------------------------------------------------
    def test_ac_0_3_13_backward_compatibility(self):
        """AC-0.3.13: All version 0.1.0 and version 0.2.0 command-line
        invocations, configuration files, and manifests behave unchanged, and a
        job with no transformation settings produces output functionally
        equivalent to version 0.2.0. Configuration, manifest, and structured
        result schemaVersion remain 1."""
        # 1. A profile-only invocation reproduces the v0.2.0 dimensions exactly,
        #    including the ODD derived heights the legacy round() path yields.
        legacy = (("small", 480, 241), ("balanced", 640, 321), ("high", 960, 482))
        for profile, width, height in legacy:
            with self.subTest(profile=profile):
                name = f"ac13-{profile}.gif"
                res = self.create(self.odd, name, profile=profile)
                clip = res.created[0]
                self.assertEqual(
                    (clip["width"], clip["height"]),
                    (width, height),
                    f"{profile}: profile-only geometry changed from v0.2.0",
                )
                self.assert_gif_geometry(self.output_path(name), width, height)
                # No transformation was applied and nothing was warned about.
                self.assertIsNone(clip["transformations"]["crop"])
                self.assertEqual(clip["transformations"]["speed"], 1.0)
                self.assertFalse(clip["transformations"]["upscaled"])
                self.assertEqual(clip["outputDurationMs"], clip["durationMs"])
                self.assertEqual(self.warnings_of(res), [])
                self.assertEqual(res.result["schemaVersion"], 1)

        # 2. A v0.2.0-era manifest with no transformation fields still validates
        #    and runs, and its schemaVersion stays 1.
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": self.sd,
                "profile": "small",
                "loop": "forever",
                "continueOnError": True,
                "clips": [
                    {"name": "legacy-a", "start": "00:00:00", "end": "00:00:01"},
                    {"name": "legacy-b", "start": "0", "duration": 1},
                ],
            }
        )
        validated = self.run_engine(["validate-manifest", "--manifest", manifest])
        self.assert_exit(validated, 0)
        self.assertEqual(validated.result["schemaVersion"], 1)
        batch = self.run_engine(["batch", "--manifest", manifest])
        self.assert_exit(batch, 0)
        self.assertEqual(batch.summary["created"], 2)
        self.assertEqual(batch.result["schemaVersion"], 1)
        for clip in batch.created:
            # Additive fields are present but describe an untransformed job.
            self.assertEqual(clip["outputDurationMs"], clip["durationMs"])
            self.assertIsNone(clip["transformations"]["crop"])

        # 3. A v0.2.0-era configuration file, with no transformations object,
        #    still validates and keeps schemaVersion 1.
        config_path = os.path.join(self.project, ".video-to-gif.json")
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schemaVersion": 1,
                    "defaultProfile": "balanced",
                    "outputDirectory": "./output",
                    "loop": "forever",
                    "collisionPolicy": "fail",
                    "continueOnError": True,
                },
                fh,
            )
        config = self.run_engine(["validate-config", "--config", config_path])
        self.assert_exit(config, 0)
        self.assertEqual(config.result["schemaVersion"], 1)

        # 4. The v0.1.0 --width flag keeps its "maximum output width" meaning and
        #    its no-upscale behavior on an undersized source.
        legacy_width = self.create(self.sd, "ac13-width.gif", "--width", "320", profile="balanced")
        self.assertEqual(
            (legacy_width.created[0]["width"], legacy_width.created[0]["height"]), (320, 180)
        )
        self.assert_gif_geometry(self.output_path("ac13-width.gif"), 320, 180)


if __name__ == "__main__":
    unittest.main()
