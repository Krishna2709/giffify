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


if __name__ == "__main__":
    unittest.main()
