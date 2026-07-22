"""JSON and CSV manifest parsing tests (spec sections 10, 11, 22.1)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import errors
from vtg.manifests import parse_csv_manifest, parse_json_manifest

JSON_OK = """
{
  "schemaVersion": 1,
  "input": "./videos/demo.mp4",
  "outputDirectory": "./output",
  "profile": "balanced",
  "loop": "forever",
  "continueOnError": true,
  "clips": [
    {"name": "opening", "start": "00:01:00", "end": "00:01:05"},
    {"name": "reaction", "start": "00:03:20", "duration": 7}
  ]
}
"""


class TestJSONManifest(unittest.TestCase):
    def test_valid(self):
        m = parse_json_manifest(JSON_OK)
        self.assertEqual(m.input, "./videos/demo.mp4")
        self.assertEqual(len(m.clips), 2)
        self.assertEqual(m.clips[0].start_ms, 60000)
        self.assertEqual(m.clips[0].end_ms, 65000)
        self.assertEqual(m.clips[1].start_ms, 200000)
        self.assertEqual(m.clips[1].end_ms, 207000)  # 200s + 7s
        self.assertEqual(m.profile, "balanced")

    def test_missing_required_fields(self):
        for payload, field in (
            ('{"input":"a.mp4","clips":[{"start":"1","end":"2"}]}', "schemaVersion"),
            ('{"schemaVersion":1,"clips":[{"start":"1","end":"2"}]}', "input"),
            ('{"schemaVersion":1,"input":"a.mp4"}', "clips"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(errors.EngineError) as ctx:
                    parse_json_manifest(payload)
                self.assertEqual(ctx.exception.code, errors.INVALID_MANIFEST)
                self.assertEqual(ctx.exception.field, field)

    def test_empty_clips_rejected(self):
        with self.assertRaises(errors.EngineError):
            parse_json_manifest('{"schemaVersion":1,"input":"a.mp4","clips":[]}')

    def test_clip_missing_start(self):
        with self.assertRaises(errors.EngineError) as ctx:
            parse_json_manifest('{"schemaVersion":1,"input":"a.mp4","clips":[{"end":"5"}]}')
        self.assertEqual(ctx.exception.clip_index, 0)

    def test_neither_end_nor_duration(self):
        with self.assertRaises(errors.EngineError):
            parse_json_manifest('{"schemaVersion":1,"input":"a.mp4","clips":[{"start":"1"}]}')

    def test_both_end_and_duration_conflict(self):
        # Conflicting end vs duration -> error.
        with self.assertRaises(errors.EngineError) as ctx:
            parse_json_manifest(
                '{"schemaVersion":1,"input":"a.mp4","clips":[{"start":"10","end":"20","duration":"5"}]}'
            )
        self.assertEqual(ctx.exception.code, errors.INVALID_MANIFEST)

    def test_both_end_and_duration_consistent_allowed(self):
        m = parse_json_manifest(
            '{"schemaVersion":1,"input":"a.mp4","clips":[{"start":"10","end":"15","duration":"5"}]}'
        )
        self.assertEqual(m.clips[0].end_ms, 15000)

    def test_duration_number_and_string(self):
        m = parse_json_manifest(
            '{"schemaVersion":1,"input":"a.mp4","clips":['
            '{"start":0,"duration":7},'
            '{"start":0,"duration":"00:00:07"}]}'
        )
        self.assertEqual(m.clips[0].end_ms, 7000)
        self.assertEqual(m.clips[1].end_ms, 7000)

    def test_clip_level_override_captured(self):
        m = parse_json_manifest(
            '{"schemaVersion":1,"input":"a.mp4","profile":"balanced","clips":['
            '{"start":"0","end":"1","profile":"high","fps":20}]}'
        )
        self.assertEqual(m.profile, "balanced")
        self.assertEqual(m.clips[0].profile, "high")
        self.assertEqual(m.clips[0].fps, 20)

    def test_unknown_fields_warn(self):
        m = parse_json_manifest(
            '{"schemaVersion":1,"input":"a.mp4","mystery":1,"clips":[{"start":"0","end":"1","weird":2}]}'
        )
        self.assertTrue(any("mystery" in w for w in m.warnings))
        self.assertTrue(any("weird" in w for w in m.warnings))

    def test_colors_over_256_rejected(self):
        with self.assertRaises(errors.EngineError):
            parse_json_manifest(
                '{"schemaVersion":1,"input":"a.mp4","clips":[{"start":"0","end":"1","colors":512}]}'
            )

    def test_malformed_json(self):
        with self.assertRaises(errors.EngineError) as ctx:
            parse_json_manifest("{not json")
        self.assertEqual(ctx.exception.code, errors.INVALID_MANIFEST)

    def test_semicolon_in_name_is_literal_data(self):
        # SEC-009: manifest values are pure data, never interpreted.
        m = parse_json_manifest(
            '{"schemaVersion":1,"input":"a.mp4","clips":'
            '[{"name":"a; rm -rf ~","start":"0","end":"1"}]}'
        )
        self.assertEqual(m.clips[0].name, "a; rm -rf ~")

    def test_collision_policy_engine_values_accepted(self):
        for policy in ("fail", "overwrite", "unique", "skip"):
            with self.subTest(policy=policy):
                m = parse_json_manifest(
                    '{"schemaVersion":1,"input":"a.mp4","collisionPolicy":"'
                    + policy
                    + '","clips":[{"start":"0","end":"1"}]}'
                )
                self.assertEqual(m.collision_policy, policy)

    def test_collision_policy_ask_rejected(self):
        # M1: "ask" is a skill-layer/config value only. A manifest drives the
        # non-interactive engine directly, so it must be rejected with a
        # structured validation error naming the field (spec 9.4 / 10 / SEC-009).
        with self.assertRaises(errors.EngineError) as ctx:
            parse_json_manifest(
                '{"schemaVersion":1,"input":"a.mp4","collisionPolicy":"ask",'
                '"clips":[{"start":"0","end":"1"}]}'
            )
        self.assertEqual(ctx.exception.code, errors.INVALID_MANIFEST)
        self.assertEqual(ctx.exception.field, "collisionPolicy")
        self.assertNotIn("ask", ctx.exception.message.split("one of")[-1])


class TestCSVManifest(unittest.TestCase):
    def test_valid(self):
        csv = (
            "name,start,end,duration,profile\n"
            "opening,00:01:00,00:01:05,,balanced\n"
            "reaction,00:03:20,,7,high\n"
            "ending,00:14:30,00:14:35,,small\n"
        )
        m = parse_csv_manifest(csv)
        self.assertEqual(len(m.clips), 3)
        self.assertEqual(m.clips[0].start_ms, 60000)
        self.assertEqual(m.clips[1].end_ms, 207000)
        self.assertEqual(m.clips[2].profile, "small")

    def test_case_insensitive_trimmed_headers(self):
        csv = " Start , END ,  Name \n00:00:01,00:00:02,x\n"
        m = parse_csv_manifest(csv)
        self.assertEqual(len(m.clips), 1)
        self.assertEqual(m.clips[0].name, "x")
        self.assertEqual(m.clips[0].start_ms, 1000)

    def test_empty_rows_ignored(self):
        csv = "start,end\n00:00:01,00:00:02\n\n , \n00:00:03,00:00:04\n"
        m = parse_csv_manifest(csv)
        self.assertEqual(len(m.clips), 2)

    def test_unknown_columns_warn(self):
        csv = "start,end,bogus\n1,2,zzz\n"
        m = parse_csv_manifest(csv)
        self.assertTrue(any("bogus" in w for w in m.warnings))

    def test_missing_start_column(self):
        with self.assertRaises(errors.EngineError) as ctx:
            parse_csv_manifest("end,name\n2,x\n")
        self.assertEqual(ctx.exception.field, "start")

    def test_missing_end_and_duration_columns(self):
        with self.assertRaises(errors.EngineError):
            parse_csv_manifest("start,name\n1,x\n")

    def test_row_with_duration(self):
        m = parse_csv_manifest("start,duration\n00:00:10,5\n")
        self.assertEqual(m.clips[0].end_ms, 15000)

    def test_no_data_rows(self):
        with self.assertRaises(errors.EngineError):
            parse_csv_manifest("start,end\n")


class TestManifestCellWhitespace(unittest.TestCase):
    """Padding around a manifest cell is a formatting artifact, not a value.

    Version 0.2.0 parsed ``width`` with ``int(str(value).strip())``, so a
    spreadsheet export carrying ``" 480"`` worked. Every other CSV column
    (start/end/duration/profile/fps/colors/loop) still tolerates padding, so
    the 0.3.0 transformation columns must too, or one stray space fails a whole
    batch with INVALID_DIMENSIONS. The trim happens before the grammar runs and
    opens no injection surface: the strict FR-025..FR-028 grammars still reject
    every hostile form (SEC-018), which the second half of this class proves.
    """

    def csv_clip(self, column, cell):
        return parse_csv_manifest(f"start,end,{column}\n0,1,{cell}\n").clips[0]

    def test_csv_padded_width_accepted(self):
        for cell in (" 480", "480 ", "  480  ", "\t480\t"):
            with self.subTest(cell=cell):
                self.assertEqual(self.csv_clip("width", cell).width, 480)

    def test_csv_padded_height_accepted(self):
        self.assertEqual(self.csv_clip("height", " 360 ").height, 360)

    def test_csv_padded_speed_accepted(self):
        self.assertEqual(float(self.csv_clip("speed", " 2.0 ").speed), 2.0)

    def test_csv_padded_crop_accepted(self):
        crop = self.csv_clip("crop", " 0:0:100:100 ").crop
        self.assertEqual((crop.x, crop.y, crop.width, crop.height), (0, 0, 100, 100))

    def test_csv_padded_dither_accepted(self):
        self.assertEqual(self.csv_clip("dither", " sierra2_4a ").dither, "sierra2_4a")

    def test_csv_padded_bayer_scale_accepted(self):
        clip = parse_csv_manifest("start,end,dither,bayerscale\n0,1,bayer, 3 \n").clips[0]
        self.assertEqual((clip.dither, clip.bayer_scale), ("bayer", 3))

    def test_csv_padding_matches_the_legacy_columns(self):
        # The point of the fix: transformation columns behave like the columns
        # that have always tolerated padding.
        m = parse_csv_manifest(
            "start,end,profile,fps,colors,width,height\n 0 , 1 , small , 10 , 64 , 480 , 240 \n"
        )
        clip = m.clips[0]
        self.assertEqual(
            (clip.start_ms, clip.end_ms, clip.profile, clip.fps, clip.colors),
            (0, 1000, "small", 10, 64),
        )
        self.assertEqual((clip.width, clip.height), (480, 240))

    def test_csv_inner_whitespace_still_rejected(self):
        for column, cell in (
            ("width", "4 80"),
            ("height", "3 60"),
            ("crop", "0:0:100 :100"),
            ("crop", "0:0:100: 100"),
            ("speed", "2. 0"),
            ("dither", "sierra2 _4a"),
        ):
            with self.subTest(column=column, cell=cell), self.assertRaises(errors.EngineError):
                self.csv_clip(column, cell)

    def test_csv_inner_newline_still_rejected(self):
        # A quoted cell can carry a real newline; trimming must not reach it.
        for column, cell in (("width", '"48\n0"'), ("crop", '"0:0:100\n:100"')):
            with self.subTest(column=column), self.assertRaises(errors.EngineError):
                self.csv_clip(column, cell)

    def test_csv_trimming_does_not_admit_filter_syntax(self):
        for column, cell in (
            ("width", " 480,scale=1:1 "),
            ("crop", " 0:0:10:10[a];[a]drawtext=x "),
            ("dither", " bayer'; touch /tmp/pwned; ' "),
            ("speed", " 2.0;drawbox "),
        ):
            with self.subTest(column=column, cell=cell), self.assertRaises(errors.EngineError):
                self.csv_clip(column, f'"{cell}"')

    def test_json_padded_width_accepted_as_in_v0_2_0(self):
        # Executed against the released 0.2.0 engine: ``" 480"`` parsed to 480.
        raw = '{"schemaVersion":1,"input":"a.mp4","clips":[{"start":"0","end":"1","width":" 480"}]}'
        self.assertEqual(parse_json_manifest(raw).clips[0].width, 480)

    def test_json_stays_strict_for_fields_new_in_0_3_0(self):
        # No 0.2.0 leniency to preserve for height/crop/speed/dither, so JSON
        # keeps the strict grammar rather than loosening past what 0.2.0 did.
        for field, value in (
            ("height", " 360"),
            ("crop", " 0:0:10:10"),
            ("speed", " 2.0"),
        ):
            raw = (
                '{"schemaVersion":1,"input":"a.mp4",'
                f'"clips":[{{"start":"0","end":"1","{field}":"{value}"}}]}}'
            )
            with self.subTest(field=field), self.assertRaises(errors.EngineError):
                parse_json_manifest(raw)

    def test_json_inner_whitespace_in_width_still_rejected(self):
        raw = '{"schemaVersion":1,"input":"a.mp4","clips":[{"start":"0","end":"1","width":"4 80"}]}'
        with self.assertRaises(errors.EngineError) as ctx:
            parse_json_manifest(raw)
        self.assertEqual(ctx.exception.code, errors.INVALID_DIMENSIONS)


if __name__ == "__main__":
    unittest.main()
