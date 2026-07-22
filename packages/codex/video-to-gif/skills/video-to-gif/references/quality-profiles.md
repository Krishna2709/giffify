# Quality profiles

The skill defines four quality profiles. These control the maximum width, target frame rate, maximum color count, and default dither mode of the generated GIF.

## Profiles (FR-014)

| Profile | Maximum width | Target FPS | Maximum colors | Default dither | Purpose |
| --- | --- | --- | --- | --- | --- |
| `small` | 480 | 10 | 128 | `bayer` (`bayerScale` 5) | Documentation and messaging |
| `balanced` | 640 | 15 | 256 | `sierra2_4a` | General default |
| `high` | 960 | 20 | 256 | `sierra2_4a` | Detailed product or UI motion |
| `custom` | user-defined | user-defined | user-defined | `sierra2_4a` | Advanced control |

The `balanced` profile is the recommended general default.

## Width is a maximum, not a forced width

- Profile widths are **maximum widths**, not forced widths.
- The engine MUST preserve the source aspect ratio.
- The engine MUST NOT upscale by default. If the source is narrower than the profile maximum, the source width is kept (unless the user explicitly opts into upscaling, e.g., `allowUpscale`/`--allow-upscale` where supported).

Example: a 320px-wide source under the `high` profile (max width 960) stays 320px wide; it is not enlarged.

## Explicit `width` / `height` override the profile maximum

A profile's maximum width is a **default bound**, not a ceiling on explicit requests (spec FR-026):

- An explicit `width` or `height` — from a flag, a manifest field, or `transformations` in configuration — replaces the profile's maximum width entirely. `--profile small --width 900` produces a 900px-wide GIF at the `small` profile's frame rate and colour count.
- Both are integers in the closed range **2 to 8192**; anything else is `INVALID_DIMENSIONS` (exit 6).
- Supplying both bounds fits the frame inside that box: output width ≤ `width` **and** output height ≤ `height`, aspect ratio preserved. The frame is never stretched.
- An explicitly supplied bound is honored **exactly, including odd values** — GIF is palette-based and has no even-dimension constraint. A *derived* dimension is rounded to the **nearest integer** and may itself be odd. That one rule applies on every path, and it is the rule 0.1.0 used, so derived dimensions are unchanged from 0.1.0/0.2.0.
- Upscaling stays gated by `--allow-upscale`. Without it, a resolved size larger than the effective source is clamped back to the source size, and the `UPSCALE_NOT_ALLOWED` warning is emitted **only** when the clamped bound was explicitly supplied — a profile maximum that simply exceeds a small source never warns.
- When a crop is applied, all of the above is evaluated against the **cropped** rectangle, not the original frame.

Detail and examples: `references/transformations.md`.

## Frame-rate capping

When the source frame rate is lower than the requested GIF frame rate, the effective frame rate SHOULD NOT exceed the source frame rate. For example, a 12fps source under the `high` profile (target 20fps) produces at most 12fps — the engine does not invent frames.

### Speed and effective fps

`speed` retimes presentation timestamps **before** frame-rate conversion, so the profile's target fps describes the finished GIF, not the pre-retimed stream: `--profile balanced --speed 2.0` still produces a 15fps GIF. No frames are interpolated — speeding up drops frames and slowing down duplicates them.

For a speed **below** 1.0, the retimed stream's intrinsic frame rate is the source frame rate multiplied by the speed, and the effective output frame rate does not exceed that value. A 30fps source at `--speed 0.5` behaves like a 15fps source, so a `high`-profile request for 20fps yields at most 15fps. The selected source range is never changed by speed; only the output duration is, as `round(durationMs / speed)`.

## Custom profile

The `custom` profile lets the user set `width`, `fps`, and `colors` directly. Provide these values per request (or in a manifest at the top level or per clip). The same aspect-ratio, no-upscaling, and frame-rate-capping rules still apply.

Manifest override fields (`width`, `height`, `fps`, `colors`) are available at both the top level and the clip level; clip-level values override top-level values. See `references/input-formats.md`.

### `create` flags for custom values and other overrides

Pass these to `python scripts/video_to_gif.py create` (verify with `create --help`):

| Flag | Purpose |
| --- | --- |
| `--profile custom` | Select the custom profile. |
| `--width <pixels>` | Maximum output width, 2 to 8192 (aspect ratio preserved). |
| `--height <pixels>` | Maximum output height, 2 to 8192 (aspect ratio preserved). |
| `--fps <n>` | Target frames per second (capped at the source frame rate). |
| `--colors <n>` | Maximum palette colors (1–256). |
| `--allow-upscale` | Permit upscaling a source narrower than the target width (off by default). |
| `--crop <x>:<y>:<w>:<h>` | Crop rectangle in orientation-normalized source pixels. |
| `--speed <multiplier>` | Playback speed multiplier, 0.25 to 4.0. |
| `--dither <mode>` | `none`, `bayer`, `floyd_steinberg`, `sierra2`, or `sierra2_4a`. |
| `--bayer-scale <n>` | Bayer scale 0 to 5, for `--dither bayer`. |
| `--loop <forever\|once\|N>` | Loop behavior (see `references/input-formats.md`). |
| `--config <path>` | Use an explicit `.video-to-gif.json` instead of the auto-discovered one. |

Example — a custom-sized GIF that may upscale a small source:

```
python scripts/video_to_gif.py create --input "./videos/demo.mp4" \
  --start "00:01:00" --end "00:01:05" \
  --profile custom --width 800 --fps 24 --colors 200 --allow-upscale \
  --loop once --json
```

## Selecting a profile

- On first project use, if neither a saved profile nor a request-specific profile exists, the agent asks the user to choose Balanced, Small file, High quality, or Custom (FR-013).
- A request-specific profile overrides the saved configuration WITHOUT modifying it, unless the user asks to save it.
- The selected default profile is stored in `.video-to-gif.json` as `defaultProfile` (see `references/configuration.md`).

## Dithering

As of version 0.3.0 the palette-use dither mode is a **public option**, not an internal detail (spec FR-028, section 15.5). Each profile supplies a default that reproduces 0.1.0/0.2.0 output, so a job that specifies no dither is functionally equivalent to earlier releases:

| Profile | Default dither mode | Default `bayerScale` |
| --- | --- | --- |
| `small` | `bayer` | `5` |
| `balanced` | `sierra2_4a` | not applicable |
| `high` | `sierra2_4a` | not applicable |
| `custom` | `sierra2_4a` | not applicable |

Override the default with `--dither` (plus `--bayer-scale` for `bayer`), a manifest `dither`/`bayerScale` field, or `transformations.dither` in configuration. When the effective mode is `bayer` and no scale is supplied, the profile's default scale applies; when the profile's default mode is not `bayer`, the scale defaults to `2`.

Compatibility: an **explicitly requested** mode and scale are honored exactly and will not change across patch releases. A **profile's default** mode or scale may change in a minor release with a changelog entry, and never in a patch release. Adding a value to the enumeration is an additive minor-release change.

Mode-by-mode size and quality guidance is in `references/transformations.md`.
