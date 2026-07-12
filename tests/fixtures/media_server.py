"""Loopback HTTP media-server fixtures for the remote-source suites (spec §22.6).

These fixtures let the integration / security / acceptance suites exercise the
engine's remote acquisition path (FR-018..023, SEC-012..017) end to end as a
subprocess, WITHOUT ever touching the public internet: every server binds to the
loopback interface (127.0.0.1) on an ephemeral port, and the loopback block of
SEC-014 is explicitly approved for the fixture via ``--allow-remote-address``.

Contents
--------
* :class:`MediaHTTPServer` -- a threaded, configurable loopback HTTP server that
  serves generated test media and can, per request path, simulate:
    - a normal ``Content-Length`` download (``/media.mp4`` or ``/ok/...``),
    - an unknown-length / connection-close download (``/nolength/...``),
    - a chunked ``Transfer-Encoding: chunked`` download (``/chunked/...``),
    - an oversized body streamed with no declared length (``/oversize/...``),
      to force the size ceiling to trip on *received* bytes (SEC-016),
    - a truncated transfer -- large declared ``Content-Length``, few bytes sent,
      then close (``/truncate/...``),
    - a slow-drip that holds the connection open far past any test timeout
      (``/drip/...``) so ``maxDownloadSeconds`` fires deterministically,
    - a redirect, including a redirect to a private/loopback address
      (``/redirect/...?to=<url>``), so the SSRF allowlist re-check is exercised,
    - an HTTP error status (``/error/...?status=<code>``),
    - an optional lying ``Content-Type`` (``?ctype=<mime>`` on any route),
    - arbitrary signed-URL-style query parameters (ignored by the handler but
      recorded, so redaction can be asserted against the transmitted query).
* :class:`ConnectionListener` -- a bare loopback TCP listener that records
  whether *anything* ever connected. Used to prove the engine performs ZERO
  network I/O on a path that must not fetch (disabled-by-default, SSRF block,
  scheme/DRM rejection) or that a hostile downloaded playlist cannot open a
  further connection (conversion isolation, SEC-012/AC-0.2.13).
* :class:`RemoteEngineTestCase` -- an :class:`EngineTestCase` specialization that
  adds server/listener factories, config writing, the standard remote-flag trio,
  a ``vtg-remote-*`` leak assertion, an opt-in sweep for a deliberately retained
  download, and a yt-dlp-absent environment builder.

Everything here is standard-library only and fully type-annotated (fixtures are
held to the annotated bar even though test methods may stay bare).
"""

from __future__ import annotations

import contextlib
import glob
import http.server
import json
import os
import socket
import tempfile
import threading
import urllib.parse
from typing import Any, cast

from fixtures.base import FFMPEG, FFPROBE, EngineTestCase, media, rmtree_with_retry

# The single loopback host every fixture binds to, and the value tests pass to
# ``--allow-remote-address`` to approve it for the SEC-014 SSRF check.
LOOPBACK = "127.0.0.1"


# ---------------------------------------------------------------------------
# Configurable loopback media server
# ---------------------------------------------------------------------------
class _MediaHandler(http.server.BaseHTTPRequestHandler):
    """Request handler dispatching on the first path segment; see module docs."""

    # HTTP/1.1 so chunked transfer-encoding is available; framing is managed
    # explicitly per route and the engine always sends ``Connection: close``.
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Silence the default stderr access log so it never pollutes the engine
        # stderr the suites parse.
        return

    def do_GET(self) -> None:
        srv = cast("MediaHTTPServer", self.server)
        srv.record_request(self.path)
        parsed = urllib.parse.urlsplit(self.path)
        segments = [s for s in parsed.path.split("/") if s]
        route = segments[0] if segments else "ok"
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        routes = {
            "ok": self._route_ok,
            "nolength": self._route_nolength,
            "chunked": self._route_chunked,
            "oversize": self._route_oversize,
            "truncate": self._route_truncate,
            "drip": self._route_drip,
            "redirect": self._route_redirect,
            "error": self._route_error,
        }
        responder = routes.get(route, self._route_ok)
        # A client that aborts mid-transfer (size ceiling, timeout, cancellation)
        # drops the socket; the resulting write error is expected, not a fault.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError, ValueError):
            responder(srv, query)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _content_type(srv: MediaHTTPServer, query: dict[str, list[str]]) -> str:
        override = query.get("ctype")
        return override[0] if override else srv.content_type

    # -- routes ------------------------------------------------------------
    def _route_ok(self, srv: MediaHTTPServer, query: dict[str, list[str]]) -> None:
        body = srv.body
        self.send_response(200)
        self.send_header("Content-Type", self._content_type(srv, query))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route_nolength(self, srv: MediaHTTPServer, query: dict[str, list[str]]) -> None:
        # Unknown total size: no Content-Length, connection-close framing. The
        # client reads until EOF (totalBytes is null in the download events).
        body = srv.body
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", self._content_type(srv, query))
        self.end_headers()
        self.wfile.write(body)

    def _route_chunked(self, srv: MediaHTTPServer, query: dict[str, list[str]]) -> None:
        body = srv.body
        self.send_response(200)
        self.send_header("Content-Type", self._content_type(srv, query))
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        step = max(1, len(body) // 3 + 1)
        for start in range(0, len(body), step):
            chunk = body[start : start + step]
            self.wfile.write(f"{len(chunk):x}\r\n".encode())
            self.wfile.write(chunk)
            self.wfile.write(b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def _route_oversize(self, srv: MediaHTTPServer, query: dict[str, list[str]]) -> None:
        # Stream more bytes than the client's ceiling with NO declared length, so
        # the ceiling must be enforced on bytes actually received (SEC-016). Zero
        # bytes carry no DRM marker.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", self._content_type(srv, query))
        self.end_headers()
        remaining = srv.oversize_bytes
        block = b"\x00" * 65536
        while remaining > 0:
            n = min(remaining, len(block))
            self.wfile.write(block[:n])
            remaining -= n

    def _route_truncate(self, srv: MediaHTTPServer, query: dict[str, list[str]]) -> None:
        # Declare a large Content-Length but send only a few bytes, then close ->
        # a truncated/incomplete transfer (REMOTE_DOWNLOAD_FAILED, exit 14).
        declared = int(query.get("declared", ["1000000"])[0])
        sent = int(query.get("sent", ["400"])[0])
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", self._content_type(srv, query))
        self.send_header("Content-Length", str(declared))
        self.end_headers()
        self.wfile.write(b"z" * sent)

    def _route_drip(self, srv: MediaHTTPServer, query: dict[str, list[str]]) -> None:
        # Send a few bytes then hold the connection open FAR longer than any test
        # timeout so the client's wall-clock download limit fires deterministically
        # (no Content-Length; the client blocks on the read). ``stop_event`` is
        # set by ``close()`` so teardown never waits the full hold.
        self.send_response(200)
        self.send_header("Content-Type", self._content_type(srv, query))
        self.end_headers()
        self.wfile.write(b"drip")
        self.wfile.flush()
        srv.stop_event.wait(timeout=srv.drip_hold)

    def _route_redirect(self, srv: MediaHTTPServer, query: dict[str, list[str]]) -> None:
        status = int(query.get("status", ["302"])[0])
        location = query.get("to", ["/ok/final.mp4"])[0]
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _route_error(self, srv: MediaHTTPServer, query: dict[str, list[str]]) -> None:
        status = int(query.get("status", ["404"])[0])
        body = b"error"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MediaHTTPServer(http.server.ThreadingHTTPServer):
    """A threaded loopback HTTP server that serves ``body`` with per-route modes.

    Each request is handled on a daemon thread so a held ``/drip`` connection can
    never block teardown. Construct with the media (or hostile) payload to serve;
    select a behavior by the first path segment of :meth:`url`.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        *,
        body: bytes = b"",
        oversize_bytes: int = 512 * 1024,
        content_type: str = "video/mp4",
        drip_hold: float = 60.0,
    ) -> None:
        self.body = body
        self.oversize_bytes = oversize_bytes
        self.content_type = content_type
        self.drip_hold = drip_hold
        self.stop_event = threading.Event()
        self._requests: list[str] = []
        self._lock = threading.Lock()
        super().__init__((LOOPBACK, 0), _MediaHandler)
        self.port: int = self.server_address[1]
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    # -- request recording -------------------------------------------------
    def record_request(self, path: str) -> None:
        with self._lock:
            self._requests.append(path)

    def received_requests(self) -> list[str]:
        with self._lock:
            return list(self._requests)

    def received_any(self) -> bool:
        with self._lock:
            return bool(self._requests)

    # -- URL construction --------------------------------------------------
    def url(self, path: str = "/media.mp4", *, query: str = "") -> str:
        suffix = f"?{query}" if query else ""
        return f"http://{LOOPBACK}:{self.port}{path}{suffix}"

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self.stop_event.set()
        with contextlib.suppress(Exception):
            self.shutdown()
        with contextlib.suppress(Exception):
            self.server_close()
        self._thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Bare connection listener (proves zero network access)
# ---------------------------------------------------------------------------
class ConnectionListener:
    """A loopback TCP listener that records whether anyone ever connected.

    A test embeds :meth:`url` in an input that MUST NOT be fetched and asserts
    ``connected`` stays clear, proving the engine performed no network I/O.
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind((LOOPBACK, 0))
        self._sock.listen(1)
        self.port: int = self._sock.getsockname()[1]
        self.connected = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        self._sock.settimeout(5.0)
        try:
            conn, _ = self._sock.accept()
            self.connected.set()
            conn.close()
        except (TimeoutError, OSError):
            pass

    def url(self, path: str = "/v.mp4", *, scheme: str = "http") -> str:
        return f"{scheme}://{LOOPBACK}:{self.port}{path}"

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()
        self._thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _remote_temp_dirs() -> set[str]:
    """Secure download dirs the engine creates use the ``vtg-remote-`` prefix."""
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "vtg-remote-*")))


HOSTILE_M3U8 = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:4.0,\n{url}\n#EXT-X-ENDLIST\n"


# ---------------------------------------------------------------------------
# Base test case for the remote-source suites
# ---------------------------------------------------------------------------
class RemoteEngineTestCase(EngineTestCase):
    """EngineTestCase specialization for the remote-source suites.

    Inherits the shared media dir (class) + fresh project dir (per test) and adds
    loopback server/listener factories, config writing, the standard remote-flag
    trio, and cleanup helpers. The base fixture's engine-temp-leak assertion also
    counts ``vtg-remote-*`` dirs, so a deliberately retained ``--keep-remote-source``
    download would trip it; :meth:`allow_retained_remote` opts a single test out by
    sweeping the retained dir before the base assertion runs.
    """

    def setUp(self) -> None:
        super().setUp()
        self._allow_retained_remote = False
        self._remote_snapshot = _remote_temp_dirs()

    def tearDown(self) -> None:
        if self._allow_retained_remote:
            # Remove any deliberately retained download so the base leak
            # assertion (which counts vtg-remote-*) sees a clean temp dir.
            for path in _remote_temp_dirs() - self._remote_snapshot:
                rmtree_with_retry(path)
        super().tearDown()

    # -- factories ---------------------------------------------------------
    def media_server(self, *, body: bytes = b"", **kw: Any) -> MediaHTTPServer:
        srv = MediaHTTPServer(body=body, **kw)
        self.addCleanup(srv.close)
        return srv

    def listener(self) -> ConnectionListener:
        lis = ConnectionListener()
        self.addCleanup(lis.close)
        return lis

    # -- config / flags ----------------------------------------------------
    def write_config(self, **fields: Any) -> None:
        data: dict[str, Any] = {"schemaVersion": 1}
        data.update(fields)
        path = os.path.join(self.project, ".video-to-gif.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def remote_flags(self, *, insecure_http: bool = True, approve: bool = True) -> list[str]:
        """The standard opt-in trio for the loopback fixture (SEC-013/SEC-014).

        ``--allow-remote`` enables acquisition for the run (FR-018);
        ``--allow-remote-address 127.0.0.1`` approves the loopback address for the
        SSRF check (SEC-014); ``--allow-insecure-http`` acknowledges the fixture's
        unencrypted http transport (SEC-013).
        """
        flags = ["--allow-remote"]
        if approve:
            flags += ["--allow-remote-address", LOOPBACK]
        if insecure_http:
            flags += ["--allow-insecure-http"]
        return flags

    # -- assertions --------------------------------------------------------
    def assert_no_remote_temp(self) -> None:
        leaked = _remote_temp_dirs() - self._remote_snapshot
        self.assertEqual(
            leaked, set(), f"remote acquisition leaked a temporary download dir: {sorted(leaked)}"
        )

    def allow_retained_remote(self) -> None:
        self._allow_retained_remote = True

    # -- environment builders ---------------------------------------------
    def env_without_ytdlp(self) -> dict[str, str]:
        """A subprocess env in which yt-dlp is guaranteed absent but ffmpeg present.

        PATH is stripped to an empty directory so ``shutil.which('yt-dlp')`` fails
        regardless of the host, while ``VTG_FFMPEG``/``VTG_FFPROBE`` overrides keep
        the ffmpeg tools resolvable. This makes the YTDLP_MISSING (exit 3) path
        deterministic on machines that DO have yt-dlp installed.
        """
        env = os.environ.copy()
        # An empty bin dir INSIDE the per-test project so PATH holds no yt-dlp;
        # placed here (not the system temp) so it is not mistaken for a leaked
        # engine ``vtg-*`` temp dir and is removed with the project in tearDown.
        empty = os.path.join(self.project, "empty-bin")
        os.makedirs(empty, exist_ok=True)
        env["PATH"] = empty
        if FFMPEG:
            env["VTG_FFMPEG"] = FFMPEG
        if FFPROBE:
            env["VTG_FFPROBE"] = FFPROBE
        env.pop("VTG_YT-DLP", None)
        return env

    # -- media -------------------------------------------------------------
    @classmethod
    def make_media_bytes(
        cls,
        name: str = "remote-src.mp4",
        *,
        size: str = "320x240",
        fps: int = 15,
        duration: float = 2.0,
    ) -> bytes:
        """Generate a small valid landscape video and return its bytes to serve."""
        path = cls.media_file(name)
        media.generate_landscape(path, size=size, fps=fps, duration=duration)
        with open(path, "rb") as fh:
            return fh.read()
