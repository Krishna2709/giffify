# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(spec §24).

## [Unreleased]

_No unreleased changes yet._

## [0.2.0] - 2026-07-12

Opt-in remote source acquisition: `video-to-gif` can now download a source from a
remote `http`/`https` URL before converting it locally. The capability is
**disabled by default** and gated by explicit enablement/approval; conversion
itself stays local and network-isolated, and all remote access is download-only.

### Added

- **Opt-in remote HTTP/HTTPS source acquisition** (spec FR-018..023): a URL may be
  supplied wherever a source is accepted (`--input` or a manifest `input` field),
  downloaded into a secure temporary directory and handed to the existing local
  pipeline as untrusted local media.
- **Remote CLI flags**: `--allow-remote` (enable acquisition for one run),
  `--keep-remote-source` (retain the download and report its path),
  `--remote-adapter ytdlp` (acquire a video-page URL through the optional,
  never-bundled yt-dlp adapter), `--allow-insecure-http` (acknowledge an
  unencrypted http transfer), and `--allow-remote-address` (approve a specific
  private/loopback address for the SSRF check; repeatable).
- **Configuration**: `remoteSources` (`disabled` (default) / `ask` / `enabled`),
  `keepRemoteSource`, and download limits `limits.maxDownloadBytes`
  (default 2 GiB) and `limits.maxDownloadSeconds` (default 900 s).
- **Download progress** emitted on stderr under stage `download` (bytes received
  and, when the total size is known, a percentage).
- **Exit code 14** (`REMOTE_DOWNLOAD_FAILED`) for a network error, HTTP error
  status, or truncated/timed-out remote download.
- **`doctor` yt-dlp reporting**: `doctor` now reports whether the optional yt-dlp
  adapter is available and, when present, its version. Its absence is never a
  failure.

### Changed

- A URL supplied under the **default** configuration now returns `REMOTE_DISABLED`
  (status `remote_disabled`, exit 8) and performs no network access. In 0.1.0 the
  same situation returned `UNSUPPORTED_REMOTE_SOURCE`; that remains the documented
  behavior of the 0.1.0 product.

### Security

- **SEC-012 — Remote source network boundary.** Only the acquisition component is
  network-capable; inspection/palette/encode stay network-isolated under SEC-010,
  and a downloaded file is treated as untrusted local media.
- **SEC-013 — URL scheme allowlist.** `https` always; `http` only with an explicit
  unencrypted-transfer warning; `file` and every other scheme rejected as
  `UNSUPPORTED_URL_SCHEME` (exit 5), enforced before any connection and on every
  redirect.
- **SEC-014 — Private-network / SSRF protection and connection pinning.** Loopback,
  private, link-local/unique-local, and cloud-metadata addresses are blocked as
  `PRIVATE_NETWORK_BLOCKED` (exit 8) unless explicitly approved; the direct path
  resolves the host, validates every address, and pins the connection to the
  validated address (DNS-rebinding resistant), re-checking every redirect.
- **SEC-015 — Credential and token redaction.** Any URL echoed anywhere is reduced
  to scheme/host/path; signed-URL query strings and userinfo are stripped and
  never stored in configuration or manifests.
- **SEC-016 — Download hardening.** Size ceiling enforced on bytes actually
  received, wall-clock timeout, and a free-disk pre-check; partial downloads are
  always removed.
- **SEC-017 — DRM and access-control integrity.** DRM-protected/access-controlled
  sources are rejected as `DRM_PROTECTED` (exit 5); no circumvention is attempted.
- **yt-dlp adapter residual risk.** The optional yt-dlp adapter performs its own
  DNS resolution, connection, and redirect-following that the engine cannot pin.
  Its scheme, DRM, and SSRF host checks are enforced **best-effort before launch**
  but do not carry the direct path's connection-pinning guarantee; a
  TOCTOU/rebinding change or a redirect yt-dlp follows to a private address is not
  guaranteed to be blocked (accepted residual risk, documented per SEC-014).

### Fixed

- **Marketplace files moved to the repository root** (`.claude-plugin/marketplace.json`
  and `.agents/plugins/marketplace.json`, previously under `marketplaces/`).
  `claude plugin marketplace add owner/repo` resolves the marketplace manifest at
  the repository root, so the nested layout made the plugin uninstallable from
  the repo. Plugin `source` paths now correctly resolve to `./packages/...` from
  the root.

## [0.1.0] - 2026-07-12

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
- **Portable structured paths on Windows** (spec §6.1, §13). The `path` and
  `outputDirectory` fields of the JSON result now always use forward slashes, so
  the structured contract is deterministic across platforms instead of leaking
  Windows `\` separators. The test fixtures also clean their own temp
  directories with a bounded retry so a subprocess's transient handle lock on
  Windows cannot fail teardown.

### Security

- Local-only processing with no network access in 0.1.0; URL sources return
  `UNSUPPORTED_REMOTE_SOURCE` (SEC-005). Network isolation is enforced at the
  FFmpeg layer (SEC-010). See [`SECURITY.md`](SECURITY.md).

### Notes

- Resolved decisions (spec §26): repository `giffify`, plugin name
  `video-to-gif`, maintainer Krishna2709, license MIT. Remaining open items
  (exact profile values and others) are tracked in spec §26.

[Unreleased]: https://github.com/Krishna2709/giffify/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Krishna2709/giffify/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Krishna2709/giffify/releases/tag/v0.1.0
