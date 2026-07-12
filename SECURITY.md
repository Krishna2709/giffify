# Security Policy

This document satisfies spec **NFR-007**: it defines supported versions, a
vulnerability-reporting channel, a disclosure policy, and a summary of the
security model. It applies to the `video-to-gif` Agent Skill and its Claude Code
and Codex packages.

## Supported versions

The project is pre-release. Security fixes are applied to the most recent
development line only; there are no older supported releases yet.

| Version   | Status                       | Security fixes |
| --------- | ---------------------------- | -------------- |
| 0.2.x     | Pre-release (in development)  | Yes            |
| 0.1.x     | Superseded by 0.2.x           | Yes            |
| < 0.1.0   | None released                | N/A            |

This table will be expanded as stable releases are published.

## Reporting a vulnerability

**Reporting channel: GitHub private vulnerability reporting.**

Report security issues privately via GitHub Security Advisories:
<https://github.com/Krishna2709/giffify/security/advisories/new>

Maintainer: [Krishna2709](https://github.com/Krishna2709).

- **Do not** open a public issue for anything that looks security-sensitive.
- If GitHub private reporting is unavailable to you, contact the maintainer
  through their GitHub profile.

When you report, please include:

- The affected version or commit.
- A description of the issue and its impact.
- Reproduction steps or a proof of concept where possible.
- Any suggested remediation.

## Disclosure policy

We follow **coordinated disclosure**:

1. **Acknowledgement** — we aim to acknowledge a report within **5 business days**
   of receipt (best effort during the pre-release period).
2. **Triage** — we validate and assess severity, and keep you updated on progress.
3. **Fix** — we develop and test a fix privately. Target windows are roughly
   **30 days** for high/critical issues and **90 days** for lower-severity issues,
   adjusted for complexity.
4. **Release & credit** — we publish the fix, note it in `CHANGELOG.md`, and
   credit the reporter unless anonymity is requested.

Please give us a reasonable opportunity to remediate before any public
disclosure. These targets are goals, not contractual guarantees, and may shift
while the project is pre-release.

## Security model summary

The skill is built to handle **untrusted media** and **hostile manifests**
safely. Processing is **local by default**; version 0.2.0 adds **opt-in,
download-only** remote source acquisition that is disabled by default and gated
by explicit enablement and approval. The controls below map to the normative
requirements in spec §17 (SEC-001..SEC-017). See
[`docs/security.md`](docs/security.md) for the full explanation and threat model.

- **SEC-001 — No shell execution.** Never `shell=True`; subprocess arguments are
  passed as arrays; user-controlled data is never interpolated into a shell
  command.
- **SEC-002 — Path normalization.** All input and output paths are resolved and
  normalized; generated filenames contain no path separators; manifest-provided
  names cannot escape the output directory.
- **SEC-003 — Project boundary.** The default write boundary is the project root;
  writing outside requires an explicit user-provided destination or explicit
  approval plus `--allow-outside-project`, and the resolved external path is
  shown first.
- **SEC-004 — Overwrite protection.** Existing outputs are never overwritten
  without explicit approval (default collision policy is `fail`).
- **SEC-005 — Network access is opt-in.** Remote acquisition is disabled by
  default. With the default configuration a URL source yields `REMOTE_DISABLED`
  (exit 8) and performs no network access. (In 0.1.0 the same situation yielded
  `UNSUPPORTED_REMOTE_SOURCE`.) When enabled, only the remote-acquisition
  component is network-capable — see SEC-012..SEC-017 below.
- **SEC-006 — Dependency installation.** Installation commands require approval
  and are never run with elevated privileges unless the user authorizes the exact
  command.
- **SEC-007 — Sensitive data.** The engine avoids logging environment values,
  credentials, unrelated home-directory contents, private configuration files, or
  full command environments.
- **SEC-008 — Untrusted media.** FFmpeg runs without elevated privileges,
  processing is limited to the requested source, arbitrary filter scripts and
  executable sidecar/metadata files are not loaded, and malformed partial output
  is cleaned up.
- **SEC-009 — Manifest safety.** Manifest values are treated purely as data — no
  shell expressions, environment expansion, command substitution, Python
  expressions, dynamic imports, or executable hooks.
- **SEC-010 — FFmpeg network isolation.** The no-network guarantee is enforced at
  the FFmpeg layer, not only in argument validation: `ffmpeg`/`ffprobe` are
  invoked with an explicit local-only protocol whitelist
  (`-protocol_whitelist file,pipe`), and reference-following containers (HLS,
  DASH, concat) are rejected as `UNSUPPORTED_MEDIA_CONTAINER` (exit 5) during
  inspection, palette generation, and encoding.
- **SEC-011 — Resource limits.** A per-clip wall-clock timeout and a
  temporary-disk ceiling (defaults 600 s and 2 GiB, configurable via `limits`)
  are enforced; exceeding a limit terminates FFmpeg, removes temporary and
  partial files, and returns `RESOURCE_LIMIT_EXCEEDED` (exit 13).

The remote-source controls below apply to version 0.2.0's opt-in acquisition and
map to spec §17 (SEC-012..SEC-017). They only take effect once remote sources
are enabled or approved (FR-018); by default no network access occurs.

- **SEC-012 — Remote source network boundary.** Network access happens only to
  acquire a user-specified remote source, and only after remote sources are
  enabled/approved. Only the acquisition component is network-capable;
  inspection, palette generation, and encoding stay network-isolated under
  SEC-010, and a downloaded file is treated as untrusted local media.
- **SEC-013 — URL scheme allowlist.** `https` is permitted; `http` only with an
  explicit unencrypted-transfer warning; `file` and every other scheme are
  rejected as `UNSUPPORTED_URL_SCHEME` (exit 5) and never opened. The allowlist
  is enforced before any connection and re-enforced on every redirect.
- **SEC-014 — Private-network / SSRF protection.** Loopback, private-network,
  link-local/unique-local, and cloud instance-metadata addresses are blocked as
  `PRIVATE_NETWORK_BLOCKED` (exit 8) unless the specific address is explicitly
  approved. The component resolves the host, checks the resolved address against
  the block list, connects to that address, and re-evaluates on every redirect
  to resist DNS rebinding.
- **Scope of the SEC-013/SEC-014 guarantees — direct path vs. yt-dlp adapter.**
  The guarantees above apply in full to the **built-in direct-download path**,
  which performs its own resolution, per-address validation, and connection
  pinning. The optional **yt-dlp adapter** (`--remote-adapter ytdlp`) performs its
  own network access — DNS resolution, connection, and redirect-following — that
  the engine validates only **best-effort**: before launching yt-dlp the engine
  applies the same scheme allowlist, URL DRM-marker check, and an SSRF host
  resolution/validation, so obvious `file://`, bad-scheme, DRM, and internal-host
  cases are refused up front with no acquisition. But the engine **cannot pin**
  yt-dlp's connections. **Residual risk (accepted, per SEC-014):** a TOCTOU / DNS
  rebinding change after the pre-check, or a redirect that yt-dlp follows to a
  private address, is **not guaranteed** to be blocked. The yt-dlp path therefore
  does **not** carry the direct path's connection-pinning guarantee. Prefer the
  direct path for untrusted inputs.
- **SEC-015 — Credential and token redaction.** Any source URL echoed in logs,
  errors, warnings, progress events, or structured results is reduced to scheme,
  host, and path; query strings (including signed-URL tokens) and userinfo are
  stripped. Credentials and signed URLs must be supplied per request and are
  never stored in configuration or manifests.
- **SEC-016 — Download hardening.** Every download enforces a size ceiling
  (`limits.maxDownloadBytes`, default 2 GiB) on bytes actually received, a
  wall-clock timeout (`limits.maxDownloadSeconds`, default 900 s), and a
  free-disk check before starting. Breaches yield `REMOTE_TOO_LARGE` (exit 13),
  `REMOTE_DOWNLOAD_FAILED` (exit 14), or `RESOURCE_LIMIT_EXCEEDED` (exit 13);
  partial downloads are removed and count toward the temporary-disk accounting.
- **SEC-017 — DRM and access-control integrity.** The engine never bypasses,
  disables, or circumvents DRM, encryption, authentication, or platform access
  controls. A DRM-protected or access-controlled source is rejected as
  `DRM_PROTECTED` (exit 5); the optional yt-dlp adapter is never used to
  circumvent access controls.

### Privacy

Conversion runs locally and uploads nothing — not source videos, GIFs, frames,
metadata, or filenames (spec §18). Version 0.2.0's remote source acquisition is
disabled by default, opt-in, and **download-only**: it only fetches a
user-specified source and never uploads. Telemetry is disabled by default; any
future telemetry requires explicit opt-in and a separate privacy review. See
[`PRIVACY.md`](PRIVACY.md).
