---
name: video-to-gif
description: Convert explicit timestamp ranges from local video files into one or more optimized animated GIFs, optionally cropped, resized, speed-adjusted, or dithered. Use when a user asks to create a GIF from a video, extract timestamped clips as GIFs, batch-generate GIFs from CSV or JSON timestamp manifests, crop or resize or speed up a clip, preview a still frame before making a GIF, or convert a remote video URL when remote sources are enabled.
license: LICENSE
compatibility: Requires Python 3.10+, ffmpeg, and ffprobe. Supports macOS, Windows, and Linux. Version 0.3.0 processes local video files by default, can optionally acquire remote HTTP or HTTPS source URLs when remote sources are explicitly enabled, and supports cropping, explicit resizing, playback-speed adjustment, dithering control, and PNG preview frames. Captions and subtitle burn-in are not supported.
metadata:
  product-version: "0.3.0"
  specification: "VTG-TS-001"
---

# Video to GIF

Convert explicitly selected portions of a video into optimized animated GIFs. You (the agent) handle the conversation, collect only the information that is required, obtain approvals, and invoke the deterministic Python engine. The engine never prompts — it takes complete arguments and returns a structured JSON result that you interpret.

This release is timestamp-based and **local by default**. There is no highlight detection and no transcript selection. Version 0.2.0 added **opt-in** remote source acquisition: an `http`/`https` URL is a valid source only when remote sources are explicitly enabled and the user approves (see step 4). Under the default configuration a URL is rejected with `REMOTE_DISABLED` (exit 8) and no network access occurs.

Version 0.3.0 adds **transformations** — crop, explicit resize, playback speed, dithering — and a `preview` command that extracts a single PNG still (step 7). Every transformation parameter is numeric or a fixed enum; there is no way to pass an FFmpeg filter string. Captions, text overlays, and subtitle burn-in are **not** supported.

## Your responsibilities

- Interpret the request; identify what is missing.
- Ask only the questions required to proceed. Never re-ask information already supplied.
- Obtain approval before installing dependencies, overwriting files, or writing outside the project.
- Obtain approval for network access to a remote source, and a rights confirmation for that source, before any fetch (step 4).
- Invoke the engine non-interactively with a single complete command, always with `--json`.
- Interpret the structured result and return one concise summary line.

The engine handles all parsing, validation, media inspection, remote acquisition, output planning, collision detection, FFmpeg execution, and cleanup. Do not reimplement any of that.

## Workflow

### 1. Resolve project configuration

Look for `.video-to-gif.json` at the project root (fall back to the current working directory). If present, it supplies defaults: `defaultProfile`, `outputDirectory`, `loop`, `collisionPolicy`, `continueOnError`, `remoteSources`, `keepRemoteSource`, `transformations`, and `limits`. Precedence, highest first:

1. Command-line argument (what you pass to the engine).
2. Request-specific user instruction.
3. Project configuration.
4. Built-in default.

For **transformation** parameters only, a clip-level manifest field ranks above the command-line argument (step 7). A request-specific override must NOT modify the saved configuration unless the user asks to save it. See `references/configuration.md`.

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

Note: `pip install ffmpeg` does NOT install FFmpeg. Detect the real `ffmpeg`/`ffprobe` executables. `doctor` also reports whether the optional `yt-dlp` adapter is available and its version; its absence is informational, not a failure (it is only needed for video-page URLs — see step 4).

### 3. Identify the source (FR-001)

The user must explicitly identify a source: a local file path, a local directory, or a filename resolvable inside the project.

- A directory with exactly one probable video file: you may select it.
- A directory with multiple probable video files: ask which one.
- A directory with no probable video file: report that no source was found.
- Never search directories outside the project unless the user explicitly names or authorizes them.

A source may also be an `http`/`https` URL, but only when remote sources are enabled — see step 4. Under the default configuration a URL is rejected with `REMOTE_DISABLED` (exit 8) and nothing is fetched.

### 4. Remote sources (opt-in, disabled by default)

Remote acquisition is **disabled by default**. The `remoteSources` configuration field takes exactly one of three values (FR-018):

- `disabled` (default) — remote URLs are rejected. The engine returns error code `REMOTE_DISABLED` (status `remote_disabled`, exit 8) and performs **no** network access.
- `ask` — you MUST obtain explicit user approval before each remote acquisition.
- `enabled` — remote acquisition is permitted without a per-request approval prompt.

When the user supplies a URL, branch on the effective `remoteSources` value:

1. **`disabled`** — Explain that remote sources are off by default. If the user wants to fetch it anyway, you may enable it for this single invocation with `--allow-remote`, but only after they approve. Do not pass `--allow-remote` silently. If the user declines, run without it and the engine returns `REMOTE_DISABLED` (no fetch).
2. **`ask`** — Obtain explicit approval for network access, then supply `--allow-remote` for that invocation.
3. **`enabled`** — Proceed without a per-request network-access prompt.

**Rights confirmation (§19.6) — required once per source, before any fetch.** Regardless of the `remoteSources` value, before acquiring a remote source you MUST confirm the user owns the video, has permission to use it, or otherwise has a lawful basis to make a GIF from it. Ask this **once per source**, not per clip. Do NOT record, store, or transmit the confirmation. Never request or accept instructions to bypass DRM, authentication, or access controls — the engine rejects DRM-protected sources with `DRM_PROTECTED` (exit 5) and does not attempt circumvention.

**Acquisition model.** The engine downloads the source into a secure temporary directory, converts it with the same local, network-isolated pipeline, then **deletes the download** when the job finishes (success or failure). It is retained only when the user asks to keep it (`--keep-remote-source`), in which case the retained path is reported in the result. `https` is preferred; `http` works only with an explicit unencrypted-transfer warning; `file` and every other scheme are rejected (`UNSUPPORTED_URL_SCHEME`, exit 5). Full detail — supported/rejected sources, limits, redaction — is in `references/remote-sources.md`.

Direct URL, remote enabled for this invocation (approval + rights confirmation obtained first):

```
python scripts/video_to_gif.py create \
  --input "https://cdn.example.com/media/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --profile balanced \
  --allow-remote \
  --json
```

Keep the downloaded source (only when the user asks to retain it):

```
python scripts/video_to_gif.py create \
  --input "https://cdn.example.com/media/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --allow-remote \
  --keep-remote-source \
  --json
```

**Video-page URLs via the optional yt-dlp adapter.** Video-platform watch pages are supported only through the optional `yt-dlp` adapter, selected with `--remote-adapter ytdlp`. yt-dlp is **never bundled** and is a separate, optional dependency detected independently of FFmpeg. Treat a missing adapter exactly like a missing FFmpeg dependency (§6.4): if the adapter is requested but unavailable, the engine returns `YTDLP_MISSING` (status `dependency_missing`, exit 3) — state that yt-dlp is missing, explain why it is needed, show the proposed install command (`references/installation.md`), ask for approval, and re-check with `doctor --json` before retrying. The adapter requires the same remote enablement and the same rights confirmation as any other remote source, and rejects DRM-protected sources without circumvention.

```
python scripts/video_to_gif.py create \
  --input "https://videos.example.com/watch/abc123" \
  --start "00:01:00" \
  --end "00:01:05" \
  --allow-remote \
  --remote-adapter ytdlp \
  --json
```

`inspect` on a URL also acquires the source first (the download is deleted afterward like any other job). Never echo a signed or tokenized URL: the engine strips query strings and credentials from every URL it reports (SEC-015), and you should too.

### 5. Collect only the required, missing information

Before conversion you must have (spec section 19.1): a resolved source, at least one valid clip definition, a quality profile, an output directory, an explicit collision policy when collisions exist, and — when the source is remote — remote-access approval plus a rights confirmation (step 4).

Ask only for what is genuinely missing. Do NOT ask for anything already supplied by the current request, a manifest, project configuration, or an earlier answer in this conversation. Ask when ambiguity could change the source, the video stream, timestamp interpretation, the output destination, overwrite behavior, the quality profile, the crop rectangle or output dimensions, or the playback-speed multiplier (§19.5). Make silent, deterministic assumptions for harmless details (default looping = forever, temporary-file cleanup on).

Clip definitions: each clip needs a `start` plus exactly one of `end` or `duration`. Timestamp forms and duration rules are in `references/input-formats.md`.

### 6. First-use profile selection (§19.4)

When `.video-to-gif.json` does not exist and the request does not already specify quality, ask the user to choose one:

1. Balanced (recommended) — 640px / 15fps / 256 colors
2. Small file — 480px / 10fps / 128 colors
3. High quality — 960px / 20fps / 256 colors
4. Custom — user-defined width, fps, colors

Use `./output` unless overridden. Tell the user these preferences can be saved to `.video-to-gif.json`, and save the configuration only after the user agrees or clearly asks to remember it. Profile details are in `references/quality-profiles.md`.

### 7. Transformations and preview frames (FR-024..FR-030)

Apply a transformation when the user asks for one. Do NOT add one they did not request — an unrequested crop or speed change silently alters their content.

| The user says | Use | Notes |
| --- | --- | --- |
| "just the left half", "crop out the sidebar", "square it" | `--crop <x>:<y>:<w>:<h>` | Coordinates are orientation-normalized **source pixels**. Get the frame size from `inspect`. Cropping is the only way to change the output aspect ratio. |
| "make it 800px wide", "fit it in 600x400", "bigger than the profile allows" | `--width <pixels>` and/or `--height <pixels>` | These are **maximum bounds** (2–8192), not exact sizes; aspect ratio is preserved. An explicit bound overrides the profile maximum width. |
| "speed it up 2x", "slow motion", "make it shorter without cutting" | `--speed <multiplier>` | 0.25–4.0. Retimes only — the selected range is unchanged. Output duration becomes `round(durationMs / speed)`. |
| "reduce the banding", "make the file smaller" | `--dither <mode>` (+ `--bayer-scale <n>` for `bayer`) | `none`, `bayer`, `floyd_steinberg`, `sierra2`, `sierra2_4a`. Each profile has a default, so leave it alone unless size or banding is the actual complaint. |

The same flags work on `create`, `batch`, and `preview`. Verify spellings with `create --help` / `preview --help`.

**Ask before guessing.** Ambiguity about the crop rectangle, the output dimensions, or the speed multiplier MUST be resolved with the user (§19.5). Never invent a crop rectangle the user did not specify or confirm.

**Preview first when framing is uncertain.** Before committing to a batch of GIFs — or to any single conversion where the user is unsure about a crop, the framing, or which moment to use — offer to extract a still instead. It is cheap, it is full-colour PNG (not palette-reduced), and it is not a GIF, so nothing has to be regenerated if the framing is wrong:

```
python scripts/video_to_gif.py preview \
  --input "./videos/demo.mp4" \
  --at "00:01:02.500" \
  --crop 320:180:1280:720 \
  --width 640 \
  --json
```

`preview --manifest "./clips.json" --json` produces one still per clip at that clip's **start** timestamp with that clip's effective transformations — the fastest way to check a whole batch's framing before encoding anything. `--at` and `--manifest` are mutually exclusive. A preview appears in the result's `previews` array, never in `created`, and `summary.previews` counts it. `speed`, `fps`, `loop`, `colors`, `dither`, and `bayerScale` are accepted but ignored for a still and produce one `TRANSFORMATION_NOT_APPLICABLE` warning — that warning is expected, not a problem. `--output-name` must end in `.png` (any other extension is `INVALID_USAGE`, exit 2).

Crop plus an explicit width:

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --crop 320:180:1280:720 \
  --width 640 \
  --json
```

Speed:

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:00:04" \
  --duration 4 \
  --speed 2.0 \
  --json
```

**Per-clip manifest values beat your CLI flags.** For transformation parameters only, precedence is: clip-level manifest field > command-line flag > top-level manifest field > project configuration > built-in default. A clip that sets `speed` in the manifest keeps its own speed even when you pass `--speed` to `batch`; the flag still applies to every clip that does not set it. Do not fight this by rewriting the manifest — use the flag for the batch-wide value and let per-clip fields override it. Everything else follows the ordinary precedence of step 1.

**Transformation values are numeric or enum only.** There is no flag, manifest field, or configuration key that accepts an FFmpeg filter string, filter-graph fragment, filter script, or expression, and none will ever be added at this layer (SEC-018). If a user asks for an arbitrary FFmpeg filter, captions, text overlay, or subtitle burn-in, say it is not supported rather than trying to smuggle it through a transformation value — the engine rejects it in preflight. `transformations` in `.video-to-gif.json` may set `width`, `height`, `speed`, `dither`, and `bayerScale`, but **never `crop`** (a rectangle is source-specific; `validate-config` rejects it).

Invalid transformations are caught in preflight before any FFmpeg process starts: `INVALID_CROP`, `INVALID_DIMENSIONS`, `INVALID_SPEED`, `INVALID_DITHER`, all exit 6. Full detail is in `references/transformations.md`.

### 8. Invoke the engine (non-interactively, always `--json`)

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

For a remote URL, add the remote flags from step 4 (`--allow-remote`, and optionally `--keep-remote-source` or `--remote-adapter ytdlp`). For a batch where collisions are plausible, run `--dry-run` first to surface collisions before doing any encoding.

### 9. Interpret the structured result (spec section 13)

With `--json`, the final JSON document is on stdout; progress events (JSON Lines) go to stderr and can be ignored for the summary. When a remote source is acquired, download progress arrives as `stage: "download"` events on stderr. Read the `status` field:

- `success` — all requested clips created.
- `partial_success` — some created, some failed (batch continued past a runtime failure). Report both counts.
- `failed` — the job failed; read `failed[].code` and `failed[].message`.
- `validation_failed` — a clip/timestamp or schema problem was caught in preflight; nothing was encoded.
- `collision` — one or more destination files already exist. See step 10.
- `dependency_missing` — `ffmpeg`/`ffprobe` (go back to step 2) or the `yt-dlp` adapter (`YTDLP_MISSING`, step 4) is missing.
- `remote_disabled` — a URL was supplied but remote sources are disabled and not overridden; no network access occurred. Offer to enable it for one invocation with `--allow-remote` after approval (step 4).
- `cancelled` — the user cancelled; completed GIFs are preserved, partial output (including any partial download) removed. Report how many completed.
- `dry_run` — preflight only; report the plan (planned outputs, detected collisions, estimated work). No GIFs were produced.

Exit codes 0–14 map to these outcomes and are listed in `references/troubleshooting.md`.

### 10. Collision handling (FR-012)

The engine never overwrites by default (`fail`). On `status: "collision"`, ask the user ONCE for a policy that covers the whole batch, then re-run the same command with an explicit `--collision-policy`:

- `overwrite` — replace existing files.
- `unique` — write a new uniquely numbered file alongside the existing one.
- `skip` — leave existing files untouched and skip those clips.
- `fail` — abort (the default).

Do not ask per-file; ask once for the entire set of detected collisions.

### 11. Invalid timestamps (FR-007)

If preflight reports invalid timestamps (e.g., end beyond source duration), nothing is encoded under the default `fail` policy. If the user has not already addressed this, ask whether to `fail`, `skip` the invalid clips, or `clamp` an end timestamp to the source duration — and obtain explicit approval before using `skip` or `clamp`. Then re-run with `--invalid-timestamp-policy {fail,skip,clamp}`, for example:

```
python scripts/video_to_gif.py create --input "./videos/demo.mp4" \
  --start "00:01:00" --end "00:01:05" --invalid-timestamp-policy clamp --json
```

The same flag applies to `batch`. See `references/input-formats.md` for the policy semantics.

### 12. Summarize (FR-017)

Return one concise line, for example:

- `Created 10 GIFs in ./output.`
- `Created 9 GIFs in ./output; 1 clip failed during encoding.`

Offer the detailed result only if the user asks or a failure needs explanation.

## References

- `references/configuration.md` — `.video-to-gif.json` schema, precedence, remote-source settings, transformation defaults, limits, restrictions.
- `references/input-formats.md` — timestamp forms, duration/clip rules, JSON + CSV manifests (including per-clip transformation fields), loop syntax.
- `references/quality-profiles.md` — profile table, max-width semantics, explicit width/height interaction, no-upscaling, fps capping, dither defaults.
- `references/transformations.md` — crop, explicit resize, speed, dithering, preview frames, per-clip manifest transformations, filter-chain order, and result reporting.
- `references/remote-sources.md` — opt-in remote acquisition: supported/rejected sources, the download-then-convert-then-delete model, limits, redaction, and rights confirmation.
- `references/installation.md` — per-platform FFmpeg install guidance (and optional yt-dlp) with the approval-first rule.
- `references/troubleshooting.md` — exit codes 0–14 and error codes with remediation.

## Assets

- `assets/config.schema.json` — JSON Schema for `.video-to-gif.json`.
- `assets/manifest.schema.json` — JSON Schema for JSON manifests.
