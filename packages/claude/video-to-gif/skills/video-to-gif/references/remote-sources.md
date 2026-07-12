# Remote sources (opt-in, version 0.2.0)

Version 0.2.0 can acquire a source from a remote URL. This capability is **disabled by default** and is gated by explicit enablement/approval (FR-018) and a per-source rights confirmation (§19.6). Conversion itself stays entirely local and network-isolated: only the acquisition step touches the network, and all remote access is **download-only** — nothing is ever uploaded (spec section 18).

## Enablement (FR-018)

The `remoteSources` configuration field in `.video-to-gif.json` takes exactly one of three values:

| Value | Behavior |
| --- | --- |
| `disabled` (default) | Remote URLs are rejected with `REMOTE_DISABLED` (status `remote_disabled`, exit 8). No network access occurs. |
| `ask` | The agent must obtain explicit user approval before each remote acquisition. |
| `enabled` | Remote acquisition is permitted without a per-request approval prompt. |

The `--allow-remote` flag overrides a `disabled` or `ask` configuration for a single invocation. When `remoteSources` is `ask` (or when overriding `disabled`), the agent obtains explicit approval before supplying `--allow-remote`. Enablement alone does NOT authorize private-network addresses (see SSRF below) or bypass the URL scheme allowlist.

A remote URL supplied under the default (`disabled`) configuration causes no network access at all — it is rejected before any connection is attempted.

> Version history: in 0.1.0 a remote URL produced `UNSUPPORTED_REMOTE_SOURCE`. In 0.2.0 the disabled-by-default behavior is reported as `REMOTE_DISABLED`.

## Rights confirmation (§19.6)

Before acquiring any remote source, the agent obtains the user's confirmation that they own the video, have permission to use it, or otherwise have a lawful basis to make a GIF from it. This confirmation:

- Is obtained **once per source**, not once per clip.
- Is an interaction requirement only — the skill does **not** record, store, or transmit the confirmation or any related statement.

The skill will never request or accept instructions to bypass DRM, authentication, or access controls.

## Supported source types (FR-019)

- **Direct HTTPS media URLs** — a URL that FFmpeg or a plain download can read (for example, an `.mp4`/`.mov`/`.webm` served over `https`). Preferred.
- **Signed cloud-storage URLs** — treated as direct URLs. Supply them per request; never store them in configuration or a manifest (their query strings are credentials — see redaction below).
- **Direct HTTP media URLs** — permitted only with an explicit warning that the transfer is unencrypted. Prefer `https`.
- **Video-page URLs** — video-platform watch pages are supported only through the optional `yt-dlp` adapter (`--remote-adapter ytdlp`), which is a separate, never-bundled dependency and requires the same enablement and rights confirmation.

A URL may be supplied wherever a source is accepted, including the `--input` argument and a manifest `input` field, subject to enablement.

## What is rejected

| Rejected | Error code | Exit |
| --- | --- | --- |
| DRM-protected or otherwise access-controlled sources (no circumvention is attempted) | `DRM_PROTECTED` | 5 |
| URLs whose scheme is not on the allowlist — `file` and every scheme other than `http`/`https` | `UNSUPPORTED_URL_SCHEME` | 5 |
| Hosts that resolve to loopback, private-network, link-local/unique-local ranges, or cloud instance-metadata endpoints (SSRF protection), unless the specific address is explicitly approved | `PRIVATE_NETWORK_BLOCKED` | 8 |

The scheme allowlist and the private-network block list are enforced before any connection and re-enforced on every redirect target. To resist DNS rebinding, the acquisition component resolves the hostname, checks the resolved address against the block list, and connects to that same address.

> **Scope — direct path vs. yt-dlp adapter.** The full guarantees in this section (per-address validation, connection pinning to the validated address, re-checking every redirect) apply to the **built-in direct-download path**. The optional `yt-dlp` adapter does its own resolution, connection, and redirect-following that the engine **cannot pin** — see "Optional yt-dlp adapter" below for the best-effort pre-checks it does receive and the residual risk that remains.

Authenticated provider-account integrations (private Google Drive/S3/GCS/Azure/Dropbox files) are out of scope for 0.2.0.

## Acquisition model (FR-020)

1. **Download** the source into a secure temporary directory with an unpredictable name in the OS temporary location.
2. **Convert** the downloaded file with the existing local pipeline — which stays network-isolated under SEC-010 and treats the download as untrusted local media (same `-protocol_whitelist file,pipe`, same reference-following-container rejection).
3. **Delete** the download after the job completes, whether it succeeded or failed.

`inspect` on a URL acquires the source first (because inspection is network-isolated), then deletes it like any other job.

### Retaining the download

The download is retained only when the user explicitly asks, via `keepRemoteSource: true` in configuration or the `--keep-remote-source` flag. When retained, the engine reports the retained file path in the structured result. A retained download is preserved like a completed output; otherwise partial and completed downloads are removed on success, failure, or cancellation (section 16).

## Limits and hardening (FR-021)

Every remote download enforces:

| Limit | Config key | Default | On breach |
| --- | --- | --- | --- |
| Maximum download size (enforced on bytes actually received during streaming, not on a declared `Content-Length`) | `limits.maxDownloadBytes` | `2147483648` (2 GiB) | `REMOTE_TOO_LARGE`, exit 13 |
| Download wall-clock timeout | `limits.maxDownloadSeconds` | `900` seconds | `REMOTE_DOWNLOAD_FAILED`, exit 14 |
| Free-disk check before download begins | — | — | `RESOURCE_LIMIT_EXCEEDED`, exit 13 |

A network error, an HTTP error status, or a truncated/incomplete download produces `REMOTE_DOWNLOAD_FAILED` (exit 14). On any download failure or cancellation, partial downloads are removed. Downloads count toward the temporary-disk accounting of SEC-011.

A `Content-Type` header or URL file extension may be used for an early advisory check, but it is not authoritative — ffprobe inspection remains the authoritative gate for whether a downloaded file is usable media.

## Redaction (SEC-015)

Any source URL echoed anywhere — logs, errors, warnings, progress events, and structured results — is reduced to **scheme, host, and path only**. The query string and any userinfo component are stripped; fragments and query strings are never reproduced. Signed-URL tokens and embedded credentials therefore never appear in output. Credentials, access tokens, and signed-URL query parameters must be supplied per request and must never be stored in configuration (section 9.4) or manifests.

When redacting a URL for the user yourself, follow the same rule: show only scheme, host, and path.

## Progress (FR-023)

Download progress is emitted as progress events on standard error using stage `download`, for example:

```
{"event":"stage_progress","stage":"download","bytesReceived":10485760,"totalBytes":52428800,"percent":20.0}
```

`totalBytes` may be `null` when the source does not declare a size, in which case `percent` may be omitted. Any URL in a progress event is redacted under SEC-015. A successful remote acquisition does not change the structure of the final result beyond additive fields (for example, the retained-source path when `--keep-remote-source` is used).

## Optional yt-dlp adapter (FR-022)

Video-page URLs are acquired through the optional `yt-dlp` adapter, selected with `--remote-adapter ytdlp`. The adapter:

- Is never bundled with the skill or its packages and is detected independently of FFmpeg.
- Requires the same remote enablement (FR-018) and rights confirmation (§19.6) as any other remote source.
- Rejects DRM-protected sources without attempting circumvention.

**Best-effort validation and residual risk (important).** Before launching yt-dlp, the engine applies the same **scheme allowlist** (SEC-013), **URL DRM-marker check** (SEC-017), and an **SSRF host resolution/validation** (SEC-014) — so obvious `file://`, unsupported-scheme, DRM, and internal-host cases are refused up front with no acquisition, and these pre-checks run *before* the missing-binary check so they apply whether or not yt-dlp is installed. However, yt-dlp then performs its **own** DNS resolution, connection, and redirect-following, which the engine **cannot pin**. Unlike the direct path, the yt-dlp path therefore does **not** guarantee the connection is bound to a validated address: a TOCTOU/DNS-rebinding change after the pre-check, or a redirect yt-dlp follows to a private address, is **not guaranteed** to be blocked. This is an accepted, documented residual risk (SEC-014). Prefer the direct HTTPS path for untrusted inputs.

When the adapter is requested but `yt-dlp` is not installed, the engine returns `YTDLP_MISSING` (status `dependency_missing`, exit 3) and attempts no acquisition. Install guidance is in `references/installation.md`; `doctor --json` reports whether yt-dlp is available and its version.

## Troubleshooting pointers

- Remote error codes (`REMOTE_DISABLED`, `UNSUPPORTED_URL_SCHEME`, `PRIVATE_NETWORK_BLOCKED`, `REMOTE_TOO_LARGE`, `REMOTE_DOWNLOAD_FAILED`, `DRM_PROTECTED`, `YTDLP_MISSING`) and their exit codes and remediation are in `references/troubleshooting.md`.
- Remote-source configuration keys (`remoteSources`, `keepRemoteSource`, `limits.maxDownloadBytes`, `limits.maxDownloadSeconds`) are in `references/configuration.md`.
