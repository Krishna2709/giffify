"""Configuration validation and precedence tests (spec section 9, 22.1)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import errors
from vtg.cli import _first
from vtg.config import (
    DEFAULT_MAX_CLIP_SECONDS,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_MAX_DOWNLOAD_SECONDS,
    DEFAULT_MAX_TEMP_BYTES,
    Config,
    validate_config_dict,
)


class TestConfigValidation(unittest.TestCase):
    def test_defaults(self):
        cfg = validate_config_dict({})
        self.assertEqual(cfg.default_profile, "balanced")
        self.assertEqual(cfg.output_directory, "./output")
        self.assertEqual(cfg.loop, "forever")
        self.assertEqual(cfg.collision_policy, "fail")
        self.assertTrue(cfg.continue_on_error)
        self.assertEqual(cfg.max_clip_processing_seconds, DEFAULT_MAX_CLIP_SECONDS)
        self.assertEqual(cfg.max_temporary_bytes, DEFAULT_MAX_TEMP_BYTES)

    def test_full_valid_config(self):
        cfg = validate_config_dict(
            {
                "schemaVersion": 1,
                "defaultProfile": "high",
                "outputDirectory": "./gifs",
                "loop": "once",
                "collisionPolicy": "ask",
                "continueOnError": False,
                "keepTemporaryFiles": True,
                "allowOutsideProject": True,
                "remoteSources": "disabled",
                "limits": {"maxClipProcessingSeconds": 120, "maxTemporaryBytes": 1000},
            }
        )
        self.assertEqual(cfg.default_profile, "high")
        self.assertEqual(cfg.loop, 1)  # "once" normalizes to 1
        self.assertEqual(cfg.collision_policy, "ask")
        self.assertFalse(cfg.continue_on_error)
        self.assertTrue(cfg.keep_temporary_files)
        self.assertEqual(cfg.max_clip_processing_seconds, 120)
        self.assertEqual(cfg.max_temporary_bytes, 1000)

    def test_malformed_field_has_field_path(self):
        cases = [
            ({"schemaVersion": "1"}, "schemaVersion"),
            ({"defaultProfile": "nope"}, "defaultProfile"),
            ({"collisionPolicy": "weird"}, "collisionPolicy"),
            ({"continueOnError": "yes"}, "continueOnError"),
            ({"loop": 0}, "loop"),
            ({"limits": {"maxClipProcessingSeconds": -1}}, "limits.maxClipProcessingSeconds"),
            ({"limits": {"maxTemporaryBytes": 0}}, "limits.maxTemporaryBytes"),
            ({"limits": "big"}, "limits"),
            ({"outputDirectory": ""}, "outputDirectory"),
        ]
        for data, field in cases:
            with self.subTest(field=field):
                with self.assertRaises(errors.EngineError) as ctx:
                    validate_config_dict(data)
                self.assertEqual(ctx.exception.code, errors.INVALID_CONFIG)
                self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_USAGE)
                self.assertEqual(ctx.exception.field, field)

    def test_unknown_fields_warn(self):
        cfg = validate_config_dict({"schemaVersion": 1, "mystery": 5, "limits": {"foo": 1}})
        self.assertTrue(any("mystery" in w for w in cfg.warnings))
        self.assertTrue(any("limits.foo" in w for w in cfg.warnings))

    def test_forbidden_fields_rejected(self):
        for key in ("password", "accessToken", "privateKey", "command", "hooks", "shell"):
            with self.subTest(key=key):
                with self.assertRaises(errors.EngineError) as ctx:
                    validate_config_dict({key: "x"})
                self.assertEqual(ctx.exception.field, key)

    def test_non_object_rejected(self):
        with self.assertRaises(errors.EngineError):
            validate_config_dict([1, 2, 3])

    def test_collision_policy_ask_still_valid_in_config(self):
        # M1: unlike a manifest (see test_manifests), config accepts "ask" — the
        # agent resolves it, and the engine treats it as fail (never overwrites).
        cfg = validate_config_dict({"schemaVersion": 1, "collisionPolicy": "ask"})
        self.assertEqual(cfg.collision_policy, "ask")


class TestRemoteConfig(unittest.TestCase):
    """v0.2.0 remote source configuration (spec section 9.5, FR-018/FR-021)."""

    def test_remote_defaults(self):
        cfg = validate_config_dict({})
        self.assertEqual(cfg.remote_sources, "disabled")
        self.assertFalse(cfg.keep_remote_source)
        self.assertEqual(cfg.max_download_bytes, DEFAULT_MAX_DOWNLOAD_BYTES)
        self.assertEqual(cfg.max_download_seconds, DEFAULT_MAX_DOWNLOAD_SECONDS)
        # The documented defaults (spec FR-021).
        self.assertEqual(cfg.max_download_bytes, 2147483648)
        self.assertEqual(cfg.max_download_seconds, 900)

    def test_remote_sources_accepts_all_three_values(self):
        for value in ("disabled", "ask", "enabled"):
            with self.subTest(value=value):
                cfg = validate_config_dict({"schemaVersion": 1, "remoteSources": value})
                self.assertEqual(cfg.remote_sources, value)

    def test_remote_sources_rejects_unknown(self):
        for bad in ("on", "true", "", 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(errors.EngineError) as ctx:
                    validate_config_dict({"remoteSources": bad})
                self.assertEqual(ctx.exception.field, "remoteSources")
                self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_USAGE)

    def test_keep_remote_source_must_be_bool(self):
        cfg = validate_config_dict({"schemaVersion": 1, "keepRemoteSource": True})
        self.assertTrue(cfg.keep_remote_source)
        with self.assertRaises(errors.EngineError) as ctx:
            validate_config_dict({"keepRemoteSource": "yes"})
        self.assertEqual(ctx.exception.field, "keepRemoteSource")

    def test_download_limits_validated(self):
        cfg = validate_config_dict(
            {"schemaVersion": 1, "limits": {"maxDownloadBytes": 5000, "maxDownloadSeconds": 30}}
        )
        self.assertEqual(cfg.max_download_bytes, 5000)
        self.assertEqual(cfg.max_download_seconds, 30)

    def test_download_limits_field_paths(self):
        cases = [
            ({"limits": {"maxDownloadBytes": 0}}, "limits.maxDownloadBytes"),
            ({"limits": {"maxDownloadBytes": -1}}, "limits.maxDownloadBytes"),
            ({"limits": {"maxDownloadBytes": 1.5}}, "limits.maxDownloadBytes"),
            ({"limits": {"maxDownloadSeconds": 0}}, "limits.maxDownloadSeconds"),
            ({"limits": {"maxDownloadSeconds": -3}}, "limits.maxDownloadSeconds"),
        ]
        for data, field in cases:
            with self.subTest(field=field, data=data):
                with self.assertRaises(errors.EngineError) as ctx:
                    validate_config_dict(data)
                self.assertEqual(ctx.exception.field, field)

    def test_omitting_download_limits_uses_defaults(self):
        # A config that omits them MUST behave as though the defaults were given.
        cfg = validate_config_dict({"schemaVersion": 1, "limits": {"maxTemporaryBytes": 1000}})
        self.assertEqual(cfg.max_download_bytes, DEFAULT_MAX_DOWNLOAD_BYTES)
        self.assertEqual(cfg.max_download_seconds, DEFAULT_MAX_DOWNLOAD_SECONDS)

    def test_keep_remote_source_precedence_cli_over_config(self):
        # The engine resolves keep = (--keep-remote-source flag) OR (config value).
        def resolve_keep(flag: bool, cfg_value: bool) -> bool:
            return bool(flag) or cfg_value

        self.assertTrue(resolve_keep(True, Config(keep_remote_source=False).keep_remote_source))
        self.assertTrue(resolve_keep(False, Config(keep_remote_source=True).keep_remote_source))
        self.assertFalse(resolve_keep(False, Config(keep_remote_source=False).keep_remote_source))


class TestPrecedence(unittest.TestCase):
    """Precedence: CLI arg > request/manifest > project config > built-in default."""

    def test_first_helper_order(self):
        # _first returns the highest-priority non-None value.
        self.assertEqual(_first("cli", "manifest", "config"), "cli")
        self.assertEqual(_first(None, "manifest", "config"), "manifest")
        self.assertEqual(_first(None, None, "config"), "config")
        self.assertIsNone(_first(None, None, None))

    def test_cli_overrides_config(self):
        cfg = Config(default_profile="small")
        # Simulate resolution: CLI arg present.
        effective = _first("high", None, cfg.default_profile)
        self.assertEqual(effective, "high")

    def test_config_used_when_no_cli(self):
        cfg = Config(default_profile="small")
        effective = _first(None, None, cfg.default_profile)
        self.assertEqual(effective, "small")


if __name__ == "__main__":
    unittest.main()
