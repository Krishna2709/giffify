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
  "keepRemoteSource": false,
  "transformations": {
    "width": null,
    "height": null,
    "speed": 1.0,
    "dither": null,
    "bayerScale": null
  },
  "limits": {
    "maxClipProcessingSeconds": 600,
    "maxTemporaryBytes": 2147483648,
    "maxDownloadBytes": 2147483648,
    "maxDownloadSeconds": 900
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
| `remoteSources` | string | `disabled` | Remote acquisition policy: `disabled`, `ask`, or `enabled`. See below and `references/remote-sources.md`. |
| `keepRemoteSource` | boolean | `false` | When true, a downloaded remote source is retained after the job and its path is reported in the result. Equivalent to `--keep-remote-source`. |
| `transformations` | object | see below | Global transformation defaults (v0.3.0). A crop rectangle is **not** permitted here. |
| `limits` | object | see below | Resource-safety and download limits. |

### `transformations` object (spec section 9.6)

Project-wide transformation defaults. Every field is optional; a configuration that omits the object behaves as though the documented defaults were supplied.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `width` | integer or `null` | `null` | Maximum output width in pixels, 2 to 8192. `null` means the effective quality profile's maximum width applies. Invalid values fail with `INVALID_DIMENSIONS` (exit 6). |
| `height` | integer or `null` | `null` | Maximum output height in pixels, 2 to 8192. `null` means unbounded (height follows the aspect ratio). Invalid values fail with `INVALID_DIMENSIONS` (exit 6). |
| `speed` | number | `1.0` | Playback speed multiplier, 0.25 to 4.0, at most three fractional digits. Invalid values fail with `INVALID_SPEED` (exit 6). |
| `dither` | string or `null` | `null` | One of `none`, `bayer`, `floyd_steinberg`, `sierra2`, `sierra2_4a`. `null` means the effective profile's default mode. Invalid values fail with `INVALID_DITHER` (exit 6). |
| `bayerScale` | integer or `null` | `null` | Bayer matrix scale, 0 to 5; meaningful only when the effective dither mode is `bayer`. Invalid values fail with `INVALID_DITHER` (exit 6). |

**`crop` is forbidden in configuration.** A crop rectangle is only meaningful against one specific source's dimensions, so `transformations.crop` is rejected by `validate-config` as a validation error with the field path `transformations.crop` (exit 2). Supply a crop per request with `--crop`, or per clip in a manifest.

`validate-config` applies every source-independent check — enum membership and numeric ranges — without touching a source. Source-dependent checks (crop bounds against the source dimensions, upscale evaluation) happen during preflight.

These fields are additive and do not change `schemaVersion`. Full semantics are in `references/transformations.md`.

### `limits` object

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `maxClipProcessingSeconds` | integer | `600` | Per-clip wall-clock timeout. Exceeding it terminates FFmpeg via the cancellation sequence, cleans up temp/partial files, and returns error code `RESOURCE_LIMIT_EXCEEDED` (exit 13). |
| `maxTemporaryBytes` | integer | `2147483648` (2 GiB) | Ceiling on temporary-disk usage per job. Exceeding it triggers the same cleanup-and-fail behavior. |
| `maxDownloadBytes` | integer | `2147483648` (2 GiB) | Maximum size of a single remote download, enforced on bytes actually received during streaming (not on a declared `Content-Length`). Exceeding it fails with `REMOTE_TOO_LARGE` (exit 13) and leaves no partial file. |
| `maxDownloadSeconds` | integer | `900` | Download wall-clock timeout in seconds. Exceeding it aborts the download with `REMOTE_DOWNLOAD_FAILED` (exit 14) and leaves no partial file. |

All defaults are documented and configurable. `maxDownloadBytes` and `maxDownloadSeconds` apply only to remote acquisition (version 0.2.0); a configuration that omits them behaves as though the documented defaults were supplied, and they do not change `schemaVersion`. A remote download also counts toward the `maxTemporaryBytes` accounting, and a free-disk shortfall before a download begins fails with `RESOURCE_LIMIT_EXCEEDED` (exit 13). The engine SHOULD also reject sources whose declared dimensions or frame counts are implausibly large before decoding begins.

## Remote source policy (spec section 9.5)

`remoteSources` controls remote acquisition and defaults to `disabled`:

| Value | Behavior |
| --- | --- |
| `disabled` (default) | Remote URLs are rejected with `REMOTE_DISABLED` (status `remote_disabled`, exit 8); no network access occurs. |
| `ask` | The agent obtains explicit user approval before each remote acquisition (before supplying `--allow-remote`). |
| `enabled` | Remote acquisition is permitted without a per-request approval prompt. |

The `--allow-remote` flag overrides a `disabled` or `ask` value for a single invocation. Enablement alone does not authorize private-network addresses or bypass the URL scheme allowlist. `keepRemoteSource` defaults to `false`; when true, a downloaded source is retained and its path is reported in the result. These fields are additive and do not change `schemaVersion`. Full behavior — supported/rejected sources, redaction, rights confirmation — is in `references/remote-sources.md`.

Configuration MUST NOT embed credentials, access tokens, or signed-URL query parameters in any remote source URL. Signed or credentialed URLs are supplied per request, never stored (see Restrictions below).

## Precedence

Highest priority first (spec section 9.3):

1. Command-line argument passed to the engine.
2. Request-specific user instruction.
3. Project configuration (`.video-to-gif.json`).
4. Built-in default.

A request-specific override (e.g., "use high quality just this once") MUST take precedence over the saved configuration WITHOUT modifying the file, unless the user explicitly asks to save it.

### Transformation precedence (spec FR-024)

For transformation parameters only (`crop`, `width`, `height`, `speed`, `dither`, `bayerScale`), the order is refined so that a per-clip value beats a batch-wide flag:

1. **Clip-level manifest field.**
2. Command-line flag.
3. Top-level manifest field.
4. Project configuration (`transformations`).
5. Built-in default.

A clip-level manifest field is more specific than a command-line flag that applies to the whole run, so it wins. Everything else follows the general order above unchanged. See `references/transformations.md`.

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
