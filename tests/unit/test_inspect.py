"""Source inspection tests with mocked ffprobe (FR-002, FR-003, SEC-010)."""

import json
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import errors
from vtg import inspect as vinspect


def fake_probe(payload, returncode=0, stderr=""):
    """Return a patched subprocess.run yielding the given ffprobe JSON."""

    def _run(*args, **kwargs):
        return types.SimpleNamespace(
            returncode=returncode, stdout=json.dumps(payload), stderr=stderr
        )

    return _run


def video_stream(
    index=0,
    width=1920,
    height=1080,
    fps="30/1",
    default=1,
    attached_pic=0,
    duration="60.0",
    codec="h264",
    side_data=None,
):
    s = {
        "index": index,
        "codec_type": "video",
        "codec_name": codec,
        "width": width,
        "height": height,
        "avg_frame_rate": fps,
        "r_frame_rate": fps,
        "duration": duration,
        "disposition": {"default": default, "attached_pic": attached_pic},
    }
    if side_data:
        s["side_data_list"] = side_data
    return s


class TestStreamSelection(unittest.TestCase):
    def _inspect(self, payload):
        with mock.patch.object(vinspect.subprocess, "run", fake_probe(payload)):
            return vinspect.inspect_source("ffprobe", "/x/demo.mp4")

    def test_single_stream(self):
        payload = {
            "format": {"format_name": "mov,mp4", "duration": "60.0"},
            "streams": [video_stream()],
        }
        src = self._inspect(payload)
        self.assertEqual(src.width, 1920)
        self.assertEqual(src.height, 1080)
        self.assertEqual(src.fps, 30.0)
        self.assertEqual(src.duration_ms, 60000)
        self.assertEqual(src.stream_index, 0)

    def test_thumbnail_excluded(self):
        payload = {
            "format": {"format_name": "mov,mp4", "duration": "60.0"},
            "streams": [
                video_stream(index=0, attached_pic=1, default=0, codec="mjpeg", fps="0/0"),
                video_stream(index=1, attached_pic=0, default=0),
            ],
        }
        src = self._inspect(payload)
        self.assertEqual(src.stream_index, 1)

    def test_multiple_with_default(self):
        payload = {
            "format": {"format_name": "matroska,webm", "duration": "60.0"},
            "streams": [
                video_stream(index=0, default=0),
                video_stream(index=1, default=1),
            ],
        }
        src = self._inspect(payload)
        self.assertEqual(src.stream_index, 1)
        self.assertTrue(any("default" in w for w in src.warnings))

    def test_ambiguous_streams(self):
        payload = {
            "format": {"format_name": "matroska,webm", "duration": "60.0"},
            "streams": [
                video_stream(index=0, default=0),
                video_stream(index=1, default=0),
            ],
        }
        with self.assertRaises(errors.EngineError) as ctx:
            self._inspect(payload)
        self.assertEqual(ctx.exception.code, errors.AMBIGUOUS_VIDEO_STREAM)

    def test_no_video_stream(self):
        payload = {
            "format": {"format_name": "mp3", "duration": "60.0"},
            "streams": [{"index": 0, "codec_type": "audio", "codec_name": "aac"}],
        }
        with self.assertRaises(errors.EngineError) as ctx:
            self._inspect(payload)
        self.assertEqual(ctx.exception.code, errors.NO_VIDEO_STREAM)

    def test_rotation_swaps_display(self):
        payload = {
            "format": {"format_name": "mov,mp4", "duration": "60.0"},
            "streams": [
                video_stream(side_data=[{"side_data_type": "Display Matrix", "rotation": -90}])
            ],
        }
        src = self._inspect(payload)
        self.assertEqual(src.rotation, 270)  # -90 % 360
        self.assertEqual(src.display_width, 1080)
        self.assertEqual(src.display_height, 1920)

    def test_duration_disagreement_prefers_stream(self):
        payload = {
            "format": {"format_name": "mov,mp4", "duration": "60.0"},
            "streams": [video_stream(duration="55.0")],
        }
        src = self._inspect(payload)
        self.assertEqual(src.duration_ms, 55000)
        self.assertTrue(any("disagree" in w for w in src.warnings))


class TestContainerRejection(unittest.TestCase):
    def _inspect(self, format_name):
        payload = {
            "format": {"format_name": format_name, "duration": "60.0"},
            "streams": [video_stream()],
        }
        with mock.patch.object(vinspect.subprocess, "run", fake_probe(payload)):
            return vinspect.inspect_source("ffprobe", "/x/hostile")

    def test_hls_rejected(self):
        for fmt in ("hls,applehttp", "dash", "concat", "hls"):
            with self.subTest(fmt=fmt):
                with self.assertRaises(errors.EngineError) as ctx:
                    self._inspect(fmt)
                self.assertEqual(ctx.exception.code, errors.UNSUPPORTED_MEDIA_CONTAINER)
                self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_MEDIA)

    def test_normal_container_ok(self):
        # Should not raise.
        self._inspect("mov,mp4,m4a,3gp")


class TestContainerSniffing(unittest.TestCase):
    """Content/extension-based reference-container detection (SEC-010)."""

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name, content):
        p = os.path.join(self.dir, name)
        with open(p, "w") as fh:
            fh.write(content)
        return p

    def test_m3u8_extension(self):
        p = self._write("v.m3u8", "#EXTM3U\n#EXTINF:1,\nhttp://evil/seg.ts\n")
        self.assertEqual(vinspect.sniff_reference_container(p), "hls")

    def test_hls_by_content_without_extension(self):
        p = self._write("playlist", "#EXTM3U\nhttp://evil/seg.ts\n")
        self.assertEqual(vinspect.sniff_reference_container(p), "hls")

    def test_dash_mpd(self):
        p = self._write(
            "v.mpd", '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011"></MPD>'
        )
        self.assertEqual(vinspect.sniff_reference_container(p), "dash")

    def test_concat_script(self):
        p = self._write("list.txt", "ffconcat version 1.0\nfile 'a.mp4'\n")
        self.assertEqual(vinspect.sniff_reference_container(p), "concat")

    def test_concat_file_lines(self):
        p = self._write("list2.txt", "file '/etc/passwd'\nfile 'b.mp4'\n")
        self.assertEqual(vinspect.sniff_reference_container(p), "concat")

    def test_normal_binary_not_detected(self):
        p = os.path.join(self.dir, "v.mp4")
        with open(p, "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42rest of binary")
        self.assertIsNone(vinspect.sniff_reference_container(p))

    def test_hostile_playlist_never_invokes_ffprobe(self):
        # SEC-010: a hostile local playlist must be rejected before ffprobe runs,
        # guaranteeing no network access.
        p = self._write("hostile.m3u8", "#EXTM3U\nhttp://169.254.169.254/\n")

        def _boom(*a, **k):
            raise AssertionError("ffprobe must not be invoked for a hostile playlist")

        with (
            mock.patch.object(vinspect.subprocess, "run", _boom),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            vinspect.inspect_source("ffprobe", p)
        self.assertEqual(ctx.exception.code, errors.UNSUPPORTED_MEDIA_CONTAINER)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_MEDIA)


class TestProbeFailure(unittest.TestCase):
    def test_nonzero_returncode(self):
        with (
            mock.patch.object(
                vinspect.subprocess, "run", fake_probe({}, returncode=1, stderr="boom")
            ),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            vinspect.inspect_source("ffprobe", "/x/bad")
        self.assertEqual(ctx.exception.code, errors.UNSUPPORTED_MEDIA)

    def test_protocol_whitelist_present_in_command(self):
        cmd = vinspect.build_ffprobe_command("ffprobe", "/x/demo.mp4")
        self.assertIn("-protocol_whitelist", cmd)
        idx = cmd.index("-protocol_whitelist")
        self.assertEqual(cmd[idx + 1], "file,pipe")


if __name__ == "__main__":
    unittest.main()
