# Project configuration

The project configuration file is `.video-to-gif.json`. It stores per-project defaults so the agent does not have to re-ask preferences on every request.

## Location

- The file MUST be named `.video-to-gif.json`.
- It is resolved relative to the detected project root.
- When no project root is detectable, the current working directory is used.

The file is optional. On first use (no file present), the agent asks for a quality profile and may save the choice here after the user agrees.

## Schema

```json
{
  "schemaVersion": 1,
  "defaultProfile": "balanced",
  "outputDirectory": "./output",
  "loop": "forever",
  "collisionPolicy": "ask",
  "continueOnError": true,
  "keepTemporaryFiles": false,
  "allowOutsideProject": false,
  "remoteSources": "disabled",
  "limits": {
    "maxClipProcessingSeconds": 600,
    "maxTemporaryBytes": 2147483648
  }
}
```

### Fields

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `schemaVersion` | integer | `1` | Must be `1` for this release. |
| `defaultProfile` | string | `balanced` | One of `small`, `balanced`, `high`, `custom`. |
| `outputDirectory` | string | `./output` | Created if missing. Writing outside the project root requires approval (see below). |
| `loop` | string or integer | `forever` | `forever`, `once`, or an integer `N >= 1`. `0` is rejected. |
| `collisionPolicy` | string | `ask` | Skill-layer value `ask`, or an engine value `fail` / `overwrite` / `unique` / `skip`. |
| `continueOnError` | boolean | `true` | When true, a runtime failure on one clip does not stop remaining clips. |
| `keepTemporaryFiles` | boolean | `false` | Debug only. Keeps palette/temp files instead of cleaning up. |
| `allowOutsideProject` | boolean | `false` | Must be true (plus explicit approval) before the engine writes outside the project root. |
| `remoteSources` | string | `disabled` | Fixed at `disabled` in v0.1.0. Remote sources are a v0.2.0 feature. |
| `limits` | object | see below | Resource-safety limits. |

### `limits` object

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `maxClipProcessingSeconds` | integer | `600` | Per-clip wall-clock timeout. Exceeding it terminates FFmpeg via the cancellation sequence, cleans up temp/partial files, and returns error code `RESOURCE_LIMIT_EXCEEDED` (exit 13). |
| `maxTemporaryBytes` | integer | `2147483648` (2 GiB) | Ceiling on temporary-disk usage per job. Exceeding it triggers the same cleanup-and-fail behavior. |

Both defaults are documented and configurable. The engine SHOULD also reject sources whose declared dimensions or frame counts are implausibly large before decoding begins.

## Precedence

Highest priority first (spec section 9.3):

1. Command-line argument passed to the engine.
2. Request-specific user instruction.
3. Project configuration (`.video-to-gif.json`).
4. Built-in default.

A request-specific override (e.g., "use high quality just this once") MUST take precedence over the saved configuration WITHOUT modifying the file, unless the user explicitly asks to save it.

## Restrictions (spec section 9.4)

Configuration MUST NOT contain any of the following. These are treated as data-only settings; there is no mechanism to execute anything from this file:

- Cloud credentials.
- Access tokens.
- Signed URLs.
- User passwords.
- Private keys.
- Arbitrary shell commands.
- Executable hook definitions.

Additional rules:

- Malformed configuration produces a validation error with a specific field path (surfaced by `validate-config`).
- Unknown fields generate warnings rather than being silently accepted (they do not fail validation).

## Validation

```
python scripts/video_to_gif.py validate-config --config "./.video-to-gif.json" --json
```

The schema for this file is `assets/config.schema.json`.
