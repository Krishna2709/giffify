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


def sanitize_output_name(name: str, *, ext: str = ".gif") -> str:
    """Sanitize a user-supplied bare output filename (FR-011).

    The input MUST be a bare filename; path separators are rejected up front by
    the CLI/manifest layers, but we defensively strip them here too. Always
    returns a name ending in ``ext`` within the safe length cap. ``ext`` is
    ``.png`` for preview frames, where every FR-011 rule applies unchanged with
    ``.png`` substituted for ``.gif`` (FR-029).
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
    # Split off the target extension if present (case-insensitive); we always
    # end with it.
    stem = base
    if stem.lower().endswith(ext.lower()):
        stem = stem[: -len(ext)]
    stem = sanitize_stem(stem)
    return _cap_length(stem, suffix=None, ext=ext) + ext


def sanitize_preview_name(name: str) -> str:
    """Sanitize a user-supplied preview filename, enforcing ``.png`` (FR-029).

    A name with no extension gains ``.png``; a name with any other extension is
    rejected with INVALID_USAGE and exit code 2.
    """
    if not isinstance(name, str) or name.strip() == "":
        raise errors.EngineError(
            errors.INVALID_USAGE,
            "Preview output name must not be empty.",
            exit_code=errors.EXIT_INVALID_USAGE,
            status=errors.STATUS_VALIDATION_FAILED,
            field="output-name",
        )
    base = name.strip()
    # Only inspect the extension here; sanitize_output_name performs the
    # separator, traversal, reserved-name, and length checks of FR-011.
    dot = base.rfind(".")
    if dot > 0:
        suffix = base[dot:]
        if suffix.lower() != ".png":
            raise errors.EngineError(
                errors.INVALID_USAGE,
                f"A preview output name must end in .png, got {name!r}.",
                exit_code=errors.EXIT_INVALID_USAGE,
                status=errors.STATUS_VALIDATION_FAILED,
                field="output-name",
                remediation="Use a .png filename, or omit the extension entirely.",
            )
    return sanitize_output_name(base, ext=".png")


def default_output_name(video_stem: str, start_ms: int, end_ms: int) -> str:
    """Build the default output name ``<stem>_<start>_to_<end>.gif`` (FR-011)."""
    stem = sanitize_stem(video_stem)
    start = format_filename_stamp(start_ms)
    end = format_filename_stamp(end_ms)
    suffix = f"_{start}_to_{end}"
    stem = _cap_length(stem, suffix=suffix)
    return f"{stem}{suffix}.gif"


def default_preview_name(stem_source: str, at_ms: int) -> str:
    """Build the default preview name ``<stem>_<at>.png`` (FR-029).

    ``stem_source`` is the video stem for the single-frame form, or the clip
    name for the manifest form, where a named clip yields
    ``<clip-name>_<start>.png``.
    """
    stem = sanitize_stem(stem_source)
    suffix = f"_{format_filename_stamp(at_ms)}"
    stem = _cap_length(stem, suffix=suffix, ext=".png")
    return f"{stem}{suffix}.png"


def _cap_length(stem: str, suffix: str | None, ext: str = ".gif") -> str:
    """Cap total filename length, preserving the timestamp suffix and extension."""
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
    dot = name.rfind(".")
    if dot > 0:
        stem, ext = name[:dot], name[dot:]
    else:
        stem, ext = name, ""
    counter = 1
    while True:
        candidate = f"{stem}-{counter}{ext}"
        if not exists(candidate):
            return candidate
        counter += 1
