"""Integration: inspect output fields, multi-stream selection, doctor health,
and config/manifest validation on good and bad inputs (spec 12.1, 12.2, 12.6,
12.7, FR-002, FR-003). Real ffprobe.
"""

import json
import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import EngineTestCase, media


class TestInspect(EngineTestCase):
    src: ClassVar[str]
    multi: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("probe.mp4")
        media.generate_landscape(cls.src, size="640x360", fps=24, duration=2.0)
        cls.multi = cls.media_file("multi.mp4")
        media.generate_multistream(cls.multi, size="320x240", fps=15, duration=1.0)

    def test_inspect_reports_required_fields(self):
        res = self.run_engine(["inspect", "--input", self.src])
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        src = res.result["source"]
        self.assertEqual(src["width"], 640)
        self.assertEqual(src["height"], 360)
        self.assertEqual(src["durationMs"], 2000)
        self.assertEqual(src["codec"], "h264")
        self.assertEqual(src["videoStreamIndex"], 0)
        self.assertAlmostEqual(src["fps"], 24.0, places=2)
        self.assertEqual(src["rotation"], 0)

    def test_multistream_selects_default_video_stream(self):
        # FR-003: prefer the default-flagged stream (index 0) with a warning.
        res = self.run_engine(["inspect", "--input", self.multi])
        self.assert_exit(res, 0)
        self.assertEqual(res.result["source"]["videoStreamIndex"], 0)
        self.assertTrue(any("Multiple video streams" in w for w in res.result["warnings"]))


class TestDoctor(EngineTestCase):
    @classmethod
    def generate_media(cls):
        pass  # doctor needs no media

    def test_doctor_healthy(self):
        res = self.run_engine(["doctor"])
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertTrue(res.result["healthy"])
        names = {c["name"] for c in res.result["checks"]}
        for required in (
            "ffmpeg",
            "ffprobe",
            "palettegen_filter",
            "paletteuse_filter",
            "gif_encoder",
            "temp_writable",
        ):
            self.assertIn(required, names)
        self.assertTrue(all(c["ok"] for c in res.result["checks"]))


class TestValidateConfig(EngineTestCase):
    @classmethod
    def generate_media(cls):
        pass

    def _write(self, data):
        path = os.path.join(self.project, ".video-to-gif.json")
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_valid_config(self):
        p = self._write({"schemaVersion": 1, "defaultProfile": "high", "loop": "forever"})
        res = self.run_engine(["validate-config", "--config", p])
        self.assert_exit(res, 0)
        self.assertTrue(res.result["valid"])
        self.assertEqual(res.result["resolved"]["defaultProfile"], "high")

    def test_invalid_profile_reports_field_path(self):
        p = self._write({"schemaVersion": 1, "defaultProfile": "ultra"})
        res = self.run_engine(["validate-config", "--config", p])
        self.assert_exit(res, 2)  # EXIT_INVALID_USAGE
        self.assert_status(res, "validation_failed")
        self.assertEqual(res.result["error"]["field"], "defaultProfile")

    def test_forbidden_field_rejected(self):
        # Section 9.4: executable/credential fields must be rejected.
        p = self._write({"schemaVersion": 1, "command": "rm -rf /"})
        res = self.run_engine(["validate-config", "--config", p])
        self.assert_exit(res, 2)
        self.assert_error_code(res, "INVALID_CONFIG")


class TestValidateManifest(EngineTestCase):
    @classmethod
    def generate_media(cls):
        pass

    def test_valid_json_manifest(self):
        p = os.path.join(self.project, "m.json")
        with open(p, "w") as fh:
            json.dump(
                {"schemaVersion": 1, "input": "x.mp4", "clips": [{"start": "0", "end": "1"}]}, fh
            )
        res = self.run_engine(["validate-manifest", "--manifest", p])
        self.assert_exit(res, 0)
        self.assertTrue(res.result["valid"])
        self.assertEqual(res.result["clipCount"], 1)

    def test_valid_csv_manifest(self):
        p = os.path.join(self.project, "m.csv")
        with open(p, "w") as fh:
            fh.write("start,end\n0,1\n2,3\n")
        res = self.run_engine(["validate-manifest", "--manifest", p])
        self.assert_exit(res, 0)
        self.assertEqual(res.result["clipCount"], 2)

    def test_bad_manifest_missing_required(self):
        p = os.path.join(self.project, "bad.json")
        with open(p, "w") as fh:
            json.dump({"schemaVersion": 1, "clips": []}, fh)  # no input, empty clips
        res = self.run_engine(["validate-manifest", "--manifest", p])
        self.assert_exit(res, 2)
        self.assert_status(res, "validation_failed")
        self.assert_error_code(res, "INVALID_MANIFEST")

    def test_bad_clip_timestamp_reports_error(self):
        p = os.path.join(self.project, "bad2.csv")
        with open(p, "w") as fh:
            fh.write("start,end\nnot-a-time,1\n")
        res = self.run_engine(["validate-manifest", "--manifest", p])
        self.assert_exit(res, 6)  # EXIT_INVALID_TIMESTAMP (spec section 14)
        self.assert_status(res, "validation_failed")
        self.assert_error_code(res, "INVALID_TIMESTAMP")


if __name__ == "__main__":
    unittest.main()
