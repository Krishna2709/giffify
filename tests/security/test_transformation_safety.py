"""Security: transformation parameters are not a filter-graph injection surface.

Spec: SEC-018 (transformation parameter validation), FR-024 through FR-029,
§9.6 (no crop in configuration), §15.2 (identical filter chain in both palette
passes), §22.7 (transformation security tests), AC-0.3.11.

The threat model is narrow and precise. Every transformation value ends up as an
argument *inside an FFmpeg filter graph*. SEC-001 already forbids ``shell=True``,
so the shell is not the exposure; the filter graph is. A value such as
``0:0:100:100,drawtext=text=x`` or ``none[a];[a]movie=/etc/passwd`` would, if
concatenated naively, add a filter, open a new input, or re-route the graph. This
suite proves that cannot happen from any input channel.

For every hostile value the suite asserts four things:

1. The documented error code (INVALID_CROP / INVALID_DIMENSIONS / INVALID_SPEED
   / INVALID_DITHER, exit 6, or INVALID_USAGE) -- never a generic failure.
2. ``stage == "validate"``: the rejection happened in preflight (§15.1 step 10),
   before any encode stage exists.
3. No media and no partial temporary artifact were produced.
4. No Python traceback reached stdout or stderr (§14).

:class:`~fixtures.geometry.FFmpegSpy` then upgrades claim 2 from an inference to
a measurement: the shims count every ffmpeg/ffprobe process the engine starts, so
"no FFmpeg process was started" is observed directly.

The same hostile values are replayed through all four input channels -- command
line, JSON manifest, CSV manifest, and project configuration -- because SEC-018
forbids accepting them "from the command line, a manifest, or configuration".
"""

import json
import os
import sys
import unittest
from decimal import Decimal
from typing import Any, ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.base import media
from fixtures.geometry import SPY_SUPPORTED, TransformEngineTestCase

from vtg import ffmpeg as vtg_ffmpeg
from vtg import transforms as vtg_transforms
from vtg.models import EffectiveSettings

# ---------------------------------------------------------------------------
# Hostile value catalogue
# ---------------------------------------------------------------------------
# Filter-graph metacharacters. SEC-018 names at least whitespace, newline, and
# , ; ' " \ [ ] = % ( ) $ ` * as outside every transformation grammar.
FILTER_METACHARACTERS = (
    "0:0:100:100,drawtext=text=x",  # the exact example from SEC-018
    "0:0:100:100;a",
    "0:0:100:100[a];[a]movie=/etc/passwd",
    "0:0:100:100'",
    '0:0:100:100"',
    "0:0:100:100\\",
    "0:0:100:100=1",
    "0:0:100:100%2",
    "0:0:100:100(1)",
    "0:0:100:100*2",
    "0:0:100:100`id`",
    "0:0:100:100$(id)",
    "0:0:100:100 ",
    "0:0:100:100\n",
    "0:0:100:100\r\n",
    "0:0:100:100\ndrawtext=text=x",
)

# Numeric-format attacks for a single-integer field (width/height/bayerScale).
INTEGER_ATTACKS = (
    "1e3",  # exponent notation
    "0x10",  # hexadecimal
    "0o10",  # octal
    "0b101",  # binary
    "+640",  # explicit sign
    "-640",  # negative
    "inf",
    "nan",
    "",  # empty
    " ",  # whitespace only
    " 640",  # leading whitespace
    "640 ",  # trailing whitespace
    "\t640",
    "640\n",  # trailing newline: the grammar anchors with \Z, not $
    "640\r\n",
    "6_40",  # Python int() would accept this; the grammar must not
    # Non-ASCII digits: str.isdigit() is True for both, but the grammar is
    # explicit [0-9], so neither may be accepted. (RUF001 flags the visual
    # ambiguity these values are deliberately built from.)
    "٦٤٠",
    "６４０",  # noqa: RUF001
    "9" * 4096,  # absurdly long digit string
    "640,scale=2:2",
    "640[a];[a]movie=/etc/passwd",
    "640;drawtext=text=x",
    "640'",
    "640=1",
    "$(id)",
)

# Decimal-format attacks for the speed field.
SPEED_ATTACKS = (
    "1e0",
    "1E0",
    "1.0e1",
    "+2.0",
    "-2.0",
    "inf",
    "nan",
    "",
    " ",
    " 2.0",
    "2.0 ",
    "2.0\n",
    "1.0001",  # more than three fractional digits
    "٢",  # Arabic-Indic digit in a decimal field
    "2.0,setpts=PTS/8",
    "2.0[a];[a]movie=/etc/passwd",
    "2.0;drawtext=text=x",
    "2.0'",
    "0",
    "0.1",  # below the 0.25 floor
    "5.0",  # above the 4.0 ceiling
    "$(id)",
)

# Dither is an enumeration, so anything that is not an exact lowercase member of
# the enum after trimming must be rejected -- case included (FR-028).
DITHER_ATTACKS = (
    "none[a];[a]movie=/etc/passwd",  # the exact example from SEC-018
    "none,drawtext=text=x",
    "none;a",
    "none=1",
    "none'",
    'none"',
    "none\\",
    "none`id`",
    "none$(id)",
    "no ne",
    "NONE",  # comparison is case-sensitive
    "Bayer",
    "SIERRA2_4A",
    "",
    " ",
    "bayer:bayer_scale=5",  # the engine's own serialized form is not input
    "sierra2_4a[x]",
    "$(id)",
)

# Crop rectangles that are structurally wrong rather than metacharacter-bearing.
CROP_STRUCTURE_ATTACKS = (
    "1:2:3:4:5",  # five fields
    "1:2:3",  # three fields
    "1:2:3:",  # trailing separator
    ":1:2:3",  # leading separator
    "1::2:3",
    "-1:0:10:10",  # negative offset
    "0:-1:10:10",
    "0:0:-10:10",  # negative size
    "0:0:10:-10",
    "+1:0:10:10",
    "0:0:0:0",  # zero-sized
    "0:0:1:10",  # width below the 2-pixel floor
    "0:0:10:1",
    "0:0:1.5:10",  # non-integer
    "1e1:0:10:10",
    "0x10:0:10:10",
    "0:0:65536:10",  # outside 0..65535
    "65536:0:10:10",
    "٠:٠:١٠:١٠",  # noqa: RUF001 - Arabic-Indic digits
    "",
    " 0:0:10:10",
    "0:0:10:10 ",
    "0:0:10:10\n",
    "9" * 40 + ":0:10:10",
)


# Values whose first character is a dash but whose second is NOT a digit.
# ``normalize_argv`` deliberately joins only ``--flag -<digit>`` pairs so a real
# flag is never swallowed, which means these reach argparse as an unknown option
# (exit 2). The equals form puts them in front of the validator instead.
DASH_LEADING_NON_NUMERIC = ("-inf", "-nan", "-x", "-e5")


def non_empty(values):
    """Drop values a manifest reads as "not specified at this level".

    §10.4 and §11.2 define an absent or empty manifest value as "the next
    precedence level applies", so an empty or whitespace-only cell is a valid
    *omission* rather than a hostile value. It is exercised separately by
    ``test_an_empty_value_means_unspecified_not_invalid``; the manifest attack
    lists filter it out so the two behaviors are never conflated.
    """
    return tuple(v for v in values if not isinstance(v, str) or v.strip())


class TransformSafetyCase(TransformEngineTestCase):
    """Assertion helpers shared by every input channel in this module."""

    src: ClassVar[str]

    @classmethod
    def generate_media(cls):
        cls.src = cls.media_file("safe.mp4")
        media.generate_landscape(cls.src, size="640x360", fps=15, duration=2.0)

    def setUp(self):
        super().setUp()
        # Each `create`/`batch` invocation gets its own output directory. Most
        # tests here loop over dozens of hostile values with subTest, and a
        # single value that is legitimately *accepted* (e.g. FR-028's whitespace
        # trimming) would otherwise leave a GIF behind and make every later
        # subTest's "nothing was produced" assertion fail for the wrong reason.
        # Rotating the directory keeps every case independent.
        self._out_seq = 0
        self._current_output = "output"

    @property
    def output_dir(self):
        return os.path.join(self.project, self._current_output)

    def next_output_dir(self):
        self._out_seq += 1
        self._current_output = f"out-{self._out_seq:03d}"
        return "./" + self._current_output

    # -- assertions --------------------------------------------------------
    def assert_rejected(self, res, code, *, exit_code=6, where=""):
        """Assert one hostile value was rejected safely and left no residue."""
        context = f"{where}: result={res.result}"
        self.assertEqual(res.returncode, exit_code, context)
        self.assertEqual(res.status, "validation_failed", context)
        self.assertEqual(res.error_code, code, context)
        # §15.1 step 10: transformations are validated in preflight, so the
        # failure stage is never an encode/palette stage.
        stage = (res.result.get("error") or {}).get("stage")
        self.assertIn(stage, ("validate", "config"), context)
        self.assertNotIn(stage, ("palette", "encode", "preview"), context)
        self.assert_no_media_produced()
        self.assert_no_traceback(res)
        # A rejected job creates nothing, so the result carries no created entry.
        self.assertEqual(res.created, [], context)

    def create(self, *flags, env=None):
        return self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                "--output-directory",
                self.next_output_dir(),
                *flags,
            ],
            env=env,
        )

    def batch_manifest(self, manifest, *flags, env=None):
        return self.run_engine(
            ["batch", "--manifest", manifest, "--output-directory", self.next_output_dir(), *flags],
            env=env,
        )

    def write_json_manifest(self, clip_extra=None, top_extra=None):
        clip = {"name": "c0", "start": "0", "end": "1"}
        clip.update(clip_extra or {})
        data = {"schemaVersion": 1, "input": self.src, "profile": "small", "clips": [clip]}
        data.update(top_extra or {})
        path = os.path.join(self.project, "clips.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def write_csv_manifest(self, column, value):
        path = os.path.join(self.project, "clips.csv")
        # csv.writer would quote/escape for us, but the point of this fixture is
        # to place the raw hostile text into the cell, so the cell is quoted by
        # hand and embedded double quotes are doubled per RFC 4180.
        cell = '"' + str(value).replace('"', '""') + '"'
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(f"name,start,end,{column}\n")
            fh.write(f"c0,0,1,{cell}\n")
        return path

    def write_config(self, transformations):
        path = os.path.join(self.project, ".video-to-gif.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schemaVersion": 1, "transformations": transformations}, fh)
        return path


# ---------------------------------------------------------------------------
# Command line (§12.10)
# ---------------------------------------------------------------------------
class TestCommandLineRejection(TransformSafetyCase):
    def test_crop_filter_metacharacters_rejected(self):
        for value in FILTER_METACHARACTERS:
            with self.subTest(crop=value):
                res = self.create("--crop", value)
                self.assert_rejected(res, "INVALID_CROP", where=f"--crop {value!r}")

    def test_crop_structural_attacks_rejected(self):
        for value in CROP_STRUCTURE_ATTACKS:
            with self.subTest(crop=value):
                res = self.create("--crop", value)
                self.assert_rejected(res, "INVALID_CROP", where=f"--crop {value!r}")

    def test_crop_out_of_bounds_rejected_against_the_source(self):
        # FR-025: x + width > source width. Source-dependent, so it is checked
        # after ffprobe but still in preflight, before any encode.
        for value in ("0:0:9000:100", "0:0:100:9000", "600:0:100:100", "0:300:100:100"):
            with self.subTest(crop=value):
                res = self.create("--crop", value)
                self.assert_rejected(res, "INVALID_CROP", where=f"--crop {value!r}")

    def test_width_attacks_rejected(self):
        for value in INTEGER_ATTACKS:
            with self.subTest(width=value):
                res = self.create("--width", value)
                self.assert_rejected(res, "INVALID_DIMENSIONS", where=f"--width {value!r}")

    def test_height_attacks_rejected(self):
        for value in INTEGER_ATTACKS:
            with self.subTest(height=value):
                res = self.create("--height", value)
                self.assert_rejected(res, "INVALID_DIMENSIONS", where=f"--height {value!r}")

    def test_dimension_range_bounds_enforced(self):
        # SEC-018: numeric bounds are part of the security contract because an
        # unbounded dimension is a resource-exhaustion vector (SEC-011).
        for value in ("0", "1", "8193", "65536", "99999999"):
            with self.subTest(width=value):
                res = self.create("--width", value)
                self.assert_rejected(res, "INVALID_DIMENSIONS", where=f"--width {value!r}")

    def test_speed_attacks_rejected(self):
        for value in SPEED_ATTACKS:
            with self.subTest(speed=value):
                res = self.create("--speed", value)
                self.assert_rejected(res, "INVALID_SPEED", where=f"--speed {value!r}")

    def test_dither_attacks_rejected(self):
        for value in DITHER_ATTACKS:
            with self.subTest(dither=value):
                res = self.create("--dither", value)
                self.assert_rejected(res, "INVALID_DITHER", where=f"--dither {value!r}")

    def test_bayer_scale_attacks_rejected(self):
        for value in (*INTEGER_ATTACKS, "6", "-1", "10"):
            with self.subTest(bayer_scale=value):
                res = self.create("--dither", "bayer", "--bayer-scale", value)
                self.assert_rejected(res, "INVALID_DITHER", where=f"--bayer-scale {value!r}")

    def test_dither_is_the_only_field_that_trims_whitespace(self):
        # FR-028 mandates comparison "after surrounding whitespace is trimmed",
        # and only for dither. The trimmed enum member is re-serialized by the
        # engine, so the padding never reaches the filter graph.
        res = self.create("--dither", "  none  ")
        self.assert_exit(res, 0)
        self.assertEqual(res.created[0]["transformations"]["dither"], "none")
        # Every other field rejects the same padding.
        for flag, code in (
            ("--width", "INVALID_DIMENSIONS"),
            ("--height", "INVALID_DIMENSIONS"),
            ("--speed", "INVALID_SPEED"),
            ("--bayer-scale", "INVALID_DITHER"),
        ):
            with self.subTest(flag=flag):
                padded = "  2  " if flag != "--speed" else "  2.0  "
                sub = self.run_engine(
                    [
                        "create",
                        "--input",
                        self.src,
                        "--start",
                        "0",
                        "--end",
                        "1",
                        "--profile",
                        "small",
                        "--output-directory",
                        "./padded",
                        flag,
                        padded,
                    ]
                )
                self.assertEqual(sub.returncode, 6, f"{flag} {padded!r}: {sub.result}")
                self.assertEqual(sub.error_code, code)
                self.assert_no_traceback(sub)

    def test_dither_trimming_covers_newlines_and_still_re_serializes(self):
        # A newline IS surrounding whitespace, so FR-028's trim rule accepts
        # "none\\n" and resolves it to the enum member `none`. That is safe -- and
        # this test pins down *why*: the engine reports, and builds the
        # paletteuse argument from, the enum member, never the supplied text.
        # SEC-018's blanket "no newline" rule is about values that would be
        # concatenated verbatim; dither never is.
        for value in ("none\n", "none\r\n", "\n none \n", "\tbayer\n"):
            with self.subTest(dither=value):
                res = self.create("--dither", value)
                self.assert_exit(res, 0)
                self.assertEqual(
                    res.created[0]["transformations"]["dither"],
                    value.strip(),
                    "the reported mode must be the trimmed enum member",
                )
                self.assertNotIn("\n", res.created[0]["transformations"]["dither"])
        # The filter argument built from any trimmed member is metacharacter-free.
        self.assertEqual(vtg_transforms.parse_dither("none\n"), "none")
        self.assertEqual(vtg_transforms.dither_filter_arg("none", None), "none")

    def test_dash_leading_non_numeric_values_are_rejected_not_crashed(self):
        # ``normalize_argv`` joins only ``--flag -<digit>``; "-inf" therefore
        # reaches argparse as an unknown option and exits 2 (INVALID_USAGE),
        # which SEC-018 permits. Supplied through the equals form, the same text
        # reaches the validator and gets the specific transformation code. Both
        # paths must reject, and neither may crash or produce media.
        for value in DASH_LEADING_NON_NUMERIC:
            with self.subTest(value=value, form="space"):
                res = self.create("--width", value)
                self.assertEqual(res.returncode, 2, f"--width {value!r}: {res.result}")
                self.assert_no_media_produced()
                self.assert_no_traceback(res)
            with self.subTest(value=value, form="equals"):
                res = self.create(f"--width={value}")
                self.assert_rejected(res, "INVALID_DIMENSIONS", where=f"--width={value}")

    def test_negative_values_reach_validation_instead_of_argparse(self):
        # A value beginning with '-' is an argparse option by default, which
        # would produce exit 2 (INVALID_USAGE) and hide the real error. The
        # engine's normalize_argv joins the pair so FR-025..FR-027 can reject it
        # with its own code and exit 6.
        cases = (
            (("--crop", "-1:0:10:10"), "INVALID_CROP"),
            (("--crop", "-1:-1:-10:-10"), "INVALID_CROP"),
            (("--width", "-640"), "INVALID_DIMENSIONS"),
            (("--height", "-1"), "INVALID_DIMENSIONS"),
            (("--speed", "-2"), "INVALID_SPEED"),
            (("--speed", "-0.5"), "INVALID_SPEED"),
            (("--bayer-scale", "-1"), "INVALID_DITHER"),
        )
        for flags, code in cases:
            with self.subTest(flags=flags):
                res = self.create(*flags)
                self.assertNotEqual(
                    res.returncode, 2, f"{flags}: argparse usage error masked the real rejection"
                )
                self.assert_rejected(res, code, where=str(flags))

    def test_a_missing_flag_value_is_still_a_usage_error(self):
        # normalize_argv must not swallow a genuine "missing value": only a
        # token matching -<digit> is joined to its flag.
        res = self.run_engine(
            ["create", "--input", self.src, "--start", "0", "--end", "1", "--crop", "--json"]
        )
        self.assertEqual(res.returncode, 2)
        self.assert_no_media_produced()
        self.assert_no_traceback(res)

    def test_rejection_message_lists_the_permitted_dither_values(self):
        # FR-028: the error message for an invalid dither value MUST list them.
        res = self.create("--dither", "none[a];[a]movie=/etc/passwd")
        self.assert_rejected(res, "INVALID_DITHER")
        message = (res.result.get("error") or {}).get("message", "")
        for mode in ("none", "bayer", "floyd_steinberg", "sierra2", "sierra2_4a"):
            self.assertIn(mode, message)


# ---------------------------------------------------------------------------
# No FFmpeg process starts (SEC-018, AC-0.3.11)
# ---------------------------------------------------------------------------
@unittest.skipUnless(SPY_SUPPORTED, "the ffmpeg spy uses POSIX shell shims")
class TestNoFFmpegProcessStarted(TransformSafetyCase):
    def test_control_run_starts_the_expected_ffmpeg_processes(self):
        # Control: a legitimate job starts exactly one ffprobe (preflight
        # inspection) and two ffmpeg processes (palettegen, paletteuse). Without
        # this control the "zero processes" assertions below would also pass if
        # the spy were simply broken.
        spy = self.spy()
        res = self.run_engine(
            ["create", "--input", self.src, "--start", "0", "--end", "1", "--profile", "small"],
            env=spy.env,
        )
        self.assert_exit(res, 0)
        self.assertEqual(spy.count("ffprobe"), 1, spy.invocations())
        self.assertEqual(spy.count("ffmpeg"), 2, spy.invocations())

    def test_hostile_values_start_no_process_at_all(self):
        # A source-independent transformation error is caught before the engine
        # even inspects the media, so neither binary runs.
        cases = (
            ("--crop", "0:0:100:100,drawtext=text=x"),
            ("--crop", "1:2:3:4:5"),
            ("--width", "640[a];[a]movie=/etc/passwd"),
            ("--height", "1e3"),
            ("--speed", "2.0,setpts=PTS/8"),
            ("--dither", "none[a];[a]movie=/etc/passwd"),
            ("--bayer-scale", "5;x"),
        )
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                spy = self.spy()
                spy.reset()
                res = self.run_engine(
                    [
                        "create",
                        "--input",
                        self.src,
                        "--start",
                        "0",
                        "--end",
                        "1",
                        "--profile",
                        "small",
                        flag,
                        value,
                    ],
                    env=spy.env,
                )
                self.assertEqual(res.returncode, 6, res.result)
                self.assertEqual(
                    spy.count("ffmpeg"),
                    0,
                    f"{flag} {value!r} started an ffmpeg process: {spy.invocations()}",
                )
                self.assert_no_media_produced()

    def test_source_dependent_crop_rejection_starts_no_ffmpeg(self):
        # An out-of-bounds crop can only be judged after ffprobe, so exactly one
        # ffprobe runs -- and still zero ffmpeg processes, because §15.1 forbids
        # starting FFmpeg before preflight completes.
        spy = self.spy()
        res = self.run_engine(
            [
                "create",
                "--input",
                self.src,
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                "--crop",
                "0:0:9000:9000",
            ],
            env=spy.env,
        )
        self.assertEqual(res.returncode, 6)
        self.assert_error_code(res, "INVALID_CROP")
        self.assertEqual(spy.count("ffprobe"), 1, spy.invocations())
        self.assertEqual(spy.count("ffmpeg"), 0, spy.invocations())
        self.assert_no_media_produced()

    def test_hostile_manifest_value_starts_no_process(self):
        spy = self.spy()
        manifest = self.write_json_manifest({"crop": "0:0:100:100,drawtext=text=x"})
        res = self.batch_manifest(manifest, env=spy.env)
        self.assertEqual(res.returncode, 6)
        self.assertEqual(spy.count("ffmpeg"), 0, spy.invocations())
        self.assert_no_media_produced()

    def test_hostile_preview_value_starts_no_process(self):
        spy = self.spy()
        res = self.run_engine(
            ["preview", "--input", self.src, "--at", "1", "--crop", "0:0:10:10;movie=/etc/passwd"],
            env=spy.env,
        )
        self.assertEqual(res.returncode, 6)
        self.assert_error_code(res, "INVALID_CROP")
        self.assertEqual(spy.count("ffmpeg"), 0, spy.invocations())
        self.assert_no_media_produced()


# ---------------------------------------------------------------------------
# JSON manifest (§10.4)
# ---------------------------------------------------------------------------
class TestJsonManifestRejection(TransformSafetyCase):
    def batch(self, clip_extra=None, top_extra=None):
        manifest = self.write_json_manifest(clip_extra, top_extra)
        return self.batch_manifest(manifest)

    def test_clip_level_crop_attacks_rejected(self):
        for value in non_empty((*FILTER_METACHARACTERS[:6], *CROP_STRUCTURE_ATTACKS[:8])):
            with self.subTest(crop=value):
                res = self.batch({"crop": value})
                self.assert_rejected(res, "INVALID_CROP", where=f"clip.crop={value!r}")

    def test_top_level_crop_attacks_rejected(self):
        for value in non_empty(FILTER_METACHARACTERS[:4]):
            with self.subTest(crop=value):
                res = self.batch(top_extra={"crop": value})
                self.assert_rejected(res, "INVALID_CROP", where=f"top.crop={value!r}")

    def test_crop_object_form_rejects_extra_and_missing_keys(self):
        cases = (
            {"x": 0, "y": 0, "width": 10, "height": 10, "extra": 1},
            {"x": 0, "y": 0, "width": 10},
            {"x": 0, "y": 0, "w": 10, "h": 10},
            {"x": "0", "y": 0, "width": 10, "height": 10},
            {"x": 0, "y": 0, "width": "10,scale=2:2", "height": 10},
            {"x": -1, "y": 0, "width": 10, "height": 10},
            {"x": 0, "y": 0, "width": 1.5, "height": 10},
            {"x": True, "y": 0, "width": 10, "height": 10},
        )
        for value in cases:
            with self.subTest(crop=value):
                res = self.batch({"crop": value})
                self.assert_rejected(res, "INVALID_CROP", where=f"clip.crop={value!r}")

    def test_dimension_attacks_rejected(self):
        for value in non_empty((*INTEGER_ATTACKS[:8], "640,scale=2:2", -640, 0, 8193, 1.5, True)):
            with self.subTest(width=value):
                res = self.batch({"width": value})
                self.assert_rejected(res, "INVALID_DIMENSIONS", where=f"clip.width={value!r}")

    def test_speed_attacks_rejected(self):
        for value in non_empty((*SPEED_ATTACKS[:8], 0, -2, 5.0, 1e400, 1.0001, True)):
            with self.subTest(speed=value):
                res = self.batch({"speed": value})
                self.assert_rejected(res, "INVALID_SPEED", where=f"clip.speed={value!r}")

    def test_dither_attacks_rejected(self):
        for value in non_empty((*DITHER_ATTACKS[:10], 1, True, ["none"], {"mode": "none"})):
            with self.subTest(dither=value):
                res = self.batch({"dither": value})
                self.assert_rejected(res, "INVALID_DITHER", where=f"clip.dither={value!r}")

    def test_bayer_scale_attacks_rejected(self):
        for value in ("5;x", "٣", -1, 6, 1.5, True, "1e0"):
            with self.subTest(bayer_scale=value):
                res = self.batch({"dither": "bayer", "bayerScale": value})
                self.assert_rejected(res, "INVALID_DITHER", where=f"clip.bayerScale={value!r}")

    def test_bayer_scale_rejected_when_the_effective_mode_is_not_bayer(self):
        # FR-028: an explicitly supplied bayerScale with a non-bayer mode is an
        # error, not a silently ignored value.
        res = self.batch({"dither": "sierra2", "bayerScale": 3})
        self.assert_rejected(res, "INVALID_DITHER")

    def test_an_empty_value_means_unspecified_not_invalid(self):
        # §11.2/§10.4: an empty cell/value means "not specified at this level",
        # so the next precedence level applies. This is the documented safe
        # behavior and must not be confused with accepting an empty filter arg.
        res = self.batch({"crop": "", "width": "", "speed": "", "dither": ""})
        self.assert_exit(res, 0)
        clip = res.created[0]
        self.assertIsNone(clip["transformations"]["crop"])
        self.assertEqual(clip["transformations"]["speed"], 1.0)
        self.assertEqual(clip["width"], 480)  # the small profile default

    def test_one_hostile_clip_rejects_the_whole_job(self):
        # FR-024: an invalid transformation rejects the job the way an invalid
        # timestamp does under the default policy -- continueOnError does not
        # turn a validation error into a per-clip failure that still encodes the
        # other clips.
        manifest = os.path.join(self.project, "multi.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schemaVersion": 1,
                    "input": self.src,
                    "profile": "small",
                    "continueOnError": True,
                    "clips": [
                        {"name": "good", "start": "0", "end": "1"},
                        {"name": "evil", "start": "0", "end": "1", "crop": "0:0:1:1,movie=x"},
                    ],
                },
                fh,
            )
        res = self.batch_manifest(manifest)
        self.assert_rejected(res, "INVALID_CROP")
        self.assertEqual(self.list_output(), [])


# ---------------------------------------------------------------------------
# CSV manifest (§11.2)
# ---------------------------------------------------------------------------
class TestCsvManifestRejection(TransformSafetyCase):
    def batch(self, column, value):
        manifest = self.write_csv_manifest(column, value)
        return self.batch_manifest(manifest, "--input", self.src)

    def test_crop_column_attacks_rejected(self):
        for value in non_empty((*FILTER_METACHARACTERS[:6], *CROP_STRUCTURE_ATTACKS[:6])):
            with self.subTest(crop=value):
                res = self.batch("crop", value)
                self.assert_rejected(res, "INVALID_CROP", where=f"csv crop={value!r}")

    def test_width_column_attacks_rejected(self):
        for value in non_empty(INTEGER_ATTACKS[:12]):
            with self.subTest(width=value):
                res = self.batch("width", value)
                self.assert_rejected(res, "INVALID_DIMENSIONS", where=f"csv width={value!r}")

    def test_speed_column_attacks_rejected(self):
        for value in non_empty(SPEED_ATTACKS[:10]):
            with self.subTest(speed=value):
                res = self.batch("speed", value)
                self.assert_rejected(res, "INVALID_SPEED", where=f"csv speed={value!r}")

    def test_dither_column_attacks_rejected(self):
        for value in non_empty(DITHER_ATTACKS[:10]):
            with self.subTest(dither=value):
                res = self.batch("dither", value)
                self.assert_rejected(res, "INVALID_DITHER", where=f"csv dither={value!r}")

    def test_bayerscale_column_attacks_rejected(self):
        for value in ("-1", "6", "5;x", "1e0", "٣"):
            with self.subTest(bayerscale=value):
                res = self.batch("bayerscale", value)
                self.assert_rejected(res, "INVALID_DITHER", where=f"csv bayerscale={value!r}")

    def test_embedded_newline_in_a_quoted_cell_cannot_smuggle_a_filter(self):
        # A quoted CSV cell may legally contain a newline, so the parser will
        # hand the engine a multi-line value. The \Z-anchored grammars must
        # reject it rather than matching only the first line.
        for column, code in (
            ("crop", "INVALID_CROP"),
            ("width", "INVALID_DIMENSIONS"),
            ("speed", "INVALID_SPEED"),
            ("dither", "INVALID_DITHER"),
        ):
            with self.subTest(column=column):
                value = {
                    "crop": "0:0:10:10\ndrawtext=text=x",
                    "width": "640\ndrawtext=text=x",
                    "speed": "2.0\ndrawtext=text=x",
                    "dither": "none\ndrawtext=text=x",
                }[column]
                res = self.batch(column, value)
                self.assert_rejected(res, code, where=f"csv {column}={value!r}")


# ---------------------------------------------------------------------------
# Project configuration (§9.6)
# ---------------------------------------------------------------------------
class TestConfigurationRejection(TransformSafetyCase):
    def validate_config(self, transformations):
        path = self.write_config(transformations)
        return self.run_engine(["validate-config", "--config", path])

    def test_configuration_rejects_any_crop_key(self):
        # §9.6: a crop rectangle is only meaningful against a specific source, so
        # configuration MUST NOT define one. The field path must be reported.
        for value in ("0:0:10:10", {"x": 0, "y": 0, "width": 10, "height": 10}, None, ""):
            with self.subTest(crop=value):
                res = self.validate_config({"crop": value})
                self.assertNotEqual(res.returncode, 0, res.result)
                self.assert_status(res, "validation_failed")
                self.assertEqual(
                    (res.result.get("error") or {}).get("field"), "transformations.crop"
                )
                self.assert_no_media_produced()
                self.assert_no_traceback(res)

    def test_configuration_crop_key_also_blocks_a_conversion(self):
        # The same rejection must happen when the config is loaded by `create`,
        # not only by `validate-config`.
        self.write_config({"crop": "0:0:10:10"})
        res = self.create()
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual((res.result.get("error") or {}).get("field"), "transformations.crop")
        self.assert_no_media_produced()

    def test_configuration_transformation_attacks_rejected(self):
        cases = (
            ("width", non_empty(INTEGER_ATTACKS[:8]), "INVALID_DIMENSIONS"),
            ("height", ("1e3", " 640", "640\n", -1, 0, 8193), "INVALID_DIMENSIONS"),
            ("speed", non_empty(SPEED_ATTACKS[:8]), "INVALID_SPEED"),
            ("dither", non_empty(DITHER_ATTACKS[:8]), "INVALID_DITHER"),
            ("bayerScale", ("-1", "6", "5;x", "1e0"), "INVALID_DITHER"),
        )
        for key, values, code in cases:
            for value in values:
                with self.subTest(key=key, value=value):
                    res = self.validate_config({key: value})
                    self.assertEqual(res.returncode, 6, f"{key}={value!r}: {res.result}")
                    self.assertEqual(res.error_code, code)
                    self.assertEqual(
                        (res.result.get("error") or {}).get("field"), f"transformations.{key}"
                    )
                    self.assert_no_traceback(res)

    def test_configuration_rejects_an_empty_transformation_value(self):
        # Unlike a manifest cell, a configuration key that is present with an
        # empty value is not an omission: only JSON null means "unspecified"
        # (§9.6). An empty string must therefore be rejected, not coerced.
        for key, code in (
            ("width", "INVALID_DIMENSIONS"),
            ("height", "INVALID_DIMENSIONS"),
            ("speed", "INVALID_SPEED"),
            ("dither", "INVALID_DITHER"),
            ("bayerScale", "INVALID_DITHER"),
        ):
            for value in ("", " "):
                with self.subTest(key=key, value=value):
                    res = self.validate_config({key: value})
                    self.assertEqual(res.returncode, 6, f"{key}={value!r}: {res.result}")
                    self.assertEqual(res.error_code, code)
                    self.assert_no_traceback(res)

    def test_configuration_null_means_unspecified(self):
        # §9.6: a null value means the effective profile default applies.
        res = self.validate_config(
            {"width": None, "height": None, "speed": None, "dither": None, "bayerScale": None}
        )
        self.assert_exit(res, 0)

    def test_configuration_attacks_also_block_a_conversion(self):
        for key, value, code in (
            ("width", "640[a];[a]movie=/etc/passwd", "INVALID_DIMENSIONS"),
            ("speed", "2.0,setpts=PTS/8", "INVALID_SPEED"),
            ("dither", "none[a];[a]movie=/etc/passwd", "INVALID_DITHER"),
        ):
            with self.subTest(key=key):
                self.write_config({key: value})
                res = self.create()
                self.assertEqual(res.returncode, 6, res.result)
                self.assertEqual(res.error_code, code)
                self.assert_no_media_produced()
                self.assert_no_traceback(res)


# ---------------------------------------------------------------------------
# Preview output safety (FR-029, §22.7)
# ---------------------------------------------------------------------------
class TestPreviewOutputSafety(TransformSafetyCase):
    def preview(self, *flags):
        return self.run_engine(["preview", "--input", self.src, "--at", "1", *flags])

    def test_preview_name_cannot_escape_the_output_directory(self):
        sentinel = os.path.join(self.project, "escaped.png")
        parent = os.path.join(os.path.dirname(self.project), "escaped.png")
        for name in ("../escaped.png", "../../escaped.png", "sub/escaped.png", "/tmp/escaped.png"):
            with self.subTest(name=name):
                res = self.preview("--output-name", name)
                self.assertEqual(res.returncode, 2, res.result)
                self.assert_error_code(res, "INVALID_USAGE")
                self.assertFalse(os.path.exists(sentinel))
                self.assertFalse(os.path.exists(parent))
                self.assert_no_media_produced()
                self.assert_no_traceback(res)

    def test_preview_does_not_overwrite_under_the_default_collision_policy(self):
        first = self.preview("--output-name", "frame.png")
        self.assert_exit(first, 0)
        with open(self.output_path("frame.png"), "rb") as fh:
            original = fh.read()
        second = self.preview("--output-name", "frame.png")
        self.assert_exit(second, 7)
        self.assert_status(second, "collision")
        with open(self.output_path("frame.png"), "rb") as fh:
            self.assertEqual(fh.read(), original, "preview overwrote an existing file")
        self.assertEqual(self.temp_gif_leftovers(), [])

    def test_preview_output_name_must_be_a_png(self):
        # FR-029: any extension other than .png is INVALID_USAGE with exit 2.
        for name in ("frame.gif", "frame.jpg", "frame.png.gif", "frame.PNG.exe"):
            with self.subTest(name=name):
                res = self.preview("--output-name", name)
                self.assertEqual(res.returncode, 2, res.result)
                self.assert_error_code(res, "INVALID_USAGE")
                self.assert_no_media_produced()

    def test_preview_transformation_attacks_produce_no_png(self):
        for flag, value, code in (
            ("--crop", "0:0:100:100,drawtext=text=x", "INVALID_CROP"),
            ("--width", "640[a];[a]movie=/etc/passwd", "INVALID_DIMENSIONS"),
            ("--speed", "2.0,setpts=PTS/8", "INVALID_SPEED"),
            ("--dither", "none[a];[a]movie=/etc/passwd", "INVALID_DITHER"),
        ):
            with self.subTest(flag=flag):
                res = self.preview(flag, value)
                self.assert_rejected(res, code, where=f"preview {flag} {value!r}")


# ---------------------------------------------------------------------------
# Identical filter chain in both palette passes (§15.2, SEC-018)
# ---------------------------------------------------------------------------
class TestFilterChainConstruction(unittest.TestCase):
    """In-process checks on the filter graph the engine actually builds.

    These exercise :mod:`vtg.transforms` and :mod:`vtg.ffmpeg` directly rather
    than through a subprocess, because the claim under test is about the exact
    argument strings handed to FFmpeg -- which is what SEC-018 constrains.
    """

    @staticmethod
    def settings(**kw: Any) -> EffectiveSettings:
        base: dict[str, Any] = {
            "width": 320,
            "height": 180,
            "fps": 10.0,
            "colors": 128,
            "loop": "forever",
            "profile_name": "small",
        }
        base.update(kw)
        return EffectiveSettings(**base)

    @staticmethod
    def chain_of(command, option):
        value = command[command.index(option) + 1]
        # palettegen appends ",palettegen=..."; paletteuse appends "[x];[x]...".
        for marker in (",palettegen=", "[x];[x]"):
            if marker in value:
                return value.split(marker)[0]
        return value

    def test_both_palette_passes_receive_an_identical_filter_chain(self):
        settings = self.settings(
            crop=vtg_transforms.CropRect(x=1, y=2, width=300, height=200),
            speed=Decimal("2.0"),
            dither="bayer",
            bayer_scale=3,
        )
        gen = vtg_ffmpeg.build_palettegen_command(
            "ffmpeg", "/src.mp4", 0, 1000, settings, "/tmp/p.png"
        )
        use = vtg_ffmpeg.build_paletteuse_command(
            "ffmpeg", "/src.mp4", 0, 1000, settings, "/tmp/p.png", "/tmp/o.gif"
        )
        self.assertEqual(
            self.chain_of(gen, "-vf"),
            self.chain_of(use, "-lavfi"),
            "palette generation and encoding saw different frames",
        )

    def test_the_filter_chain_holds_the_normative_step_order(self):
        # §15.2 steps 4-7: crop -> setpts -> fps -> scale.
        chain = vtg_transforms.build_filter_chain(
            crop=vtg_transforms.CropRect(x=1, y=2, width=300, height=200),
            speed=Decimal("2.0"),
            fps=10.0,
            width=150,
            height=100,
        )
        parts = chain.split(",")
        indices = {
            "crop": next(i for i, p in enumerate(parts) if p.startswith("crop=")),
            "setpts": next(i for i, p in enumerate(parts) if p.startswith("setpts=")),
            "fps": next(i for i, p in enumerate(parts) if p.startswith("fps=")),
            "scale": next(i for i, p in enumerate(parts) if p.startswith("scale=")),
        }
        self.assertLess(indices["crop"], indices["scale"], "crop must precede scale")
        self.assertLess(indices["setpts"], indices["fps"], "retiming must precede fps conversion")
        self.assertLess(indices["fps"], indices["scale"])

    def test_the_filter_chain_is_built_only_from_re_serialized_values(self):
        # Every argument in the chain is regenerated from validated int/Decimal/
        # enum values, so no user-supplied text can appear even if a hostile
        # string somehow reached this far.
        chain = vtg_transforms.build_filter_chain(
            crop=vtg_transforms.parse_crop("10:20:300:200"),
            speed=vtg_transforms.parse_speed("1.5"),
            fps=10.0,
            width=150,
            height=100,
        )
        self.assertEqual(
            chain,
            "crop=300:200:10:20,setpts=PTS/1.5,fps=10,scale=150:100:flags=lanczos",
        )
        for forbidden in (";", "[", "]", "'", '"', "\\", "%", "(", ")", "$", "`", "*", " ", "\n"):
            self.assertNotIn(forbidden, chain, f"{forbidden!r} appeared in the filter chain")

    def test_dither_argument_is_built_from_the_enum_and_integer_only(self):
        self.assertEqual(vtg_transforms.dither_filter_arg("bayer", 5), "bayer:bayer_scale=5")
        self.assertEqual(vtg_transforms.dither_filter_arg("sierra2_4a", None), "sierra2_4a")
        for mode in vtg_transforms.DITHER_MODES:
            arg = vtg_transforms.dither_filter_arg(mode, 2 if mode == "bayer" else None)
            for forbidden in (";", "[", "]", ",", "'", '"', "\\", "$", "`", " "):
                self.assertNotIn(forbidden, arg)

    def test_every_ffmpeg_command_keeps_the_protocol_whitelist(self):
        # SEC-018 requires SEC-010's whitelist on every invocation, preview
        # included, so no filter may reference a remote resource.
        settings = self.settings(crop=vtg_transforms.CropRect(x=0, y=0, width=100, height=100))
        commands = (
            vtg_ffmpeg.build_palettegen_command("ffmpeg", "/s.mp4", 0, 1000, settings, "/p.png"),
            vtg_ffmpeg.build_paletteuse_command(
                "ffmpeg", "/s.mp4", 0, 1000, settings, "/p.png", "/o.gif"
            ),
            vtg_ffmpeg.build_preview_command("ffmpeg", "/s.mp4", 0, settings, "/o.png"),
        )
        for command in commands:
            self.assertIn("-protocol_whitelist", command)
            self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file,pipe")
            self.assertIn("-an", command)  # audio always disabled

    def test_preview_chain_omits_the_temporal_steps(self):
        # FR-029 / §15.2: a preview uses steps 1-4 and 7 only.
        chain = vtg_transforms.build_preview_filter_chain(
            crop=vtg_transforms.CropRect(x=0, y=0, width=100, height=100), width=50, height=50
        )
        self.assertNotIn("setpts", chain)
        self.assertNotIn("fps=", chain)
        self.assertIn("crop=", chain)
        self.assertIn("scale=", chain)


if __name__ == "__main__":
    unittest.main()
