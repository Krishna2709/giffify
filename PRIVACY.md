# Privacy Policy — video-to-gif

Effective date: July 12, 2026

This policy covers the `video-to-gif` Agent Skill and its Claude Code and
Codex plugin packages, published from
[github.com/Krishna2709/giffify](https://github.com/Krishna2709/giffify).

## Summary

**video-to-gif collects nothing and sends nothing.** All processing happens
locally on your machine.

## What the plugin processes

- Local video files you explicitly identify, read only to inspect metadata
  (via `ffprobe`) and encode the GIF clips you request (via `ffmpeg`).
- Timestamp ranges, quality settings, and output names you supply directly or
  through a JSON/CSV manifest you provide.
- An optional per-project configuration file (`.video-to-gif.json`) that you
  create, containing only conversion preferences. It must never contain
  credentials, and the engine rejects executable or credential-like fields.

## What the plugin collects or transmits

Nothing.

- **No network access.** Version 0.1.x performs no network requests of any
  kind. URL inputs are rejected rather than fetched, and `ffmpeg`/`ffprobe`
  are invoked with a protocol whitelist restricted to local file access, so
  even a malicious local file cannot trigger a network connection.
- **No uploads.** Source videos, generated GIFs, video frames, metadata, and
  filenames never leave your machine.
- **No telemetry, no analytics, no crash reporting.** There is no usage
  tracking of any kind, and none can be enabled remotely.
- **No accounts.** The plugin has no sign-in, no API keys, and no external
  services.

## Data storage

Generated GIFs are written to the output directory you choose (default
`./output` in your project). Temporary files created during conversion are
stored in your operating system's temporary directory and removed when the
job completes, fails, or is cancelled.

## The agent platform

The plugin runs inside an agent (Claude Code or Codex). Your conversation
with that agent — including file paths and timestamps you type — is handled
under the platform's own privacy policy (Anthropic's or OpenAI's,
respectively). This policy covers only what the video-to-gif engine itself
does, which is strictly local.

## Future versions

Remote source acquisition (downloading a video from a URL you provide) is
planned for version 0.2.0. It will require your explicit approval per
request, and this policy will be updated before any such feature ships. Any
future telemetry would be opt-in only and preceded by a policy update — the
current version has none.

## Content responsibility

video-to-gif is a processing tool. It does not host, distribute, moderate, or
inspect the meaning of any content. You, the user, are solely responsible for:

- ensuring you own the videos you process, have permission to use them, or
  otherwise have a lawful basis to do so;
- complying with copyright law, platform terms of service, and any other
  applicable rules for the source material; and
- how you use, share, or publish the GIFs you create.

The maintainers and contributors provide the software "as is" under the MIT
License and accept no responsibility or liability for the content users
process with it or for any infringement arising from that use. The tool will
not bypass DRM or access controls, and future remote-source features will ask
you to confirm you have the right to use a source before fetching it.

## Changes and contact

Changes to this policy are versioned in this repository's history. Questions
or concerns: open an issue at
[github.com/Krishna2709/giffify/issues](https://github.com/Krishna2709/giffify/issues)
or contact the maintainer, [Krishna2709](https://github.com/Krishna2709).
For security reports, see [SECURITY.md](SECURITY.md).
