Video-to-GIF Agent Skill

Versioned Technical Specification

Field	Value
Document ID	VTG-TS-001
Specification version	0.1.0-draft.2
Product version	0.1.0
Status	Draft for implementation
Date	July 11, 2026
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

3.3 Non-goals for version 0.1.0

Version 0.1.0 will not include:

* Automatic highlight or interesting-moment detection.
* Multimodal video understanding.
* Transcript-based clip selection.
* YouTube or video-platform downloading.
* Authenticated cloud-storage integrations.
* Captions or subtitle rendering.
* Cropping.
* Playback-speed changes.
* Exact target-file-size optimization.
* A hosted conversion service.
* An MCP server.
* DRM bypass or access-control circumvention.

Remote sources are planned for version 0.2.0.

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
* Explaining warnings and failures.
* Producing the final one-line summary.

Python engine

The Python engine MUST handle:

* Input parsing.
* Path normalization.
* Configuration validation.
* Manifest parsing.
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
description: Convert explicit timestamp ranges from local video files into one or more optimized animated GIFs. Use when a user asks to create a GIF from a video, extract timestamped clips as GIFs, or batch-generate GIFs from CSV or JSON timestamp manifests.
license: LICENSE
compatibility: Requires Python 3.10+, ffmpeg, and ffprobe. Supports macOS, Windows, and Linux. Version 0.1 processes local video files only.
metadata:
  product-version: "0.1.0"
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
  "limits": {
    "maxClipProcessingSeconds": 600,
    "maxTemporaryBytes": 2147483648
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

Malformed configuration MUST produce a validation error with a specific field path.

Unknown fields SHOULD generate warnings rather than being silently accepted.

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
* cancelled.
* dry_run.

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

Detailed error codes MUST also be included in structured JSON.

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

Temporary files MUST use unpredictable names in an appropriate temporary directory.

Temporary files MUST be removed after success or failure unless:

{
  "keepTemporaryFiles": true
}

This debugging option SHOULD be disabled by default.

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

⸻

18. Privacy requirements

Version 0.1.0 MUST process videos locally.

The skill MUST NOT upload:

* Source videos.
* Generated GIFs.
* Video frames.
* Metadata.
* Filenames.

The documentation MUST clearly state that version 0.1.0 performs local processing only.

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

19.2 Questions the agent should ask

The agent SHOULD ask only questions required to proceed.

Typical questions:

* Which video should be used when multiple candidates exist?
* Which quality profile should be saved for first use?
* Should missing FFmpeg dependencies be installed?
* What should happen to existing files?
* Should an invalid batch row be corrected, skipped, or clamped?
* Is writing to the external destination approved?

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
* A SECURITY.md defining supported versions, a vulnerability-reporting channel, a disclosure policy, and a summary of the security model (local-only processing, no network access, subprocess isolation, resource limits).

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

22.5 Fuzz and property tests

The timestamp parser and manifest parsers consume untrusted input and MUST have generative tests:

* Randomized malformed timestamp strings MUST produce structured validation errors, never uncaught exceptions.
* Randomized malformed JSON and CSV manifests MUST produce structured validation errors.
* Generative tests MUST use fixed seeds for reproducibility.

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
0.1.0-draft.2	Current	Adds FFmpeg network isolation (SEC-010), resource limits (SEC-011), exit code 13, --output-name flag, duration and loop syntax rules, marketplace metadata (21.5), fuzz tests (22.5), mandatory CI matrix, named validation tooling
0.1.0-rc.1	Planned	Updated after prototype and architecture review
0.1.0	Planned	Approved specification for first stable implementation

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

Planned capabilities:

* Direct HTTP and HTTPS media URLs.
* Optional yt-dlp integration.
* Temporary local downloads.
* Download progress.
* Source cleanup.
* Credential redaction.
* Explicit network approval.
* Public and signed cloud URLs.

Authenticated provider integrations remain separate from general URL support.

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