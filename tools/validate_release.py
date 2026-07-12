#!/usr/bin/env python3
"""Validate a built release against the integrity rules in spec §21.4 / §21.5.

Standard library only (spec §6.2). Intended to run AFTER tools/build_packages.py.

Checks performed:
  * SKILL.md exists with `name` and `description` frontmatter, and `name` matches
    the skill directory name (spec §7, §21.4.3).
  * Both plugin.json manifests parse and their versions match pyproject.toml and
    CHANGELOG.md (spec §21.4.9).
  * No credential-looking strings appear in packaged files (spec §21.4.8).
  * No media files are present in the packages (spec §21.4.7).
  * No Python bytecode (__pycache__/, .pyc/.pyo) is present in the packages;
    bytecode is a build byproduct and must never ship (spec §21.4.7).
  * Both marketplace files parse and pin the same version (spec §21.5).

Exit codes:
    0  all checks passed
    1  one or more checks failed
    2  invalid usage
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterator
from pathlib import Path

SKILL_NAME = "video-to-gif"
MEDIA_SUFFIXES = {".gif", ".mp4", ".mov", ".webm"}
BYTECODE_SUFFIXES = {".pyc", ".pyo"}
BYTECODE_DIR_NAMES = {"__pycache__"}

# High-signal credential/token patterns (spec §21.4.8). Kept precise to avoid
# false positives on ordinary skill content, schemas, and checksums.
CREDENTIAL_PATTERNS = [
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[posru]_[0-9A-Za-z]{30,}")),
    ("GitHub fine-grained PAT", re.compile(r"github_pat_[0-9A-Za-z_]{22,}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("Stripe secret key", re.compile(r"sk_live_[0-9A-Za-z]{16,}")),
    ("Private key block", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    (
        "JSON Web Token",
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
    (
        "secret assignment",
        re.compile(
            r"""(?i)(?:api[_-]?key|secret|password|access[_-]?token|auth[_-]?token)"""
            r"""\s*[:=]\s*["'][A-Za-z0-9+/_\-]{20,}["']"""
        ),
    ),
]


class Reporter:
    def __init__(self) -> None:
        self.failures = 0
        self.checks = 0

    def ok(self, message: str) -> None:
        self.checks += 1
        print(f"[pass] {message}")

    def fail(self, message: str) -> None:
        self.checks += 1
        self.failures += 1
        print(f"[FAIL] {message}")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# --- Version sources -----------------------------------------------------------


def read_pyproject_version(root: Path, report: Reporter) -> str | None:
    path = root / "pyproject.toml"
    if not path.is_file():
        report.fail(f"pyproject.toml not found at {path}")
        return None
    text = path.read_text(encoding="utf-8")
    version: str | None = None
    try:
        import tomllib  # type: ignore[import-not-found]  # Python 3.11+; regex fallback below

        version = tomllib.loads(text).get("project", {}).get("version")
    except ModuleNotFoundError:
        match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
        version = match.group(1) if match else None
    if not version:
        report.fail("pyproject.toml is missing [project].version")
        return None
    report.ok(f"pyproject.toml version = {version}")
    return version


def read_changelog_version(root: Path, report: Reporter) -> str | None:
    path = root / "CHANGELOG.md"
    if not path.is_file():
        report.fail(f"CHANGELOG.md not found at {path}")
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^##\s*\[(\d+\.\d+\.\d+)\]", text)
    if not match:
        report.fail("CHANGELOG.md has no versioned '## [x.y.z]' section")
        return None
    version = match.group(1)
    report.ok(f"CHANGELOG.md version = {version}")
    return version


# --- SKILL.md frontmatter ------------------------------------------------------


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract simple top-level 'key: value' pairs from a leading --- block."""
    match = re.match(r"^﻿?---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line[:1] in (" ", "\t", "#"):
            # Skip blanks, comments, and nested (indented) keys such as metadata.
            continue
        kv = re.match(r"([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if kv:
            fields[kv.group(1)] = kv.group(2).strip().strip('"').strip("'")
    return fields


def check_skill_md(root: Path, report: Reporter) -> None:
    skill_dir = root / "src" / "skill" / SKILL_NAME
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        report.fail(f"SKILL.md not found at {skill_md}")
        return
    fields = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    name = fields.get("name")
    description = fields.get("description")
    if not name:
        report.fail("SKILL.md frontmatter is missing 'name'")
    elif name != skill_dir.name:
        report.fail(f"SKILL.md name '{name}' does not match directory '{skill_dir.name}'")
    else:
        report.ok(f"SKILL.md name matches directory ('{name}')")
    if not description:
        report.fail("SKILL.md frontmatter is missing 'description'")
    else:
        report.ok("SKILL.md has a description")


# --- Plugin manifests ----------------------------------------------------------


def check_plugin_manifests(root: Path, expected: str, report: Reporter) -> None:
    manifests = [
        ("claude", root / "packages" / "claude" / SKILL_NAME / ".claude-plugin" / "plugin.json"),
        ("codex", root / "packages" / "codex" / SKILL_NAME / ".codex-plugin" / "plugin.json"),
    ]
    for platform, path in manifests:
        if not path.is_file():
            report.fail(f"{platform} plugin.json not found at {path} (run build_packages.py first)")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.fail(f"{platform} plugin.json is not valid JSON: {exc}")
            continue
        if data.get("name") != SKILL_NAME:
            report.fail(
                f"{platform} plugin.json name is '{data.get('name')}', expected '{SKILL_NAME}'"
            )
        version = data.get("version")
        if version != expected:
            report.fail(f"{platform} plugin.json version '{version}' != expected '{expected}'")
        else:
            report.ok(f"{platform} plugin.json version pinned to {expected}")


# --- Packaged file scans -------------------------------------------------------


def iter_package_files(root: Path) -> Iterator[Path]:
    packages_dir = root / "packages"
    if not packages_dir.is_dir():
        return
    for path in sorted(packages_dir.rglob("*")):
        if path.is_file():
            yield path


def check_no_media_in_packages(root: Path, report: Reporter) -> None:
    packages_dir = root / "packages"
    if not packages_dir.is_dir():
        report.fail("packages/ directory not found (run build_packages.py first)")
        return
    offenders = [
        str(p.relative_to(root))
        for p in iter_package_files(root)
        if p.suffix.lower() in MEDIA_SUFFIXES
    ]
    if offenders:
        report.fail(
            "media files present in packages:\n         - " + "\n         - ".join(offenders)
        )
    else:
        report.ok("no media files in packages")


def check_no_bytecode_in_packages(root: Path, report: Reporter) -> None:
    """Fail if any Python bytecode was shipped in a package (spec §21.4.7)."""
    packages_dir = root / "packages"
    if not packages_dir.is_dir():
        report.fail("packages/ directory not found (run build_packages.py first)")
        return
    offenders: list[str] = []
    for path in sorted(packages_dir.rglob("*")):
        if path.is_dir() and path.name in BYTECODE_DIR_NAMES:
            offenders.append(str(path.relative_to(root)) + "/")
        elif path.is_file() and path.suffix.lower() in BYTECODE_SUFFIXES:
            offenders.append(str(path.relative_to(root)))
    if offenders:
        report.fail(
            "Python bytecode present in packages (must never ship):\n         - "
            + "\n         - ".join(offenders)
        )
    else:
        report.ok("no Python bytecode in packages")


def check_no_credentials(root: Path, report: Reporter) -> None:
    packages_dir = root / "packages"
    if not packages_dir.is_dir():
        report.fail("packages/ directory not found (run build_packages.py first)")
        return
    hits: list[str] = []
    for path in iter_package_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            report.fail(f"could not read {path}: {exc}")
            continue
        for label, pattern in CREDENTIAL_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(root)}: {label}")
    if hits:
        report.fail("credential-looking strings found:\n         - " + "\n         - ".join(hits))
    else:
        report.ok("no credential-looking strings in packages")


# --- Marketplace metadata ------------------------------------------------------


def find_plugin_version(data: object, name: str) -> str | None:
    """Return the version pinned for *name* within a marketplace document."""
    if isinstance(data, dict):
        plugins = data.get("plugins")
        if isinstance(plugins, list):
            for entry in plugins:
                if isinstance(entry, dict) and entry.get("name") == name:
                    return entry.get("version")
    return None


def check_marketplaces(root: Path, expected: str, report: Reporter) -> None:
    files = [
        ("claude", root / "marketplaces" / "claude" / ".claude-plugin" / "marketplace.json"),
        ("codex", root / "marketplaces" / "codex" / ".agents" / "plugins" / "marketplace.json"),
    ]
    for platform, path in files:
        if not path.is_file():
            report.fail(f"{platform} marketplace.json not found at {path}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.fail(f"{platform} marketplace.json is not valid JSON: {exc}")
            continue
        version = find_plugin_version(data, SKILL_NAME)
        if version is None:
            report.fail(
                f"{platform} marketplace.json does not list plugin '{SKILL_NAME}' with a version"
            )
        elif version != expected:
            report.fail(
                f"{platform} marketplace.json pins '{SKILL_NAME}' at "
                f"'{version}', expected '{expected}'"
            )
        else:
            report.ok(f"{platform} marketplace.json pins {SKILL_NAME} at {expected}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Repository root.")
    args = parser.parse_args(argv)
    root = args.root.resolve() if args.root else repo_root()

    report = Reporter()

    py_version = read_pyproject_version(root, report)
    changelog_version = read_changelog_version(root, report)

    # The pyproject version is the reference the rest of the release must match.
    expected = py_version
    if py_version and changelog_version and py_version != changelog_version:
        report.fail(
            f"version mismatch: pyproject '{py_version}' != CHANGELOG '{changelog_version}'"
        )
    elif py_version and changelog_version:
        report.ok(f"pyproject and CHANGELOG agree on version {py_version}")

    check_skill_md(root, report)

    if expected:
        check_plugin_manifests(root, expected, report)
        check_marketplaces(root, expected, report)
    else:
        report.fail("cannot verify manifest/marketplace versions without a pyproject version")

    check_no_media_in_packages(root, report)
    check_no_bytecode_in_packages(root, report)
    check_no_credentials(root, report)

    print()
    if report.failures:
        print(f"Release validation FAILED: {report.failures} of {report.checks} checks failed.")
        return 1
    print(f"Release validation passed: {report.checks} checks OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
