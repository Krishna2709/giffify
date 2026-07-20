"""Transformation parsing, validation, and filter-graph construction.

Spec references: FR-024 (transformation model), FR-025 (cropping), FR-026
(explicit resizing), FR-027 (playback speed), FR-028 (dithering), FR-030
(reporting), section 15.2 (filter-chain order), section 15.5 (dither defaults),
and SEC-018 (transformation parameter validation).

SEC-018 is the security contract of this module. Every transformation parameter
is an integer, a bounded decimal, or a member of a fixed enumeration. Values are
parsed and range-checked *before* any filter graph exists, and every filter
argument is re-serialized by this module from its own validated ``int`` /
``Decimal`` / enum member. User-supplied text is never concatenated into a
filter graph, so filter-graph metacharacters cannot reach FFmpeg.

The module depends only on :mod:`vtg.errors` and the standard library, so it can
be imported from :mod:`vtg.models`, :mod:`vtg.config`, :mod:`vtg.manifests`,
:mod:`vtg.ffmpeg`, and :mod:`vtg.cli` without a cycle.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from . import errors

# ---------------------------------------------------------------------------
# Bounds and enumerations (FR-025 .. FR-028)
# ---------------------------------------------------------------------------

#: Maximum value of any crop component (FR-025).
CROP_MAX = 65535
#: Minimum width/height of a crop rectangle (FR-025).
MIN_CROP_SIDE = 2

#: Closed range for an explicit output dimension bound (FR-026).
MIN_DIMENSION = 2
MAX_DIMENSION = 8192

#: Closed range for the speed multiplier and its fractional-digit cap (FR-027).
MIN_SPEED = Decimal("0.25")
MAX_SPEED = Decimal("4.0")
MAX_SPEED_DECIMALS = 3
DEFAULT_SPEED = Decimal("1")

#: The dither enumeration (FR-028). Comparison is case-sensitive.
DITHER_MODES: tuple[str, ...] = ("none", "bayer", "floyd_steinberg", "sierra2", "sierra2_4a")
BAYER_SCALE_MIN = 0
BAYER_SCALE_MAX = 5
#: Fallback bayerScale when the effective profile default mode is not bayer
#: but the user asked for bayer without a scale (FR-028).
DEFAULT_BAYER_SCALE = 2

#: Per-profile dither defaults (section 15.5). These reproduce the v0.1.0/v0.2.0
#: behavior exactly, so a job that specifies no dither is byte-comparable with
#: earlier releases (NFR-002, NFR-006).
PROFILE_DITHER_DEFAULTS: dict[str, tuple[str, int | None]] = {
    "small": ("bayer", 5),
    "balanced": ("sierra2_4a", None),
    "high": ("sierra2_4a", None),
    "custom": ("sierra2_4a", None),
}
FALLBACK_DITHER: tuple[str, int | None] = ("sierra2_4a", None)

#: Stable warning tokens defined by version 0.3.0 (section 13.4).
WARN_UPSCALE_NOT_ALLOWED = "UPSCALE_NOT_ALLOWED"
WARN_TRANSFORMATION_NOT_APPLICABLE = "TRANSFORMATION_NOT_APPLICABLE"

# ---------------------------------------------------------------------------
# Grammars (SEC-018)
# ---------------------------------------------------------------------------
# Explicit ``[0-9]`` rather than ``\d``: ``\d`` also matches Unicode decimal
# digits (e.g. Arabic-Indic), which are not part of any documented grammar.
#
# ``\Z`` rather than ``$``: ``$`` also matches immediately BEFORE a trailing
# newline, so "10\n" would pass a ``^[0-9]+$`` check. SEC-018 requires a
# trailing newline to be rejected.
_UINT_RE = re.compile(r"^[0-9]+\Z")
_CROP_RE = re.compile(r"^([0-9]+):([0-9]+):([0-9]+):([0-9]+)\Z")
_DECIMAL_RE = re.compile(rf"^[0-9]+(?:\.[0-9]{{1,{MAX_SPEED_DECIMALS}}})?\Z")

# Only these characters may appear in the trimmed text of a transformation
# value. Everything else -- whitespace, newline, and , ; ' " \ [ ] = % ( ) $ `
# and * -- is outside every grammar above and is therefore rejected. This is a
# defense-in-depth assertion; the regexes above already enforce it.
_ALLOWED_TEXT_RE = re.compile(r"^[0-9.:_a-z]*\Z")


def _tx_error(
    code: str,
    message: str,
    *,
    field_path: str | None = None,
    clip_index: int | None = None,
    remediation: str | None = None,
) -> errors.EngineError:
    """Build the standard transformation validation error (exit 6, FR-024)."""
    return errors.EngineError(
        code,
        message,
        exit_code=errors.EXIT_INVALID_TIMESTAMP,
        status=errors.STATUS_VALIDATION_FAILED,
        stage="validate",
        field=field_path,
        clip_index=clip_index,
        remediation=remediation,
    )


def _text(value: Any, *, trim: bool = False) -> str | None:
    """Return the text of a string value, or None when the value is not a string.

    SEC-018 rejects any value whose text contains a character outside its
    grammar, explicitly including whitespace and newlines, so whitespace is NOT
    stripped by default: a padded numeric value is a rejected value, not a
    trimmed one. ``trim=True`` is used only for ``dither``, where FR-028
    mandates comparison "after surrounding whitespace is trimmed".
    """
    if isinstance(value, str):
        return value.strip() if trim else value
    return None


# ---------------------------------------------------------------------------
# Crop rectangle (FR-025)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CropRect:
    """A validated crop rectangle in orientation-normalized source pixels."""

    x: int
    y: int
    width: int
    height: int

    def to_public(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @property
    def needs_full_chroma(self) -> bool:
        """True when the rectangle cannot be expressed on a subsampled grid.

        FR-025 forbids rounding, clamping, or re-centering the requested
        rectangle; when an offset or size is odd the frame is converted to a
        non-subsampled pixel format before cropping instead.
        """
        return bool((self.x | self.y | self.width | self.height) & 1)

    def filter_arg(self) -> str:
        """Return the ``crop`` filter built from validated integers only."""
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


def _crop_error(message: str, field_path: str | None, clip_index: int | None) -> errors.EngineError:
    return _tx_error(
        errors.INVALID_CROP,
        message,
        field_path=field_path,
        clip_index=clip_index,
        remediation=(
            "Use four unsigned integers as x:y:width:height (or an object with "
            "exactly x, y, width, height) inside the source dimensions."
        ),
    )


def _crop_component(value: Any, name: str, field_path: str | None, clip_index: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _crop_error(f"crop.{name} must be an integer, got {value!r}.", field_path, clip_index)
    if value < 0 or value > CROP_MAX:
        raise _crop_error(
            f"crop.{name} must be between 0 and {CROP_MAX}, got {value}.", field_path, clip_index
        )
    return int(value)


def parse_crop(
    value: Any,
    *,
    field_path: str = "crop",
    clip_index: int | None = None,
) -> CropRect:
    """Parse a crop rectangle from the string or object form (FR-025).

    Accepts ``"x:y:width:height"`` (four unsigned decimal integers, exactly
    three colons, no signs and no exponent notation) or a mapping with exactly
    the keys ``x``, ``y``, ``width``, ``height``. Anything else raises
    ``INVALID_CROP`` with exit code 6.
    """
    if isinstance(value, CropRect):
        rect = value
    elif isinstance(value, dict):
        keys = set(value)
        expected = {"x", "y", "width", "height"}
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            detail = []
            if missing:
                detail.append(f"missing {missing}")
            if extra:
                detail.append(f"unexpected {extra}")
            raise _crop_error(
                "crop object must have exactly the keys x, y, width, height ("
                + "; ".join(detail)
                + ").",
                field_path,
                clip_index,
            )
        rect = CropRect(
            x=_crop_component(value["x"], "x", field_path, clip_index),
            y=_crop_component(value["y"], "y", field_path, clip_index),
            width=_crop_component(value["width"], "width", field_path, clip_index),
            height=_crop_component(value["height"], "height", field_path, clip_index),
        )
    else:
        text = _text(value)
        if text is None or not _ALLOWED_TEXT_RE.match(text) or not _CROP_RE.match(text):
            raise _crop_error(
                f"crop must be four unsigned integers as x:y:width:height, got {value!r}.",
                field_path,
                clip_index,
            )
        match = _CROP_RE.match(text)
        assert match is not None  # guarded above
        parts = [int(part) for part in match.groups()]
        for name, part in zip(("x", "y", "width", "height"), parts, strict=True):
            if part > CROP_MAX:
                raise _crop_error(
                    f"crop.{name} must be between 0 and {CROP_MAX}, got {part}.",
                    field_path,
                    clip_index,
                )
        rect = CropRect(x=parts[0], y=parts[1], width=parts[2], height=parts[3])

    if rect.width < MIN_CROP_SIDE or rect.height < MIN_CROP_SIDE:
        raise _crop_error(
            f"crop width and height must each be at least {MIN_CROP_SIDE}, got "
            f"{rect.width}x{rect.height}.",
            field_path,
            clip_index,
        )
    return rect


def validate_crop_bounds(
    crop: CropRect,
    source_width: int,
    source_height: int,
    *,
    field_path: str = "crop",
    clip_index: int | None = None,
) -> None:
    """Reject a rectangle that leaves the orientation-normalized source (FR-025).

    ``source_width``/``source_height`` MUST be the display (rotation-corrected)
    dimensions so a rotated source is cropped in the geometry the user sees.
    """
    if crop.x + crop.width > source_width or crop.y + crop.height > source_height:
        raise _crop_error(
            f"crop rectangle {crop.x}:{crop.y}:{crop.width}:{crop.height} extends beyond the "
            f"orientation-normalized source dimensions {source_width}x{source_height}.",
            field_path,
            clip_index,
        )


# ---------------------------------------------------------------------------
# Dimension bounds (FR-026)
# ---------------------------------------------------------------------------


def parse_dimension(
    value: Any,
    *,
    field_path: str,
    clip_index: int | None = None,
) -> int:
    """Parse an explicit width/height bound: an integer in 2..8192 (FR-026)."""
    remediation = f"Use an integer between {MIN_DIMENSION} and {MAX_DIMENSION}."
    number: int
    if isinstance(value, bool) or value is None:
        raise _tx_error(
            errors.INVALID_DIMENSIONS,
            f"{field_path} must be an integer, got {value!r}.",
            field_path=field_path,
            clip_index=clip_index,
            remediation=remediation,
        )
    if isinstance(value, int):
        number = value
    else:
        text = _text(value)
        if text is None or not _ALLOWED_TEXT_RE.match(text) or not _UINT_RE.match(text):
            raise _tx_error(
                errors.INVALID_DIMENSIONS,
                f"{field_path} must be an unsigned decimal integer, got {value!r}.",
                field_path=field_path,
                clip_index=clip_index,
                remediation=remediation,
            )
        number = int(text)
    if number < MIN_DIMENSION or number > MAX_DIMENSION:
        raise _tx_error(
            errors.INVALID_DIMENSIONS,
            f"{field_path} must be between {MIN_DIMENSION} and {MAX_DIMENSION}, got {number}.",
            field_path=field_path,
            clip_index=clip_index,
            remediation=remediation,
        )
    return number


# ---------------------------------------------------------------------------
# Playback speed (FR-027)
# ---------------------------------------------------------------------------


def parse_speed(
    value: Any,
    *,
    field_path: str = "speed",
    clip_index: int | None = None,
) -> Decimal:
    """Parse the speed multiplier: a decimal in 0.25..4.0, at most 3 decimals."""
    remediation = (
        f"Use a decimal between {MIN_SPEED} and {MAX_SPEED} with at most "
        f"{MAX_SPEED_DECIMALS} fractional digits."
    )

    def reject(detail: str) -> errors.EngineError:
        return _tx_error(
            errors.INVALID_SPEED,
            detail,
            field_path=field_path,
            clip_index=clip_index,
            remediation=remediation,
        )

    if isinstance(value, bool) or value is None:
        raise reject(f"{field_path} must be a decimal number, got {value!r}.")

    number: Decimal
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise reject(f"{field_path} must be a finite decimal number, got {value!r}.")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:  # pragma: no cover - str(float) is parseable
            raise reject(f"{field_path} must be a decimal number, got {value!r}.") from exc
    else:
        text = _text(value)
        if text is None or not _ALLOWED_TEXT_RE.match(text) or not _DECIMAL_RE.match(text):
            raise reject(
                f"{field_path} must be a plain decimal number without a sign or exponent, "
                f"got {value!r}."
            )
        number = Decimal(text)

    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int) or -exponent > MAX_SPEED_DECIMALS:
        raise reject(
            f"{field_path} must have at most {MAX_SPEED_DECIMALS} fractional digits, got {value!r}."
        )
    if number < MIN_SPEED or number > MAX_SPEED:
        raise reject(f"{field_path} must be between {MIN_SPEED} and {MAX_SPEED}, got {value!r}.")
    return number


def speed_str(speed: Decimal) -> str:
    """Re-serialize a validated speed as a plain decimal literal (SEC-018)."""
    return format(speed, "f")


def output_duration_ms(duration_ms: int, speed: Decimal) -> int:
    """Return ``round(durationMs / speed)`` using exact decimal arithmetic (FR-027)."""
    if speed == DEFAULT_SPEED:
        return duration_ms
    quotient = Decimal(duration_ms) / speed
    return int(quotient.to_integral_value(rounding="ROUND_HALF_UP"))


def effective_source_fps(source_fps: float, speed: Decimal) -> float:
    """Return the frame-rate ceiling contributed by the source (FR-014, FR-027).

    For a speed below 1.0 the retimed stream's intrinsic frame rate is the
    source frame rate multiplied by the speed, so the output frame rate should
    not exceed that. For 1.0 and above the rule is unchanged from v0.1.0.
    """
    if speed < DEFAULT_SPEED:
        return source_fps * float(speed)
    return source_fps


# ---------------------------------------------------------------------------
# Dithering (FR-028, section 15.5)
# ---------------------------------------------------------------------------


def parse_dither(
    value: Any,
    *,
    field_path: str = "dither",
    clip_index: int | None = None,
) -> str:
    """Parse a dither mode: an exact, case-sensitive enumeration member."""
    permitted = ", ".join(DITHER_MODES)
    text = _text(value, trim=True)
    if text is None or text not in DITHER_MODES:
        raise _tx_error(
            errors.INVALID_DITHER,
            f"{field_path} must be one of: {permitted}. Got {value!r}.",
            field_path=field_path,
            clip_index=clip_index,
            remediation=f"Permitted values: {permitted}.",
        )
    return text


def parse_bayer_scale(
    value: Any,
    *,
    field_path: str = "bayerScale",
    clip_index: int | None = None,
) -> int:
    """Parse a Bayer scale: an integer in the closed range 0..5 (FR-028)."""
    remediation = f"Use an integer between {BAYER_SCALE_MIN} and {BAYER_SCALE_MAX}."
    number: int
    if isinstance(value, bool) or value is None:
        raise _tx_error(
            errors.INVALID_DITHER,
            f"{field_path} must be an integer, got {value!r}.",
            field_path=field_path,
            clip_index=clip_index,
            remediation=remediation,
        )
    if isinstance(value, int):
        number = value
    else:
        text = _text(value)
        if text is None or not _ALLOWED_TEXT_RE.match(text) or not _UINT_RE.match(text):
            raise _tx_error(
                errors.INVALID_DITHER,
                f"{field_path} must be an unsigned decimal integer, got {value!r}.",
                field_path=field_path,
                clip_index=clip_index,
                remediation=remediation,
            )
        number = int(text)
    if number < BAYER_SCALE_MIN or number > BAYER_SCALE_MAX:
        raise _tx_error(
            errors.INVALID_DITHER,
            f"{field_path} must be between {BAYER_SCALE_MIN} and {BAYER_SCALE_MAX}, got {number}.",
            field_path=field_path,
            clip_index=clip_index,
            remediation=remediation,
        )
    return number


def profile_dither_default(profile_name: str) -> tuple[str, int | None]:
    """Return the (mode, bayerScale) default for a profile (section 15.5)."""
    return PROFILE_DITHER_DEFAULTS.get(profile_name, FALLBACK_DITHER)


def resolve_dither(
    *,
    dither: str | None,
    bayer_scale: int | None,
    profile_name: str,
    field_path: str = "bayerScale",
    clip_index: int | None = None,
) -> tuple[str, int | None]:
    """Resolve the effective dither mode and Bayer scale (FR-028, section 15.5).

    ``bayer_scale`` is the explicitly supplied value (or None). Supplying it
    while the effective mode is not ``bayer`` is a validation error.
    """
    default_mode, default_scale = profile_dither_default(profile_name)
    mode = default_mode if dither is None else dither
    if mode == "bayer":
        if bayer_scale is not None:
            return mode, bayer_scale
        if default_mode == "bayer" and default_scale is not None:
            return mode, default_scale
        return mode, DEFAULT_BAYER_SCALE
    if bayer_scale is not None:
        raise _tx_error(
            errors.INVALID_DITHER,
            f"bayerScale is only meaningful when the effective dither mode is 'bayer'; "
            f"the effective mode is {mode!r}.",
            field_path=field_path,
            clip_index=clip_index,
            remediation="Remove bayerScale or set dither to 'bayer'.",
        )
    return mode, None


def dither_filter_arg(mode: str, bayer_scale: int | None) -> str:
    """Build the ``paletteuse`` dither argument from validated values (SEC-018)."""
    if mode == "bayer":
        scale = DEFAULT_BAYER_SCALE if bayer_scale is None else bayer_scale
        return f"bayer:bayer_scale={scale}"
    return mode


# ---------------------------------------------------------------------------
# Dimension resolution (FR-026)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionResult:
    width: int
    height: int
    upscaled: bool
    warning: str | None = None


def _even(value: float) -> int:
    """Round to the nearest even integer, never below the minimum dimension.

    An automatically derived dimension stays even (FR-026), mirroring FFmpeg's
    ``scale=W:-2`` behavior. An explicitly supplied bound is never passed
    through here: it is honored exactly, odd values included.
    """
    rounded = math.floor(value / 2.0 + 0.5) * 2
    return max(MIN_DIMENSION, int(rounded))


def _floor_even(value: int) -> int:
    return max(MIN_DIMENSION, value - (value % 2))


def _derive(other_source: int, driving_out: int, driving_source: int) -> int:
    """Derive the companion dimension from an EXPLICIT bound, preserving AR.

    FR-026: a dimension derived automatically from the other dimension remains
    even, mirroring FFmpeg's ``scale=W:-2``.
    """
    if driving_out == driving_source:
        # Identity scale: no resampling, so keep the source dimension exactly.
        return max(MIN_DIMENSION, other_source)
    return _even(other_source * driving_out / driving_source)


def _derive_legacy(other_source: int, driving_out: int, driving_source: int) -> int:
    """Derive the companion dimension for a PROFILE-ONLY invocation.

    FR-026 is explicit that when neither width nor height is supplied, dimension
    derivation is unchanged from version 0.1.0 so profile-only invocations
    produce the same dimensions as earlier versions. That takes precedence over
    the even-parity rule, which governs derivations driven by an explicit bound.
    """
    return max(1, round(other_source * driving_out / driving_source))


def resolve_output_dimensions(
    effective_source_width: int,
    effective_source_height: int,
    *,
    width: int | None,
    height: int | None,
    profile_max_width: int | None,
    allow_upscale: bool,
) -> DimensionResult:
    """Resolve output dimensions from the effective source geometry (FR-026).

    ``width``/``height`` are *explicitly supplied* bounds from any precedence
    level; ``profile_max_width`` is the quality profile's maximum width, used
    only when neither explicit bound is present. The effective source is the
    cropped rectangle when a crop is applied, otherwise the orientation
    normalized frame (FR-025).
    """
    eff_w = max(1, effective_source_width)
    eff_h = max(1, effective_source_height)

    if width is None and height is None:
        # Profile-only path: unchanged from v0.1.0/v0.2.0, and never warns.
        bound = profile_max_width if profile_max_width is not None else eff_w
        out_w = max(1, int(bound) if allow_upscale else min(eff_w, int(bound)))
        out_h = _derive_legacy(eff_h, out_w, eff_w)
        return DimensionResult(out_w, out_h, allow_upscale and (out_w > eff_w or out_h > eff_h))

    # An explicit bound overrides the profile maximum width entirely (FR-026).
    if width is not None and height is not None:
        if width * eff_h <= height * eff_w:
            out_w = width
            out_h = _derive(eff_h, width, eff_w)
            if out_h > height:
                out_h = _floor_even(height)
        else:
            out_h = height
            out_w = _derive(eff_w, height, eff_h)
            if out_w > width:
                out_w = _floor_even(width)
    elif width is not None:
        out_w = width
        out_h = _derive(eff_h, width, eff_w)
    else:
        assert height is not None
        out_h = height
        out_w = _derive(eff_w, height, eff_h)

    warning: str | None = None
    exceeds = out_w > eff_w or out_h > eff_h
    if exceeds and not allow_upscale:
        warning = (
            f"{WARN_UPSCALE_NOT_ALLOWED}: requested output {out_w}x{out_h} exceeds the effective "
            f"source {eff_w}x{eff_h}; the output was clamped to the effective source size."
        )
        out_w, out_h = eff_w, eff_h
        exceeds = False

    return DimensionResult(max(MIN_DIMENSION, out_w), max(MIN_DIMENSION, out_h), exceeds, warning)


# ---------------------------------------------------------------------------
# Transformation input bundle and precedence (FR-024)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransformSpec:
    """Validated but source-independent transformation inputs from one source.

    One instance per precedence level: clip-level manifest field, command-line
    flag, top-level manifest field, and project configuration (FR-024).
    """

    crop: CropRect | None = None
    width: int | None = None
    height: int | None = None
    speed: Decimal | None = None
    dither: str | None = None
    bayer_scale: int | None = None

    def supplied_fields(self) -> list[str]:
        """Names of the fields this level actually supplies, in a stable order."""
        names = []
        for attr, public in (
            ("crop", "crop"),
            ("width", "width"),
            ("height", "height"),
            ("speed", "speed"),
            ("dither", "dither"),
            ("bayer_scale", "bayerScale"),
        ):
            if getattr(self, attr) is not None:
                names.append(public)
        return names


EMPTY_TRANSFORMS = TransformSpec()


def merge_transforms(*levels: TransformSpec | None) -> TransformSpec:
    """Merge precedence levels, highest priority first (FR-024).

    Order for transformations: clip-level manifest field, command-line flag,
    top-level manifest field, project configuration, built-in default. This
    deliberately ranks a clip-level manifest field above a batch-wide flag
    (section 9.3 refinement).
    """
    merged: dict[str, Any] = {
        "crop": None,
        "width": None,
        "height": None,
        "speed": None,
        "dither": None,
        "bayer_scale": None,
    }
    for level in levels:
        if level is None:
            continue
        for key in merged:
            if merged[key] is None:
                merged[key] = getattr(level, key)
    return TransformSpec(**merged)


def parse_cli_transforms(
    *,
    crop: Any = None,
    width: Any = None,
    height: Any = None,
    speed: Any = None,
    dither: Any = None,
    bayer_scale: Any = None,
) -> TransformSpec:
    """Parse the section 12.10 transformation flags into a TransformSpec."""
    return TransformSpec(
        crop=None if crop is None else parse_crop(crop, field_path="--crop"),
        width=None if width is None else parse_dimension(width, field_path="--width"),
        height=None if height is None else parse_dimension(height, field_path="--height"),
        speed=None if speed is None else parse_speed(speed, field_path="--speed"),
        dither=None if dither is None else parse_dither(dither, field_path="--dither"),
        bayer_scale=(
            None
            if bayer_scale is None
            else parse_bayer_scale(bayer_scale, field_path="--bayer-scale")
        ),
    )


# ---------------------------------------------------------------------------
# Filter-chain construction (section 15.2, SEC-018)
# ---------------------------------------------------------------------------


def fps_arg(fps: float) -> str:
    """Serialize an effective frame rate for the ``fps`` filter."""
    if abs(fps - round(fps)) < 1e-6:
        return str(round(fps))
    return f"{fps:.5f}".rstrip("0").rstrip(".")


def build_filter_chain(
    *,
    crop: CropRect | None,
    speed: Decimal | None,
    fps: float,
    width: int,
    height: int,
) -> str:
    """Build the shared step 4-7 filter chain (section 15.2).

    Order is normative: orientation normalization (performed by FFmpeg's
    autorotation before the graph) -> crop -> setpts -> fps -> scale. The exact
    string returned here is used by BOTH palette passes so the palette is
    derived from exactly the frames that are encoded (SEC-018).
    """
    parts: list[str] = []
    if crop is not None:
        if crop.needs_full_chroma:
            # FR-025: convert rather than adjust the requested rectangle.
            parts.append("format=yuv444p")
        parts.append(crop.filter_arg())
    if speed is not None and speed != DEFAULT_SPEED:
        parts.append(f"setpts=PTS/{speed_str(speed)}")
    parts.append(f"fps={fps_arg(fps)}")
    parts.append(f"scale={width}:{height}:flags=lanczos")
    return ",".join(parts)


def build_preview_filter_chain(
    *,
    crop: CropRect | None,
    width: int,
    height: int,
) -> str:
    """Build the still-frame chain: steps 4 and 7 only (FR-029, section 15.2)."""
    parts: list[str] = []
    if crop is not None:
        if crop.needs_full_chroma:
            parts.append("format=yuv444p")
        parts.append(crop.filter_arg())
    parts.append(f"scale={width}:{height}:flags=lanczos")
    return ",".join(parts)


def not_applicable_warning(names: list[str]) -> str:
    """Build the FR-029 warning for settings that a still frame ignores."""
    listed = ", ".join(names)
    if len(names) == 1:
        return (
            f"{WARN_TRANSFORMATION_NOT_APPLICABLE}: {listed} does not apply to a preview "
            "frame and was ignored."
        )
    return (
        f"{WARN_TRANSFORMATION_NOT_APPLICABLE}: {listed} do not apply to a preview frame "
        "and were ignored."
    )
