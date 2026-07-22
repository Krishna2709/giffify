"""Project configuration loading, validation, and precedence (spec section 9).

Config file: ``.video-to-gif.json`` resolved relative to the project root.
Precedence (highest first): CLI arg > request instruction > project config >
built-in default. The engine sees CLI args and the config file; the "request
instruction" layer belongs to the agent and arrives as CLI args.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from . import errors, models, transforms

CONFIG_FILENAME = ".video-to-gif.json"

# Fields the engine understands at the top level of the config.
_KNOWN_FIELDS = {
    "schemaVersion",
    "defaultProfile",
    "outputDirectory",
    "loop",
    "collisionPolicy",
    "continueOnError",
    "keepTemporaryFiles",
    "allowOutsideProject",
    "remoteSources",
    "keepRemoteSource",
    "transformations",
    "limits",
}
# Global transformation defaults (spec section 9.6). A crop rectangle is only
# meaningful against a specific source's dimensions, so it MUST NOT appear here.
_KNOWN_TRANSFORM_FIELDS = {"width", "height", "speed", "dither", "bayerScale"}
_KNOWN_LIMIT_FIELDS = {
    "maxClipProcessingSeconds",
    "maxTemporaryBytes",
    "maxDownloadBytes",
    "maxDownloadSeconds",
}

# Permitted values for the remoteSources gate (spec FR-018 / section 9.5). In
# v0.1.0 only "disabled" was accepted; v0.2.0 adds "ask" and "enabled".
VALID_REMOTE_SOURCES = frozenset({"disabled", "ask", "enabled"})

# Forbidden config content (section 9.4). Presence of these keys is an error.
_FORBIDDEN_FIELDS = {
    "credentials",
    "accessToken",
    "accessTokens",
    "token",
    "password",
    "privateKey",
    "signedUrl",
    "signedUrls",
    "command",
    "commands",
    "hook",
    "hooks",
    "exec",
    "shell",
}

DEFAULT_MAX_CLIP_SECONDS = 600
DEFAULT_MAX_TEMP_BYTES = 2147483648
DEFAULT_MAX_DOWNLOAD_BYTES = 2147483648  # 2 GiB (spec FR-021 / section 9.5)
DEFAULT_MAX_DOWNLOAD_SECONDS = 900  # 15 minutes (spec FR-021 / section 9.5)


@dataclass
class Config:
    schema_version: int = 1
    default_profile: str = "balanced"
    output_directory: str = "./output"
    loop: models.LoopValue = "forever"
    collision_policy: str = "fail"
    continue_on_error: bool = True
    keep_temporary_files: bool = False
    allow_outside_project: bool = False
    remote_sources: str = "disabled"
    keep_remote_source: bool = False
    max_clip_processing_seconds: int = DEFAULT_MAX_CLIP_SECONDS
    max_temporary_bytes: int = DEFAULT_MAX_TEMP_BYTES
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    max_download_seconds: int = DEFAULT_MAX_DOWNLOAD_SECONDS
    # Global transformation defaults (section 9.6). ``None`` at every field means
    # "not configured", so the built-in default applies (FR-024).
    transform_width: int | None = None
    transform_height: int | None = None
    transform_speed: Decimal | None = None
    transform_dither: str | None = None
    transform_bayer_scale: int | None = None
    source_path: str | None = None  # where the config was loaded from
    warnings: list[str] = field(default_factory=list)

    @property
    def transform_spec(self) -> transforms.TransformSpec:
        """The configuration precedence level for transformations (FR-024)."""
        return transforms.TransformSpec(
            crop=None,  # never configurable (section 9.6)
            width=self.transform_width,
            height=self.transform_height,
            speed=self.transform_speed,
            dither=self.transform_dither,
            bayer_scale=self.transform_bayer_scale,
        )

    def transformations_public(self) -> dict[str, Any]:
        """Serialize the resolved transformation defaults for validate-config."""
        return {
            "width": self.transform_width,
            "height": self.transform_height,
            "speed": None if self.transform_speed is None else float(self.transform_speed),
            "dither": self.transform_dither,
            "bayerScale": self.transform_bayer_scale,
        }


def _config_error(message: str, field_path: str) -> errors.EngineError:
    return errors.EngineError(
        errors.INVALID_CONFIG,
        message,
        exit_code=errors.EXIT_INVALID_USAGE,
        status=errors.STATUS_VALIDATION_FAILED,
        stage="config",
        field=field_path,
    )


def validate_config_dict(data: Any, *, source_path: str | None = None) -> Config:
    """Validate a parsed config mapping and return a :class:`Config`.

    Raises :class:`EngineError` (INVALID_CONFIG, exit 2) with a field path on any
    structural problem. Unknown fields produce warnings, not errors.
    """
    if not isinstance(data, dict):
        raise _config_error("Configuration must be a JSON object.", "$")

    cfg = Config(source_path=source_path)
    warnings: list[str] = []

    # Forbidden content (section 9.4).
    for key in data:
        if key in _FORBIDDEN_FIELDS:
            raise _config_error(
                f"Configuration must not contain sensitive or executable field {key!r}.",
                key,
            )

    # schemaVersion
    if "schemaVersion" in data:
        sv = data["schemaVersion"]
        if not isinstance(sv, int) or isinstance(sv, bool):
            raise _config_error("schemaVersion must be an integer.", "schemaVersion")
        cfg.schema_version = sv
        if sv != 1:
            warnings.append(f"Unrecognized schemaVersion {sv}; expected 1. Proceeding best-effort.")

    if "defaultProfile" in data:
        prof = data["defaultProfile"]
        if not isinstance(prof, str) or prof not in models.VALID_PROFILE_NAMES:
            raise _config_error(
                f"defaultProfile must be one of {sorted(models.VALID_PROFILE_NAMES)}.",
                "defaultProfile",
            )
        cfg.default_profile = prof

    if "outputDirectory" in data:
        od = data["outputDirectory"]
        if not isinstance(od, str) or od == "":
            raise _config_error("outputDirectory must be a non-empty string.", "outputDirectory")
        cfg.output_directory = od

    if "loop" in data:
        try:
            cfg.loop = models.parse_loop(data["loop"], field_path="loop")
        except errors.EngineError as exc:
            raise _config_error(exc.message, "loop") from exc

    if "collisionPolicy" in data:
        cp = data["collisionPolicy"]
        if not isinstance(cp, str) or cp not in models.VALID_COLLISION_POLICIES:
            raise _config_error(
                f"collisionPolicy must be one of {sorted(models.VALID_COLLISION_POLICIES)}.",
                "collisionPolicy",
            )
        cfg.collision_policy = cp

    for bool_field, attr in (
        ("continueOnError", "continue_on_error"),
        ("keepTemporaryFiles", "keep_temporary_files"),
        ("allowOutsideProject", "allow_outside_project"),
        ("keepRemoteSource", "keep_remote_source"),
    ):
        if bool_field in data:
            val = data[bool_field]
            if not isinstance(val, bool):
                raise _config_error(f"{bool_field} must be a boolean.", bool_field)
            setattr(cfg, attr, val)

    if "remoteSources" in data:
        rs = data["remoteSources"]
        if not isinstance(rs, str) or rs not in VALID_REMOTE_SOURCES:
            raise _config_error(
                f"remoteSources must be one of {sorted(VALID_REMOTE_SOURCES)}.",
                "remoteSources",
            )
        cfg.remote_sources = rs

    if "transformations" in data:
        _apply_transformations(cfg, data["transformations"], warnings)

    if "limits" in data:
        limits = data["limits"]
        if not isinstance(limits, dict):
            raise _config_error("limits must be an object.", "limits")
        for lk in limits:
            if lk not in _KNOWN_LIMIT_FIELDS:
                warnings.append(f"Unknown config field: limits.{lk}")
        if "maxClipProcessingSeconds" in limits:
            v = limits["maxClipProcessingSeconds"]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                raise _config_error(
                    "limits.maxClipProcessingSeconds must be a positive number.",
                    "limits.maxClipProcessingSeconds",
                )
            cfg.max_clip_processing_seconds = int(v)
        if "maxTemporaryBytes" in limits:
            v = limits["maxTemporaryBytes"]
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise _config_error(
                    "limits.maxTemporaryBytes must be a positive integer.",
                    "limits.maxTemporaryBytes",
                )
            cfg.max_temporary_bytes = v
        if "maxDownloadBytes" in limits:
            v = limits["maxDownloadBytes"]
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise _config_error(
                    "limits.maxDownloadBytes must be a positive integer.",
                    "limits.maxDownloadBytes",
                )
            cfg.max_download_bytes = v
        if "maxDownloadSeconds" in limits:
            v = limits["maxDownloadSeconds"]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                raise _config_error(
                    "limits.maxDownloadSeconds must be a positive number.",
                    "limits.maxDownloadSeconds",
                )
            cfg.max_download_seconds = int(v)

    # Unknown top-level fields -> warnings (section 9.4).
    for key in data:
        if key not in _KNOWN_FIELDS:
            warnings.append(f"Unknown config field: {key}")

    cfg.warnings = warnings
    return cfg


def _apply_transformations(cfg: Config, raw: Any, warnings: list[str]) -> None:
    """Validate the ``transformations`` object (section 9.6, FR-026..FR-028).

    Every source-independent check of FR-026 through FR-028 is applied here, so
    ``validate-config`` rejects an out-of-range bound, an unknown dither mode, or
    a Bayer scale outside 0..5 without touching a source. Source-dependent checks
    (crop bounds, upscale evaluation) happen during preflight.
    """
    if not isinstance(raw, dict):
        raise _config_error("transformations must be an object.", "transformations")

    # A crop rectangle is only meaningful against a specific source (FR-025), so
    # configuration MUST NOT define one; this is an error, not a warning.
    if "crop" in raw:
        raise _config_error(
            "transformations.crop is not permitted in project configuration: a crop "
            "rectangle is only meaningful against a specific source's dimensions. "
            "Supply it per request (--crop) or per clip in a manifest.",
            "transformations.crop",
        )

    for key in raw:
        if key not in _KNOWN_TRANSFORM_FIELDS:
            warnings.append(f"Unknown config field: transformations.{key}")

    def parse(key: str, parser: Any) -> Any:
        value = raw.get(key)
        if key not in raw or value is None:
            return None
        try:
            return parser(value, field_path=f"transformations.{key}")
        except errors.EngineError as exc:
            # Keep the transformation error code (FR-026..028) but report it as a
            # configuration validation failure with the exact field path.
            exc.stage = "config"
            exc.field = f"transformations.{key}"
            raise

    cfg.transform_width = parse("width", transforms.parse_dimension)
    cfg.transform_height = parse("height", transforms.parse_dimension)
    cfg.transform_speed = parse("speed", transforms.parse_speed)
    cfg.transform_dither = parse("dither", transforms.parse_dither)
    cfg.transform_bayer_scale = parse("bayerScale", transforms.parse_bayer_scale)


def load_config_file(path: str) -> Config:
    """Load and validate a config file at an explicit path."""
    if not os.path.exists(path):
        raise errors.EngineError(
            errors.INVALID_CONFIG,
            f"Configuration file not found: {path}",
            exit_code=errors.EXIT_INPUT_NOT_FOUND,
            status=errors.STATUS_FAILED,
            stage="config",
            remediation="Provide a valid path to a .video-to-gif.json file.",
        )
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise errors.EngineError(
            errors.INVALID_CONFIG,
            f"Cannot read configuration file {path}: {exc}",
            exit_code=errors.EXIT_INPUT_NOT_FOUND,
            status=errors.STATUS_FAILED,
            stage="config",
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _config_error(
            f"Malformed configuration JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}.",
            "$",
        ) from exc
    return validate_config_dict(data, source_path=path)


def resolve_config(
    *,
    explicit_path: str | None = None,
    project_root: str,
) -> Config:
    """Resolve the effective project config (section 9).

    Uses ``explicit_path`` when given, otherwise looks for ``.video-to-gif.json``
    at the project root. Returns built-in defaults when no file exists.
    """
    if explicit_path:
        return load_config_file(explicit_path)
    candidate = os.path.join(project_root, CONFIG_FILENAME)
    if os.path.exists(candidate):
        return load_config_file(candidate)
    return Config()
