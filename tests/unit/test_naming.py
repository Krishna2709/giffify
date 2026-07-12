"""Filename sanitization and output naming tests (spec FR-011, section 22.1)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import errors
from vtg.naming import (
    MAX_FILENAME_LENGTH,
    default_output_name,
    sanitize_output_name,
    sanitize_stem,
    unique_name,
)


class TestSanitizeOutputName(unittest.TestCase):
    def test_adds_gif_extension(self):
        self.assertEqual(sanitize_output_name("opening"), "opening.gif")
        self.assertEqual(sanitize_output_name("opening.gif"), "opening.gif")
        self.assertEqual(sanitize_output_name("opening.GIF"), "opening.gif")

    def test_windows_invalid_chars_replaced(self):
        self.assertEqual(sanitize_output_name('a<b>c:"?*.gif'), "a_b_c____.gif")

    def test_path_separators_rejected(self):
        for bad in ("a/b.gif", "a\\b.gif", "../evil.gif", "/etc/passwd.gif", "sub/x.gif"):
            with self.subTest(bad=bad):
                with self.assertRaises(errors.EngineError) as ctx:
                    sanitize_output_name(bad)
                self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_USAGE)

    def test_traversal_tokens_rejected(self):
        for bad in (".", "..", ""):
            with self.subTest(bad=bad), self.assertRaises(errors.EngineError):
                sanitize_output_name(bad)

    def test_reserved_windows_names(self):
        for name in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"):
            with self.subTest(name=name):
                out = sanitize_output_name(name + ".gif")
                # Reserved name must be defused (prefixed), not used verbatim.
                self.assertNotEqual(out.lower(), name.lower() + ".gif")
                self.assertTrue(out.startswith("_"))
                self.assertTrue(out.endswith(".gif"))

    def test_non_reserved_lookalikes(self):
        # COM10 / LPT0 are not reserved device names.
        self.assertEqual(sanitize_output_name("COM10.gif"), "COM10.gif")
        self.assertEqual(sanitize_output_name("LPT0.gif"), "LPT0.gif")

    def test_unicode_preserved(self):
        self.assertEqual(sanitize_output_name("café_日本.gif"), "café_日本.gif")

    def test_control_characters_replaced(self):
        self.assertEqual(sanitize_output_name("a\tb\nc.gif"), "a_b_c.gif")

    def test_null_byte_rejected(self):
        with self.assertRaises(errors.EngineError):
            sanitize_output_name("a\x00b.gif")


class TestDefaultOutputName(unittest.TestCase):
    def test_format(self):
        self.assertEqual(
            default_output_name("product-demo", 60000, 65000),
            "product-demo_00-01-00.000_to_00-01-05.000.gif",
        )

    def test_stem_sanitized(self):
        out = default_output_name("my:vid/eo", 0, 1000)
        self.assertTrue(out.startswith("my_vid_eo_"))
        self.assertTrue(out.endswith("_00-00-00.000_to_00-00-01.000.gif"))

    def test_length_cap_preserves_timestamp_suffix(self):
        out = default_output_name("x" * 500, 60000, 65000)
        self.assertLessEqual(len(out), MAX_FILENAME_LENGTH)
        self.assertTrue(out.endswith("_00-01-00.000_to_00-01-05.000.gif"))

    def test_deterministic(self):
        a = default_output_name("clip", 1000, 2000)
        b = default_output_name("clip", 1000, 2000)
        self.assertEqual(a, b)


class TestSanitizeStem(unittest.TestCase):
    def test_trailing_dots_and_spaces_stripped(self):
        self.assertEqual(sanitize_stem("name...  "), "name")

    def test_empty_becomes_clip(self):
        self.assertEqual(sanitize_stem("   "), "clip")
        self.assertEqual(sanitize_stem("///"), "clip")


class TestUniqueName(unittest.TestCase):
    def test_no_collision_returns_same(self):
        self.assertEqual(unique_name("a.gif", lambda n: False), "a.gif")

    def test_appends_counter(self):
        taken = {"a.gif", "a-1.gif"}
        self.assertEqual(unique_name("a.gif", lambda n: n in taken), "a-2.gif")


if __name__ == "__main__":
    unittest.main()
