# Security

This document explains the security requirements SEC-001 through SEC-011 from
spec §17 and the threat model behind them. The specification
([`versioned_technical_spec.md`](../versioned_technical_spec.md)) is normative;
this document is explanatory. For reporting and supported versions, see
[`SECURITY.md`](../SECURITY.md).

## Threat model

The engine processes inputs it did not create and cannot fully trust. Three
categories of hostile input drive the design:

### 1. Untrusted media

A video file is a complex binary format parsed by FFmpeg. A malicious or
malformed file could attempt to trigger parser bugs, cause resource exhaustion,
or reference external resources. Mitigations:

- FFmpeg runs **without elevated privileges** and processes only the requested
  source (SEC-008).
- **Resource limits** bound wall-clock time and temporary-disk usage per clip so
  a crafted file cannot run indefinitely or fill the disk (SEC-011).
- **Partial output is cleaned up** on any failure or cancellation, so a crash
  mid-encode does not leave a corrupt GIF at the destination (SEC-008, §15.3,
  §16).
- The engine does not load arbitrary filter scripts, executable metadata, or
  sidecar files from the source directory (SEC-008).

### 2. Hostile manifests and configuration

Manifests (JSON/CSV) and the project config file (`.video-to-gif.json`) are data
supplied by users and may be attacker-influenced. The core risk is that a value
could be interpreted as **code** or as a **path that escapes** intended
boundaries. Mitigations:

- Manifest and config values are treated **purely as data** — no shell
  expressions, no environment-variable expansion, no command substitution, no
  Python expressions, no dynamic imports, no executable hooks (SEC-009).
- Subprocesses are invoked with **argument arrays**, never `shell=True`, and
  user-controlled data is never interpolated into a shell command (SEC-001).
  Shell metacharacters (`;`, `|`, `` ` ``, `$(...)`) therefore remain literal
  characters, never operators.
- Generated and user-provided output names are **sanitized** and contain no path
  separators; manifest-provided names cannot escape the selected output directory
  (SEC-002).
- Configuration must not contain credentials, tokens, signed URLs, passwords,
  private keys, shell commands, or hook definitions; malformed configuration
  produces a validation error with a specific field path (SEC-009, §9.4).

### 3. Network isolation and reference-following media

FFmpeg can fetch remote resources referenced by a **local** input file — for
example an HLS playlist, a DASH manifest, or a concat script that names a
`http(s)://` URL. Argument validation alone cannot prevent this, so isolation is
enforced at the FFmpeg layer:

- `ffmpeg` and `ffprobe` are invoked with an explicit **local-only protocol
  whitelist** (`-protocol_whitelist file,pipe`) during inspection, palette
  generation, and encoding (SEC-010).
- Reference-following containers (HLS, DASH, concat) are **rejected** as
  `UNSUPPORTED_MEDIA_CONTAINER` with exit code 5 (SEC-010).
- Version 0.1.0 performs **no network access** at all; a URL passed as a source
  returns `UNSUPPORTED_REMOTE_SOURCE` rather than being fetched (SEC-005).

A security test must verify that a hostile local playlist referencing a network
URL produces no network connection (spec §22.4).

## SEC-001..SEC-011 explained

### SEC-001 — No shell execution

The implementation never uses `shell=True`. All subprocess invocations pass
arguments as arrays (`["ffmpeg", "-i", path, ...]`). Because there is no shell,
filenames and manifest values cannot be reinterpreted as commands. This is the
primary defense against command injection.

### SEC-002 — Path normalization

Every input and output path is resolved and normalized before use. Generated
filenames must not contain path separators, and names taken from manifests must
not resolve outside the selected output directory (no `..` traversal, no absolute
override). Filename generation additionally excludes characters invalid on
Windows, avoids reserved device names, preserves the `.gif` extension, and stays
within a safe length (spec FR-011).

### SEC-003 — Project boundary

By default the engine may only write under the **project root**. Writing outside
requires either a destination the user explicitly provided, or explicit approval
combined with `--allow-outside-project`. In either case the resolved absolute
external path is shown before writing, so the user sees exactly where output will
land.

### SEC-004 — Overwrite protection

Existing files are never overwritten without explicit approval. The engine's
default collision policy is `fail`; `overwrite`, `unique`, and `skip` are only
used when explicitly selected (spec FR-012). The skill-layer `ask` policy runs a
preflight, reports collisions, and reruns the engine with an explicit policy the
user chose.

### SEC-005 — Network access

Version 0.1.0 makes no network access. This is both a functional decision (remote
sources are 0.2.0) and a security property: there is no code path that fetches a
URL. A URL source yields `UNSUPPORTED_REMOTE_SOURCE`.

### SEC-006 — Dependency installation

The skill never installs system dependencies (such as FFmpeg) without explicit
user approval. It states what is missing, why it is required, and the exact
proposed command, then asks before running it, and verifies afterward.
Installation commands are never run with elevated privileges unless the user
authorizes the exact command (spec §6.4).

### SEC-007 — Sensitive data

Logging avoids environment-variable values, credentials, unrelated
home-directory contents, private configuration files, and complete command
environments. Diagnostic detail (including stack traces) is suppressed by default
and only exposed under an explicit `--debug` flag (spec §14).

### SEC-008 — Untrusted media

FFmpeg runs unprivileged and limited to the requested source. The engine avoids
loading arbitrary filter scripts or invoking executable metadata/sidecar files,
and it cleans up malformed partial output. Combined with SEC-011 resource limits,
this bounds the blast radius of a hostile media file.

### SEC-009 — Manifest safety

Manifest values are data, never code. None of shell expressions, environment
expansion, command substitution, arbitrary Python expressions, dynamic imports,
or executable hooks are supported. This keeps CSV/JSON manifests safe to share
and process.

### SEC-010 — FFmpeg network isolation

As described in the threat model above, the no-network guarantee is enforced at
the FFmpeg layer via a local-only protocol whitelist and rejection of
reference-following containers — closing the gap that pure argument validation
would leave open.

### SEC-011 — Resource limits

The engine enforces a per-clip wall-clock timeout and a temporary-disk ceiling.
Defaults are 600 seconds and 2 GiB (2,147,483,648 bytes), configurable through
the `limits` object in project configuration:

```json
{
  "limits": {
    "maxClipProcessingSeconds": 600,
    "maxTemporaryBytes": 2147483648
  }
}
```

Exceeding a limit terminates the active FFmpeg subprocess using the cancellation
sequence (spec §16), removes temporary and partial output files, and returns
`RESOURCE_LIMIT_EXCEEDED` with exit code 13. The engine should also reject sources
whose declared dimensions or frame counts are implausibly large before decoding.

## Cancellation and cleanup

On cancellation the engine stops the active FFmpeg subprocess (graceful, then
forced), removes incomplete GIFs, palette files, and temporary directories, but
preserves previously completed GIFs, returning status `cancelled` with a count of
clips completed (spec §16). Temporary files use unpredictable names in an
appropriate temporary directory and are removed after success or failure unless
`keepTemporaryFiles` is set for debugging.

## Privacy

Version 0.1.0 processes videos locally and uploads nothing — no source videos,
GIFs, frames, metadata, or filenames (spec §18). Telemetry is disabled by
default; any future telemetry requires explicit opt-in, documented fields, no
media content, no full local paths, and a separate privacy review.

## Testing the security model

Security tests must verify (spec §22.4): filenames cannot escape the output
directory; manifest values cannot execute shell commands; shell metacharacters
remain literal; external writes are rejected without authorization; existing
files are preserved by default; temporary files are removed after failure;
cancellation removes partial output; a hostile local playlist cannot trigger
network access; and resource limits terminate runaway conversions and clean up.
The timestamp and manifest parsers additionally require seeded fuzz tests that
must always produce structured validation errors rather than uncaught exceptions
(spec §22.5).
