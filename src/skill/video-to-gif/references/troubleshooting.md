# Troubleshooting

Every failure returns a stable error `code`, a human-readable `message`, the failed `stage`, the relevant `clipIndex` when applicable, and a remediation suggestion when one is known. With `--json`, read these from the final JSON document on stdout. The process exit code also indicates the outcome.

## Exit codes (spec section 14)

| Code | Meaning | What to do |
| --- | --- | --- |
| `0` | Complete success | All requested GIFs were created. |
| `2` | Invalid CLI usage or malformed schema | Fix the command arguments, or fix the config/manifest field flagged by the validator (`validate-config` / `validate-manifest`). Also covers a `preview --output-name` whose extension is not `.png` (`INVALID_USAGE`). |
| `3` | Required dependency missing | `ffmpeg`/`ffprobe` not found (or the optional `yt-dlp` adapter — see `YTDLP_MISSING`). See `references/installation.md` and re-run `doctor`. |
| `4` | Input not found or inaccessible | Check the source path and read permissions; confirm the file exists inside the project. |
| `5` | Invalid or unsupported media | The file is not decodable, or is a reference-following container (see `UNSUPPORTED_MEDIA_CONTAINER`). Also covers `UNSUPPORTED_URL_SCHEME` and `DRM_PROTECTED` for remote sources. |
| `6` | Invalid timestamp or clip definition | A timestamp is out of range or a clip is malformed. Fix it, or choose a `skip`/`clamp` policy. Also covers every invalid transformation: `INVALID_CROP`, `INVALID_DIMENSIONS`, `INVALID_SPEED`, `INVALID_DITHER`. |
| `7` | Output collision | A destination file already exists. Choose a collision policy (see below). |
| `8` | Filesystem permission or project-boundary violation | The output path is not writable, or is outside the project without authorization. Also covers `REMOTE_DISABLED` and `PRIVATE_NETWORK_BLOCKED` for remote sources. |
| `9` | FFmpeg conversion failure | Encoding failed at runtime (see `FFMPEG_FAILED`). |
| `10` | Operation cancelled | The user cancelled; completed GIFs are preserved, partial output (including any partial download) removed. |
| `11` | Partial batch success | Some clips succeeded, some failed. Inspect the `failed` array. |
| `12` | Internal engine error | Unexpected engine fault. Re-run with `--debug` for diagnostics. |
| `13` | Resource limit exceeded | A per-clip timeout, temp-disk ceiling, or download size ceiling / free-disk shortfall was hit (see `RESOURCE_LIMIT_EXCEEDED`, `REMOTE_TOO_LARGE`). |
| `14` | Remote acquisition failure | A remote download failed: network error, HTTP error status, truncated download, or the download timeout (see `REMOTE_DOWNLOAD_FAILED`). |

There is no exit code `1`. Internal stack traces are not shown by default; pass `--debug` to expose diagnostic details.

## Error codes

### `REMOTE_DISABLED` (exit 8, `status: "remote_disabled"`)

A remote URL was supplied but remote sources are disabled (the default) and were not overridden. No network access occurred.

- Remediation: if the user has a lawful basis to use the source and approves network access, enable remote acquisition for a single invocation by adding `--allow-remote`, or set `remoteSources` to `ask` or `enabled` in `.video-to-gif.json`. See `references/remote-sources.md`.
- (Version 0.1.0 reported this situation as `UNSUPPORTED_REMOTE_SOURCE`; 0.2.0 reports `REMOTE_DISABLED`.)

### `UNSUPPORTED_URL_SCHEME` (exit 5)

The URL's scheme is not on the allowlist. Only `https` (and `http`, with an unencrypted-transfer warning) are permitted; `file` and every other scheme are rejected and never fetched or opened.

- Remediation: provide an `https` URL, or download the file yourself and pass a local path.

### `PRIVATE_NETWORK_BLOCKED` (exit 8)

The URL resolved to a loopback, private-network, link-local/unique-local, or cloud instance-metadata address. These are blocked to prevent server-side request forgery (SSRF).

- Remediation: use a public host. The address is fetched only if the user explicitly approves that specific address. See `references/remote-sources.md`.

### `REMOTE_TOO_LARGE` (exit 13)

The remote download exceeded `limits.maxDownloadBytes` (default 2147483648 bytes / 2 GiB), enforced on bytes actually received. No partial file is left behind.

- Remediation: use a smaller source, or raise `limits.maxDownloadBytes` in `.video-to-gif.json` (see `references/configuration.md`).

### `REMOTE_DOWNLOAD_FAILED` (exit 14)

A remote download failed: a network error, an HTTP error status, a truncated/incomplete download, or the download exceeded `limits.maxDownloadSeconds` (default 900s). Any partial download is removed.

- Remediation: verify the URL is reachable and still valid (signed URLs expire), check connectivity, then retry. Raise `limits.maxDownloadSeconds` for large sources on slow links.

### `DRM_PROTECTED` (exit 5)

The source is DRM-protected or otherwise access-controlled. The engine does not bypass, disable, or circumvent DRM, encryption, authentication, or platform access controls.

- Remediation: obtain an unprotected copy you are permitted to use, and pass that instead.

### `YTDLP_MISSING` (exit 3, `status: "dependency_missing"`)

A video-page URL was requested with `--remote-adapter ytdlp`, but the optional `yt-dlp` adapter is not installed. yt-dlp is never bundled and is detected independently of FFmpeg. No acquisition was attempted.

- Remediation: install yt-dlp with approval (see the optional-adapter section of `references/installation.md`), then re-run `doctor --json` to confirm and retry.

### `UNSUPPORTED_MEDIA_CONTAINER` (exit 5)

The input's detected container is a reference-following format — an HLS playlist, a DASH manifest, or an FFmpeg concat script. These can point at other (possibly network) resources, so they are rejected. FFmpeg/ffprobe are always invoked with a local-only protocol whitelist (`-protocol_whitelist file,pipe`).

- Remediation: provide a self-contained local media file (e.g., `.mp4`, `.mov`, `.webm`) rather than a playlist/manifest.

### `RESOURCE_LIMIT_EXCEEDED` (exit 13)

A per-clip wall-clock timeout (`maxClipProcessingSeconds`, default 600s) or the temporary-disk ceiling (`maxTemporaryBytes`, default 2 GiB) was exceeded. The engine terminates FFmpeg via the cancellation sequence and removes temporary and partial output.

- Remediation: shorten the clip, lower the profile (smaller width/fps/colors), or raise the relevant value in the `limits` object of `.video-to-gif.json` (see `references/configuration.md`).

### `INVALID_CROP` (exit 6, `status: "validation_failed"`)

The crop rectangle is malformed or does not fit the source. Detected in preflight; **no FFmpeg process is started**.

Common causes: a value that is not four unsigned integers in `x:y:width:height` form; an object form missing one of `x`, `y`, `width`, `height` or carrying an extra key; a component outside `0`–`65535`; a `width` or `height` below `2`; or `x + width` / `y + height` extending past the source frame.

- Remediation: run `inspect --input <file> --json` and read the reported `width`/`height` — those are the orientation-normalized dimensions the rectangle must fit inside — then supply `x:y:width:height` within them. Never guess a rectangle: extract a `preview` frame and confirm the framing with the user.

A `crop` key inside `transformations` in `.video-to-gif.json` is a different failure: it is reported as `INVALID_CONFIG` (exit 2) with field path `transformations.crop`, because a rectangle is only meaningful against one specific source. Move it to `--crop` or to a manifest clip.

### `INVALID_DIMENSIONS` (exit 6, `status: "validation_failed"`)

An explicit `width` or `height` is not an integer in the closed range **2 to 8192**.

- Remediation: use an integer between 2 and 8192. Remember these are *maximum* bounds with the aspect ratio preserved, not exact output sizes; supplying both fits the frame inside that box. Odd values are legal and are honored exactly.

### `INVALID_SPEED` (exit 6, `status: "validation_failed"`)

The speed multiplier is not a decimal in the closed range **0.25 to 4.0** with at most three fractional digits. Zero, negative values, non-numeric text, and exponent notation are all rejected.

- Remediation: use a plain decimal such as `0.5`, `1.25`, or `2.0`. The engine never clamps a speed value — `--invalid-timestamp-policy clamp` applies to timestamps only.

### `INVALID_DITHER` (exit 6, `status: "validation_failed"`)

Either the `dither` value is not a member of the enumeration (comparison is case-sensitive after trimming surrounding whitespace), or `bayerScale` is not an integer in **0 to 5**, or `bayerScale` was supplied explicitly while the effective dither mode is not `bayer`.

- Remediation: use one of `none`, `bayer`, `floyd_steinberg`, `sierra2`, `sierra2_4a` — the error message lists them. Supply `bayerScale` only together with `dither bayer`, otherwise remove it. See `references/transformations.md` for the size/quality tradeoff of each mode.

### `INVALID_USAGE` from `preview --output-name` (exit 2, `status: "validation_failed"`)

A preview writes a PNG still, so `--output-name` must end in `.png`. A name with any other extension (for example `framing.jpg`) is rejected.

- Remediation: pass a `.png` filename, or omit the extension entirely and the engine appends `.png`. The name must still be a bare filename with no path separators. Omit `--output-name` altogether to get the generated name `<video-stem>_<at>.png` (or `<clip-name>_<start>.png` in the manifest form).

### `FFMPEG_FAILED` (exit 9)

FFmpeg exited with a non-zero status during encoding.

- Remediation: check that the source media is intact (`inspect` the file), confirm the clip range is valid, and re-run with `--debug` for FFmpeg diagnostics. In a batch with `continueOnError: true`, remaining clips are still attempted and the overall status is `partial_success`.

### Collisions (exit 7, `status: "collision"`)

The engine never overwrites an existing file by default (`fail`). When a destination already exists, the result reports the collision instead of encoding.

- Remediation: pick one policy for the whole batch and re-run with `--collision-policy`:
  - `overwrite` — replace the existing file.
  - `unique` — write a new uniquely numbered file alongside it.
  - `skip` — leave the existing file and skip that clip.
  - `fail` — abort (default).

Ask the user once for a policy covering all detected collisions, then re-run.

## Warnings

Warnings are plain strings in the result's `warnings` array and never fail a job. A warning defined by version 0.3.0 begins with a stable token followed by `": "`, so it can be identified without parsing prose.

### `UPSCALE_NOT_ALLOWED`

The resolved output dimensions exceeded the effective source dimensions (the cropped rectangle when a crop was applied, otherwise the orientation-normalized frame) and `allowUpscale` was not set, so the output was **clamped to the effective source size**. The GIF was still produced.

This warning is emitted **only** when the clamped bound was explicitly supplied as `width` or `height`. A profile maximum width that simply exceeds a small source does not warn.

- Remediation: if the larger size is genuinely wanted, re-run with `--allow-upscale` (quality will not improve — pixels are interpolated). Otherwise ignore the warning, or lower the requested bound to the source size.

### `TRANSFORMATION_NOT_APPLICABLE`

Settings that a still frame cannot express — `speed`, `fps`, `loop`, `colors`, `dither`, `bayerScale` — were supplied to `preview`. They are accepted so a preview can be requested with the same settings as the GIF it previews, but they change nothing. One warning per invocation names all the ignored settings.

- Remediation: none required. Drop those flags from the `preview` call if you want a clean result, and keep them for the `create`/`batch` run that follows.

## Diagnosing before you convert

- `doctor --json` — verify dependencies, filters, encoder, and temp/output writability.
- `inspect --input <file> --json` — confirm duration, dimensions, frame rate, codec, and stream selection.
- `batch --manifest <file> --dry-run --json` — preflight: validate clips, resolve names, detect collisions, and estimate work without producing GIFs.
- `preview --input <file> --at <timestamp> --json` — extract one full-colour PNG still to confirm a crop or framing before encoding anything. `preview --manifest <file> --dry-run --json` plans stills without writing them.
- `validate-config` / `validate-manifest` — catch schema problems with a specific field path before running a job. `validate-config` applies every source-independent transformation check (enum membership, numeric ranges) without touching a source; crop bounds and upscale evaluation need a source and are checked in preflight.
