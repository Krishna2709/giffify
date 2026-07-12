"""Timestamp and duration parsing tests (spec FR-004, FR-005, section 22.1)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import errors
from vtg.timestamps import (
    format_filename_stamp,
    format_hhmmss,
    parse_duration,
    parse_timestamp,
    seconds_str,
)


class TestTimestampFormats(unittest.TestCase):
    def test_every_supported_format(self):
        cases = {
            "75": 75000,
            "75.5": 75500,
            "01:15": 75000,
            "01:15.500": 75500,
            "00:01:15": 75000,
            "00:01:15.500": 75500,
            "0": 0,
            "00:00": 0,
            "1:00:00": 3_600_000,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_timestamp(text), expected)

    def test_numeric_inputs(self):
        self.assertEqual(parse_timestamp(75), 75000)
        self.assertEqual(parse_timestamp(75.5), 75500)
        self.assertEqual(parse_timestamp(0), 0)

    def test_fractional_millisecond_precision(self):
        self.assertEqual(parse_timestamp("0.001"), 1)
        self.assertEqual(parse_timestamp("0.25"), 250)
        self.assertEqual(parse_timestamp("00:00:00.123"), 123)
        # Rounding to milliseconds.
        self.assertEqual(parse_timestamp("0.0004"), 0)
        self.assertEqual(parse_timestamp("0.0006"), 1)

    def test_minutes_unbounded_seconds_bounded(self):
        self.assertEqual(parse_timestamp("90:00"), 5_400_000)
        with self.assertRaises(errors.EngineError):
            parse_timestamp("1:60")  # seconds must be 0-59

    def test_negative_rejected(self):
        for bad in (-5, -0.5, "-5", "-1:00"):
            with self.subTest(bad=bad):
                with self.assertRaises(errors.EngineError) as ctx:
                    parse_timestamp(bad)
                self.assertEqual(ctx.exception.code, errors.INVALID_TIMESTAMP)
                self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_TIMESTAMP)

    def test_garbage_rejected(self):
        bad_values: tuple[object, ...] = (
            "",
            "abc",
            "1:2:3:4",
            "::",
            "1.2.3",
            "01:aa",
            None,
            True,
            [1],
            {},
        )
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(errors.EngineError):
                parse_timestamp(bad)

    def test_field_path_attached(self):
        with self.assertRaises(errors.EngineError) as ctx:
            parse_timestamp("nope", field_path="clips[3].start")
        self.assertEqual(ctx.exception.field, "clips[3].start")


class TestDuration(unittest.TestCase):
    def test_number_vs_string(self):
        self.assertEqual(parse_duration(5), 5000)
        self.assertEqual(parse_duration(5.5), 5500)
        self.assertEqual(parse_duration("5"), 5000)
        self.assertEqual(parse_duration("00:00:05"), 5000)
        self.assertEqual(parse_duration("01:30"), 90000)

    def test_must_be_strictly_positive(self):
        for bad in (0, "0", -1, "00:00:00"):
            with self.subTest(bad=bad):
                with self.assertRaises(errors.EngineError) as ctx:
                    parse_duration(bad)
                self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_TIMESTAMP)

    def test_invalid_duration_code(self):
        with self.assertRaises(errors.EngineError) as ctx:
            parse_duration("garbage")
        self.assertEqual(ctx.exception.code, errors.INVALID_DURATION)


class TestFormatters(unittest.TestCase):
    def test_format_hhmmss(self):
        self.assertEqual(format_hhmmss(75500), "00:01:15.500")
        self.assertEqual(format_hhmmss(0), "00:00:00.000")
        self.assertEqual(format_hhmmss(3_661_500), "01:01:01.500")

    def test_format_filename_stamp(self):
        self.assertEqual(format_filename_stamp(60000), "00-01-00.000")
        self.assertEqual(format_filename_stamp(65000), "00-01-05.000")
        self.assertEqual(format_filename_stamp(3_661_500), "01-01-01.500")

    def test_seconds_str(self):
        self.assertEqual(seconds_str(2000), "2.000")
        self.assertEqual(seconds_str(1500), "1.500")


if __name__ == "__main__":
    unittest.main()
