"""Core data models and quality profiles (spec sections 8, 9, 10, FR-014, FR-015).

Pure data structures plus small pure helpers (loop parsing, profile lookup).
This module only depends on :mod:`vtg.errors`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import errors

# ---------------------------------------------------------------------------
# Quality profiles (FR-014)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityProfile:
    name: str
    max_width: int | None
    fps: int | None
    max_colors: int | None


BUILTIN_PROFILES: dict[str, QualityProfile] = {
    "small": QualityProfile("small", 480, 10, 128),
    "balanced": QualityProfile("balanced", 640, 15, 256),
    "high": QualityProfile("high", 960, 20, 256),
}

VALID_PROFILE_NAMES = frozenset([*BUILTIN_PROFILES, "custom"])

# Loop value is either the literal string "forever" or a positive integer count.
LoopValue = str | int

# Collision policies recognised by the engine (FR-012). "ask" is a skill-layer /
# config alias that the engine treats as "fail" (report collisions, never
# overwrite). It is accepted in project config, but NOT in a manifest: manifests
# feed the non-interactive engine directly, so there is no agent present to
# resolve an "ask" into a concrete policy.
VALID_COLLISION_POLICIES = frozenset({"fail", "overwrite", "unique", "skip", "ask"})
VALID_MANIFEST_COLLISION_POLICIES = VALID_COLLISION_POLICIES - {"ask"}
VALID_INVALID_TIMESTAMP_POLICIES = frozenset({"fail", "skip", "clamp"})


def parse_loop(value: Any, *, field_path: str = "loop") -> LoopValue:
    """Parse a loop value per FR-015.

    Accepts ``forever``, ``once``, or an integer/int-string ``N >= 1``.
    The value ``0`` MUST be rejected. Returns ``"forever"`` or ``int``.
    """
    if isinstance(value, bool):
        # bool is a subclass of int; reject explicitly to avoid True == 1 slip.
        raise errors.EngineError(
            errors.INVALID_LOOP,
            f"Invalid loop value for '{field_path}': booleans are not allowed.",
            exit_code=errors.EXIT_INVALID_TIMESTAMP,
            status=errors.STATUS_VALIDATION_FAILED,
            field=field_path,
            remediation="Use 'forever', 'once', or an integer >= 1.",
        )
    if isinstance(value, str):
        token = value.strip().lower()
        if token == "forever":
            return "forever"
        if token == "once":
            return 1
        if token.isdigit():
            n = int(token)
            return _validate_loop_count(n, field_path)
        # Allow signed / non digit strings to fall through to a clear error.
        try:
            n = int(token)
        except (ValueError, TypeError) as exc:
            raise errors.EngineError(
                errors.INVALID_LOOP,
                f"Invalid loop value for '{field_path}': {value!r}.",
                exit_code=errors.EXIT_INVALID_TIMESTAMP,
                status=errors.STATUS_VALIDATION_FAILED,
                field=field_path,
                remediation="Use 'forever', 'once', or an integer >= 1.",
            ) from exc
        return _validate_loop_count(n, field_path)
    if isinstance(value, int):
        return _validate_loop_count(value, field_path)
    raise errors.EngineError(
        errors.INVALID_LOOP,
        f"Invalid loop value for '{field_path}': {value!r}.",
        exit_code=errors.EXIT_INVALID_TIMESTAMP,
        status=errors.STATUS_VALIDATION_FAILED,
        field=field_path,
        remediation="Use 'forever', 'once', or an integer >= 1.",
    )


def _validate_loop_count(n: int, field_path: str) -> int:
    if n == 0:
        raise errors.EngineError(
            errors.INVALID_LOOP,
            "Loop count 0 is not allowed (ambiguous with GIF loop-extension "
            "semantics). Use 'forever' for infinite looping.",
            exit_code=errors.EXIT_INVALID_TIMESTAMP,
            status=errors.STATUS_VALIDATION_FAILED,
            field=field_path,
            remediation="Use 'forever', 'once', or an integer >= 1.",
        )
    if n < 1:
        raise errors.EngineError(
            errors.INVALID_LOOP,
            f"Loop count must be >= 1, got {n}.",
            exit_code=errors.EXIT_INVALID_TIMESTAMP,
            status=errors.STATUS_VALIDATION_FAILED,
            field=field_path,
            remediation="Use 'forever', 'once', or an integer >= 1.",
        )
    return n


def loop_to_ffmpeg(loop: LoopValue) -> int:
    """Map a normalized loop value to the FFmpeg gif muxer ``-loop`` value.

    FFmpeg semantics: ``0`` loops forever, ``-1`` plays exactly once, and a
    positive value ``v`` plays ``v + 1`` times total. FR-015 defines an
    integer ``N`` as "plays N times in total", so::

        forever -> 0
        N == 1  -> -1   (once)
        N >= 2  -> N - 1
    """
    if loop == "forever":
        return 0
    if isinstance(loop, int):
        if loop == 1:
            return -1
        return loop - 1
    raise errors.EngineError(
        errors.INVALID_LOOP,
        f"Unresolved loop value: {loop!r}.",
        exit_code=errors.EXIT_INVALID_TIMESTAMP,
    )


def resolve_profile(name: str, *, field_path: str = "profile") -> QualityProfile:
    if name in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[name]
    raise errors.EngineError(
        errors.INVALID_PROFILE,
        f"Unknown quality profile: {name!r}.",
        exit_code=errors.EXIT_INVALID_USAGE,
        status=errors.STATUS_VALIDATION_FAILED,
        field=field_path,
        remediation="Valid profiles: small, balanced, high, custom.",
    )


# ---------------------------------------------------------------------------
# Source information (FR-002)
# ---------------------------------------------------------------------------


@dataclass
class SourceInfo:
    path: str
    duration_ms: int
    width: int
    height: int
    display_width: int
    display_height: int
    fps: float
    codec: str
    stream_index: int
    rotation: int = 0
    container_duration_ms: int | None = None
    stream_duration_ms: int | None = None
    disposition: dict[str, Any] = field(default_factory=dict)
    format_name: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "durationMs": self.duration_ms,
            "width": self.display_width,
            "height": self.display_height,
            "fps": round(self.fps, 4),
            "codec": self.codec,
            "videoStreamIndex": self.stream_index,
            "rotation": self.rotation,
        }


# ---------------------------------------------------------------------------
# Clip specification (FR-005)
# ---------------------------------------------------------------------------


@dataclass
class ClipSpec:
    index: int
    start_ms: int
    end_ms: int
    name: str | None = None
    profile: str | None = None
    width: int | None = None
    fps: int | None = None
    colors: int | None = None
    loop: LoopValue | None = None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class EffectiveSettings:
    """The fully resolved encode settings for one clip."""

    width: int
    height: int
    fps: float
    colors: int
    loop: LoopValue
    profile_name: str
