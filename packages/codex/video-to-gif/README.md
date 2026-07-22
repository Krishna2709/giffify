# video-to-gif (Codex plugin package)

Generate optimized animated GIFs from explicit video timestamp ranges. This
package wraps the portable `video-to-gif` Agent Skill for Codex.

- Version: 0.3.0
- Author: Krishna2709 (https://github.com/Krishna2709)
- License: MIT
- Source repository: https://github.com/Krishna2709/giffify
- Requirements: Python 3.10+ and the `ffmpeg`/`ffprobe` executables on PATH
  (`pip install ffmpeg` is NOT FFmpeg)

The skill itself lives under `skills/video-to-gif/` (SKILL.md plus the
deterministic Python engine, references, and JSON schemas). Conversion is local
by default; version 0.2.0 adds opt-in, download-only remote source acquisition
that is disabled by default and gated by explicit enablement/approval. See
`skills/video-to-gif/references/` and the repository's SECURITY.md for the
security model.

This directory is GENERATED from the canonical source at
`src/skill/video-to-gif/` by `tools/build_packages.py` — do not edit it by
hand. `CHECKSUMS.sha256` covers every file in this package.
