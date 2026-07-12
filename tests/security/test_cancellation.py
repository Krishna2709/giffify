"""Security / acceptance: cancellation mid-encode (spec section 16, 22.4, AC-012).

A batch of heavier clips is started as a subprocess; once the first clip has
completed (observed on the stderr progress stream), SIGINT is delivered to the
engine mid-encode. The engine must stop the active ffmpeg process, remove the
incomplete GIF, preserve the already-completed GIF, clean temporary files, and
return status ``cancelled`` with exit code 10.
"""

import json
import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import EngineTestCase, media


class TestCancellation(EngineTestCase):
    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        # Heavy enough that each high-profile clip takes ~1s+, giving a reliable
        # window to interrupt clip 1 after clip 0 completes.
        cls.src = cls.media_file("heavy.mp4")
        media.generate_landscape(cls.src, size="1920x1080", fps=30, duration=8.0)

    def _manifest(self):
        data = {
            "schemaVersion": 1,
            "input": self.src,
            "profile": "high",
            "clips": [
                {"name": "c0", "start": "0", "end": "3"},
                {"name": "c1", "start": "0", "end": "3"},
                {"name": "c2", "start": "0", "end": "3"},
                {"name": "c3", "start": "0", "end": "3"},
            ],
        }
        path = os.path.join(self.project, "clips.json")
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def test_sigint_preserves_completed_removes_partial(self):
        m = self._manifest()

        def trigger(ev):
            return ev.get("event") == "clip_completed" and ev.get("clipIndex") == 0

        res = self.run_engine_until(["batch", "--manifest", m], trigger, timeout=120)

        # --- TEMP DIAGNOSTIC (remove before final) ------------------------
        import subprocess as _sp

        print("\n=== CANCEL DIAG START ===", file=sys.stderr)
        print(f"returncode={res.returncode} status={res.status!r}", file=sys.stderr)
        print(
            f"events={[(e.get('event'), e.get('clipIndex')) for e in res.events]}", file=sys.stderr
        )
        print("STDERR TAIL:\n" + res.stderr[-1500:], file=sys.stderr)
        try:
            print(f"project listing={os.listdir(self.project)}", file=sys.stderr)
            if os.path.isdir(self.output_dir):
                print(f"output listing={os.listdir(self.output_dir)}", file=sys.stderr)
            print(f"media listing={os.listdir(self.media_dir)}", file=sys.stderr)
        except OSError as _e:
            print(f"listing error: {_e}", file=sys.stderr)
        if sys.platform == "win32":
            for img in ("ffmpeg.exe", "ffprobe.exe"):
                try:
                    tl = _sp.run(
                        ["tasklist", "/FI", f"IMAGENAME eq {img}", "/FO", "CSV"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    print(f"tasklist {img}:\n{tl.stdout}", file=sys.stderr)
                except OSError as _e:
                    print(f"tasklist error: {_e}", file=sys.stderr)
        print("=== CANCEL DIAG END ===\n", file=sys.stderr)
        # --- END TEMP DIAGNOSTIC ------------------------------------------

        # Contract: cancelled status and exit code 10.
        self.assert_exit(res, 10)  # EXIT_CANCELLED
        self.assert_status(res, "cancelled")

        # We must actually have observed clip 0 completing before cancelling.
        self.assertTrue(
            any(e.get("event") == "clip_completed" and e.get("clipIndex") == 0 for e in res.events),
            "clip 0 never reported completion; cannot assess preservation",
        )

        outputs = set(self.list_output())
        # Completed GIF preserved.
        self.assertIn("c0.gif", outputs)
        self.assert_valid_gif(self.output_path("c0.gif"))
        # The clip that was interrupted (and any not-yet-started clips) left no GIF.
        self.assertNotIn("c1.gif", outputs)
        self.assertNotIn("c3.gif", outputs)
        # No temporary GIF artifacts remain in the output directory.
        self.assertEqual(self.temp_gif_leftovers(), [])


if __name__ == "__main__":
    unittest.main()
