"""CLI-level transformation and preview tests (spec section 22.7).

Hermetic: ffprobe and the FFmpeg pipeline are mocked, so these exercise flag
parsing, the FR-024 precedence chain, config and manifest transformation
fields, the FR-030 result shape, and the whole FR-029 preview command without
touching real media.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest

from vtg import cli, errors
from vtg.ffmpeg import ConversionResult, PreviewResult


class _Recorder:
    """Captures every conversion/preview call instead of running FFmpeg."""

    def __init__(self):
        self.calls = []
        self.previews = []

    def convert(self, ffmpeg, source, **kw):
        self.calls.append(kw)
        with open(kw["dest_path"], "wb") as fh:
            fh.write(b"GIF89a fake")
        s = kw["settings"]
        return ConversionResult(
            path=kw["dest_path"], size_bytes=11, width=s.width, height=s.height, fps=s.fps
        )

    def preview(self, ffmpeg, source, **kw):
        self.previews.append(kw)
        with open(kw["dest_path"], "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n fake")
        s = kw["settings"]
        return PreviewResult(
            path=kw["dest_path"],
            size_bytes=16,
            width=s.width,
            height=s.height,
            at_ms=kw["at_ms"],
        )


class TransformCLIBase(unittest.TestCase):
    SOURCE_WIDTH = 1920
    SOURCE_HEIGHT = 1080

    def setUp(self):
        self.project = tempfile.mkdtemp()
        with open(os.path.join(self.project, ".video-to-gif.json"), "w") as fh:
            fh.write('{"schemaVersion": 1}')
        self.old_cwd = os.getcwd()
        os.chdir(self.project)
        self.source = vtgtest.make_source(
            path=os.path.join(self.project, "demo.mp4"),
            duration_ms=60000,
            width=self.SOURCE_WIDTH,
            height=self.SOURCE_HEIGHT,
            fps=30.0,
        )
        open(self.source.path, "wb").close()
        self.recorder = _Recorder()
        self._patchers = [
            mock.patch(
                "vtg.dependencies.require_ffmpeg_tools",
                return_value={"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"},
            ),
            mock.patch("vtg.cli.inspect_source", return_value=self.source),
            mock.patch("vtg.ffmpeg.convert_clip", side_effect=self.recorder.convert),
            mock.patch("vtg.ffmpeg.extract_preview", side_effect=self.recorder.preview),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        os.chdir(self.old_cwd)
        shutil.rmtree(self.project, ignore_errors=True)

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        payload = out.getvalue().strip()
        return code, (json.loads(payload) if payload else {})

    def write_config(self, data):
        with open(os.path.join(self.project, ".video-to-gif.json"), "w") as fh:
            json.dump(data, fh)

    def write_manifest(self, data, name="clips.json"):
        path = os.path.join(self.project, name)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    def write_csv(self, text, name="clips.csv"):
        path = os.path.join(self.project, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def create(self, *extra):
        return self.run_cli(
            ["create", "--input", "demo.mp4", "--start", "0", "--end", "4", *extra, "--json"]
        )

    def settings(self, index=0):
        return self.recorder.calls[index]["settings"]


# ---------------------------------------------------------------------------
# Flag parsing and preflight rejection (section 12.10, SEC-018)
# ---------------------------------------------------------------------------


class TestTransformFlagRejection(TransformCLIBase):
    def assert_rejected(self, code, *extra):
        exit_code, result = self.create(*extra)
        self.assertEqual(exit_code, errors.EXIT_INVALID_TIMESTAMP, msg=result)
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(result["error"]["code"], code)
        # No FFmpeg process may start before preflight completes (section 15.1).
        self.assertEqual(self.recorder.calls, [])
        return result

    def test_invalid_crop_rejected(self):
        self.assert_rejected(errors.INVALID_CROP, "--crop", "0:0:100:100,drawtext=text=x")

    def test_out_of_bounds_crop_rejected(self):
        self.assert_rejected(errors.INVALID_CROP, "--crop", "0:0:4000:4000")

    def test_negative_crop_rejected_with_exit_6(self):
        # argparse would otherwise treat "-1:..." as an unknown option (exit 2).
        self.assert_rejected(errors.INVALID_CROP, "--crop", "-1:0:100:100")

    def test_invalid_dimensions_rejected(self):
        self.assert_rejected(errors.INVALID_DIMENSIONS, "--width", "1")
        self.recorder.calls.clear()
        self.assert_rejected(errors.INVALID_DIMENSIONS, "--height", "9000")
        self.recorder.calls.clear()
        self.assert_rejected(errors.INVALID_DIMENSIONS, "--width", "-5")

    def test_invalid_speed_rejected(self):
        for bad in ("0", "-1", "0.1", "5.0", "1e1", "1.0001"):
            with self.subTest(bad=bad):
                self.recorder.calls.clear()
                self.assert_rejected(errors.INVALID_SPEED, "--speed", bad)

    def test_invalid_dither_rejected_listing_permitted_values(self):
        result = self.assert_rejected(errors.INVALID_DITHER, "--dither", "wobble")
        self.assertIn("sierra2_4a", result["error"]["message"])

    def test_dither_filter_injection_rejected(self):
        self.assert_rejected(errors.INVALID_DITHER, "--dither", "none[a];[a]movie=/etc/passwd")

    def test_bayer_scale_out_of_range_rejected(self):
        self.assert_rejected(errors.INVALID_DITHER, "--bayer-scale", "9")

    def test_bayer_scale_with_non_bayer_mode_rejected(self):
        self.assert_rejected(errors.INVALID_DITHER, "--dither", "sierra2", "--bayer-scale", "3")


# ---------------------------------------------------------------------------
# FR-030 reporting
# ---------------------------------------------------------------------------


class TestTransformReporting(TransformCLIBase):
    def test_created_entry_reports_every_transformation_field(self):
        code, result = self.create(
            "--crop", "320:180:1280:720", "--width", "640", "--speed", "2.0", "--dither", "bayer"
        )
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        entry = result["created"][0]
        self.assertEqual(entry["durationMs"], 4000)
        self.assertEqual(entry["outputDurationMs"], 2000)
        self.assertEqual(
            entry["transformations"],
            {
                "crop": {"x": 320, "y": 180, "width": 1280, "height": 720},
                "sourceWidth": 1920,
                "sourceHeight": 1080,
                "effectiveSourceWidth": 1280,
                "effectiveSourceHeight": 720,
                "speed": 2.0,
                "dither": "bayer",
                "bayerScale": 2,
                "upscaled": False,
            },
        )
        self.assertEqual((entry["width"], entry["height"]), (640, 360))

    def test_untransformed_job_reports_neutral_transformations(self):
        code, result = self.create()
        self.assertEqual(code, errors.EXIT_SUCCESS)
        tx = result["created"][0]["transformations"]
        self.assertIsNone(tx["crop"])
        self.assertEqual(tx["speed"], 1.0)
        self.assertEqual(tx["dither"], "sierra2_4a")
        self.assertIsNone(tx["bayerScale"])
        self.assertFalse(tx["upscaled"])
        self.assertEqual(result["created"][0]["outputDurationMs"], 4000)

    def test_upscaled_flag_and_warning(self):
        code, result = self.create("--width", "3000")
        self.assertEqual(code, errors.EXIT_SUCCESS)
        self.assertFalse(result["created"][0]["transformations"]["upscaled"])
        self.assertTrue(
            any(w.startswith("UPSCALE_NOT_ALLOWED: ") for w in result["warnings"]), result
        )
        self.assertEqual(result["created"][0]["width"], 1920)

    def test_allow_upscale_honors_the_bound(self):
        code, result = self.create("--width", "3000", "--allow-upscale")
        self.assertEqual(code, errors.EXIT_SUCCESS)
        self.assertTrue(result["created"][0]["transformations"]["upscaled"])
        self.assertEqual(result["created"][0]["width"], 3000)
        self.assertEqual(result["warnings"], [])

    def test_profile_only_job_emits_no_upscale_warning(self):
        # Regression guard for FR-026: profile-only jobs emit exactly the
        # warnings they emitted in v0.2.0.
        code, result = self.create("--profile", "high")
        self.assertEqual(code, errors.EXIT_SUCCESS)
        self.assertEqual(result["warnings"], [])

    def test_create_and_batch_report_an_empty_previews_array(self):
        code, result = self.create()
        self.assertEqual(code, errors.EXIT_SUCCESS)
        self.assertEqual(result["previews"], [])
        self.assertEqual(result["summary"]["previews"], 0)

    def test_explicit_odd_width_is_honored_end_to_end(self):
        code, result = self.create("--width", "641")
        self.assertEqual(code, errors.EXIT_SUCCESS)
        self.assertEqual(result["created"][0]["width"], 641)


# ---------------------------------------------------------------------------
# FR-024 precedence, end to end
# ---------------------------------------------------------------------------


class TestTransformPrecedence(TransformCLIBase):
    def test_clip_level_manifest_value_beats_the_command_line_flag(self):
        # AC-0.3.10 / section 9.3: a per-clip value is more specific than a
        # batch-wide flag and MUST win.
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": "demo.mp4",
                "width": 300,
                "clips": [
                    {"name": "a", "start": "0", "end": "1", "width": 500, "speed": 2.0},
                    {"name": "b", "start": "2", "end": "3"},
                ],
            }
        )
        code, result = self.run_cli(["batch", "--manifest", manifest, "--width", "400", "--json"])
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        # Clip a: clip-level 500 beats the --width 400 flag.
        self.assertEqual(result["created"][0]["width"], 500)
        self.assertEqual(result["created"][0]["transformations"]["speed"], 2.0)
        self.assertEqual(result["created"][0]["outputDurationMs"], 500)
        # Clip b: no clip-level value, so the flag beats the top-level 300.
        self.assertEqual(result["created"][1]["width"], 400)
        self.assertEqual(result["created"][1]["transformations"]["speed"], 1.0)

    def test_top_level_manifest_beats_configuration(self):
        self.write_config({"schemaVersion": 1, "transformations": {"width": 200}})
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": "demo.mp4",
                "width": 300,
                "clips": [{"name": "a", "start": "0", "end": "1"}],
            }
        )
        code, result = self.run_cli(["batch", "--manifest", manifest, "--json"])
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["created"][0]["width"], 300)

    def test_configuration_beats_the_built_in_default(self):
        self.write_config(
            {
                "schemaVersion": 1,
                "transformations": {"width": 200, "speed": 2.0, "dither": "floyd_steinberg"},
            }
        )
        code, result = self.create()
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["created"][0]["width"], 200)
        self.assertEqual(result["created"][0]["transformations"]["speed"], 2.0)
        self.assertEqual(result["created"][0]["transformations"]["dither"], "floyd_steinberg")

    def test_command_line_flag_beats_configuration(self):
        self.write_config({"schemaVersion": 1, "transformations": {"width": 200}})
        code, result = self.create("--width", "800")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["created"][0]["width"], 800)

    def test_explicit_width_overrides_the_profile_maximum(self):
        # AC-0.3.3: profile small caps at 480; --width 800 must win.
        code, result = self.create("--profile", "small", "--width", "800")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["created"][0]["width"], 800)


# ---------------------------------------------------------------------------
# Manifest and configuration parsing (sections 9.6, 10.4, 11.2)
# ---------------------------------------------------------------------------


class TestManifestAndConfigFields(TransformCLIBase):
    def test_json_manifest_crop_object_and_string_forms(self):
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": "demo.mp4",
                "clips": [
                    {
                        "name": "a",
                        "start": "0",
                        "end": "1",
                        "crop": {"x": 0, "y": 0, "width": 1280, "height": 720},
                    },
                    {"name": "b", "start": "2", "end": "3", "crop": "0:0:640:360"},
                ],
            }
        )
        code, result = self.run_cli(["batch", "--manifest", manifest, "--json"])
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(
            result["created"][0]["transformations"]["crop"],
            {"x": 0, "y": 0, "width": 1280, "height": 720},
        )
        self.assertEqual(
            result["created"][1]["transformations"]["crop"],
            {"x": 0, "y": 0, "width": 640, "height": 360},
        )

    def test_csv_transformation_columns(self):
        csv_path = self.write_csv(
            "name,start,end,crop,width,speed,dither,bayerScale\n"
            "a,0,4,0:0:1280:720,640,2.0,bayer,4\n"
        )
        code, result = self.run_cli(
            ["batch", "--manifest", csv_path, "--input", "demo.mp4", "--json"]
        )
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        tx = result["created"][0]["transformations"]
        self.assertEqual(tx["crop"], {"x": 0, "y": 0, "width": 1280, "height": 720})
        self.assertEqual(tx["speed"], 2.0)
        self.assertEqual(tx["dither"], "bayer")
        self.assertEqual(tx["bayerScale"], 4)
        self.assertEqual(result["created"][0]["width"], 640)
        self.assertEqual(result["created"][0]["outputDurationMs"], 2000)

    def test_empty_csv_cell_means_unspecified(self):
        csv_path = self.write_csv("name,start,end,crop,speed\na,0,4,,\n")
        code, result = self.run_cli(
            ["batch", "--manifest", csv_path, "--input", "demo.mp4", "--json"]
        )
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertIsNone(result["created"][0]["transformations"]["crop"])
        self.assertEqual(result["created"][0]["transformations"]["speed"], 1.0)

    def test_manifest_transformation_error_names_the_clip(self):
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": "demo.mp4",
                "clips": [
                    {"name": "a", "start": "0", "end": "1"},
                    {"name": "b", "start": "2", "end": "3", "speed": 9.0},
                ],
            }
        )
        code, result = self.run_cli(["batch", "--manifest", manifest, "--json"])
        self.assertEqual(code, errors.EXIT_INVALID_TIMESTAMP)
        self.assertEqual(result["error"]["code"], errors.INVALID_SPEED)
        self.assertEqual(result["error"]["clipIndex"], 1)
        self.assertEqual(result["error"]["field"], "clips[1].speed")

    def test_config_crop_key_rejected_by_validate_config(self):
        # Section 9.6: a crop rectangle is never configurable.
        path = os.path.join(self.project, "cfg.json")
        with open(path, "w") as fh:
            json.dump({"schemaVersion": 1, "transformations": {"crop": "0:0:10:10"}}, fh)
        code, result = self.run_cli(["validate-config", "--config", path, "--json"])
        self.assertEqual(code, errors.EXIT_INVALID_USAGE)
        self.assertEqual(result["error"]["code"], errors.INVALID_CONFIG)
        self.assertEqual(result["error"]["field"], "transformations.crop")

    def test_validate_config_reports_transformation_defaults(self):
        path = os.path.join(self.project, "cfg.json")
        with open(path, "w") as fh:
            json.dump(
                {
                    "schemaVersion": 1,
                    "transformations": {"width": 640, "speed": 1.5, "dither": "sierra2"},
                },
                fh,
            )
        code, result = self.run_cli(["validate-config", "--config", path, "--json"])
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(
            result["resolved"]["transformations"],
            {
                "width": 640,
                "height": None,
                "speed": 1.5,
                "dither": "sierra2",
                "bayerScale": None,
            },
        )

    def test_validate_config_range_checks_transformations(self):
        # Section 9.6: every source-independent FR-026..FR-028 check applies.
        for bad, code in (
            ({"width": 1}, errors.INVALID_DIMENSIONS),
            ({"speed": 9}, errors.INVALID_SPEED),
            ({"dither": "wobble"}, errors.INVALID_DITHER),
            ({"bayerScale": 9}, errors.INVALID_DITHER),
        ):
            with self.subTest(bad=bad):
                path = os.path.join(self.project, "cfg.json")
                with open(path, "w") as fh:
                    json.dump({"schemaVersion": 1, "transformations": bad}, fh)
                exit_code, result = self.run_cli(["validate-config", "--config", path, "--json"])
                self.assertEqual(exit_code, errors.EXIT_INVALID_TIMESTAMP)
                self.assertEqual(result["error"]["code"], code)

    def test_unknown_transformation_config_field_warns(self):
        path = os.path.join(self.project, "cfg.json")
        with open(path, "w") as fh:
            json.dump({"schemaVersion": 1, "transformations": {"wobble": 1}}, fh)
        code, result = self.run_cli(["validate-config", "--config", path, "--json"])
        self.assertEqual(code, errors.EXIT_SUCCESS)
        self.assertIn("Unknown config field: transformations.wobble", result["warnings"])

    def test_config_omitting_transformations_behaves_as_defaults(self):
        # NFR-006: a v0.2.0 config must behave exactly as before.
        self.write_config({"schemaVersion": 1, "defaultProfile": "small"})
        code, result = self.create()
        self.assertEqual(code, errors.EXIT_SUCCESS)
        tx = result["created"][0]["transformations"]
        self.assertEqual(tx["speed"], 1.0)
        self.assertEqual(tx["dither"], "bayer")
        self.assertEqual(tx["bayerScale"], 5)


# ---------------------------------------------------------------------------
# FR-029 / sections 12.9, 13.4: preview frames
# ---------------------------------------------------------------------------


class TestPreviewCommand(TransformCLIBase):
    def preview(self, *extra):
        return self.run_cli(["preview", "--input", "demo.mp4", *extra, "--json"])

    def test_single_frame_result_shape(self):
        # AC-0.3.9
        code, result = self.preview(
            "--at", "00:00:02.500", "--crop", "320:180:1280:720", "--width", "640"
        )
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["command"], "preview")
        self.assertEqual(result["created"], [])
        self.assertEqual(result["summary"]["created"], 0)
        self.assertEqual(result["summary"]["previews"], 1)
        self.assertEqual(len(result["previews"]), 1)
        entry = result["previews"][0]
        self.assertEqual(entry["atMs"], 2500)
        self.assertEqual((entry["width"], entry["height"]), (640, 360))
        self.assertIn("sizeBytes", entry)
        self.assertEqual(
            entry["transformations"]["crop"],
            {"x": 320, "y": 180, "width": 1280, "height": 720},
        )
        # No GIF is produced.
        self.assertEqual(self.recorder.calls, [])
        self.assertEqual(len(self.recorder.previews), 1)

    def test_default_name_is_video_stem_and_timestamp(self):
        code, result = self.preview("--at", "00:00:12.500")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertTrue(result["previews"][0]["path"].endswith("demo_00-00-12.500.png"))

    def test_manifest_form_names_each_clip(self):
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": "demo.mp4",
                "clips": [
                    {"name": "opening", "start": "00:00:01", "end": "00:00:02"},
                    {"start": "00:00:05", "end": "00:00:06"},
                ],
            }
        )
        code, result = self.run_cli(["preview", "--manifest", manifest, "--json"])
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["summary"]["previews"], 2)
        self.assertTrue(result["previews"][0]["path"].endswith("opening_00-00-01.000.png"))
        self.assertTrue(result["previews"][1]["path"].endswith("demo_00-00-05.000.png"))
        self.assertEqual(result["previews"][0]["atMs"], 1000)

    def test_manifest_form_applies_per_clip_transformations(self):
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": "demo.mp4",
                "clips": [
                    {"name": "a", "start": "1", "end": "2", "width": 400},
                    {"name": "b", "start": "3", "end": "4", "crop": "0:0:600:600"},
                ],
            }
        )
        code, result = self.run_cli(["preview", "--manifest", manifest, "--json"])
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["previews"][0]["width"], 400)
        self.assertEqual(
            (result["previews"][1]["width"], result["previews"][1]["height"]), (600, 600)
        )

    def test_explicit_png_output_name(self):
        code, result = self.preview("--at", "1", "--output-name", "framing-check.png")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertTrue(result["previews"][0]["path"].endswith("framing-check.png"))

    def test_output_name_without_extension_gains_png(self):
        code, result = self.preview("--at", "1", "--output-name", "framing")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertTrue(result["previews"][0]["path"].endswith("framing.png"))

    def test_non_png_output_name_rejected(self):
        for bad in ("shot.gif", "shot.jpg", "shot.PNG.gif"):
            with self.subTest(bad=bad):
                code, result = self.preview("--at", "1", "--output-name", bad)
                self.assertEqual(code, errors.EXIT_INVALID_USAGE)
                self.assertEqual(result["error"]["code"], errors.INVALID_USAGE)

    def test_uppercase_png_extension_accepted(self):
        code, result = self.preview("--at", "1", "--output-name", "Shot.PNG")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertTrue(result["previews"][0]["path"].endswith(".png"))

    def test_output_name_with_path_separator_rejected(self):
        code, result = self.preview("--at", "1", "--output-name", "../escape.png")
        self.assertEqual(code, errors.EXIT_INVALID_USAGE)
        self.assertEqual(result["error"]["code"], errors.INVALID_USAGE)

    def test_ignored_settings_produce_one_warning(self):
        code, result = self.preview("--at", "1", "--speed", "2.0", "--fps", "10")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        applicable = [
            w for w in result["warnings"] if w.startswith("TRANSFORMATION_NOT_APPLICABLE: ")
        ]
        self.assertEqual(len(applicable), 1)
        self.assertIn("speed", applicable[0])
        self.assertIn("fps", applicable[0])
        # The preview itself still reports a neutral speed (section 13.4).
        self.assertEqual(result["previews"][0]["transformations"]["speed"], 1.0)
        self.assertIsNone(result["previews"][0]["transformations"]["dither"])

    def test_no_warning_when_only_spatial_settings_supplied(self):
        code, result = self.preview("--at", "1", "--crop", "0:0:640:360", "--width", "320")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(
            [w for w in result["warnings"] if "TRANSFORMATION_NOT_APPLICABLE" in w], []
        )

    def test_at_outside_source_duration_rejected(self):
        for bad in ("60", "120", "99:00"):
            with self.subTest(bad=bad):
                code, result = self.preview("--at", bad)
                self.assertEqual(code, errors.EXIT_INVALID_TIMESTAMP)
                self.assertEqual(result["error"]["code"], errors.INVALID_TIMESTAMP)
                self.assertEqual(self.recorder.previews, [])

    def test_at_zero_accepted(self):
        code, _ = self.preview("--at", "0")
        self.assertEqual(code, errors.EXIT_SUCCESS)

    def test_missing_at_and_manifest_rejected(self):
        code, result = self.preview()
        self.assertEqual(code, errors.EXIT_INVALID_USAGE)
        self.assertEqual(result["error"]["code"], errors.INVALID_USAGE)

    def test_collision_uses_the_default_fail_policy(self):
        os.makedirs(os.path.join(self.project, "output"), exist_ok=True)
        with open(os.path.join(self.project, "output", "demo_00-00-01.000.png"), "wb") as fh:
            fh.write(b"existing")
        code, result = self.preview("--at", "1")
        self.assertEqual(code, errors.EXIT_COLLISION)
        self.assertEqual(result["status"], "collision")
        self.assertEqual(self.recorder.previews, [])
        # The existing file is untouched.
        with open(os.path.join(self.project, "output", "demo_00-00-01.000.png"), "rb") as fh:
            self.assertEqual(fh.read(), b"existing")

    def test_collision_policy_unique_renames(self):
        os.makedirs(os.path.join(self.project, "output"), exist_ok=True)
        with open(os.path.join(self.project, "output", "demo_00-00-01.000.png"), "wb") as fh:
            fh.write(b"existing")
        code, result = self.preview("--at", "1", "--collision-policy", "unique")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertTrue(result["previews"][0]["path"].endswith("demo_00-00-01.000-1.png"))

    def test_dry_run_writes_nothing(self):
        code, result = self.preview("--at", "1", "--dry-run")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(len(result["plan"]), 1)
        self.assertEqual(result["plan"][0]["atMs"], 1000)
        self.assertEqual(self.recorder.previews, [])

    def test_invalid_transformation_rejected_before_extraction(self):
        code, result = self.preview("--at", "1", "--crop", "0:0:9000:9000")
        self.assertEqual(code, errors.EXIT_INVALID_TIMESTAMP)
        self.assertEqual(result["error"]["code"], errors.INVALID_CROP)
        self.assertEqual(self.recorder.previews, [])

    def test_failed_preview_is_reported_as_a_failure(self):
        def boom(ffmpeg, source, **kw):
            raise errors.EngineError(
                errors.FFMPEG_FAILED,
                "boom",
                exit_code=errors.EXIT_FFMPEG_FAILED,
                status=errors.STATUS_FAILED,
                stage="preview",
                clip_index=kw["clip_index"],
            )

        with mock.patch("vtg.ffmpeg.extract_preview", side_effect=boom):
            code, result = self.preview("--at", "1")
        self.assertEqual(code, errors.EXIT_FFMPEG_FAILED)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["summary"]["previews"], 0)
        self.assertEqual(result["summary"]["created"], 0)
        self.assertEqual(result["failed"][0]["stage"], "preview")
        self.assertEqual(result["failed"][0]["code"], errors.FFMPEG_FAILED)

    def test_preview_honors_the_profile_maximum_width(self):
        code, result = self.preview("--at", "1", "--profile", "small")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["previews"][0]["width"], 480)

    def test_preview_upscale_rules_apply(self):
        code, result = self.preview("--at", "1", "--width", "3000")
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["previews"][0]["width"], 1920)
        self.assertTrue(any(w.startswith("UPSCALE_NOT_ALLOWED: ") for w in result["warnings"]))


# ---------------------------------------------------------------------------
# NFR-006 backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility(TransformCLIBase):
    def test_v0_2_0_invocation_is_unchanged(self):
        # AC-0.3.13: schemaVersion stays 1 and the v0.2.0 fields keep meaning.
        code, result = self.run_cli(
            [
                "create",
                "--input",
                "demo.mp4",
                "--start",
                "00:00:01",
                "--duration",
                "2",
                "--profile",
                "balanced",
                "--width",
                "800",
                "--fps",
                "12",
                "--colors",
                "128",
                "--loop",
                "forever",
                "--json",
            ]
        )
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["schemaVersion"], 1)
        entry = result["created"][0]
        self.assertEqual(entry["width"], 800)
        self.assertEqual(entry["fps"], 12.0)
        self.assertEqual(entry["durationMs"], 2000)

    def test_v0_2_0_manifest_without_transformations(self):
        manifest = self.write_manifest(
            {
                "schemaVersion": 1,
                "input": "demo.mp4",
                "profile": "small",
                "clips": [{"name": "a", "start": "0", "end": "1"}],
            }
        )
        code, result = self.run_cli(["batch", "--manifest", manifest, "--json"])
        self.assertEqual(code, errors.EXIT_SUCCESS, msg=result)
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(result["created"][0]["width"], 480)
        self.assertEqual(result["created"][0]["transformations"]["dither"], "bayer")

    def test_normalize_argv_leaves_ordinary_arguments_alone(self):
        argv = ["create", "--input", "demo.mp4", "--start", "0", "--end", "1", "--json"]
        self.assertEqual(cli.normalize_argv(argv), argv)

    def test_normalize_argv_joins_only_negative_numeric_values(self):
        self.assertEqual(cli.normalize_argv(["--crop", "-1:0:2:2"]), ["--crop=-1:0:2:2"])
        self.assertEqual(cli.normalize_argv(["--speed", "-1"]), ["--speed=-1"])
        # A following flag is never swallowed.
        self.assertEqual(
            cli.normalize_argv(["--crop", "--width", "500"]), ["--crop", "--width", "500"]
        )


if __name__ == "__main__":
    unittest.main()
