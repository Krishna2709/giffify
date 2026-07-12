# Quality profiles

Version 0.1.0 defines four quality profiles. These control the maximum width, target frame rate, and maximum color count of the generated GIF.

## Profiles (FR-014)

| Profile | Maximum width | Target FPS | Maximum colors | Purpose |
| --- | --- | --- | --- | --- |
| `small` | 480 | 10 | 128 | Documentation and messaging |
| `balanced` | 640 | 15 | 256 | General default |
| `high` | 960 | 20 | 256 | Detailed product or UI motion |
| `custom` | user-defined | user-defined | user-defined | Advanced control |

The `balanced` profile is the recommended general default.

## Width is a maximum, not a forced width

- Profile widths are **maximum widths**, not forced widths.
- The engine MUST preserve the source aspect ratio.
- The engine MUST NOT upscale by default. If the source is narrower than the profile maximum, the source width is kept (unless the user explicitly opts into upscaling, e.g., `allowUpscale`/`--allow-upscale` where supported).

Example: a 320px-wide source under the `high` profile (max width 960) stays 320px wide; it is not enlarged.

## Frame-rate capping

When the source frame rate is lower than the requested GIF frame rate, the effective frame rate SHOULD NOT exceed the source frame rate. For example, a 12fps source under the `high` profile (target 20fps) produces at most 12fps — the engine does not invent frames.

## Custom profile

The `custom` profile lets the user set `width`, `fps`, and `colors` directly. Provide these values per request (or in a manifest at the top level or per clip). The same aspect-ratio, no-upscaling, and frame-rate-capping rules still apply.

Manifest override fields (`width`, `fps`, `colors`) are available at both the top level and the clip level; clip-level values override top-level values. See `references/input-formats.md`.

### `create` flags for custom values and other overrides

Pass these to `python scripts/video_to_gif.py create` (verify with `create --help`):

| Flag | Purpose |
| --- | --- |
| `--profile custom` | Select the custom profile. |
| `--width <px>` | Maximum output width (aspect ratio preserved). |
| `--fps <n>` | Target frames per second (capped at the source frame rate). |
| `--colors <n>` | Maximum palette colors (1–256). |
| `--allow-upscale` | Permit upscaling a source narrower than the target width (off by default). |
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

## Dithering (informational)

The `balanced` and `high` profiles use FFmpeg's default palette-use dithering; the `small` profile may use a more compression-oriented mode. Dithering is an internal detail and is not part of the public manifest schema; it may change between patch releases without a schema change.
