"""Shared base TestCase and assertion helpers for the video-to-gif suites.

Design (per CLAUDE.md testing rules and spec section 22):

  * All media is generated at runtime with :mod:`tools.generate_test_video` into
    a class-scoped ``tempfile.mkdtemp`` OUTSIDE the repository, reused across the
    tests of a class (``setUpClass``) for speed, and removed in ``tearDownClass``.
  * Each test runs the real engine as a subprocess with ``cwd`` set to a fresh
    per-test temp "project root" (an empty ``pyproject.toml`` marker) so every
    ``./output`` write lands in temp and is removed in ``tearDown`` -- nothing is
    ever written inside the repo.
  * ``tearDown`` asserts the project directory is gone and that the engine leaked
    no ``vtg-*`` temporary directories into the system temp dir.
  * Assertions are made against the structured JSON contract (spec section 13):
    status, exit code, created/failed/skipped, error codes -- never on log text.

The engine entry point is invoked exactly as an agent would::

    python3 <repo>/src/skill/video-to-gif/scripts/video_to_gif.py <cmd> ... --json
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Repository / engine path resolution
# ---------------------------------------------------------------------------
FIXTURES_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.dirname(FIXTURES_DIR)
REPO_ROOT = os.path.dirname(TESTS_DIR)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "src", "skill", "video-to-gif", "scripts")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
ENTRY = os.path.join(SCRIPTS_DIR, "video_to_gif.py")

for _p in (SCRIPTS_DIR, TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Re-exported for the suites: tests do `from fixtures.base import media`.
import generate_test_video as media  # noqa: E402, F401  (path set up above; re-export)

_FALLBACK_BIN = "/opt/homebrew/bin"


def find_tool(name: str) -> str | None:
    override = os.environ.get("VTG_" + name.upper())
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    found = shutil.which(name)
    if found:
        return found
    candidate = os.path.join(_FALLBACK_BIN, name)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def rmtree_with_retry(path: str, *, retry_seconds: float = 2.0, interval: float = 0.05) -> None:
    """Remove a directory tree the fixture created, tolerating transient locks.

    On Windows a subprocess the engine launched (ffmpeg reading the source, or
    writing into ``output/``) may hold a handle for a few milliseconds after it
    is terminated and reaped, and antivirus can hold brief locks; a single
    ``shutil.rmtree`` then leaves the fixture's own temp dir behind. Retrying in
    short sleeps until it is gone makes teardown deterministic. This guards only
    the fixture's own directories -- the engine-leak assertions
    (``_engine_temp_dirs``/``temp_gif_leftovers``) remain strict. No-op-cost on
    POSIX, where the first attempt succeeds.
    """
    deadline = time.monotonic() + retry_seconds
    while True:
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path) or time.monotonic() >= deadline:
            return
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Engine invocation result
# ---------------------------------------------------------------------------
class EngineResult:
    """Parsed result of one engine subprocess run.

    ``result`` is the single final JSON document from stdout (spec 13.1);
    ``events`` is the list of JSON Lines progress events from stderr (spec 13.3).
    """

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.result: dict[str, Any] = self._parse_final(stdout)
        self.events: list[dict[str, Any]] = self._parse_events(stderr)

    @staticmethod
    def _parse_final(stdout: str) -> dict[str, Any]:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)  # type: ignore[no-any-return]  # engine emits a JSON object
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def _parse_events(stderr: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in stderr.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                events.append(obj)
        return events

    # convenience accessors
    @property
    def status(self) -> str | None:
        return self.result.get("status")

    @property
    def error_code(self) -> str | None:
        return (self.result.get("error") or {}).get("code")

    @property
    def summary(self) -> dict[str, Any]:
        return self.result.get("summary") or {}

    @property
    def created(self) -> list[dict[str, Any]]:
        return self.result.get("created") or []

    @property
    def failed(self) -> list[dict[str, Any]]:
        return self.result.get("failed") or []


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------
@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are required for these tests")
class EngineTestCase(unittest.TestCase):
    """Base class: shared media dir (class), fresh project dir (per test)."""

    media_dir: str = ""

    # -- class media -------------------------------------------------------
    @classmethod
    def setUpClass(cls) -> None:
        if not (FFMPEG and FFPROBE):  # pragma: no cover - guarded by skipUnless
            raise unittest.SkipTest("ffmpeg/ffprobe unavailable")
        cls.media_dir = tempfile.mkdtemp(prefix="vtg-media-")
        cls.generate_media()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.media_dir:
            rmtree_with_retry(cls.media_dir)
            # The shared media dir must be outside the repo and fully removed.
            assert not os.path.exists(cls.media_dir), "class media dir not cleaned"

    @classmethod
    def generate_media(cls) -> None:
        """Override in subclasses to generate shared media into ``cls.media_dir``."""

    @classmethod
    def media_file(cls, name: str) -> str:
        return os.path.join(cls.media_dir, name)

    # -- per-test project --------------------------------------------------
    def setUp(self) -> None:
        self.project = tempfile.mkdtemp(prefix="vtg-proj-")
        # Empty pyproject.toml marker makes this temp dir the detected project root
        # (spec 9.1), keeping every engine write inside temp, never in the repo.
        with open(os.path.join(self.project, "pyproject.toml"), "w"):
            pass
        # Snapshot system-temp engine dirs so we can assert no leaks after the run.
        self._temp_snapshot = self._engine_temp_dirs()

    def tearDown(self) -> None:
        rmtree_with_retry(self.project)
        self.assertFalse(os.path.exists(self.project), "project temp dir not cleaned")
        leaked = self._engine_temp_dirs() - self._temp_snapshot
        self.assertEqual(
            leaked,
            set(),
            f"engine leaked temporary directories: {sorted(leaked)}",
        )

    @staticmethod
    def _engine_temp_dirs() -> set:
        """Engine palette temp dirs use prefix 'vtg-' (not our 'vtg-media-/proj-')."""
        out = set()
        for path in glob.glob(os.path.join(tempfile.gettempdir(), "vtg-*")):
            base = os.path.basename(path)
            if base.startswith(("vtg-media-", "vtg-proj-")):
                continue
            out.add(path)
        return out

    # -- output helpers ----------------------------------------------------
    @property
    def output_dir(self) -> str:
        return os.path.join(self.project, "output")

    def output_path(self, name: str) -> str:
        return os.path.join(self.output_dir, name)

    def list_output(self) -> list[str]:
        if not os.path.isdir(self.output_dir):
            return []
        return sorted(os.listdir(self.output_dir))

    def temp_gif_leftovers(self) -> list[str]:
        """Engine writes temp GIFs as ``.vtg-*.gif.tmp`` in the output dir."""
        if not os.path.isdir(self.output_dir):
            return []
        return sorted(glob.glob(os.path.join(self.output_dir, ".vtg-*")))

    # -- engine runners ----------------------------------------------------
    def run_engine(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: int = 180,
        add_json: bool = True,
        env: dict[str, str] | None = None,
    ) -> EngineResult:
        cwd = cwd or self.project
        argv = [sys.executable, ENTRY] + [str(a) for a in args]
        if add_json and "--json" not in argv:
            argv.append("--json")
        proc = subprocess.run(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return EngineResult(proc.returncode, proc.stdout, proc.stderr)

    def run_engine_until(
        self,
        args: list[str],
        trigger: Callable[[dict[str, Any]], bool],
        *,
        cwd: str | None = None,
        send_signal: int | None = None,
        timeout: int = 180,
    ) -> EngineResult:
        """Run the engine, and when a stderr progress event satisfies ``trigger``,
        deliver a cancellation signal to the engine process. Used for cancellation
        tests.

        The signal is platform-appropriate: POSIX uses SIGINT; Windows cannot
        deliver SIGINT to a subprocess (``send_signal`` raises ``ValueError:
        Unsupported signal: 2``), so the engine is launched in its own process
        group (``CREATE_NEW_PROCESS_GROUP``) and cancelled with
        ``CTRL_BREAK_EVENT``, which the engine receives as SIGBREAK (spec §16).
        Cancellation semantics are identical, so callers' assertions are unchanged.
        Pass ``send_signal`` to override the default for a specific platform test.
        """
        cwd = cwd or self.project
        argv = [sys.executable, ENTRY] + [str(a) for a in args]
        if "--json" not in argv:
            argv.append("--json")
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if sys.platform == "win32":
            # Own process group so CTRL_BREAK_EVENT reaches only the engine.
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            cancel_signal = signal.CTRL_BREAK_EVENT if send_signal is None else send_signal
        else:
            cancel_signal = signal.SIGINT if send_signal is None else send_signal
        proc = subprocess.Popen(argv, **popen_kwargs)
        stdout_chunks: list[str] = []

        def _drain_stdout() -> None:
            assert proc.stdout is not None
            stdout_chunks.append(proc.stdout.read())

        t = threading.Thread(target=_drain_stdout, daemon=True)
        t.start()

        stderr_lines: list[str] = []
        fired = False
        assert proc.stderr is not None
        for raw in proc.stderr:
            stderr_lines.append(raw)
            if fired:
                continue
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict) and trigger(ev):
                proc.send_signal(cancel_signal)
                fired = True
        proc.wait(timeout=timeout)
        t.join(timeout=timeout)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        return EngineResult(proc.returncode, "".join(stdout_chunks), "".join(stderr_lines))

    # -- structured-contract assertions ------------------------------------
    def assert_exit(self, res: EngineResult, code: int) -> None:
        self.assertEqual(
            res.returncode,
            code,
            f"expected exit {code}, got {res.returncode}; result={res.result}",
        )

    def assert_status(self, res: EngineResult, status: str) -> None:
        self.assertEqual(
            res.status,
            status,
            f"expected status {status!r}, got {res.status!r}; result={res.result}",
        )

    def assert_error_code(self, res: EngineResult, code: str) -> None:
        self.assertEqual(
            res.error_code,
            code,
            f"expected error code {code!r}, got {res.error_code!r}; result={res.result}",
        )

    # -- GIF inspection (real ffprobe) -------------------------------------
    def probe_gif(self, path: str) -> dict[str, Any]:
        """Return {width, height, nb_frames, avg_frame_rate, streams, has_audio}."""
        assert FFPROBE is not None  # class is skipped unless ffprobe is present
        proc = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", path],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        video: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})
        nb_raw = video.get("nb_frames")
        nb: int | None
        try:
            nb = int(nb_raw) if nb_raw is not None else None
        except (TypeError, ValueError):
            nb = None
        return {
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "nb_frames": nb,
            "avg_frame_rate": video.get("avg_frame_rate"),
            "streams": streams,
            "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        }

    def avg_fps(self, path: str) -> float:
        info = self.probe_gif(path)
        rate = info["avg_frame_rate"] or "0/1"
        num, _, den = rate.partition("/")
        den = den or "1"
        try:
            return float(num) / float(den) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0

    # -- GIF byte-level inspection (stdlib only) ---------------------------
    @staticmethod
    def parse_gif_header(path: str) -> dict[str, Any]:
        """Parse GIF header without third-party deps.

        Returns the global color-table size (a power of two, or 0 if absent), and
        NETSCAPE2.0 loop-extension presence + loop count. Note: the GIF muxer pads
        the global color table up to 256 entries regardless of the palettegen
        ``max_colors`` cap, so GCT size cannot verify the exact color limit -- it
        is only a sanity bound. Loop semantics, however, are exact.
        """
        with open(path, "rb") as fh:
            data = fh.read()
        header = data[:6]
        packed = data[10] if len(data) > 10 else 0
        gct_flag = bool(packed & 0x80)
        gct_colors = (2 ** ((packed & 0x07) + 1)) if gct_flag else 0
        netscape = b"NETSCAPE2.0" in data
        loop_count = None
        idx = data.find(b"NETSCAPE2.0")
        if idx != -1:
            sub = data[idx + 11 : idx + 16]  # 0x03 0x01 <loopLE16> 0x00
            if len(sub) >= 4:
                loop_count = sub[2] | (sub[3] << 8)
        return {
            "header": header,
            "is_gif": header.startswith(b"GIF8"),
            "gct_colors": gct_colors,
            "netscape": netscape,
            "loop_count": loop_count,
        }

    def assert_valid_gif(self, path: str) -> None:
        self.assertTrue(os.path.isfile(path), f"GIF not found: {path}")
        self.assertGreater(os.path.getsize(path), 0, "GIF is empty")
        self.assertTrue(self.parse_gif_header(path)["is_gif"], "not a GIF8x header")
