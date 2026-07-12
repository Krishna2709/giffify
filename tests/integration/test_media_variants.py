"""Integration: media variants from the spec 22.2 catalogue -- video with audio
(GIF must be silent), corrupted media (structured exit 5), and input/output
paths containing spaces and Unicode (AC-011). Real ffmpeg.
"""

import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import EngineTestCase, media


class TestAudioSource(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("withaudio.mp4")
        media.generate_with_audio(cls.src, size="320x240", fps=15, duration=2.0)

    def test_source_has_audio_but_gif_is_silent(self):
        # Sanity: the source really carries an audio stream.
        probe = self.probe_gif(self.src)
        self.assertTrue(probe["has_audio"])
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
                "silent.gif",
            ]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        out = self.probe_gif(self.output_path("silent.gif"))
        # 15.4: no audio stream in the output GIF.
        self.assertFalse(out["has_audio"])
        self.assertEqual(len([s for s in out["streams"] if s.get("codec_type") == "video"]), 1)


class TestCorruptedMedia(EngineTestCase):
    corrupt: ClassVar[str]
    zero: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.corrupt = cls.media_file("corrupt.mp4")
        media.generate_corrupted(cls.corrupt, keep_bytes=2000)
        cls.zero = cls.media_file("zero.mp4")
        media.generate_zero_byte(cls.zero)

    def test_corrupted_file_inspect_exit_5(self):
        res = self.run_engine(["inspect", "--input", self.corrupt])
        self.assert_exit(res, 5)  # EXIT_INVALID_MEDIA
        self.assert_status(res, "failed")
        self.assert_error_code(res, "UNSUPPORTED_MEDIA")

    def test_corrupted_file_create_exit_5_no_output(self):
        res = self.run_engine(["create", "--input", self.corrupt, "--start", "0", "--end", "1"])
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_MEDIA")
        self.assertEqual(self.list_output(), [])

    def test_zero_byte_file_exit_5(self):
        res = self.run_engine(["inspect", "--input", self.zero])
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_MEDIA")


class TestUnicodeAndSpacePaths(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        # Input filename with spaces AND Unicode (AC-011).
        cls.src = cls.media_file("mövie clip ünïcode.mp4")
        media.generate_landscape(cls.src, size="320x240", fps=15, duration=2.0)

    def test_unicode_input_and_output_paths(self):
        out_name = "sünset clip.gif"
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
                out_name,
            ]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertIn(out_name, self.list_output())
        self.assert_valid_gif(self.output_path(out_name))

    def test_unicode_default_naming(self):
        res = self.run_engine(
            ["create", "--input", self.src, "--start", "0", "--end", "1", "--profile", "small"]
        )
        self.assert_exit(res, 0)
        # Default name derives from the (Unicode) video stem and stays a valid GIF.
        self.assertEqual(len(self.list_output()), 1)
        self.assert_valid_gif(self.output_path(self.list_output()[0]))


if __name__ == "__main__":
    unittest.main()
