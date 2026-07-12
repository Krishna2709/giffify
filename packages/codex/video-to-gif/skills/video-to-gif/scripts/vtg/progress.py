"""Progress reporting as JSON Lines on stderr (spec section 13.3).

Progress events MUST NOT corrupt the final JSON document on stdout, so they are
always written to stderr, one JSON object per line.
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
            self._stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._stream.flush()
        except (OSError, ValueError):  # pragma: no cover - stderr closed
            pass

    def clip_started(self, clip_index: int, total_clips: int, name: str | None = None) -> None:
        self.emit("clip_started", clipIndex=clip_index, totalClips=total_clips, name=name)

    def stage_progress(self, clip_index: int, stage: str, percent: float) -> None:
        self.emit("stage_progress", clipIndex=clip_index, stage=stage, percent=round(percent, 1))

    def clip_completed(self, clip_index: int, path: str) -> None:
        self.emit("clip_completed", clipIndex=clip_index, path=path)

    def clip_failed(self, clip_index: int, code: str, message: str) -> None:
        self.emit("clip_failed", clipIndex=clip_index, code=code, message=message)

    def clip_skipped(self, clip_index: int, reason: str) -> None:
        self.emit("clip_skipped", clipIndex=clip_index, reason=reason)


NULL_REPORTER = ProgressReporter(enabled=False)
