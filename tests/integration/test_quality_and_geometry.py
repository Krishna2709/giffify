"""Integration: quality-profile dimensions and frame rate, no-upscale behavior,
portrait aspect preservation, and rotation-metadata handling (spec FR-014,
AC-009). Output GIFs are re-probed with ffprobe.

Note on color limits (spec FR-014 / AC-009): the small profile caps palettegen
at 128 colors and balanced/high at 256, but the GIF muxer pads the global color
table to 256 entries regardless, so the exact color cap is NOT observable from
the GIF bytes with the standard library alone. These tests therefore assert the
deterministic, observable properties -- output width/height and effective frame
rate (via exact frame count) -- and only sanity-bound the color table (<= 256).
Frame rate is verified by frame count because GIF centisecond delay quantization
makes avg_frame_rate report 50/3 for a 15 fps clip.
"""

import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import EngineTestCase, media


class TestQualityProfiles(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        # 1280x720 @ 30 fps so every profile downscales (no upscale) and its
        # target fps (10/15/20) stays under the 30 fps source cap.
        cls.src = cls.media_file("hd.mp4")
        media.generate_landscape(cls.src, size="1280x720", fps=30, duration=2.0)

    # Expected effective (width, height, target_fps) for a 1280x720 source.
    CASES: ClassVar[dict[str, tuple[int, int, int]]] = {
        "small": (480, 270, 10),
        "balanced": (640, 360, 15),
        "high": (960, 540, 20),
    }

    def _run_profile(self, profile):
        name = f"{profile}.gif"
        res = self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "0",
                "--end",
                "2",
                "--profile",
                profile,
                "--output-name",
                name,
            ]
        )
        self.assert_exit(res, 0)
        return name, res

    def test_small_profile_dimensions_and_fps(self):
        self._assert_profile("small")

    def test_balanced_profile_dimensions_and_fps(self):
        self._assert_profile("balanced")

    def test_high_profile_dimensions_and_fps(self):
        self._assert_profile("high")

    def _assert_profile(self, profile):
        w, h, fps = self.CASES[profile]
        name, res = self._run_profile(profile)
        clip = res.created[0]
        # Result contract reports the effective dimensions.
        self.assertEqual((clip["width"], clip["height"]), (w, h))
        self.assertEqual(clip["fps"], fps)
        # Re-probe the actual GIF bytes.
        info = self.probe_gif(self.output_path(name))
        self.assertEqual((info["width"], info["height"]), (w, h))
        # Frame count == duration * target fps (2 s clip), tolerant by +/-1.
        self.assertIsNotNone(info["nb_frames"])
        self.assertLessEqual(
            abs(info["nb_frames"] - 2 * fps),
            1,
            f"{profile}: {info['nb_frames']} frames, expected ~{2 * fps}",
        )
        # Color table sanity bound (see module docstring).
        self.assertLessEqual(self.parse_gif_header(self.output_path(name))["gct_colors"], 256)


class TestGeometry(EngineTestCase):
    narrow: ClassVar[str]
    portrait: ClassVar[str]
    rotated: ClassVar[str]

    @classmethod
    def generate_media(cls):
        # Narrow landscape source (320 wide) -- narrower than every profile width.
        cls.narrow = cls.media_file("narrow.mp4")
        media.generate_landscape(cls.narrow, size="320x240", fps=15, duration=2.0)
        # Portrait source (taller than wide).
        cls.portrait = cls.media_file("portrait.mp4")
        media.generate_portrait(cls.portrait, size="240x426", fps=15, duration=2.0)
        # Landscape pixels + 90-degree display matrix -> portrait display.
        cls.rotated = cls.media_file("rotated.mp4")
        media.generate_rotated(cls.rotated, rotation=90, size="640x360", fps=15, duration=2.0)

    def test_no_upscale_keeps_source_width(self):
        # FR-014: source narrower than the profile width stays at source width.
        res = self.run_engine(
            [
                "create",
                "--input",
                self.narrow,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "high",
                "--output-name",
                "narrow.gif",
            ]
        )  # high wants 960
        self.assert_exit(res, 0)
        clip = res.created[0]
        self.assertEqual((clip["width"], clip["height"]), (320, 240))
        info = self.probe_gif(self.output_path("narrow.gif"))
        self.assertEqual((info["width"], info["height"]), (320, 240))

    def test_portrait_aspect_preserved(self):
        res = self.run_engine(
            [
                "create",
                "--input",
                self.portrait,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "balanced",
                "--output-name",
                "portrait.gif",
            ]
        )
        self.assert_exit(res, 0)
        clip = res.created[0]
        self.assertGreater(clip["height"], clip["width"])  # still portrait
        self.assertEqual((clip["width"], clip["height"]), (240, 426))
        info = self.probe_gif(self.output_path("portrait.gif"))
        self.assertEqual((info["width"], info["height"]), (240, 426))

    def test_rotation_metadata_yields_portrait_gif(self):
        # inspect swaps display dims for a 90-degree rotation; ffmpeg autorotates
        # during decode, so a 640x360 rotated source produces a 360x640 GIF.
        inspect = self.run_engine(["inspect", "--input", self.rotated])
        self.assert_exit(inspect, 0)
        self.assertEqual(inspect.result["source"]["rotation"], 90)
        self.assertEqual(
            (inspect.result["source"]["width"], inspect.result["source"]["height"]), (360, 640)
        )
        res = self.run_engine(
            [
                "create",
                "--input",
                self.rotated,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "balanced",
                "--output-name",
                "rot.gif",
            ]
        )
        self.assert_exit(res, 0)
        clip = res.created[0]
        self.assertGreater(clip["height"], clip["width"])  # portrait output
        info = self.probe_gif(self.output_path("rot.gif"))
        self.assertGreater(info["height"], info["width"])
        self.assertEqual((info["width"], info["height"]), (360, 640))


if __name__ == "__main__":
    unittest.main()
