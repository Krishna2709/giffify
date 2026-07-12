"""Security: no network access and resource-limit enforcement (spec 22.4,
SEC-005, SEC-010, SEC-011, AC-013). Real ffmpeg.

A real local TCP listener is bound and its URL embedded in hostile inputs; the
tests assert the listener never receives a connection, proving the no-network
guarantee is enforced before ffmpeg ever opens the reference-following input.
"""

import contextlib
import json
import os
import socket
import sys
import threading
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import FFMPEG, EngineTestCase, media

from vtg import errors as vtg_errors
from vtg import ffmpeg as vtg_ffmpeg
from vtg.models import EffectiveSettings, SourceInfo


class _Listener:
    """A local TCP listener that records whether anyone connected."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.connected = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        self.sock.settimeout(4.0)
        try:
            conn, _ = self.sock.accept()
            self.connected.set()
            conn.close()
        except (TimeoutError, OSError):
            pass

    def url(self, path="/seg.ts"):
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()
        self._thread.join(timeout=1.0)


class TestReferenceFollowingContainers(EngineTestCase):
    @classmethod
    def generate_media(cls):
        pass  # hostile playlists are written per-test

    def _listener(self):
        lis = _Listener()
        self.addCleanup(lis.close)
        return lis

    def _write(self, name, text):
        path = os.path.join(self.project, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_hostile_m3u8_rejected_without_network(self):
        # SEC-010: HLS playlist referencing a network URL -> exit 5, no connection.
        lis = self._listener()
        p = self._write("hostile.m3u8", "#EXTM3U\n#EXT-X-VERSION:3\n" + lis.url() + "\n")
        res = self.run_engine(["inspect", "--input", p])
        self.assert_exit(res, 5)  # EXIT_INVALID_MEDIA
        self.assert_error_code(res, "UNSUPPORTED_MEDIA_CONTAINER")
        self.assertFalse(lis.connected.is_set(), "engine made a network connection")

    def test_hostile_mpd_rejected_without_network(self):
        lis = self._listener()
        p = self._write(
            "hostile.mpd",
            '<?xml version="1.0"?>\n<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">'
            f"<BaseURL>{lis.url('/')}</BaseURL></MPD>\n",
        )
        res = self.run_engine(["inspect", "--input", p])
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_MEDIA_CONTAINER")
        self.assertFalse(lis.connected.is_set())

    def test_hostile_concat_rejected_without_network(self):
        # Content-sniffed concat script (extension does not reveal it).
        lis = self._listener()
        p = self._write("playlist.txt", "ffconcat version 1.0\nfile '" + lis.url("/a.mp4") + "'\n")
        res = self.run_engine(["inspect", "--input", p])
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_MEDIA_CONTAINER")
        self.assertFalse(lis.connected.is_set())

    def test_hostile_playlist_in_create_no_output(self):
        lis = self._listener()
        p = self._write("hostile2.m3u8", "#EXTM3U\n" + lis.url() + "\n")
        res = self.run_engine(["create", "--input", p, "--start", "0", "--end", "1"])
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_MEDIA_CONTAINER")
        self.assertFalse(lis.connected.is_set())
        self.assertEqual(self.list_output(), [])


class TestRemoteUrls(EngineTestCase):
    @classmethod
    def generate_media(cls):
        pass

    def test_http_url_rejected_as_remote_no_fetch(self):
        # SEC-005/AC-013: URL input -> UNSUPPORTED_REMOTE_SOURCE, never fetched.
        lis = _Listener()
        self.addCleanup(lis.close)
        res = self.run_engine(
            ["create", "--input", lis.url("/v.mp4"), "--start", "0", "--end", "1"]
        )
        self.assert_exit(res, 5)  # EXIT_INVALID_MEDIA
        self.assert_error_code(res, "UNSUPPORTED_REMOTE_SOURCE")
        self.assertFalse(lis.connected.is_set())
        self.assertEqual(self.list_output(), [])

    def test_https_url_rejected_as_remote(self):
        res = self.run_engine(
            ["create", "--input", "https://example.invalid/v.mp4", "--start", "0", "--end", "1"]
        )
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_REMOTE_SOURCE")

    def test_file_url_rejected_without_fetch(self):
        # Security-critical property (holds): a file:// input is rejected with a
        # non-zero exit and no network activity. (The specific error code differs
        # from UNSUPPORTED_REMOTE_SOURCE -- see the expected-failure test below.)
        res = self.run_engine(
            ["create", "--input", "file:///no/such/local/file.mp4", "--start", "0", "--end", "1"]
        )
        self.assertNotEqual(res.returncode, 0)
        self.assert_status(res, "failed")
        self.assertEqual(self.list_output(), [])

    def test_file_url_reported_as_unsupported_remote_source(self):
        """BUG-001 fixed: file:// URL is classified as a remote source.

        Spec SEC-005 requires that *a URL* supplied to v0.1.0 produces an
        UNSUPPORTED_REMOTE_SOURCE result. ``vtg.paths.reject_if_remote`` now
        rejects *any* ``scheme://`` input — including ``file://`` — so
        ``paths.resolve_source_path`` short-circuits before touching the
        filesystem and the engine returns UNSUPPORTED_REMOTE_SOURCE (exit 5)
        rather than treating ``file:///...`` as a (non-existent) local path.

        Repro:
            python3 scripts/video_to_gif.py create \\
              --input 'file:///no/such/file.mp4' --start 0 --end 1 --json
          per SEC-005: error.code == UNSUPPORTED_REMOTE_SOURCE, exit 5
        """
        res = self.run_engine(
            ["create", "--input", "file:///no/such/local/file.mp4", "--start", "0", "--end", "1"]
        )
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_REMOTE_SOURCE")


class TestResourceLimits(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("rl.mp4")
        media.generate_landscape(cls.src, size="640x360", fps=30, duration=2.0)

    def _write_config(self, data):
        with open(os.path.join(self.project, ".video-to-gif.json"), "w") as fh:
            json.dump(data, fh)

    def test_resource_limit_exit_13_and_cleanup(self):
        # SEC-011: exceeding the per-clip wall-clock limit terminates the process,
        # cleans temp/partial output, and yields RESOURCE_LIMIT_EXCEEDED exit 13.
        # A sub-second limit makes the breach deterministic regardless of host
        # speed (per the task's guidance to use a deterministic config knob).
        self._write_config(
            {
                "schemaVersion": 1,
                "continueOnError": False,
                "limits": {"maxClipProcessingSeconds": 0.5},
            }
        )
        res = self.run_engine(
            ["create", "--input", self.src, "--start", "0", "--end", "2", "--profile", "high"]
        )
        self.assert_exit(res, 13)  # EXIT_RESOURCE_LIMIT
        self.assert_error_code(res, "RESOURCE_LIMIT_EXCEEDED")
        self.assertEqual(self.list_output(), [])
        self.assertEqual(self.temp_gif_leftovers(), [])

    def test_resource_limit_direct_pipeline_cleanup(self):
        # Deterministic at the pipeline layer: a zero-second timeout breaches on
        # the first poll; the process group is terminated and temp files removed.
        out_dir = os.path.join(self.project, "out")
        os.makedirs(out_dir, exist_ok=True)
        source = SourceInfo(
            path=self.src,
            duration_ms=2000,
            width=640,
            height=360,
            display_width=640,
            display_height=360,
            fps=30.0,
            codec="h264",
            stream_index=0,
        )
        settings = EffectiveSettings(
            width=640, height=360, fps=20.0, colors=256, loop="forever", profile_name="high"
        )
        before = self._engine_temp_dirs()
        assert FFMPEG is not None  # class is skipped unless ffmpeg is present
        with self.assertRaises(vtg_errors.EngineError) as cm:
            vtg_ffmpeg.convert_clip(
                FFMPEG,
                source,
                start_ms=0,
                duration_ms=2000,
                settings=settings,
                dest_path=os.path.join(out_dir, "out.gif"),
                output_dir=out_dir,
                timeout_seconds=0.0,
                max_temp_bytes=2**31,
            )
        self.assertEqual(cm.exception.code, "RESOURCE_LIMIT_EXCEEDED")
        self.assertEqual(cm.exception.exit_code, 13)
        self.assertEqual([p for p in os.listdir(out_dir) if p.startswith(".vtg-")], [])
        self.assertEqual(self._engine_temp_dirs() - before, set())

    def test_resource_limit_default_continue_on_error_exit_13(self):
        """BUG-002 fixed: resource-limit breach exits 13 under the default
        continueOnError=true.

        Spec SEC-011 and section 14 require exit code 13 whenever a resource limit
        is exceeded. For a single-clip ``create`` with the documented default
        continueOnError=true, ``cli._run_conversions`` collects the
        RESOURCE_LIMIT_EXCEEDED failure into ``failed[]``; ``cli._job_exit_code``
        now applies the SEC-011 precedence rule — a wholly-failed job whose
        failures include a resource-limit breach maps to EXIT_RESOURCE_LIMIT (13)
        rather than being flattened to EXIT_FFMPEG_FAILED (9).

        Repro (config: {"limits": {"maxClipProcessingSeconds": 0.5}}):
            python3 scripts/video_to_gif.py create \\
              --input rl.mp4 --start 0 --end 2 --profile high --json
          per SEC-011/section 14: exit 13
        """
        self._write_config({"schemaVersion": 1, "limits": {"maxClipProcessingSeconds": 0.5}})
        res = self.run_engine(
            ["create", "--input", self.src, "--start", "0", "--end", "2", "--profile", "high"]
        )
        self.assert_exit(res, 13)


if __name__ == "__main__":
    unittest.main()
