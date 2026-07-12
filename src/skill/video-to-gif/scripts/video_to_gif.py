#!/usr/bin/env python3
"""Thin CLI entry point for the video-to-gif Agent Skill engine.

All logic lives in the :mod:`vtg` package (spec section 5). This wrapper only
ensures the package is importable and delegates to :func:`vtg.cli.main`.
"""

from __future__ import annotations

import os
import sys

# Ensure the sibling ``vtg`` package is importable when the script is invoked by
# absolute path (as agents do) rather than as a module.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from vtg.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
