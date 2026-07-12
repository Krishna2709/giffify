---
name: video-to-gif
description: Convert explicit timestamp ranges from local video files into one or more optimized animated GIFs. Use when a user asks to create a GIF from a video, extract timestamped clips as GIFs, or batch-generate GIFs from CSV or JSON timestamp manifests.
license: LICENSE
compatibility: Requires Python 3.10+, ffmpeg, and ffprobe. Supports macOS, Windows, and Linux. Version 0.1 processes local video files only.
metadata:
  product-version: "0.1.0"
  specification: "VTG-TS-001"
---

# Video to GIF

Convert explicitly selected portions of a local video into optimized animated GIFs. You (the agent) handle the conversation, collect only the information that is required, obtain approvals, and invoke the deterministic Python engine. The engine never prompts — it takes complete arguments and returns a structured JSON result that you interpret.

This release is timestamp-based and local-only. There is no highlight detection, no transcript selection, and no network access. A URL is not a valid source (it returns `UNSUPPORTED_REMOTE_SOURCE`).

## Your responsibilities

- Interpret the request; identify what is missing.
- Ask only the questions required to proceed. Never re-ask information already supplied.
- Obtain approval before installing dependencies, overwriting files, or writing outside the project.
- Invoke the engine non-interactively with a single complete command, always with `--json`.
- Interpret the structured result and return one concise summary line.

The engine handles all parsing, validation, media inspection, output planning, collision detection, FFmpeg execution, and cleanup. Do not reimplement any of that.

## Workflow

### 1. Resolve project configuration

Look for `.video-to-gif.json` at the project root (fall back to the current working directory). If present, it supplies defaults: `defaultProfile`, `outputDirectory`, `loop`, `collisionPolicy`, `continueOnError`, and `limits`. Precedence, highest first:

1. Command-line argument (what you pass to the engine).
2. Request-specific user instruction.
3. Project configuration.
4. Built-in default.

A request-specific override must NOT modify the saved configuration unless the user asks to save it. See `references/configuration.md`.

### 2. Confirm dependencies (when their state is unknown)

If you have not already confirmed FFmpeg availability this session, run:

```
python scripts/video_to_gif.py doctor --json
```

If the result status is `dependency_missing` (or `doctor` reports a missing executable/filter), follow the approval-first flow from spec section 6.4:

1. State which executable is missing (`ffmpeg` and/or `ffprobe`).
2. Explain why it is required.
3. Show the proposed install command for the user's platform (see `references/installation.md`).
4. Ask whether to run it. Do not install without explicit approval, and never run an install with elevated privileges unless the user authorizes that exact command.
5. After approval and installation, re-run `doctor --json` to verify before continuing.

Note: `pip install ffmpeg` does NOT install FFmpeg. Detect the real `ffmpeg`/`ffprobe` executables.

### 3. Identify the source (FR-001)

The user must explicitly identify a source: a local file path, a local directory, or a filename resolvable inside the project.

- A directory with exactly one probable video file: you may select it.
- A directory with multiple probable video files: ask which one.
- A directory with no probable video file: report that no source was found.
- Never search directories outside the project unless the user explicitly names or authorizes them.

A URL is not a valid v0.1 source — the engine returns `UNSUPPORTED_REMOTE_SOURCE`.

### 4. Collect only the required, missing information

Before conversion you must have (spec section 19.1): a resolved source, at least one valid clip definition, a quality profile, an output directory, and — only when collisions exist — an explicit collision policy.

Ask only for what is genuinely missing. Do NOT ask for anything already supplied by the current request, a manifest, project configuration, or an earlier answer in this conversation. Ask when ambiguity could change the source, the video stream, timestamp interpretation, the output destination, overwrite behavior, or the quality profile. Make silent, deterministic assumptions for harmless details (default looping = forever, temporary-file cleanup on).

Clip definitions: each clip needs a `start` plus exactly one of `end` or `duration`. Timestamp forms and duration rules are in `references/input-formats.md`.

### 5. First-use profile selection (§19.4)

When `.video-to-gif.json` does not exist and the request does not already specify quality, ask the user to choose one:

1. Balanced (recommended) — 640px / 15fps / 256 colors
2. Small file — 480px / 10fps / 128 colors
3. High quality — 960px / 20fps / 256 colors
4. Custom — user-defined width, fps, colors

Use `./output` unless overridden. Tell the user these preferences can be saved to `.video-to-gif.json`, and save the configuration only after the user agrees or clearly asks to remember it. Profile details are in `references/quality-profiles.md`.

### 6. Invoke the engine (non-interactively, always `--json`)

Pass complete arguments in a single command. Examples (see spec section 12 for the full contract):

Single clip, start + end:

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

Single clip, start + duration:

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --duration 5 \
  --profile balanced \
  --json
```

Explicit output name (bare filename, no path separators; sanitized and placed inside the output directory):

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --output-name "opening.gif" \
  --json
```

Batch from a manifest (JSON or CSV):

```
python scripts/video_to_gif.py batch \
  --manifest "./clips.json" \
  --collision-policy fail \
  --json
```

Preflight without producing GIFs (inspects source, validates clips, resolves names, detects collisions, estimates work):

```
python scripts/video_to_gif.py batch \
  --manifest "./clips.json" \
  --dry-run \
  --json
```

Inspection and validation helpers:

```
python scripts/video_to_gif.py inspect --input "./videos/demo.mp4" --json
python scripts/video_to_gif.py validate-config --config "./.video-to-gif.json" --json
python scripts/video_to_gif.py validate-manifest --manifest "./clips.json" --json
```

For a batch where collisions are plausible, run `--dry-run` first to surface collisions before doing any encoding.

### 7. Interpret the structured result (spec section 13)

With `--json`, the final JSON document is on stdout; progress events (JSON Lines) go to stderr and can be ignored for the summary. Read the `status` field:

- `success` — all requested clips created.
- `partial_success` — some created, some failed (batch continued past a runtime failure). Report both counts.
- `failed` — the job failed; read `failed[].code` and `failed[].message`.
- `validation_failed` — a clip/timestamp or schema problem was caught in preflight; nothing was encoded.
- `collision` — one or more destination files already exist. See step 8.
- `dependency_missing` — go back to step 2.
- `cancelled` — the user cancelled; completed GIFs are preserved, partial output removed. Report how many completed.
- `dry_run` — preflight only; report the plan (planned outputs, detected collisions, estimated work). No GIFs were produced.

Exit codes 0–13 map to these outcomes and are listed in `references/troubleshooting.md`.

### 8. Collision handling (FR-012)

The engine never overwrites by default (`fail`). On `status: "collision"`, ask the user ONCE for a policy that covers the whole batch, then re-run the same command with an explicit `--collision-policy`:

- `overwrite` — replace existing files.
- `unique` — write a new uniquely numbered file alongside the existing one.
- `skip` — leave existing files untouched and skip those clips.
- `fail` — abort (the default).

Do not ask per-file; ask once for the entire set of detected collisions.

### 9. Invalid timestamps (FR-007)

If preflight reports invalid timestamps (e.g., end beyond source duration), nothing is encoded under the default `fail` policy. If the user has not already addressed this, ask whether to `fail`, `skip` the invalid clips, or `clamp` an end timestamp to the source duration — and obtain explicit approval before using `skip` or `clamp`. Then re-run with `--invalid-timestamp-policy {fail,skip,clamp}`, for example:

```
python scripts/video_to_gif.py create --input "./videos/demo.mp4" \
  --start "00:01:00" --end "00:01:05" --invalid-timestamp-policy clamp --json
```

The same flag applies to `batch`. See `references/input-formats.md` for the policy semantics.

### 10. Summarize (FR-017)

Return one concise line, for example:

- `Created 10 GIFs in ./output.`
- `Created 9 GIFs in ./output; 1 clip failed during encoding.`

Offer the detailed result only if the user asks or a failure needs explanation.

## References

- `references/configuration.md` — `.video-to-gif.json` schema, precedence, limits, restrictions.
- `references/input-formats.md` — timestamp forms, duration/clip rules, JSON + CSV manifests, loop syntax.
- `references/quality-profiles.md` — profile table, max-width semantics, no-upscaling, fps capping.
- `references/installation.md` — per-platform FFmpeg install guidance and the approval-first rule.
- `references/troubleshooting.md` — exit codes 0–13 and error codes with remediation.

## Assets

- `assets/config.schema.json` — JSON Schema for `.video-to-gif.json`.
- `assets/manifest.schema.json` — JSON Schema for JSON manifests.
