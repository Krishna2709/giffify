"""Error serialization and loop-syntax tests (NFR-003, FR-015, section 22.1)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import errors
from vtg.models import loop_to_ffmpeg, parse_loop


class TestErrorSerialization(unittest.TestCase):
    def test_to_dict_shape(self):
        exc = errors.EngineError(
            errors.FFMPEG_FAILED,
            "boom",
            exit_code=errors.EXIT_FFMPEG_FAILED,
            stage="encode",
            clip_index=3,
            remediation="retry",
            field="clips[3]",
        )
        d = exc.to_dict()
        self.assertEqual(d["code"], "FFMPEG_FAILED")
        self.assertEqual(d["message"], "boom")
        self.assertEqual(d["stage"], "encode")
        self.assertEqual(d["clipIndex"], 3)
        self.assertEqual(d["remediation"], "retry")
        self.assertEqual(d["field"], "clips[3]")

    def test_null_stage_and_clip_present(self):
        exc = errors.EngineError(errors.INVALID_CONFIG, "bad", exit_code=2)
        d = exc.to_dict()
        self.assertIn("stage", d)
        self.assertIn("clipIndex", d)
        self.assertIsNone(d["stage"])
        self.assertIsNone(d["clipIndex"])

    def test_exit_code_map(self):
        # A representative spot-check against spec section 14.
        self.assertEqual(errors.EXIT_SUCCESS, 0)
        self.assertEqual(errors.EXIT_INVALID_USAGE, 2)
        self.assertEqual(errors.EXIT_DEPENDENCY_MISSING, 3)
        self.assertEqual(errors.EXIT_INPUT_NOT_FOUND, 4)
        self.assertEqual(errors.EXIT_INVALID_MEDIA, 5)
        self.assertEqual(errors.EXIT_INVALID_TIMESTAMP, 6)
        self.assertEqual(errors.EXIT_COLLISION, 7)
        self.assertEqual(errors.EXIT_PERMISSION, 8)
        self.assertEqual(errors.EXIT_FFMPEG_FAILED, 9)
        self.assertEqual(errors.EXIT_CANCELLED, 10)
        self.assertEqual(errors.EXIT_PARTIAL, 11)
        self.assertEqual(errors.EXIT_INTERNAL, 12)
        self.assertEqual(errors.EXIT_RESOURCE_LIMIT, 13)

    def test_cancelled_error(self):
        exc = errors.CancelledError(clip_index=2)
        self.assertEqual(exc.code, errors.CANCELLED)
        self.assertEqual(exc.exit_code, errors.EXIT_CANCELLED)
        self.assertEqual(exc.status, errors.STATUS_CANCELLED)


class TestLoopSyntax(unittest.TestCase):
    def test_forever(self):
        self.assertEqual(parse_loop("forever"), "forever")
        self.assertEqual(parse_loop("FOREVER"), "forever")
        self.assertEqual(parse_loop("  forever  "), "forever")

    def test_once_is_one(self):
        self.assertEqual(parse_loop("once"), 1)
        self.assertEqual(parse_loop("ONCE"), 1)

    def test_integer_counts(self):
        self.assertEqual(parse_loop(1), 1)
        self.assertEqual(parse_loop(5), 5)
        self.assertEqual(parse_loop("3"), 3)

    def test_zero_rejected(self):
        for bad in (0, "0"):
            with self.subTest(bad=bad):
                with self.assertRaises(errors.EngineError) as ctx:
                    parse_loop(bad)
                self.assertEqual(ctx.exception.code, errors.INVALID_LOOP)

    def test_negative_and_garbage_rejected(self):
        for bad in (-1, "-2", "loop", "", 1.5, True, None):
            with self.subTest(bad=bad), self.assertRaises(errors.EngineError):
                parse_loop(bad)

    def test_loop_to_ffmpeg_mapping(self):
        self.assertEqual(loop_to_ffmpeg("forever"), 0)
        self.assertEqual(loop_to_ffmpeg(1), -1)  # once -> play once
        self.assertEqual(loop_to_ffmpeg(2), 1)  # 2 plays -> 1 repeat
        self.assertEqual(loop_to_ffmpeg(5), 4)


if __name__ == "__main__":
    unittest.main()
