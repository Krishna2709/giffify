# Troubleshooting

Every failure returns a stable error `code`, a human-readable `message`, the failed `stage`, the relevant `clipIndex` when applicable, and a remediation suggestion when one is known. With `--json`, read these from the final JSON document on stdout. The process exit code also indicates the outcome.

## Exit codes (spec section 14)

| Code | Meaning | What to do |
| --- | --- | --- |
| `0` | Complete success | All requested GIFs were created. |
| `2` | Invalid CLI usage or malformed schema | Fix the command arguments, or fix the config/manifest field flagged by the validator (`validate-config` / `validate-manifest`). |
| `3` | Required dependency missing | `ffmpeg`/`ffprobe` not found (or the optional `yt-dlp` adapter — see `YTDLP_MISSING`). See `references/installation.md` and re-run `doctor`. |
| `4` | Input not found or inaccessible | Check the source path and read permissions; confirm the file exists inside the project. |
| `5` | Invalid or unsupported media | The file is not decodable, or is a reference-following container (see `UNSUPPORTED_MEDIA_CONTAINER`). Also covers `UNSUPPORTED_URL_SCHEME` and `DRM_PROTECTED` for remote sources. |
| `6` | Invalid timestamp or clip definition | A timestamp is out of range or a clip is malformed. Fix it, or choose a `skip`/`clamp` policy. |
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

## Diagnosing before you convert

- `doctor --json` — verify dependencies, filters, encoder, and temp/output writability.
- `inspect --input <file> --json` — confirm duration, dimensions, frame rate, codec, and stream selection.
- `batch --manifest <file> --dry-run --json` — preflight: validate clips, resolve names, detect collisions, and estimate work without producing GIFs.
- `validate-config` / `validate-manifest` — catch schema problems with a specific field path before running a job.
