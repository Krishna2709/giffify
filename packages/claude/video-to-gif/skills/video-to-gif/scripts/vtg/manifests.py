"""JSON and CSV manifest parsing (spec sections 10, 11; SEC-009).

Manifest values are pure data: no environment expansion, no expressions, no
hooks. Malformed manifests always produce structured validation errors.
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from typing import Any

from . import errors, models
from .timestamps import parse_duration, parse_timestamp

# JSON manifest fields (section 10).
_JSON_TOP_KNOWN = {
    "schemaVersion",
    "input",
    "outputDirectory",
    "profile",
    "loop",
    "continueOnError",
    "collisionPolicy",
    "width",
    "fps",
    "colors",
    "allowUpscale",
    "clips",
}
_JSON_CLIP_KNOWN = {
    "name",
    "start",
    "end",
    "duration",
    "profile",
    "width",
    "fps",
    "colors",
    "loop",
}

# CSV columns (section 11).
_CSV_REQUIRED = {"start"}
_CSV_KNOWN = {"start", "end", "duration", "name", "profile", "width", "fps", "colors", "loop"}


@dataclass
class Manifest:
    input: str | None
    clips: list[models.ClipSpec]
    output_directory: str | None = None
    profile: str | None = None
    loop: models.LoopValue | None = None
    continue_on_error: bool | None = None
    collision_policy: str | None = None
    width: int | None = None
    fps: int | None = None
    colors: int | None = None
    allow_upscale: bool | None = None
    schema_version: int = 1
    warnings: list[str] = field(default_factory=list)


def _manifest_error(
    message: str, field_path: str, *, clip_index: int | None = None
) -> errors.EngineError:
    return errors.EngineError(
        errors.INVALID_MANIFEST,
        message,
        exit_code=errors.EXIT_INVALID_USAGE,
        status=errors.STATUS_VALIDATION_FAILED,
        stage="manifest",
        field=field_path,
        clip_index=clip_index,
    )


def _positive_int(value: Any, field_path: str, clip_index: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        # Allow numeric strings from CSV.
        try:
            number = int(str(value).strip())
        except (ValueError, TypeError) as exc:
            raise _manifest_error(
                f"{field_path} must be a positive integer.", field_path, clip_index=clip_index
            ) from exc
    else:
        number = value
    if number <= 0:
        raise _manifest_error(
            f"{field_path} must be a positive integer.", field_path, clip_index=clip_index
        )
    return number


def _clip_from_fields(
    index: int,
    *,
    name: str | None,
    start: Any,
    end: Any,
    duration: Any,
    profile: Any,
    width: Any,
    fps: Any,
    colors: Any,
    loop: Any,
    field_prefix: str,
) -> models.ClipSpec:
    if start is None or (isinstance(start, str) and start.strip() == ""):
        raise _manifest_error(
            "Clip is missing required 'start'.", f"{field_prefix}.start", clip_index=index
        )

    has_end = end is not None and not (isinstance(end, str) and end.strip() == "")
    has_dur = duration is not None and not (isinstance(duration, str) and duration.strip() == "")

    if not has_end and not has_dur:
        raise _manifest_error(
            "Clip must provide exactly one of 'end' or 'duration'.",
            f"{field_prefix}",
            clip_index=index,
        )

    try:
        start_ms = parse_timestamp(start, field_path=f"{field_prefix}.start")
    except errors.EngineError as exc:
        exc.clip_index = index
        raise

    end_ms: int | None = None
    if has_end:
        try:
            end_ms = parse_timestamp(end, field_path=f"{field_prefix}.end")
        except errors.EngineError as exc:
            exc.clip_index = index
            raise
    dur_end_ms: int | None = None
    if has_dur:
        try:
            dur_ms = parse_duration(duration, field_path=f"{field_prefix}.duration")
        except errors.EngineError as exc:
            exc.clip_index = index
            raise
        dur_end_ms = start_ms + dur_ms

    if has_end and has_dur:
        # Allowed only when both resolve to the same end timestamp (FR-005).
        if end_ms != dur_end_ms:
            raise _manifest_error(
                "Clip must not provide both 'end' and 'duration' unless they "
                f"resolve to the same end (end={end_ms} ms vs "
                f"start+duration={dur_end_ms} ms).",
                f"{field_prefix}",
                clip_index=index,
            )
        resolved_end = end_ms
    elif has_end:
        resolved_end = end_ms
    else:
        resolved_end = dur_end_ms
    # Exactly one of end/duration was provided (checked above), so an end is set.
    assert resolved_end is not None

    clip_name = None
    if name is not None and str(name).strip() != "":
        clip_name = str(name).strip()

    clip_profile = None
    if profile is not None and str(profile).strip() != "":
        p = str(profile).strip()
        if p not in models.VALID_PROFILE_NAMES:
            raise _manifest_error(
                f"Unknown profile {p!r}.",
                f"{field_prefix}.profile",
                clip_index=index,
            )
        clip_profile = p

    clip_loop = None
    if loop is not None and str(loop).strip() != "":
        try:
            clip_loop = models.parse_loop(loop, field_path=f"{field_prefix}.loop")
        except errors.EngineError as exc:
            exc.clip_index = index
            raise

    clip_width = _positive_int(width, f"{field_prefix}.width", index) if _present(width) else None
    clip_fps = _positive_int(fps, f"{field_prefix}.fps", index) if _present(fps) else None
    clip_colors = (
        _positive_int(colors, f"{field_prefix}.colors", index) if _present(colors) else None
    )
    if clip_colors is not None and clip_colors > 256:
        raise _manifest_error(
            "colors must be between 1 and 256.", f"{field_prefix}.colors", clip_index=index
        )

    return models.ClipSpec(
        index=index,
        start_ms=start_ms,
        end_ms=resolved_end,
        name=clip_name,
        profile=clip_profile,
        width=clip_width,
        fps=clip_fps,
        colors=clip_colors,
        loop=clip_loop,
    )


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and value.strip() == "")


def parse_json_manifest(raw: str, *, source_path: str | None = None) -> Manifest:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _manifest_error(
            f"Malformed manifest JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}.",
            "$",
        ) from exc
    if not isinstance(data, dict):
        raise _manifest_error("Manifest must be a JSON object.", "$")

    warnings: list[str] = []
    for key in data:
        if key not in _JSON_TOP_KNOWN:
            warnings.append(f"Unknown manifest field: {key}")

    # Required top-level fields.
    if "schemaVersion" not in data:
        raise _manifest_error("Manifest missing required 'schemaVersion'.", "schemaVersion")
    sv = data["schemaVersion"]
    if not isinstance(sv, int) or isinstance(sv, bool):
        raise _manifest_error("schemaVersion must be an integer.", "schemaVersion")
    if sv != 1:
        warnings.append(f"Unrecognized manifest schemaVersion {sv}; expected 1.")

    if "input" not in data or not isinstance(data["input"], str) or data["input"].strip() == "":
        raise _manifest_error("Manifest missing required 'input' (string).", "input")

    if "clips" not in data or not isinstance(data["clips"], list):
        raise _manifest_error("Manifest missing required 'clips' array.", "clips")
    if len(data["clips"]) == 0:
        raise _manifest_error("Manifest 'clips' array must not be empty.", "clips")

    top_profile = _opt_profile(data.get("profile"), "profile")
    top_loop = _opt_loop(data.get("loop"), "loop")
    top_collision = _opt_collision(data.get("collisionPolicy"), "collisionPolicy")
    top_continue = _opt_bool(data.get("continueOnError"), "continueOnError")
    top_allow_upscale = _opt_bool(data.get("allowUpscale"), "allowUpscale")
    top_width = _positive_int(data["width"], "width", None) if _present(data.get("width")) else None
    top_fps = _positive_int(data["fps"], "fps", None) if _present(data.get("fps")) else None
    top_colors = (
        _positive_int(data["colors"], "colors", None) if _present(data.get("colors")) else None
    )
    output_dir = data.get("outputDirectory")
    if output_dir is not None and (not isinstance(output_dir, str) or output_dir == ""):
        raise _manifest_error("outputDirectory must be a non-empty string.", "outputDirectory")

    clips: list[models.ClipSpec] = []
    for i, clip in enumerate(data["clips"]):
        if not isinstance(clip, dict):
            raise _manifest_error("Each clip must be an object.", f"clips[{i}]", clip_index=i)
        for key in clip:
            if key not in _JSON_CLIP_KNOWN:
                warnings.append(f"Unknown clip field at clips[{i}]: {key}")
        clips.append(
            _clip_from_fields(
                i,
                name=clip.get("name"),
                start=clip.get("start"),
                end=clip.get("end"),
                duration=clip.get("duration"),
                profile=clip.get("profile"),
                width=clip.get("width"),
                fps=clip.get("fps"),
                colors=clip.get("colors"),
                loop=clip.get("loop"),
                field_prefix=f"clips[{i}]",
            )
        )

    return Manifest(
        input=data["input"],
        clips=clips,
        output_directory=output_dir,
        profile=top_profile,
        loop=top_loop,
        continue_on_error=top_continue,
        collision_policy=top_collision,
        width=top_width,
        fps=top_fps,
        colors=top_colors,
        allow_upscale=top_allow_upscale,
        schema_version=sv,
        warnings=warnings,
    )


def parse_csv_manifest(raw: str) -> Manifest:
    warnings: list[str] = []
    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    if not rows:
        raise _manifest_error("CSV manifest is empty.", "$")

    # Header: case-insensitive, whitespace-trimmed.
    header = [h.strip().lower() for h in rows[0]]
    if not any(header):
        raise _manifest_error("CSV manifest has no header row.", "$")

    for col in header:
        if col and col not in _CSV_KNOWN:
            warnings.append(f"Unknown CSV column: {col}")
    if "start" not in header:
        raise _manifest_error("CSV manifest must include a 'start' column.", "start")
    if "end" not in header and "duration" not in header:
        raise _manifest_error("CSV manifest must include an 'end' or 'duration' column.", "$")

    def get(row: list[str], col: str) -> str | None:
        if col not in header:
            return None
        idx = header.index(col)
        if idx >= len(row):
            return None
        return row[idx]

    clips: list[models.ClipSpec] = []
    clip_index = 0
    for line_no, row in enumerate(rows[1:], start=2):
        # Ignore empty rows (all cells blank).
        if not any(cell.strip() for cell in row):
            continue
        clips.append(
            _clip_from_fields(
                clip_index,
                name=get(row, "name"),
                start=get(row, "start"),
                end=get(row, "end"),
                duration=get(row, "duration"),
                profile=get(row, "profile"),
                width=get(row, "width"),
                fps=get(row, "fps"),
                colors=get(row, "colors"),
                loop=get(row, "loop"),
                field_prefix=f"row {line_no}",
            )
        )
        clip_index += 1

    if not clips:
        raise _manifest_error("CSV manifest contains no data rows.", "$")

    return Manifest(input=None, clips=clips, warnings=warnings)


def load_manifest_file(path: str) -> Manifest:
    if not os.path.exists(path):
        raise errors.EngineError(
            errors.INVALID_MANIFEST,
            f"Manifest file not found: {path}",
            exit_code=errors.EXIT_INPUT_NOT_FOUND,
            status=errors.STATUS_FAILED,
            stage="manifest",
            remediation="Provide a valid path to a JSON or CSV manifest.",
        )
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            raw = fh.read()
    except OSError as exc:
        raise errors.EngineError(
            errors.INVALID_MANIFEST,
            f"Cannot read manifest file {path}: {exc}",
            exit_code=errors.EXIT_INPUT_NOT_FOUND,
            status=errors.STATUS_FAILED,
            stage="manifest",
        ) from exc
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return parse_json_manifest(raw, source_path=path)
    if ext == ".csv":
        return parse_csv_manifest(raw)
    # Fall back to sniffing: JSON starts with '{'.
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        return parse_json_manifest(raw, source_path=path)
    return parse_csv_manifest(raw)


# --- small optional-field helpers -----------------------------------------


def _opt_profile(value: Any, field_path: str) -> str | None:
    if not _present(value):
        return None
    if not isinstance(value, str) or value not in models.VALID_PROFILE_NAMES:
        raise _manifest_error(f"{field_path} must be a valid profile name.", field_path)
    return value


def _opt_loop(value: Any, field_path: str) -> models.LoopValue | None:
    if not _present(value):
        return None
    return models.parse_loop(value, field_path=field_path)


def _opt_collision(value: Any, field_path: str) -> str | None:
    if not _present(value):
        return None
    # "ask" is a skill-layer/config value only; a manifest drives the
    # non-interactive engine directly, so it must name a concrete engine policy.
    if not isinstance(value, str) or value not in models.VALID_MANIFEST_COLLISION_POLICIES:
        raise _manifest_error(
            f"{field_path} must be one of {sorted(models.VALID_MANIFEST_COLLISION_POLICIES)}.",
            field_path,
        )
    return value


def _opt_bool(value: Any, field_path: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _manifest_error(f"{field_path} must be a boolean.", field_path)
    return value
