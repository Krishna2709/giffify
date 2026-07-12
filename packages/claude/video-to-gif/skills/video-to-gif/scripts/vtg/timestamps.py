"""Timestamp and duration parsing (spec FR-004, FR-005).

Accepted forms::

    75            -> seconds
    75.5          -> seconds with fractional part
    01:15         -> MM:SS
    01:15.500     -> MM:SS.mmm
    00:01:15      -> HH:MM:SS
    00:01:15.500  -> HH:MM:SS.mmm

All values normalize to integer milliseconds. Malformed input always raises a
structured :class:`~vtg.errors.EngineError`, never an uncaught exception.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from . import errors

_NUMBER_RE = re.compile(r"^\d+(\.\d+)?$")
_MMSS_RE = re.compile(r"^(\d+):([0-5]?\d)(\.\d+)?$")
_HHMMSS_RE = re.compile(r"^(\d+):([0-5]?\d):([0-5]?\d)(\.\d+)?$")

_MAX_MS = 100 * 60 * 60 * 1000  # 100 hours; a sane upper bound guard.


def _frac_to_ms(frac: str) -> int:
    """Convert a fractional string like ``.500`` to milliseconds (rounded)."""
    if not frac:
        return 0
    digits = frac[1:]  # strip leading '.'
    try:
        value = Decimal("0." + digits)
    except InvalidOperation as exc:  # pragma: no cover - regex guarantees digits
        raise _invalid(frac, "fractional seconds") from exc
    # Round half up to milliseconds.
    ms = int((value * 1000).to_integral_value(rounding="ROUND_HALF_UP"))
    return ms


def _invalid(value: Any, what: str = "timestamp") -> errors.EngineError:
    return errors.EngineError(
        errors.INVALID_TIMESTAMP,
        f"Invalid {what}: {value!r}. Expected forms like 75, 75.5, MM:SS, "
        f"or HH:MM:SS with an optional .mmm fractional part.",
        exit_code=errors.EXIT_INVALID_TIMESTAMP,
        status=errors.STATUS_VALIDATION_FAILED,
        remediation="Use seconds (75, 75.5), MM:SS, or HH:MM:SS[.mmm].",
    )


def parse_timestamp(value: Any, *, field_path: str | None = None) -> int:
    """Parse a timestamp into integer milliseconds (>= 0).

    Numbers (``int``/``float``) are interpreted as seconds. Strings follow the
    FR-004 grammar. Negative values are rejected.
    """
    err = None
    if isinstance(value, bool):
        err = _invalid(value)
    elif isinstance(value, (int, float)):
        if (isinstance(value, float) and not math.isfinite(value)) or value < 0:
            err = _invalid(value)
        else:
            ms = round(float(value) * 1000)
            return _bounded(ms, value, field_path)
    elif isinstance(value, str):
        text = value.strip()
        if text == "":
            err = _invalid(value)
        elif _NUMBER_RE.match(text):
            whole, _, frac = text.partition(".")
            ms = int(whole) * 1000 + (_frac_to_ms("." + frac) if frac else 0)
            return _bounded(ms, value, field_path)
        else:
            m = _HHMMSS_RE.match(text)
            if m:
                hh, mm, ss, frac = m.group(1), m.group(2), m.group(3), m.group(4) or ""
                ms = (int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + _frac_to_ms(frac)
                return _bounded(ms, value, field_path)
            m = _MMSS_RE.match(text)
            if m:
                mm, ss, frac = m.group(1), m.group(2), m.group(3) or ""
                ms = (int(mm) * 60 + int(ss)) * 1000 + _frac_to_ms(frac)
                return _bounded(ms, value, field_path)
            err = _invalid(value)
    else:
        err = _invalid(value)

    if err is not None:
        if field_path:
            err.field = field_path
        raise err
    # Unreachable.
    raise _invalid(value)  # pragma: no cover


def _bounded(ms: int, original: Any, field_path: str | None) -> int:
    if ms < 0 or ms > _MAX_MS:
        err = _invalid(original)
        if field_path:
            err.field = field_path
        raise err
    return ms


def parse_duration(value: Any, *, field_path: str = "duration") -> int:
    """Parse a duration into integer milliseconds; MUST be strictly positive.

    Same grammar as :func:`parse_timestamp`. Bare numbers are seconds. In JSON
    manifests ``value`` may already be a number; in CSV/CLI it is a string.
    """
    try:
        ms = parse_timestamp(value, field_path=field_path)
    except errors.EngineError as exc:
        raise errors.EngineError(
            errors.INVALID_DURATION,
            f"Invalid duration: {value!r}. Use seconds (5, 5.5), MM:SS, or HH:MM:SS[.mmm].",
            exit_code=errors.EXIT_INVALID_TIMESTAMP,
            status=errors.STATUS_VALIDATION_FAILED,
            field=field_path,
            remediation="Provide a strictly positive duration.",
        ) from exc
    if ms <= 0:
        raise errors.EngineError(
            errors.INVALID_DURATION,
            f"Duration must be strictly positive, got {value!r} ({ms} ms).",
            exit_code=errors.EXIT_INVALID_TIMESTAMP,
            status=errors.STATUS_VALIDATION_FAILED,
            field=field_path,
            remediation="Provide a duration greater than zero.",
        )
    return ms


def format_hhmmss(ms: int) -> str:
    """Format milliseconds as ``HH:MM:SS.mmm`` (for FFmpeg -ss/-t inputs)."""
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_filename_stamp(ms: int) -> str:
    """Format milliseconds as ``HH-MM-SS.mmm`` for default output filenames.

    Example: ``60000 -> "00-01-00.000"`` (spec FR-011).
    """
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}-{minutes:02d}-{seconds:02d}.{millis:03d}"


def seconds_str(ms: int) -> str:
    """Return a plain seconds string (e.g. ``"5.000"``) for FFmpeg -t."""
    return f"{ms / 1000:.3f}"
