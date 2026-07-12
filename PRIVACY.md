# Privacy Policy — video-to-gif

Effective date: July 12, 2026

This policy covers the `video-to-gif` Agent Skill and its Claude Code and
Codex plugin packages, published from
[github.com/Krishna2709/giffify](https://github.com/Krishna2709/giffify).

## Summary

**video-to-gif collects nothing and uploads nothing.** Conversion is local by
default, on your machine. Starting in version 0.2.0 the skill can optionally
**download** a remote source you specify — but only after you explicitly enable
that feature and approve it, the access is strictly download-only, and no source
video, GIF, frame, metadata, or filename is ever sent anywhere.

## What the plugin processes

- Local video files you explicitly identify, read only to inspect metadata
  (via `ffprobe`) and encode the GIF clips you request (via `ffmpeg`).
- Timestamp ranges, quality settings, and output names you supply directly or
  through a JSON/CSV manifest you provide.
- An optional per-project configuration file (`.video-to-gif.json`) that you
  create, containing only conversion preferences. It must never contain
  credentials, and the engine rejects executable or credential-like fields.

## What the plugin collects or transmits

Nothing is collected, and nothing is uploaded.

- **No uploads.** Source videos, generated GIFs, video frames, metadata, and
  filenames never leave your machine. All remote access is download-only.
- **Local by default; download-only when enabled.** Remote source acquisition
  is **disabled by default**. With the default configuration, a URL is rejected
  (`REMOTE_DISABLED`) and no network request is made. When you explicitly enable
  it and approve, the only network access is downloading the specific source you
  named, from the host in that URL — see "Remote sources" below. Conversion
  itself stays local: `ffmpeg`/`ffprobe` are always invoked with a protocol
  whitelist restricted to local file access, so a downloaded file is treated as
  untrusted local media and cannot itself trigger a network connection.
- **No telemetry, no analytics, no crash reporting.** There is no usage
  tracking of any kind, and none can be enabled remotely.
- **No accounts.** The plugin has no sign-in, no API keys, and no external
  services. Authenticated cloud-storage integrations are out of scope; only
  direct and signed URLs you provide are supported.

## Data storage

Generated GIFs are written to the output directory you choose (default
`./output` in your project). Temporary files created during conversion are
stored in your operating system's temporary directory and removed when the
job completes, fails, or is cancelled.

## Remote sources (version 0.2.0)

Version 0.2.0 can optionally acquire a source from a remote `http`/`https` URL.
This changes nothing about the privacy guarantees above except that a source
file may now be **downloaded** to your machine before it is converted. The
guarantees:

- **Disabled by default.** Remote acquisition is off unless you explicitly turn
  it on (`remoteSources` set to `ask` or `enabled`, or a one-time
  `--allow-remote`). With the default configuration nothing is fetched.
- **Only after your approval.** When enabled with `ask` — or when overriding the
  default for a single run — the agent asks before any download.
- **Only the source you specify.** For a direct `http`/`https` URL, network
  access is limited to downloading the exact URL you provide, from the host in
  that URL. Requests to private-network, loopback, and cloud-metadata addresses
  are blocked. Nothing else is contacted.
  - **yt-dlp adapter caveat.** If you opt into the optional `yt-dlp` adapter
    (`--remote-adapter ytdlp`) for a video-page URL, that adapter resolves and
    connects on its own and may contact the **video platform's own hosts and
    CDNs** (not only the host in the URL you typed) in order to fetch the media.
    It is still download-only — nothing about you or your files is uploaded — but
    the set of hosts contacted is determined by yt-dlp and the platform, and the
    engine validates it only best-effort (it cannot pin those connections).
- **Download-only.** The skill only receives data; it never uploads your source,
  GIFs, frames, metadata, or filenames. There is no telemetry.
- **Deleted after use.** A downloaded source is stored in a secure temporary
  directory and deleted when the job finishes (success or failure), unless you
  explicitly ask to keep it (`keepRemoteSource` / `--keep-remote-source`).
- **Credentials and tokens are redacted.** Any URL echoed in logs, warnings,
  progress, or results is reduced to scheme, host, and path — query strings
  (including signed-URL tokens) and embedded credentials are stripped and never
  stored. Provide signed or credentialed URLs per request, never in a saved
  configuration file.
- **Rights confirmation.** Before downloading, the agent asks you to confirm you
  own the video, have permission to use it, or otherwise have a lawful basis to
  make a GIF from it. This confirmation is a conversational check only — it is
  not recorded, stored, or transmitted. The skill does not bypass DRM or access
  controls.

## The agent platform

The plugin runs inside an agent (Claude Code or Codex). Your conversation
with that agent — including file paths and timestamps you type — is handled
under the platform's own privacy policy (Anthropic's or OpenAI's,
respectively). This policy covers only what the video-to-gif engine itself
does, which is strictly local.

## Future versions

Remote source acquisition (downloading a video from a URL you provide) shipped
in version 0.2.0 and is described under "Remote sources" above — it is disabled
by default, opt-in, download-only, and requires your approval. Authenticated
cloud-storage integrations remain out of scope. Any future telemetry would be
opt-in only and preceded by a policy update — the current version has none.

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
