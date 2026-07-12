# Security Policy

This document satisfies spec **NFR-007**: it defines supported versions, a
vulnerability-reporting channel, a disclosure policy, and a summary of the
security model. It applies to the `video-to-gif` Agent Skill and its Claude Code
and Codex packages.

## Supported versions

The project is pre-release. Security fixes are applied to the most recent
`0.1.x` line only; there are no older supported releases yet.

| Version   | Status                      | Security fixes |
| --------- | --------------------------- | -------------- |
| 0.1.x     | Pre-release (in development) | Yes            |
| < 0.1.0   | None released               | N/A            |

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

Version 0.1.0 is built to handle **untrusted media** and **hostile manifests**
safely, and to make **no network access**. The controls below map to the
normative requirements in spec §17 (SEC-001..SEC-011). See
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
- **SEC-005 — No network access.** Version 0.1.0 performs no network access; a
  URL source yields `UNSUPPORTED_REMOTE_SOURCE` rather than being fetched.
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

### Privacy

Version 0.1.0 processes videos locally and uploads nothing — not source videos,
GIFs, frames, metadata, or filenames (spec §18). Telemetry is disabled by
default; any future telemetry requires explicit opt-in and a separate privacy
review.
