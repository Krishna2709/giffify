Video-to-GIF Agent Skill

Versioned Technical Specification

Field	Value
Document ID	VTG-TS-001
Specification version	0.3.0-draft.1
Product version	0.3.0
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

Beginning with version 0.3.0, a selected range MAY additionally be transformed while it is converted. Version 0.3.0 supports cropping, explicit resizing, playback-speed adjustment, and explicit dithering control, and it can extract a single still preview frame instead of a GIF. Every transformation parameter is a number or a member of a fixed enumeration; free text is never accepted. Transformations are specified normatively in FR-024 through FR-030 and SEC-018.

Text captions and subtitle burn-in are not part of version 0.3.0 and are specified for a later release (section 3.3 and section 25.4).

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

3.3 Non-goals for version 0.3.0

Version 0.3.0 will not include:

* Automatic highlight or interesting-moment detection.
* Multimodal video understanding.
* Transcript-based clip selection.
* Authenticated cloud-storage or provider-account integrations, including Google Drive private files, private S3, GCS, or Azure objects, and authenticated Dropbox files.
* Captions, text overlays, or subtitle rendering, including subtitle burn-in and embedded subtitle streams.
* Non-uniform resizing that changes the aspect ratio of the selected region.
* Geometric transformations other than cropping and uniform scaling, including rotation, flipping, and perspective correction.
* Arbitrary user-supplied FFmpeg filter strings, filter-graph fragments, filter scripts, or expressions.
* Transparent-background or alpha-channel handling.
* Exact target-file-size optimization.
* A hosted conversion service.
* An MCP server.
* DRM bypass or access-control circumvention.
* Uploading source videos, generated GIFs, frames, metadata, or filenames to any remote endpoint. Remote access is download-only.

Version 0.2.0 added opt-in remote source acquisition for direct HTTP and HTTPS media URLs, with an optional yt-dlp adapter for video-page URLs. Remote acquisition is disabled by default and is specified normatively in FR-018 through FR-023 and SEC-012 through SEC-017. Authenticated provider integrations remain planned for a later release.

Version 0.3.0 adds cropping, explicit resizing, playback-speed adjustment, dithering control, preview frames, and per-clip transformation settings, specified normatively in FR-024 through FR-030, SEC-018, sections 9.6, 10.4, 12.9, 12.10, 13.4, 15.2, 15.5, 22.7, and the version 0.3.0 acceptance criteria in section 23.

Captions and subtitle burn-in are deferred to version 0.4.0 (section 25.4) because they raise three problems that no version 0.3.0 transformation raises:

1. Font resolution. Rendering text requires locating a usable font file on macOS, Windows, and Linux, handling missing fonts, font-family fallback, and the licensing of any bundled font. Version 0.3.0 introduces no font dependency.
2. Escaping and injection surface. Caption text is free user text that must reach an FFmpeg filter argument, which requires a dedicated escaping design and a security review beyond SEC-018. Every version 0.3.0 transformation parameter is numeric or a fixed enum and is re-serialized by the engine from validated values, so no free text ever reaches a filter graph.
3. Subtitle formats. Burn-in requires parsing external subtitle files (SRT, ASS or SSA, WebVTT) and selecting embedded subtitle streams. Each format is an additional untrusted-input parser with its own styling model, and ASS in particular carries its own scripting and rendering surface.

Deferral keeps the version 0.3.0 filter graph free of user-supplied text.

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
│           │       ├── timestamps.py
│           │       └── transforms.py
│           ├── references/
│           │   ├── configuration.md
│           │   ├── input-formats.md
│           │   ├── quality-profiles.md
│           │   ├── transformations.md
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
* The crop filter exists.
* The setpts filter exists.
* GIF encoding is available.
* PNG encoding is available, because preview frames depend on it (FR-029).
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
description: Convert explicit timestamp ranges from local video files into one or more optimized animated GIFs, optionally cropped, resized, speed-adjusted, or dithered. Use when a user asks to create a GIF from a video, extract timestamped clips as GIFs, batch-generate GIFs from CSV or JSON timestamp manifests, crop or resize or speed up a clip, preview a still frame before making a GIF, or convert a remote video URL when remote sources are enabled.
license: LICENSE
compatibility: Requires Python 3.10+, ffmpeg, and ffprobe. Supports macOS, Windows, and Linux. Version 0.3.0 processes local video files by default, can optionally acquire remote HTTP or HTTPS source URLs when remote sources are explicitly enabled, and supports cropping, explicit resizing, playback-speed adjustment, dithering control, and PNG preview frames. Captions and subtitle burn-in are not supported.
metadata:
  product-version: "0.3.0"
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

From version 0.3.0, these rules are evaluated against the effective source geometry rather than the raw frame: when a crop is applied, the cropped rectangle supplies the aspect ratio and the dimensions against which upscaling is judged (FR-025), an explicit width or height overrides the profile maximum width while leaving the no-upscale rule in force (FR-026), and for a speed multiplier below 1.0 the source frame rate used by the last rule is the source frame rate multiplied by that speed (FR-027). The meaning of this requirement is otherwise unchanged.

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

FR-024: Transformation model

Version 0.3.0 defines four clip transformations — cropping (FR-025), explicit resizing (FR-026), playback-speed adjustment (FR-027), and dithering control (FR-028) — and one additional output mode, preview frame extraction (FR-029).

Every transformation parameter MUST be an integer, a bounded decimal, or a member of a fixed enumeration. The engine MUST NOT accept free text, FFmpeg filter strings, filter-graph fragments, filter scripts, expressions, or option key-value pairs as transformation input from any source (SEC-018).

Transformation values are resolvable from the command line, from a manifest, and from project configuration. For a given clip, the effective value of each transformation parameter MUST be resolved in this order, highest priority first:

1. The clip-level manifest field.
2. The command-line flag.
3. The top-level manifest field.
4. Project configuration.
5. The built-in default.

This refines section 9.3 for transformations only: a clip-level manifest field is more specific than a batch-wide command-line flag and MUST win, consistent with the clip-level override rule of section 10.3. All other configuration continues to follow section 9.3 unchanged.

Every transformation parameter MUST be parsed, validated, and range-checked during preflight (section 15.1), before any FFmpeg process is started. An invalid transformation MUST produce the specific error code defined in FR-025 through FR-028 with exit code 6 and status validation_failed, and MUST reject the job in the same way an invalid timestamp does under the default policy of FR-007.

The --invalid-timestamp-policy values skip and clamp apply to timestamps only. The engine MUST NOT clamp, round, re-center, or otherwise silently correct an invalid transformation value.

When no transformation is specified, the engine MUST produce output that is functionally equivalent to version 0.2.0 output for the same source, range, profile, and configuration (NFR-002, NFR-006).

The effective transformations for every clip MUST be reported in the structured result (FR-030).

FR-025: Cropping

A crop rectangle selects a sub-rectangle of the source frame. It MUST be expressed as four integers in orientation-normalized source pixels: x, y, width, and height, where x and y are the offsets of the rectangle's top-left corner from the top-left corner of the orientation-normalized frame.

Accepted forms:

* Command line: --crop <x>:<y>:<width>:<height>, four unsigned decimal integers separated by colons, with no spaces, signs, or exponent notation.
* JSON manifest: either an object with exactly the keys x, y, width, and height, each an integer, or the same colon-separated string form.
* CSV manifest: the colon-separated string form in a crop column.

Validation MUST reject the following with error code INVALID_CROP and exit code 6:

* A value that is not an unsigned decimal integer, or a string form that does not contain exactly four colon-separated fields.
* An object form missing any of the four keys, or containing any other key.
* Any value outside the range 0 through 65535.
* width < 2 or height < 2.
* x + width > effective source width, or y + height > effective source height.

A crop rectangle supplied in project configuration is a separate case: because it is a schema violation rather than an invalid rectangle, it MUST be rejected as a configuration validation error with error code INVALID_CONFIG, exit code 2, and the field path transformations.crop (section 9.6), consistent with every other prohibited configuration field.

The rectangle MUST be validated against the orientation-normalized source dimensions reported by inspection (FR-002), not the raw coded dimensions, so that a rotated source is cropped in the geometry the user sees.

The engine MUST apply the requested rectangle exactly. It MUST NOT round, clamp, re-center, or expand the rectangle. When the decoded pixel format cannot represent the requested offsets exactly, the engine MUST convert the frame to a non-subsampled pixel format before cropping rather than adjusting the rectangle.

Cropping MUST occur before scaling (section 15.2). After cropping, the cropped rectangle is the effective source geometry for every later step:

* Aspect-ratio preservation under FR-014 applies to the cropped rectangle, not to the original frame. Cropping is the only supported way to change the output aspect ratio.
* Profile maximum widths under FR-014 apply to the cropped width. When the cropped width is already at or below the effective maximum width, the cropped width is retained and no upscaling occurs.
* The no-upscale rule of FR-014 is evaluated against the cropped dimensions (FR-026).

FR-026: Explicit resizing

The engine MUST support two output dimension bounds:

* width: the maximum output width in pixels. This flag and field existed in version 0.1.0 and its meaning is unchanged.
* height: the maximum output height in pixels. New in version 0.3.0.

Both MUST be integers in the closed range 2 through 8192. Any other value MUST be rejected with error code INVALID_DIMENSIONS and exit code 6.

Resolution rules:

* An explicit width or height MUST override the maximum width of the effective quality profile (FR-014). A profile maximum is a default bound, not a ceiling on explicit requests.
* When only width is supplied, height MUST be derived from the effective source aspect ratio.
* When only height is supplied, width MUST be derived from the effective source aspect ratio.
* When both are supplied, the frame MUST be scaled to the largest size that satisfies both bounds while preserving the effective source aspect ratio, so that output width <= width and output height <= height. Non-uniform scaling that changes the aspect ratio MUST NOT be performed (section 3.3).
* The effective source aspect ratio is the aspect ratio of the cropped rectangle when a crop is applied (FR-025), otherwise the orientation-normalized source aspect ratio.

Upscaling:

* When the resolved output dimensions would exceed the effective source dimensions and allowUpscale is not set, the engine MUST clamp the output to the effective source dimensions. This preserves the version 0.1.0 behavior of --width.
* The UPSCALE_NOT_ALLOWED warning MUST be emitted only when the clamped bound was explicitly supplied as width or height. A profile maximum width that simply exceeds a small source MUST NOT produce a warning, so profile-only jobs emit exactly the warnings they emitted in version 0.2.0.
* When allowUpscale is set through --allow-upscale or the manifest allowUpscale field, the resolved dimensions MUST be honored up to the 8192 bound.
* Upscaling MUST NOT be inferred from the presence of --width or --height.

Dimension parity:

* An explicitly supplied width or height MUST be honored exactly, including odd values. GIF is a palette-based format without chroma subsampling, so it imposes no even-dimension constraint, and rounding an explicit bound would silently contradict the user's request.
* When width or height is explicitly supplied, a dimension derived from that bound MUST be rounded to an even value.
* When neither width nor height is supplied, dimension derivation is unchanged from version 0.1.0, which rounds to the nearest integer and MAY therefore produce an odd dimension. Profile-only invocations MUST produce byte-comparable output to version 0.2.0; this rule takes precedence over the even-rounding rule above, which applies only to the explicit-bound path.
* No output dimension may be smaller than 2.

The resolved dimensions MUST be deterministic for the same source, crop, profile, and bounds (NFR-002) and MUST be reported (FR-030).

FR-027: Playback-speed adjustment

The speed parameter is a decimal multiplier applied to the playback rate of the selected range. A value of 1.0 leaves timing unchanged, a value greater than 1.0 makes playback faster, and a value less than 1.0 makes playback slower.

speed MUST be a decimal number in the closed range 0.25 through 4.0 with at most three fractional digits. Values outside the range, zero, negative values, non-numeric values, values in exponent notation, and values with more than three fractional digits MUST be rejected with error code INVALID_SPEED and exit code 6.

The engine MUST implement speed adjustment by retiming presentation timestamps, using a setpts expression constructed from the validated numeric value (SEC-018). The engine MUST NOT change the selected source range: the start position and source duration resolved under FR-004 through FR-006 are unaffected by speed.

Duration:

* The output GIF duration MUST be the selected source duration divided by the speed multiplier: outputDurationMs = round(clipDurationMs / speed).
* A 4000 ms range at speed 2.0 therefore produces an approximately 2000 ms GIF, and the same range at speed 0.5 produces an approximately 8000 ms GIF.
* Both the source range duration and the output duration MUST be reported (FR-030).
* Accuracy expectations are defined in section 15.4.

Frame behavior:

* Speed retiming MUST be applied before frame-rate conversion (section 15.2), so the requested output frame rate describes the finished GIF.
* The engine MUST NOT synthesize interpolated frames. Speeding up drops frames and slowing down duplicates frames.
* For speed values below 1.0, the retimed stream's intrinsic frame rate is the source frame rate multiplied by speed. The effective output frame rate SHOULD NOT exceed that value, consistent with the source-frame-rate rule of FR-014.

Audio is not relevant: GIF output has no audio stream (section 15.4). Speed adjustment MUST NOT attempt audio retiming, and the engine MUST continue to disable audio in every FFmpeg invocation.

FR-028: Dithering control

The palette-use dither mode becomes a public option in version 0.3.0.

The dither field MUST accept exactly one of the following values:

Value	Behavior	Size and quality guidance
none	No dithering	Smallest files; visible banding on gradients
bayer	Ordered dithering with a Bayer matrix	Small files, deterministic pattern; a higher bayerScale gives a coarser pattern, better compression, and more banding
floyd_steinberg	Floyd-Steinberg error diffusion	Good gradients; larger files and more inter-frame noise
sierra2	Sierra-2 error diffusion	Similar to floyd_steinberg with slightly softer noise
sierra2_4a	Sierra-2-4A error diffusion	FFmpeg's default; the general-purpose quality and size balance

The bayerScale field MUST be an integer in the closed range 0 through 5 and is meaningful only when the effective dither mode is bayer.

Validation MUST reject the following with error code INVALID_DITHER and exit code 6:

* Any dither value that is not a member of the enumeration after surrounding whitespace is trimmed. Comparison MUST be case-sensitive against the lowercase names above.
* Any bayerScale value that is not an integer in the range 0 through 5.
* An explicitly supplied bayerScale when the effective dither mode is not bayer.

The error message for an invalid dither value MUST list the permitted values.

Defaults:

* When dither is not supplied at any precedence level, the effective quality profile's default dither mode applies (section 15.5).
* bayerScale is resolved independently through the same precedence chain as dither (FR-024). When the effective mode is bayer and no bayerScale is supplied, the effective profile's default bayerScale applies; when the effective profile default mode is not bayer, bayerScale defaults to 2.

The engine MUST construct the paletteuse dither argument from the validated enum member and the validated integer only, never from the user-supplied text (SEC-018). Compatibility rules for dither values are defined in section 15.5.

FR-029: Preview frames

The engine MUST provide a preview command that extracts a single still image instead of producing a GIF, so a user can confirm framing before committing to a conversion.

Two forms MUST be supported:

* A single timestamp: preview --input <source> --at <timestamp>, with optional transformation flags.
* A manifest: preview --manifest <manifest>, which MUST produce one still per clip at that clip's start timestamp using that clip's effective transformations.

Requirements:

* The output format MUST be PNG in full colour. Preview output MUST NOT be palette-quantized, so preview fidelity is not limited by GIF colour reduction.
* The --at value MUST be a timestamp in an FR-004 format and MUST satisfy 0 <= at < source duration. A value outside that range MUST be rejected with error code INVALID_TIMESTAMP and exit code 6.
* Orientation normalization, cropping (FR-025), and resizing (FR-026) MUST be applied exactly as they would be for a GIF of the same clip with the same settings, including the profile maximum width and the upscale rules.
* The temporal and palette settings speed, fps, loop, colors, dither, and bayerScale do not apply to a still frame. When supplied, they MUST be accepted, MUST NOT change the extracted image, and MUST produce one warning per invocation whose message begins with the token TRANSFORMATION_NOT_APPLICABLE and names the ignored settings. The check MUST consider every source of those settings that applies to the invocation, including command-line flags and top-level and clip-level manifest fields, so a setting supplied only in a manifest is reported rather than silently ignored.
* Output naming: a user-supplied --output-name MUST be a bare filename with no path separators, sanitized under FR-011. Every FR-011 rule applies with .png substituted for .gif, so preview names exclude characters invalid on Windows, prevent directory traversal, avoid reserved Windows device names, remain deterministic, preserve the timestamp suffix when shortening is required, and stay within the safe filename length. When the supplied name has no extension, .png MUST be appended; when it has an extension other than .png, the command MUST fail with error code INVALID_USAGE and exit code 2. A generated name MUST follow <video-stem>_<at>.png, for example product-demo_00-01-02.500.png. In the manifest form, a named clip MUST produce <clip-name>_<start>.png.
* The output directory (FR-010), project-boundary rules (SEC-003), and collision policies (FR-012) apply unchanged, including the default collision policy fail. A preview MUST NOT overwrite an existing file by default.
* Temporary output, verification, and atomic move (section 15.3), cancellation and cleanup (section 16), and resource limits (SEC-011) apply unchanged.
* preview MUST support --dry-run with the preflight semantics of section 12.5: resolve names, detect collisions, and produce no files.
* preview MUST accept a remote source under the same enablement, approval, and cleanup rules as FR-018 through FR-021.
* A preview MUST NOT be counted as a created GIF. Preview results MUST appear in a separate previews array, MUST NOT appear in created, and MUST NOT be included in summary.created (section 13.4).
* A failed preview MUST be reported in failed with its stage and error code, and the summary MUST reflect it as a failure rather than a created output.

FR-030: Transformation and preview reporting

The structured result MUST report the transformations that were actually applied, so the agent can summarize accurately without re-deriving them.

Every entry in created MUST include:

* transformations.crop: the applied rectangle as an object with x, y, width, and height, or null when no crop was applied.
* transformations.sourceWidth and transformations.sourceHeight: the orientation-normalized source dimensions.
* transformations.effectiveSourceWidth and transformations.effectiveSourceHeight: the dimensions after cropping, equal to the source dimensions when no crop was applied.
* transformations.speed: the effective speed multiplier as a number.
* transformations.dither: the effective dither mode.
* transformations.bayerScale: the effective Bayer scale, or null when the effective mode is not bayer.
* transformations.upscaled: true when the output exceeds the effective source dimensions under an explicit allowUpscale, otherwise false.
* outputDurationMs: the duration of the generated GIF, equal to round(durationMs / speed).

The existing width, height, and fps fields continue to report the effective output values, and durationMs continues to report the selected source range duration. Their meanings are unchanged from version 0.1.0.

Every entry in previews MUST include path, atMs, width, height, sizeBytes, and the same transformations object, with speed reported as 1.0 and dither and bayerScale reported as null.

The summary object MUST include a previews count. When a transformation was clamped, adjusted, or ignored, the corresponding warning MUST appear in warnings.

All of these fields are additive. The structured result schemaVersion remains 1 (section 13).

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
  "transformations": {
    "width": null,
    "height": null,
    "speed": 1.0,
    "dither": null,
    "bayerScale": null
  },
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

For transformation parameters only, a clip-level manifest field ranks above a command-line argument, because a per-clip value is more specific than a batch-wide flag (FR-024, section 10.4). All other settings follow the order above unchanged.

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

9.6 Transformation configuration

Project configuration MAY define global transformation defaults in a transformations object:

{
  "transformations": {
    "width": null,
    "height": null,
    "speed": 1.0,
    "dither": null,
    "bayerScale": null
  }
}

* width and height are the dimension bounds of FR-026. A null value means the effective quality profile's maximum width applies.
* speed MUST default to 1.0 and MUST satisfy FR-027.
* dither MUST default to null, meaning the effective profile's default mode (section 15.5), and MUST satisfy FR-028.
* bayerScale MUST default to null and MUST satisfy FR-028.

Configuration MUST NOT define a crop rectangle. A crop rectangle is only meaningful against a specific source's dimensions, so it MUST be supplied per request or per clip (FR-025). A crop key inside transformations MUST be rejected by validate-config as a validation error with the field path transformations.crop.

validate-config MUST apply every source-independent check in FR-026 through FR-028, including enum membership and numeric ranges. Source-dependent checks, namely crop bounds and upscale evaluation, occur during preflight (section 15.1).

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
* height.
* fps.
* colors.
* allowUpscale.
* crop.
* speed.
* dither.
* bayerScale.

Clip level:

* name.
* profile.
* width.
* height.
* fps.
* colors.
* loop.
* crop.
* speed.
* dither.
* bayerScale.

Clip-level values MUST override top-level values.

10.4 Transformation fields

The transformation fields introduced in version 0.3.0 MAY appear at the top level, at the clip level, or both. Clip-level values MUST override top-level values, and a clip-level value MUST also override a command-line transformation flag for that clip (FR-024).

Field	Type	Normative definition
crop	Object with integer keys x, y, width, height, or the string form "x:y:width:height"	FR-025
width	Integer, 2 to 8192	FR-026
height	Integer, 2 to 8192	FR-026
speed	Number, 0.25 to 4.0, at most three fractional digits	FR-027
dither	One of none, bayer, floyd_steinberg, sierra2, sierra2_4a	FR-028
bayerScale	Integer, 0 to 5	FR-028

Example:

{
  "schemaVersion": 1,
  "input": "./videos/demo.mp4",
  "profile": "balanced",
  "width": 800,
  "dither": "sierra2_4a",
  "clips": [
    {
      "name": "opening",
      "start": "00:01:00",
      "end": "00:01:05",
      "crop": { "x": 320, "y": 180, "width": 1280, "height": 720 }
    },
    {
      "name": "reaction",
      "start": "00:03:20",
      "duration": 7,
      "crop": "0:0:1920:800",
      "speed": 2.0,
      "dither": "bayer",
      "bayerScale": 5
    }
  ]
}

An unknown transformation field MUST be handled by the existing unknown-field rule of section 10.2 and section 11.2, which generates a warning.

The manifest schemaVersion remains 1. Every transformation field is optional and additive, no existing field changes meaning, and a manifest that omits them MUST behave exactly as it did in version 0.2.0. A schemaVersion increment would be required only if an existing field's meaning changed or a new field became mandatory, and neither occurs in version 0.3.0.

⸻

11. CSV manifest

11.1 Required columns

start,end,duration

Each row MUST supply:

* start.
* Either end or duration.

11.2 Optional columns

name,profile,width,height,fps,colors,loop,crop,speed,dither,bayerScale

The crop column MUST use the colon-separated string form defined in FR-025. An empty cell means the value is not specified for that row and the next precedence level applies (FR-024).

11.3 Example

name,start,end,duration,profile
opening,00:01:00,00:01:05,,balanced
reaction,00:03:20,,7,high
ending,00:14:30,00:14:35,,small

Example with transformations:

name,start,end,profile,crop,width,speed,dither
opening,00:01:00,00:01:05,balanced,320:180:1280:720,800,1.0,sierra2_4a
reaction,00:03:20,00:03:27,high,,640,2.0,bayer

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
* --allow-insecure-http: permit an http URL with an unencrypted-transfer warning (SEC-013).
* --allow-remote-address <address>: approve one otherwise-blocked private-network or loopback address for this invocation (SEC-014).

For inspect on a URL, the engine MUST acquire the source under FR-020 before running ffprobe, because inspection is network-isolated under SEC-010.

These additions are backward compatible. Existing local invocations MUST behave exactly as in version 0.1.0 (NFR-006).

12.9 Preview frames

Single-frame form:

python scripts/video_to_gif.py preview \
  --input "./videos/demo.mp4" \
  --at "00:01:02.500" \
  --crop 320:180:1280:720 \
  --width 640 \
  --json

Manifest form, producing one still per clip at that clip's start timestamp:

python scripts/video_to_gif.py preview \
  --manifest "./clips.json" \
  --json

Explicit output naming:

python scripts/video_to_gif.py preview \
  --input "./videos/demo.mp4" \
  --at "00:01:02.500" \
  --output-name "framing-check.png" \
  --json

The preview command MUST accept --input or --manifest, --at, --output-name, --output-directory, --config, --collision-policy, --allow-outside-project, --allow-upscale, --dry-run, --json, every transformation flag in section 12.10, and the remote source flags of section 12.8.

--at and --manifest are mutually exclusive: --at selects a single frame from a single source, while a manifest supplies its own per-clip start timestamps. Supplying both MUST fail with error code INVALID_USAGE and exit code 2.

It MUST also accept --profile, --fps, --colors, and --loop so that a preview can be requested with the same settings as the GIF it previews. The settings that do not apply to a still frame are ignored with a warning under FR-029 rather than rejected.

The behavior of preview is defined normatively in FR-029, and its result shape in section 13.4.

12.10 Transformation flags

The create, batch, and preview commands MUST accept the following additive flags:

Flag	Value format	Normative definition
--crop <x>:<y>:<w>:<h>	Four unsigned decimal integers separated by colons	FR-025
--width <pixels>	Integer, 2 to 8192	FR-026
--height <pixels>	Integer, 2 to 8192	FR-026
--speed <multiplier>	Decimal, 0.25 to 4.0, at most three fractional digits	FR-027
--dither <mode>	One of none, bayer, floyd_steinberg, sierra2, sierra2_4a	FR-028
--bayer-scale <n>	Integer, 0 to 5	FR-028

--width already existed in version 0.1.0 as the maximum output width and its meaning is unchanged; --height is its vertical counterpart. --allow-upscale continues to govern whether resolved dimensions may exceed the effective source dimensions (FR-026).

For batch, a transformation flag applies to every clip that does not define that field at the clip level; a clip-level manifest value takes precedence over the flag (FR-024).

An invalid flag value MUST be rejected during preflight with the error code defined in FR-025 through FR-028 and exit code 6, before any FFmpeg process is started.

These additions are backward compatible. Every version 0.1.0 and version 0.2.0 invocation MUST behave exactly as it did before (NFR-006).

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
      "outputDurationMs": 2500,
      "width": 640,
      "height": 360,
      "fps": 15,
      "sizeBytes": 2814300,
      "transformations": {
        "crop": { "x": 320, "y": 180, "width": 1280, "height": 720 },
        "sourceWidth": 1920,
        "sourceHeight": 1080,
        "effectiveSourceWidth": 1280,
        "effectiveSourceHeight": 720,
        "speed": 2.0,
        "dither": "sierra2_4a",
        "bayerScale": null,
        "upscaled": false
      }
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
    "skipped": 0,
    "previews": 0
  }
}

The outputDurationMs, transformations, previews, and summary.previews fields are additive and do not change the result schemaVersion (FR-030).

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

Preview extraction MUST emit progress using stage "preview":

{"event":"stage_progress","clipIndex":0,"stage":"preview","percent":100.0}

13.4 Preview result

The preview command MUST return the same document structure with a previews array. Preview entries MUST NOT appear in created, and summary.created MUST NOT count them (FR-029).

Example:

{
  "schemaVersion": 1,
  "command": "preview",
  "status": "success",
  "source": {
    "path": "./videos/demo.mp4",
    "durationMs": 902144,
    "width": 1920,
    "height": 1080,
    "videoStreamIndex": 0
  },
  "created": [],
  "previews": [
    {
      "clipIndex": 0,
      "name": "opening",
      "path": "./output/product-demo_00-01-02.500.png",
      "atMs": 62500,
      "width": 640,
      "height": 360,
      "sizeBytes": 214300,
      "transformations": {
        "crop": { "x": 320, "y": 180, "width": 1280, "height": 720 },
        "sourceWidth": 1920,
        "sourceHeight": 1080,
        "effectiveSourceWidth": 1280,
        "effectiveSourceHeight": 720,
        "speed": 1.0,
        "dither": null,
        "bayerScale": null,
        "upscaled": false
      }
    }
  ],
  "failed": [],
  "warnings": [
    "TRANSFORMATION_NOT_APPLICABLE: speed does not apply to a preview frame and was ignored."
  ],
  "summary": {
    "requested": 1,
    "created": 0,
    "failed": 0,
    "skipped": 0,
    "previews": 1
  }
}

The created array MUST be present and empty for the preview command. The previews array MUST be present and empty for the create and batch commands.

Warnings remain plain strings. A warning defined by version 0.3.0 MUST begin with its stable token followed by ": ", so tests and agents can identify it without parsing prose. The tokens defined in this version are UPSCALE_NOT_ALLOWED and TRANSFORMATION_NOT_APPLICABLE.

⸻

14. Exit codes

Code	Meaning
0	Complete success
2	Invalid CLI usage or malformed schema
3	Required dependency missing
4	Input not found or inaccessible
5	Invalid or unsupported media or source type
6	Invalid timestamp or clip definition
7	Output collision
8	Filesystem, project-boundary, or network-policy violation
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

Transformation failures introduce no new exit codes. They reuse existing codes:

* 2: INVALID_USAGE, for a preview --output-name whose extension is not .png (FR-029).
* 6: INVALID_CROP, INVALID_DIMENSIONS, INVALID_SPEED, and INVALID_DITHER, all detected during preflight (FR-024 through FR-028). INVALID_TIMESTAMP continues to cover an out-of-range preview --at value.
* 7: OUTPUT_COLLISION, for a preview output that already exists under the default collision policy.
* 9: FFMPEG_FAILED, for a failure during preview extraction or during a transformed conversion.

Exit code 6 was chosen for every invalid transformation because a transformation is part of the clip definition and is validated in the same preflight pass as timestamps. No new exit code is required.

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
10. Resolve and validate transformations, including crop bounds against the orientation-normalized source dimensions, dimension bounds, upscale evaluation, speed, and dither (FR-024 through FR-028).
11. Resolve output directory.
12. Generate output names.
13. Detect collisions.
14. Estimate processing work.
15. Return preflight errors or proceed.

No FFmpeg process may be started before step 15 completes.

15.2 GIF palette pipeline

For every valid clip:

1. Seek to the requested start position.
2. Decode only the required duration.
3. Normalize display orientation.
4. Apply the validated crop rectangle, when one is supplied (FR-025).
5. Apply playback-speed retiming, when the effective speed is not 1.0 (FR-027).
6. Convert to the target frame rate.
7. Scale while preserving the aspect ratio of the current frame.
8. Generate a representative palette.
9. Encode the GIF using that palette and the effective dither mode (FR-028).
10. Write to a temporary output file.
11. Verify that the output is a non-empty GIF.
12. Atomically move the completed file to its destination.

Steps 4 through 7 define the transformation order and MUST hold:

* Cropping MUST precede scaling, so that scaling, aspect-ratio preservation, the profile maximum width, and the no-upscale rule of FR-014 all apply to the cropped rectangle rather than the original frame.
* Speed retiming MUST precede frame-rate conversion, so that the requested output frame rate describes the finished GIF rather than the pre-retimed stream.
* Frame-rate conversion MUST precede palette generation, unchanged from version 0.1.0.

Steps 4 through 7 MUST be constructed from validated values only and MUST be identical in the palette-generation pass and the encoding pass, so the palette is derived from exactly the frames that are encoded (SEC-018).

Cropping is purely spatial while speed retiming and frame-rate conversion are purely temporal. An implementation MAY therefore reorder step 4 relative to steps 5 and 6 for efficiency, provided the result remains functionally equivalent under NFR-002. Steps 4 and 7 MUST NOT be reordered relative to each other, and steps 5 and 6 MUST NOT be reordered relative to each other.

FFmpeg provides palettegen to generate a representative palette and paletteuse to apply that palette during GIF encoding. (FFmpeg)

Preview extraction (FR-029) uses steps 1 through 4 and 7, skips steps 5, 6, 8, and 9, and encodes a single full-colour PNG frame. Steps 10 through 12 then apply unchanged, except that step 11 verifies a non-empty PNG rather than a non-empty GIF.

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

For a transformed clip, the following additional expectations apply:

* The applied crop rectangle MUST equal the requested rectangle exactly (FR-025).
* Output dimensions MUST be deterministic for the same source, crop, profile, and dimension bounds.
* For a speed-adjusted clip, output duration accuracy SHOULD be within one output frame or 100 milliseconds of clipDurationMs / speed, whichever is greater. The tolerance is evaluated against the speed-adjusted target, not against the source range duration.
* Start-position accuracy is unaffected by speed, because retiming does not change the selected source range (FR-027).
* NFR-002 applies unchanged: the same source, range, profile, configuration, and transformation values MUST produce functionally equivalent output.

15.5 Dithering

Version 0.3.0 makes the palette-use dither mode a public option (FR-028). Profile defaults MUST be:

Profile	Default dither mode	Default bayerScale
small	bayer	5
balanced	sierra2_4a	Not applicable
high	sierra2_4a	Not applicable
custom	sierra2_4a	Not applicable

These defaults reproduce the version 0.1.0 and 0.2.0 behavior, so a job that does not specify a dither value MUST produce functionally equivalent output to earlier versions (NFR-002, NFR-006). The exact values remain subject to the profile benchmarking of open decision 5 in section 26.

Because the dither mode is now public, the version 0.1.0 allowance to change dithering internally is superseded by the following compatibility rules:

* An explicitly requested dither mode and bayerScale MUST be honored exactly and MUST NOT change across patch releases.
* A profile's default dither mode or default bayerScale MAY change in a minor release with a changelog entry, and MUST NOT change in a patch release.
* Adding a value to the dither enumeration is an additive minor-release change. Removing a value or changing the meaning of an existing value is a breaking change under section 24.
* The dither and bayerScale manifest and configuration fields are additive optional fields and do not change schemaVersion 1 (NFR-006).

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
* http MAY be permitted only through the explicit --allow-insecure-http flag, and the engine MUST emit a warning that the transfer is unencrypted. Without the flag, http MUST be rejected as UNSUPPORTED_URL_SCHEME.
* file and all other schemes MUST be rejected with error code UNSUPPORTED_URL_SCHEME and exit code 5, and MUST NOT be fetched or opened.

The scheme allowlist MUST be enforced before any network connection is attempted, and MUST be re-enforced on every redirect target.

SEC-014: Private-network and SSRF protection

The acquisition component MUST guard against server-side request forgery.

Requests to the following MUST be blocked unless the user explicitly approves the specific address through the --allow-remote-address flag, with error code PRIVATE_NETWORK_BLOCKED and exit code 8:

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

SEC-018: Transformation parameter validation

Transformation values become arguments inside an FFmpeg filter graph and MUST therefore be treated as an injection surface, even though SEC-001 already prohibits shell execution. SEC-001 remains in force unchanged: subprocess arguments are always passed as arrays and shell=True is never used.

The engine MUST:

* Accept only integers, bounded decimals, and members of the fixed enumerations defined in FR-025 through FR-028. Free text, filter strings, filter-graph fragments, filter scripts, FFmpeg expressions, and option key-value pairs MUST NOT be accepted as transformation input from the command line, a manifest, or configuration.
* Validate and range-check every transformation parameter before any filter graph is constructed, and construct the filter graph exclusively from values that the engine re-serializes from its own validated numeric and enum types. The user-supplied text MUST NOT be concatenated into a filter graph.
* Reject any value whose text contains a character outside the grammar for its type. For every transformation parameter this excludes at least whitespace, newline, and the characters , ; ' " \ [ ] = % ( ) $ ` and *. The colon is permitted only as the field separator inside a --crop value, which MUST contain exactly three colons and four unsigned integer fields.
* Apply the identical validated filter chain to the palette-generation pass and the encoding pass (section 15.2), so palette generation cannot be driven by a different or unvalidated parameter set.
* Continue to enforce the protocol whitelist of SEC-010 on every FFmpeg and ffprobe invocation, including preview extraction, so no filter may reference a remote resource.
* Never accept a user-supplied filter script file or an inline filter definition through any flag, manifest field, or configuration key (SEC-008, SEC-009).

Numeric bounds are part of the security contract, not only the usability contract: an unbounded dimension, crop offset, or speed value is a resource-exhaustion vector under SEC-011.

Security tests MUST verify that transformation values containing filter-graph metacharacters, for example a crop value of "0:0:100:100,drawtext=text=x" or a dither value of "none[a];[a]movie=/etc/passwd", are rejected during preflight, cause no FFmpeg process to start, and cannot add, remove, or reorder a filter.

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
* Which region of the frame should be kept when the user asks for a crop without giving a rectangle?

The agent SHOULD offer a preview frame (FR-029) when a requested crop or resize is ambiguous, rather than guessing a rectangle and producing a GIF. The agent MUST NOT invent a crop rectangle that the user did not specify or confirm.

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
* The crop rectangle or the output dimensions.
* The playback-speed multiplier.

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
* Transformation documentation, provided as references/transformations.md, covering the crop parameter model and coordinate space, the width and height bounds and their interaction with quality profiles and upscaling, the speed multiplier and its effect on output duration and frame count, the dither enumeration with size and quality guidance, the preview command, and the per-clip manifest fields.
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
* Transformation parameters cannot inject filter-graph syntax (SEC-018).

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

22.7 Transformation tests

Unit tests MUST cover:

* Crop parsing in all three accepted forms: the CLI and CSV string form, the JSON object form, and rejection of a malformed string form.
* Crop rejection for negative, zero, non-integer, out-of-range, and out-of-bounds rectangles, each producing INVALID_CROP and exit code 6.
* Crop bounds evaluated against orientation-normalized dimensions for a rotated source.
* Dimension bound parsing and rejection outside 2 through 8192, producing INVALID_DIMENSIONS.
* Dimension resolution when only width is given, only height is given, and both are given, verifying that the both-given case fits inside the box and preserves the aspect ratio.
* Explicit dimensions overriding a profile maximum width in both directions.
* Upscale gating: without allowUpscale the output is clamped to the effective source dimensions and the UPSCALE_NOT_ALLOWED warning is emitted; with allowUpscale the requested dimensions are honored.
* Dimension parity: an explicitly supplied odd bound is honored exactly, an automatically derived dimension remains even, and the profile-only path produces the same dimensions as version 0.2.0.
* Speed parsing and rejection for 0, negative values, values below 0.25, values above 4.0, non-numeric values, exponent notation, and more than three fractional digits, each producing INVALID_SPEED.
* Speed duration arithmetic: outputDurationMs equals round(durationMs / speed) for representative multipliers.
* Dither enum validation, including rejection of an unknown mode, of a bayerScale outside 0 through 5, and of a bayerScale supplied with a non-bayer mode, each producing INVALID_DITHER.
* Dither and bayerScale default resolution per profile (section 15.5).
* Transformation precedence: clip-level manifest value over CLI flag over top-level manifest value over configuration over built-in default (FR-024).
* Rejection of a crop key in project configuration (section 9.6).
* Preview output naming, extension handling, and the INVALID_USAGE rejection of a non-PNG --output-name.
* Result serialization of the transformations object, outputDurationMs, previews, and summary.previews.

Integration tests MUST verify, against synthetic media:

* A cropped GIF has the expected output dimensions, confirming that crop was applied before scale.
* A crop combined with a profile maximum width produces dimensions derived from the cropped rectangle, not the original frame.
* A speed-adjusted GIF has an output duration within the section 15.4 tolerance of the speed-adjusted target.
* Each dither mode produces a valid GIF, and the same settings produce functionally equivalent output on repeated runs (NFR-002).
* A preview invocation produces exactly one PNG, produces no GIF, and reports created as empty with summary.previews equal to 1.
* A manifest with per-clip crop, width, speed, and dither values produces per-clip output dimensions and durations matching each clip's settings.
* A default invocation with no transformation flags produces output functionally equivalent to version 0.2.0 for the same inputs.

Security tests MUST verify:

* Transformation values containing filter-graph metacharacters are rejected in preflight, start no FFmpeg process, and cannot add, remove, or reorder a filter (SEC-018).
* A preview output cannot escape the output directory and does not overwrite an existing file under the default collision policy.
* The palette-generation and encoding passes receive the identical validated filter chain.

Generative tests for crop, dimension, speed, and dither parsing MUST use fixed seeds and MUST produce structured validation errors, never uncaught exceptions (section 22.5).

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

Version 0.3.0 acceptance criteria

Version 0.3.0 additionally requires the following.

AC-0.3.1: Crop applied

Given a 1920x1080 source and --crop 320:180:1280:720 with a profile whose maximum width is 640, the output GIF is 640x360, confirming that cropping occurred before scaling and that the aspect ratio of the cropped rectangle was preserved.

AC-0.3.2: Crop bounds rejected

A crop rectangle that is negative, zero-sized, non-integer, or extends beyond the orientation-normalized source dimensions is rejected during preflight with INVALID_CROP and exit code 6, and no GIF is produced.

AC-0.3.3: Explicit resize overrides the profile

With profile small, whose maximum width is 480, an explicit --width 800 on a source at least 800 pixels wide produces an 800-pixel-wide GIF.

AC-0.3.4: Upscale gating

For a 640-pixel-wide source, --width 1280 without --allow-upscale produces a 640-pixel-wide GIF and an UPSCALE_NOT_ALLOWED warning; the same request with --allow-upscale produces a 1280-pixel-wide GIF.

AC-0.3.5: Dimension box

With both --width 800 and --height 200 on a 16:9 source, the output fits inside the box, preserves the aspect ratio, and is not distorted.

AC-0.3.6: Speed duration

A four-second range at --speed 2.0 produces a GIF of approximately two seconds, and the same range at --speed 0.5 produces a GIF of approximately eight seconds, each within the section 15.4 tolerance. Both durationMs and outputDurationMs are reported.

AC-0.3.7: Speed bounds

Speed values of 0, a negative number, 0.1, and 5.0 are each rejected with INVALID_SPEED and exit code 6 before any conversion starts.

AC-0.3.8: Dither enumeration

Each of none, bayer, floyd_steinberg, sierra2, and sierra2_4a produces a valid GIF, and an unrecognized dither value is rejected with INVALID_DITHER and exit code 6 with a message listing the permitted values.

AC-0.3.9: Preview frame

A preview invocation writes exactly one PNG to the output directory, produces no GIF, applies the requested crop and resize, and returns a result where created is empty, previews contains one entry, and summary.created is 0.

AC-0.3.10: Per-clip transformations

A manifest whose clips specify different crop, width, speed, and dither values produces one GIF per clip whose reported dimensions, output duration, and dither match that clip's settings, and a clip-level value overrides both the top-level manifest value and the equivalent command-line flag.

AC-0.3.11: No filter injection

Transformation values containing filter-graph metacharacters are rejected during preflight, no FFmpeg process is started, and the filter graph cannot be altered.

AC-0.3.12: Transformation reporting

Every created entry reports the applied crop rectangle, effective source and output dimensions, speed, dither mode, bayerScale, upscaled, and outputDurationMs, and the agent's summary reflects them without re-deriving them.

AC-0.3.13: Backward compatibility

All version 0.1.0 and version 0.2.0 command-line invocations, configuration files, and manifests behave unchanged, and a job with no transformation settings produces output functionally equivalent to version 0.2.0. Configuration, manifest, and structured result schemaVersion remain 1.

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
0.2.0-draft.1	Ratified	Ratified and shipped as product 0.2.0. Adds remote source acquisition: FR-018 through FR-023, SEC-012 through SEC-017, exit code 14, remoteSources and keepRemoteSource configuration, limits.maxDownloadBytes and limits.maxDownloadSeconds, --allow-remote / --keep-remote-source / --remote-adapter flags, download progress events, rights-confirmation interaction (19.6), remote testing (22.6) and version 0.2.0 acceptance criteria (section 23)
0.2.0	Released	Product 0.2.0 released from specification 0.2.0-draft.1
0.3.0-draft.1	Current	Adds transformations without text: FR-024 through FR-030, SEC-018, crop / width / height / speed / dither / bayerScale across CLI, manifests, and configuration, the preview command and PNG preview results, transformation ordering in the palette pipeline (15.2), public dither enumeration and profile defaults (15.5), transformation reporting fields, transformation testing (22.7) and version 0.3.0 acceptance criteria (section 23). Introduces no new exit code and no schemaVersion change. Defers captions and subtitle burn-in to version 0.4.0 and renumbers the roadmap accordingly

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

25.3 Version 0.3.0 — Transformations without text

Version 0.3.0 is specified normatively by FR-024 through FR-030, SEC-018, sections 9.6, 10.4, 12.9, 12.10, 13.4, the transformation ordering in section 15.2, the accuracy rules in section 15.4, the dithering rules in section 15.5, section 22.7, and the version 0.3.0 acceptance criteria in section 23.

Capabilities:

* Cropping in orientation-normalized source pixels, applied before scaling (FR-025).
* Explicit resizing with width and height bounds that override profile maximums, with upscaling still gated by allowUpscale (FR-026).
* Playback-speed adjustment from 0.25x to 4.0x by timestamp retiming (FR-027).
* Custom dithering from a fixed enumeration, with documented profile defaults (FR-028).
* PNG preview frames through the preview command, which never produce a GIF (FR-029).
* Per-clip transformation settings in JSON and CSV manifests, with clip-level override (section 10.4).
* Transformation reporting in the structured result (FR-030).

Every transformation parameter is numeric or a member of a fixed enumeration, so no user-supplied text reaches an FFmpeg filter graph (SEC-018). Text captions and subtitle burn-in are deliberately excluded and move to version 0.4.0 for the reasons recorded in section 3.3.

Configuration, manifest, and structured result schema versions remain 1, and no new exit code is introduced.

25.4 Version 0.4.0 — Captions and subtitle burn-in

Planned capabilities:

* Text captions with position, size, and colour control.
* Subtitle burn-in from external SRT, ASS or SSA, and WebVTT files.
* Burn-in from embedded subtitle streams.
* Cross-platform font resolution with an explicit fallback policy.

Version 0.4.0 requires its own specification pass before implementation, including a dedicated security review of FFmpeg filter-argument escaping for free text, a decision on font discovery and any bundled-font licensing on macOS, Windows, and Linux, and a threat model for each supported subtitle parser. The version 0.3.0 rule that no user-supplied text reaches a filter graph (SEC-018) MUST be replaced by an explicit, reviewed escaping requirement rather than silently relaxed.

25.5 Version 0.5.0 — Size optimization

Planned capabilities:

* Approximate maximum-file-size targets.
* Iterative width, frame-rate, and color optimization.
* Optimization reports.
* User-defined quality floors.

25.6 Version 1.0.0 — Stable public distribution

Includes:

* Stable CLI and schemas.
* Marketplace-ready Claude package.
* Marketplace-ready Codex package.
* Security review.
* Complete CI matrix.
* Signed or checksummed release artifacts.
* Upgrade documentation.
* Public contribution guidelines.

25.7 Version 2.0.0 — AI-assisted clip discovery

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
12. Whether file-size estimation belongs in version 0.1.0 or the size-optimization release, now version 0.5.0.
13. Whether preview frames should support an output format other than PNG, and whether a multi-frame contact sheet is worth a later release. Version 0.3.0 specifies PNG only (FR-029).
14. Whether the transformation defaults of section 9.6 also belong in a global user configuration, which depends on open decision 4.

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