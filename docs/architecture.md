# Architecture

This document describes the layered architecture of the `video-to-gif` Agent
Skill and the responsibility boundaries between its layers. It expands on spec §4
(Architectural decisions). The specification
([`versioned_technical_spec.md`](../versioned_technical_spec.md)) is normative;
this document is explanatory.

## Skill versus plugin

The project uses two distinct concepts (spec §4.1):

- **Agent Skill** — the reusable capability: workflow instructions, scripts,
  schemas, and references. This is the **canonical** implementation and lives at
  `src/skill/video-to-gif/`.
- **Plugin** — the distribution package. A Claude Code plugin
  (`.claude-plugin/plugin.json`) and a Codex plugin (`.codex-plugin/plugin.json`)
  each wrap the same skill for their platform's marketplace.

There is **one** shared implementation. Platform packages under `packages/` are
generated from the canonical skill and must not contain independent conversion
logic. The canonical source is the single point of truth; packages are build
artifacts.

## The three layers

```
User request
    │
    ▼
Claude Code / Codex            ← Agent layer (natural language, approvals)
    │
    ▼
video-to-gif Agent Skill
    │
    ▼
Python orchestration engine    ← deterministic, non-interactive
    │
    ├── ffprobe inspection
    ├── timestamp validation
    ├── output planning
    ├── collision detection
    └── FFmpeg conversion       ← FFmpeg/ffprobe layer (decode, palette, encode)
            │
            ▼
        output/*.gif  +  structured JSON result
```

### 1. Agent layer (Claude Code / Codex)

The agent owns the **conversation** and everything that requires judgement or
user consent (spec §4.3, §19). It:

- interprets the natural-language request;
- identifies missing information and asks **only** the questions required to
  proceed (which video, which timestamps, which quality profile on first use);
- obtains approval for dependency installation, for overwriting existing files,
  and for writing to destinations outside the project;
- explains warnings and failures; and
- produces the final one-line summary (e.g. "Created 10 GIFs in ./output.").

The agent must not re-ask for information already supplied by the request, a
manifest, project configuration, or an earlier answer in the same conversation.

### 2. Python engine (`scripts/video_to_gif.py` + `vtg/`)

The engine is **deterministic and non-interactive** — it never prompts. It
receives complete arguments from the agent (or a terminal user) and returns a
structured result. It owns (spec §4.3):

- input parsing and path normalization;
- configuration and manifest validation;
- timestamp conversion (normalized to integer milliseconds);
- media inspection (via ffprobe);
- output planning, name generation, and collision detection;
- FFmpeg subprocess execution, progress extraction, cancellation, and cleanup;
  and
- emitting structured results (final JSON on stdout, progress JSON Lines on
  stderr).

The engine **must not** conduct an interactive conversation. All ambiguity is
surfaced back to the agent as structured results (for example, an ambiguous
video-stream selection), which the agent resolves with the user.

Maintainability boundary (spec §5, NFR-005): core modules separate parsing,
validation, media inspection, conversion, filesystem operations, and progress
handling. The reference module layout is:

```
scripts/
├── video_to_gif.py        # thin entry point
└── vtg/
    ├── cli.py             # argument parsing, command dispatch
    ├── config.py          # project configuration + precedence
    ├── dependencies.py    # doctor / feature detection
    ├── errors.py          # error codes, exit codes, serialization
    ├── ffmpeg.py          # subprocess construction and execution
    ├── inspect.py         # ffprobe inspection and stream selection
    ├── manifests.py       # JSON/CSV manifest parsing
    ├── models.py          # data types (clips, jobs, results)
    ├── naming.py          # filename generation and sanitization
    ├── paths.py           # path resolution, project boundary checks
    ├── progress.py        # progress extraction and JSON Lines emission
    └── timestamps.py      # timestamp parsing/normalization
```

> The engine modules above are implemented by other contributors; this document
> describes the intended boundaries, not a claim that every file exists yet.

### 3. FFmpeg and ffprobe

The external executables do the media work (spec §4.3):

- **ffprobe** inspects source media and returns machine-readable (JSON) metadata:
  container and stream durations, dimensions, average frame rate, codec, stream
  index and disposition, and rotation/orientation where available.
- **FFmpeg** performs decoding, orientation normalization, frame-rate conversion,
  aspect-ratio-preserving scaling, palette generation (`palettegen`), and GIF
  encoding (`paletteuse`).

Both are invoked with an explicit local-only protocol whitelist
(`-protocol_whitelist file,pipe`) so that no local input file can cause a network
fetch (SEC-010). `pip install ffmpeg` is not FFmpeg — the engine detects the real
`ffmpeg`/`ffprobe` executables via feature detection rather than version numbers
(spec §6.3).

## Conversion pipeline (overview)

For every job the engine runs a **preflight** (spec §15.1): resolve project root,
load and validate configuration, resolve and verify the source, run ffprobe,
select the video stream, parse and validate all timestamps, resolve profiles and
the output directory, generate output names, detect collisions, and estimate
work — all before any encoding.

For every valid clip the engine runs the **palette pipeline** (spec §15.2): seek,
decode only the required duration, normalize orientation, convert frame rate,
scale preserving aspect ratio, generate a palette, encode the GIF to a temporary
file, verify it is a non-empty GIF, and atomically move it to its destination.
The destination becomes visible only after successful verification (spec §15.3).

## Data flow and results

The engine returns one final JSON document on stdout (schema version 1, spec
§13.1) with `source`, `created`, `failed`, `warnings`, and `summary` sections,
and a top-level `status` from the fixed set (`success`, `partial_success`,
`failed`, `validation_failed`, `collision`, `dependency_missing`, `cancelled`,
`dry_run`). Progress events stream separately on stderr as JSON Lines so they can
never corrupt the final document. Exit codes 0–13 (spec §14) are part of the
contract and mirror the structured error codes.

## Why this shape

- **Separation of concerns** keeps deterministic media processing testable and
  reproducible while leaving conversation and consent to the agent.
- **One canonical skill, thin platform packages** avoids divergent logic across
  Claude Code and Codex and lets a single test suite cover both.
- **Standard-library-only engine** maximizes portability across macOS, Windows,
  and Linux (spec §6, NFR-001) and keeps the runtime dependency surface limited
  to the FFmpeg executables.
