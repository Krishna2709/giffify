"""Robust temporary-artifact deletion shared across the engine (spec section 16).

Deleting a file or directory tree with a short bounded retry absorbs transient
lock errors. On Windows a just-terminated FFmpeg (or yt-dlp) process's file
handles are not released synchronously with ``proc.wait()``, and antivirus
scanners can hold brief locks, so ``os.remove``/``shutil.rmtree`` on the still
locked path raises ``PermissionError`` where POSIX would have unlinked the open
file immediately. Retrying for a short bounded window absorbs that lag; on POSIX
the first attempt succeeds, so the loop is a no-op.

This module is imported by :mod:`vtg.ffmpeg` (palette/temp GIF cleanup) and by
:mod:`vtg.remote` (partial-download cleanup) so both share one implementation.
"""

from __future__ import annotations

import os
import shutil
import time

# Bounded retry window for deleting temp artifacts after a subprocess is
# terminated (see module docstring). On POSIX the first attempt succeeds.
CLEANUP_RETRY_SECONDS = 2.0
CLEANUP_RETRY_INTERVAL = 0.05


def remove_path(path: str, *, deadline: float) -> None:
    """Delete a file or directory tree, retrying on transient lock errors.

    Deletes ``path`` (a file, symlink, or directory tree) and returns once it is
    gone or already absent. On ``PermissionError``/``OSError`` it retries in
    short sleeps until ``deadline`` (a ``time.monotonic()`` value), then gives up
    best-effort so cleanup never blocks or raises.
    """
    while True:
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            elif os.path.lexists(path):
                os.remove(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if time.monotonic() >= deadline:
                return  # best-effort: never block or raise from cleanup
            time.sleep(CLEANUP_RETRY_INTERVAL)


def remove_paths(paths: list[str]) -> None:
    """Remove several temp artifacts, sharing one bounded retry deadline."""
    deadline = time.monotonic() + CLEANUP_RETRY_SECONDS
    for path in paths:
        remove_path(path, deadline=deadline)
