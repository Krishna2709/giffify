# video-to-gif

[![CI](https://github.com/Krishna2709/giffify/actions/workflows/ci.yml/badge.svg)](https://github.com/Krishna2709/giffify/actions/workflows/ci.yml)
[![Release](https://github.com/Krishna2709/giffify/actions/workflows/release.yml/badge.svg)](https://github.com/Krishna2709/giffify/actions/workflows/release.yml)

A portable **Agent Skill** that converts explicitly selected timestamp ranges from
**local** video files into one or more optimized animated GIFs. It runs inside
**Claude Code** and **OpenAI Codex** from the same shared implementation, driving a
deterministic Python engine on top of `ffmpeg`/`ffprobe`.

The agent handles the conversation (which video, which timestamps, which quality
profile, approvals). The Python engine does the deterministic work: parsing and
validating timestamps, inspecting media, planning outputs, running FFmpeg's
two-pass palette pipeline, and returning structured results. It never prompts.

> Normative specification: [`versioned_technical_spec.md`](versioned_technical_spec.md)
> (VTG-TS-001, v0.1.0-draft.2). When this README and the spec disagree, the spec wins.

## Status: pre-release (0.1.0, in development)

This repository is at release-candidate stage for 0.1.0. Interfaces described
here track the spec but are **not yet stable**. A few items remain open
decisions (exact profile values and others — spec §26). Do not treat 0.1.0 as
production-ready until it is tagged and published.

Version **0.1.0 processes local files only** — it performs no network access.
Remote source acquisition is planned for 0.2.0 (spec §25.2).

## Requirements

- **Python 3.10+** (standard library only — no third-party runtime packages)
- **ffmpeg** and **ffprobe** on `PATH`
  - `pip install ffmpeg` does **not** install the FFmpeg executables. Install the
    real binaries (e.g. `brew install ffmpeg`, `apt-get install ffmpeg`,
    `choco install ffmpeg`).

Run the built-in environment check at any time:

```
python scripts/video_to_gif.py doctor --json
```

## Installation

The canonical skill lives at `src/skill/video-to-gif/`. You can install it
directly as an Agent Skill, or (once published) install the packaged plugin from
a marketplace.

> **Releases:** packaged archives (`video-to-gif-claude-<version>.{tar.gz,zip}`
> and `video-to-gif-codex-<version>.{tar.gz,zip}`) plus a `SHA256SUMS` checksum
> file are attached to each
> [GitHub Release](https://github.com/Krishna2709/giffify/releases).

### Claude Code — direct skill install

Copy `src/skill/video-to-gif/` into one of:

| Scope    | Location                             |
| -------- | ------------------------------------ |
| Project  | `.claude/skills/video-to-gif/`       |
| Personal | `~/.claude/skills/video-to-gif/`     |

### Codex — direct skill install

Copy `src/skill/video-to-gif/` into one of:

| Scope    | Location                             |
| -------- | ------------------------------------ |
| Project  | `.agents/skills/video-to-gif/`       |
| Personal | `~/.agents/skills/video-to-gif/`     |

### Plugin / marketplace install (once published)

For versioned, discoverable distribution the skill is wrapped in thin plugins:

- **Claude Code** — add the marketplace (`marketplaces/claude/.claude-plugin/marketplace.json`),
  then install the `video-to-gif` plugin from it.
- **Codex** — add the marketplace (`marketplaces/codex/.agents/plugins/marketplace.json`),
  then install the `video-to-gif` plugin from it.

Platform packages under `packages/` are **generated** from the canonical source by
`tools/build_packages.py` — never edit them by hand.

## Usage

The command-line contract below matches spec §12 exactly. The examples assume you
are running from inside the skill directory (where `scripts/video_to_gif.py`
lives); from the repository root, prefix the path with
`src/skill/video-to-gif/`.

With `--json`, the final JSON document is written to **stdout** and progress
events stream as JSON Lines on **stderr** (spec §13.3). Exit codes `0`–`13` are
part of the contract (spec §14).

### Check the environment

```
python scripts/video_to_gif.py doctor --json
```

### Inspect a source video

```
python scripts/video_to_gif.py inspect \
  --input "./videos/demo.mp4" \
  --json
```

### Create one GIF (start + end)

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --profile balanced \
  --output-directory "./output" \
  --collision-policy fail \
  --json
```

### Create one GIF (start + duration)

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --duration 5 \
  --profile balanced \
  --json
```

### Create one GIF with an explicit output name

`--output-name` must be a bare filename with no path separators. It is sanitized
(FR-011) and resolved inside the effective output directory.

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --output-name "opening.gif" \
  --json
```

### Batch from a manifest (JSON or CSV)

```
python scripts/video_to_gif.py batch \
  --manifest "./clips.json" \
  --collision-policy fail \
  --json
```

### Preflight (dry run — validates and plans, generates nothing)

```
python scripts/video_to_gif.py batch \
  --manifest "./clips.json" \
  --dry-run \
  --json
```

### Validate configuration or a manifest

```
python scripts/video_to_gif.py validate-config \
  --config "./.video-to-gif.json" \
  --json

python scripts/video_to_gif.py validate-manifest \
  --manifest "./clips.json" \
  --json
```

Timestamps accept `75`, `75.5`, `MM:SS`, `HH:MM:SS`, each with optional `.mmm`
fractional seconds. A clip is defined by `start` plus exactly one of `end` or
`duration`.

## Quality profiles

Widths are **maximums**, not forced widths. The engine preserves source aspect
ratio and does not upscale by default. When the source frame rate is below the
target, the effective frame rate does not exceed the source (spec §8, FR-014).

| Profile  | Max width | Target FPS | Max colors | Intended use                     |
| -------- | --------- | ---------- | ---------- | -------------------------------- |
| small    | 480 px    | 10         | 128        | Documentation and messaging      |
| balanced | 640 px    | 15         | 256        | General default                  |
| high     | 960 px    | 20         | 256        | Detailed product or UI motion    |
| custom   | user-set  | user-set   | user-set   | Advanced control                 |

> Exact profile values are provisional pending benchmark testing (spec §26 open
> decision 5).

## Security model (summary)

Version 0.1.0 is designed to be safe on untrusted media and hostile manifests.
Full detail: [`docs/security.md`](docs/security.md) and [`SECURITY.md`](SECURITY.md).

- **Local-only processing.** No source video, GIF, frame, metadata, or filename
  is uploaded anywhere. Telemetry is disabled (spec §18).
- **No network access in 0.1.0.** A URL source returns `UNSUPPORTED_REMOTE_SOURCE`
  rather than being fetched (SEC-005).
- **Network isolation enforced at the FFmpeg layer.** `ffmpeg`/`ffprobe` are
  invoked with `-protocol_whitelist file,pipe`, and reference-following
  containers (HLS, DASH, concat scripts) are rejected as
  `UNSUPPORTED_MEDIA_CONTAINER` (exit 5) so a hostile local playlist cannot reach
  the network (SEC-010).
- **No shell.** Subprocesses are invoked with argument arrays, never
  `shell=True`; manifest and config values are treated purely as data (SEC-001,
  SEC-009).
- **Resource limits.** A per-clip wall-clock timeout (default 600 s) and a
  temporary-disk ceiling (default 2 GiB) are enforced; exceeding either yields
  `RESOURCE_LIMIT_EXCEEDED` (exit 13) and cleans up (SEC-011).
- **Overwrite protection.** The engine never overwrites an existing file by
  default (collision policy `fail`); writing outside the project root requires
  explicit authorization (SEC-002, SEC-003, SEC-004).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — layers and responsibility boundaries
- [`docs/security.md`](docs/security.md) — SEC-001..SEC-011 and the threat model
- [`docs/release-process.md`](docs/release-process.md) — build integrity, publishing, and versioning
- [`versioned_technical_spec.md`](versioned_technical_spec.md) — the normative specification
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development setup and workflow
- [`SECURITY.md`](SECURITY.md) — supported versions and vulnerability reporting
- [`CHANGELOG.md`](CHANGELOG.md) — release notes

## License

MIT — see [`LICENSE`](LICENSE).
