"""Security: filesystem-boundary and command-injection safety (spec 22.4, SEC-001,
SEC-002, SEC-003, SEC-004, SEC-008, AC-007, AC-014). Real ffmpeg.

Every test asserts on the structured contract and on filesystem side effects:
no file escapes the output directory, no shell command executes, existing files
are byte-preserved, external writes require authorization, and temporary files
are removed after a failed conversion.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import FFMPEG, EngineTestCase, media

from vtg import errors as vtg_errors

# Engine package is importable (fixtures.base put scripts/ on sys.path).
from vtg import ffmpeg as vtg_ffmpeg
from vtg.models import EffectiveSettings, SourceInfo


class TestTraversalAndInjection(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("safe.mp4")
        media.generate_landscape(cls.src, size="320x240", fps=15, duration=2.0)

    def _manifest(self, clips):
        data = {"schemaVersion": 1, "input": self.src, "profile": "small", "clips": clips}
        path = os.path.join(self.project, "clips.json")
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_manifest_name_traversal_cannot_escape_output_dir(self):
        # SEC-002: a manifest name with path separators must not escape ./output.
        sentinel_parent = os.path.join(self.project, "escaped.gif")
        m = self._manifest([{"name": "../../escaped.gif", "start": "0", "end": "1"}])
        res = self.run_engine(["batch", "--manifest", m])
        self.assertNotEqual(res.returncode, 0)
        self.assert_error_code(res, "INVALID_USAGE")
        # Nothing was written outside the output directory.
        self.assertFalse(os.path.exists(sentinel_parent))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.project), "escaped.gif")))

    def test_output_name_with_separator_rejected(self):
        # FR-011 / SEC-002: --output-name must be a bare filename.
        res = self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "0",
                "--end",
                "1",
                "--output-name",
                "../sneaky.gif",
            ]
        )
        self.assertNotEqual(res.returncode, 0)
        self.assert_error_code(res, "INVALID_USAGE")
        self.assertFalse(os.path.exists(os.path.join(self.project, "sneaky.gif")))

    def test_shell_metacharacters_in_output_name_are_literal(self):
        # SEC-001/AC-014: $(...) must not execute; the filename is literal.
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
                "$(touch pwned).gif",
            ]
        )
        self.assert_exit(res, 0)
        # No command executed: the sentinel file never appears anywhere.
        for d in (self.project, self.output_dir, os.getcwd()):
            self.assertFalse(os.path.exists(os.path.join(d, "pwned")))
        # The literal filename was created inside the output directory.
        self.assertIn("$(touch pwned).gif", self.list_output())

    def test_shell_operators_in_manifest_name_are_literal(self):
        # SEC-009/AC-014: manifest values are data; ';', '&&', backticks inert.
        m = self._manifest([{"name": "a; echo hi && `id` z", "start": "0", "end": "1"}])
        res = self.run_engine(["batch", "--manifest", m])
        self.assert_exit(res, 0)
        self.assertEqual(res.summary["created"], 1)
        # No side-effect files from the metacharacters.
        for junk in ("hi", "z", "id"):
            self.assertFalse(os.path.exists(os.path.join(self.project, junk)))
        # One literal GIF exists in output.
        self.assertEqual(len([n for n in self.list_output() if n.endswith(".gif")]), 1)


class TestBoundaryAndCollision(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("bnd.mp4")
        media.generate_landscape(cls.src, size="320x240", fps=15, duration=2.0)

    def test_external_write_rejected_without_flag(self):
        # SEC-003: writing outside the project root needs authorization.
        external = tempfile.mkdtemp(prefix="giffy-ext-")
        self.addCleanup(shutil.rmtree, external, ignore_errors=True)
        res = self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "0",
                "--end",
                "1",
                "--output-directory",
                external,
            ]
        )
        self.assert_exit(res, 8)  # EXIT_PERMISSION
        self.assert_error_code(res, "PROJECT_BOUNDARY_VIOLATION")
        self.assertEqual(os.listdir(external), [])

    def test_external_write_allowed_with_flag(self):
        external = tempfile.mkdtemp(prefix="giffy-ext-")
        self.addCleanup(shutil.rmtree, external, ignore_errors=True)
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
                "--output-directory",
                external,
                "--allow-outside-project",
            ]
        )
        self.assert_exit(res, 0)
        gifs = [n for n in os.listdir(external) if n.endswith(".gif")]
        self.assertEqual(len(gifs), 1)

    def test_existing_file_preserved_under_default_collision(self):
        # AC-007 / SEC-004: default policy is fail; existing bytes must not change.
        os.makedirs(self.output_dir, exist_ok=True)
        name = "keep.gif"
        original = b"ORIGINAL-BYTES-DO-NOT-OVERWRITE"
        with open(self.output_path(name), "wb") as fh:
            fh.write(original)
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
                name,
            ]
        )
        self.assert_exit(res, 7)  # EXIT_COLLISION
        self.assert_status(res, "collision")
        with open(self.output_path(name), "rb") as fh:
            self.assertEqual(fh.read(), original)  # byte-identical


class TestTempCleanupOnFailure(EngineTestCase):
    """SEC-008 / 22.4: temporary files removed after a failed conversion.

    Exercised directly against the real ffmpeg conversion pipeline: a bogus
    (non-video) source makes ffmpeg exit non-zero during palette generation, and
    the pipeline must clean up its palette temp dir and the in-place temp GIF.
    """

    @classmethod
    def generate_media(cls):
        pass

    def _settings(self):
        return EffectiveSettings(
            width=320, height=240, fps=10.0, colors=128, loop="forever", profile_name="small"
        )

    def _source(self, path):
        return SourceInfo(
            path=path,
            duration_ms=2000,
            width=320,
            height=240,
            display_width=320,
            display_height=240,
            fps=15.0,
            codec="h264",
            stream_index=0,
        )

    def test_temp_files_removed_after_ffmpeg_failure(self):
        out_dir = os.path.join(self.project, "out")
        os.makedirs(out_dir, exist_ok=True)
        bogus = os.path.join(self.project, "bogus.mp4")
        with open(bogus, "w") as fh:
            fh.write("this is not a video")
        before = self._engine_temp_dirs()
        assert FFMPEG is not None  # class is skipped unless ffmpeg is present
        with self.assertRaises(vtg_errors.EngineError) as cm:
            vtg_ffmpeg.convert_clip(
                FFMPEG,
                self._source(bogus),
                start_ms=0,
                duration_ms=1000,
                settings=self._settings(),
                dest_path=os.path.join(out_dir, "out.gif"),
                output_dir=out_dir,
                timeout_seconds=30,
                max_temp_bytes=2**31,
            )
        self.assertEqual(cm.exception.code, "FFMPEG_FAILED")
        self.assertEqual(cm.exception.exit_code, 9)
        # No temp GIF left in the output dir, no palette temp dir leaked.
        self.assertEqual([p for p in os.listdir(out_dir) if p.startswith(".vtg-")], [])
        self.assertEqual(self._engine_temp_dirs() - before, set())


if __name__ == "__main__":
    unittest.main()
