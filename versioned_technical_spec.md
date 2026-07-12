Video-to-GIF Agent Skill

Versioned Technical Specification

Field	Value
Document ID	VTG-TS-001
Specification version	0.2.0-draft.1
Product version	0.2.0
Status	Draft for implementation
Date	July 12, 2026
Target agents	Claude Code and OpenAI Codex
Core runtime	Python and FFmpeg
License	MIT
Canonical skill name	video-to-gif

⸻

1. Purpose

This specification defines a portable Agent Skill that converts explicitly selected portions of a video into one or more animated GIF files.

The first release supports deterministic, timestamp-based conversion. Users must identify the source video and provide either:

* A start time and end time.
* A start time and duration.
* Multiple timestamp ranges.
* A CSV manifest.
* A JSON manifest.

The source video is a local file by default. Beginning with version 0.2.0, the source MAY instead be a remote HTTP or HTTPS URL. Remote source acquisition is disabled by default and is gated by explicit enablement and approval (see FR-018 and section 17).

The implementation will consist of:

1. A shared Agent Skill.
2. A deterministic Python conversion engine.
3. FFmpeg and ffprobe integration.
4. Thin distribution packages for Claude Code and Codex.
5. Documentation, schemas, examples, and automated tests.

Agent Skills use a directory containing SKILL.md and may include scripts, references, and assets. Both Claude Code and Codex support this open format. Claude Code and Codex also distinguish between a reusable skill and a plugin used for distribution. (Claude Platform Docs)

⸻

2. Normative language

The terms MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY define implementation requirements:

* MUST: Required for conformance.
* MUST NOT: Prohibited.
* SHOULD: Recommended unless a documented reason justifies deviation.
* SHOULD NOT: Normally prohibited unless a documented exception exists.
* MAY: Optional.

⸻

3. Product goals

3.1 Primary goals

The product MUST:

1. Generate a GIF from one timestamp range in a local video.
2. Generate multiple GIFs from multiple timestamp ranges.
3. Support start/end and start/duration input.
4. Support JSON and CSV batch manifests.
5. Inspect source media before conversion.
6. Validate timestamps before conversion.
7. Produce optimized GIF output using FFmpeg palette generation.
8. work on macOS, Windows, and Linux.
9. protect existing files from accidental overwrites.
10. continue batch processing after an individual runtime failure.
11. provide progress, cancellation, and temporary-file cleanup.
12. return a concise final summary.
13. save generated GIFs in ./output by default.
14. support project-specific saved preferences.
15. use one shared implementation for Claude Code and Codex.

3.2 Secondary goals

The product SHOULD:

1. Minimize external Python dependencies.
2. Produce machine-readable output for agent orchestration.
3. remain usable directly from a terminal.
4. provide actionable dependency-installation guidance.
5. make filesystem and network operations explicit.
6. support future transformations without breaking the initial interface.

3.3 Non-goals for version 0.2.0

Version 0.2.0 will not include:

* Automatic highlight or interesting-moment detection.
* Multimodal video understanding.
* Transcript-based clip selection.
* Authenticated cloud-storage or provider-account integrations, including Google Drive private files, private S3, GCS, or Azure objects, and authenticated Dropbox files.
* Captions or subtitle rendering.
* Cropping.
* Playback-speed changes.
* Exact target-file-size optimization.
* A hosted conversion service.
* An MCP server.
* DRM bypass or access-control circumvention.
* Uploading source videos, generated GIFs, frames, metadata, or filenames to any remote endpoint. Remote access is download-only.

Version 0.2.0 adds opt-in remote source acquisition for direct HTTP and HTTPS media URLs, with an optional yt-dlp adapter for video-page URLs. Remote acquisition is disabled by default and is specified normatively in FR-018 through FR-023 and SEC-012 through SEC-017. Authenticated provider integrations remain planned for a later release.

⸻

4. Architectural decisions

4.1 Skill versus plugin

The project MUST use both concepts:

* The Agent Skill contains the reusable workflow, instructions, scripts, schemas, and references.
* A Claude Code plugin packages the skill for Claude Code distribution.
* A Codex plugin packages the skill for Codex distribution.

The shared skill is the canonical implementation. Platform packages MUST NOT maintain independent conversion logic.

Claude Code recommends standalone skills for experimentation and plugins for versioned sharing and marketplace distribution. Codex similarly recommends developing the workflow as a skill and packaging it as a plugin when it is ready to distribute. (Claude)

4.2 High-level architecture

User request
    │
    ▼
Claude Code or Codex
    │
    ▼
video-to-gif Agent Skill
    │
    ├── Resolve project configuration
    ├── Ask only required questions
    ├── Obtain dependency approval where necessary
    └── Invoke deterministic engine
            │
            ▼
    Python orchestration engine
            │
            ├── ffprobe inspection
            ├── Timestamp validation
            ├── Output planning
            ├── Collision detection
            └── FFmpeg conversion
                    │
                    ▼
                output/*.gif

4.3 Responsibility boundaries

Agent layer

The Claude Code or Codex agent MUST handle:

* Natural-language interpretation.
* Identifying missing information.
* Asking the user for required preferences.
* Obtaining approval for installation.
* Obtaining approval for overwrite operations.
* Obtaining approval for external output paths.
* Obtaining approval for network access to a remote source.
* Obtaining the user's confirmation of a lawful basis for a remote source.
* Explaining warnings and failures.
* Producing the final one-line summary.

Python engine

The Python engine MUST handle:

* Input parsing.
* Path normalization.
* Configuration validation.
* Manifest parsing.
* Remote source acquisition to a secure temporary directory, when remote sources are enabled.
* Timestamp conversion.
* Media inspection.
* Output planning.
* Collision detection.
* FFmpeg subprocess execution.
* Progress extraction.
* Cancellation.
* Cleanup.
* Structured results.

The Python engine MUST NOT conduct an interactive conversation.

Only the remote source acquisition component performs network access, and only after remote sources are enabled or approved (FR-018). Media inspection and conversion remain network-isolated under SEC-010, and a downloaded source is treated as untrusted local media (SEC-012).

FFmpeg and ffprobe

ffprobe MUST inspect source media and return machine-readable metadata. FFmpeg MUST perform video decoding, scaling, frame-rate conversion, palette generation, and GIF encoding. ffprobe officially supports machine-readable output such as JSON. (FFmpeg)

⸻

5. Repository structure

The canonical repository SHOULD use the following structure:

video-to-gif-agent/
├── src/
│   └── skill/
│       └── video-to-gif/
│           ├── SKILL.md
│           ├── scripts/
│           │   ├── video_to_gif.py
│           │   └── vtg/
│           │       ├── __init__.py
│           │       ├── cli.py
│           │       ├── config.py
│           │       ├── dependencies.py
│           │       ├── errors.py
│           │       ├── ffmpeg.py
│           │       ├── inspect.py
│           │       ├── manifests.py
│           │       ├── models.py
│           │       ├── naming.py
│           │       ├── paths.py
│           │       ├── progress.py
│           │       └── timestamps.py
│           ├── references/
│           │   ├── configuration.md
│           │   ├── input-formats.md
│           │   ├── quality-profiles.md
│           │   ├── installation.md
│           │   └── troubleshooting.md
│           └── assets/
│               ├── config.schema.json
│               └── manifest.schema.json
│
├── packages/
│   ├── claude/
│   │   └── video-to-gif/
│   │       ├── .claude-plugin/
│   │       │   └── plugin.json
│   │       └── skills/
│   │           └── video-to-gif/
│   └── codex/
│       └── video-to-gif/
│           ├── .codex-plugin/
│           │   └── plugin.json
│           └── skills/
│               └── video-to-gif/
│
├── examples/
│   ├── clips.csv
│   ├── clips.json
│   └── video-to-gif.config.json
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── acceptance/
│   └── fixtures/
│
├── tools/
│   ├── build_packages.py
│   ├── generate_test_video.py
│   └── validate_release.py
│
├── docs/
│   ├── architecture.md
│   ├── security.md
│   └── release-process.md
│
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
└── pyproject.toml

The contents under src/skill/video-to-gif MUST be canonical. Platform package directories MUST be generated during the release build rather than edited independently.

The skill structure conforms to the Agent Skills convention of SKILL.md plus optional scripts, references, and assets directories. (Agent Skills)

⸻

6. Platform requirements

6.1 Supported operating systems

Version 0.1.0 MUST support:

* macOS.
* Windows.
* Linux.

Windows support MUST include:

* PowerShell-compatible invocation.
* Paths containing drive letters.
* Backslash and forward-slash path input.
* Paths containing spaces.
* Unicode filenames.

WSL MAY be supported through the Linux path, but native Windows and WSL paths MUST NOT be silently mixed.

6.2 Python

The engine MUST support Python 3.10 or later.

The core runtime SHOULD use only the Python standard library.

Test-only dependencies MAY be added through optional development dependency groups.

6.3 FFmpeg

The user environment MUST provide:

* ffmpeg.
* ffprobe.

The implementation MUST use feature detection rather than depend exclusively on an FFmpeg version number.

The doctor command MUST verify:

* ffmpeg is executable.
* ffprobe is executable.
* The palettegen filter exists.
* The paletteuse filter exists.
* GIF encoding is available.
* The temporary directory is writable.
* The requested output directory is writable, when supplied.

The doctor command MUST also report whether the optional yt-dlp adapter is available and, when present, its version. The absence of yt-dlp MUST NOT be reported as a failure, because the adapter is optional (FR-022).

The project MUST NOT treat pip install ffmpeg as installation of the FFmpeg executable.

6.4 Dependency installation

The skill MUST NOT install system dependencies without explicit user approval.

When a dependency is missing, the skill MUST:

1. State which executable is missing.
2. Explain why it is required.
3. Show the proposed installation command.
4. Ask whether the user wants the command executed.
5. Verify the installation afterward.

Version 0.1.0 MUST NOT bundle FFmpeg binaries. Bundling binaries in a later release requires a separate licensing and security review.

⸻

7. Skill metadata

The canonical SKILL.md MUST begin with valid Agent Skills frontmatter.

Proposed metadata:

---
name: video-to-gif
description: Convert explicit timestamp ranges from local video files into one or more optimized animated GIFs. Use when a user asks to create a GIF from a video, extract timestamped clips as GIFs, batch-generate GIFs from CSV or JSON timestamp manifests, or convert a remote video URL when remote sources are enabled.
license: LICENSE
compatibility: Requires Python 3.10+, ffmpeg, and ffprobe. Supports macOS, Windows, and Linux. Version 0.2 processes local video files by default and can optionally acquire remote HTTP or HTTPS source URLs when remote sources are explicitly enabled.
metadata:
  product-version: "0.2.0"
  specification: "VTG-TS-001"
---

The name MUST match the directory name. The description MUST clearly describe both functionality and triggering conditions. These are required by the Agent Skills specification. (Agent Skills)

The initial release SHOULD avoid allowed-tools because support for that field may vary across clients.

⸻

8. Functional requirements

FR-001: Source identification

The user MUST explicitly identify a source through one of:

* A local video file path.
* A local directory containing the video.
* A filename resolvable relative to the current project directory.

When the user supplies a directory:

* If exactly one probable video file exists, the agent MAY select it.
* If multiple probable video files exist, the agent MUST ask which one to use.
* If no probable video file exists, the agent MUST report that no source was found.

The agent MUST NOT search arbitrary directories outside the project unless the user explicitly names or authorizes them.

FR-002: Source inspection

Before conversion, the engine MUST inspect the source with ffprobe.

At minimum, inspection MUST retrieve:

* Container duration.
* Video-stream duration when available.
* Width.
* Height.
* Average frame rate.
* Codec.
* Stream index.
* Stream disposition.
* Rotation or display-orientation metadata when available.

If container duration and stream duration disagree, the engine SHOULD use the valid video-stream duration and emit a warning.

FR-003: Video-stream selection

When one normal video stream exists, it MUST be selected.

When multiple video streams exist:

1. Prefer a stream marked as default.
2. Exclude obvious thumbnail or attached-picture streams.
3. If multiple plausible streams remain, return an ambiguity result.
4. The agent MUST ask the user which stream to use.

FR-004: Timestamp input

The engine MUST support:

75
75.5
01:15
01:15.500
00:01:15
00:01:15.500

Interpretation:

* A single number represents seconds.
* MM:SS represents minutes and seconds.
* HH:MM:SS represents hours, minutes, and seconds.
* Fractional seconds are supported to millisecond precision.

Internally, timestamps MUST be normalized to integer milliseconds.

FR-005: Clip definitions

A clip MUST be defined using either:

{
  "start": "00:01:00",
  "end": "00:01:05"
}

or:

{
  "start": "00:01:00",
  "duration": 5
}

A clip MUST NOT provide both end and duration unless both resolve to the same end timestamp.

Duration format:

* In JSON manifests, duration MAY be a number, interpreted as seconds with an optional fractional part, or a string in any FR-004 timestamp format.
* In CSV manifests and CLI arguments, duration is parsed the same way: a bare number means seconds; colon-separated forms follow FR-004.
* Durations MUST normalize to integer milliseconds and MUST be strictly positive.

FR-006: Timestamp validation

For every clip:

start >= 0
start < source duration
end > start
end <= source duration
duration > 0

Overlapping clips MUST be allowed.

Duplicate timestamp ranges SHOULD produce separate output files only when they have distinct user-provided names. Otherwise, duplicates SHOULD be reported during preflight.

FR-007: Invalid timestamp handling

Invalid timestamps MUST be detected before conversion starts.

The engine MUST support these policies:

* fail: reject the job.
* skip: skip invalid clips and process valid clips.
* clamp: adjust an end timestamp to source duration.

The default policy MUST be fail.

The skill MUST obtain explicit user approval before using skip or clamp when invalid timestamps were not already addressed in the request.

FR-008: Single conversion

The product MUST create one GIF from a single timestamp range.

Example request:

Create a GIF from demo.mp4 from 01:00 to 01:05.

FR-009: Batch conversion

The product MUST generate multiple GIFs from:

* Repeated natural-language timestamp ranges.
* A JSON manifest.
* A CSV manifest.

There MUST be no hard product limit on the number of clips.

The agent MAY warn about unusually large batches, long clips, projected disk use, or extended execution time. Such warnings MUST NOT become hard limits unless resource safety requires rejection.

FR-010: Output directory

The default output directory MUST be:

./output

The directory MUST be created when it does not exist.

The agent MUST request approval before writing outside the current project unless the user already explicitly requested the external destination.

FR-011: Output naming

When the user provides a valid output name, it MUST be used after filename sanitization.

Otherwise, the generated name MUST follow:

<video-stem>_<start>_to_<end>.gif

Example:

product-demo_00-01-00.000_to_00-01-05.000.gif

Generated names MUST:

* Exclude characters invalid on Windows.
* Prevent directory traversal.
* Preserve the .gif extension.
* Remain deterministic.
* Preserve the timestamp suffix if shortening is required.
* Avoid reserved Windows device names.
* Be limited to a safe filename length.

FR-012: Collision behavior

The engine MUST NOT overwrite an existing file by default.

Supported collision policies:

* fail.
* overwrite.
* unique.
* skip.

The engine default MUST be fail.

The skill-layer policy MAY be called ask. Under ask:

1. The engine performs preflight and reports collisions.
2. The agent asks the user what to do.
3. The engine is rerun with an explicit policy.

For batch jobs, the agent SHOULD ask once for a policy covering all detected collisions.

FR-013: Quality preferences

On first skill use within a project, if neither a saved profile nor a request-specific profile exists, the agent MUST ask the user to select:

1. Balanced.
2. Small file.
3. High quality.
4. Custom.

The selected profile SHOULD be stored in project configuration.

A request-specific profile MUST override saved configuration without modifying the configuration unless the user asks to save it.

FR-014: Quality profiles

Version 0.1.0 defines the following profiles:

Profile	Maximum width	Target FPS	Maximum colors	Purpose
small	480	10	128	Documentation and messaging
balanced	640	15	256	General default
high	960	20	256	Detailed product or UI motion
custom	User-defined	User-defined	User-defined	Advanced control

Profile dimensions are maximum widths, not forced widths.

The engine MUST preserve source aspect ratio.

The engine MUST NOT upscale by default.

When source frame rate is lower than the requested GIF frame rate, the effective frame rate SHOULD not exceed the source frame rate.

FR-015: Looping

The default output MUST loop forever.

Supported loop modes SHOULD include:

* forever.
* once.
* An explicit loop count.

Loop syntax in configuration, manifests, and CSV columns:

* The string forever means infinite looping.
* The string once is equivalent to a count of 1.
* An integer N, where N >= 1, means the animation plays N times in total.
* The value 0 MUST be rejected to avoid ambiguity with GIF loop-extension semantics.

FR-016: Batch runtime failures

A runtime failure for one clip MUST NOT prevent remaining valid clips from being attempted when continueOnError is enabled.

The default MUST be:

{
  "continueOnError": true
}

The final result MUST identify each successful and failed clip.

FR-017: Summary

The agent MUST return a concise summary.

Success example:

Created 10 GIFs in ./output.

Partial-success example:

Created 9 GIFs in ./output; 1 clip failed during encoding.

A detailed result MAY be shown when the user asks for it or when failures require explanation.

FR-018: Remote source enablement

Remote source acquisition MUST be disabled by default.

The remoteSources configuration field MUST accept exactly one of:

* disabled: remote sources are rejected. This is the default.
* ask: the agent MUST obtain explicit user approval before each remote acquisition.
* enabled: remote sources are permitted without a per-request approval prompt.

When a source is a remote URL and the effective remoteSources value is disabled, the engine MUST reject the source with error code REMOTE_DISABLED and exit code 8, status remote_disabled, and MUST NOT perform any network access.

The --allow-remote command-line flag MUST override a disabled or ask configuration for a single invocation. When remoteSources is ask, the agent layer MUST obtain explicit user approval before supplying --allow-remote.

Enablement alone MUST NOT authorize access to private-network addresses (SEC-014) or bypass the URL scheme allowlist (SEC-013).

In version 0.1.0 a remote URL produced an UNSUPPORTED_REMOTE_SOURCE result (SEC-005). In version 0.2.0 the disabled-by-default behavior is reported as REMOTE_DISABLED; the version 0.1.0 result remains the documented behavior of the version 0.1.0 product.

FR-019: Supported remote source types

Version 0.2.0 supports acquiring a source from a direct HTTP or HTTPS media URL that FFmpeg or a plain download can read, including signed cloud-storage URLs, which are treated as direct URLs.

A source URL MAY be supplied wherever a source is specified, including the --input argument and the manifest input field, and is subject to FR-018 enablement.

Video-page URLs, such as video-platform watch pages, are supported only through the optional yt-dlp adapter (FR-022), which requires separate approval and a separately detected dependency and is never bundled.

The following MUST be rejected rather than acquired:

* DRM-protected or otherwise access-controlled sources, with error code DRM_PROTECTED and exit code 5. The engine MUST NOT attempt to bypass DRM, authentication, or access controls.
* URLs whose scheme is not permitted by SEC-013, with error code UNSUPPORTED_URL_SCHEME and exit code 5.

Authenticated provider-account integrations are out of scope for version 0.2.0 (section 3.3).

FR-020: Remote acquisition and cleanup

When a remote source is permitted, the engine MUST:

1. Download the source into a secure temporary directory with an unpredictable name in the operating-system temporary location (section 16).
2. Convert the downloaded file using the existing local conversion pipeline (section 15), which remains network-isolated under SEC-010 and treats the downloaded file as untrusted local media.
3. Delete the downloaded source after the job completes, whether it succeeded or failed.

The engine MUST retain the downloaded source only when the user explicitly requests retention through the keepRemoteSource configuration field or the --keep-remote-source flag. When retained, the engine MUST report the retained file path in the structured result.

Downloaded sources MUST count toward the temporary-disk accounting enforced by SEC-011 and MUST be removed by the cleanup rules in section 16 on success, failure, or cancellation.

When a remote acquisition fails, the affected job status MUST be failed, or partial_success when other clips in a batch succeed, consistent with FR-016.

FR-021: Download limits and hardening

The engine MUST enforce, for every remote acquisition:

* A maximum download size, limits.maxDownloadBytes, with a documented default of 2147483648 bytes (2 GiB). The ceiling MUST be enforced during streaming based on bytes actually received, not solely on a declared Content-Length. Exceeding the ceiling MUST produce error code REMOTE_TOO_LARGE and exit code 13.
* A download wall-clock timeout, limits.maxDownloadSeconds, with a documented default of 900 seconds. Exceeding the timeout MUST abort the download with error code REMOTE_DOWNLOAD_FAILED and exit code 14.
* A free-disk check before the download begins. When available free space is insufficient for the projected download plus existing temporary usage, the engine MUST refuse the download with error code RESOURCE_LIMIT_EXCEEDED and exit code 13.

A Content-Type header or URL file extension MAY be used for an early advisory check but MUST NOT be treated as authoritative. ffprobe inspection under section 15 remains the authoritative gate for whether a downloaded file is usable media.

A network error, an HTTP error status, or a truncated or incomplete download MUST produce error code REMOTE_DOWNLOAD_FAILED and exit code 14. On any download failure or cancellation, partial downloads MUST be removed under section 16.

FR-022: Optional yt-dlp adapter

Video-page URLs MAY be acquired through an optional yt-dlp adapter, selected with --remote-adapter ytdlp.

The adapter MUST:

* Be treated as an optional dependency that is never bundled with the skill or its packages.
* Be detected independently of FFmpeg. When the adapter is requested but yt-dlp is not available, the engine MUST report error code YTDLP_MISSING with exit code 3, status dependency_missing, and MUST NOT attempt acquisition.
* Require the same remote enablement as FR-018 and the same rights confirmation as section 19.6.
* Reject DRM-protected sources under FR-019 without attempting circumvention.

The doctor command MUST report yt-dlp availability and version under section 6.3.

FR-023: Remote acquisition results and progress

A successful remote acquisition MUST NOT change the structure of the final result defined in section 13 beyond additive fields.

The engine MUST emit download progress as progress events on standard error using stage "download", consistent with section 13.3. Each event SHOULD include bytes received and, when a total size is known, a percentage. When the total size is unknown, the percentage MAY be omitted.

Any URL echoed in the structured result, warnings, progress events, or errors MUST be redacted under SEC-015.

⸻

9. Project configuration

9.1 Configuration location

The project configuration file MUST be:

.video-to-gif.json

It MUST be resolved relative to the detected project root.

When no project root is detectable, the current working directory MUST be used.

9.2 Configuration schema

Example:

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
  "limits": {
    "maxClipProcessingSeconds": 600,
    "maxTemporaryBytes": 2147483648,
    "maxDownloadBytes": 2147483648,
    "maxDownloadSeconds": 900
  }
}

9.3 Configuration precedence

Highest priority first:

1. Command-line argument.
2. Request-specific user instruction.
3. Project configuration.
4. Built-in default.

9.4 Configuration restrictions

Configuration MUST NOT contain:

* Cloud credentials.
* Access tokens.
* Signed URLs.
* User passwords.
* Private keys.
* Arbitrary shell commands.
* Executable hook definitions.

This restriction extends to remote source definitions: configuration MUST NOT embed credentials, access tokens, or signed-URL query parameters in any remote source URL. Signed or credentialed URLs MUST be supplied per request, not stored in configuration.

Malformed configuration MUST produce a validation error with a specific field path.

Unknown fields SHOULD generate warnings rather than being silently accepted.

9.5 Remote source configuration

The remoteSources field controls remote acquisition and MUST default to disabled. Permitted values and their behavior are defined in FR-018.

The keepRemoteSource field MUST default to false. When true, a downloaded remote source is retained after the job and its path is reported in the structured result (FR-020).

The limits object MUST support:

* maxDownloadBytes: the maximum size of a single remote download, enforced during streaming. Default 2147483648.
* maxDownloadSeconds: the download wall-clock timeout, in seconds. Default 900.

These fields are additive and do not change the configuration schemaVersion. A configuration that omits them MUST behave as though the documented defaults were supplied.

⸻

10. JSON manifest

10.1 Example

{
  "schemaVersion": 1,
  "input": "./videos/demo.mp4",
  "outputDirectory": "./output",
  "profile": "balanced",
  "loop": "forever",
  "continueOnError": true,
  "clips": [
    {
      "name": "opening",
      "start": "00:01:00",
      "end": "00:01:05"
    },
    {
      "name": "reaction",
      "start": "00:03:20",
      "duration": 7
    }
  ]
}

10.2 Required fields

Top level:

* schemaVersion.
* input.
* clips.

Each clip:

* start.
* Exactly one of end or duration.

10.3 Optional fields

Top level:

* outputDirectory.
* profile.
* loop.
* continueOnError.
* collisionPolicy.
* width.
* fps.
* colors.
* allowUpscale.

Clip level:

* name.
* profile.
* width.
* fps.
* colors.
* loop.

Clip-level values MUST override top-level values.

⸻

11. CSV manifest

11.1 Required columns

start,end,duration

Each row MUST supply:

* start.
* Either end or duration.

11.2 Optional columns

name,profile,width,fps,colors,loop

11.3 Example

name,start,end,duration,profile
opening,00:01:00,00:01:05,,balanced
reaction,00:03:20,,7,high
ending,00:14:30,00:14:35,,small

Empty rows MUST be ignored.

Column names SHOULD be case-insensitive and whitespace-trimmed.

Unknown columns SHOULD generate warnings.

⸻

12. Command-line interface

The main entry point MUST be:

python scripts/video_to_gif.py

All commands MUST support --help.

Skill scripts SHOULD be non-interactive, accept complete arguments, provide useful errors, and expose structured output. This follows official Agent Skills guidance for script-backed skills. (Agent Skills)

12.1 Doctor

python scripts/video_to_gif.py doctor --json

Responsibilities:

* Detect Python version.
* Detect FFmpeg.
* Detect ffprobe.
* Detect required filters and encoder support.
* Test temporary-directory access.
* Report optional yt-dlp availability and version when present.
* Return proposed installation guidance for missing dependencies.

12.2 Inspect

python scripts/video_to_gif.py inspect \
  --input "./videos/demo.mp4" \
  --json

12.3 Create

python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --profile balanced \
  --output-directory "./output" \
  --collision-policy fail \
  --json

Duration form:

python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --duration 5 \
  --profile balanced \
  --json

Explicit output naming:

python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --output-name "opening.gif" \
  --json

--output-name MUST be a bare filename without path separators. It is sanitized under FR-011 and resolved inside the effective output directory.

12.4 Batch

python scripts/video_to_gif.py batch \
  --manifest "./clips.json" \
  --collision-policy fail \
  --json

12.5 Preflight

python scripts/video_to_gif.py batch \
  --manifest "./clips.json" \
  --dry-run \
  --json

Preflight MUST:

* Inspect the source.
* Validate clips.
* Resolve output names.
* Detect collisions.
* Estimate work units.
* Avoid generating GIFs.

12.6 Configuration validation

python scripts/video_to_gif.py validate-config \
  --config "./.video-to-gif.json" \
  --json

12.7 Manifest validation

python scripts/video_to_gif.py validate-manifest \
  --manifest "./clips.json" \
  --json

12.8 Remote sources

The create, batch, and inspect commands MUST accept an http or https URL wherever they accept a source, but only when remote sources are enabled under FR-018. When a URL is supplied and remote sources are disabled, the command MUST fail with error code REMOTE_DISABLED and exit code 8 without performing network access.

Additional flags:

* --allow-remote: enable remote acquisition for a single invocation, overriding a disabled or ask configuration (FR-018).
* --keep-remote-source: retain the downloaded source after the job and report its path (FR-020).
* --remote-adapter ytdlp: acquire a video-page URL through the optional yt-dlp adapter (FR-022).

For inspect on a URL, the engine MUST acquire the source under FR-020 before running ffprobe, because inspection is network-isolated under SEC-010.

These additions are backward compatible. Existing local invocations MUST behave exactly as in version 0.1.0 (NFR-006).

⸻

13. Structured result contract

13.1 Final result

When --json is present, standard output MUST contain one final JSON document.

Example:

{
  "schemaVersion": 1,
  "command": "batch",
  "status": "partial_success",
  "source": {
    "path": "./videos/demo.mp4",
    "durationMs": 902144,
    "width": 1920,
    "height": 1080,
    "videoStreamIndex": 0
  },
  "created": [
    {
      "clipIndex": 0,
      "name": "opening",
      "path": "./output/opening.gif",
      "startMs": 60000,
      "endMs": 65000,
      "durationMs": 5000,
      "width": 640,
      "height": 360,
      "fps": 15,
      "sizeBytes": 2814300
    }
  ],
  "failed": [
    {
      "clipIndex": 1,
      "stage": "encode",
      "code": "FFMPEG_FAILED",
      "message": "FFmpeg exited with a non-zero status."
    }
  ],
  "warnings": [],
  "summary": {
    "requested": 2,
    "created": 1,
    "failed": 1,
    "skipped": 0
  }
}

13.2 Status values

Supported values:

* success.
* partial_success.
* failed.
* validation_failed.
* collision.
* dependency_missing.
* remote_disabled.
* cancelled.
* dry_run.

The remote_disabled status applies when a remote URL is supplied but remote sources are disabled and not overridden (FR-018). Adding this value is additive and does not change the structured result schemaVersion.

13.3 Progress output

Progress MUST NOT corrupt final JSON output.

When progress reporting is enabled:

* Final JSON goes to standard output.
* Progress events go to standard error.
* Progress events SHOULD use JSON Lines.

Example:

{"event":"clip_started","clipIndex":0,"totalClips":10}
{"event":"stage_progress","clipIndex":0,"stage":"palette","percent":42.5}
{"event":"stage_progress","clipIndex":0,"stage":"encode","percent":83.0}
{"event":"clip_completed","clipIndex":0,"path":"./output/opening.gif"}

When a remote source is acquired, download progress MUST use stage "download":

{"event":"stage_progress","stage":"download","bytesReceived":10485760,"totalBytes":52428800,"percent":20.0}

The totalBytes field MAY be null when the source does not declare a size, in which case percent MAY be omitted. Any URL that appears in a progress event MUST be redacted under SEC-015.

⸻

14. Exit codes

Code	Meaning
0	Complete success
2	Invalid CLI usage or malformed schema
3	Required dependency missing
4	Input not found or inaccessible
5	Invalid or unsupported media
6	Invalid timestamp or clip definition
7	Output collision
8	Filesystem permission or project-boundary violation
9	FFmpeg conversion failure
10	Operation cancelled
11	Partial batch success
12	Internal engine error
13	Resource limit exceeded
14	Remote acquisition failure

Detailed error codes MUST also be included in structured JSON.

Remote source failures reuse existing exit codes where the semantics fit and add exit code 14 only where they do not:

* 3: YTDLP_MISSING.
* 5: UNSUPPORTED_URL_SCHEME, DRM_PROTECTED.
* 8: REMOTE_DISABLED, PRIVATE_NETWORK_BLOCKED.
* 13: REMOTE_TOO_LARGE, and RESOURCE_LIMIT_EXCEEDED for a free-disk or temporary-disk breach during download.
* 14: REMOTE_DOWNLOAD_FAILED, covering network errors, HTTP error statuses, truncated downloads, and the download wall-clock timeout.

Internal stack traces MUST NOT be shown by default. A --debug option MAY expose diagnostic details.

⸻

15. Conversion pipeline

15.1 Preflight sequence

For every job:

1. Resolve project root.
2. Load and validate configuration.
3. Resolve source path.
4. Verify source existence and readability.
5. Run ffprobe.
6. Select or resolve the video stream.
7. Parse timestamps.
8. Validate all clips.
9. Resolve effective profiles.
10. Resolve output directory.
11. Generate output names.
12. Detect collisions.
13. Estimate processing work.
14. Return preflight errors or proceed.

15.2 GIF palette pipeline

For every valid clip:

1. Seek to the requested start position.
2. Decode only the required duration.
3. Normalize display orientation.
4. Convert to the target frame rate.
5. Scale while preserving aspect ratio.
6. Generate a representative palette.
7. Encode the GIF using that palette.
8. Write to a temporary output file.
9. Verify that the output is a non-empty GIF.
10. Atomically move the completed file to its destination.

FFmpeg provides palettegen to generate a representative palette and paletteuse to apply that palette during GIF encoding. (FFmpeg)

15.3 Temporary output

FFmpeg MUST initially write to a temporary path.

The destination file MUST become visible only after successful completion.

The temporary file SHOULD reside on the same filesystem as the destination when atomic replacement is required.

15.4 Accuracy requirements

The generated clip SHOULD satisfy:

* Start-position accuracy within one output frame.
* Duration accuracy within one output frame or 100 milliseconds, whichever is greater.
* Aspect-ratio preservation.
* Correct display orientation.
* No audio stream.
* Deterministic output dimensions for the same input and settings.

15.5 Dithering

The balanced and high profiles SHOULD use FFmpeg’s default palette-use dithering unless testing identifies a better deterministic setting.

The small profile MAY use a more compression-oriented dithering mode.

Dithering details MUST remain configurable internally without changing the public manifest schema in patch releases.

⸻

16. Cancellation and cleanup

The engine MUST handle user cancellation.

On cancellation, it MUST:

1. Stop the active FFmpeg subprocess.
2. Allow a short graceful-shutdown period.
3. Force termination if necessary.
4. Remove incomplete GIF files.
5. Remove generated palette files.
6. Remove temporary directories.
7. Preserve previously completed GIF files.
8. return status cancelled.
9. identify how many clips completed before cancellation.

When a remote source was being downloaded, cancellation and failure MUST also remove any incomplete or partial download, unless the user requested retention under FR-020. A retained download MUST be preserved like a completed output.

Temporary files MUST use unpredictable names in an appropriate temporary directory.

Temporary files MUST be removed after success or failure unless:

{
  "keepTemporaryFiles": true
}

This debugging option SHOULD be disabled by default.

The secure temporary directory used for remote downloads is subject to the same unpredictable-naming, cleanup, and temporary-disk accounting rules as other temporary files.

⸻

17. Security requirements

SEC-001: Shell execution

The implementation MUST NOT use shell=True.

Subprocess arguments MUST be passed as argument arrays.

User-controlled data MUST NOT be interpolated into a shell command.

SEC-002: Path normalization

All input and output paths MUST be resolved and normalized.

Generated filenames MUST NOT contain path separators.

Manifest-provided names MUST NOT escape the selected output directory.

SEC-003: Project boundary

The default allowed write boundary MUST be the project root.

Writing outside the project requires:

* An explicit user-requested destination, or
* An explicit approval followed by --allow-outside-project.

The resolved external path MUST be shown before writing.

SEC-004: Overwrite protection

Existing output files MUST NOT be overwritten without explicit approval.

SEC-005: Network access

Version 0.1.0 MUST NOT perform network access.

A URL supplied to version 0.1.0 MUST produce an UNSUPPORTED_REMOTE_SOURCE result rather than being fetched implicitly.

SEC-006: Dependency installation

Installation commands MUST require approval.

The skill MUST NOT run installation commands with elevated privileges unless the user explicitly authorizes the exact command.

SEC-007: Sensitive data

The engine MUST avoid logging:

* Environment-variable values.
* Credentials.
* Home-directory contents unrelated to the operation.
* Private configuration files.
* Complete command environments.

SEC-008: Untrusted media

The engine SHOULD:

* Run FFmpeg without elevated privileges.
* Limit processing to the requested source.
* Avoid loading arbitrary filter scripts from the source directory.
* Avoid invoking executable metadata or sidecar files.
* Clean up malformed partial output.

SEC-009: Manifest safety

Manifest values MUST be treated as data.

Manifests MUST NOT support:

* Shell expressions.
* Environment-variable expansion by default.
* Command substitution.
* Arbitrary Python expressions.
* Dynamic imports.
* Executable hooks.

SEC-010: FFmpeg network isolation

Version 0.1.0 MUST enforce the no-network guarantee at the FFmpeg layer, not only at the argument-validation layer, because FFmpeg can fetch remote resources referenced by local input files.

The engine MUST:

* Invoke ffmpeg and ffprobe with an explicit protocol whitelist restricted to local access, such as -protocol_whitelist file,pipe.
* Reject inputs whose detected container is a reference-following format, including HLS playlists, DASH manifests, and concat scripts.
* Report a rejected container as invalid media (exit code 5) with error code UNSUPPORTED_MEDIA_CONTAINER.
* Apply the same restrictions during inspection, palette generation, and encoding.

A security test MUST verify that a hostile local playlist referencing a network URL does not produce a network connection.

SEC-011: Resource limits

The engine MUST enforce:

* A per-clip wall-clock processing timeout.
* A ceiling on temporary-disk usage per job.

Defaults MUST be documented and MUST be configurable through the limits object in project configuration.

Exceeding a limit MUST:

1. Terminate the active FFmpeg subprocess using the cancellation sequence in section 16.
2. Remove temporary and partial output files.
3. Produce error code RESOURCE_LIMIT_EXCEEDED with exit code 13.

Exit-code precedence for limit breaches: exit code 13 applies when the job produced no successful clips and at least one failure was a resource-limit breach. When other clips in the batch succeeded, exit code 11 (partial batch success) applies and the RESOURCE_LIMIT_EXCEEDED error code is preserved on each affected clip in the structured result.

The engine SHOULD reject sources whose declared dimensions or frame counts are implausibly large before decoding begins.

SEC-012: Remote source network boundary

Network access MUST be performed only to acquire a user-specified remote source, and only after remote sources are enabled or approved under FR-018.

Only the remote source acquisition component MUST be network-capable. Media inspection, palette generation, and encoding MUST remain network-isolated under SEC-010, which stays in force unchanged. A downloaded source MUST be treated as untrusted local media and MUST be inspected with the same protocol whitelist and reference-following container rejection as any other local input.

A remote URL supplied while remoteSources is disabled MUST NOT cause any network access (FR-018).

SEC-013: URL scheme allowlist

Remote source URLs MUST be restricted to an allowlist of schemes.

* https MUST be permitted.
* http MAY be permitted only with an explicit warning that the transfer is unencrypted.
* file and all other schemes MUST be rejected with error code UNSUPPORTED_URL_SCHEME and exit code 5, and MUST NOT be fetched or opened.

The scheme allowlist MUST be enforced before any network connection is attempted, and MUST be re-enforced on every redirect target.

SEC-014: Private-network and SSRF protection

The acquisition component MUST guard against server-side request forgery.

Requests to the following MUST be blocked unless the user explicitly approves the specific address, with error code PRIVATE_NETWORK_BLOCKED and exit code 8:

* Loopback addresses.
* Private-network ranges.
* Link-local and unique-local ranges.
* Cloud instance-metadata endpoints.

To resist DNS rebinding, the acquisition component MUST resolve the hostname, evaluate the resolved address against the block list, and then connect to that same resolved address. The block list MUST be re-evaluated for every redirect target. When an implementation cannot bind the connection to the validated address, it MUST document the accepted residual risk.

SEC-015: Credential and token redaction

The engine MUST NOT log signed-URL query parameters or embedded credentials.

The engine MUST apply a single redaction rule to any source URL echoed anywhere, including logs, errors, warnings, progress events, and structured results: strip the query string and any userinfo component, retaining only scheme, host, and path. Fragments and query strings MUST NOT be reproduced.

Credentials, access tokens, and signed-URL query parameters MUST NOT be stored in project configuration (section 9.4) or in manifests (SEC-009). They MUST be supplied per request.

SEC-016: Download hardening

Every remote download MUST enforce the size ceiling, wall-clock timeout, and free-disk check defined in FR-021.

The size ceiling MUST be enforced on bytes actually received during streaming, independent of any declared Content-Length. Content-Type and URL-extension checks are advisory only; ffprobe inspection remains the authoritative media gate.

Partial downloads MUST be removed on failure or cancellation under section 16, and every download MUST count toward the temporary-disk accounting of SEC-011.

SEC-017: DRM and access-control integrity

The engine MUST NOT bypass, disable, or circumvent DRM, encryption, authentication, or platform access controls.

A source detected as DRM-protected or otherwise access-controlled MUST be rejected with error code DRM_PROTECTED and exit code 5. The optional yt-dlp adapter MUST NOT be used to circumvent access controls.

⸻

18. Privacy requirements

Video conversion MUST be performed locally in every version.

In version 0.2.0, the skill performs network access solely to download a user-specified remote source, and only after remote sources are enabled or approved (SEC-012). All remote access is download-only.

The skill MUST NOT upload:

* Source videos.
* Generated GIFs.
* Video frames.
* Metadata.
* Filenames.

The documentation MUST clearly state that conversion is local by default, that remote source acquisition is opt-in and disabled by default, and that all remote access is download-only. The repository PRIVACY.md MUST be updated in the same release to describe the remote acquisition model, the enablement policy, and the download-only guarantee (NFR-007).

Telemetry MUST be disabled by default.

Any future telemetry feature requires:

* Explicit opt-in.
* Documented fields.
* No media content.
* No full local paths.
* A separate privacy review.

⸻

19. Agent interaction contract

19.1 Required information

Before conversion, the skill MUST have:

* A resolved source.
* At least one valid clip definition.
* A quality profile.
* An output directory.
* An explicit collision policy when collisions exist.
* Remote-access approval and a rights confirmation when the source is remote (section 19.6).

19.2 Questions the agent should ask

The agent SHOULD ask only questions required to proceed.

Typical questions:

* Which video should be used when multiple candidates exist?
* Which quality profile should be saved for first use?
* Should missing FFmpeg dependencies be installed?
* What should happen to existing files?
* Should an invalid batch row be corrected, skipped, or clamped?
* Is writing to the external destination approved?
* Should network access be enabled to download a remote source?
* Does the user have a lawful basis to use the remote video?

19.3 Questions the agent should not repeat

The agent MUST NOT ask for information already supplied through:

* The current user request.
* A manifest.
* Project configuration.
* An earlier answer in the same conversation.

19.4 First-use behavior

When .video-to-gif.json does not exist:

1. Detect whether the request already specifies quality.
2. If not, ask for a profile.
3. Use ./output unless overridden.
4. Explain that preferences can be saved.
5. Save configuration only after the user agrees or clearly asks to remember the project preference.

19.5 Ambiguity behavior

The skill MUST ask when ambiguity could change:

* The selected source.
* The selected video stream.
* Timestamp interpretation.
* Output destination.
* Overwrite behavior.
* Quality profile.

The skill SHOULD make deterministic assumptions for harmless details such as default looping and temporary-file cleanup.

19.6 Remote source approval and rights confirmation

Before acquiring a remote source, the agent MUST:

1. Obtain approval for network access when remoteSources is ask, or when overriding a disabled configuration with --allow-remote.
2. Obtain the user's confirmation that they own the video, have permission to use it, or otherwise have a lawful basis to create a GIF from it.

The rights confirmation MUST be obtained once per source, not once per clip. It is an interaction requirement: the skill MUST NOT record, store, or transmit the confirmation or any related statement.

The agent MUST NOT request or accept instructions to bypass DRM, authentication, or access controls (SEC-017).

⸻

20. Non-functional requirements

NFR-001: Portability

The same Python source MUST execute on supported macOS, Windows, and Linux environments.

NFR-002: Determinism

Given the same:

* FFmpeg build.
* Source media.
* Timestamp range.
* Profile.
* Configuration.

the engine SHOULD generate functionally equivalent output.

Byte-for-byte identity across different FFmpeg builds is not required.

NFR-003: Observability

Every failure MUST include:

* A stable error code.
* A human-readable message.
* The failed processing stage.
* The relevant clip index when applicable.
* A remediation suggestion when one is known.

NFR-004: Performance

The engine SHOULD avoid decoding unrelated portions of long source videos where accurate seeking permits.

Batch jobs SHOULD inspect a source only once when all clips use the same source.

NFR-005: Maintainability

Core modules SHOULD separate:

* Parsing.
* Validation.
* Media inspection.
* Conversion.
* Filesystem operations.
* Progress handling.
* Agent instructions.

NFR-006: Backward compatibility

Patch releases MUST NOT break:

* CLI flag names.
* Configuration schema version 1.
* Manifest schema version 1.
* Structured result schema version 1.

Additive optional fields are allowed in minor releases.

NFR-007: Documentation

The repository MUST provide:

* Installation instructions.
* Usage examples.
* JSON and CSV examples.
* Quality-profile documentation.
* Troubleshooting instructions.
* Security behavior.
* Platform-specific notes.
* Release notes.
* A SECURITY.md defining supported versions, a vulnerability-reporting channel, a disclosure policy, and a summary of the security model (local processing by default, opt-in download-only remote acquisition, subprocess isolation, resource limits).
* A PRIVACY.md stating that conversion is local by default, that remote source acquisition is opt-in, disabled by default, and download-only, and that no telemetry is collected by default.

⸻

21. Packaging requirements

21.1 Canonical skill

The canonical skill MUST remain independently installable as a standard Agent Skill.

21.2 Claude Code package

The Claude package MUST include:

video-to-gif/
├── .claude-plugin/
│   └── plugin.json
└── skills/
    └── video-to-gif/
        ├── SKILL.md
        ├── scripts/
        ├── references/
        └── assets/

Claude Code plugin manifests use .claude-plugin/plugin.json, and plugin skills belong under the plugin root rather than inside the manifest directory. (Claude)

Proposed manifest:

{
  "name": "video-to-gif",
  "description": "Generate optimized animated GIFs from explicit video timestamp ranges.",
  "version": "0.1.0",
  "author": {
    "name": "Krishna2709"
  }
}

21.3 Codex package

The Codex package MUST include:

video-to-gif/
├── .codex-plugin/
│   └── plugin.json
└── skills/
    └── video-to-gif/
        ├── SKILL.md
        ├── scripts/
        ├── references/
        └── assets/

Codex plugin tooling uses .codex-plugin/plugin.json, while the skill itself remains based on the open Agent Skills format. (OpenAI Developers)

21.4 Build integrity

The release build MUST:

1. Copy the canonical skill into each platform package.
2. Verify copied files are identical.
3. Validate SKILL.md with the Agent Skills reference validator (skills-ref validate).
4. Validate both plugin manifests, using claude plugin validate for the Claude package.
5. Run unit and integration tests.
6. Generate checksums.
7. verify that no test media or temporary files are included.
8. Verify that no credentials are present.
9. Verify that the package version matches the changelog.

21.5 Marketplace metadata

The repository MUST provide marketplace metadata for both platforms:

* Claude Code: a marketplace directory containing .claude-plugin/marketplace.json listing the video-to-gif plugin.
* Codex: .agents/plugins/marketplace.json for repository and personal marketplace installation.

Marketplace entries MUST:

* Pin an exact plugin version.
* Pass platform validation (claude plugin validate for Claude packages).
* Reference only released, checksummed artifacts.

Publishing sequence:

1. Publish the repository and self-hosted marketplace files.
2. Submit to the Claude community catalog after a stable release.
3. Submit the Codex plugin through the OpenAI public submission process, which requires a verified developer or business identity.

⸻

22. Testing requirements

22.1 Unit tests

Unit tests MUST cover:

* Every supported timestamp format.
* Fractional timestamps.
* Negative timestamps.
* End-before-start.
* End beyond duration.
* Start at duration.
* Start plus duration.
* JSON parsing.
* CSV parsing.
* Configuration precedence.
* Filename sanitization.
* Reserved Windows filenames.
* Unicode paths.
* Collision policies.
* Project-boundary checks.
* Error serialization.

22.2 Integration tests

Integration tests MUST use synthetic media generated during testing.

Test media SHOULD include:

* Constant-color video.
* Moving shapes.
* Rapid scene changes.
* Portrait video.
* Landscape video.
* Variable-frame-rate video when practical.
* Video without audio.
* Video with audio.
* Video with multiple streams.
* Video with rotation metadata.
* Corrupted media.
* Filenames containing spaces and Unicode.

22.3 Platform matrix

Continuous integration MUST include:

* Current supported Ubuntu.
* Current supported macOS.
* Current supported Windows.
* Python 3.10.
* A newer stable Python version.

22.4 Security tests

Security tests MUST verify:

* Filenames cannot escape the output directory.
* Manifest values cannot execute shell commands.
* Semicolons and shell metacharacters remain literal path characters where legal.
* External writes are rejected without authorization.
* Existing files are preserved by default.
* Temporary files are removed after failure.
* Cancellation removes partial output.
* A hostile local playlist file cannot trigger network access.
* Resource limits terminate runaway conversions and clean up temporary files.
* A signed URL is redacted from all logs, progress events, and structured results.
* A private-network or loopback URL is blocked without explicit approval.

22.5 Fuzz and property tests

The timestamp parser and manifest parsers consume untrusted input and MUST have generative tests:

* Randomized malformed timestamp strings MUST produce structured validation errors, never uncaught exceptions.
* Randomized malformed JSON and CSV manifests MUST produce structured validation errors.
* Generative tests MUST use fixed seeds for reproducibility.

22.6 Remote source tests

Remote source tests MUST NOT depend on the public internet. They MUST use a local HTTP server bound to the loopback interface, with the loopback block of SEC-014 explicitly approved for the test fixture.

Remote source tests MUST verify:

* Disabled by default: a URL supplied with default configuration produces error code REMOTE_DISABLED and exit code 8 and performs no network access.
* Direct download: with remote sources enabled, a direct media URL is downloaded, converted by the local pipeline, and the download is deleted after the job.
* Retention: --keep-remote-source retains the download and reports its path.
* Scheme rejection: a file URL and, when http is disallowed, an http URL produce UNSUPPORTED_URL_SCHEME without any fetch.
* SSRF block: a URL resolving to a loopback or private address produces PRIVATE_NETWORK_BLOCKED unless the address was explicitly approved.
* Redaction: a signed URL's query string and any embedded credentials never appear in logs, progress events, or structured results.
* Size enforcement: a response exceeding maxDownloadBytes produces REMOTE_TOO_LARGE and leaves no partial file.
* Timeout enforcement: a download exceeding maxDownloadSeconds produces REMOTE_DOWNLOAD_FAILED and leaves no partial file.
* Partial-download cleanup: an interrupted or failed download leaves no residual temporary file.
* Conversion isolation: the downloaded file is inspected and converted under the SEC-010 whitelist, and a hostile downloaded playlist cannot trigger network access.
* yt-dlp adapter: guarded by adapter availability; when yt-dlp is absent, requesting the adapter produces YTDLP_MISSING and exit code 3.

Generative tests for URL parsing MUST produce structured validation errors for malformed URLs, never uncaught exceptions.

⸻

23. Acceptance criteria for version 0.1.0

Version 0.1.0 is complete when all of the following pass.

AC-001: Single GIF

Given a valid 15-minute local video and the range 01:00–01:05, the product creates one approximately five-second GIF in ./output.

AC-002: Ten GIFs

Given ten valid timestamp ranges, the product creates ten independently named GIFs.

AC-003: Duration input

Given start=01:00 and duration=5, the output is equivalent to start=01:00 and end=01:05.

AC-004: JSON batch

A valid JSON manifest creates all requested GIFs.

AC-005: CSV batch

A valid CSV manifest creates all requested GIFs.

AC-006: Invalid timestamps

A timestamp beyond source duration is rejected before conversion.

No GIF is produced for the invalid clip without an explicit skip or clamp policy.

AC-007: Collision protection

When an output exists, it remains unchanged under the default policy.

AC-008: Partial batch failure

If one runtime conversion fails, remaining clips are attempted and the result is partial_success.

AC-009: Quality profiles

The small, balanced, and high profiles produce outputs with the documented effective width, frame rate, and color limits.

AC-010: Project configuration

Saved project defaults are used on subsequent invocations and request-specific overrides take precedence.

AC-011: Cross-platform paths

Input and output paths containing spaces and Unicode work on macOS, Windows, and Linux.

AC-012: Cancellation

Cancelling during conversion stops the active process, removes incomplete output, preserves completed output, and returns cancelled.

AC-013: Local-only behavior

Version 0.1.0 performs no network access.

AC-014: No command injection

Malicious-looking filenames and manifest fields cannot cause unintended commands to execute.

AC-015: Agent usability

Both Claude Code and Codex can:

1. Recognize an appropriate video-to-GIF request.
2. Load the skill.
3. Collect missing information.
4. Invoke the script.
5. Interpret the structured result.
6. Return the required summary.

Version 0.2.0 acceptance criteria

Version 0.2.0 additionally requires the following.

AC-0.2.1: Disabled by default

A remote URL supplied with default configuration is rejected with REMOTE_DISABLED and exit code 8, and no network access occurs.

AC-0.2.2: Direct download and cleanup

With remote sources enabled, a direct HTTPS media URL is downloaded to secure temporary storage, converted by the local pipeline into a GIF, and the download is deleted after the job.

AC-0.2.3: Retained source

With --keep-remote-source, the downloaded file is retained and its path is reported in the structured result.

AC-0.2.4: Scheme rejection

A file URL is rejected with UNSUPPORTED_URL_SCHEME and is never fetched or opened.

AC-0.2.5: SSRF protection

A URL resolving to a loopback or private-network address is rejected with PRIVATE_NETWORK_BLOCKED unless the specific address was explicitly approved.

AC-0.2.6: Redaction

A signed URL's query string and any embedded credentials never appear in logs, progress events, or structured results.

AC-0.2.7: Size ceiling

A download exceeding maxDownloadBytes is aborted with REMOTE_TOO_LARGE, and no partial file remains.

AC-0.2.8: Timeout

A download exceeding maxDownloadSeconds is aborted with REMOTE_DOWNLOAD_FAILED, and no partial file remains.

AC-0.2.9: Partial-download cleanup

An interrupted or failed download leaves no residual temporary file.

AC-0.2.10: Optional yt-dlp adapter

When yt-dlp is present and requested, a video-page URL is acquired; when yt-dlp is absent, requesting the adapter yields YTDLP_MISSING with exit code 3. The adapter is never bundled.

AC-0.2.11: DRM rejection

A DRM-protected source is rejected with DRM_PROTECTED and exit code 5, with no circumvention attempted.

AC-0.2.12: Rights confirmation

The agent obtains a lawful-basis confirmation once per source before acquisition, and the skill records nothing.

AC-0.2.13: Conversion isolation

The downloaded file is inspected and converted under the SEC-010 protocol whitelist, and a hostile downloaded playlist cannot trigger network access.

AC-0.2.14: Backward compatibility

All version 0.1.0 command-line invocations, configuration, and local behavior are unchanged.

⸻

24. Versioning policy

Product releases MUST use semantic versioning:

MAJOR.MINOR.PATCH

* MAJOR: Breaking CLI, configuration, manifest, or behavior changes.
* MINOR: Backward-compatible capabilities.
* PATCH: Backward-compatible fixes and documentation improvements.

Schema versions are independent integers:

{
  "schemaVersion": 1
}

A product major-version change does not automatically require a schema-version change.

24.1 Specification version history

Version	Status	Description
0.1.0-draft.1	Superseded	Initial implementation specification
0.1.0-draft.2	Ratified	Ratified and shipped as product 0.1.0. Adds FFmpeg network isolation (SEC-010), resource limits (SEC-011), exit code 13, --output-name flag, duration and loop syntax rules, marketplace metadata (21.5), fuzz tests (22.5), mandatory CI matrix, named validation tooling
0.1.0-rc.1	Not issued	Release-candidate stage folded into the product 0.1.0 release; the specification shipped directly from 0.1.0-draft.2
0.1.0	Released	Product 0.1.0 released from specification 0.1.0-draft.2
0.2.0-draft.1	Current	Adds remote source acquisition: FR-018 through FR-023, SEC-012 through SEC-017, exit code 14, remoteSources and keepRemoteSource configuration, limits.maxDownloadBytes and limits.maxDownloadSeconds, --allow-remote / --keep-remote-source / --remote-adapter flags, download progress events, rights-confirmation interaction (19.6), remote testing (22.6) and version 0.2.0 acceptance criteria (section 23)

⸻

25. Release roadmap

25.1 Version 0.1.0 — Local timestamp conversion

Includes:

* Local sources.
* Single and batch conversion.
* JSON and CSV manifests.
* Inspection and validation.
* Quality profiles.
* Configuration.
* Progress.
* Cancellation.
* Collision protection.
* Cross-platform support.

25.2 Version 0.2.0 — Remote acquisition

Version 0.2.0 is specified normatively by FR-018 through FR-023, SEC-012 through SEC-017, exit code 14, the remoteSources and keepRemoteSource configuration fields, the limits.maxDownloadBytes and limits.maxDownloadSeconds fields, section 19.6, section 22.6, and the version 0.2.0 acceptance criteria in section 23.

Capabilities:

* Direct HTTP and HTTPS media URLs, including public and signed cloud URLs (FR-019).
* Optional, never-bundled yt-dlp adapter for video-page URLs (FR-022).
* Download to secure temporary storage, local conversion, and source cleanup (FR-020).
* Download progress events (FR-023).
* Credential and token redaction (SEC-015).
* Explicit, opt-in network enablement and per-source rights confirmation (FR-018, section 19.6).

Remote acquisition is disabled by default. Authenticated provider integrations remain out of scope and separate from general URL support (section 3.3).

25.3 Version 0.3.0 — Transformations

Planned capabilities:

* Cropping.
* Explicit resizing.
* Speed adjustment.
* Text captions.
* Subtitle burn-in.
* Custom dithering.
* Preview frames.
* Per-clip transformation settings.

25.4 Version 0.4.0 — Size optimization

Planned capabilities:

* Approximate maximum-file-size targets.
* Iterative width, frame-rate, and color optimization.
* Optimization reports.
* User-defined quality floors.

25.5 Version 1.0.0 — Stable public distribution

Includes:

* Stable CLI and schemas.
* Marketplace-ready Claude package.
* Marketplace-ready Codex package.
* Security review.
* Complete CI matrix.
* Signed or checksummed release artifacts.
* Upgrade documentation.
* Public contribution guidelines.

25.6 Version 2.0.0 — AI-assisted clip discovery

Potential capabilities:

* Transcript extraction.
* Scene-change detection.
* Frame sampling.
* Audio-event detection.
* Multimodal ranking.
* User-defined moment criteria.
* Candidate previews.
* Human approval before GIF creation.

AI-assisted selection SHOULD be implemented as a separate skill or optional companion capability rather than embedded into the deterministic conversion core.

⸻

26. Open decisions

Resolved on July 11, 2026:

1. Final repository and plugin name → repository giffify (https://github.com/Krishna2709/giffify); plugin and skill name video-to-gif.
2. Maintainer identity and package metadata → Krishna2709 (https://github.com/Krishna2709); security reports via GitHub private vulnerability reporting.
3. Open-source license → MIT.
11. Marketplace publishing accounts and ownership → GitHub account Krishna2709; self-hosted marketplace files in the repository.

Still open before release candidate status:

4. Whether global user configuration is needed in addition to project configuration.
5. Exact quality-profile values after benchmark testing.
6. FFmpeg installation commands for each supported platform.
7. Whether direct invocation should default to balanced or require --profile.
8. Maximum safe generated filename length.
9. Progress-event stability guarantees.
10. Whether package artifacts should be generated in CI or committed.
12. Whether file-size estimation belongs in version 0.1.0 or 0.4.0.

The remaining decisions do not block release-candidate preparation.

⸻

27. Definition of done

The version 0.1.0 implementation is done when:

* All mandatory functional requirements are implemented.
* All acceptance criteria pass.
* Unit, integration, security, and platform tests pass.
* The canonical skill validates.
* Claude Code can execute the packaged skill.
* Codex can execute the packaged skill.
* The engine performs no network access.
* No system dependency is installed without approval.
* Existing files are protected by default.
* Documentation and examples are complete.
* The changelog contains the 0.1.0 release.
* Release packages are generated from the same canonical skill source.
* A clean checkout can run the documented setup, tests, and packaging workflow.