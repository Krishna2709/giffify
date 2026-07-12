"""Output filename generation and sanitization (spec FR-011, FR-012, SEC-002).

Generated names must:
  * Exclude characters invalid on Windows.
  * Prevent directory traversal (no separators).
  * Preserve the ``.gif`` extension.
  * Remain deterministic.
  * Preserve the timestamp suffix if shortening is required.
  * Avoid reserved Windows device names.
  * Be limited to a safe filename length.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from . import errors
from .timestamps import format_filename_stamp

# Characters invalid in Windows filenames, plus control characters.
_WINDOWS_INVALID = set('<>:"/\\|?*')
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Reserved Windows device names (case-insensitive, with or without extension).
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Maximum safe filename length (bytes/characters). Chosen conservatively so the
# name fits comfortably within common filesystem limits (255) while leaving
# headroom for collision suffixes.
MAX_FILENAME_LENGTH = 200


def _replace_invalid(text: str) -> str:
    out = []
    for ch in text:
        if ch in _WINDOWS_INVALID:
            out.append("_")
        else:
            out.append(ch)
    text = "".join(out)
    text = _CONTROL_RE.sub("_", text)
    return text


def sanitize_stem(stem: str) -> str:
    """Sanitize a filename stem (no extension) deterministically."""
    stem = _replace_invalid(stem)
    # Collapse path separators that may have survived (defense in depth).
    stem = stem.replace("/", "_").replace("\\", "_")
    # Strip leading/trailing whitespace and dots (Windows strips trailing dots).
    stem = stem.strip().strip(". ")
    # A stem that reduced to only filler characters is not a usable name.
    if stem.strip("_. ") == "":
        stem = "clip"
    # Reserved device name check (case-insensitive) against the whole stem.
    if stem.upper() in _RESERVED_NAMES:
        stem = "_" + stem
    return stem


def sanitize_output_name(name: str) -> str:
    """Sanitize a user-supplied bare output filename (FR-011).

    The input MUST be a bare filename; path separators are rejected up front by
    the CLI/manifest layers, but we defensively strip them here too. Always
    returns a name ending in ``.gif`` within the safe length cap.
    """
    if name is None:
        raise errors.EngineError(
            errors.INVALID_USAGE,
            "Output name must not be empty.",
            exit_code=errors.EXIT_INVALID_USAGE,
            status=errors.STATUS_VALIDATION_FAILED,
            field="output-name",
        )
    # Reject explicit path separators (SEC-002) before sanitizing.
    if "/" in name or "\\" in name or "\x00" in name:
        raise errors.EngineError(
            errors.INVALID_USAGE,
            f"Output name must be a bare filename without path separators: {name!r}.",
            exit_code=errors.EXIT_INVALID_USAGE,
            status=errors.STATUS_VALIDATION_FAILED,
            field="output-name",
            remediation="Provide just a filename, e.g. 'opening.gif'.",
        )
    # Reject traversal tokens.
    base = name.strip()
    if base in ("", ".", ".."):
        raise errors.EngineError(
            errors.INVALID_USAGE,
            f"Output name is not a valid filename: {name!r}.",
            exit_code=errors.EXIT_INVALID_USAGE,
            status=errors.STATUS_VALIDATION_FAILED,
            field="output-name",
        )
    # Split off a .gif extension if present (case-insensitive); we always end .gif.
    stem = base
    if stem.lower().endswith(".gif"):
        stem = stem[:-4]
    stem = sanitize_stem(stem)
    return _cap_length(stem, suffix=None) + ".gif"


def default_output_name(video_stem: str, start_ms: int, end_ms: int) -> str:
    """Build the default output name ``<stem>_<start>_to_<end>.gif`` (FR-011)."""
    stem = sanitize_stem(video_stem)
    start = format_filename_stamp(start_ms)
    end = format_filename_stamp(end_ms)
    suffix = f"_{start}_to_{end}"
    stem = _cap_length(stem, suffix=suffix)
    return f"{stem}{suffix}.gif"


def _cap_length(stem: str, suffix: str | None) -> str:
    """Cap total filename length, preserving the timestamp suffix and .gif."""
    ext = ".gif"
    reserved = len(ext) + (len(suffix) if suffix else 0)
    budget = MAX_FILENAME_LENGTH - reserved
    if budget < 1:
        budget = 1
    if len(stem) > budget:
        stem = stem[:budget].rstrip(". ")
        if stem == "":
            stem = "clip"
    return stem


def unique_name(name: str, exists: Callable[[str], bool]) -> str:
    """Return a collision-free variant of ``name`` by appending ``-N`` (FR-012).

    ``exists`` is a predicate returning ``True`` when a candidate filename is
    already taken (on disk or already planned within the job).
    """
    if not exists(name):
        return name
    if name.lower().endswith(".gif"):
        stem, ext = name[:-4], name[-4:]
    else:
        stem, ext = name, ""
    counter = 1
    while True:
        candidate = f"{stem}-{counter}{ext}"
        if not exists(candidate):
            return candidate
        counter += 1
