"""Path resolution, project-boundary enforcement, and URL detection.

Spec references: SEC-002 (path normalization), SEC-003 (project boundary),
SEC-005 (remote sources rejected without fetch), section 9.1 (project root).
"""

from __future__ import annotations

import os
import re

from . import errors

# Markers that identify a project root, in priority order.
_PROJECT_MARKERS = (
    ".video-to-gif.json",
    ".git",
    "pyproject.toml",
    "package.json",
    ".hg",
    ".svn",
)

# A scheme like ``http:``, ``https:``, ``ftp:``, ``s3:`` etc. Windows drive
# letters (``C:\\``) are single letters and are explicitly excluded.
_URL_SCHEME_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]+)://")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _url_scheme(value: str) -> str | None:
    """Return the lowercased ``scheme`` of a ``scheme://`` input, else None.

    A single-letter scheme (e.g. ``C:\\`` or ``C:/``) is a Windows drive letter,
    not a URL, and yields None.
    """
    if not isinstance(value, str):
        return None
    if _WINDOWS_DRIVE_RE.match(value):
        return None
    m = _URL_SCHEME_RE.match(value)
    if not m:
        # Only explicit ``scheme://`` forms are treated as URLs; schemeless forms
        # such as ``mailto:`` are not media sources.
        return None
    return m.group("scheme").lower()


def is_url(value: str) -> bool:
    """Return True if ``value`` looks like a remote URL (SEC-005).

    A single-letter scheme is treated as a Windows drive letter, not a URL. The
    ``file`` scheme is handled by :func:`reject_if_remote` (which rejects *any*
    scheme), so it is intentionally not reported here.
    """
    scheme = _url_scheme(value)
    return scheme is not None and scheme != "file"


def reject_if_remote(value: str) -> None:
    """Raise UNSUPPORTED_REMOTE_SOURCE for any URL input (SEC-005). No fetch.

    Any ``scheme://`` input — including ``file://`` — is a URL and must be
    rejected without a fetch. Windows drive paths (``C:\\...``) are local paths.
    """
    if _url_scheme(value) is not None:
        raise errors.EngineError(
            errors.UNSUPPORTED_REMOTE_SOURCE,
            f"Remote sources are not supported in version 0.1.0: {value!r}.",
            exit_code=errors.EXIT_INVALID_MEDIA,
            status=errors.STATUS_FAILED,
            stage="input",
            remediation="Download the video locally and pass a local file path.",
        )


def resolve_project_root(start: str | None = None) -> str:
    """Resolve the project root by walking up from ``start`` (section 9.1).

    Falls back to the current working directory when no marker is found.
    """
    current = os.path.abspath(start or os.getcwd())
    if os.path.isfile(current):
        current = os.path.dirname(current)
    node = current
    while True:
        for marker in _PROJECT_MARKERS:
            if os.path.exists(os.path.join(node, marker)):
                return node
        parent = os.path.dirname(node)
        if parent == node:
            break
        node = parent
    return current


def resolve_source_path(raw: str, *, project_root: str) -> str:
    """Resolve and validate a source path (rejects URLs, normalizes)."""
    reject_if_remote(raw)
    # Normalize backslashes on non-Windows for user convenience only when the
    # path does not exist as-is; keep native semantics otherwise.
    candidate = os.path.expanduser(raw)
    if not os.path.isabs(candidate):
        candidate = os.path.join(project_root, candidate)
    normalized = os.path.normpath(candidate)
    return normalized


def ensure_source_readable(path: str) -> None:
    if not os.path.exists(path):
        raise errors.EngineError(
            errors.INPUT_NOT_FOUND,
            f"Source not found: {path}",
            exit_code=errors.EXIT_INPUT_NOT_FOUND,
            status=errors.STATUS_FAILED,
            stage="input",
            remediation="Check the path and that the file exists.",
        )
    if os.path.isdir(path):
        raise errors.EngineError(
            errors.INPUT_NOT_FOUND,
            f"Source path is a directory, not a video file: {path}",
            exit_code=errors.EXIT_INPUT_NOT_FOUND,
            status=errors.STATUS_FAILED,
            stage="input",
            remediation="Provide a path to a specific video file.",
        )
    if not os.access(path, os.R_OK):
        raise errors.EngineError(
            errors.INPUT_NOT_READABLE,
            f"Source is not readable: {path}",
            exit_code=errors.EXIT_INPUT_NOT_FOUND,
            status=errors.STATUS_FAILED,
            stage="input",
            remediation="Check file permissions.",
        )


def is_within(path: str, root: str) -> bool:
    """Return True if the resolved ``path`` is inside ``root``."""
    path_abs = os.path.abspath(path)
    root_abs = os.path.abspath(root)
    try:
        common = os.path.commonpath([path_abs, root_abs])
    except ValueError:
        # Different drives on Windows.
        return False
    return common == root_abs


def resolve_output_directory(
    raw: str | None,
    *,
    project_root: str,
    allow_outside_project: bool,
) -> str:
    """Resolve the effective output directory and enforce the boundary (SEC-003)."""
    target = raw if raw else "./output"
    reject_if_remote(target)
    expanded = os.path.expanduser(target)
    if not os.path.isabs(expanded):
        expanded = os.path.join(project_root, expanded)
    normalized = os.path.normpath(expanded)
    if not is_within(normalized, project_root) and not allow_outside_project:
        raise errors.EngineError(
            errors.PROJECT_BOUNDARY_VIOLATION,
            f"Refusing to write outside the project root without authorization: {normalized}",
            exit_code=errors.EXIT_PERMISSION,
            status=errors.STATUS_FAILED,
            stage="plan",
            remediation="Pass --allow-outside-project to write to this location.",
            details={"resolvedPath": normalized, "projectRoot": project_root},
        )
    return normalized


def ensure_directory(path: str) -> None:
    """Create the output directory if missing (FR-010)."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise errors.EngineError(
            errors.PERMISSION_DENIED,
            f"Cannot create output directory {path}: {exc}",
            exit_code=errors.EXIT_PERMISSION,
            status=errors.STATUS_FAILED,
            stage="plan",
            remediation="Check directory permissions.",
        ) from exc


def resolve_within_directory(directory: str, filename: str) -> str:
    """Join and verify a sanitized filename cannot escape ``directory`` (SEC-002)."""
    joined = os.path.normpath(os.path.join(directory, filename))
    if os.path.dirname(joined) != os.path.normpath(directory):
        raise errors.EngineError(
            errors.PROJECT_BOUNDARY_VIOLATION,
            f"Output name escapes the output directory: {filename!r}.",
            exit_code=errors.EXIT_PERMISSION,
            status=errors.STATUS_FAILED,
            stage="plan",
            remediation="Use a bare filename without path separators.",
        )
    return joined
