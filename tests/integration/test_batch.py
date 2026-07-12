"""Integration: batch conversion via JSON and CSV manifests, and --dry-run
preflight (spec 12.4, 12.5, FR-009, AC-002, AC-004, AC-005). Real ffmpeg.
"""

import json
import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import EngineTestCase, media


class TestBatch(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("batch.mp4")
        media.generate_landscape(cls.src, size="320x240", fps=15, duration=5.0)

    def _write_json_manifest(self, clips, **top):
        data = {"schemaVersion": 1, "input": self.src, "profile": "small", "clips": clips}
        data.update(top)
        path = os.path.join(self.project, "clips.json")
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def _write_csv_manifest(self, rows_text):
        path = os.path.join(self.project, "clips.csv")
        with open(path, "w") as fh:
            fh.write(rows_text)
        return path

    # -- JSON manifest -----------------------------------------------------
    def test_json_manifest_creates_all(self):
        m = self._write_json_manifest(
            [
                {"name": "a", "start": "0", "end": "1"},
                {"name": "b", "start": "1", "end": "2"},
                {"name": "c", "start": "2", "duration": 1},
            ]
        )
        res = self.run_engine(["batch", "--manifest", m])
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 3)
        self.assertEqual(set(self.list_output()), {"a.gif", "b.gif", "c.gif"})
        for name in ("a.gif", "b.gif", "c.gif"):
            self.assert_valid_gif(self.output_path(name))

    def test_ten_independently_named_gifs(self):
        # AC-002: ten valid ranges -> ten independently named GIFs.
        clips = [{"name": f"clip{i}", "start": str(i * 0.4), "duration": 0.3} for i in range(10)]
        m = self._write_json_manifest(clips)
        res = self.run_engine(["batch", "--manifest", m])
        self.assert_exit(res, 0)
        self.assertEqual(res.summary["created"], 10)
        self.assertEqual(len(set(self.list_output())), 10)

    # -- CSV manifest ------------------------------------------------------
    def test_csv_manifest_creates_all(self):
        # AC-005: CSV manifest (start/end and start/duration rows, mixed).
        m = self._write_csv_manifest(
            "name,start,end,duration,profile\n"
            "intro,0,1,,small\n"
            "middle,1,,1,small\n"
            "\n"  # empty row must be ignored
            "outro,3,4,,small\n"
        )
        res = self.run_engine(["batch", "--manifest", m, "--input", self.src])
        self.assert_exit(res, 0)
        self.assertEqual(res.summary["created"], 3)
        self.assertEqual(set(self.list_output()), {"intro.gif", "middle.gif", "outro.gif"})

    # -- dry run -----------------------------------------------------------
    def test_dry_run_resolves_names_without_encoding(self):
        m = self._write_json_manifest(
            [
                {"name": "one", "start": "0", "end": "1"},
                {"name": "two", "start": "1", "end": "2"},
            ]
        )
        res = self.run_engine(["batch", "--manifest", m, "--dry-run"])
        self.assert_exit(res, 0)
        self.assert_status(res, "dry_run")
        names = [os.path.basename(p["path"]) for p in res.result["plan"]]
        self.assertEqual(names, ["one.gif", "two.gif"])
        self.assertEqual(res.summary["planned"], 2)
        self.assertEqual(res.summary["collisions"], 0)
        # No GIF written; output dir not created by preflight.
        self.assertEqual(self.list_output(), [])
        for p in res.result["plan"]:
            self.assertIn("estimatedFrames", p)
            self.assertEqual(p["action"], "write")

    def test_dry_run_reports_collisions(self):
        m = self._write_json_manifest([{"name": "dup", "start": "0", "end": "1"}])
        # First real run creates dup.gif.
        self.assert_exit(self.run_engine(["batch", "--manifest", m]), 0)
        before = os.path.getsize(self.output_path("dup.gif"))
        # Dry-run now must detect the collision and still write nothing.
        res = self.run_engine(["batch", "--manifest", m, "--dry-run"])
        self.assert_exit(res, 0)
        self.assert_status(res, "dry_run")
        self.assertEqual(res.summary["collisions"], 1)
        self.assertEqual([p["action"] for p in res.result["plan"]], ["collision"])
        self.assertEqual(os.path.getsize(self.output_path("dup.gif")), before)


if __name__ == "__main__":
    unittest.main()
