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
