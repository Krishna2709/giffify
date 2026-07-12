# Installation

The skill requires two executables in the environment: **`ffmpeg`** and **`ffprobe`** (both ship together with an FFmpeg install). Python 3.10+ is also required; the core runtime uses only the Python standard library.

Version 0.1.0 does NOT bundle FFmpeg binaries. It detects the real executables and, if they are missing, guides installation with your approval.

## `pip install ffmpeg` does NOT install FFmpeg

This is the most common mistake. `pip install ffmpeg` (and `ffmpeg-python`) installs a Python **wrapper**, not the FFmpeg program. It will not give you a working `ffmpeg`/`ffprobe`. Use your operating system's package manager instead, as shown below. The skill verifies the actual executables with `ffmpeg -version` / `ffprobe -version` (via `doctor`), not by checking pip packages.

## Approval-first rule (spec section 6.4)

The skill MUST NOT install system dependencies without explicit user approval. When a dependency is missing, the agent will:

1. State which executable is missing (`ffmpeg` and/or `ffprobe`).
2. Explain why it is required (media inspection and GIF encoding).
3. Show the proposed installation command for your platform.
4. Ask whether you want the command executed.
5. Verify the installation afterward by re-running `doctor`.

Installation commands require approval, and the skill will not run an install with elevated privileges (e.g., `sudo`) unless you explicitly authorize that exact command.

## Platform commands

### macOS

```
brew install ffmpeg
```

Requires Homebrew (https://brew.sh). This installs both `ffmpeg` and `ffprobe`.

### Windows

Using winget (built in on current Windows):

```
winget install Gyan.FFmpeg
```

Or using Chocolatey:

```
choco install ffmpeg
```

After installation, open a new terminal so the updated `PATH` takes effect, then confirm with `ffmpeg -version`.

### Linux

Debian / Ubuntu:

```
sudo apt update && sudo apt install ffmpeg
```

Fedora / RHEL:

```
sudo dnf install ffmpeg
```

(On some minimal distributions `ffmpeg` and `ffprobe` may be split across packages; install both if `doctor` still reports `ffprobe` missing.)

## Verifying

After installing, verify with the doctor command:

```
python scripts/video_to_gif.py doctor --json
```

`doctor` confirms that `ffmpeg` and `ffprobe` are executable, that the `palettegen` and `paletteuse` filters exist, that GIF encoding is available, and that the temporary directory (and any supplied output directory) is writable. If it still reports a missing dependency, ensure the executable is on your `PATH` and start a new terminal session.
