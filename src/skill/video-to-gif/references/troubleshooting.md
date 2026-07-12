# Troubleshooting

Every failure returns a stable error `code`, a human-readable `message`, the failed `stage`, the relevant `clipIndex` when applicable, and a remediation suggestion when one is known. With `--json`, read these from the final JSON document on stdout. The process exit code also indicates the outcome.

## Exit codes (spec section 14)

| Code | Meaning | What to do |
| --- | --- | --- |
| `0` | Complete success | All requested GIFs were created. |
| `2` | Invalid CLI usage or malformed schema | Fix the command arguments, or fix the config/manifest field flagged by the validator (`validate-config` / `validate-manifest`). |
| `3` | Required dependency missing | `ffmpeg`/`ffprobe` not found. See `references/installation.md` and re-run `doctor`. |
| `4` | Input not found or inaccessible | Check the source path and read permissions; confirm the file exists inside the project. |
| `5` | Invalid or unsupported media | The file is not decodable, or is a reference-following container (see `UNSUPPORTED_MEDIA_CONTAINER`). |
| `6` | Invalid timestamp or clip definition | A timestamp is out of range or a clip is malformed. Fix it, or choose a `skip`/`clamp` policy. |
| `7` | Output collision | A destination file already exists. Choose a collision policy (see below). |
| `8` | Filesystem permission or project-boundary violation | The output path is not writable, or is outside the project without authorization. |
| `9` | FFmpeg conversion failure | Encoding failed at runtime (see `FFMPEG_FAILED`). |
| `10` | Operation cancelled | The user cancelled; completed GIFs are preserved, partial output removed. |
| `11` | Partial batch success | Some clips succeeded, some failed. Inspect the `failed` array. |
| `12` | Internal engine error | Unexpected engine fault. Re-run with `--debug` for diagnostics. |
| `13` | Resource limit exceeded | A per-clip timeout or temp-disk ceiling was hit (see `RESOURCE_LIMIT_EXCEEDED`). |

There is no exit code `1`. Internal stack traces are not shown by default; pass `--debug` to expose diagnostic details.

## Error codes

### `UNSUPPORTED_REMOTE_SOURCE`

A URL (or other remote source) was supplied. Version 0.1.0 performs no network access and processes local files only.

- Remediation: download the video yourself and pass a local file path. Remote acquisition is planned for v0.2.0.

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
