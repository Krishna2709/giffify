"""Executable acceptance suite for version 0.1.0 (spec section 23).

One test per acceptance criterion (AC-001 .. AC-014), named ``test_ac_NNN_*`` with
the criterion text in the docstring. AC-015 (agent usability on both platforms)
is not automatable from here and is documented in ``AC-015-manual.md``.

All media is synthetic and generated at runtime; the engine is exercised end to
end as a subprocess with real ffmpeg, and every assertion is made against the
structured JSON result contract (spec section 13) and filesystem side effects.

Substitution note (AC-001): the criterion specifies a 15-minute source. A long
source is unnecessary to prove the semantics, so a ~70-second synthetic source is
used instead -- it is comfortably longer than the 01:00-01:05 range, so the
"range well inside a longer source yields one ~5 s GIF" semantics are preserved.
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
from fixtures.base import EngineTestCase, media


class _Listener:
    """Local TCP listener that records whether a connection was ever accepted."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.connected = threading.Event()
        self._t = threading.Thread(target=self._accept, daemon=True)
        self._t.start()

    def _accept(self):
        self.sock.settimeout(4.0)
        try:
            conn, _ = self.sock.accept()
            self.connected.set()
            conn.close()
        except (TimeoutError, OSError):
            pass

    def url(self, path="/v.mp4"):
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()
        self._t.join(timeout=1.0)


class TestAcceptance(EngineTestCase):
    long_src: ClassVar[str]
    hd_src: ClassVar[str]
    heavy_src: ClassVar[str]
    unicode_src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        # ~70 s source (AC-001/003 substitution) -- comfortably longer than the
        # 01:00-01:05 range so a 5 s slice is well inside it.
        cls.long_src = cls.media_file("long.mp4")
        media.generate_landscape(cls.long_src, size="320x240", fps=15, duration=70.0)
        # HD source so quality profiles visibly downscale (AC-009, AC-010).
        cls.hd_src = cls.media_file("hd.mp4")
        media.generate_landscape(cls.hd_src, size="1280x720", fps=30, duration=2.0)
        # Heavy source giving a reliable window to cancel (AC-012).
        cls.heavy_src = cls.media_file("heavy.mp4")
        media.generate_landscape(cls.heavy_src, size="1920x1080", fps=30, duration=8.0)
        # Source with spaces + Unicode in the name (AC-011).
        cls.unicode_src = cls.media_file("mövie clip ünïcode.mp4")
        media.generate_landscape(cls.unicode_src, size="320x240", fps=15, duration=2.0)

    def _write_json(self, name, data):
        path = os.path.join(self.project, name)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def _write_text(self, name, text):
        path = os.path.join(self.project, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    # -- AC-001 ------------------------------------------------------------
    def test_ac_001_single_gif(self):
        """AC-001: Given a valid (long) local video and the range 01:00-01:05, the
        product creates one approximately five-second GIF in ./output."""
        res = self.run_engine(
            [
                "create",
                "--input",
                self.long_src,
                "--start",
                "01:00",
                "--end",
                "01:05",
                "--profile",
                "small",
            ]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 1)
        clip = res.created[0]
        self.assertEqual(clip["durationMs"], 5000)
        self.assertTrue(clip["path"].startswith("./output/"))
        gif = self.output_path(os.path.basename(clip["path"]))
        self.assert_valid_gif(gif)
        # ~5 seconds at 10 fps -> ~50 frames.
        info = self.probe_gif(gif)
        self.assertLessEqual(abs(info["nb_frames"] - 50), 2)

    # -- AC-002 ------------------------------------------------------------
    def test_ac_002_ten_gifs(self):
        """AC-002: Given ten valid timestamp ranges, the product creates ten
        independently named GIFs."""
        clips = [{"name": f"clip{i:02d}", "start": str(i), "duration": 1} for i in range(10)]
        m = self._write_json(
            "ten.json",
            {"schemaVersion": 1, "input": self.long_src, "profile": "small", "clips": clips},
        )
        res = self.run_engine(["batch", "--manifest", m])
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 10)
        self.assertEqual(len(set(self.list_output())), 10)

    # -- AC-003 ------------------------------------------------------------
    def test_ac_003_duration_input(self):
        """AC-003: Given start=01:00 and duration=5, the output is equivalent to
        start=01:00 and end=01:05."""
        by_end = self.run_engine(
            [
                "create",
                "--input",
                self.long_src,
                "--start",
                "01:00",
                "--end",
                "01:05",
                "--profile",
                "small",
                "--output-name",
                "end.gif",
            ]
        )
        by_dur = self.run_engine(
            [
                "create",
                "--input",
                self.long_src,
                "--start",
                "01:00",
                "--duration",
                "5",
                "--profile",
                "small",
                "--output-name",
                "dur.gif",
            ]
        )
        self.assert_exit(by_end, 0)
        self.assert_exit(by_dur, 0)
        a, b = by_end.created[0], by_dur.created[0]
        for key in ("startMs", "endMs", "durationMs", "width", "height", "fps"):
            self.assertEqual(a[key], b[key], f"mismatch on {key}")
        self.assertEqual((a["startMs"], a["endMs"]), (60000, 65000))

    # -- AC-004 ------------------------------------------------------------
    def test_ac_004_json_batch(self):
        """AC-004: A valid JSON manifest creates all requested GIFs."""
        m = self._write_json(
            "clips.json",
            {
                "schemaVersion": 1,
                "input": self.long_src,
                "profile": "small",
                "clips": [
                    {"name": "a", "start": "0", "end": "1"},
                    {"name": "b", "start": "2", "duration": 1},
                    {"name": "c", "start": "00:00:04", "end": "00:00:05"},
                ],
            },
        )
        res = self.run_engine(["batch", "--manifest", m])
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 3)
        self.assertEqual(set(self.list_output()), {"a.gif", "b.gif", "c.gif"})

    # -- AC-005 ------------------------------------------------------------
    def test_ac_005_csv_batch(self):
        """AC-005: A valid CSV manifest creates all requested GIFs."""
        m = self._write_text(
            "clips.csv",
            "name,start,end,duration,profile\none,0,1,,small\ntwo,2,,1,small\nthree,4,5,,small\n",
        )
        res = self.run_engine(["batch", "--manifest", m, "--input", self.long_src])
        self.assert_exit(res, 0)
        self.assertEqual(res.summary["created"], 3)
        self.assertEqual(set(self.list_output()), {"one.gif", "two.gif", "three.gif"})

    # -- AC-006 ------------------------------------------------------------
    def test_ac_006_invalid_timestamps(self):
        """AC-006: A timestamp beyond source duration is rejected before
        conversion; no GIF is produced for the invalid clip without an explicit
        skip or clamp policy."""
        # Source is ~70 s; 02:00 is beyond its duration. Default policy is fail.
        res = self.run_engine(
            [
                "create",
                "--input",
                self.long_src,
                "--start",
                "01:00",
                "--end",
                "02:00",
                "--profile",
                "small",
            ]
        )
        self.assert_exit(res, 6)  # EXIT_INVALID_TIMESTAMP
        self.assert_status(res, "validation_failed")
        self.assert_error_code(res, "INVALID_TIMESTAMP")
        self.assertEqual(self.list_output(), [])  # nothing encoded

    # -- AC-007 ------------------------------------------------------------
    def test_ac_007_collision_protection(self):
        """AC-007: When an output exists, it remains unchanged under the default
        policy."""
        os.makedirs(self.output_dir, exist_ok=True)
        name = "existing.gif"
        original = b"KEEP-ME-UNCHANGED-0123456789"
        with open(self.output_path(name), "wb") as fh:
            fh.write(original)
        res = self.run_engine(
            [
                "create",
                "--input",
                self.long_src,
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
            self.assertEqual(fh.read(), original)

    # -- AC-008 ------------------------------------------------------------
    def test_ac_008_partial_batch_failure(self):
        """AC-008: If one runtime conversion fails, remaining clips are attempted
        and the result is partial_success."""
        # The second clip requests colors=2, which makes palettegen fail at
        # runtime while the other clips convert normally.
        m = self._write_json(
            "mixed.json",
            {
                "schemaVersion": 1,
                "input": self.long_src,
                "profile": "small",
                "clips": [
                    {"name": "ok1", "start": "0", "end": "1"},
                    {"name": "boom", "start": "1", "end": "2", "colors": 2},
                    {"name": "ok2", "start": "2", "end": "3"},
                ],
            },
        )
        res = self.run_engine(["batch", "--manifest", m])
        self.assert_exit(res, 11)  # EXIT_PARTIAL
        self.assert_status(res, "partial_success")
        self.assertEqual(res.summary["created"], 2)
        self.assertEqual(res.summary["failed"], 1)
        self.assertEqual(res.failed[0]["clipIndex"], 1)
        self.assertEqual(res.failed[0]["code"], "FFMPEG_FAILED")
        # The other two clips were still produced.
        self.assertEqual(set(self.list_output()), {"ok1.gif", "ok2.gif"})

    # -- AC-009 ------------------------------------------------------------
    def test_ac_009_quality_profiles(self):
        """AC-009: The small, balanced, and high profiles produce outputs with the
        documented effective width, frame rate, and color limits.

        Color-cap note: the GIF muxer pads the global color table to 256 entries
        regardless of the palettegen max_colors cap, so the exact 128/256 cap is
        not observable from the GIF bytes with the standard library; width, height
        and effective frame rate (via frame count) are verified instead.
        """
        expected = {"small": (480, 270, 10), "balanced": (640, 360, 15), "high": (960, 540, 20)}
        for profile, (w, h, fps) in expected.items():
            name = f"{profile}.gif"
            res = self.run_engine(
                [
                    "create",
                    "--input",
                    self.hd_src,
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
            clip = res.created[0]
            self.assertEqual((clip["width"], clip["height"], clip["fps"]), (w, h, fps))
            info = self.probe_gif(self.output_path(name))
            self.assertEqual((info["width"], info["height"]), (w, h))
            self.assertLessEqual(abs(info["nb_frames"] - 2 * fps), 1)
            self.assertLessEqual(self.parse_gif_header(self.output_path(name))["gct_colors"], 256)

    # -- AC-010 ------------------------------------------------------------
    def test_ac_010_project_configuration(self):
        """AC-010: Saved project defaults are used on subsequent invocations and
        request-specific overrides take precedence."""
        cfg = self._write_json(
            ".video-to-gif.json", {"schemaVersion": 1, "defaultProfile": "small"}
        )
        with open(cfg, "rb") as fh:
            cfg_before = fh.read()
        # No --profile: the saved default (small -> 480 wide for the HD source).
        saved = self.run_engine(
            [
                "create",
                "--input",
                self.hd_src,
                "--start",
                "0",
                "--end",
                "1",
                "--output-name",
                "saved.gif",
            ]
        )
        self.assert_exit(saved, 0)
        self.assertEqual(saved.created[0]["width"], 480)
        # Request-specific override wins (high -> 960 wide) without touching config.
        override = self.run_engine(
            [
                "create",
                "--input",
                self.hd_src,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "high",
                "--output-name",
                "override.gif",
            ]
        )
        self.assert_exit(override, 0)
        self.assertEqual(override.created[0]["width"], 960)
        with open(cfg, "rb") as fh:
            self.assertEqual(fh.read(), cfg_before)  # config unchanged

    # -- AC-011 ------------------------------------------------------------
    def test_ac_011_cross_platform_paths(self):
        """AC-011: Input and output paths containing spaces and Unicode work."""
        out_name = "füll résult clip.gif"
        res = self.run_engine(
            [
                "create",
                "--input",
                self.unicode_src,
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

    # -- AC-012 ------------------------------------------------------------
    def test_ac_012_cancellation(self):
        """AC-012: Cancelling during conversion stops the active process, removes
        incomplete output, preserves completed output, and returns cancelled."""
        # k0 is deliberately tiny and k1..k3 are long: a batch may encode
        # independent clips concurrently, so "clip 0 completed" must not be taken
        # to mean "clip 1 has not started yet". The short first clip guarantees a
        # completed GIF exists while the rest are still mid-encode, whatever the
        # worker count (including 1), which is the window this criterion needs.
        m = self._write_json(
            "cancel.json",
            {
                "schemaVersion": 1,
                "input": self.heavy_src,
                "profile": "high",
                "clips": [{"name": "k0", "start": "0", "end": "0.2"}]
                + [{"name": f"k{i}", "start": "0", "end": "3"} for i in range(1, 4)],
            },
        )

        def trigger(ev):
            return ev.get("event") == "clip_completed" and ev.get("clipIndex") == 0

        res = self.run_engine_until(["batch", "--manifest", m], trigger, timeout=120)
        self.assert_exit(res, 10)  # EXIT_CANCELLED
        self.assert_status(res, "cancelled")
        self.assertTrue(
            any(e.get("event") == "clip_completed" and e.get("clipIndex") == 0 for e in res.events)
        )
        outputs = set(self.list_output())
        self.assertIn("k0.gif", outputs)  # completed output preserved
        self.assert_valid_gif(self.output_path("k0.gif"))
        self.assertNotIn("k1.gif", outputs)  # incomplete output removed
        self.assertEqual(self.temp_gif_leftovers(), [])  # temp files cleaned

    # -- AC-013 ------------------------------------------------------------
    def test_ac_013_local_only_behavior(self):
        """AC-013: no network access by default.

        The v0.1.0 criterion ("performs no network access") remains true in
        v0.2.0 because remote source acquisition is disabled by default (FR-018).
        A URL supplied with the default configuration is now rejected with
        REMOTE_DISABLED / exit 8 (v0.1.0 reported UNSUPPORTED_REMOTE_SOURCE /
        exit 5); either way the engine makes no network connection and writes no
        output -- the guarantee the criterion asserts.
        """
        lis = _Listener()
        self.addCleanup(lis.close)
        res = self.run_engine(["create", "--input", lis.url(), "--start", "0", "--end", "1"])
        self.assert_exit(res, 8)  # EXIT_PERMISSION (remote disabled)
        self.assert_error_code(res, "REMOTE_DISABLED")
        self.assertFalse(lis.connected.is_set(), "engine attempted a network connection")
        self.assertEqual(self.list_output(), [])

    # -- AC-014 ------------------------------------------------------------
    def test_ac_014_no_command_injection(self):
        """AC-014: Malicious-looking filenames and manifest fields cannot cause
        unintended commands to execute."""
        # 1) Shell substitution in --output-name stays literal, executes nothing.
        res = self.run_engine(
            [
                "create",
                "--input",
                self.long_src,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                "--output-name",
                "$(touch owned).gif",
            ]
        )
        self.assert_exit(res, 0)
        self.assertIn("$(touch owned).gif", self.list_output())
        # 2) Shell operators in a manifest name stay literal.
        m = self._write_json(
            "evil.json",
            {
                "schemaVersion": 1,
                "input": self.long_src,
                "profile": "small",
                "clips": [{"name": "x; touch owned2 && `id`", "start": "1", "end": "2"}],
            },
        )
        res2 = self.run_engine(["batch", "--manifest", m])
        self.assert_exit(res2, 0)
        # No injected command ran anywhere the engine could have executed it.
        for junk in ("owned", "owned2", "id"):
            for base in (self.project, self.output_dir):
                self.assertFalse(
                    os.path.exists(os.path.join(base, junk)),
                    f"unexpected side-effect file {junk!r}",
                )


if __name__ == "__main__":
    unittest.main()
