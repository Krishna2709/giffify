"""End-to-end CLI orchestration tests with mocked ffmpeg/ffprobe.

Hermetic: no real media, no real ffmpeg. Exercises precedence, collision
policies, partial batch failure, invalid-timestamp handling, dry-run, and
remote-source rejection through :func:`vtg.cli.main`.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest

from vtg import (
    cli,
    errors,
)
from vtg.ffmpeg import ConversionResult


class _Recorder:
    def __init__(self):
        self.calls = []

    def convert(self, ffmpeg, source, **kw):
        self.calls.append(kw)
        # Simulate producing the output file.
        with open(kw["dest_path"], "wb") as fh:
            fh.write(b"GIF89a fake")
        s = kw["settings"]
        return ConversionResult(
            path=kw["dest_path"], size_bytes=11, width=s.width, height=s.height, fps=s.fps
        )


class CLIBase(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp()
        # Marker (and valid default config) so this temp dir is the project root.
        with open(os.path.join(self.project, ".video-to-gif.json"), "w") as fh:
            fh.write('{"schemaVersion": 1}')
        self.old_cwd = os.getcwd()
        os.chdir(self.project)
        self.source = vtgtest.make_source(
            path=os.path.join(self.project, "demo.mp4"),
            duration_ms=60000,
            width=1920,
            height=1080,
            fps=30.0,
        )
        # A real (empty) source file so ensure_source_readable passes.
        open(self.source.path, "wb").close()
        self.recorder = _Recorder()
        self._patchers = [
            mock.patch(
                "vtg.dependencies.require_ffmpeg_tools",
                return_value={"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"},
            ),
            mock.patch("vtg.cli.inspect_source", return_value=self.source),
            mock.patch("vtg.ffmpeg.convert_clip", side_effect=self.recorder.convert),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        os.chdir(self.old_cwd)
        shutil.rmtree(self.project, ignore_errors=True)

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        payload = out.getvalue().strip()
        result = json.loads(payload) if payload else {}
        return code, result

    def write_config(self, data):
        with open(os.path.join(self.project, ".video-to-gif.json"), "w") as fh:
            json.dump(data, fh)


class TestCreate(CLIBase):
    def test_success(self):
        code, result = self.run_cli(
            ["create", "--input", "demo.mp4", "--start", "00:00:01", "--duration", "2", "--json"]
        )
        self.assertEqual(code, errors.EXIT_SUCCESS)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary"]["created"], 1)
        self.assertEqual(len(self.recorder.calls), 1)

    def test_config_default_profile_used(self):
        self.write_config({"schemaVersion": 1, "defaultProfile": "small"})
        code, _ = self.run_cli(
            ["create", "--input", "demo.mp4", "--start", "0", "--end", "1", "--json"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.recorder.calls[0]["settings"].profile_name, "small")

    def test_cli_profile_overrides_config(self):
        self.write_config({"schemaVersion": 1, "defaultProfile": "small"})
        self.run_cli(
            [
                "create",
                "--input",
                "demo.mp4",
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "high",
                "--json",
            ]
        )
        self.assertEqual(self.recorder.calls[0]["settings"].profile_name, "high")

    def test_output_name_sanitized(self):
        code, result = self.run_cli(
            [
                "create",
                "--input",
                "demo.mp4",
                "--start",
                "0",
                "--end",
                "1",
                "--output-name",
                "My Clip.gif",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(result["created"][0]["path"].endswith("My Clip.gif"))

    def test_collision_default_fail(self):
        # Pre-create the default output name.
        outdir = os.path.join(self.project, "output")
        os.makedirs(outdir, exist_ok=True)
        name = "demo_00-00-00.000_to_00-00-01.000.gif"
        open(os.path.join(outdir, name), "w").close()
        code, result = self.run_cli(
            ["create", "--input", "demo.mp4", "--start", "0", "--end", "1", "--json"]
        )
        self.assertEqual(code, errors.EXIT_COLLISION)
        self.assertEqual(result["status"], "collision")
        self.assertEqual(len(self.recorder.calls), 0)  # nothing encoded

    def test_collision_overwrite(self):
        outdir = os.path.join(self.project, "output")
        os.makedirs(outdir, exist_ok=True)
        name = "demo_00-00-00.000_to_00-00-01.000.gif"
        open(os.path.join(outdir, name), "w").close()
        code, result = self.run_cli(
            [
                "create",
                "--input",
                "demo.mp4",
                "--start",
                "0",
                "--end",
                "1",
                "--collision-policy",
                "overwrite",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "success")

    def test_invalid_timestamp_fail(self):
        code, result = self.run_cli(
            ["create", "--input", "demo.mp4", "--start", "00:02:00", "--end", "00:02:05", "--json"]
        )
        self.assertEqual(code, errors.EXIT_INVALID_TIMESTAMP)
        self.assertEqual(result["status"], "validation_failed")

    def test_invalid_timestamp_skip(self):
        code, result = self.run_cli(
            [
                "create",
                "--input",
                "demo.mp4",
                "--start",
                "00:02:00",
                "--end",
                "00:02:05",
                "--invalid-timestamp-policy",
                "skip",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertEqual(result["summary"]["created"], 0)

    def test_both_end_and_duration_conflict(self):
        code, _ = self.run_cli(
            [
                "create",
                "--input",
                "demo.mp4",
                "--start",
                "10",
                "--end",
                "20",
                "--duration",
                "5",
                "--json",
            ]
        )
        self.assertEqual(code, errors.EXIT_INVALID_TIMESTAMP)

    def test_remote_source_rejected_when_disabled(self):
        # v0.2.0 FR-018: a URL supplied while remoteSources is disabled (the
        # default) is rejected with REMOTE_DISABLED / exit 8 / status
        # remote_disabled, and nothing is inspected or encoded. (In v0.1.0 this
        # reported UNSUPPORTED_REMOTE_SOURCE / exit 5; the spec supersedes it.)
        code, result = self.run_cli(
            [
                "create",
                "--input",
                "https://example.com/v.mp4",
                "--start",
                "0",
                "--end",
                "1",
                "--json",
            ]
        )
        self.assertEqual(code, errors.EXIT_PERMISSION)
        self.assertEqual(result["status"], "remote_disabled")
        self.assertEqual(result["error"]["code"], errors.REMOTE_DISABLED)
        # No inspection/encoding attempted.
        self.assertEqual(len(self.recorder.calls), 0)


class TestBatch(CLIBase):
    def _write_manifest(self, clips, **top):
        data = {"schemaVersion": 1, "input": "demo.mp4", "clips": clips}
        data.update(top)
        path = os.path.join(self.project, "clips.json")
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_batch_success(self):
        m = self._write_manifest(
            [
                {"name": "a", "start": "0", "end": "1"},
                {"name": "b", "start": "2", "end": "3"},
            ]
        )
        code, result = self.run_cli(["batch", "--manifest", m, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["summary"]["created"], 2)

    def test_dry_run(self):
        m = self._write_manifest([{"name": "a", "start": "0", "end": "1"}])
        code, result = self.run_cli(["batch", "--manifest", m, "--dry-run", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(len(result["plan"]), 1)
        self.assertIn("estimatedFrames", result["plan"][0])
        self.assertEqual(len(self.recorder.calls), 0)

    def test_partial_failure(self):
        # Second clip fails during encode.
        def convert(ffmpeg, source, **kw):
            if kw["clip_index"] == 1:
                raise errors.EngineError(
                    errors.FFMPEG_FAILED,
                    "boom",
                    exit_code=errors.EXIT_FFMPEG_FAILED,
                    stage="encode",
                    clip_index=1,
                )
            with open(kw["dest_path"], "wb") as fh:
                fh.write(b"GIF89a")
            s = kw["settings"]
            return ConversionResult(kw["dest_path"], 6, s.width, s.height, s.fps)

        m = self._write_manifest(
            [
                {"name": "a", "start": "0", "end": "1"},
                {"name": "b", "start": "2", "end": "3"},
            ]
        )
        with mock.patch("vtg.ffmpeg.convert_clip", side_effect=convert):
            code, result = self.run_cli(["batch", "--manifest", m, "--json"])
        self.assertEqual(code, errors.EXIT_PARTIAL)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["summary"]["created"], 1)
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["failed"][0]["clipIndex"], 1)

    def test_continue_on_error_false_stops(self):
        def convert(ffmpeg, source, **kw):
            raise errors.EngineError(
                errors.FFMPEG_FAILED,
                "boom",
                exit_code=errors.EXIT_FFMPEG_FAILED,
                stage="encode",
                clip_index=kw["clip_index"],
            )

        m = self._write_manifest(
            [
                {"name": "a", "start": "0", "end": "1"},
                {"name": "b", "start": "2", "end": "3"},
            ],
            continueOnError=False,
        )
        with mock.patch("vtg.ffmpeg.convert_clip", side_effect=convert):
            _, result = self.run_cli(["batch", "--manifest", m, "--json"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["summary"]["failed"], 1)  # stopped after first

    def test_clip_level_profile_override(self):
        m = self._write_manifest(
            [
                {"name": "a", "start": "0", "end": "1", "profile": "high"},
            ],
            profile="small",
        )
        self.run_cli(["batch", "--manifest", m, "--json"])
        self.assertEqual(self.recorder.calls[0]["settings"].profile_name, "high")

    def test_stop_on_error_with_prior_success_is_partial(self):
        # M4 / SEC-011 precedence: continueOnError=false, an earlier clip already
        # succeeded, a later clip breaches the resource limit. The batch stops but
        # is a *partial* success: exit 11 (NOT 13), the created clip is reported,
        # the RESOURCE_LIMIT_EXCEEDED code is preserved on the failed clip, and the
        # untried clip is reported as skipped so the result stays complete.
        def convert(ffmpeg, source, **kw):
            if kw["clip_index"] == 1:
                raise errors.EngineError(
                    errors.RESOURCE_LIMIT_EXCEEDED,
                    "per-clip timeout exceeded",
                    exit_code=errors.EXIT_RESOURCE_LIMIT,
                    status=errors.STATUS_FAILED,
                    stage="encode",
                    clip_index=1,
                )
            with open(kw["dest_path"], "wb") as fh:
                fh.write(b"GIF89a")
            s = kw["settings"]
            return ConversionResult(kw["dest_path"], 6, s.width, s.height, s.fps)

        m = self._write_manifest(
            [
                {"name": "a", "start": "0", "end": "1"},
                {"name": "b", "start": "2", "end": "3"},
                {"name": "c", "start": "4", "end": "5"},
            ],
            continueOnError=False,
        )
        with mock.patch("vtg.ffmpeg.convert_clip", side_effect=convert):
            code, result = self.run_cli(["batch", "--manifest", m, "--json"])
        self.assertEqual(code, errors.EXIT_PARTIAL)  # 11, not 13
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["summary"]["created"], 1)
        self.assertEqual(result["created"][0]["clipIndex"], 0)
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["failed"][0]["clipIndex"], 1)
        self.assertEqual(result["failed"][0]["code"], "RESOURCE_LIMIT_EXCEEDED")
        # The untried clip is reported as skipped, keeping the result complete.
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertEqual(result["skipped"][0]["clipIndex"], 2)
        # A partial success must NOT mask the created work behind a top-level error.
        self.assertNotIn("error", result)

    def test_stop_on_error_no_prior_success_is_failed_exit_13(self):
        # M4: continueOnError=false, the FIRST clip breaches the resource limit so
        # nothing succeeded -> wholly failed, exit 13 (SEC-011 precedence). The
        # untried clip is reported skipped and a top-level RESOURCE_LIMIT_EXCEEDED
        # error is surfaced (matching the standalone error result shape).
        def convert(ffmpeg, source, **kw):
            raise errors.EngineError(
                errors.RESOURCE_LIMIT_EXCEEDED,
                "per-clip timeout exceeded",
                exit_code=errors.EXIT_RESOURCE_LIMIT,
                status=errors.STATUS_FAILED,
                stage="encode",
                clip_index=kw["clip_index"],
            )

        m = self._write_manifest(
            [
                {"name": "a", "start": "0", "end": "1"},
                {"name": "b", "start": "2", "end": "3"},
            ],
            continueOnError=False,
        )
        with mock.patch("vtg.ffmpeg.convert_clip", side_effect=convert):
            code, result = self.run_cli(["batch", "--manifest", m, "--json"])
        self.assertEqual(code, errors.EXIT_RESOURCE_LIMIT)  # 13
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["summary"]["created"], 0)
        self.assertEqual(result["error"]["code"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertEqual(result["summary"]["skipped"], 1)


class TestValidateCommands(CLIBase):
    def test_validate_config_ok(self):
        path = os.path.join(self.project, "cfg.json")
        with open(path, "w") as fh:
            json.dump({"schemaVersion": 1, "defaultProfile": "high"}, fh)
        code, result = self.run_cli(["validate-config", "--config", path, "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(result["valid"])

    def test_validate_config_bad(self):
        path = os.path.join(self.project, "cfg.json")
        with open(path, "w") as fh:
            json.dump({"defaultProfile": "nope"}, fh)
        code, result = self.run_cli(["validate-config", "--config", path, "--json"])
        self.assertEqual(code, errors.EXIT_INVALID_USAGE)
        self.assertEqual(result["error"]["field"], "defaultProfile")

    def test_validate_manifest_ok(self):
        path = os.path.join(self.project, "clips.csv")
        with open(path, "w") as fh:
            fh.write("start,end\n00:00:00,00:00:01\n")
        code, result = self.run_cli(["validate-manifest", "--manifest", path, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["clipCount"], 1)


if __name__ == "__main__":
    unittest.main()
