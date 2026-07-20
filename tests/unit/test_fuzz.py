"""Seeded generative fuzz tests (spec section 22.5).

Randomized malformed input MUST always produce a structured validation error
(:class:`vtg.errors.EngineError`) and MUST NOT raise any other/uncaught
exception. All randomness uses fixed seeds for reproducibility.
"""

import os
import random
import string
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import errors, transforms
from vtg.manifests import parse_csv_manifest, parse_json_manifest
from vtg.timestamps import parse_duration, parse_timestamp
from vtg.transforms import (
    parse_bayer_scale,
    parse_crop,
    parse_dimension,
    parse_dither,
    parse_speed,
)

# The alphabet embeds a fullwidth colon (U+FF1A), an intentional Unicode
# lookalike for fuzzing the timestamp/manifest parsers against ambiguous
# separators; RUF001 is suppressed on the string for that reason.
_ALPHABET = string.printable + "：:.-/\\éあ\t\n\x00"  # noqa: RUF001


class TestTimestampFuzz(unittest.TestCase):
    def test_random_strings_never_crash(self):
        rng = random.Random(1337)
        for _ in range(5000):
            length = rng.randint(0, 24)
            candidate = "".join(rng.choice(_ALPHABET) for _ in range(length))
            try:
                result = parse_timestamp(candidate)
                self.assertIsInstance(result, int)
                self.assertGreaterEqual(result, 0)
            except errors.EngineError:
                pass  # structured error is acceptable
            except Exception as exc:
                self.fail(f"Uncaught {type(exc).__name__} for {candidate!r}: {exc}")

    def test_random_numeric_types(self):
        rng = random.Random(4242)
        for _ in range(2000):
            candidate = rng.choice(
                [
                    rng.uniform(-1e6, 1e6),
                    rng.randint(-(10**9), 10**9),
                    float("nan") if rng.random() < 0.02 else rng.random(),
                ]
            )
            try:
                parse_timestamp(candidate)
            except errors.EngineError:
                pass
            except Exception as exc:
                self.fail(f"Uncaught {type(exc).__name__} for {candidate!r}: {exc}")

    def test_duration_fuzz(self):
        rng = random.Random(99)
        for _ in range(3000):
            length = rng.randint(0, 16)
            candidate = "".join(rng.choice(_ALPHABET) for _ in range(length))
            try:
                result = parse_duration(candidate)
                self.assertIsInstance(result, int)
                self.assertGreater(result, 0)
            except errors.EngineError:
                pass
            except Exception as exc:
                self.fail(f"Uncaught {type(exc).__name__} for {candidate!r}: {exc}")


class TestManifestFuzz(unittest.TestCase):
    def test_random_json_never_crashes(self):
        rng = random.Random(2024)
        fragments = [
            "{",
            "}",
            "[",
            "]",
            '"',
            ":",
            ",",
            "schemaVersion",
            "input",
            "clips",
            "start",
            "end",
            "1",
            "true",
            "null",
            "a.mp4",
            " ",
            "\n",
        ]
        for _ in range(4000):
            n = rng.randint(0, 30)
            candidate = "".join(rng.choice(fragments) for _ in range(n))
            try:
                parse_json_manifest(candidate)
            except errors.EngineError:
                pass  # any structured validation error is acceptable
            except Exception as exc:
                self.fail(f"Uncaught {type(exc).__name__} for {candidate!r}: {exc}")

    def test_structured_json_random_values(self):
        rng = random.Random(7)
        for _ in range(2000):
            clip: dict[str, object] = {}
            if rng.random() < 0.8:
                clip["start"] = rng.choice(["1", 1, "aa", "", "-5", None, "1:2:3:4"])
            if rng.random() < 0.6:
                clip["end"] = rng.choice(["2", 2, "zz", None])
            if rng.random() < 0.4:
                clip["duration"] = rng.choice([5, "5", "-1", 0, "xx"])
            payload = f'{{"schemaVersion":1,"input":"a.mp4","clips":[{_to_json(clip)}]}}'
            try:
                parse_json_manifest(payload)
            except errors.EngineError:
                pass  # any structured validation error is acceptable
            except Exception as exc:
                self.fail(f"Uncaught {type(exc).__name__} for {payload!r}: {exc}")

    def test_random_csv_never_crashes(self):
        rng = random.Random(555)
        cols = ["start", "end", "duration", "name", "profile", "loop", "bogus"]
        for _ in range(3000):
            header = ",".join(rng.sample(cols, rng.randint(1, len(cols))))
            rows = [header]
            for _ in range(rng.randint(0, 4)):
                row = ",".join(
                    rng.choice(["1", "00:01", "", "xx", "-5", "5"])
                    for _ in range(rng.randint(0, 7))
                )
                rows.append(row)
            candidate = "\n".join(rows) + "\n"
            try:
                parse_csv_manifest(candidate)
            except errors.EngineError:
                pass  # any structured validation error is acceptable
            except Exception as exc:
                self.fail(f"Uncaught {type(exc).__name__} for {candidate!r}: {exc}")


class TestTransformFuzz(unittest.TestCase):
    """Generative tests for the v0.3.0 transformation parsers (section 22.7).

    Every parser MUST produce a structured validation error for malformed input,
    never an uncaught exception, and MUST never return a value outside its
    documented range. All seeds are fixed.
    """

    #: Every transformation parser, with the error code it must raise and a
    #: predicate that validates any successfully parsed value.
    def _parsers(self):
        return (
            (
                parse_crop,
                errors.INVALID_CROP,
                lambda r: (
                    all(0 <= v <= 65535 for v in (r.x, r.y, r.width, r.height))
                    and r.width >= 2
                    and r.height >= 2
                ),
            ),
            (
                lambda v: parse_dimension(v, field_path="width"),
                errors.INVALID_DIMENSIONS,
                lambda n: 2 <= n <= 8192,
            ),
            (
                parse_speed,
                errors.INVALID_SPEED,
                lambda d: Decimal("0.25") <= d <= Decimal("4.0"),
            ),
            (parse_dither, errors.INVALID_DITHER, lambda s: s in transforms.DITHER_MODES),
            (parse_bayer_scale, errors.INVALID_DITHER, lambda n: 0 <= n <= 5),
        )

    def test_random_strings_never_crash(self):
        rng = random.Random(90210)
        for parser, code, valid in self._parsers():
            for _ in range(3000):
                length = rng.randint(0, 20)
                candidate = "".join(rng.choice(_ALPHABET) for _ in range(length))
                try:
                    result = parser(candidate)
                except errors.EngineError as exc:
                    self.assertEqual(exc.code, code, msg=repr(candidate))
                    self.assertEqual(exc.exit_code, errors.EXIT_INVALID_TIMESTAMP)
                except Exception as exc:
                    self.fail(f"Uncaught {type(exc).__name__} for {candidate!r}: {exc}")
                else:
                    self.assertTrue(valid(result), msg=f"{candidate!r} -> {result!r}")

    def test_random_structured_values_never_crash(self):
        rng = random.Random(24680)
        pool = [
            None,
            True,
            False,
            0,
            -1,
            2**64,
            1.5,
            float("nan"),
            float("inf"),
            "",
            "0:0:10:10",
            [1, 2, 3],
            {"x": 0},
            {"x": 0, "y": 0, "width": 10, "height": 10},
            {"x": 0, "y": 0, "width": 10, "height": 10, "extra": 1},
            Decimal("2.0"),
        ]
        for parser, code, valid in self._parsers():
            for _ in range(1500):
                candidate = rng.choice(pool)
                try:
                    result = parser(candidate)
                except errors.EngineError as exc:
                    self.assertEqual(exc.code, code, msg=repr(candidate))
                except Exception as exc:
                    self.fail(f"Uncaught {type(exc).__name__} for {candidate!r}: {exc}")
                else:
                    self.assertTrue(valid(result), msg=f"{candidate!r} -> {result!r}")

    def test_random_crop_rectangles_round_trip(self):
        # A rectangle built from valid integers must always parse back equal.
        rng = random.Random(13579)
        for _ in range(2000):
            x = rng.randint(0, 65535)
            y = rng.randint(0, 65535)
            w = rng.randint(2, 65535)
            h = rng.randint(2, 65535)
            rect = parse_crop(f"{x}:{y}:{w}:{h}")
            self.assertEqual((rect.x, rect.y, rect.width, rect.height), (x, y, w, h))
            self.assertEqual(parse_crop(rect.to_public()), rect)

    def test_filter_chain_never_contains_metacharacters(self):
        # SEC-018: whatever survives validation is re-serialized by the engine,
        # so a built chain can only ever contain the filter grammar.
        rng = random.Random(11111)
        allowed = set(string.ascii_letters + "0123456789=,:./_-")
        for _ in range(2000):
            crop = transforms.CropRect(
                rng.randint(0, 4096),
                rng.randint(0, 4096),
                rng.randint(2, 4096),
                rng.randint(2, 4096),
            )
            speed = Decimal(str(rng.choice([0.25, 0.5, 1.0, 1.5, 2.0, 3.333, 4.0])))
            chain = transforms.build_filter_chain(
                crop=crop,
                speed=speed,
                fps=rng.choice([10.0, 12.5, 15.0, 20.0]),
                width=rng.randint(2, 8192),
                height=rng.randint(2, 8192),
            )
            self.assertTrue(set(chain) <= allowed, msg=chain)
            for forbidden in (";", "'", '"', "\\", "%", "(", ")", "$", "`", " ", "\n"):
                self.assertNotIn(forbidden, chain)


def _to_json(obj):
    import json

    return json.dumps(obj)


if __name__ == "__main__":
    unittest.main()
