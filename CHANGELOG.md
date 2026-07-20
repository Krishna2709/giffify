# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(spec §24).

## [Unreleased]

### Fixed

- **Output encoding is no longer locale-dependent** (spec §13.5, §6.1). The final
  JSON document and the JSON Lines progress stream were written to streams whose
  encoding defaulted to the host locale — the console codepage on Windows
  (cp1252/cp437). Any character outside that codepage in the result raised
  `UnicodeEncodeError` inside the writer *after* the outcome had been decided, so
  the process exited with code 1 — which §14 deliberately leaves undefined — and
  standard output carried no structured result at all. A source filename in CJK,
  Cyrillic, or emoji was enough to trigger it on a fully successful conversion,
  leaving the agent layer blind. Both streams are now pinned to UTF-8 at engine
  entry, and the final document is escaped to pure ASCII so it survives any
  consumer, pipe, or redirection. Escaping is lossless: parsing returns the
  original string.
- **Subprocess output is decoded as UTF-8** rather than with the locale default,
  so a non-ASCII path quoted back by ffprobe, FFmpeg, or yt-dlp cannot fail to
  decode (spec §13.5).

## [0.3.0] - 2026-07-12

Transformations without text: clips can now be cropped, explicitly resized,
speed-adjusted, and dithered, and a new `preview` command extracts a single
full-colour PNG still so framing can be confirmed before any GIF is encoded.
Every transformation parameter is an integer, a bounded decimal, or a member of a
fixed enumeration, so no user-supplied text ever reaches an FFmpeg filter graph.
Captions and subtitle burn-in are deliberately excluded and deferred to 0.4.0
(spec §3.3, §25.4).

### Added

- **Cropping** (spec FR-025): `--crop <x>:<y>:<w>:<h>` in orientation-normalized
  source pixels, applied **before** scaling, so aspect-ratio preservation, the
  profile maximum width, and the no-upscale rule all evaluate against the cropped
  rectangle. The requested rectangle is applied exactly — never rounded, clamped,
  re-centered, or expanded; a rectangle the decoded pixel format cannot express
  triggers a conversion to a non-subsampled format instead of an adjustment.
- **Explicit resizing** (spec FR-026): `--width` and the new `--height`, both
  integers in the closed range 2–8192. They are *maximum bounds* with the aspect
  ratio preserved; supplying both fits the frame inside that box. An explicit
  bound overrides the effective quality profile's maximum width. Upscaling
  remains gated by `--allow-upscale`, and an oversized request is clamped back to
  the effective source size.
- **Playback-speed adjustment** (spec FR-027): `--speed`, a decimal multiplier
  from 0.25 to 4.0 with at most three fractional digits, implemented by retiming
  presentation timestamps. The selected source range is unchanged; the output GIF
  duration becomes `round(durationMs / speed)`. No frames are interpolated, and
  retiming is applied before frame-rate conversion so the requested fps describes
  the finished GIF.
- **Dithering control** (spec FR-028): `--dither` with the fixed enumeration
  `none`, `bayer`, `floyd_steinberg`, `sierra2`, `sierra2_4a`, plus
  `--bayer-scale` (0–5) for `bayer`. Documented per-profile defaults (§15.5):
  `bayer` with scale 5 for `small`, `sierra2_4a` for `balanced`, `high`, and
  `custom` — these reproduce 0.1.0/0.2.0 output, so a job that specifies no
  dither is functionally equivalent to earlier releases.
- **`preview` subcommand** (spec FR-029, §12.9): extracts a single full-colour
  PNG still instead of producing a GIF. `preview --input <source> --at
  <timestamp>` for one frame, or `preview --manifest <manifest>` for one still
  per clip at that clip's start timestamp with that clip's effective
  transformations. Preview output is never palette-quantized. Crop, resize, and
  orientation normalization apply exactly as they would for the corresponding
  GIF; `speed`, `fps`, `loop`, `colors`, `dither`, and `bayerScale` are accepted,
  change nothing, and produce one `TRANSFORMATION_NOT_APPLICABLE` warning.
  `--dry-run`, collision policies, project-boundary rules, and remote sources all
  apply unchanged. Generated names are `<video-stem>_<at>.png`, or
  `<clip-name>_<start>.png` in the manifest form.
- **Configuration**: a `transformations` object in `.video-to-gif.json` with
  `width`, `height`, `speed`, `dither`, and `bayerScale` (spec §9.6). `crop` is
  **not** permitted there — a rectangle is only meaningful against one specific
  source — and `validate-config` rejects it with the field path
  `transformations.crop`.
- **Manifest fields** (spec §10.4, §11.2): `crop`, `width`, `height`, `speed`,
  `dither`, and `bayerScale` at the top level, at the clip level, or both, in
  both JSON and CSV manifests. JSON accepts either the object form
  `{ "x", "y", "width", "height" }` or the string form `"x:y:width:height"`; CSV
  uses the string form in a `crop` column, where an empty cell means "not
  specified for this row".
- **Transformation precedence** (spec FR-024, refining §9.3): clip-level manifest
  field > command-line flag > top-level manifest field > project configuration >
  built-in default. A per-clip value is more specific than a batch-wide flag and
  wins; every non-transformation setting follows §9.3 unchanged.
- **Result reporting** (spec FR-030): every `created` entry gains a
  `transformations` object (`crop`, `sourceWidth`, `sourceHeight`,
  `effectiveSourceWidth`, `effectiveSourceHeight`, `speed`, `dither`,
  `bayerScale`, `upscaled`) and `outputDurationMs`; a `previews` array carries
  `path`, `atMs`, `width`, `height`, `sizeBytes`, and the same `transformations`
  object; `summary` gains a `previews` count. Preview entries never appear in
  `created` and are never counted by `summary.created`. Preview extraction emits
  progress under stage `preview`.
- **Warnings** with stable leading tokens (spec §13.4): `UPSCALE_NOT_ALLOWED`
  when an explicitly supplied bound was clamped to the effective source size, and
  `TRANSFORMATION_NOT_APPLICABLE` when settings a still frame cannot express were
  supplied to `preview`.
- **Documentation**: new `references/transformations.md` (spec NFR-007) covering
  the crop coordinate model, the width/height bounds and their interaction with
  profiles and upscaling, the speed multiplier and its duration math, the dither
  enumeration with size/quality guidance, preview frames, per-clip manifest
  transformations, and the filter-chain order.

### Changed

- **Output dimensions are unchanged.** Every 0.1.0 and 0.2.0 invocation produces
  the same GIF it did: with no transformation specified, output is
  byte-comparable to 0.2.0 for the same source, range, profile, and
  configuration. `--width` keeps its 0.1.0 meaning as a maximum output width, and
  its derived height is unchanged. This was verified by executing the released
  0.2.0 engine and 0.3.0 side by side over profile-only, `--width`, and manifest
  `width` invocations across several source geometries and comparing both the
  reported dimensions and the produced GIF bytes. Configuration, manifest, and
  structured result schema versions all remain `1`, and no new exit code is
  introduced — invalid transformations reuse exit 6 (`INVALID_CROP`,
  `INVALID_DIMENSIONS`, `INVALID_SPEED`, `INVALID_DITHER`), a `preview
  --output-name` with a non-`.png` extension reuses exit 2 (`INVALID_USAGE`), and
  a preview collision reuses exit 7.
- **Dimension parity, stated exactly** (spec FR-026). An explicitly supplied
  `width`/`height` is honored exactly, odd values included: GIF is a palette-based
  format without chroma subsampling, so it imposes no even-dimension constraint
  and rounding an explicit bound would silently contradict the request. A
  *derived* dimension is rounded to the nearest integer and may itself be odd —
  the same rule 0.1.0 used, on every path (profile-only, width-only,
  height-only, and both-bounds). No even-rounding is applied anywhere.
- **Manifest `width` is now range-checked (rejected-input change).** FR-026
  requires both dimension bounds to be integers in the closed range 2–8192, and
  that check applies to the manifest `width` field, which existed in 0.1.0. A
  manifest carrying `width: 1` or `width: 10000` was accepted by 0.2.0 and is now
  rejected with `INVALID_DIMENSIONS` and exit 6. No previously *accepted output*
  changes — a value in 2–8192 behaves exactly as before — but a manifest relying
  on an out-of-range width will need that value corrected. Whitespace-padded
  values such as `" 480"` are unaffected and still accepted: a CSV cell is
  trimmed before validation in every column, and a JSON manifest `width` string
  is trimmed exactly as 0.2.0 trimmed it. Fields introduced in 0.3.0 (`height`,
  `crop`, `speed`, `dither`, `bayerScale`) have no such legacy, so in JSON they
  take the strict grammar with no padding allowed.
- **Dithering is now a public option.** The 0.1.0 allowance to change dithering
  internally is superseded: an explicitly requested mode and `bayerScale` are
  honored exactly and will not change across patch releases, while a *profile's*
  default may change only in a minor release with a changelog entry (spec §15.5).

### Security

- **SEC-018 — Transformation parameter validation.** Transformation values become
  arguments inside an FFmpeg filter graph and are treated as an injection
  surface. Only integers, bounded decimals, and members of the fixed
  enumerations are accepted; free text, filter strings, filter-graph fragments,
  filter scripts, FFmpeg expressions, and option key-value pairs are rejected
  from the command line, manifests, and configuration alike. Every parameter is
  parsed and range-checked **before any filter graph is constructed**, and the
  graph is built exclusively from values the engine re-serializes from its own
  validated numeric and enum types — user-supplied text is never concatenated
  into a filter graph. Any value containing a character outside its grammar is
  rejected — this covers at least inner whitespace, inner newlines, and the
  characters ``, ; ' " \ [ ] = % ( ) $ ` *``. The colon is permitted only as the
  field separator inside `--crop`, which must contain exactly three colons and
  four unsigned integer fields.
  Two deliberate exceptions concern *surrounding* whitespace only, and neither
  reaches the filter graph. `--dither` is compared after its surrounding
  whitespace is trimmed (FR-028), so `"sierra2_4a\n"` is accepted — what the
  engine then emits is the matched enum *member*, never the supplied text, so a
  padded value and a clean one produce byte-identical arguments. A CSV cell is
  likewise trimmed before its grammar runs, consistently across every column and
  as 0.2.0 did for `width`; the strict grammar still runs on the trimmed text,
  so inner whitespace, embedded newlines, and every metacharacter above remain
  rejected, and padding a hostile value does not launder it. The
  identical validated filter chain is applied to the palette-generation pass and
  the encoding pass, so palette generation cannot be driven by a different or
  unvalidated parameter set. No user-supplied filter script file or inline filter
  definition is accepted through any flag, field, or key.
- **Numeric bounds are part of the security contract**, not only usability: an
  unbounded dimension, crop offset, or speed value is a resource-exhaustion
  vector under SEC-011. Crop components are capped at 65535, dimensions at 8192,
  and speed at 4.0.
- **SEC-010 unchanged for the new paths.** The local-only protocol whitelist is
  enforced on every FFmpeg and ffprobe invocation including preview extraction,
  so no filter may reference a remote resource. SEC-001 remains in force:
  subprocess arguments are always passed as arrays and `shell=True` is never
  used.

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

[Unreleased]: https://github.com/Krishna2709/giffify/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Krishna2709/giffify/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Krishna2709/giffify/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Krishna2709/giffify/releases/tag/v0.1.0
