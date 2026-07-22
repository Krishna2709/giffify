#!/usr/bin/env python3
"""Build the Claude Code and Codex plugin packages from the canonical skill.

Standard library only (spec §6.2). This tool implements the automatable parts of
the build-integrity requirements in spec §21.4:

  1. Copy the canonical skill (src/skill/video-to-gif) into each platform package.
  2. Verify copied files are byte-identical to the source.
  6. Generate SHA-256 checksums for each package.
  7. Verify that no test media or temporary files are included.

It also writes the plugin manifests per spec §21.2 / §21.3.

Package layout produced:

    packages/claude/video-to-gif/
    ├── .claude-plugin/plugin.json
    ├── skills/video-to-gif/           (byte-identical copy of the canonical skill)
    └── CHECKSUMS.sha256

    packages/codex/video-to-gif/
    ├── .codex-plugin/plugin.json
    ├── skills/video-to-gif/           (byte-identical copy of the canonical skill)
    └── CHECKSUMS.sha256

Media files in the canonical skill source (.gif, .mp4, .mov, .webm) FAIL the
build so they can never enter a package. Python bytecode (__pycache__/, .pyc)
is a build byproduct -- for example the test suite imports the engine from the
canonical source -- so it is PRUNED from the source and excluded from the copy
rather than failing the build (spec §27 DoD: a clean checkout can run tests
then packaging without manual cleanup).

Exit codes:
    0  success
    1  build failure (missing source, forbidden files, or verification mismatch)
    2  invalid usage
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

# --- Constants (spec §21.2 / §21.3) --------------------------------------------

SKILL_NAME = "video-to-gif"
PLUGIN_DESCRIPTION = "Generate optimized animated GIFs from explicit video timestamp ranges."
PLUGIN_VERSION = "0.3.0"
PLUGIN_AUTHOR = {"name": "Krishna2709", "url": "https://github.com/Krishna2709"}

# Media files are a contamination signal: they must never appear in the
# canonical skill source or a package, and their presence is a hard failure.
FORBIDDEN_SUFFIXES = {".gif", ".mp4", ".mov", ".webm"}

# Python bytecode is a build byproduct (running the test suite imports the engine
# from the canonical source, writing scripts/vtg/__pycache__). It is pruned from
# the source and excluded from the copy rather than failing the build.
BYTECODE_SUFFIXES = {".pyc", ".pyo"}
BYTECODE_DIR_NAMES = {"__pycache__"}

# The name of the per-package checksum file (excluded from copy/verify/hash).
CHECKSUM_FILENAME = "CHECKSUMS.sha256"

# Platform package definitions: (package subdir, manifest dir, has been mapped).
PLATFORMS = {
    "claude": ".claude-plugin",
    "codex": ".codex-plugin",
}


class BuildError(Exception):
    """Raised for any recoverable build failure."""


def repo_root() -> Path:
    """Return the repository root (parent of this tools/ directory)."""
    return Path(__file__).resolve().parent.parent


def canonical_skill_dir(root: Path) -> Path:
    return root / "src" / "skill" / SKILL_NAME


def _is_bytecode(path: Path) -> bool:
    return path.suffix.lower() in BYTECODE_SUFFIXES or any(
        part in BYTECODE_DIR_NAMES for part in path.parts
    )


def iter_files(base: Path) -> Iterator[Path]:
    """Yield every regular file under *base*, sorted for determinism.

    Skips the per-package checksum file so it is never copied, verified, or
    re-hashed into itself, and skips Python bytecode so the byte-identical
    verification stays consistent with the pruning (see :func:`prune_bytecode`).
    """
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name == CHECKSUM_FILENAME or _is_bytecode(path):
            continue
        yield path


def prune_bytecode(skill_dir: Path) -> list[str]:
    """Remove Python bytecode (``__pycache__`` dirs, ``*.pyc``/``*.pyo``) from a tree.

    Bytecode is a build byproduct: running the test suite imports the engine from
    the canonical source, writing ``scripts/vtg/__pycache__``. It must never fail
    the build or enter a package, so it is pruned before the forbidden-files check
    and excluded from the copy (spec §27 DoD: a clean checkout can run tests then
    packaging with no manual cleanup). Returns the removed paths (relative).
    """
    pruned: list[str] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.exists():
            # Already removed as part of a pruned __pycache__ directory.
            continue
        if path.is_dir() and path.name in BYTECODE_DIR_NAMES:
            shutil.rmtree(path, ignore_errors=True)
            pruned.append(str(path.relative_to(skill_dir)) + "/")
        elif path.is_file() and path.suffix.lower() in BYTECODE_SUFFIXES:
            path.unlink()
            pruned.append(str(path.relative_to(skill_dir)))
    return pruned


def check_no_forbidden_files(skill_dir: Path) -> None:
    """Fail the build if the skill source contains media files (spec §21.4.7).

    Media (.gif/.mp4/.mov/.webm) is a contamination signal and a hard failure.
    Python bytecode is handled separately by :func:`prune_bytecode` (a build
    byproduct, not contamination) and never reaches this check.
    """
    problems: list[str] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"forbidden file: {path.relative_to(skill_dir)}")
    if problems:
        raise BuildError(
            "Media files must not exist in the skill source:\n  - " + "\n  - ".join(problems)
        )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_are_identical(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ba = fa.read(65536)
            bb = fb.read(65536)
            if ba != bb:
                return False
            if not ba:
                return True


def write_manifest(manifest_path: Path) -> None:
    manifest = {
        "name": SKILL_NAME,
        "description": PLUGIN_DESCRIPTION,
        "version": PLUGIN_VERSION,
        "author": dict(PLUGIN_AUTHOR),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def verify_byte_identical(source_skill: Path, dest_skill: Path) -> None:
    """Verify dest_skill is a byte-identical copy of source_skill (spec §21.4.2)."""
    source_files = {p.relative_to(source_skill): p for p in iter_files(source_skill)}
    dest_files = {p.relative_to(dest_skill): p for p in iter_files(dest_skill)}

    missing = sorted(str(r) for r in source_files.keys() - dest_files.keys())
    extra = sorted(str(r) for r in dest_files.keys() - source_files.keys())
    if missing:
        raise BuildError("Copied package is missing files:\n  - " + "\n  - ".join(missing))
    if extra:
        raise BuildError("Copied package has unexpected extra files:\n  - " + "\n  - ".join(extra))

    mismatched = [
        str(rel)
        for rel, src in source_files.items()
        if not files_are_identical(src, dest_files[rel])
    ]
    if mismatched:
        raise BuildError(
            "Copied files are not byte-identical to source:\n  - "
            + "\n  - ".join(sorted(mismatched))
        )


def write_checksums(package_root: Path) -> Path:
    """Write a SHA-256 checksum file covering every file in the package (spec §21.4.6)."""
    lines: list[str] = []
    for path in iter_files(package_root):
        rel = path.relative_to(package_root).as_posix()
        lines.append(f"{sha256_of(path)}  {rel}")
    checksum_path = package_root / CHECKSUM_FILENAME
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


_PACKAGE_README = """\
# video-to-gif ({platform_title} plugin package)

Generate optimized animated GIFs from explicit video timestamp ranges. This
package wraps the portable `video-to-gif` Agent Skill for {platform_title}.

- Version: {version}
- Author: {author} ({author_url})
- License: MIT
- Source repository: https://github.com/Krishna2709/giffify
- Requirements: Python 3.10+ and the `ffmpeg`/`ffprobe` executables on PATH
  (`pip install ffmpeg` is NOT FFmpeg)

The skill itself lives under `skills/video-to-gif/` (SKILL.md plus the
deterministic Python engine, references, and JSON schemas). Conversion is local
by default; version 0.2.0 adds opt-in, download-only remote source acquisition
that is disabled by default and gated by explicit enablement/approval. See
`skills/video-to-gif/references/` and the repository's SECURITY.md for the
security model.

This directory is GENERATED from the canonical source at
`src/skill/video-to-gif/` by `tools/build_packages.py` — do not edit it by
hand. `CHECKSUMS.sha256` covers every file in this package.
"""


def write_package_readme(package_root: Path, platform: str) -> None:
    """Write a per-package README so the package is self-describing (catalog reviews)."""
    titles = {"claude": "Claude Code", "codex": "Codex"}
    content = _PACKAGE_README.format(
        platform_title=titles.get(platform, platform),
        version=PLUGIN_VERSION,
        author=PLUGIN_AUTHOR["name"],
        author_url=PLUGIN_AUTHOR["url"],
    )
    (package_root / "README.md").write_text(content, encoding="utf-8")


def build_platform(platform: str, manifest_dirname: str, root: Path, source_skill: Path) -> Path:
    package_root = root / "packages" / platform / SKILL_NAME
    dest_skill = package_root / "skills" / SKILL_NAME

    # Start from a clean package tree so stale files cannot survive a rebuild.
    if package_root.exists():
        shutil.rmtree(package_root)
    dest_skill.parent.mkdir(parents=True, exist_ok=True)

    # 1. Copy canonical skill, excluding any Python bytecode byproducts.
    shutil.copytree(
        source_skill,
        dest_skill,
        ignore=shutil.ignore_patterns(*BYTECODE_DIR_NAMES, "*.pyc", "*.pyo"),
    )

    # 2. Verify byte-identical.
    verify_byte_identical(source_skill, dest_skill)

    # Write the plugin manifest (spec §21.2 / §21.3) and a self-describing README.
    write_manifest(package_root / manifest_dirname / "plugin.json")
    write_package_readme(package_root, platform)

    # 7. Ensure the copied package contains no forbidden files (defense in depth).
    check_no_forbidden_files(dest_skill)

    # 6. Generate checksums (covers skill files + manifest).
    write_checksums(package_root)

    return package_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (defaults to the parent of tools/).",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve() if args.root else repo_root()
    source_skill = canonical_skill_dir(root)

    try:
        if not source_skill.is_dir():
            raise BuildError(
                f"Canonical skill directory not found: {source_skill}\n"
                "Nothing to build. The skill source (src/skill/video-to-gif) "
                "must exist before packaging."
            )
        skill_md = source_skill / "SKILL.md"
        if not skill_md.is_file():
            raise BuildError(f"Canonical skill is missing SKILL.md: {skill_md}")

        # Prune bytecode byproducts (e.g. from running the test suite) so they
        # never fail the build or enter a package (spec §27 DoD).
        pruned = prune_bytecode(source_skill)
        if pruned:
            print(f"[prune] removed {len(pruned)} bytecode item(s) from the skill source")

        # Fail fast if the source contains media files (spec §21.4.7).
        check_no_forbidden_files(source_skill)

        built: list[Path] = []
        for platform, manifest_dirname in PLATFORMS.items():
            package_root = build_platform(platform, manifest_dirname, root, source_skill)
            built.append(package_root)
            manifest_rel = (package_root / manifest_dirname / "plugin.json").relative_to(root)
            print(f"[ok] built {platform} package: {package_root.relative_to(root)}")
            print(f"     manifest:  {manifest_rel}")
            print(f"     checksums: {(package_root / CHECKSUM_FILENAME).relative_to(root)}")
    except BuildError as exc:
        print(f"[build failed] {exc}", file=sys.stderr)
        return 1

    print(f"\nBuilt {len(built)} package(s) at version {PLUGIN_VERSION}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
