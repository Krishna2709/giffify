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

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import errors
from vtg.manifests import parse_csv_manifest, parse_json_manifest
from vtg.timestamps import parse_duration, parse_timestamp

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


def _to_json(obj):
    import json

    return json.dumps(obj)


if __name__ == "__main__":
    unittest.main()
