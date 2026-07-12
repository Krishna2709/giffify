# Release process

This document describes how a `video-to-gif` release is built, validated,
versioned, and published. It consolidates spec §21.4 (build integrity), §21.5
(marketplace metadata and publishing), and §24 (versioning). The specification
([`versioned_technical_spec.md`](../versioned_technical_spec.md)) is normative.

## Versioning policy (spec §24)

Releases use **semantic versioning** `MAJOR.MINOR.PATCH`:

- **MAJOR** — breaking changes to the CLI, configuration, manifest, or behavior.
- **MINOR** — backward-compatible new capabilities.
- **PATCH** — backward-compatible fixes and documentation improvements.

**Schema versions are independent integers** (`"schemaVersion": 1`) covering the
configuration, manifest, and result schemas. A product major-version change does
not automatically require a schema-version bump. Patch releases MUST NOT break
CLI flag names or schema version 1 (spec NFR-006); minor releases may add
optional fields.

The current product version is **0.1.0**. This exact string must be identical in:

- `pyproject.toml` (`[project].version`)
- `CHANGELOG.md` (the `[0.1.0]` section)
- both generated plugin manifests (`packages/*/video-to-gif/.­*-plugin/plugin.json`)
- both marketplace files (`marketplaces/*/**/marketplace.json`)

`tools/validate_release.py` enforces that these agree.

## The canonical source

The only editable copy of the skill is `src/skill/video-to-gif/`. Everything
under `packages/` is **generated** and must never be edited by hand (spec §5).
The build verifies the copied trees are byte-identical to the canonical source.

## Build integrity steps (spec §21.4)

A release build MUST:

1. **Copy** the canonical skill into each platform package
   (`packages/claude/video-to-gif/skills/video-to-gif` and
   `packages/codex/video-to-gif/skills/video-to-gif`).
2. **Verify** copied files are byte-identical to the source.
3. **Validate `SKILL.md`** with the Agent Skills reference validator
   (`skills-ref validate`).
4. **Validate both plugin manifests** (using `claude plugin validate` for the
   Claude package).
5. **Run unit and integration tests.**
6. **Generate checksums** (SHA-256) for each package.
7. **Verify no test media or temporary files** are included in the packages.
8. **Verify no credentials** are present in packaged files.
9. **Verify the package version matches the changelog.**

### What the tooling in this repository automates

Two standard-library-only tools cover the parts that can be automated here:

- **`tools/build_packages.py`** performs steps 1, 2, 6, and 7, and writes the
  plugin manifests:
  - copies the canonical skill into both platform packages;
  - **fails the build** if the skill source contains any media files
    (`.gif`, `.mp4`, `.mov`, `.webm`) so such contamination can never enter a
    package;
  - **prunes Python bytecode** (`__pycache__/`, `.pyc`/`.pyo`) from the skill
    source and excludes it from the copy — running the test suites imports the
    engine from the canonical source and writes bytecode there, so the build
    cleans it up automatically (no manual cache sweep is needed between the test
    and packaging steps);
  - writes `packages/claude/video-to-gif/.claude-plugin/plugin.json` and
    `packages/codex/video-to-gif/.codex-plugin/plugin.json`
    (name `video-to-gif`, description
    "Generate optimized animated GIFs from explicit video timestamp ranges.",
    version `0.1.0`, author `Krishna2709`);
  - verifies every copied file is byte-identical to its source; and
  - emits a `CHECKSUMS.sha256` file per package.

  Run it from the repository root:

  ```
  python3 tools/build_packages.py
  ```

- **`tools/validate_release.py`** performs steps 8 and 9 and re-checks the
  packaged output:
  - `SKILL.md` exists with `name` and `description` frontmatter, and `name`
    matches the skill directory name;
  - both plugin manifests parse and their versions match `pyproject.toml` and
    `CHANGELOG.md`;
  - no credential-looking strings appear in packaged files (regex scan for common
    token patterns);
  - no media files are present in the packages;
  - no Python bytecode (`__pycache__/`, `.pyc`/`.pyo`) is present in the packages;
    and
  - both marketplace files parse and pin the same version.

  Run it after a build:

  ```
  python3 tools/validate_release.py
  ```

Steps 3, 4, and 5 use external tooling (`skills-ref validate`, `claude plugin
validate`, the test suites) and are run in CI and locally as part of the release;
they are not reimplemented by the stdlib tools above.

## Marketplace metadata and publishing (spec §21.5)

The repository provides marketplace metadata for both platforms:

- **Claude Code** — `marketplaces/claude/.claude-plugin/marketplace.json` listing
  the `video-to-gif` plugin.
- **Codex** — `marketplaces/codex/.agents/plugins/marketplace.json` for
  repository and personal marketplace installation.

Marketplace entries MUST pin an exact plugin version, pass platform validation
(`claude plugin validate` for Claude packages), and reference only released,
checksummed artifacts. Owner and publishing account: GitHub `Krishna2709`
(spec §26 decisions 2 and 11, resolved).

### Publishing sequence

1. **Publish** the repository and the self-hosted marketplace files so users can
   add the marketplace directly.
2. **Submit to the Claude community catalog** after a stable release (submissions
   are validated and safety-screened; the separately curated official Anthropic
   marketplace has no normal application process).
3. **Submit the Codex plugin** through the OpenAI public submission process, which
   requires a verified developer or business identity.

## Automated release pipeline (`.github/workflows/release.yml`)

Releases are cut by **pushing a version tag**. `.github/workflows/release.yml`
triggers on any tag matching `v*` and runs three jobs:

1. **verify** — asserts the tag (with the leading `v` stripped) equals
   `pyproject.toml`'s `[project].version`, and fails fast if a GitHub Release
   for the tag already exists, so a duplicate tag push cannot double-publish.
2. **test** — the full spec §22.3 matrix (Ubuntu/macOS/Windows × Python 3.10
   and 3.12), installing FFmpeg per OS (mirroring `ci.yml`) and running all four
   suites: unit, integration, security, and acceptance.
3. **build-and-release** (needs verify + test) — runs
   `tools/build_packages.py`, then `tools/validate_release.py`, creates `tar.gz`
   and `zip` archives of each platform package
   (`video-to-gif-claude-<version>.{tar.gz,zip}` and
   `video-to-gif-codex-<version>.{tar.gz,zip}`), writes a combined `SHA256SUMS`
   over the four archives, extracts release notes from the matching
   `CHANGELOG.md` section (falling back to `gh`'s auto-generated notes if the
   section is missing), and publishes the GitHub Release with `gh release
   create`, attaching the four archives plus `SHA256SUMS`.

`validate_release.py` inspects `packages/` and therefore runs **after** the
build, inside the build-and-release job — it is not part of the pre-build verify
gate. The pipeline needs no secrets beyond the automatically provided
`GITHUB_TOKEN`. This is how release artifacts are produced (spec §26 decision
10): **generated in CI at tag time**, not committed to the repository.

## Release checklist

The release is **tag-driven**: pushing a `vX.Y.Z` tag runs the pipeline above,
which tests, builds, validates, and publishes the checksummed archives
automatically. A maintainer's steps are:

1. Ensure the spec reflects the release (spec-first rule; see `CONTRIBUTING.md`).
2. Bump the version to `X.Y.Z` in `pyproject.toml` and confirm it is consistent
   across `CHANGELOG.md`, both plugin manifests, and both marketplace files
   (`tools/validate_release.py` enforces this).
3. Move the relevant entries under the `## [X.Y.Z]` section of `CHANGELOG.md`
   and **date the release** — the published release notes are extracted from
   this section.
4. Verify `LICENSE` contains the MIT license text (spec §26 decision 3,
   resolved: MIT).
5. *(Optional local pre-flight)* Run the same checks the pipeline runs, to catch
   problems before tagging:

   ```
   python3 -m unittest discover -s tests/unit -v   # + integration/security/acceptance (need ffmpeg)
   python3 tools/build_packages.py
   python3 tools/validate_release.py
   ```

   `build_packages.py` may be run on the same checkout immediately after the
   suites with no manual cleanup: it prunes the Python bytecode
   (`__pycache__/`, `.pyc`) that importing the engine during testing leaves in
   the skill source. Also validate `SKILL.md` (`skills-ref validate`) and the
   plugin/marketplace packages (`claude plugin validate`).
6. Commit the version bump and dated changelog.
7. Tag the release and push the tag — this is what launches the pipeline:

   ```
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

8. **`release.yml` takes over**: it runs the matrix, builds the packages,
   validates the release, and publishes the GitHub Release with the four
   checksummed archives + `SHA256SUMS` attached. Watch the workflow run and
   confirm the Release and its assets appear.
9. Publish/refresh the marketplace files, then submit to the community catalogs
   per the [publishing sequence](#publishing-sequence) above. Marketplace
   entries then reference the released, checksummed archives the pipeline
   attached to the GitHub Release.

## Open decisions affecting releases (spec §26)

- Final repository and plugin name (decision 1) — **resolved**: repository
  `giffify`, plugin `video-to-gif`.
- Maintainer identity and package metadata (decision 2) — **resolved**:
  `Krishna2709`.
- Open-source license (decision 3) — **resolved**: MIT.
- Whether package artifacts are generated in CI or committed (decision 10) —
  open.
- Marketplace publishing accounts and ownership (decision 11) — **resolved**:
  GitHub `Krishna2709`.
