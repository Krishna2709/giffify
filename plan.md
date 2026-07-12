Research conclusion

The right design is:

One shared, portable Agent Skill for the actual workflow, packaged inside two thin plugins—one for Claude Code and one for Codex—for installation and marketplace distribution.

Do not create two separate implementations. Claude Code and Codex both support the open Agent Skills format: a directory containing SKILL.md, optional executable scripts, references, and assets. Both progressively load the skill only when relevant.  

A plugin is the distribution package, while a skill is the actual capability and workflow:

* Skill: teaches the agent how to gather requirements, validate timestamps, run the conversion engine, handle errors, and report results.
* Plugin: makes that skill installable, versioned, discoverable, and publishable.
* MCP server: unnecessary for the first version because conversion happens through local executables. MCP becomes useful only if you later introduce a hosted conversion service, cloud storage connector, or video-analysis API.
* Subagent: unnecessary for deterministic timestamp-based conversion.

Recommended product structure

User request
    ↓
Claude Code or Codex
    ↓
video-to-gif Agent Skill
    ↓
Portable Python orchestration script
    ↓
ffprobe → validate video and timestamps
    ↓
FFmpeg → generate GIF
    ↓
output/ + structured result

The language model handles conversation and ambiguity. The Python script handles deterministic processing. FFmpeg handles video decoding and GIF generation.

⸻

Why a shared skill is feasible

The open Agent Skills standard requires:

video-to-gif/
├── SKILL.md
├── scripts/
├── references/
└── assets/

The required SKILL.md fields are name and description. Scripts and reference files are loaded only when needed. The standard recommends keeping the main skill instructions concise and moving technical details into supporting files.  

The same core skill can therefore work in both products.

For direct local installation:

Platform	Skill location
Claude Code project	.claude/skills/video-to-gif/
Claude Code personal	~/.claude/skills/video-to-gif/
Codex project	.agents/skills/video-to-gif/
Codex personal	~/.agents/skills/video-to-gif/

Claude Code supports project, personal, enterprise, and plugin-provided skills. Codex scans .agents/skills from the working directory up to the repository root and also supports personal and administrator locations.  

For public distribution, create separate packaging manifests:

Claude plugin:
.claude-plugin/plugin.json
Codex plugin:
.codex-plugin/plugin.json

The skill content remains identical; only the plugin manifests and marketplace metadata differ.  

⸻

Recommended repository

video-to-gif-agent/
├── skill/
│   └── video-to-gif/
│       ├── SKILL.md
│       ├── scripts/
│       │   └── video_to_gif.py
│       ├── references/
│       │   ├── input-formats.md
│       │   ├── quality-profiles.md
│       │   ├── remote-sources.md
│       │   └── troubleshooting.md
│       └── assets/
│           ├── config.schema.json
│           └── clips.schema.json
│
├── packages/
│   ├── claude/
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json
│   │   └── skills/
│   │       └── video-to-gif/
│   └── codex/
│       ├── .codex-plugin/
│       │   └── plugin.json
│       └── skills/
│           └── video-to-gif/
│
├── marketplaces/
│   ├── claude/
│   │   └── .claude-plugin/
│   │       └── marketplace.json
│   └── codex/
│       └── .agents/
│           └── plugins/
│               └── marketplace.json
│
├── scripts/
│   └── build_packages.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
├── README.md
├── SECURITY.md
├── CONTRIBUTING.md
├── CHANGELOG.md
└── LICENSE

skill/video-to-gif/ should be the canonical source. The build script copies it into each plugin package. Copying is safer than linking because installed plugins are cached independently and cannot reliably refer to files outside their package. Claude’s plugin documentation explicitly warns that external relative paths are not copied into the plugin cache.  

⸻

MVP requirements

Supported user requests

The first version should handle:

Create a GIF from video.mp4 from 01:00 to 01:05.
Create GIFs from:
00:10–00:15
01:25–01:32
03:45–03:50
Create the GIFs described in clips.csv from meeting.mp4.
Create GIFs using clips.json and save them in ./output.

Users can provide:

* Start and end time.
* Start time and duration.
* Multiple timestamp ranges.
* CSV manifest.
* JSON manifest.
* Optional output names.
* Optional quality overrides.

Automatic highlight detection is explicitly outside the first version.

Timestamp formats

Support:

75
75.5
01:15
01:15.500
00:01:15
00:01:15.500

Normalize everything internally to milliseconds.

For each clip, accept either:

{
  "start": "00:01:00",
  "end": "00:01:05"
}

or:

{
  "start": "00:01:00",
  "duration": 5
}

Do not accept both end and duration for the same clip unless they resolve to the same value.

Proposed JSON batch format

{
  "version": 1,
  "input": "./videos/demo.mp4",
  "outputDirectory": "./output",
  "profile": "balanced",
  "clips": [
    {
      "name": "opening",
      "start": "00:01:00",
      "end": "00:01:05"
    },
    {
      "start": "00:03:20",
      "duration": 7
    }
  ]
}

Proposed CSV format

name,start,end,duration
opening,00:01:00,00:01:05,
reaction,00:03:20,,7
ending,00:14:30,00:14:35,

⸻

User interaction model

Asking every quality question on every conversion would become frustrating. A better model is:

1. On first use, ask for defaults.
2. Save those defaults in a configuration file.
3. Use them silently on later requests.
4. Allow every setting to be overridden in the current request.

The agent should ask only when:

* The input video is missing or ambiguous.
* No timestamps were provided.
* The first-run quality profile is not configured.
* FFmpeg or another dependency is missing.
* Automatic dependency installation requires approval.
* A destination file already exists.
* An output path is outside the current project.
* A timestamp is invalid or beyond the video duration.
* A remote source requires network access, authentication, or downloading.
* The requested operation has meaningful legal or security implications.

Proposed project configuration

{
  "version": 1,
  "outputDirectory": "./output",
  "profile": "balanced",
  "loop": "forever",
  "overwrite": "ask",
  "keepTemporaryFiles": false,
  "continueOnBatchError": true,
  "remoteSourcePolicy": "ask"
}

Use a project-level file such as:

.video-to-gif.json

A global configuration can be added later. Project configuration is easier to audit, version, and use within agent filesystem restrictions.

⸻

Quality profiles

These are proposed product defaults, not FFmpeg standards:

Profile	Width	FPS	Colors	Intended use
Small	480 px	10	128	Chat, documentation, small uploads
Balanced	640 px	15	256	Default general use
High quality	960 px	20	256	Product demos and detailed motion
Custom	User-defined	User-defined	User-defined	Advanced use

The first-run experience could ask:

Choose your default GIF profile:
1. Balanced — recommended
2. Small file
3. High quality
4. Custom

A hard maximum file size should not be promised in the first release. GIF size depends heavily on motion, colors, dimensions, and duration. Exact target-size generation requires repeated encoding attempts that progressively reduce frame rate, resolution, or color count.

⸻

What “two-pass FFmpeg palette generation” means

GIF images can use a maximum palette of 256 colors. A naive conversion often produces poor colors, banding, or unnecessarily large files.

The optimized approach is:

1. Analyze the selected video clip and generate a representative color palette.
2. Encode the GIF using that palette.

FFmpeg provides palettegen for generating a palette and paletteuse for applying it. Its documentation also provides dithering controls and options that can improve GIF compression when only part of the frame changes.  

Conceptually:

ffmpeg -ss 00:01:00 -t 5 -i video.mp4 \
  -vf "fps=15,scale=640:-2:flags=lanczos,palettegen" \
  palette.png

Then:

ffmpeg -ss 00:01:00 -t 5 -i video.mp4 -i palette.png \
  -lavfi "fps=15,scale=640:-2:flags=lanczos[x];[x][1:v]paletteuse" \
  output.gif

The production script should construct argument arrays directly rather than building a shell command string.

⸻

Implementation language

Recommendation: Python plus FFmpeg

Use Python for orchestration because it provides portable handling for:

* Windows, macOS, and Linux paths.
* JSON and CSV input.
* Subprocess control.
* Signals and cancellation.
* Temporary directories.
* Validation.
* Progress parsing.
* Structured errors.
* Unit testing.

Use only the Python standard library for the core MVP.

The actual conversion dependency is the FFmpeg executable, including ffprobe. FFmpeg describes itself as a cross-platform solution for converting and processing audio and video.  

Importantly:

pip install ffmpeg is generally not the correct way to install the FFmpeg executable.

Packages with names such as ffmpeg-python are wrappers. The skill should detect the actual ffmpeg and ffprobe binaries with commands such as:

ffmpeg -version
ffprobe -version

Then give platform-specific installation guidance and ask whether the user wants the agent to execute it.

⸻

Proposed script interface

python scripts/video_to_gif.py doctor

Checks Python, FFmpeg, ffprobe, optional yt-dlp, output permissions, and supported filters.

python scripts/video_to_gif.py inspect \
  --input "./videos/demo.mp4"

Returns duration, dimensions, frame rate, and stream information.

python scripts/video_to_gif.py create \
  --input "./videos/demo.mp4" \
  --start "00:01:00" \
  --end "00:01:05" \
  --profile balanced \
  --output "./output/demo_00-01-00_to_00-01-05.gif"
python scripts/video_to_gif.py batch \
  --manifest "./clips.json"

The script itself should never ask interactive questions. Agent skill guidance recommends non-interactive scripts with flags, clear --help documentation, useful errors, and structured output. The Claude Code or Codex agent asks the user questions and then invokes the script with complete arguments.  

Structured output

Standard output:

{
  "status": "partial_success",
  "created": [
    {
      "file": "./output/demo_00-01-00_to_00-01-05.gif",
      "startMs": 60000,
      "endMs": 65000,
      "sizeBytes": 2814300
    }
  ],
  "failed": [
    {
      "clip": 2,
      "reason": "End timestamp exceeds video duration"
    }
  ]
}

Progress and diagnostic logging should go to standard error.

FFmpeg provides a machine-readable -progress option that outputs key=value updates and marks each update as continue or end. This is suitable for agent-visible progress reporting.  

Final agent response:

Created 9 GIFs in ./output; 1 clip failed because its end time exceeded the 15:02 video duration.

⸻

Video inspection and validation

Every job should begin with ffprobe.

ffprobe can selectively return format and stream fields using -show_entries, which allows the script to retrieve duration, dimensions, codecs, frame rate, and stream information as structured JSON.  

Validate before encoding:

0 ≤ start < video duration
end > start
end ≤ video duration
duration > 0
input has a decodable video stream
output directory is writable
output filename is valid on the current OS

Overlapping clips should be allowed. Overlap is not inherently invalid.

For invalid batch clips:

1. Detect all problems before conversion.
2. Present them together.
3. Ask the user whether to correct, skip, or clamp them.
4. During execution, continue after individual runtime failures.
5. Report all failures in the final summary.

⸻

Output behavior

Default directory:

./output/

Recommended generated filename:

<video-name>_<start>_to_<end>.gif

Example:

product-demo_00-01-00.000_to_00-01-05.000.gif

Sanitize characters that are invalid on Windows and normalize spaces consistently.

Collision behavior

The engine should default to refusing to overwrite.

It returns:

{
  "status": "collision",
  "path": "./output/example.gif"
}

The agent then asks:

example.gif already exists. Overwrite it, create a uniquely numbered file, or skip it?

For a batch, ask once for the entire set of collisions rather than asking once per file.

⸻

Local and remote inputs

Recommended first-version policy

Core processing should remain local:

* Local video paths: fully supported.
* Direct HTTP or HTTPS media URLs: supported when FFmpeg can read them.
* YouTube and video-page URLs: optional yt-dlp integration.
* Signed cloud-storage URLs: treated as direct URLs.
* Authenticated cloud-storage resources: later provider-specific integrations.
* DRM-protected video: explicitly unsupported.
* Uploading video to an AI model or external conversion API: not part of the MVP.

FFmpeg supports network input protocols, while yt-dlp is a separate downloader with structured output templates and broad site-specific extraction behavior.  

“All cloud links” cannot honestly be treated as a single feature. A Google Drive sharing page, an S3 signed URL, an authenticated Dropbox file, and a YouTube watch page all require different acquisition mechanisms.

Use this support model:

Source	MVP handling
Local file	Native
Direct .mp4, .mov, .webm URL	FFmpeg or temporary download
Signed HTTP URL	Temporary download or stream
YouTube/video website	Optional yt-dlp
Google Drive public direct-download link	Optional adapter
Private S3/GCS/Azure object	Existing provider CLI or signed URL
DRM/encrypted stream	Reject

For remote video, download into a secure temporary directory, convert locally, and remove the source afterward. Keep the downloaded source only when the user explicitly requests it.

For large remote videos, the acquisition layer may attempt section-only downloading where supported, but it must retain a full-download fallback because section extraction behavior varies by service and source format.

⸻

Security and privacy implications

Filesystem boundaries

The default should be writing only under the current project.

Writing elsewhere is allowed only when:

* The user explicitly provides the destination.
* The agent displays the resolved absolute destination.
* The platform grants the necessary filesystem permission.

This aligns well with Codex’s default security model, which limits agents to editing the folder or branch where they are working and asks for permission for elevated operations.  

Command injection

The Python engine must:

* Never use shell=True.
* Pass subprocess arguments as arrays.
* Never interpolate filenames into shell scripts.
* Validate filter parameters independently.
* Treat manifest data purely as data.
* Sanitize generated filenames.

URL security

For remote inputs:

* Allow only expected schemes such as HTTPS and optionally HTTP.
* Reject file:// and arbitrary executable protocols.
* Avoid logging signed URL query parameters.
* Redact tokens and credentials from errors.
* Require permission before accessing local or private-network URLs.
* Never store cloud credentials in the project configuration.

Untrusted media

Video files are complex binary inputs. The skill should:

* Recommend a maintained FFmpeg release.
* Run with bounded time and temporary disk usage.
* Remove partial outputs after cancellation.
* Avoid privileged execution.
* Avoid processing unknown remote media automatically without confirmation.

Copyright and platform terms

The remote-source workflow should ask users to confirm that they own the video, have permission, or otherwise have a lawful basis to create the GIF. It should not bypass DRM, access controls, or authentication restrictions.

YouTube’s terms and licensing guidance distinguish between rights retained by creators, platform-enabled reuse, Creative Commons content, and third-party copyrighted material. A few seconds of content is not automatically lawful merely because it is short.  

⸻

Cancellation and cleanup

The Python process should:

1. Launch FFmpeg in a process group.
2. Capture Ctrl+C or platform cancellation signals.
3. Terminate FFmpeg gracefully.
4. Force-kill only after a timeout.
5. Remove incomplete GIFs.
6. Remove generated palette files.
7. Remove temporary downloads unless retention was requested.
8. Return a structured cancelled result.

Temporary files should be created using the operating system’s secure temporary directory, not predictable filenames in the repository.

⸻

Release plan

Release 0.1 — Local timestamp conversion

Scope:

* Single local video.
* Single or multiple timestamp ranges.
* Start/end and start/duration formats.
* JSON and CSV manifests.
* ffprobe inspection.
* FFmpeg palette-based conversion.
* Quality profiles.
* Project configuration.
* Output naming.
* Collision protection.
* Progress and cancellation.
* Continue-on-error batch processing.
* macOS, Windows, and Linux tests.

This is the minimum successful version.

Release 0.2 — Remote source acquisition

Scope:

* Direct HTTP/HTTPS media.
* Optional yt-dlp.
* Temporary downloads.
* Network confirmation.
* Credential redaction.
* Download progress.
* Rights confirmation.
* Remote-source documentation.

Release 0.3 — Transformations

Scope:

* Cropping.
* Custom resizing.
* Speed changes.
* Text captions.
* Subtitle burn-in.
* Start/end frame preview.
* File-size target optimization.
* Transparent-background handling where applicable.

Captions deserve a separate release because fonts, escaping, subtitle formats, and Windows/macOS/Linux font resolution introduce additional complexity.

Release 1.0 — Stable distribution

Scope:

* Stable CLI contract.
* Versioned schemas.
* Complete documentation.
* GitHub releases.
* Claude Code plugin package.
* Codex skills-only plugin package.
* Marketplace metadata.
* CI validation.
* Security policy.
* Upgrade and migration documentation.

Release 2.0 — AI-assisted clip selection

This should be a separate skill or optional capability. It would involve combinations of:

* Transcript extraction.
* Scene-change detection.
* Audio event analysis.
* Frame sampling.
* Multimodal model analysis.
* User-defined criteria such as humorous, romantic, action, instructional, or visually surprising moments.
* Ranking and previewing candidates before conversion.

It should not complicate the deterministic first version.

⸻

Marketplace strategy

Claude Code

Claude plugins use .claude-plugin/plugin.json, and Claude marketplaces use .claude-plugin/marketplace.json. Users can add a GitHub repository, URL, Git repository, or local marketplace and then install a namespaced plugin from it.  

Claude also has a community submission process. Submitted plugins are validated and safety screened; approved plugins are pinned in Anthropic’s community catalog. Anthropic’s official marketplace is curated separately and does not have a normal application process.  

Therefore:

1. Publish the GitHub repository and your own marketplace immediately.
2. Let users add the marketplace directly.
3. Submit the plugin to the Claude community catalog after stability.
4. Do not depend on inclusion in the separately curated official marketplace.

Codex

Codex plugins use .codex-plugin/plugin.json. A skills-only plugin is officially supported, so this project does not need an MCP server merely to qualify as a plugin. Codex supports repository and personal marketplace files and provides CLI marketplace commands for adding, upgrading, listing, and removing marketplace sources.  

OpenAI’s public submission process accepts:

* Skills-only plugins.
* MCP-backed app plugins.
* Plugins combining skills and an MCP app.

After review and publication, an approved plugin appears in the universal plugin directory for ChatGPT and Codex. Public submission requires an appropriate Platform organization role and verified developer or business identity.  

⸻

Testing strategy

Use synthetic videos created during tests rather than committing copyrighted sample videos.

For example, integration tests can generate:

* A 20-second color test video.
* A video with moving shapes.
* Different dimensions and frame rates.
* A video path containing spaces and Unicode.
* A video with no audio.
* A malformed file.
* A zero-duration or partially damaged file.

Test cases should include:

Area	Cases
Timestamp parsing	Seconds, MM:SS, HH:MM:SS, milliseconds
Validation	Negative, reversed, beyond duration, zero-length
Batch processing	Full success, partial success, all failures
Paths	Spaces, Unicode, long paths, Windows separators
Output	Collision, custom name, outside-project path
Cancellation	During inspection, palette generation, encoding
Configuration	First use, overrides, malformed config
Remote sources	Direct URL, failed download, redacted token
Quality	Small, balanced, high, custom
Portability	Ubuntu, macOS, Windows

The skill itself should also be validated against the Agent Skills specification. The specification provides a skills-ref validate command, and Claude provides claude plugin validate for plugin and marketplace packages.  

⸻

Final architectural decisions

Decision	Recommendation
Skill or plugin	Both: skill is the implementation; plugin is distribution
Number of implementations	One shared core skill
Platform packages	Separate Claude and Codex wrappers
Runtime	Python standard library
Conversion engine	FFmpeg and ffprobe
AI model required	No
Local processing	Default
Remote videos	Optional acquisition layer
Output directory	./output/
Preferences	First-run project configuration
Overwrites	Refuse, then ask
Batch failure	Continue and report
Progress	Parse FFmpeg machine-readable progress
Temporary files	Secure temp directory and automatic cleanup
Automatic highlights	Separate future release
MCP server	Not needed for MVP
Public release	Skills-only plugins are sufficient