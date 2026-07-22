"""Progress reporting as JSON Lines on stderr (spec section 13.3).

Progress events MUST NOT corrupt the final JSON document on stdout, so they are
always written to stderr, one JSON object per line.

Every line is ASCII-escaped and the stream itself is pinned to UTF-8 by
:func:`vtg.cli.configure_output_encoding` (spec section 13.5), so a non-ASCII
clip name or path can never raise an encoding error under a non-UTF-8 host
locale such as a Windows console codepage.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO


class ProgressReporter:
    def __init__(self, enabled: bool = True, stream: TextIO | None = None) -> None:
        self.enabled = enabled
        self._stream = stream if stream is not None else sys.stderr

    def emit(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        payload: dict[str, Any] = {"event": event}
        payload.update(fields)
        try:
            # ensure_ascii=True (spec section 13.5): progress lines carry
            # user-controlled text (clip names, output paths, FFmpeg messages),
            # so they are escaped to pure ASCII rather than trusted to encode
            # under the host locale. UnicodeEncodeError is caught alongside the
            # closed-stream cases as a last resort -- a progress event MUST NEVER
            # be able to take down a run that is otherwise succeeding.
            self._stream.write(json.dumps(payload, ensure_ascii=True) + "\n")
            self._stream.flush()
        except (OSError, ValueError):  # pragma: no cover - stderr closed/unencodable
            pass

    def clip_started(self, clip_index: int, total_clips: int, name: str | None = None) -> None:
        self.emit("clip_started", clipIndex=clip_index, totalClips=total_clips, name=name)

    def stage_progress(self, clip_index: int, stage: str, percent: float) -> None:
        self.emit("stage_progress", clipIndex=clip_index, stage=stage, percent=round(percent, 1))

    def download_progress(
        self,
        bytes_received: int,
        total_bytes: int | None,
        percent: float | None = None,
    ) -> None:
        """Emit a remote-download progress event (spec section 13.3, FR-023).

        Uses stage ``"download"`` with ``bytesReceived`` and, when the total size
        is known, ``totalBytes`` and ``percent``. ``totalBytes`` MAY be null and
        ``percent`` is then omitted. Any URL echoed here MUST already be redacted
        by the caller under SEC-015; this method never receives a URL.
        """
        fields: dict[str, Any] = {
            "stage": "download",
            "bytesReceived": bytes_received,
            "totalBytes": total_bytes,
        }
        if percent is not None and total_bytes is not None:
            fields["percent"] = round(percent, 1)
        self.emit("stage_progress", **fields)

    def preview_progress(self, clip_index: int, percent: float) -> None:
        """Emit preview-extraction progress with stage ``preview`` (13.3, FR-029)."""
        self.stage_progress(clip_index, "preview", percent)

    def clip_completed(self, clip_index: int, path: str) -> None:
        self.emit("clip_completed", clipIndex=clip_index, path=path)

    def clip_failed(self, clip_index: int, code: str, message: str) -> None:
        self.emit("clip_failed", clipIndex=clip_index, code=code, message=message)

    def clip_skipped(self, clip_index: int, reason: str) -> None:
        self.emit("clip_skipped", clipIndex=clip_index, reason=reason)


NULL_REPORTER = ProgressReporter(enabled=False)
