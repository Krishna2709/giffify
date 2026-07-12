"""Structured error handling for the video-to-gif engine (spec NFR-003, section 14).

Every failure surfaced to the caller carries a stable error code, a human
readable message, the processing stage, the relevant clip index (when
applicable) and a remediation hint. Internal stack traces are never shown
unless ``--debug`` is supplied (handled by the CLI layer).
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Exit codes (spec section 14)
# ---------------------------------------------------------------------------
EXIT_SUCCESS = 0
EXIT_INVALID_USAGE = 2  # Invalid CLI usage or malformed schema
EXIT_DEPENDENCY_MISSING = 3  # Required dependency missing
EXIT_INPUT_NOT_FOUND = 4  # Input not found or inaccessible
EXIT_INVALID_MEDIA = 5  # Invalid or unsupported media
EXIT_INVALID_TIMESTAMP = 6  # Invalid timestamp or clip definition
EXIT_COLLISION = 7  # Output collision
EXIT_PERMISSION = 8  # Filesystem permission or project-boundary violation
EXIT_FFMPEG_FAILED = 9  # FFmpeg conversion failure
EXIT_CANCELLED = 10  # Operation cancelled
EXIT_PARTIAL = 11  # Partial batch success
EXIT_INTERNAL = 12  # Internal engine error
EXIT_RESOURCE_LIMIT = 13  # Resource limit exceeded


# ---------------------------------------------------------------------------
# Stable error codes (strings, part of the structured contract)
# ---------------------------------------------------------------------------
INVALID_USAGE = "INVALID_USAGE"
INVALID_CONFIG = "INVALID_CONFIG"
INVALID_MANIFEST = "INVALID_MANIFEST"
DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
INPUT_NOT_FOUND = "INPUT_NOT_FOUND"
INPUT_NOT_READABLE = "INPUT_NOT_READABLE"
UNSUPPORTED_REMOTE_SOURCE = "UNSUPPORTED_REMOTE_SOURCE"
UNSUPPORTED_MEDIA_CONTAINER = "UNSUPPORTED_MEDIA_CONTAINER"
UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
AMBIGUOUS_VIDEO_STREAM = "AMBIGUOUS_VIDEO_STREAM"
NO_VIDEO_STREAM = "NO_VIDEO_STREAM"
INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
INVALID_DURATION = "INVALID_DURATION"
INVALID_CLIP = "INVALID_CLIP"
INVALID_LOOP = "INVALID_LOOP"
INVALID_PROFILE = "INVALID_PROFILE"
OUTPUT_COLLISION = "OUTPUT_COLLISION"
PERMISSION_DENIED = "PERMISSION_DENIED"
PROJECT_BOUNDARY_VIOLATION = "PROJECT_BOUNDARY_VIOLATION"
FFMPEG_FAILED = "FFMPEG_FAILED"
CANCELLED = "CANCELLED"
INTERNAL_ERROR = "INTERNAL_ERROR"
RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"

# Result status values (spec section 13.2)
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial_success"
STATUS_FAILED = "failed"
STATUS_VALIDATION_FAILED = "validation_failed"
STATUS_COLLISION = "collision"
STATUS_DEPENDENCY_MISSING = "dependency_missing"
STATUS_CANCELLED = "cancelled"
STATUS_DRY_RUN = "dry_run"


class EngineError(Exception):
    """A structured, non-fatal engine error.

    Carries everything required to build the structured result contract and
    to choose the right process exit code without leaking a stack trace.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = EXIT_INTERNAL,
        status: str = STATUS_FAILED,
        stage: str | None = None,
        clip_index: int | None = None,
        remediation: str | None = None,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.status = status
        self.stage = stage
        self.clip_index = clip_index
        self.remediation = remediation
        self.field = field
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the structured error object (spec NFR-003)."""
        data: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        # Always include stage and clipIndex keys for a stable shape, but keep
        # them null when not applicable.
        data["stage"] = self.stage
        data["clipIndex"] = self.clip_index
        if self.field is not None:
            data["field"] = self.field
        if self.remediation is not None:
            data["remediation"] = self.remediation
        if self.details:
            data["details"] = self.details
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"EngineError(code={self.code!r}, exit_code={self.exit_code}, "
            f"stage={self.stage!r}, clip_index={self.clip_index!r}, "
            f"message={self.message!r})"
        )


class CancelledError(EngineError):
    """Raised when the operation is cancelled by a signal."""

    def __init__(self, message: str = "Operation cancelled by user.", **kwargs: Any) -> None:
        kwargs.setdefault("exit_code", EXIT_CANCELLED)
        kwargs.setdefault("status", STATUS_CANCELLED)
        super().__init__(CANCELLED, message, **kwargs)
