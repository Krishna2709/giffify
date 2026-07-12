# AC-015 — Agent usability (manual verification)

> **Spec:** VTG-TS-001 §23, AC-015. **Status:** not automatable from the engine
> test harness — this document defines the manual procedure.

AC-015 requires that **both Claude Code and OpenAI Codex** can, end to end:

1. Recognize an appropriate video-to-GIF request.
2. Load the `video-to-gif` skill.
3. Collect missing information.
4. Invoke the script.
5. Interpret the structured result.
6. Return the required one-line summary.

This exercises the **agent layer** (natural-language interpretation, question
asking, approval gating, summarization) which lives outside the deterministic
Python engine. The engine's own behavior — the parts (4) and (5) depend on — is
covered automatically by `tests/integration`, `tests/security`, and
`tests/acceptance` (AC-001..AC-014). What remains is a human-in-the-loop check
that each agent client wires the skill up correctly.

Perform the procedure below on **each** supported platform combination from the
CI matrix (§22.3): current macOS, current Ubuntu, and current Windows, since path
handling and PowerShell invocation differ per OS.

## Preconditions

- `ffmpeg` and `ffprobe` are installed and on `PATH`
  (verify: `python3 scripts/video_to_gif.py doctor --json` reports `healthy: true`).
- Python 3.10+.
- The `video-to-gif` skill (or its platform plugin package) is installed in the
  client under test:
  - **Claude Code:** install the plugin from `packages/claude/video-to-gif`
    (or the marketplace entry) and confirm `SKILL.md` is discovered.
  - **Codex:** install the plugin from `packages/codex/video-to-gif`
    (or the marketplace entry) and confirm the skill loads.
- A short local test video exists. Generate one without committing it:
  ```
  python3 tools/generate_test_video.py landscape "./sample demo.mp4" \
    --size 640x360 --fps 30 --duration 70
  ```
  (Use a name containing a space to also spot-check AC-011 path handling.)

## Procedure (run in each client, on each OS)

### 1. Recognition + skill load
Prompt the agent with natural language that does **not** name the skill or script,
e.g.:

> "Make me a GIF of `./sample demo.mp4` from 1:00 to 1:05."

**Expected:** the agent recognizes this as a video-to-GIF task and loads the
`video-to-gif` skill (visible in the client's skill/tool trace). It must **not**
hand-roll its own ffmpeg command.

### 2. Collect missing information
Give a request that is missing a required decision, e.g. omit the quality profile
on first use in a fresh project (no `.video-to-gif.json`):

> "Turn the first five seconds of `./sample demo.mp4` into a GIF."

**Expected (§19):** the agent asks **only** the required question(s) — e.g. which
quality profile to use — and does not re-ask for anything already supplied. It
must ask before writing outside the project or overwriting an existing file, and
must ask before installing any dependency.

### 3. Invoke the script
After questions are answered, the agent must invoke the engine non-interactively
with `--json`, e.g. the equivalent of:

```
python3 scripts/video_to_gif.py create \
  --input "./sample demo.mp4" --start 01:00 --end 01:05 \
  --profile balanced --json
```

**Expected:** exactly one JSON document on stdout; progress (if surfaced) comes
from stderr JSON Lines; the process exits `0`.

### 4. Interpret the structured result
**Expected:** the agent parses the final JSON (not log text) — reads `status`,
`created[]`, `summary`, and any `warnings`/`failed[]` — and maps the exit code per
§14. On a collision it should surface the `collision` status and ask how to
proceed rather than overwriting.

### 5. Return the required summary
**Expected (§17):** a concise one-line summary, e.g.:

> "Created 1 GIF in ./output."

For a partial batch it must read like:

> "Created 9 GIFs in ./output; 1 clip failed during encoding."

### 6. Cross-check the artifacts
- The GIF(s) exist under `./output` and play (loop forever by default).
- Nothing was written outside the project without explicit approval.
- No sample media or temp files were committed to the repository.

## Pass criteria

For **both** Claude Code and Codex, on **each** OS: steps 1–6 succeed, the agent
asks only necessary questions, respects approval gates (install / overwrite /
external path), and returns the required summary. Record client version, OS,
Python version, and ffmpeg version alongside the result.

## Cleanup

Remove the generated sample and any `./output` artifacts:

```
rm -f "./sample demo.mp4"
rm -rf ./output
```

No media may be left in the working tree (see the repo-cleanliness check in the
automated suites).
