# Input formats

This document covers timestamp forms, duration and clip rules, the JSON manifest, the CSV manifest, and loop syntax.

## Timestamps (FR-004)

The engine accepts these forms:

| Input | Meaning |
| --- | --- |
| `75` | 75 seconds |
| `75.5` | 75.5 seconds |
| `01:15` | `MM:SS` — 1 minute 15 seconds |
| `01:15.500` | `MM:SS.mmm` — 1 minute 15.5 seconds |
| `00:01:15` | `HH:MM:SS` — 1 minute 15 seconds |
| `00:01:15.500` | `HH:MM:SS.mmm` — 1 minute 15.5 seconds |

Rules:

- A bare number is seconds (with an optional fractional part).
- `MM:SS` is minutes and seconds.
- `HH:MM:SS` is hours, minutes, and seconds.
- Fractional seconds are supported to millisecond precision (`.mmm`).
- Internally every timestamp is normalized to integer milliseconds.

## Duration (FR-005)

- In JSON manifests, `duration` may be a **number** (interpreted as seconds, with an optional fractional part) or a **string** in any timestamp form above.
- In CSV manifests and CLI arguments, `duration` is parsed the same way: a bare number is seconds; colon-separated forms follow the timestamp rules.
- Durations normalize to integer milliseconds and MUST be strictly positive (`> 0`).

## Clip definitions (FR-005, FR-006)

Each clip requires a `start` plus **exactly one** of `end` or `duration`:

```json
{ "start": "00:01:00", "end": "00:01:05" }
```

```json
{ "start": "00:01:00", "duration": 5 }
```

A clip MUST NOT provide both `end` and `duration` unless both resolve to the same end timestamp.

Validation applied to every clip (in preflight, before any encoding):

```
start >= 0
start < source duration
end > start
end <= source duration
duration > 0
```

- Overlapping clips are allowed.
- Duplicate timestamp ranges produce separate output files only when they have distinct user-provided names; otherwise duplicates are reported during preflight.

### Invalid-timestamp policies (FR-007)

Invalid timestamps are detected before conversion starts. Policies:

- `fail` (default) — reject the job.
- `skip` — skip invalid clips and process valid ones.
- `clamp` — adjust an `end` timestamp to the source duration.

The agent must obtain explicit user approval before using `skip` or `clamp` when the invalid timestamps were not already addressed in the request.

## JSON manifest (spec section 10)

```json
{
  "schemaVersion": 1,
  "input": "./videos/demo.mp4",
  "outputDirectory": "./output",
  "profile": "balanced",
  "loop": "forever",
  "continueOnError": true,
  "clips": [
    {
      "name": "opening",
      "start": "00:01:00",
      "end": "00:01:05"
    },
    {
      "name": "reaction",
      "start": "00:03:20",
      "duration": 7
    }
  ]
}
```

### Required fields

- Top level: `schemaVersion`, `input`, `clips`.
- Each clip: `start`, plus exactly one of `end` or `duration`.

### Optional fields

- Top level: `outputDirectory`, `profile`, `loop`, `continueOnError`, `collisionPolicy`, `width`, `height`, `fps`, `colors`, `allowUpscale`, `crop`, `speed`, `dither`, `bayerScale`.
- Clip level: `name`, `profile`, `width`, `height`, `fps`, `colors`, `loop`, `crop`, `speed`, `dither`, `bayerScale`.

Clip-level values override top-level values.

### Transformation fields (spec section 10.4)

The v0.3.0 transformation fields may appear at the top level, at the clip level, or both:

| Field | Type | Notes |
| --- | --- | --- |
| `crop` | Object with integer `x`, `y`, `width`, `height`, **or** the string `"x:y:width:height"` | Orientation-normalized source pixels; must lie inside the source frame |
| `width` | Integer, 2 to 8192 | Maximum output width |
| `height` | Integer, 2 to 8192 | Maximum output height (new in 0.3.0) |
| `speed` | Number, 0.25 to 4.0, at most three fractional digits | Playback multiplier |
| `dither` | `none`, `bayer`, `floyd_steinberg`, `sierra2`, or `sierra2_4a` | Case-sensitive |
| `bayerScale` | Integer, 0 to 5 | Only when the effective dither mode is `bayer` |

Clip-level values override top-level values **and** a command-line transformation flag for that clip (spec FR-024). An unknown transformation field generates a warning like any other unknown field.

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

`schemaVersion` stays **1**: every transformation field is optional and additive, no existing field changes meaning, and a manifest that omits them behaves exactly as it did in 0.2.0. Full semantics are in `references/transformations.md`.

The `name` field must not contain path separators and must not escape the output directory (see FR-011 / naming rules). The schema for JSON manifests is `assets/manifest.schema.json`.

## CSV manifest (spec section 11)

### Required columns

`start`, and either `end` or `duration` per row.

### Optional columns

`name`, `profile`, `width`, `height`, `fps`, `colors`, `loop`, `crop`, `speed`, `dither`, `bayerScale`.

### Example

```csv
name,start,end,duration,profile
opening,00:01:00,00:01:05,,balanced
reaction,00:03:20,,7,high
ending,00:14:30,00:14:35,,small
```

With transformations:

```csv
name,start,end,duration,profile,crop,width,height,speed,dither,bayerScale
opening,00:01:00,00:01:05,,balanced,320:180:1280:720,800,,1.0,sierra2_4a,
reaction,00:03:20,,7,high,,640,,2.0,bayer,5
ending,00:14:30,00:14:35,,small,,,,,,
```

Rules:

- Each row supplies `start` and exactly one of `end` / `duration` (leave the other column empty, as shown).
- Empty rows are ignored.
- Column names are case-insensitive and whitespace-trimmed.
- Unknown columns generate warnings.
- The `crop` column uses the colon-separated string form `x:y:width:height` — the object form is JSON-only.
- An **empty cell** means the value is not specified for that row, so the next precedence level applies (command-line flag, then top-level manifest field, then project configuration, then the built-in default).

Note: a CSV manifest does not carry the top-level `input`. Supply the source separately (for example via a CLI `--input`, per-request instruction, or project configuration).

## Loop syntax (FR-015)

Used in configuration, JSON manifests, and CSV `loop` columns:

- `forever` — infinite looping (this is the default output behavior).
- `once` — equivalent to a count of `1`.
- An integer `N` where `N >= 1` — the animation plays `N` times in total.
- `0` MUST be rejected, to avoid ambiguity with GIF loop-extension semantics.
