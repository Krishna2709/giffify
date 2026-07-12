"""Integration: single conversion, start/end vs start/duration equivalence,
default naming, explicit output naming, and loop-mode encoding (spec 12.3,
FR-011, FR-015, AC-003). Real ffmpeg; synthetic media generated at runtime.
"""

import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import EngineTestCase, media


class TestSingleCreate(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("src.mp4")
        media.generate_landscape(cls.src, size="640x360", fps=30, duration=3.0)

    def test_single_gif_created(self):
        res = self.run_engine(
            ["create", "--input", self.src, "--start", "0", "--end", "2", "--profile", "small"]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 1)
        clip = res.created[0]
        self.assertEqual((clip["startMs"], clip["endMs"], clip["durationMs"]), (0, 2000, 2000))
        self.assert_valid_gif(self.output_path(os.path.basename(clip["path"])))

    def test_default_naming_pattern(self):
        # FR-011: <video-stem>_<start>_to_<end>.gif with HH-MM-SS.mmm stamps.
        res = self.run_engine(
            ["create", "--input", self.src, "--start", "0", "--end", "2", "--profile", "small"]
        )
        self.assert_exit(res, 0)
        self.assertIn("src_00-00-00.000_to_00-00-02.000.gif", self.list_output())

    def test_explicit_output_name(self):
        res = self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                "--output-name",
                "opening.gif",
            ]
        )
        self.assert_exit(res, 0)
        self.assertTrue(res.created[0]["path"].endswith("opening.gif"))
        self.assertIn("opening.gif", self.list_output())

    def test_start_duration_equivalent_to_start_end(self):
        # AC-003: start=00:00:01 + duration=2 == start=00:00:01 + end=00:00:03.
        end_res = self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "00:00:01",
                "--end",
                "00:00:03",
                "--profile",
                "small",
                "--output-name",
                "by_end.gif",
            ]
        )
        dur_res = self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "00:00:01",
                "--duration",
                "2",
                "--profile",
                "small",
                "--output-name",
                "by_dur.gif",
            ]
        )
        self.assert_exit(end_res, 0)
        self.assert_exit(dur_res, 0)
        a, b = end_res.created[0], dur_res.created[0]
        for key in ("startMs", "endMs", "durationMs", "width", "height", "fps"):
            self.assertEqual(a[key], b[key], f"mismatch on {key}")
        self.assertEqual((a["startMs"], a["endMs"], a["durationMs"]), (1000, 3000, 2000))


class TestLoopModes(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("loop.mp4")
        media.generate_landscape(cls.src, size="320x240", fps=15, duration=2.0)

    def _make(self, loop, name):
        res = self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                "--loop",
                loop,
                "--output-name",
                name,
            ]
        )
        self.assert_exit(res, 0)
        return self.parse_gif_header(self.output_path(name))

    def test_forever_writes_netscape_loop_zero(self):
        # FR-015: default/forever loops infinitely -> NETSCAPE2.0 ext, count 0.
        info = self._make("forever", "forever.gif")
        self.assertTrue(info["netscape"])
        self.assertEqual(info["loop_count"], 0)

    def test_once_omits_netscape_extension(self):
        # once (N=1) -> ffmpeg -loop -1 -> no NETSCAPE loop extension.
        info = self._make("once", "once.gif")
        self.assertFalse(info["netscape"])

    def test_count_writes_netscape_loop_n_minus_1(self):
        # N total plays -> NETSCAPE loop count N-1 (GIF loop-extension semantics).
        info = self._make("3", "thrice.gif")
        self.assertTrue(info["netscape"])
        self.assertEqual(info["loop_count"], 2)


if __name__ == "__main__":
    unittest.main()
