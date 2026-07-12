# Contributing

Thanks for your interest in `video-to-gif`. This project is a portable Agent
Skill for Claude Code and Codex, backed by a deterministic Python engine and
FFmpeg. Please read this guide before opening a change.

## Spec-first rule (important)

[`versioned_technical_spec.md`](versioned_technical_spec.md) (VTG-TS-001) is
**normative** and is the source of truth. It uses MUST/SHOULD/MAY language.

- Any behavior change starts with a **spec update**. If your change would alter
  the CLI, config/manifest/result schemas, security controls, exit codes, or any
  MUST/SHOULD requirement, update the spec first (or in the same change) and
  reference the relevant section.
- When the spec and `plan.md` disagree, the **spec wins** (`plan.md` is earlier
  research).
- Open decisions are tracked in spec §26. Do not invent answers to them — ask or
  leave them clearly marked **TBD**.

## Canonical-source rule (important)

The **only** place to edit the skill is:

```
src/skill/video-to-gif/
```

Everything under `packages/` (the Claude and Codex plugin packages) is
**generated** from that canonical source by `tools/build_packages.py`. Never edit
`packages/` by hand — your changes there will be overwritten on the next build,
and the build verifies the copied trees are byte-identical to the source.

## Development setup

Requirements:

- **Python 3.10+** (standard library only for the runtime — do not add
  third-party runtime dependencies; spec §6.2)
- **ffmpeg** and **ffprobe** on your `PATH`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt-get install -y ffmpeg`
  - Windows: `choco install ffmpeg -y`
  - `pip install ffmpeg` is **not** FFmpeg — install the real executables.

Verify your environment with the skill's own doctor command:

```
python src/skill/video-to-gif/scripts/video_to_gif.py doctor --json
```

No virtual environment or `pip install` is required to run the engine, because it
uses only the standard library.

## Running tests

The test framework is the standard-library `unittest` runner (zero
dependencies). Run the unit suite from the repository root:

```
python3 -m unittest discover -s tests/unit -v
```

Additional suites live under `tests/` and run the same way once present:

```
python3 -m unittest discover -s tests/integration -v
python3 -m unittest discover -s tests/security -v
python3 -m unittest discover -s tests/acceptance -v
```

Guidelines for tests (spec §22):

- **Never commit sample videos.** Integration tests must generate synthetic media
  at test time.
- Cover timestamp parsing, validation edges, manifest parsing, filename
  sanitization (Windows reserved names, Unicode), collision policies, and
  project-boundary checks.
- Security tests must prove that manifest values cannot execute commands, that
  filenames cannot escape the output directory, that hostile playlist files
  cannot trigger network access, and that resource limits clean up after
  themselves.
- Timestamp and manifest parsers need seeded generative/fuzz tests (spec §22.5) —
  use fixed seeds for reproducibility.

### Linting and type checking

Style, imports, and types are enforced by [Ruff](https://docs.astral.sh/ruff/)
(linter + formatter) and [mypy](https://mypy.readthedocs.io/). Both are
**dev-only** tools — the runtime stays standard-library-only (spec §6.2). Install
them from the `dev` optional-dependency group:

```
pip install -e ".[dev]"    # or: pip install ruff mypy
```

Run all three checks from the repository root (this is exactly what CI runs):

```
ruff check .            # lint (E, F, W, I, UP, B, SIM, C4, RUF)
ruff format --check .    # formatting (line length 100)
mypy                     # type check (config and paths come from pyproject.toml)
```

`mypy` is invoked bare: it reads its file list, `mypy_path`, and per-module
strictness from `[tool.mypy]` in `pyproject.toml`. Apply formatting fixes with
`ruff format .` and auto-fixable lint findings with `ruff check --fix .`.

## Building and validating packages

Generate the platform packages from the canonical source, then validate the
release:

```
python3 tools/build_packages.py
python3 tools/validate_release.py
```

`build_packages.py` copies the canonical skill into each platform package, writes
the plugin manifests, refuses to include media/temp files, verifies the copies
are byte-identical, and emits SHA-256 checksums. `validate_release.py` re-checks
the packaged output against the release integrity rules (spec §21.4). Both use
the standard library only. See [`docs/release-process.md`](docs/release-process.md).

## Continuous integration

CI runs on Ubuntu, macOS, and Windows against Python 3.10 and 3.12 (spec §22.3):
it installs FFmpeg per OS, runs the unit suite (and the integration/security/
acceptance suites when present), and performs a package build + release
validation on one matrix leg. Please make sure the unit suite passes locally
before opening a change.

## Commit and pull-request expectations

- Keep the runtime standard-library-only; keep tests `unittest`-only.
- Reference the spec section(s) your change implements or updates.
- Update `CHANGELOG.md` under the `[0.1.0]` (Unreleased) section for
  user-visible changes.
- Do not edit generated `packages/` output; edit `src/skill/video-to-gif/` and
  rebuild.

## Code of conduct and license

A code of conduct may be added before the first stable release. The project is
licensed under the **MIT License**; see [`LICENSE`](LICENSE). By contributing
you agree that your contributions will be licensed under the MIT License.
