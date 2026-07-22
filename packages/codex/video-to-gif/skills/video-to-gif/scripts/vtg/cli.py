"""Command-line interface and orchestration (spec sections 12, 13, 14, 15).

Thin argument parsing plus deterministic, non-interactive orchestration. With
``--json`` a single final JSON document is written to stdout and progress events
go to stderr as JSON Lines. Exit codes follow section 14.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from types import FrameType
from typing import Any, NoReturn

from . import (
    __version__,
    cleanup,
    dependencies,
    errors,
    ffmpeg,
    manifests,
    models,
    naming,
    paths,
    transforms,
)
from . import config as config_mod
from . import remote as remote_mod
from .inspect import inspect_source
from .models import BUILTIN_PROFILES, ClipSpec, EffectiveSettings, SourceInfo
from .progress import ProgressReporter
from .timestamps import parse_timestamp

SCHEMA_VERSION = 1

# Global cancellation flag, set by SIGINT/SIGTERM handlers (section 16).
_CANCEL_EVENT = threading.Event()


# ---------------------------------------------------------------------------
# Planning data structures
# ---------------------------------------------------------------------------


@dataclass
class PlannedClip:
    clip: ClipSpec
    filename: str
    dest_path: str
    settings: EffectiveSettings
    action: str  # 'write' | 'overwrite' | 'skip' | 'collision'
    collided: bool


# Settings that a still frame cannot express (FR-029). Supplying any of them to
# `preview` is accepted, changes nothing, and produces one warning.
_PREVIEW_IGNORED_FLAGS: tuple[tuple[str, str], ...] = (
    ("speed", "speed"),
    ("fps", "fps"),
    ("loop", "loop"),
    ("colors", "colors"),
    ("dither", "dither"),
    ("bayer_scale", "bayerScale"),
)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:  # pragma: no cover - argparse path
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(errors.EXIT_INVALID_USAGE)


# Value-taking transformation flags whose value may legitimately begin with a
# dash (a negative number or a crop rectangle with a negative offset).
_DASH_VALUE_FLAGS = frozenset({"--crop", "--width", "--height", "--speed", "--bayer-scale"})
_NEGATIVE_VALUE_RE = re.compile(r"^-[0-9]")


def normalize_argv(argv: list[str]) -> list[str]:
    """Rewrite ``--flag -1...`` as ``--flag=-1...`` for transformation flags.

    argparse treats any token starting with a dash as an option, so a negative
    transformation value would fail as a usage error (exit 2) instead of
    reaching validation. FR-025 through FR-027 require a negative value to be
    rejected during preflight with its own error code and exit code 6, so the
    pair is joined here. Only a token matching ``-<digit>`` is joined, so a real
    flag is never swallowed and a genuine "missing value" stays a usage error.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if (
            token in _DASH_VALUE_FLAGS
            and i + 1 < len(argv)
            and _NEGATIVE_VALUE_RE.match(argv[i + 1])
        ):
            out.append(f"{token}={argv[i + 1]}")
            i += 2
            continue
        out.append(token)
        i += 1
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="video_to_gif.py",
        description="Convert explicit timestamp ranges from local videos into optimized GIFs.",
    )
    parser.add_argument("--version", action="version", version=f"video-to-gif {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="Emit a single JSON result on stdout.")
        p.add_argument("--debug", action="store_true", help="Show diagnostic details on failure.")
        p.add_argument(
            "--no-progress", action="store_true", help="Disable JSON Lines progress on stderr."
        )

    def add_remote(p: argparse.ArgumentParser) -> None:
        # Opt-in remote source acquisition (spec section 12.8, FR-018..022).
        p.add_argument(
            "--allow-remote",
            action="store_true",
            help="Permit remote (http/https) source acquisition for this run (FR-018).",
        )
        p.add_argument(
            "--keep-remote-source",
            action="store_true",
            help="Retain the downloaded remote source and report its path (FR-020).",
        )
        p.add_argument(
            "--remote-adapter",
            choices=["ytdlp"],
            help="Acquire a video-page URL through the optional yt-dlp adapter (FR-022).",
        )
        p.add_argument(
            "--allow-insecure-http",
            action="store_true",
            help="Acknowledge and permit an unencrypted http remote source (SEC-013).",
        )
        p.add_argument(
            "--allow-remote-address",
            action="append",
            metavar="IP",
            help="Explicitly approve a private/loopback IP for SSRF checks (SEC-014); repeatable.",
        )

    def add_transforms(p: argparse.ArgumentParser) -> None:
        # Transformation flags (spec section 12.10, FR-025..FR-028). Values are
        # accepted as text and parsed by vtg.transforms during preflight so an
        # invalid value produces the specific error code with exit 6 rather than
        # argparse's generic usage error (SEC-018).
        p.add_argument(
            "--crop",
            metavar="X:Y:W:H",
            help="Crop rectangle in orientation-normalized source pixels (FR-025).",
        )
        p.add_argument(
            "--width", metavar="PIXELS", help="Maximum output width, 2 to 8192 (FR-026)."
        )
        p.add_argument(
            "--height", metavar="PIXELS", help="Maximum output height, 2 to 8192 (FR-026)."
        )
        p.add_argument(
            "--speed",
            metavar="MULTIPLIER",
            help="Playback speed multiplier, 0.25 to 4.0 (FR-027).",
        )
        p.add_argument(
            "--dither",
            metavar="MODE",
            help="Dither mode: " + ", ".join(transforms.DITHER_MODES) + " (FR-028).",
        )
        p.add_argument(
            "--bayer-scale", metavar="N", help="Bayer scale 0 to 5, for dither=bayer (FR-028)."
        )

    p_doctor = sub.add_parser("doctor", help="Check the environment and dependencies.")
    p_doctor.add_argument(
        "--output-directory", help="Optionally check this output directory is writable."
    )
    add_common(p_doctor)

    p_inspect = sub.add_parser("inspect", help="Inspect source media with ffprobe.")
    p_inspect.add_argument("--input", required=True, help="Path to the source video or URL.")
    p_inspect.add_argument("--config", help="Explicit config file path.")
    add_remote(p_inspect)
    add_common(p_inspect)

    p_create = sub.add_parser("create", help="Create one GIF from a timestamp range.")
    p_create.add_argument("--input", required=True)
    p_create.add_argument("--start", required=True)
    p_create.add_argument("--end")
    p_create.add_argument("--duration")
    p_create.add_argument("--profile", choices=sorted(models.VALID_PROFILE_NAMES))
    p_create.add_argument("--output-directory")
    p_create.add_argument("--output-name", help="Bare output filename (no path separators).")
    p_create.add_argument("--collision-policy", choices=sorted(models.VALID_COLLISION_POLICIES))
    p_create.add_argument("--loop", help="forever, once, or an integer >= 1.")
    p_create.add_argument("--fps", type=int)
    p_create.add_argument("--colors", type=int)
    p_create.add_argument("--allow-upscale", action="store_true")
    p_create.add_argument("--allow-outside-project", action="store_true")
    p_create.add_argument(
        "--invalid-timestamp-policy",
        choices=sorted(models.VALID_INVALID_TIMESTAMP_POLICIES),
        default="fail",
    )
    p_create.add_argument("--config", help="Explicit config file path.")
    add_transforms(p_create)
    add_remote(p_create)
    add_common(p_create)

    p_batch = sub.add_parser("batch", help="Create multiple GIFs from a manifest.")
    p_batch.add_argument("--manifest", required=True)
    p_batch.add_argument("--input", help="Override the manifest source path.")
    p_batch.add_argument("--profile", choices=sorted(models.VALID_PROFILE_NAMES))
    p_batch.add_argument("--output-directory")
    p_batch.add_argument("--collision-policy", choices=sorted(models.VALID_COLLISION_POLICIES))
    p_batch.add_argument("--allow-upscale", action="store_true")
    p_batch.add_argument("--allow-outside-project", action="store_true")
    p_batch.add_argument("--dry-run", action="store_true", help="Preflight only; do not encode.")
    p_batch.add_argument(
        "--invalid-timestamp-policy",
        choices=sorted(models.VALID_INVALID_TIMESTAMP_POLICIES),
        default="fail",
    )
    p_batch.add_argument("--config", help="Explicit config file path.")
    add_transforms(p_batch)
    add_remote(p_batch)
    add_common(p_batch)

    # Preview frames (spec section 12.9, FR-029).
    p_preview = sub.add_parser(
        "preview", help="Extract a single still PNG instead of producing a GIF."
    )
    p_preview.add_argument("--input", help="Path to the source video or URL.")
    p_preview.add_argument("--manifest", help="Produce one still per clip at that clip's start.")
    p_preview.add_argument("--at", help="Timestamp of the frame to extract (FR-004 format).")
    p_preview.add_argument("--profile", choices=sorted(models.VALID_PROFILE_NAMES))
    p_preview.add_argument("--output-directory")
    p_preview.add_argument("--output-name", help="Bare .png filename (no path separators).")
    p_preview.add_argument("--collision-policy", choices=sorted(models.VALID_COLLISION_POLICIES))
    p_preview.add_argument("--loop", help="Accepted for parity with create; ignored (FR-029).")
    p_preview.add_argument("--fps", type=int)
    p_preview.add_argument("--colors", type=int)
    p_preview.add_argument("--allow-upscale", action="store_true")
    p_preview.add_argument("--allow-outside-project", action="store_true")
    p_preview.add_argument("--dry-run", action="store_true", help="Preflight only; write nothing.")
    p_preview.add_argument("--config", help="Explicit config file path.")
    add_transforms(p_preview)
    add_remote(p_preview)
    add_common(p_preview)

    p_vc = sub.add_parser("validate-config", help="Validate a configuration file.")
    p_vc.add_argument("--config", required=True)
    add_common(p_vc)

    p_vm = sub.add_parser("validate-manifest", help="Validate a manifest file.")
    p_vm.add_argument("--manifest", required=True)
    add_common(p_vm)

    return parser


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def configure_output_encoding() -> None:
    """Force UTF-8 on stdout/stderr before anything is written (spec section 13.5).

    ``sys.stdout``/``sys.stderr`` default to the host locale encoding, which on
    Windows is the console codepage (cp1252/cp437 on the CI runners). Writing any
    character outside that codepage -- a Unicode digit echoed back in a validation
    error, a CJK/Cyrillic/emoji source filename, a non-ASCII clip name from a
    manifest -- then raises ``UnicodeEncodeError`` inside the writer. That escapes
    the engine's error contract entirely: the process dies with exit code 1, which
    section 14 deliberately does not define, and stdout carries no structured
    result at all, leaving the agent layer blind.

    Pinning both streams to UTF-8 makes the encoding independent of the host
    locale on every supported platform (section 6.1). ``errors="backslashreplace"``
    guarantees the call can never itself raise: a path that survived
    ``os.fsdecode`` on POSIX may contain lone surrogates, which even UTF-8 cannot
    encode strictly.

    Streams replaced by in-process test doubles (``io.StringIO``) have no
    ``reconfigure``; they are already text-native and need no adjustment, so the
    missing attribute is not an error.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):  # pragma: no cover - detached stream
            continue


def _emit(result: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        # ensure_ascii=True (spec section 13.5): the contract document is pure
        # ASCII, so it survives any downstream consumer, pipe or redirection
        # whatever their encoding. Non-ASCII becomes \uXXXX escapes, which
        # json.loads restores to the identical string.
        sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
        sys.stdout.flush()
    else:
        _emit_human(result)


def _emit_human(result: dict[str, Any]) -> None:
    command = result.get("command")
    status = result.get("status")
    if command == "doctor":
        health = "healthy" if result.get("healthy") else "problems detected"
        print(f"doctor: {health}")
        for c in result.get("checks", []):
            mark = "ok" if c["ok"] else "FAIL"
            print(f"  [{mark}] {c['name']}: {c['detail']}")
        return
    if result.get("error"):
        err = result["error"]
        sys.stderr.write(f"{status}: {err.get('code')}: {err.get('message')}\n")
        if err.get("remediation"):
            sys.stderr.write(f"  hint: {err['remediation']}\n")
        return
    summary = result.get("summary", {})
    if command == "preview":
        out = result.get("outputDirectory", "./output")
        if status == "dry_run":
            print(
                f"dry run: {summary.get('planned', 0)} preview(s) planned, "
                f"{summary.get('collisions', 0)} collision(s)."
            )
        else:
            msg = f"Extracted {summary.get('previews', 0)} preview frame(s) in {out}"
            if summary.get("failed"):
                msg += f"; {summary['failed']} failed"
            print(msg + ".")
        return
    if command in ("create", "batch"):
        out = result.get("outputDirectory", "./output")
        created = summary.get("created", 0)
        failed = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)
        if status == "dry_run":
            print(
                f"dry run: {summary.get('planned', 0)} clip(s) planned, "
                f"{summary.get('collisions', 0)} collision(s)."
            )
        else:
            msg = f"Created {created} GIF(s) in {out}"
            extra = []
            if failed:
                extra.append(f"{failed} failed")
            if skipped:
                extra.append(f"{skipped} skipped")
            if extra:
                msg += "; " + ", ".join(extra)
            print(msg + ".")
        return
    print(json.dumps(result, indent=2))


def _error_result(command: str, exc: errors.EngineError, warnings: list[str]) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "status": exc.status,
        "error": exc.to_dict(),
        "warnings": warnings or [],
    }


# ---------------------------------------------------------------------------
# Remote source acquisition (spec section 12.8, FR-018..023)
# ---------------------------------------------------------------------------


class _JobCleanup:
    """Tracks a downloaded remote source so it is removed after the job.

    A retained source (keepRemoteSource / --keep-remote-source) is preserved like
    a completed output; otherwise the whole secure temp dir is removed on success,
    failure, or cancellation (spec section 16 / FR-020).
    """

    def __init__(self) -> None:
        self.remote: remote_mod.RemoteResult | None = None

    def cleanup(self) -> None:
        r = self.remote
        if r is not None and not r.retained:
            cleanup.remove_paths([r.temp_dir])


def _acquire_remote(
    raw: str,
    args: argparse.Namespace,
    cfg: config_mod.Config,
    reporter: ProgressReporter,
    job: _JobCleanup,
) -> str:
    """Gate (FR-018) then acquire a remote URL; return the local download path."""
    remote_mod.ensure_remote_permitted(
        raw, cfg.remote_sources, bool(getattr(args, "allow_remote", False))
    )
    result = remote_mod.acquire_remote_source(
        raw,
        adapter=getattr(args, "remote_adapter", None),
        allow_insecure_http=bool(getattr(args, "allow_insecure_http", False)),
        max_download_bytes=cfg.max_download_bytes,
        max_download_seconds=cfg.max_download_seconds,
        reporter=reporter,
        cancel_event=_CANCEL_EVENT,
        approved_addresses=frozenset(getattr(args, "allow_remote_address", None) or []),
    )
    result.retained = bool(getattr(args, "keep_remote_source", False)) or cfg.keep_remote_source
    job.remote = result
    # The download is untrusted LOCAL media from here on (SEC-012).
    paths.ensure_source_readable(result.local_path)
    return result.local_path


def _resolve_source(
    raw: str,
    args: argparse.Namespace,
    *,
    project_root: str,
    cfg: config_mod.Config,
    reporter: ProgressReporter,
    job: _JobCleanup,
) -> str:
    """Resolve a source string to a local path, downloading it first if a URL."""
    if paths.url_scheme(raw) is not None:
        return _acquire_remote(raw, args, cfg, reporter, job)
    source_path = paths.resolve_source_path(raw, project_root=project_root)
    paths.ensure_source_readable(source_path)
    return source_path


def _annotate_remote(result: dict[str, Any], job: _JobCleanup) -> None:
    """Add the additive remote block and redact any leaked source path (FR-023)."""
    r = job.remote
    if r is None:
        return
    result["remoteSource"] = r.to_public()
    src = result.get("source")
    if isinstance(src, dict):
        # Never expose the internal temp path; show the redacted URL instead.
        src["path"] = r.redacted_url
    if r.warnings:
        existing = result.get("warnings") or []
        result["warnings"] = list(dict.fromkeys([*existing, *r.warnings]))


def _emit_job(result: dict[str, Any], args: argparse.Namespace, job: _JobCleanup) -> None:
    _annotate_remote(result, job)
    _emit(result, getattr(args, "json", False))


# ---------------------------------------------------------------------------
# Settings resolution (precedence: CLI > manifest > config > default)
# ---------------------------------------------------------------------------


def _first(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _make_settings_resolver(
    source: SourceInfo,
    *,
    top_profile: str,
    top_width: int | None,
    top_fps: int | None,
    top_colors: int | None,
    top_loop: models.LoopValue,
    allow_upscale: bool,
    cli_transforms: transforms.TransformSpec | None = None,
    manifest_transforms: transforms.TransformSpec | None = None,
    config_transforms: transforms.TransformSpec | None = None,
    warnings: list[str] | None = None,
) -> Callable[[ClipSpec], EffectiveSettings]:
    """Build the per-clip settings resolver.

    Transformation precedence is the FR-024 order, highest first: the clip-level
    manifest field, the command-line flag, the top-level manifest field, project
    configuration, then the built-in default. This deliberately ranks a
    clip-level field above a batch-wide flag (section 9.3).
    """
    # Legacy callers pass the merged explicit width as ``top_width``; treat it as
    # the command-line level so the precedence chain stays a single code path.
    cli_level = (
        cli_transforms if cli_transforms is not None else transforms.TransformSpec(width=top_width)
    )

    def resolve(clip: ClipSpec) -> EffectiveSettings:
        profile_name = clip.profile or top_profile
        builtin = BUILTIN_PROFILES.get(profile_name)
        base_fps = builtin.fps if builtin else None
        base_colors = builtin.max_colors if builtin else None
        tx = transforms.merge_transforms(
            clip.transform_spec, cli_level, manifest_transforms, config_transforms
        )
        target_fps = _first(clip.fps, top_fps, base_fps)
        colors = _first(clip.colors, top_colors, base_colors)
        loop = _first(clip.loop, top_loop) or "forever"
        try:
            return ffmpeg.resolve_effective_settings(
                source,
                max_width=builtin.max_width if builtin else None,
                target_fps=target_fps,
                colors=colors,
                loop=loop,
                allow_upscale=allow_upscale,
                profile_name=profile_name,
                crop=tx.crop,
                explicit_width=tx.width,
                explicit_height=tx.height,
                speed=tx.speed,
                dither=tx.dither,
                bayer_scale=tx.bayer_scale,
                warnings=warnings,
            )
        except errors.EngineError as exc:
            # Attribute a transformation failure to the clip it came from.
            if exc.clip_index is None:
                exc.clip_index = clip.index
            raise

    return resolve


# ---------------------------------------------------------------------------
# Clip validation (FR-006, FR-007)
# ---------------------------------------------------------------------------


def validate_and_filter(
    clips: list[ClipSpec],
    duration_ms: int,
    policy: str,
) -> tuple[list[ClipSpec], list[dict[str, Any]]]:
    """Apply timestamp validation and the invalid-timestamp policy.

    Returns (surviving clips, skipped records). Raises on ``fail`` policy.
    """
    valid: list[ClipSpec] = []
    skipped: list[dict[str, Any]] = []

    for clip in clips:
        non_clampable: str | None = None
        clampable = False
        if clip.start_ms < 0:
            non_clampable = "start is negative"
        elif clip.start_ms >= duration_ms:
            non_clampable = (
                f"start ({clip.start_ms} ms) is at or beyond source duration ({duration_ms} ms)"
            )
        elif clip.end_ms <= clip.start_ms:
            non_clampable = f"end ({clip.end_ms} ms) is not after start ({clip.start_ms} ms)"
        elif clip.end_ms > duration_ms:
            clampable = True

        if non_clampable is None and not clampable:
            valid.append(clip)
            continue

        # There is a problem with this clip.
        reason = non_clampable or (
            f"end ({clip.end_ms} ms) is beyond source duration ({duration_ms} ms)"
        )
        if policy == "clamp" and clampable and non_clampable is None:
            # replace() carries every clip field forward, including the v0.3.0
            # transformation fields, so a clamped clip keeps its settings.
            valid.append(replace(clip, end_ms=duration_ms))
            continue
        if policy == "skip":
            skipped.append({"clipIndex": clip.index, "reason": reason, "stage": "validate"})
            continue
        # fail (default) or clamp on a non-clampable error.
        raise errors.EngineError(
            errors.INVALID_TIMESTAMP,
            f"Clip {clip.index} has an invalid timestamp range: {reason}.",
            exit_code=errors.EXIT_INVALID_TIMESTAMP,
            status=errors.STATUS_VALIDATION_FAILED,
            stage="validate",
            clip_index=clip.index,
            remediation="Use --invalid-timestamp-policy skip or clamp, or correct the range.",
        )

    return valid, skipped


# ---------------------------------------------------------------------------
# Output planning (FR-011, FR-012, section 15.1)
# ---------------------------------------------------------------------------


def plan_outputs(
    clips: list[ClipSpec],
    source: SourceInfo,
    *,
    output_dir: str,
    collision_policy: str,
    resolve_settings: Callable[[ClipSpec], EffectiveSettings],
    namer: Callable[[ClipSpec, str], str] | None = None,
) -> list[PlannedClip]:
    """Resolve settings, names, and collisions for every clip (15.1 steps 9-13).

    ``namer`` overrides the default ``.gif`` naming; the preview command supplies
    the FR-029 ``.png`` naming through it.
    """
    source_stem = os.path.splitext(os.path.basename(source.path))[0]
    planned: list[PlannedClip] = []
    reserved: set = set()

    def exists(fn: str) -> bool:
        return fn.lower() in reserved or os.path.exists(os.path.join(output_dir, fn))

    for clip in clips:
        settings = resolve_settings(clip)
        if namer is not None:
            base_fn = namer(clip, source_stem)
        elif clip.name:
            base_fn = naming.sanitize_output_name(clip.name)
        else:
            base_fn = naming.default_output_name(source_stem, clip.start_ms, clip.end_ms)

        collided = exists(base_fn)
        action = "write"
        final_fn = base_fn
        if collided:
            if collision_policy in ("fail", "ask"):
                action = "collision"
            elif collision_policy == "overwrite":
                action = "overwrite"
            elif collision_policy == "skip":
                action = "skip"
            elif collision_policy == "unique":
                final_fn = naming.unique_name(base_fn, exists)
                action = "write"

        dest = paths.resolve_within_directory(output_dir, final_fn)
        if action not in ("skip", "collision"):
            reserved.add(final_fn.lower())
        planned.append(PlannedClip(clip, final_fn, dest, settings, action, collided))

    return planned


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = dependencies.run_doctor(getattr(args, "output_directory", None))
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "command": "doctor",
        "status": errors.STATUS_SUCCESS if report["healthy"] else errors.STATUS_DEPENDENCY_MISSING,
        "healthy": report["healthy"],
        "checks": report["checks"],
        "ffmpeg": report["ffmpeg"],
        "ffprobe": report["ffprobe"],
        "ytdlp": report["ytdlp"],
        "installGuidance": report["installGuidance"],
        "warnings": [],
    }
    _emit(result, args.json)
    return errors.EXIT_SUCCESS if report["healthy"] else errors.EXIT_DEPENDENCY_MISSING


def _cmd_inspect(args: argparse.Namespace) -> int:
    reporter = ProgressReporter(enabled=not args.no_progress)
    job = _JobCleanup()
    try:
        project_root = paths.resolve_project_root(os.getcwd())
        cfg = config_mod.resolve_config(
            explicit_path=getattr(args, "config", None), project_root=project_root
        )
        tools = dependencies.require_ffmpeg_tools()
        # inspect on a URL acquires the source first (network-isolated ffprobe,
        # SEC-010 / section 12.8).
        source_path = _resolve_source(
            args.input, args, project_root=project_root, cfg=cfg, reporter=reporter, job=job
        )
        source = inspect_source(tools["ffprobe"], source_path)
        result = {
            "schemaVersion": SCHEMA_VERSION,
            "command": "inspect",
            "status": errors.STATUS_SUCCESS,
            "source": source.to_public(),
            "warnings": list(source.warnings),
        }
        _emit_job(result, args, job)
        return errors.EXIT_SUCCESS
    finally:
        job.cleanup()


def _clip_result(planned: PlannedClip, conv: ffmpeg.ConversionResult) -> dict[str, Any]:
    clip = planned.clip
    settings = planned.settings
    return {
        "clipIndex": clip.index,
        "name": clip.name,
        "path": _display_path(conv.path),
        "startMs": clip.start_ms,
        "endMs": clip.end_ms,
        # durationMs remains the selected SOURCE range duration; outputDurationMs
        # is the duration of the generated GIF after speed retiming (FR-030).
        "durationMs": clip.duration_ms,
        "outputDurationMs": transforms.output_duration_ms(clip.duration_ms, settings.speed),
        "width": conv.width,
        "height": conv.height,
        "fps": conv.fps,
        "sizeBytes": conv.size_bytes,
        "transformations": settings.transformations_public(),
    }


def _preview_result_entry(planned: PlannedClip, prev: ffmpeg.PreviewResult) -> dict[str, Any]:
    """Serialize one entry of the ``previews`` array (section 13.4, FR-030)."""
    clip = planned.clip
    return {
        "clipIndex": clip.index,
        "name": clip.name,
        "path": _display_path(prev.path),
        "atMs": prev.at_ms,
        "width": prev.width,
        "height": prev.height,
        "sizeBytes": prev.size_bytes,
        "transformations": planned.settings.transformations_public(still_frame=True),
    }


def _display_path(path: str) -> str:
    try:
        rel = os.path.relpath(path, os.getcwd())
        if not rel.startswith(".."):
            # Normalize to forward slashes so the structured `path`/`outputDirectory`
            # contract (spec §13) is portable and deterministic across platforms;
            # Windows os.path.relpath uses '\\', which would otherwise leak into the
            # JSON. This is a no-op on POSIX where os.sep is already '/'.
            rel = rel.replace(os.sep, "/")
            return "./" + rel if not rel.startswith("./") else rel
    except ValueError:
        pass
    return path


def _run_conversions(
    planned: list[PlannedClip],
    source: SourceInfo,
    cfg: config_mod.Config,
    tools: dict[str, str],
    reporter: ProgressReporter,
    *,
    continue_on_error: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total = len(planned)

    for i, planned_clip in enumerate(planned):
        clip = planned_clip.clip
        if planned_clip.action == "skip":
            skipped.append({"clipIndex": clip.index, "reason": "output exists (skip policy)"})
            reporter.clip_skipped(clip.index, "collision-skip")
            continue
        if _CANCEL_EVENT.is_set():
            raise errors.CancelledError(clip_index=clip.index)

        reporter.clip_started(clip.index, total, clip.name)
        try:
            conv = ffmpeg.convert_clip(
                tools["ffmpeg"],
                source,
                start_ms=clip.start_ms,
                duration_ms=clip.duration_ms,
                settings=planned_clip.settings,
                dest_path=planned_clip.dest_path,
                output_dir=os.path.dirname(planned_clip.dest_path),
                timeout_seconds=cfg.max_clip_processing_seconds,
                max_temp_bytes=cfg.max_temporary_bytes,
                keep_temporary_files=cfg.keep_temporary_files,
                clip_index=clip.index,
                cancel_event=_CANCEL_EVENT,
                reporter=reporter,
            )
        except errors.CancelledError:
            raise
        except errors.EngineError as exc:
            failed.append(exc.to_dict())
            reporter.clip_failed(clip.index, exc.code, exc.message)
            if not continue_on_error:
                # Stop-on-error: leave the remaining clips unprocessed, but still
                # report them as skipped so the structured result stays complete.
                # A stop-on-error run with prior successes is a partial batch
                # success (exit 11); with none it is a failure whose code (e.g.
                # RESOURCE_LIMIT_EXCEEDED per SEC-011) is preserved. The section 14
                # / SEC-011 exit-code precedence is applied by _job_exit_code.
                for remaining in planned[i + 1 :]:
                    r_clip = remaining.clip
                    reason = (
                        "output exists (skip policy)"
                        if remaining.action == "skip"
                        else "not processed (stopped after an earlier failure)"
                    )
                    skipped.append({"clipIndex": r_clip.index, "reason": reason})
                    reporter.clip_skipped(r_clip.index, "stopped-on-error")
                break
        else:
            created.append(_clip_result(planned_clip, conv))
            reporter.clip_completed(clip.index, _display_path(conv.path))

    return created, failed, skipped


def _finalize_status(created: list, failed: list, requested: int) -> str:
    if failed and created:
        return errors.STATUS_PARTIAL
    if failed and not created:
        return errors.STATUS_FAILED
    return errors.STATUS_SUCCESS


def _collision_result(
    command: str, planned: list[PlannedClip], output_dir: str, warnings: list[str]
) -> dict[str, Any]:
    collisions = [
        {
            "clipIndex": p.clip.index,
            "name": p.clip.name,
            "path": _display_path(p.dest_path),
        }
        for p in planned
        if p.action == "collision"
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "status": errors.STATUS_COLLISION,
        "outputDirectory": _display_path(output_dir),
        "collisions": collisions,
        "warnings": warnings,
        "summary": {"requested": len(planned), "collisions": len(collisions)},
    }


def _dry_run_result(
    command: str,
    source: SourceInfo,
    planned: list[PlannedClip],
    output_dir: str,
    skipped: list,
    warnings: list[str],
) -> dict[str, Any]:
    plan_entries = []
    collisions = 0
    for p in planned:
        duration_s = p.clip.duration_ms / 1000.0
        est_frames = round(duration_s * p.settings.fps)
        if p.action == "collision":
            collisions += 1
        plan_entries.append(
            {
                "clipIndex": p.clip.index,
                "name": p.clip.name,
                "path": _display_path(p.dest_path),
                "startMs": p.clip.start_ms,
                "endMs": p.clip.end_ms,
                "durationMs": p.clip.duration_ms,
                "width": p.settings.width,
                "height": p.settings.height,
                "fps": p.settings.fps,
                "colors": p.settings.colors,
                "action": p.action,
                "collision": p.collided,
                "estimatedFrames": est_frames,
                "estimatedWorkUnits": est_frames,
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "status": errors.STATUS_DRY_RUN,
        "source": source.to_public(),
        "outputDirectory": _display_path(output_dir),
        "plan": plan_entries,
        "skipped": skipped,
        "warnings": warnings,
        "summary": {
            "requested": len(planned) + len(skipped),
            "planned": len([p for p in planned if p.action != "collision"]),
            "collisions": collisions,
            "skipped": len(skipped),
        },
    }


def _prepare_job(
    args: argparse.Namespace,
    command: str,
    *,
    reporter: ProgressReporter,
    job: _JobCleanup,
) -> dict[str, Any]:
    """Shared preflight for create/batch. Returns a context dict."""
    project_root = paths.resolve_project_root(os.getcwd())
    cfg = config_mod.resolve_config(
        explicit_path=getattr(args, "config", None), project_root=project_root
    )
    warnings: list[str] = list(cfg.warnings)

    # Manifest (batch, and the preview manifest form) vs single clip (create).
    manifest: manifests.Manifest | None = None
    if command == "batch" or (command == "preview" and getattr(args, "manifest", None)):
        manifest = manifests.load_manifest_file(args.manifest)
        warnings.extend(manifest.warnings)

    # Transformation flags are parsed and range-checked here, during preflight,
    # before any FFmpeg process is started (FR-024, section 12.10, SEC-018).
    cli_tx = transforms.parse_cli_transforms(
        crop=getattr(args, "crop", None),
        width=getattr(args, "width", None),
        height=getattr(args, "height", None),
        speed=getattr(args, "speed", None),
        dither=getattr(args, "dither", None),
        bayer_scale=getattr(args, "bayer_scale", None),
    )
    manifest_tx = manifest.transform_spec if manifest else transforms.EMPTY_TRANSFORMS
    config_tx = cfg.transform_spec

    tools = dependencies.require_ffmpeg_tools()

    manifest_input = manifest.input if manifest else None
    raw_source = _first(getattr(args, "input", None), manifest_input)
    if not raw_source:
        raise errors.EngineError(
            errors.INPUT_NOT_FOUND,
            "No source specified. Provide --input or set 'input' in the manifest.",
            exit_code=errors.EXIT_INPUT_NOT_FOUND,
            status=errors.STATUS_FAILED,
            stage="input",
        )
    # A URL is gated (FR-018) and downloaded to a temp dir here; local paths are
    # resolved exactly as in v0.1.0.
    source_path = _resolve_source(
        raw_source, args, project_root=project_root, cfg=cfg, reporter=reporter, job=job
    )
    source = inspect_source(tools["ffprobe"], source_path)
    warnings.extend(source.warnings)

    # Resolve top-level settings via precedence.
    allow_outside = bool(getattr(args, "allow_outside_project", False)) or cfg.allow_outside_project
    top_profile = (
        _first(
            getattr(args, "profile", None),
            manifest.profile if manifest else None,
            cfg.default_profile,
        )
        or "balanced"
    )
    output_dir_raw = _first(
        getattr(args, "output_directory", None),
        manifest.output_directory if manifest else None,
        cfg.output_directory,
    )
    output_dir = paths.resolve_output_directory(
        output_dir_raw, project_root=project_root, allow_outside_project=allow_outside
    )

    collision_policy = (
        _first(
            getattr(args, "collision_policy", None),
            manifest.collision_policy if manifest else None,
            cfg.collision_policy,
        )
        or "fail"
    )
    top_loop = (
        _first(getattr(args, "loop", None), manifest.loop if manifest else None, cfg.loop)
        or "forever"
    )
    if isinstance(top_loop, str) and top_loop not in ("forever",):
        top_loop = models.parse_loop(top_loop)
    continue_on_error = _first(
        manifest.continue_on_error if manifest else None, cfg.continue_on_error
    )
    if continue_on_error is None:
        continue_on_error = True

    top_fps = _first(getattr(args, "fps", None), manifest.fps if manifest else None)
    top_colors = _first(getattr(args, "colors", None), manifest.colors if manifest else None)
    allow_upscale = bool(getattr(args, "allow_upscale", False)) or (
        manifest.allow_upscale if manifest and manifest.allow_upscale else False
    )

    return {
        "project_root": project_root,
        "cfg": cfg,
        "warnings": warnings,
        "manifest": manifest,
        "tools": tools,
        "source": source,
        "output_dir": output_dir,
        "collision_policy": collision_policy,
        "continue_on_error": continue_on_error,
        "cli_transforms": cli_tx,
        "manifest_transforms": manifest_tx,
        "resolver": _make_settings_resolver(
            source,
            top_profile=top_profile,
            top_width=None,
            top_fps=top_fps,
            top_colors=top_colors,
            top_loop=top_loop,
            allow_upscale=allow_upscale,
            cli_transforms=cli_tx,
            manifest_transforms=manifest_tx,
            config_transforms=config_tx,
            warnings=warnings,
        ),
    }


def _build_create_clip(args: argparse.Namespace) -> ClipSpec:
    from .timestamps import parse_duration, parse_timestamp

    has_end = args.end is not None
    has_dur = args.duration is not None
    if not has_end and not has_dur:
        raise errors.EngineError(
            errors.INVALID_CLIP,
            "create requires exactly one of --end or --duration.",
            exit_code=errors.EXIT_INVALID_TIMESTAMP,
            status=errors.STATUS_VALIDATION_FAILED,
            stage="validate",
            remediation="Provide --end or --duration.",
        )
    start_ms = parse_timestamp(args.start, field_path="start")
    end_ms = None
    if has_end:
        end_ms = parse_timestamp(args.end, field_path="end")
    dur_end = None
    if has_dur:
        dur_end = start_ms + parse_duration(args.duration, field_path="duration")
    if has_end and has_dur and end_ms != dur_end:
        raise errors.EngineError(
            errors.INVALID_CLIP,
            "--end and --duration must resolve to the same end timestamp.",
            exit_code=errors.EXIT_INVALID_TIMESTAMP,
            status=errors.STATUS_VALIDATION_FAILED,
            stage="validate",
        )
    resolved_end = end_ms if has_end else dur_end
    # Exactly one of --end/--duration was provided (checked above), so end is set.
    assert resolved_end is not None
    loop = models.parse_loop(args.loop) if getattr(args, "loop", None) else None
    name = args.output_name if getattr(args, "output_name", None) else None
    # Transformation flags are carried by the command-line precedence level, not
    # by the clip: a ClipSpec field is the clip-level manifest value, which
    # outranks a flag (FR-024).
    return ClipSpec(
        index=0,
        start_ms=start_ms,
        end_ms=resolved_end,
        name=name,
        profile=args.profile,
        fps=args.fps,
        colors=args.colors,
        loop=loop,
    )


def _cmd_create(args: argparse.Namespace) -> int:
    reporter = ProgressReporter(enabled=not args.no_progress)
    job = _JobCleanup()
    try:
        ctx = _prepare_job(args, "create", reporter=reporter, job=job)
        source: SourceInfo = ctx["source"]
        clip = _build_create_clip(args)

        valid, skipped_invalid = validate_and_filter(
            [clip], source.duration_ms, args.invalid_timestamp_policy
        )
        if not valid:
            # Single clip skipped as invalid.
            result = _empty_job_result(
                "create", source, ctx["output_dir"], skipped_invalid, ctx["warnings"]
            )
            _emit_job(result, args, job)
            return errors.EXIT_SUCCESS

        planned = plan_outputs(
            valid,
            source,
            output_dir=ctx["output_dir"],
            collision_policy=ctx["collision_policy"],
            resolve_settings=ctx["resolver"],
        )
        if any(p.action == "collision" for p in planned):
            result = _collision_result("create", planned, ctx["output_dir"], ctx["warnings"])
            _emit_job(result, args, job)
            return errors.EXIT_COLLISION

        paths.ensure_directory(ctx["output_dir"])
        created, failed, skipped = _run_conversions(
            planned,
            source,
            ctx["cfg"],
            ctx["tools"],
            reporter,
            continue_on_error=ctx["continue_on_error"],
        )
        skipped = skipped_invalid + skipped

        status = _finalize_status(created, failed, 1)
        result = _job_result(
            "create", source, ctx["output_dir"], created, failed, skipped, ctx["warnings"], 1
        )
        result["status"] = status
        _emit_job(result, args, job)
        return _job_exit_code(status, created, failed)
    finally:
        job.cleanup()


def _cmd_batch(args: argparse.Namespace) -> int:
    reporter = ProgressReporter(enabled=not args.no_progress)
    job = _JobCleanup()
    try:
        ctx = _prepare_job(args, "batch", reporter=reporter, job=job)
        source: SourceInfo = ctx["source"]
        manifest: manifests.Manifest = ctx["manifest"]

        valid, skipped_invalid = validate_and_filter(
            manifest.clips, source.duration_ms, args.invalid_timestamp_policy
        )
        planned = plan_outputs(
            valid,
            source,
            output_dir=ctx["output_dir"],
            collision_policy=ctx["collision_policy"],
            resolve_settings=ctx["resolver"],
        )

        if args.dry_run:
            result = _dry_run_result(
                "batch", source, planned, ctx["output_dir"], skipped_invalid, ctx["warnings"]
            )
            _emit_job(result, args, job)
            return errors.EXIT_SUCCESS

        if any(p.action == "collision" for p in planned):
            result = _collision_result("batch", planned, ctx["output_dir"], ctx["warnings"])
            _emit_job(result, args, job)
            return errors.EXIT_COLLISION

        paths.ensure_directory(ctx["output_dir"])
        created, failed, skipped = _run_conversions(
            planned,
            source,
            ctx["cfg"],
            ctx["tools"],
            reporter,
            continue_on_error=ctx["continue_on_error"],
        )
        skipped = skipped_invalid + skipped

        requested = len(manifest.clips)
        status = _finalize_status(created, failed, requested)
        result = _job_result(
            "batch", source, ctx["output_dir"], created, failed, skipped, ctx["warnings"], requested
        )
        result["status"] = status
        _emit_job(result, args, job)
        return _job_exit_code(status, created, failed)
    finally:
        job.cleanup()


# ---------------------------------------------------------------------------
# Preview frames (spec FR-029, sections 12.9 / 13.4)
# ---------------------------------------------------------------------------


def _preview_ignored_warning(
    args: argparse.Namespace, manifest: manifests.Manifest | None
) -> str | None:
    """Build the one-per-invocation TRANSFORMATION_NOT_APPLICABLE warning.

    Temporal and palette settings are accepted so a preview can be requested
    with the same settings as the GIF it previews, but they cannot change a
    still frame (FR-029).
    """
    supplied: list[str] = []
    for attr, public in _PREVIEW_IGNORED_FLAGS:
        if getattr(args, attr, None) is not None:
            supplied.append(public)
    if manifest is not None:
        for attr, public in (
            ("speed", "speed"),
            ("fps", "fps"),
            ("loop", "loop"),
            ("colors", "colors"),
            ("dither", "dither"),
            ("bayer_scale", "bayerScale"),
        ):
            if getattr(manifest, attr, None) is not None and public not in supplied:
                supplied.append(public)
        for clip in manifest.clips:
            for attr, public in (
                ("speed", "speed"),
                ("fps", "fps"),
                ("loop", "loop"),
                ("colors", "colors"),
                ("dither", "dither"),
                ("bayer_scale", "bayerScale"),
            ):
                if getattr(clip, attr, None) is not None and public not in supplied:
                    supplied.append(public)
    if not supplied:
        return None
    order = [public for _attr, public in _PREVIEW_IGNORED_FLAGS]
    supplied.sort(key=order.index)
    return transforms.not_applicable_warning(supplied)


def _preview_clips(args: argparse.Namespace, manifest: manifests.Manifest | None) -> list[ClipSpec]:
    """Build the clip list for a preview job (FR-029)."""
    if manifest is not None:
        if getattr(args, "at", None) is not None:
            raise errors.EngineError(
                errors.INVALID_USAGE,
                "preview accepts either --manifest or --at, not both.",
                exit_code=errors.EXIT_INVALID_USAGE,
                status=errors.STATUS_VALIDATION_FAILED,
                stage="validate",
                remediation="The manifest form previews each clip at its start timestamp.",
            )
        return list(manifest.clips)
    if not getattr(args, "at", None):
        raise errors.EngineError(
            errors.INVALID_USAGE,
            "preview requires --at <timestamp> when --manifest is not supplied.",
            exit_code=errors.EXIT_INVALID_USAGE,
            status=errors.STATUS_VALIDATION_FAILED,
            stage="validate",
            remediation="Provide --at, e.g. --at 00:01:02.500.",
        )
    at_ms = parse_timestamp(args.at, field_path="at")
    name = args.output_name if getattr(args, "output_name", None) else None
    # end_ms is unused by preview extraction; a still has no range.
    return [ClipSpec(index=0, start_ms=at_ms, end_ms=at_ms, name=name, profile=args.profile)]


def _validate_preview_times(clips: list[ClipSpec], duration_ms: int) -> None:
    """Enforce ``0 <= at < source duration`` for every preview frame (FR-029)."""
    for clip in clips:
        if clip.start_ms < 0 or clip.start_ms >= duration_ms:
            raise errors.EngineError(
                errors.INVALID_TIMESTAMP,
                f"Preview timestamp {clip.start_ms} ms is outside the source duration "
                f"(0 to {duration_ms} ms, exclusive of the end).",
                exit_code=errors.EXIT_INVALID_TIMESTAMP,
                status=errors.STATUS_VALIDATION_FAILED,
                stage="validate",
                clip_index=clip.index,
                remediation="Choose a timestamp inside the source duration.",
            )


def _preview_namer(explicit_name: str | None) -> Callable[[ClipSpec, str], str]:
    """Return the FR-029 preview name builder.

    An explicit ``--output-name`` is sanitized under FR-011 with ``.png``
    substituted for ``.gif``; otherwise a named clip yields
    ``<clip-name>_<start>.png`` and an unnamed one ``<video-stem>_<at>.png``.
    """

    def namer(clip: ClipSpec, source_stem: str) -> str:
        if explicit_name is not None:
            return naming.sanitize_preview_name(explicit_name)
        stem = clip.name if clip.name else source_stem
        return naming.default_preview_name(stem, clip.start_ms)

    return namer


def _preview_dry_run_result(
    source: SourceInfo,
    planned: list[PlannedClip],
    output_dir: str,
    warnings: list[str],
) -> dict[str, Any]:
    collisions = 0
    plan_entries = []
    for p in planned:
        if p.action == "collision":
            collisions += 1
        plan_entries.append(
            {
                "clipIndex": p.clip.index,
                "name": p.clip.name,
                "path": _display_path(p.dest_path),
                "atMs": p.clip.start_ms,
                "width": p.settings.width,
                "height": p.settings.height,
                "action": p.action,
                "collision": p.collided,
                "estimatedWorkUnits": 1,
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": "preview",
        "status": errors.STATUS_DRY_RUN,
        "source": source.to_public(),
        "outputDirectory": _display_path(output_dir),
        "plan": plan_entries,
        "skipped": [],
        "warnings": warnings,
        "summary": {
            "requested": len(planned),
            "planned": len([p for p in planned if p.action != "collision"]),
            "collisions": collisions,
            "skipped": 0,
            "previews": 0,
        },
    }


def _run_previews(
    planned: list[PlannedClip],
    source: SourceInfo,
    cfg: config_mod.Config,
    tools: dict[str, str],
    reporter: ProgressReporter,
    *,
    continue_on_error: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    previews: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total = len(planned)

    for i, planned_clip in enumerate(planned):
        clip = planned_clip.clip
        if planned_clip.action == "skip":
            skipped.append({"clipIndex": clip.index, "reason": "output exists (skip policy)"})
            reporter.clip_skipped(clip.index, "collision-skip")
            continue
        if _CANCEL_EVENT.is_set():
            raise errors.CancelledError(clip_index=clip.index)

        reporter.clip_started(clip.index, total, clip.name)
        try:
            prev = ffmpeg.extract_preview(
                tools["ffmpeg"],
                source,
                at_ms=clip.start_ms,
                settings=planned_clip.settings,
                dest_path=planned_clip.dest_path,
                output_dir=os.path.dirname(planned_clip.dest_path),
                timeout_seconds=cfg.max_clip_processing_seconds,
                max_temp_bytes=cfg.max_temporary_bytes,
                keep_temporary_files=cfg.keep_temporary_files,
                clip_index=clip.index,
                cancel_event=_CANCEL_EVENT,
                reporter=reporter,
            )
        except errors.CancelledError:
            raise
        except errors.EngineError as exc:
            # A failed preview is reported in `failed` with its stage and code,
            # and counts as a failure rather than a created output (FR-029).
            failed.append(exc.to_dict())
            reporter.clip_failed(clip.index, exc.code, exc.message)
            if not continue_on_error:
                for remaining in planned[i + 1 :]:
                    skipped.append(
                        {
                            "clipIndex": remaining.clip.index,
                            "reason": "not processed (stopped after an earlier failure)",
                        }
                    )
                    reporter.clip_skipped(remaining.clip.index, "stopped-on-error")
                break
        else:
            previews.append(_preview_result_entry(planned_clip, prev))
            reporter.clip_completed(clip.index, _display_path(prev.path))

    return previews, failed, skipped


def _cmd_preview(args: argparse.Namespace) -> int:
    reporter = ProgressReporter(enabled=not args.no_progress)
    job = _JobCleanup()
    try:
        if not getattr(args, "input", None) and not getattr(args, "manifest", None):
            raise errors.EngineError(
                errors.INVALID_USAGE,
                "preview requires --input or --manifest.",
                exit_code=errors.EXIT_INVALID_USAGE,
                status=errors.STATUS_VALIDATION_FAILED,
                stage="validate",
            )
        # An invalid --output-name extension fails before any other work (FR-029).
        if getattr(args, "output_name", None):
            naming.sanitize_preview_name(args.output_name)

        ctx = _prepare_job(args, "preview", reporter=reporter, job=job)
        source: SourceInfo = ctx["source"]
        manifest: manifests.Manifest | None = ctx["manifest"]
        warnings: list[str] = ctx["warnings"]

        ignored = _preview_ignored_warning(args, manifest)
        if ignored is not None:
            warnings.append(ignored)

        clips = _preview_clips(args, manifest)
        _validate_preview_times(clips, source.duration_ms)

        planned = plan_outputs(
            clips,
            source,
            output_dir=ctx["output_dir"],
            collision_policy=ctx["collision_policy"],
            resolve_settings=ctx["resolver"],
            namer=_preview_namer(getattr(args, "output_name", None)),
        )

        if args.dry_run:
            result = _preview_dry_run_result(source, planned, ctx["output_dir"], warnings)
            _emit_job(result, args, job)
            return errors.EXIT_SUCCESS

        if any(p.action == "collision" for p in planned):
            result = _collision_result("preview", planned, ctx["output_dir"], warnings)
            _emit_job(result, args, job)
            return errors.EXIT_COLLISION

        paths.ensure_directory(ctx["output_dir"])
        previews, failed, skipped = _run_previews(
            planned,
            source,
            ctx["cfg"],
            ctx["tools"],
            reporter,
            continue_on_error=ctx["continue_on_error"],
        )

        status = _finalize_status(previews, failed, len(clips))
        result = _job_result(
            "preview",
            source,
            ctx["output_dir"],
            [],
            failed,
            skipped,
            warnings,
            len(clips),
            previews=previews,
        )
        result["status"] = status
        _emit_job(result, args, job)
        return _job_exit_code(status, previews, failed)
    finally:
        job.cleanup()


def _empty_job_result(
    command: str,
    source: SourceInfo,
    output_dir: str,
    skipped: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "status": errors.STATUS_SUCCESS,
        "source": source.to_public(),
        "outputDirectory": _display_path(output_dir),
        "created": [],
        "previews": [],
        "failed": [],
        "skipped": skipped,
        "warnings": warnings,
        "summary": {
            "requested": len(skipped),
            "created": 0,
            "failed": 0,
            "skipped": len(skipped),
            "previews": 0,
        },
    }


def _representative_error(failed: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the failure that surfaces as the top-level error of a wholly failed
    job. SEC-011 precedence: a resource-limit breach wins; otherwise the first
    failure is used. Entries are already ``EngineError.to_dict()`` shaped.
    """
    for entry in failed:
        if entry.get("code") == errors.RESOURCE_LIMIT_EXCEEDED:
            return entry
    return failed[0]


def _job_result(
    command: str,
    source: SourceInfo,
    output_dir: str,
    created: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    warnings: list[str],
    requested: int,
    previews: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # The previews array is always present: empty for create and batch, populated
    # for preview. A preview is never counted in summary.created (section 13.4).
    previews = previews or []
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "status": errors.STATUS_SUCCESS,
        "source": source.to_public(),
        "outputDirectory": _display_path(output_dir),
        "created": created,
        "previews": previews,
        "failed": failed,
        "skipped": skipped,
        "warnings": warnings,
        "summary": {
            "requested": requested,
            "created": len(created),
            "failed": len(failed),
            "skipped": len(skipped),
            "previews": len(previews),
        },
    }
    # A wholly failed job (nothing created) also surfaces a top-level structured
    # error mirroring the standalone error result, so callers reading `error`
    # still see the stable code. Partial successes keep per-clip codes only, so
    # the created work is never masked by a top-level error (section 13 / 14).
    if failed and not created:
        result["error"] = _representative_error(failed)
    return result


# Natural process exit code for each stable error code (spec section 14). Used
# to map a wholly-failed job's error code(s) to the correct exit status instead
# of hard-flattening every failure to an FFmpeg failure.
_CODE_EXIT: dict[str, int] = {
    errors.RESOURCE_LIMIT_EXCEEDED: errors.EXIT_RESOURCE_LIMIT,
    errors.FFMPEG_FAILED: errors.EXIT_FFMPEG_FAILED,
    errors.DEPENDENCY_MISSING: errors.EXIT_DEPENDENCY_MISSING,
    errors.INPUT_NOT_FOUND: errors.EXIT_INPUT_NOT_FOUND,
    errors.INPUT_NOT_READABLE: errors.EXIT_INPUT_NOT_FOUND,
    errors.PERMISSION_DENIED: errors.EXIT_PERMISSION,
}


def _job_exit_code(status: str, created: list, failed: list) -> int:
    if status == errors.STATUS_SUCCESS:
        return errors.EXIT_SUCCESS
    if status == errors.STATUS_PARTIAL:
        # Some clips succeeded: partial batch success. Per-clip error codes are
        # preserved in the structured result (SEC-011 precedence).
        return errors.EXIT_PARTIAL
    # failed: no clips succeeded.
    if failed:
        codes = {f.get("code") for f in failed}
        # SEC-011: a resource-limit breach with no successful clips -> exit 13,
        # taking precedence over any other failure code in the job.
        if errors.RESOURCE_LIMIT_EXCEEDED in codes:
            return errors.EXIT_RESOURCE_LIMIT
        # When every failure shares one error code, map it to its natural exit;
        # otherwise fall back to a generic FFmpeg failure.
        if len(codes) == 1:
            return _CODE_EXIT.get(next(iter(codes)), errors.EXIT_FFMPEG_FAILED)
        return errors.EXIT_FFMPEG_FAILED
    return errors.EXIT_INTERNAL


def _cmd_validate_config(args: argparse.Namespace) -> int:
    cfg = config_mod.load_config_file(args.config)
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "command": "validate-config",
        "status": errors.STATUS_SUCCESS,
        "valid": True,
        "warnings": cfg.warnings,
        "resolved": {
            "defaultProfile": cfg.default_profile,
            "outputDirectory": cfg.output_directory,
            "loop": cfg.loop,
            "collisionPolicy": cfg.collision_policy,
            "continueOnError": cfg.continue_on_error,
            "keepTemporaryFiles": cfg.keep_temporary_files,
            "allowOutsideProject": cfg.allow_outside_project,
            "remoteSources": cfg.remote_sources,
            "keepRemoteSource": cfg.keep_remote_source,
            "transformations": cfg.transformations_public(),
            "limits": {
                "maxClipProcessingSeconds": cfg.max_clip_processing_seconds,
                "maxTemporaryBytes": cfg.max_temporary_bytes,
                "maxDownloadBytes": cfg.max_download_bytes,
                "maxDownloadSeconds": cfg.max_download_seconds,
            },
        },
    }
    _emit(result, args.json)
    return errors.EXIT_SUCCESS


def _cmd_validate_manifest(args: argparse.Namespace) -> int:
    manifest = manifests.load_manifest_file(args.manifest)
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "command": "validate-manifest",
        "status": errors.STATUS_SUCCESS,
        "valid": True,
        "input": manifest.input,
        "clipCount": len(manifest.clips),
        "clips": [
            {
                "clipIndex": c.index,
                "name": c.name,
                "startMs": c.start_ms,
                "endMs": c.end_ms,
                "durationMs": c.duration_ms,
            }
            for c in manifest.clips
        ],
        "warnings": manifest.warnings,
    }
    _emit(result, args.json)
    return errors.EXIT_SUCCESS


_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "doctor": _cmd_doctor,
    "inspect": _cmd_inspect,
    "create": _cmd_create,
    "batch": _cmd_batch,
    "preview": _cmd_preview,
    "validate-config": _cmd_validate_config,
    "validate-manifest": _cmd_validate_manifest,
}


def _install_signal_handlers() -> None:
    def handler(signum: int, frame: FrameType | None) -> None:
        _CANCEL_EVENT.set()

    signals = [signal.SIGINT, signal.SIGTERM]
    # Windows delivers console cancellation as CTRL_BREAK -> SIGBREAK (a
    # CTRL_C_EVENT/CTRL_BREAK_EVENT sent to the engine's process group), so the
    # SIGINT/SIGTERM pair alone would never observe cancellation there. Register
    # SIGBREAK too when the platform defines it (Windows only) so cancellation
    # works cross-platform per spec section 16. getattr keeps this a no-op on
    # POSIX, where signal has no SIGBREAK.
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signals.append(sigbreak)

    for sig in signals:
        # Not on the main thread (e.g. under a test runner) -> cannot install.
        with contextlib.suppress(ValueError, OSError):  # pragma: no cover
            signal.signal(sig, handler)


def _cancelled_result(
    command: str, exc: errors.CancelledError, warnings: list[str], created: list | None = None
) -> dict[str, Any]:
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "status": errors.STATUS_CANCELLED,
        "error": exc.to_dict(),
        "warnings": warnings or [],
        "summary": {"created": len(created or [])},
    }
    return result


def main(argv: list[str] | None = None) -> int:
    # First statement in the engine: every later write to stdout/stderr -- the
    # final JSON document, the JSON Lines progress stream, argparse usage errors,
    # human-readable output -- depends on this (spec section 13.5).
    configure_output_encoding()
    parser = build_parser()
    args = parser.parse_args(normalize_argv(list(sys.argv[1:] if argv is None else argv)))
    _install_signal_handlers()
    _CANCEL_EVENT.clear()

    handler = _HANDLERS.get(args.command)
    if handler is None:  # pragma: no cover - argparse guards this
        parser.error(f"Unknown command: {args.command}")
        return errors.EXIT_INVALID_USAGE

    json_mode = getattr(args, "json", False)
    debug = getattr(args, "debug", False)
    try:
        return handler(args)
    except errors.CancelledError as exc:
        result = _cancelled_result(args.command, exc, [])
        _emit(result, json_mode)
        return errors.EXIT_CANCELLED
    except errors.EngineError as exc:
        result = _error_result(args.command, exc, [])
        _emit(result, json_mode)
        if debug:
            import traceback

            traceback.print_exc()
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover
        cancelled = errors.CancelledError()
        _emit(_cancelled_result(args.command, cancelled, []), json_mode)
        return errors.EXIT_CANCELLED
    except Exception as exc:
        # FIX m2: an unexpected exception on the remote path could carry a raw
        # source URL in its message; redact any http(s) URL substring (SEC-015)
        # before surfacing it. A no-op for the common non-URL messages.
        engine_exc = errors.EngineError(
            errors.INTERNAL_ERROR,
            remote_mod.redact_message_urls(f"Internal engine error: {exc}"),
            exit_code=errors.EXIT_INTERNAL,
            status=errors.STATUS_FAILED,
        )
        _emit(_error_result(args.command, engine_exc, []), json_mode)
        if debug:
            import traceback

            traceback.print_exc()
        return errors.EXIT_INTERNAL
