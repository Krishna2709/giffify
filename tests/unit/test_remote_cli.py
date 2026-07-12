"""CLI-level remote gating and acquisition orchestration (spec section 12.8).

Hermetic and in-process: ffmpeg/ffprobe and the conversion are mocked, and the
remote download itself is mocked so no real network is used. These tests assert
the CLI wiring: the enablement gate, the additive ``remoteSource`` result block,
source-path redaction, download cleanup vs. retention, and inspect-on-URL.
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

from vtg import cli, errors, remote
from vtg.ffmpeg import ConversionResult

TOKEN = "SECRETsigTOKENshouldNOTleak0987654321"


class _Recorder:
    def __init__(self):
        self.calls = []

    def convert(self, ffmpeg, source, **kw):
        self.calls.append(kw)
        with open(kw["dest_path"], "wb") as fh:
            fh.write(b"GIF89a fake")
        s = kw["settings"]
        return ConversionResult(
            path=kw["dest_path"], size_bytes=11, width=s.width, height=s.height, fps=s.fps
        )


class RemoteCLIBase(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp()
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
        self.recorder = _Recorder()
        self._created_temp_dirs = []
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
        for d in self._created_temp_dirs:
            shutil.rmtree(d, ignore_errors=True)

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

    def make_fake_acquire(self, *, warnings=None, bytes_downloaded=4242):
        """A fake remote.acquire_remote_source that writes a real local temp file.

        Sets ``self.source.path`` to the download path so the pre-annotation
        result would leak the temp path if redaction failed.
        """
        temp_dir = tempfile.mkdtemp(prefix="vtg-remote-")
        self._created_temp_dirs.append(temp_dir)
        local = os.path.join(temp_dir, "remote-source.mp4")
        with open(local, "wb"):
            pass
        self.source.path = local
        self.temp_dir = temp_dir
        self.local_path = local

        def _acquire(url, **kw):
            return remote.RemoteResult(
                local_path=local,
                temp_dir=temp_dir,
                redacted_url=remote.redact_url(url),
                adapter="direct",
                bytes_downloaded=bytes_downloaded,
                warnings=list(warnings or []),
            )

        return _acquire


class TestGating(RemoteCLIBase):
    def test_url_disabled_by_default_no_acquire_no_socket(self):
        acquire = mock.Mock(side_effect=AssertionError("acquire must not run"))
        with (
            mock.patch("vtg.remote.acquire_remote_source", acquire),
            mock.patch(
                "vtg.remote.socket.create_connection", side_effect=AssertionError("connected")
            ),
        ):
            code, result = self.run_cli(
                ["create", "--input", "https://h/v.mp4", "--start", "0", "--end", "1", "--json"]
            )
        self.assertEqual(code, errors.EXIT_PERMISSION)  # 8
        self.assertEqual(result["status"], "remote_disabled")
        self.assertEqual(result["error"]["code"], errors.REMOTE_DISABLED)
        acquire.assert_not_called()
        self.assertEqual(len(self.recorder.calls), 0)

    def test_ask_treated_as_disabled(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "ask"})
        with mock.patch(
            "vtg.remote.acquire_remote_source", side_effect=AssertionError("must not run")
        ):
            code, result = self.run_cli(
                ["create", "--input", "https://h/v.mp4", "--start", "0", "--end", "1", "--json"]
            )
        self.assertEqual(code, errors.EXIT_PERMISSION)
        self.assertEqual(result["error"]["code"], errors.REMOTE_DISABLED)

    def test_allow_remote_flag_overrides_disabled(self):
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()):
            code, result = self.run_cli(
                [
                    "create",
                    "--input",
                    "https://h/v.mp4",
                    "--start",
                    "0",
                    "--end",
                    "1",
                    "--allow-remote",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(self.recorder.calls), 1)

    def test_config_enabled_permits(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "enabled"})
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()):
            code, result = self.run_cli(
                ["create", "--input", "https://h/v.mp4", "--start", "0", "--end", "1", "--json"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "success")


class TestRemoteResultAnnotation(RemoteCLIBase):
    def test_remote_source_block_and_redacted_path(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "enabled"})
        url = f"https://cdn.example.com/v.mp4?token={TOKEN}"
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()):
            code, result = self.run_cli(
                ["create", "--input", url, "--start", "0", "--end", "1", "--json"]
            )
        self.assertEqual(code, 0)
        self.assertIn("remoteSource", result)
        self.assertEqual(result["remoteSource"]["url"], "https://cdn.example.com/v.mp4")
        self.assertEqual(result["remoteSource"]["adapter"], "direct")
        # The internal temp path is never exposed; source.path is the redacted URL.
        self.assertEqual(result["source"]["path"], "https://cdn.example.com/v.mp4")
        # The signed token appears nowhere in the whole document.
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(self.temp_dir, json.dumps(result))

    def test_download_deleted_after_job_when_not_retained(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "enabled"})
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()):
            code, result = self.run_cli(
                ["create", "--input", "https://h/v.mp4", "--start", "0", "--end", "1", "--json"]
            )
        self.assertEqual(code, 0)
        self.assertFalse(result["remoteSource"]["retained"])
        self.assertIsNone(result["remoteSource"]["path"])
        # The secure temp dir is removed after the job (FR-020 / section 16).
        self.assertFalse(os.path.exists(self.temp_dir))

    def test_keep_remote_source_flag_retains_and_reports_path(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "enabled"})
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()):
            code, result = self.run_cli(
                [
                    "create",
                    "--input",
                    "https://h/v.mp4",
                    "--start",
                    "0",
                    "--end",
                    "1",
                    "--keep-remote-source",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(result["remoteSource"]["retained"])
        self.assertEqual(result["remoteSource"]["path"], self.local_path)
        # Retained download is preserved like a completed output.
        self.assertTrue(os.path.exists(self.temp_dir))
        self.assertTrue(os.path.isfile(self.local_path))

    def test_keep_remote_source_config_retains(self):
        self.write_config(
            {"schemaVersion": 1, "remoteSources": "enabled", "keepRemoteSource": True}
        )
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()):
            code, result = self.run_cli(
                ["create", "--input", "https://h/v.mp4", "--start", "0", "--end", "1", "--json"]
            )
        self.assertEqual(code, 0)
        self.assertTrue(result["remoteSource"]["retained"])
        self.assertTrue(os.path.exists(self.temp_dir))

    def test_http_warning_surfaced_in_result(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "enabled"})
        fake = self.make_fake_acquire(warnings=["Downloading over unencrypted http; not secure."])
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=fake):
            code, result = self.run_cli(
                [
                    "create",
                    "--input",
                    "http://h/v.mp4",
                    "--start",
                    "0",
                    "--end",
                    "1",
                    "--allow-insecure-http",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(any("unencrypted" in w.lower() for w in result["warnings"]))


class TestCleanupOnFailure(RemoteCLIBase):
    def test_download_deleted_even_when_pipeline_fails(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "enabled"})

        def boom(ffmpeg, source, **kw):
            raise errors.EngineError(
                errors.FFMPEG_FAILED,
                "encode failed",
                exit_code=errors.EXIT_FFMPEG_FAILED,
                stage="encode",
                clip_index=kw["clip_index"],
            )

        with (
            mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()),
            mock.patch("vtg.ffmpeg.convert_clip", side_effect=boom),
        ):
            _code, result = self.run_cli(
                ["create", "--input", "https://h/v.mp4", "--start", "0", "--end", "1", "--json"]
            )
        self.assertEqual(result["status"], "failed")
        # The download is removed on failure (FR-020: delete whether it succeeded
        # or failed).
        self.assertFalse(os.path.exists(self.temp_dir))


class TestInspectRemote(RemoteCLIBase):
    def test_inspect_url_acquires_then_inspects(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "enabled"})
        url = f"https://cdn.example.com/v.mp4?token={TOKEN}"
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()):
            code, result = self.run_cli(["inspect", "--input", url, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "success")
        self.assertIn("remoteSource", result)
        self.assertEqual(result["source"]["path"], "https://cdn.example.com/v.mp4")
        self.assertNotIn(TOKEN, json.dumps(result))
        # Download removed after inspection.
        self.assertFalse(os.path.exists(self.temp_dir))

    def test_inspect_url_disabled(self):
        with mock.patch(
            "vtg.remote.acquire_remote_source", side_effect=AssertionError("must not run")
        ):
            code, result = self.run_cli(["inspect", "--input", "https://h/v.mp4", "--json"])
        self.assertEqual(code, errors.EXIT_PERMISSION)
        self.assertEqual(result["error"]["code"], errors.REMOTE_DISABLED)


class TestBatchRemote(RemoteCLIBase):
    def _write_manifest(self, clips, **top):
        data = {"schemaVersion": 1, "input": "https://h/v.mp4", "clips": clips}
        data.update(top)
        path = os.path.join(self.project, "clips.json")
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_manifest_url_input_gated_and_acquired(self):
        self.write_config({"schemaVersion": 1, "remoteSources": "enabled"})
        m = self._write_manifest([{"name": "a", "start": "0", "end": "1"}])
        with mock.patch("vtg.remote.acquire_remote_source", side_effect=self.make_fake_acquire()):
            code, result = self.run_cli(["batch", "--manifest", m, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["summary"]["created"], 1)
        self.assertIn("remoteSource", result)
        self.assertFalse(os.path.exists(self.temp_dir))  # cleaned up

    def test_manifest_url_input_disabled(self):
        m = self._write_manifest([{"name": "a", "start": "0", "end": "1"}])
        with mock.patch(
            "vtg.remote.acquire_remote_source", side_effect=AssertionError("must not run")
        ):
            code, result = self.run_cli(["batch", "--manifest", m, "--json"])
        self.assertEqual(code, errors.EXIT_PERMISSION)
        self.assertEqual(result["error"]["code"], errors.REMOTE_DISABLED)


if __name__ == "__main__":
    unittest.main()
