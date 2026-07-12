# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(spec §24).

## [Unreleased]

Changes staged for the next release beyond 0.1.0 will be listed here.

## [0.1.0] - Unreleased

Initial implementation of the `video-to-gif` Agent Skill: deterministic,
timestamp-based conversion of **local** video files into optimized animated GIFs,
packaged for both Claude Code and Codex from one shared source.

### Added

- **Agent Skill** (`src/skill/video-to-gif/`) with `SKILL.md`, `scripts/`,
  `references/`, and `assets/`, following the open Agent Skills format.
- **Deterministic Python engine** (`scripts/video_to_gif.py` + `vtg/` package),
  standard library only, non-interactive, with a structured (`--json`) result
  contract and JSON Lines progress on stderr.
- **CLI commands** (spec §12): `doctor`, `inspect`, `create`, `batch`
  (including `--dry-run` preflight), `validate-config`, and `validate-manifest`;
  `create` supports `--start` with either `--end` or `--duration`, and an
  explicit `--output-name`.
- **Timestamp handling**: accepts `SS`, `SS.mmm`, `MM:SS`, `HH:MM:SS` forms and
  normalizes to integer milliseconds; preflight validation before any encoding
  with `fail` / `skip` / `clamp` policies (default `fail`).
- **Batch conversion** from repeated ranges, JSON manifests, and CSV manifests,
  with `continueOnError` (default true) and per-clip created/failed/skipped
  reporting.
- **Quality profiles** `small`, `balanced`, `high`, and `custom` (widths are
  maximums; aspect ratio preserved; no upscaling by default).
- **FFmpeg two-pass palette pipeline** (`palettegen` → `paletteuse`) writing to a
  temporary path and atomically moving verified output into place.
- **Project configuration** (`.video-to-gif.json`) with documented precedence:
  CLI arg > request instruction > project config > built-in default.
- **Collision protection**: engine never overwrites by default; policies `fail`,
  `overwrite`, `unique`, `skip`.
- **Cancellation and cleanup** that stop FFmpeg, remove partial/palette/temporary
  files, and preserve completed GIFs.
- **Security controls** SEC-001..SEC-011, including FFmpeg network isolation
  (`-protocol_whitelist file,pipe`, rejection of reference-following containers)
  and resource limits (default 600 s wall-clock / 2 GiB temporary disk per clip;
  `RESOURCE_LIMIT_EXCEEDED`, exit 13).
- **Packaging tooling**: `tools/build_packages.py` (generates the Claude and Codex
  plugin packages from canonical source, verifies byte-identical copies, rejects
  media/temp files, emits SHA-256 checksums) and `tools/validate_release.py`
  (release integrity checks per spec §21.4).
- **Marketplace metadata** for Claude Code (`.claude-plugin/marketplace.json`)
  and Codex (`.agents/plugins/marketplace.json`), pinning version 0.1.0.
- **Documentation**: `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and
  `docs/architecture.md`, `docs/security.md`, `docs/release-process.md`.
- **Continuous integration** matrix across Ubuntu, macOS, and Windows on Python
  3.10 and 3.12 (spec §22.3).

### Fixed

- **Windows cancellation and temp-file cleanup** (spec §6.1, §16, SEC-011). The
  engine now registers a `SIGBREAK` handler on Windows alongside `SIGINT`/
  `SIGTERM`, so a `CTRL_BREAK_EVENT` delivered to its process group cancels
  cleanly. All cleanup paths (resource-limit breach, cancellation, failure, and
  the conversion pipeline's temp-dir removal) now wait for the terminated FFmpeg
  process to exit and delete temp artifacts with a bounded retry, so partial
  `.vtg-*.gif.tmp` output and `vtg-*` palette temp directories no longer leak on
  Windows where a just-killed process briefly holds its file handles. The
  cancellation test harness launches the engine with
  `CREATE_NEW_PROCESS_GROUP` and cancels via `CTRL_BREAK_EVENT` on Windows
  (SIGINT elsewhere).

### Security

- Local-only processing with no network access in 0.1.0; URL sources return
  `UNSUPPORTED_REMOTE_SOURCE` (SEC-005). Network isolation is enforced at the
  FFmpeg layer (SEC-010). See [`SECURITY.md`](SECURITY.md).

### Notes

- Resolved decisions (spec §26): repository `giffify`, plugin name
  `video-to-gif`, maintainer Krishna2709, license MIT. Remaining open items
  (exact profile values and others) are tracked in spec §26.

[Unreleased]: https://github.com/Krishna2709/giffify/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Krishna2709/giffify/releases/tag/v0.1.0
