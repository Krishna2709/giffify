"""Dependency detection and the ``doctor`` check (spec sections 6.3, 12.1).

Detects the real ``ffmpeg``/``ffprobe`` executables (not the unrelated
``pip install ffmpeg`` package) and verifies the filters/encoder the pipeline
needs. Never installs anything; installation is an approval-gated agent action.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any

from . import errors

MIN_PYTHON = (3, 10)

# Suggested installation commands per platform (guidance only; never executed
# by the engine). Open decision 6 in the spec; these are reasonable defaults.
INSTALL_GUIDANCE = {
    "darwin": "brew install ffmpeg",
    "linux": "sudo apt-get install ffmpeg  (or your distro's package manager)",
    "win32": "winget install Gyan.FFmpeg  (or: choco install ffmpeg)",
}


def find_executable(name: str) -> str | None:
    """Locate a real executable by name, honouring a VTG_* override env var."""
    override = os.environ.get("VTG_" + name.upper())
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    return shutil.which(name)


def _run(cmd: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        text=True,
        # Probe output (version banners, filter/encoder tables) is UTF-8; decode
        # it independently of the host locale and never raise (section 13.5).
        encoding="utf-8",
        errors="replace",
        check=False,
    )


# Process-local memo for the capability probes below. `doctor` asks six boolean
# questions that are answered by only two ffmpeg tables, and each spawn costs
# ~20ms of ffmpeg's own dyld/registration startup rather than real work. The key
# is the full argv, which carries the RESOLVED executable path, so a VTG_FFMPEG
# override can never be served an answer produced by PATH's ffmpeg. The cache is
# deliberately in-memory only and never persisted: doctor exists to report the
# toolchain as it is right now, and a stale on-disk capability table would defeat
# exactly the failure it is there to catch (section 6.3).
_PROBE_CACHE: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {}


def _run_cached(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """``_run`` memoized for the lifetime of the process (read-only result)."""
    key = tuple(cmd)
    proc = _PROBE_CACHE.get(key)
    if proc is None:
        proc = _run(cmd)
        _PROBE_CACHE[key] = proc
    return proc


def _has_filter(ffmpeg: str, filter_name: str) -> bool:
    try:
        proc = _run_cached([ffmpeg, "-hide_banner", "-filters"])
    except (OSError, subprocess.SubprocessError):
        return False
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == filter_name:
            return True
    return filter_name in proc.stdout


def _has_encoder(ffmpeg: str, encoder: str) -> bool:
    try:
        proc = _run_cached([ffmpeg, "-hide_banner", "-encoders"])
    except (OSError, subprocess.SubprocessError):
        return False
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == encoder:
            return True
    return False


def _has_gif_encoder(ffmpeg: str) -> bool:
    return _has_encoder(ffmpeg, "gif")


def find_ytdlp() -> str | None:
    """Locate the optional ``yt-dlp`` executable (never installs it, FR-022)."""
    return find_executable("yt-dlp")


def ytdlp_version(path: str) -> str | None:
    """Return the yt-dlp version string, or None if it cannot be determined."""
    try:
        proc = _run([path, "--version"], timeout=10.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    version = (proc.stdout or "").strip().splitlines()[0].strip() if proc.stdout else ""
    return version or None


# Upper bound on how long run_doctor waits to collect the backgrounded yt-dlp
# version probe. The real bound is the 10s subprocess timeout inside
# ytdlp_version; this only exists so a worker wedged somewhere outside that
# timeout degrades to version=None instead of hanging doctor forever.
_YTDLP_JOIN_SECONDS = 12.0


def _tempdir_writable() -> bool:
    try:
        with tempfile.NamedTemporaryFile(prefix="vtg-doctor-", delete=True) as fh:
            fh.write(b"ok")
        return True
    except OSError:
        return False


def _dir_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".vtg-doctor-", delete=True):
            pass
        return True
    except OSError:
        return False


def run_doctor(output_directory: str | None = None) -> dict[str, Any]:
    """Perform all doctor checks (section 6.3) and return a structured report."""
    checks: list[dict[str, Any]] = []

    py_ok = sys.version_info >= MIN_PYTHON
    checks.append(
        {
            "name": "python",
            "ok": py_ok,
            "detail": f"Python {sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}",
            "remediation": None if py_ok else "Upgrade to Python 3.10 or later.",
        }
    )

    ffmpeg = find_executable("ffmpeg")
    ffprobe = find_executable("ffprobe")
    platform_key = sys.platform if sys.platform in INSTALL_GUIDANCE else "linux"
    install_hint = INSTALL_GUIDANCE[platform_key]

    # The optional yt-dlp version probe costs ~150ms -- yt-dlp's shebang starts a
    # whole fresh CPython and imports the yt_dlp package just to print one string
    # -- and nothing below it feeds that probe. Start it now so it overlaps the
    # ffmpeg capability probes instead of running after them. daemon=True so a
    # wedged yt-dlp can never delay interpreter exit or Ctrl-C (section 16);
    # doctor writes no temp artifacts, so there is nothing for it to leave behind.
    ytdlp_path = find_ytdlp()
    ytdlp_probe: dict[str, str | None] = {"version": None}
    ytdlp_thread: threading.Thread | None = None
    if ytdlp_path:
        resolved_ytdlp = ytdlp_path

        def _probe_ytdlp() -> None:
            ytdlp_probe["version"] = ytdlp_version(resolved_ytdlp)

        ytdlp_thread = threading.Thread(target=_probe_ytdlp, daemon=True)
        ytdlp_thread.start()

    checks.append(
        {
            "name": "ffmpeg",
            "ok": ffmpeg is not None,
            "detail": ffmpeg or "not found on PATH",
            "remediation": None if ffmpeg else f"Install FFmpeg: {install_hint}",
        }
    )
    checks.append(
        {
            "name": "ffprobe",
            "ok": ffprobe is not None,
            "detail": ffprobe or "not found on PATH",
            "remediation": None
            if ffprobe
            else f"Install FFmpeg (provides ffprobe): {install_hint}",
        }
    )

    if ffmpeg:
        palettegen = _has_filter(ffmpeg, "palettegen")
        paletteuse = _has_filter(ffmpeg, "paletteuse")
        gif_enc = _has_gif_encoder(ffmpeg)
        # v0.3.0 (spec section 6.3): cropping needs `crop`, speed retiming needs
        # `setpts`, and preview frames need PNG encoding (FR-025, FR-027, FR-029).
        crop_filter = _has_filter(ffmpeg, "crop")
        setpts_filter = _has_filter(ffmpeg, "setpts")
        png_enc = _has_encoder(ffmpeg, "png")
    else:
        palettegen = paletteuse = gif_enc = False
        crop_filter = setpts_filter = png_enc = False

    checks.append(
        {
            "name": "palettegen_filter",
            "ok": palettegen,
            "detail": "available" if palettegen else "missing",
            "remediation": None
            if palettegen
            else "Install an FFmpeg build with the palettegen filter.",
        }
    )
    checks.append(
        {
            "name": "paletteuse_filter",
            "ok": paletteuse,
            "detail": "available" if paletteuse else "missing",
            "remediation": None
            if paletteuse
            else "Install an FFmpeg build with the paletteuse filter.",
        }
    )
    checks.append(
        {
            "name": "gif_encoder",
            "ok": gif_enc,
            "detail": "available" if gif_enc else "missing",
            "remediation": None if gif_enc else "Install an FFmpeg build with GIF encoding.",
        }
    )
    for name, ok, what in (
        ("crop_filter", crop_filter, "the crop filter"),
        ("setpts_filter", setpts_filter, "the setpts filter"),
        ("png_encoder", png_enc, "PNG encoding"),
    ):
        checks.append(
            {
                "name": name,
                "ok": ok,
                "detail": "available" if ok else "missing",
                "remediation": None if ok else f"Install an FFmpeg build with {what}.",
            }
        )

    temp_ok = _tempdir_writable()
    checks.append(
        {
            "name": "temp_writable",
            "ok": temp_ok,
            "detail": tempfile.gettempdir(),
            "remediation": None if temp_ok else "Ensure the temporary directory is writable.",
        }
    )

    # Optional yt-dlp adapter (FR-022, spec section 6.3). Its absence MUST NOT be
    # a failure, so this check is always ok=True and never gates `healthy`; the
    # presence/version is reported for the agent's information.
    if ytdlp_thread is not None:
        ytdlp_thread.join(timeout=_YTDLP_JOIN_SECONDS)
    ytdlp_ver = ytdlp_probe["version"]
    checks.append(
        {
            "name": "ytdlp_adapter",
            "ok": True,
            "optional": True,
            "detail": (
                f"yt-dlp {ytdlp_ver} ({ytdlp_path})"
                if ytdlp_path
                else "not installed (optional; only needed for --remote-adapter ytdlp)"
            ),
            "remediation": None,
        }
    )

    if output_directory is not None:
        out_ok = _dir_writable(output_directory)
        checks.append(
            {
                "name": "output_writable",
                "ok": out_ok,
                "detail": output_directory,
                "remediation": None if out_ok else "Ensure the output directory is writable.",
            }
        )

    healthy = all(c["ok"] for c in checks)
    return {
        "healthy": healthy,
        "checks": checks,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "ytdlp": {
            "available": ytdlp_path is not None,
            "path": ytdlp_path,
            "version": ytdlp_ver,
        },
        "installGuidance": None if (ffmpeg and ffprobe) else install_hint,
    }


def require_ffmpeg_tools() -> dict[str, str]:
    """Return {ffmpeg, ffprobe} paths or raise DEPENDENCY_MISSING (exit 3)."""
    ffmpeg = find_executable("ffmpeg")
    ffprobe = find_executable("ffprobe")
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if not ffprobe:
        missing.append("ffprobe")
    if missing:
        platform_key = sys.platform if sys.platform in INSTALL_GUIDANCE else "linux"
        raise errors.EngineError(
            errors.DEPENDENCY_MISSING,
            f"Required executable(s) not found: {', '.join(missing)}.",
            exit_code=errors.EXIT_DEPENDENCY_MISSING,
            status=errors.STATUS_DEPENDENCY_MISSING,
            stage="dependency",
            remediation=f"Install FFmpeg: {INSTALL_GUIDANCE[platform_key]}",
            details={"missing": missing},
        )
    # Both are non-None here: any missing tool raised above.
    assert ffmpeg is not None and ffprobe is not None
    return {"ffmpeg": ffmpeg, "ffprobe": ffprobe}


def require_ytdlp() -> str:
    """Return the ``yt-dlp`` path or raise YTDLP_MISSING (exit 3, FR-022).

    The adapter is never installed by the engine; the caller requested it via
    ``--remote-adapter ytdlp`` but the executable was not detected.
    """
    path = find_ytdlp()
    if path is None:
        raise errors.EngineError(
            errors.YTDLP_MISSING,
            "The yt-dlp adapter was requested (--remote-adapter ytdlp) but yt-dlp "
            "was not found on PATH.",
            exit_code=errors.EXIT_DEPENDENCY_MISSING,
            status=errors.STATUS_DEPENDENCY_MISSING,
            stage="remote",
            remediation="Install yt-dlp (e.g. 'pipx install yt-dlp') and re-run.",
            details={"missing": ["yt-dlp"]},
        )
    return path
