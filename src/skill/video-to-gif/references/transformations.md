# Transformations

Version 0.3.0 adds four clip transformations — **crop**, **explicit resize**, **playback speed**, and **dithering** — plus one extra output mode, **preview frames** (spec FR-024..FR-030).

Every transformation parameter is an **integer, a bounded decimal, or a member of a fixed enumeration**. There is no way to pass a filter string, a filter-graph fragment, a filter script, an FFmpeg expression, or a key-value option through any flag, manifest field, or configuration key. Values are parsed and range-checked in preflight — before any FFmpeg process starts — and the filter graph is rebuilt by the engine from its own validated numbers and enum members, so user text never reaches FFmpeg (SEC-018).

## Flags (spec section 12.10)

Accepted by `create`, `batch`, and `preview`:

| Flag | Value format | Requirement |
| --- | --- | --- |
| `--crop <x>:<y>:<w>:<h>` | Four unsigned decimal integers separated by colons | FR-025 |
| `--width <pixels>` | Integer, 2 to 8192 | FR-026 |
| `--height <pixels>` | Integer, 2 to 8192 | FR-026 |
| `--speed <multiplier>` | Decimal, 0.25 to 4.0, at most three fractional digits | FR-027 |
| `--dither <mode>` | One of `none`, `bayer`, `floyd_steinberg`, `sierra2`, `sierra2_4a` | FR-028 |
| `--bayer-scale <n>` | Integer, 0 to 5 | FR-028 |

An invalid value is rejected during preflight with a specific error code and **exit 6** (`INVALID_CROP`, `INVALID_DIMENSIONS`, `INVALID_SPEED`, `INVALID_DITHER`). Nothing is encoded. The `--invalid-timestamp-policy` values `skip` and `clamp` apply to **timestamps only** — the engine never clamps, rounds, re-centers, or silently corrects a transformation value.

When no transformation is specified, output is functionally equivalent to version 0.2.0 for the same source, range, profile, and configuration.

## Precedence (FR-024)

For transformation parameters only, the effective value is resolved highest priority first:

1. **Clip-level manifest field**
2. Command-line flag
3. Top-level manifest field
4. Project configuration (`transformations` object)
5. Built-in default

This refines the general precedence of spec section 9.3: **a clip-level manifest field beats a batch-wide command-line flag**, because a per-clip value is more specific than a flag that applies to the whole run. Every other setting follows section 9.3 unchanged. Each parameter resolves independently — a clip that sets only `speed` still inherits `dither` from the flag, the top level, or the configuration.

## Crop (FR-025)

A crop rectangle selects a sub-rectangle of the source frame.

- The four integers are `x:y:width:height`, where `x` and `y` are the offsets of the rectangle's **top-left corner** from the top-left corner of the frame.
- Coordinates are **source pixels**, in the **orientation-normalized** geometry — the frame as the user sees it after rotation metadata is applied, not the raw coded frame. Use the `width`/`height` reported by `inspect` to plan a rectangle.
- Cropping is the **only** supported way to change the output aspect ratio. Non-uniform scaling, rotation, and flipping are non-goals (spec section 3.3).

Accepted forms:

| Where | Form |
| --- | --- |
| Command line | `--crop 320:180:1280:720` |
| JSON manifest | `{ "x": 320, "y": 180, "width": 1280, "height": 720 }` or `"320:180:1280:720"` |
| CSV manifest | `320:180:1280:720` in a `crop` column |
| Project configuration | **Not permitted** — see below |

Rejected with `INVALID_CROP` (exit 6):

- A value that is not an unsigned decimal integer, or a string that does not have exactly four colon-separated fields.
- An object form missing any of `x`, `y`, `width`, `height`, or carrying any other key.
- Any component outside `0`–`65535`.
- `width < 2` or `height < 2`.
- `x + width` beyond the effective source width, or `y + height` beyond the effective source height.

A crop rectangle in `.video-to-gif.json` is rejected separately, as a **configuration** validation error: `INVALID_CONFIG` (exit 2) with the field path `transformations.crop`. A rectangle is only meaningful against one specific source's dimensions, so it is supplied per request or per clip.

The requested rectangle is applied **exactly** — never rounded, clamped, re-centered, or expanded. When the decoded pixel format cannot express the requested offsets (an odd `x`, `y`, `width`, or `height` on a chroma-subsampled source), the engine converts the frame to a non-subsampled format before cropping rather than adjusting the rectangle.

### Why crop precedes scale

Cropping happens **before** scaling, so after the crop the cropped rectangle *is* the source geometry for everything that follows:

- Aspect-ratio preservation applies to the cropped rectangle, not the original frame.
- The quality profile's maximum width applies to the cropped width. If the cropped width is already at or below that maximum, it is kept and nothing is upscaled.
- The no-upscale rule is evaluated against the cropped dimensions.

That is what makes output dimensions predictable: you crop to choose the region and aspect ratio, then bound the result with a profile maximum or with explicit `width`/`height`.

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --crop 320:180:1280:720 \
  --width 640 \
  --json
```

On a 1920x1080 source this keeps a 1280x720 region and produces a 640x360 GIF. The result reports `effectiveSourceWidth`/`effectiveSourceHeight` as `1280`/`720`.

## Explicit resize (FR-026)

`width` and `height` are **bounds**, not exact output sizes. Aspect ratio is always preserved.

- `width` — the maximum output width. This flag and field existed in 0.1.0 and its meaning is unchanged.
- `height` — the maximum output height. New in 0.3.0.
- Both are integers in the closed range **2 to 8192**. Anything else is `INVALID_DIMENSIONS` (exit 6).

Resolution rules:

| Supplied | Result |
| --- | --- |
| `width` only | Height is derived from the effective source aspect ratio. |
| `height` only | Width is derived from the effective source aspect ratio. |
| Both | The frame is scaled to the largest size that fits **inside the box**: output width ≤ `width` **and** output height ≤ `height`, aspect ratio preserved. The frame is never stretched to fill the box. |
| Neither | The effective quality profile's maximum width applies, exactly as in 0.1.0/0.2.0. |

"Effective source aspect ratio" means the cropped rectangle's aspect ratio when a crop is applied, otherwise the orientation-normalized frame's.

### Explicit bounds override the profile maximum

A profile maximum width is a *default* bound, not a ceiling on explicit requests. `--profile small --width 900` produces a 900px-wide GIF (source permitting) at the `small` profile's frame rate and colour count. Profile detail is in `references/quality-profiles.md`.

### Odd values are honored exactly

GIF is a palette-based format with no chroma subsampling, so it imposes **no even-dimension constraint**. Nothing here is ever rounded to an even value.

- An explicitly supplied `width` or `height` is honored **exactly, including odd values**. Rounding an explicit bound would silently contradict the request. `--width 641` yields a 641px-wide GIF.
- A **derived** dimension is rounded to the **nearest integer** and may itself be odd. `--width 641` on a 1920x1080 source gives `641x338` (1080 × 641 / 1920 = 338.06); `--height 301` on the same source gives `535x301` (1920 × 301 / 1080 = 535.19).
- This is one rule on **every** path — `width` only, `height` only, both, and neither — and it is the rule 0.1.0 used. Derived dimensions are therefore unchanged from 0.1.0/0.2.0, and every earlier invocation stays byte-comparable. A 1000x502 source at `--width 320` gives `320x161` in 0.1.0, 0.2.0, and 0.3.0 alike.
- No output dimension is ever smaller than 2.

### Upscaling

- If the resolved dimensions would exceed the effective source dimensions and `allowUpscale` is not set, the output is **clamped to the effective source size** — the 0.1.0 behavior of `--width`.
- A clamp emits the `UPSCALE_NOT_ALLOWED` warning **only when the clamped bound was explicitly supplied** as `width` or `height`. A profile maximum that simply exceeds a small source never warns, so profile-only jobs emit exactly the warnings they emitted in 0.2.0.
- `--allow-upscale` (or the manifest `allowUpscale` field) honors the resolved dimensions up to the 8192 bound. Upscaling is never inferred from the presence of `--width` or `--height`.

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --duration 4 \
  --width 800 \
  --height 400 \
  --json
```

On a 1920x1080 source this produces `712x400` — the largest 16:9 frame that fits inside 800x400.

## Speed (FR-027)

`speed` is a decimal multiplier on the playback rate of the selected range: `1.0` leaves timing unchanged, above `1.0` is faster, below `1.0` is slower.

- Range **0.25 to 4.0**, at most three fractional digits. Values outside the range, zero, negatives, non-numeric text, and exponent notation are rejected with `INVALID_SPEED` (exit 6).
- Speed is implemented by **retiming presentation timestamps**. It does **not** change the selected source range — `start`, `end`, and `duration` mean exactly what they meant before.

### Duration math

```
outputDurationMs = round(clipDurationMs / speed)
```

A 4000 ms range at `--speed 2.0` produces an approximately 2000 ms GIF; the same range at `--speed 0.5` produces an approximately 8000 ms GIF. The result reports both: `durationMs` is the selected **source** range, `outputDurationMs` is the duration of the generated GIF.

### Frames

- Retiming is applied **before** frame-rate conversion, so the requested output frame rate describes the finished GIF.
- **No frames are interpolated.** Speeding up drops frames; slowing down duplicates them.
- Below `1.0`, the retimed stream's intrinsic frame rate is the source frame rate times the speed, and the effective output frame rate does not exceed that — the same rule that caps output fps at the source fps.
- GIF has no audio, so nothing is retimed on the audio side; audio stays disabled in every FFmpeg invocation.

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:00:04" \
  --duration 4 \
  --speed 2.0 \
  --json
```

## Dithering (FR-028, spec section 15.5)

Dithering trades file size against banding when the frame is reduced to a GIF palette. It is a public option as of 0.3.0.

| Value | Behavior | Size and quality guidance |
| --- | --- | --- |
| `none` | No dithering | Smallest files; visible banding on gradients |
| `bayer` | Ordered dithering with a Bayer matrix | Small files, deterministic pattern; a higher `bayerScale` gives a coarser pattern, better compression, and more banding |
| `floyd_steinberg` | Floyd-Steinberg error diffusion | Good gradients; larger files and more inter-frame noise |
| `sierra2` | Sierra-2 error diffusion | Similar to `floyd_steinberg` with slightly softer noise |
| `sierra2_4a` | Sierra-2-4A error diffusion | FFmpeg's default; the general-purpose quality and size balance |

Comparison is **case-sensitive** against the lowercase names above (surrounding whitespace is trimmed first).

`bayerScale` is an integer **0 to 5** and is meaningful only when the effective dither mode is `bayer`.

Rejected with `INVALID_DITHER` (exit 6):

- A `dither` value outside the enumeration. The error message lists the permitted values.
- A `bayerScale` that is not an integer in 0–5.
- An explicitly supplied `bayerScale` when the effective dither mode is not `bayer`.

### Defaults

When `dither` is not supplied at any precedence level, the effective quality profile's default applies (spec section 15.5):

| Profile | Default dither | Default `bayerScale` |
| --- | --- | --- |
| `small` | `bayer` | `5` |
| `balanced` | `sierra2_4a` | not applicable |
| `high` | `sierra2_4a` | not applicable |
| `custom` | `sierra2_4a` | not applicable |

These reproduce 0.1.0/0.2.0 behavior, so a job that specifies no dither is functionally equivalent to earlier releases. `bayerScale` resolves through the same precedence chain: when the effective mode is `bayer` and no scale is supplied, the profile's default scale applies; when the profile default mode is not `bayer`, the scale defaults to `2`.

An explicitly requested mode and scale are honored exactly and will not change across patch releases. A *profile's default* mode or scale may change in a minor release with a changelog entry, never in a patch release.

```
python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --duration 3 \
  --profile small \
  --dither bayer \
  --bayer-scale 3 \
  --json
```

## Preview frames (FR-029, spec section 12.9)

`preview` extracts a **single full-colour PNG still** instead of producing a GIF, so framing can be confirmed before committing to a conversion. Preview output is never palette-quantized, so its fidelity is not limited by GIF colour reduction.

Two forms:

```
python scripts/video_to_gif.py preview \
  --input "./videos/demo.mp4" \
  --at "00:01:02.500" \
  --crop 320:180:1280:720 \
  --width 640 \
  --json
```

```
python scripts/video_to_gif.py preview \
  --manifest "./clips.json" \
  --json
```

The manifest form produces one still per clip, at that clip's **start** timestamp, using that clip's effective transformations. `--at` and `--manifest` are mutually exclusive.

Behavior:

- `--at` accepts any timestamp form from `references/input-formats.md` and must satisfy `0 <= at < source duration`. Outside that range is `INVALID_TIMESTAMP` (exit 6).
- Orientation normalization, crop, and resize apply **exactly** as they would for a GIF of the same clip with the same settings, including the profile maximum width and the upscale rules.
- `speed`, `fps`, `loop`, `colors`, `dither`, and `bayerScale` do not apply to a still. They are **accepted** (so a preview can be requested with the same settings as the GIF it previews), change nothing, and produce **one** warning per invocation beginning with `TRANSFORMATION_NOT_APPLICABLE` that names the ignored settings.
- Naming: a generated name is `<video-stem>_<at>.png` (for example `demo_00-00-02.500.png`); in the manifest form a named clip yields `<clip-name>_<start>.png`. An explicit `--output-name` must be a bare filename with no path separators and is sanitized under the same FR-011 rules as a GIF name with `.png` substituted for `.gif`. A name with no extension gains `.png`; a name with **any other extension fails with `INVALID_USAGE` (exit 2)**.
- The output directory, project-boundary rules, and collision policies apply unchanged, including the default policy `fail` — a preview never overwrites an existing file by default.
- `--dry-run` is supported with the usual preflight semantics: resolve names, detect collisions, write nothing.
- Remote sources are accepted under the same enablement, approval, and cleanup rules as any other command.

**A preview is not a created GIF.** Preview entries appear in a separate `previews` array, never in `created`, and are not counted by `summary.created`; `summary.previews` counts them. The `created` array is present and empty for `preview`, and the `previews` array is present and empty for `create` and `batch`. A failed preview is reported in `failed` with its stage and error code.

## Per-clip transformations in manifests (spec section 10.4, 11.2)

Every transformation field may appear at the **top level**, at the **clip level**, or both. Clip-level values override top-level values, and — for transformations — also override a command-line flag for that clip.

| Field | Type |
| --- | --- |
| `crop` | Object with integer `x`, `y`, `width`, `height`, or the string `"x:y:width:height"` |
| `width` | Integer, 2 to 8192 |
| `height` | Integer, 2 to 8192 |
| `speed` | Number, 0.25 to 4.0, at most three fractional digits |
| `dither` | One of `none`, `bayer`, `floyd_steinberg`, `sierra2`, `sierra2_4a` |
| `bayerScale` | Integer, 0 to 5 |

The manifest `schemaVersion` stays **1**: every field is optional and additive, no existing field changes meaning, and a manifest that omits them behaves exactly as it did in 0.2.0. An unknown transformation field generates a warning under the existing unknown-field rule.

### Worked example

```json
{
  "schemaVersion": 1,
  "input": "./videos/demo.mp4",
  "profile": "balanced",
  "width": 800,
  "dither": "sierra2_4a",
  "clips": [
    {
      "name": "opening",
      "start": "00:01:00",
      "end": "00:01:05",
      "crop": { "x": 320, "y": 180, "width": 1280, "height": 720 }
    },
    {
      "name": "reaction",
      "start": "00:03:20",
      "duration": 7,
      "crop": "0:0:1920:800",
      "speed": 2.0,
      "dither": "bayer",
      "bayerScale": 5
    }
  ]
}
```

Run against a 1920x1080 source with a batch-wide flag that the clips partly override:

```
python scripts/video_to_gif.py batch \
  --manifest "./clips.json" \
  --speed 0.5 \
  --json
```

- `opening` — crops to 1280x720, inherits `width: 800` and `dither: sierra2_4a` from the top level, and takes `speed 0.5` from the flag (it defines no clip-level speed). Output: 800x450, 5000 ms source range played at half speed → 10000 ms GIF.
- `reaction` — crops to a 1920x800 letterbox, and its **clip-level** `speed: 2.0`, `dither: bayer`, `bayerScale: 5` beat the `--speed 0.5` flag and the top-level dither. Output: 800x334, 7000 ms source range → 3500 ms GIF.

The CSV equivalent uses one column per field, with the colon-separated crop form; an empty cell means "not specified for this row", so the next precedence level applies:

```csv
name,start,end,duration,profile,crop,width,height,speed,dither,bayerScale
opening,00:01:00,00:01:05,,balanced,320:180:1280:720,800,,1.0,sierra2_4a,
reaction,00:03:20,,7,high,,640,,2.0,bayer,5
ending,00:14:30,00:14:35,,small,,,,,,
```

## Filter-chain order (spec section 15.2)

For every valid clip the engine builds this pipeline, and the order is normative:

```
seek → decode only the required duration → normalize orientation
     → crop → speed retiming (setpts) → frame-rate conversion (fps) → scale
     → palettegen → paletteuse (with the effective dither mode)
     → temporary file → verify → atomic move
```

Why the order matters:

- **Crop before scale.** Scaling, aspect-ratio preservation, the profile maximum width, and the no-upscale rule all apply to the cropped rectangle rather than the original frame. Reversing this would make output dimensions depend on the pre-crop frame and would make a cropped clip's size unpredictable.
- **Speed before frame-rate conversion.** The requested output frame rate then describes the finished GIF instead of the pre-retimed stream — `--fps 15 --speed 2.0` gives a 15fps GIF, not a 30fps one.
- **Frame-rate conversion before palette generation** (unchanged from 0.1.0), so the palette is derived from the frames that are actually encoded.

The crop/setpts/fps/scale chain is built from validated values only and is **identical in the palette-generation pass and the encoding pass**, so palette generation can never be driven by a different or unvalidated parameter set (SEC-018).

Preview extraction uses the spatial steps only — seek, decode, orientation, crop, scale — skips retiming, frame-rate conversion, and both palette passes, and encodes a single full-colour PNG. Temporary-file, verification, and atomic-move behavior is unchanged, except that the verification step checks for a non-empty PNG.

## Reporting (FR-030)

Every entry in `created` carries a `transformations` object plus `outputDurationMs`:

```json
{
  "clipIndex": 0,
  "name": "opening",
  "path": "./output/opening.gif",
  "startMs": 60000,
  "endMs": 65000,
  "durationMs": 5000,
  "outputDurationMs": 2500,
  "width": 640,
  "height": 360,
  "fps": 15,
  "sizeBytes": 2814300,
  "transformations": {
    "crop": { "x": 320, "y": 180, "width": 1280, "height": 720 },
    "sourceWidth": 1920,
    "sourceHeight": 1080,
    "effectiveSourceWidth": 1280,
    "effectiveSourceHeight": 720,
    "speed": 2.0,
    "dither": "sierra2_4a",
    "bayerScale": null,
    "upscaled": false
  }
}
```

| Field | Meaning |
| --- | --- |
| `transformations.crop` | The applied rectangle, or `null` when no crop was applied |
| `transformations.sourceWidth` / `sourceHeight` | Orientation-normalized source dimensions |
| `transformations.effectiveSourceWidth` / `effectiveSourceHeight` | Dimensions after cropping (equal to the source dimensions when no crop was applied) |
| `transformations.speed` | Effective speed multiplier |
| `transformations.dither` | Effective dither mode |
| `transformations.bayerScale` | Effective Bayer scale, or `null` when the mode is not `bayer` |
| `transformations.upscaled` | `true` when the output exceeds the effective source dimensions under an explicit `allowUpscale` |
| `outputDurationMs` | Duration of the generated GIF, `round(durationMs / speed)` |

`width`, `height`, and `fps` continue to report the effective output values and `durationMs` the selected **source** range duration; their meanings are unchanged from 0.1.0. Entries in `previews` carry `path`, `atMs`, `width`, `height`, `sizeBytes`, and the same `transformations` object with `speed` reported as `1.0` and `dither`/`bayerScale` as `null`. `summary` gains a `previews` count.

All of these fields are additive: the structured result `schemaVersion` remains **1**.

Warnings are plain strings, and a 0.3.0 warning always begins with its stable token followed by `": "`. The tokens defined in this version are **`UPSCALE_NOT_ALLOWED`** and **`TRANSFORMATION_NOT_APPLICABLE`**.
