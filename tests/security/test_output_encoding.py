"""Security: the structured result survives a hostile stream encoding (spec §13.5).

Spec: §13.1 (final result), §13.3 (progress output), §13.5 (output encoding),
§14 (exit codes), §6.1 (supported operating systems), §22.3 and §22.4.

THE DEFECT THIS LOCKS OUT. ``sys.stdout`` and ``sys.stderr`` default to the host
*locale* encoding. On Windows that is the console codepage -- cp1252 on the
GitHub runners, cp437 on many consoles -- not UTF-8. Writing the final JSON
document with ``ensure_ascii=False`` therefore asked the codepage to encode
whatever user-controlled text the document carried. Any character outside it
raised ``UnicodeEncodeError`` *inside the writer*, after the engine had already
decided the outcome. Python then exited with code 1 and an empty stdout.

That is a conformance failure, not a cosmetic one: §14 deliberately defines no
exit code 1, so the crash escaped the error contract entirely, and the agent
layer received no structured result to interpret. The user-visible case is not
exotic -- a source file named ``видео.mp4`` or ``デモ.mp4`` is enough, and §6.1
requires Windows Unicode filenames while AC-011 requires Unicode paths to work on
all three platforms.

WHY THESE TESTS RUN EVERYWHERE. The guarantee is enforced by the engine (it pins
both streams to UTF-8 and escapes the final document to ASCII), not by the host,
so it is testable without Windows: forcing ``PYTHONIOENCODING`` to a non-UTF-8
codepage reproduces the runner's encoding exactly on macOS and Linux. §22.3
requires this test to run on every platform in the matrix and never be skipped.

Each test asserts the full contract, not merely "it did not crash":

1. The exit code is the one §14 defines for the outcome -- never 1.
2. stdout carries exactly one parseable JSON document.
3. The document is pure ASCII (§13.5), so it survives any further consumer.
4. Escaping is lossless: ``json.loads`` returns the original Unicode string.
5. No traceback reached either stream.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import EngineTestCase, media

# ---------------------------------------------------------------------------
# Hostile stream encodings
# ---------------------------------------------------------------------------
#: Console codepages that cannot represent the text below. cp1252 is what the
#: Windows GitHub runners use; cp437 is the classic OEM console default. Both are
#: single-byte, so every non-Latin-1 character is unencodable in them.
CODEPAGES = ("cp1252", "cp437")

#: Unicode digits accepted by ``int()`` but rejected by the strict grammar of
#: FR-026. They are echoed back verbatim in the error message, which is what put
#: unencodable characters into the result document on the Windows CI legs.
#: Arabic-Indic "640" (U+0666 U+0664 U+0660).
ARABIC_INDIC_640 = "٦٤٠"
#: Fullwidth "640" (U+FF16 U+FF14 U+FF10). The lookalike digits are the point.
FULLWIDTH_640 = "６４０"  # noqa: RUF001

#: A filename mixing three scripts plus an emoji: none of these characters exist
#: in cp1252 or cp437. This is the real-user case behind the defect.
UNICODE_STEM = "视频-видео-\U0001f3ac"  # 视频-видео-🎬

#: Clip names for the manifest case, one per script family.
UNICODE_CLIP_NAMES = ("开场", "начало", "café")


def env_with_codepage(codepage: str) -> dict[str, str]:
    """A full environment that forces the engine's streams to ``codepage``.

    ``PYTHONIOENCODING`` is how CPython lets a caller override the stream
    encoding, so setting it reproduces a Windows console codepage faithfully on
    any host. The rest of the environment is inherited so ffmpeg stays on PATH.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = codepage
    return env


class ForcedEncodingTestCase(EngineTestCase):
    """Shared media and the five §13.5 assertions."""

    @classmethod
    def generate_media(cls):
        media.generate_landscape(cls.media_file("plain.mp4"), duration=2.0)
        media.generate_landscape(cls.media_file(f"{UNICODE_STEM}.mp4"), duration=2.0)

    # -- contract assertions ----------------------------------------------
    def assert_ascii_safe(self, res) -> None:
        """§13.5: the final document contains no character outside US-ASCII."""
        offenders = sorted({ch for ch in res.stdout if ord(ch) > 127})
        self.assertEqual(
            offenders,
            [],
            "final JSON document is not ASCII-safe; unescaped characters: "
            + ", ".join(f"{ch!r} (U+{ord(ch):04X})" for ch in offenders),
        )

    def assert_single_json_document(self, res) -> None:
        """§13.1: stdout carries exactly one JSON document, and it parses."""
        lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        self.assertEqual(
            len(lines),
            1,
            f"expected exactly one final JSON line on stdout, got {len(lines)}: {res.stdout!r}",
        )
        try:
            json.loads(lines[0])
        except json.JSONDecodeError as exc:  # pragma: no cover - failure path
            self.fail(f"final document is not valid JSON ({exc}): {lines[0]!r}")

    def assert_no_traceback(self, res) -> None:
        """§14: an unhandled exception must never reach either stream."""
        for name, text in (("stdout", res.stdout), ("stderr", res.stderr)):
            self.assertNotIn("Traceback (most recent call last)", text, f"traceback on {name}")
            self.assertNotIn("UnicodeEncodeError", text, f"UnicodeEncodeError on {name}")
            self.assertNotIn("UnicodeDecodeError", text, f"UnicodeDecodeError on {name}")

    def assert_contract_intact(self, res, exit_code: int) -> None:
        """Every §13.5 guarantee at once, for the given expected exit code."""
        self.assertNotEqual(
            res.returncode,
            1,
            "exit code 1 is undefined by §14 and signals an unhandled crash; "
            f"stderr={res.stderr!r}",
        )
        self.assert_exit(res, exit_code)
        self.assert_single_json_document(res)
        self.assert_ascii_safe(res)
        self.assert_no_traceback(res)

    def assert_stderr_events_parse(self, res) -> None:
        """§13.3: progress lines remain parseable JSON Lines under any codepage."""
        for line in res.stderr.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:  # pragma: no cover - failure path
                self.fail(f"progress line is not valid JSON ({exc}): {line!r}")


class TestUnicodeValueRejectionUnderCodepage(ForcedEncodingTestCase):
    """A rejected Unicode parameter value still produces its §14 exit code.

    This is the exact shape of the four red Windows CI cases on PR #7: the
    validation error echoes the offending value, so the result document carries
    characters the console codepage cannot encode.
    """

    def create(self, *flags, env=None):
        return self.run_engine(
            [
                "create",
                "--input",
                self.media_file("plain.mp4"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                *flags,
            ],
            env=env,
        )

    def test_arabic_indic_height_rejected_with_exit_6(self):
        """The repro command: exit 6 and a real document, not exit 1 and silence."""
        for codepage in CODEPAGES:
            with self.subTest(codepage=codepage):
                res = self.create("--height", ARABIC_INDIC_640, env=env_with_codepage(codepage))
                self.assert_contract_intact(res, 6)
                self.assert_error_code(res, "INVALID_DIMENSIONS")
                self.assert_status(res, "validation_failed")

    def test_fullwidth_digits_rejected_across_flags(self):
        """Every transformation flag from the failing CI matrix, both codepages."""
        cases = (
            ("--height", FULLWIDTH_640, "INVALID_DIMENSIONS"),
            ("--width", FULLWIDTH_640, "INVALID_DIMENSIONS"),
            ("--bayer-scale", FULLWIDTH_640, "INVALID_DITHER"),
            ("--crop", FULLWIDTH_640, "INVALID_CROP"),
            ("--speed", FULLWIDTH_640, "INVALID_SPEED"),
        )
        for codepage in CODEPAGES:
            for flag, value, code in cases:
                with self.subTest(codepage=codepage, flag=flag):
                    res = self.create(flag, value, env=env_with_codepage(codepage))
                    self.assert_contract_intact(res, 6)
                    self.assert_error_code(res, code)

    def test_escaped_message_round_trips_to_the_original_value(self):
        """§13.5: ASCII escaping is lossless -- the parsed message is unchanged.

        The document on the wire holds ``\\u0666``; after ``json.loads`` it is
        ``٦`` again. Escaping changes the transport, never the value.
        """
        res = self.create("--height", ARABIC_INDIC_640, env=env_with_codepage("cp1252"))
        self.assert_contract_intact(res, 6)
        message = res.result["error"]["message"]
        self.assertIn(
            ARABIC_INDIC_640,
            message,
            f"the offending value did not survive the round trip: {message!r}",
        )
        # The raw bytes on the wire really were escaped, not merely re-decoded.
        self.assertIn("\\u0666", res.stdout)
        self.assertNotIn(ARABIC_INDIC_640, res.stdout)


class TestUnicodeFilenameUnderCodepage(ForcedEncodingTestCase):
    """A non-ASCII *source filename* must not break the result (§6.1, AC-011).

    No test input is hostile here: a user whose video is named ``视频.mp4`` on a
    cp1252 console hit the identical crash on a fully successful conversion,
    because the echoed source path alone was unencodable.
    """

    def test_conversion_succeeds_and_the_path_round_trips(self):
        for codepage in CODEPAGES:
            with self.subTest(codepage=codepage):
                res = self.run_engine(
                    [
                        "create",
                        "--input",
                        self.media_file(f"{UNICODE_STEM}.mp4"),
                        "--start",
                        "0",
                        "--end",
                        "1",
                        "--profile",
                        "small",
                        # Relative to the per-test project root: each codepage
                        # gets its own directory so the second run is not a
                        # collision against the first (the engine never overwrites).
                        "--output-directory",
                        f"./out-{codepage}",
                    ],
                    env=env_with_codepage(codepage),
                )
                self.assert_contract_intact(res, 0)
                self.assert_status(res, "success")
                self.assert_stderr_events_parse(res)
                # The source path survived the escape/parse round trip intact.
                self.assertIn(UNICODE_STEM, res.result["source"]["path"])
                # And the GIF the document names really exists on disk.
                self.assertEqual(len(res.created), 1, res.result)
                created = res.created[0]["path"]
                path = created if os.path.isabs(created) else os.path.join(self.project, created)
                self.assert_valid_gif(path)

    def test_inspect_reports_a_unicode_path_without_crashing(self):
        """``inspect`` echoes the path straight back -- the narrowest repro."""
        res = self.run_engine(
            ["inspect", "--input", self.media_file(f"{UNICODE_STEM}.mp4")],
            env=env_with_codepage("cp1252"),
        )
        self.assert_contract_intact(res, 0)
        self.assertIn(UNICODE_STEM, res.result["source"]["path"])

    def test_missing_unicode_input_reports_its_own_error_code(self):
        """A failure path echoes the path too, and must stay inside §14."""
        res = self.run_engine(
            ["inspect", "--input", os.path.join(self.project, f"{UNICODE_STEM}-absent.mp4")],
            env=env_with_codepage("cp437"),
        )
        self.assert_contract_intact(res, 4)
        self.assert_error_code(res, "INPUT_NOT_FOUND")


class TestUnicodeManifestUnderCodepage(ForcedEncodingTestCase):
    """A UTF-8 manifest with non-ASCII clip names parses and reports correctly.

    This covers both directions of §13.5: the manifest is *read* with an explicit
    UTF-8 decode (never the locale default, which would mangle these names on a
    cp1252 host) and the names are then *written* back into the result document.
    """

    def write_manifest(self) -> str:
        clips = [
            {"name": name, "start": f"0.{index}", "duration": "0.4"}
            for index, name in enumerate(UNICODE_CLIP_NAMES)
        ]
        data = {
            "schemaVersion": 1,
            "input": self.media_file("plain.mp4"),
            "profile": "small",
            "clips": clips,
        }
        path = os.path.join(self.project, "clips.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return path

    def test_batch_reports_every_unicode_clip_name(self):
        manifest = self.write_manifest()
        res = self.run_engine(
            ["batch", "--manifest", manifest],
            env=env_with_codepage("cp1252"),
        )
        self.assert_contract_intact(res, 0)
        self.assert_status(res, "success")
        self.assert_stderr_events_parse(res)
        self.assertEqual(res.summary.get("created"), len(UNICODE_CLIP_NAMES), res.result)
        reported = {entry.get("name") for entry in res.created}
        self.assertEqual(reported, set(UNICODE_CLIP_NAMES), res.result)

    def test_dry_run_validation_preserves_names_under_cp437(self):
        manifest = self.write_manifest()
        res = self.run_engine(
            ["validate-manifest", "--manifest", manifest],
            env=env_with_codepage("cp437"),
        )
        self.assert_contract_intact(res, 0)
        # Parsing the wire document must give back the exact names written.
        parsed = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(parsed.get("status"), "success", parsed)
        self.assertEqual(
            [clip.get("name") for clip in parsed.get("clips") or []],
            list(UNICODE_CLIP_NAMES),
            parsed,
        )


class TestProgressStreamEncoding(ForcedEncodingTestCase):
    """§13.3 + §13.5: the JSON Lines progress stream is held to the same rule.

    Progress events carry clip names and output paths, so they are exposed to the
    identical failure. A progress write MUST NOT be able to take down a run that
    is otherwise succeeding.
    """

    def test_progress_events_are_ascii_and_parseable(self):
        res = self.run_engine(
            [
                "create",
                "--input",
                self.media_file(f"{UNICODE_STEM}.mp4"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
            ],
            env=env_with_codepage("cp1252"),
        )
        self.assert_contract_intact(res, 0)
        self.assert_stderr_events_parse(res)
        offenders = sorted({ch for ch in res.stderr if ord(ch) > 127})
        self.assertEqual(
            offenders,
            [],
            "progress stream is not ASCII-safe: "
            + ", ".join(f"{ch!r} (U+{ord(ch):04X})" for ch in offenders),
        )
        # Progress events were actually emitted -- otherwise this proves nothing.
        self.assertTrue(res.events, f"no progress events on stderr: {res.stderr!r}")
        completed = [e for e in res.events if e.get("event") == "clip_completed"]
        self.assertTrue(completed, res.events)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
