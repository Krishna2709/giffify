"""Remote source acquisition unit tests (spec FR-018..023, SEC-012..017).

Hermetic: no public internet. Network-path tests use a loopback ``http.server``
bound to 127.0.0.1, whose loopback address is explicitly approved for the
fixture (spec section 22.6, SEC-014). SSRF-block and gating tests assert that no
socket is opened at all.
"""

import contextlib
import glob
import http.server
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import cleanup, dependencies, errors, remote
from vtg.progress import ProgressReporter

# A signed-URL-style token that must never appear in any output (SEC-015).
SECRET_TOKEN = "SIGVALUEwontappearANYWHERE1234567890"
SECRET_USERINFO = "aladdin:opensesame"


# ---------------------------------------------------------------------------
# Loopback HTTP server helper
# ---------------------------------------------------------------------------
class _LoopbackServer:
    """A configurable loopback HTTP server for direct-download tests."""

    def __init__(self, responder: Any) -> None:
        self._responder = responder
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def do_GET(self) -> None:
                with contextlib.suppress(Exception):
                    parent._responder(self)

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str = "/media.mp4", query: str = "") -> str:
        q = f"?{query}" if query else ""
        return f"http://127.0.0.1:{self.port}{path}{q}"

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.httpd.shutdown()
        with contextlib.suppress(Exception):
            self.httpd.server_close()
        self._thread.join(timeout=2.0)


def _serve_bytes(body: bytes, *, content_length: bool = True, status: int = 200) -> Any:
    def responder(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(status)
        if content_length:
            h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    return responder


LOOPBACK_APPROVED = frozenset({"127.0.0.1"})


class _RemoteTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._before = self._remote_dirs()

    def tearDown(self) -> None:
        # Best-effort removal of any temp dir a successful acquisition returned;
        # failure cleanup is asserted explicitly in each test via
        # ``assertNoRemoteLeak``.
        for path in self._remote_dirs() - self._before:
            cleanup.remove_paths([path])

    def assertNoRemoteLeak(self) -> None:
        self.assertEqual(
            self._remote_dirs() - self._before,
            set(),
            "a failed remote acquisition left a temp dir behind",
        )

    @staticmethod
    def _remote_dirs() -> set:
        return set(glob.glob(os.path.join(tempfile.gettempdir(), "vtg-remote-*")))

    def _server(self, responder: Any) -> _LoopbackServer:
        srv = _LoopbackServer(responder)
        self.addCleanup(srv.close)
        return srv

    def _reporter(self) -> tuple[ProgressReporter, io.StringIO]:
        stream = io.StringIO()
        return ProgressReporter(enabled=True, stream=stream), stream

    def _acquire(self, url: str, **kw: Any) -> remote.RemoteResult:
        kw.setdefault("allow_insecure_http", True)
        kw.setdefault("approved_addresses", LOOPBACK_APPROVED)
        kw.setdefault("max_download_bytes", 2147483648)
        kw.setdefault("max_download_seconds", 30)
        return remote.acquire_remote_source(url, **kw)


# ---------------------------------------------------------------------------
# Redaction (SEC-015)
# ---------------------------------------------------------------------------
class TestRedaction(unittest.TestCase):
    def test_strips_query_and_userinfo(self):
        url = f"https://{SECRET_USERINFO}@cdn.example.com/v.mp4?token={SECRET_TOKEN}&sig=abc"
        red = remote.redact_url(url)
        self.assertEqual(red, "https://cdn.example.com/v.mp4")
        self.assertNotIn(SECRET_TOKEN, red)
        self.assertNotIn("aladdin", red)
        self.assertNotIn("opensesame", red)

    def test_keeps_scheme_host_port_path(self):
        self.assertEqual(
            remote.redact_url("http://host.test:8080/a/b/c.mp4?x=1#frag"),
            "http://host.test:8080/a/b/c.mp4",
        )

    def test_drops_fragment(self):
        self.assertNotIn("frag", remote.redact_url("https://h/x#frag"))

    def test_non_url_returned_as_is(self):
        self.assertEqual(remote.redact_url("/local/file.mp4"), "/local/file.mp4")


# ---------------------------------------------------------------------------
# Enablement gate (FR-018)
# ---------------------------------------------------------------------------
class TestEnablementGate(unittest.TestCase):
    def test_permission_matrix(self):
        # (remote_sources, allow_remote) -> permitted?
        cases = {
            ("disabled", False): False,
            ("disabled", True): True,
            ("ask", False): False,  # ask is skill-layer: engine treats as disabled
            ("ask", True): True,
            ("enabled", False): True,
            ("enabled", True): True,
        }
        for (rs, allow), expected in cases.items():
            with self.subTest(remote_sources=rs, allow_remote=allow):
                self.assertEqual(remote.remote_permitted(rs, allow), expected)

    def test_disabled_raises_remote_disabled_with_no_socket(self):
        # FR-018 / §12.8: REMOTE_DISABLED, exit 8, status remote_disabled, and
        # ZERO network I/O -- prove no socket is ever constructed or connected.
        with (
            mock.patch.object(remote.socket, "getaddrinfo", side_effect=AssertionError("resolved")),
            mock.patch.object(
                remote.socket, "create_connection", side_effect=AssertionError("connected")
            ),
            mock.patch.object(remote.socket, "socket", side_effect=AssertionError("socket()")),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            remote.ensure_remote_permitted(
                f"https://h/v.mp4?token={SECRET_TOKEN}", "disabled", False
            )
        exc = ctx.exception
        self.assertEqual(exc.code, errors.REMOTE_DISABLED)
        self.assertEqual(exc.exit_code, errors.EXIT_PERMISSION)
        self.assertEqual(exc.status, errors.STATUS_REMOTE_DISABLED)
        self.assertNotIn(SECRET_TOKEN, exc.message)  # redacted

    def test_ask_treated_as_disabled_without_flag(self):
        with self.assertRaises(errors.EngineError) as ctx:
            remote.ensure_remote_permitted("https://h/v.mp4", "ask", False)
        self.assertEqual(ctx.exception.code, errors.REMOTE_DISABLED)

    def test_allow_remote_overrides_disabled(self):
        # Permitted -> ensure_remote_permitted does not raise.
        remote.ensure_remote_permitted("https://h/v.mp4", "disabled", True)
        remote.ensure_remote_permitted("https://h/v.mp4", "ask", True)
        remote.ensure_remote_permitted("https://h/v.mp4", "enabled", False)


# ---------------------------------------------------------------------------
# Scheme allowlist (SEC-013)
# ---------------------------------------------------------------------------
class TestSchemeAllowlist(_RemoteTestBase):
    def _assert_scheme_rejected(self, url: str, **kw: Any) -> errors.EngineError:
        with (
            mock.patch.object(
                remote.socket, "create_connection", side_effect=AssertionError("connected")
            ),
            mock.patch.object(remote.socket, "getaddrinfo", side_effect=AssertionError("resolved")),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            remote.acquire_remote_source(
                url,
                allow_insecure_http=kw.get("allow_insecure_http", False),
                max_download_bytes=1000,
                max_download_seconds=5,
            )
        return ctx.exception

    def test_file_scheme_rejected_without_open(self):
        exc = self._assert_scheme_rejected("file:///etc/passwd")
        self.assertEqual(exc.code, errors.UNSUPPORTED_URL_SCHEME)
        self.assertEqual(exc.exit_code, errors.EXIT_INVALID_MEDIA)

    def test_other_schemes_rejected(self):
        for url in ("ftp://h/x", "s3://bucket/x", "gopher://h/x", "data:text/plain,hi"):
            with self.subTest(url=url):
                exc = self._assert_scheme_rejected(url)
                self.assertEqual(exc.code, errors.UNSUPPORTED_URL_SCHEME)

    def test_http_rejected_without_acknowledgment(self):
        exc = self._assert_scheme_rejected("http://h/x.mp4", allow_insecure_http=False)
        self.assertEqual(exc.code, errors.UNSUPPORTED_URL_SCHEME)

    def test_https_scheme_allowed(self):
        # https passes the allowlist; the failure is a network error (no server),
        # NOT a scheme rejection.
        with self.assertRaises(errors.EngineError) as ctx:
            remote.acquire_remote_source(
                "https://127.0.0.1:1/x.mp4",
                allow_insecure_http=False,
                approved_addresses=LOOPBACK_APPROVED,
                max_download_bytes=1000,
                max_download_seconds=3,
            )
        self.assertEqual(ctx.exception.code, errors.REMOTE_DOWNLOAD_FAILED)


# ---------------------------------------------------------------------------
# SSRF guard (SEC-014)
# ---------------------------------------------------------------------------
class TestSsrfAddressClassification(unittest.TestCase):
    def test_blocked_ranges(self):
        blocked = [
            "127.0.0.1",
            "127.5.5.5",
            "::1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.0.1",  # link-local
            "169.254.169.254",  # cloud metadata
            "100.100.100.200",  # Alibaba metadata
            "fe80::1",  # IPv6 link-local
            "fd00::1",  # IPv6 unique-local
            "224.0.0.1",  # multicast
            "0.0.0.0",  # unspecified
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "::ffff:10.0.0.1",  # IPv4-mapped private
            "not-an-ip",  # unparseable -> refuse
        ]
        for addr in blocked:
            with self.subTest(addr=addr):
                self.assertTrue(remote.address_is_disallowed(addr), addr)

    def test_public_addresses_allowed(self):
        for addr in ("8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"):
            with self.subTest(addr=addr):
                self.assertFalse(remote.address_is_disallowed(addr), addr)


class TestSsrfEnforcement(_RemoteTestBase):
    def test_loopback_blocked_without_approval(self):
        # No approval -> PRIVATE_NETWORK_BLOCKED before any connect (SEC-014).
        with (
            mock.patch.object(
                remote.socket, "create_connection", side_effect=AssertionError("connected")
            ),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            remote.acquire_remote_source(
                "http://127.0.0.1:9/x.mp4",
                allow_insecure_http=True,
                approved_addresses=frozenset(),
                max_download_bytes=1000,
                max_download_seconds=5,
            )
        self.assertEqual(ctx.exception.code, errors.PRIVATE_NETWORK_BLOCKED)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_PERMISSION)
        self.assertNoRemoteLeak()

    def test_any_disallowed_resolved_address_blocks(self):
        # A rebinding record mixing a public and a private address is blocked.
        def fake_getaddrinfo(host, port, *a, **k):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
            ]

        with (
            mock.patch.object(remote.socket, "getaddrinfo", side_effect=fake_getaddrinfo),
            mock.patch.object(
                remote.socket, "create_connection", side_effect=AssertionError("connected")
            ),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            remote.acquire_remote_source(
                "http://rebind.test/x.mp4",
                allow_insecure_http=True,
                approved_addresses=frozenset(),
                max_download_bytes=1000,
                max_download_seconds=5,
            )
        self.assertEqual(ctx.exception.code, errors.PRIVATE_NETWORK_BLOCKED)

    def test_connection_pinned_to_validated_ip(self):
        # DNS-rebinding resistance: the socket connects to the resolved+validated
        # address, NOT a fresh resolution of the hostname. Point a hostname at the
        # loopback server, approve 127.0.0.1, and assert create_connection got the
        # IP literal (not the hostname).
        srv = self._server(_serve_bytes(b"GIF89a-payload"))
        captured: list = []
        real_create = socket.create_connection

        def spy_create(address, *a, **k):
            captured.append(address[0])
            return real_create(address, *a, **k)

        def fake_getaddrinfo(host, port, *a, **k):
            # Resolve every lookup (the hostname, and the pinned IP literal that
            # create_connection re-resolves) to the loopback server.
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", srv.port))]

        url = f"http://pinned.test:{srv.port}/media.mp4"
        with (
            mock.patch.object(remote.socket, "getaddrinfo", side_effect=fake_getaddrinfo),
            mock.patch.object(remote.socket, "create_connection", side_effect=spy_create),
        ):
            result = self._acquire(url)
        self.assertEqual(result.bytes_downloaded, len(b"GIF89a-payload"))
        # The socket connected to the validated IP, never to the hostname.
        self.assertIn("127.0.0.1", captured)
        self.assertNotIn("pinned.test", captured)


# ---------------------------------------------------------------------------
# Download mechanics (FR-020/FR-021/SEC-016)
# ---------------------------------------------------------------------------
class TestDownload(_RemoteTestBase):
    def test_successful_download_and_progress(self):
        body = b"\x00\x01\x02" * 4096
        srv = self._server(_serve_bytes(body))
        reporter, stream = self._reporter()
        result = self._acquire(srv.url(), reporter=reporter)
        self.assertEqual(result.adapter, "direct")
        self.assertEqual(result.bytes_downloaded, len(body))
        self.assertTrue(os.path.isfile(result.local_path))
        with open(result.local_path, "rb") as fh:
            self.assertEqual(fh.read(), body)
        # At least one download-stage progress event (spec 13.3).
        events = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
        dl = [
            e for e in events if e.get("event") == "stage_progress" and e.get("stage") == "download"
        ]
        self.assertTrue(dl)
        self.assertIn("bytesReceived", dl[-1])

    def test_size_ceiling_enforced_on_received_bytes(self):
        # No declared Content-Length: the ceiling is enforced on bytes received.
        body = b"x" * 5000
        srv = self._server(_serve_bytes(body, content_length=False))
        with self.assertRaises(errors.EngineError) as ctx:
            remote.acquire_remote_source(
                srv.url(),
                allow_insecure_http=True,
                approved_addresses=LOOPBACK_APPROVED,
                max_download_bytes=1000,
                max_download_seconds=10,
            )
        self.assertEqual(ctx.exception.code, errors.REMOTE_TOO_LARGE)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_RESOURCE_LIMIT)
        self.assertNoRemoteLeak()  # no partial file remains

    def test_size_ceiling_advisory_content_length(self):
        srv = self._server(_serve_bytes(b"y" * 5000))  # Content-Length: 5000
        with self.assertRaises(errors.EngineError) as ctx:
            remote.acquire_remote_source(
                srv.url(),
                allow_insecure_http=True,
                approved_addresses=LOOPBACK_APPROVED,
                max_download_bytes=1000,
                max_download_seconds=10,
            )
        self.assertEqual(ctx.exception.code, errors.REMOTE_TOO_LARGE)
        self.assertNoRemoteLeak()

    def test_timeout_enforced(self):
        def slow(h):
            h.send_response(200)
            h.send_header("Content-Length", "100000")
            h.end_headers()
            h.wfile.write(b"start")
            h.wfile.flush()
            time.sleep(2.0)  # hold the connection past the client's wall-clock

        srv = self._server(slow)
        with self.assertRaises(errors.EngineError) as ctx:
            remote.acquire_remote_source(
                srv.url(),
                allow_insecure_http=True,
                approved_addresses=LOOPBACK_APPROVED,
                max_download_bytes=1000000,
                max_download_seconds=1,
            )
        self.assertEqual(ctx.exception.code, errors.REMOTE_DOWNLOAD_FAILED)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_REMOTE_FAILURE)
        self.assertNoRemoteLeak()

    def test_http_error_status(self):
        srv = self._server(_serve_bytes(b"nope", status=404))
        with self.assertRaises(errors.EngineError) as ctx:
            self._acquire(srv.url())
        self.assertEqual(ctx.exception.code, errors.REMOTE_DOWNLOAD_FAILED)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_REMOTE_FAILURE)
        self.assertNoRemoteLeak()

    def test_truncated_download(self):
        def truncated(h):
            h.send_response(200)
            h.send_header("Content-Length", "1000")  # claims 1000
            h.end_headers()
            h.wfile.write(b"z" * 400)  # sends only 400, then closes -> EOF

        srv = self._server(truncated)
        with self.assertRaises(errors.EngineError) as ctx:
            self._acquire(srv.url())
        self.assertEqual(ctx.exception.code, errors.REMOTE_DOWNLOAD_FAILED)
        self.assertNoRemoteLeak()

    def test_cancellation_removes_partial(self):
        srv = self._server(_serve_bytes(b"data" * 1000))
        cancel = threading.Event()
        cancel.set()  # already cancelled -> aborts on the first stream check
        with self.assertRaises(errors.CancelledError):
            remote.acquire_remote_source(
                srv.url(),
                allow_insecure_http=True,
                approved_addresses=LOOPBACK_APPROVED,
                max_download_bytes=1000000,
                max_download_seconds=10,
                cancel_event=cancel,
            )
        self.assertNoRemoteLeak()

    def test_redirect_followed(self):
        payload = b"redirected-body-data"

        def responder(h):
            if h.path.startswith("/start"):
                h.send_response(302)
                h.send_header("Location", "/final.mp4")
                h.send_header("Content-Length", "0")
                h.end_headers()
                return
            h.send_response(200)
            h.send_header("Content-Length", str(len(payload)))
            h.end_headers()
            h.wfile.write(payload)

        srv = self._server(responder)
        result = self._acquire(srv.url("/start"))
        self.assertEqual(result.bytes_downloaded, len(payload))

    def test_redirect_to_private_address_blocked(self):
        def responder(h):
            h.send_response(302)
            h.send_header("Location", "http://10.0.0.5/secret.mp4")
            h.send_header("Content-Length", "0")
            h.end_headers()

        srv = self._server(responder)
        with (
            mock.patch.object(
                remote.socket, "create_connection", wraps=socket.create_connection
            ) as spy,
            self.assertRaises(errors.EngineError) as ctx,
        ):
            self._acquire(srv.url("/start"))
        self.assertEqual(ctx.exception.code, errors.PRIVATE_NETWORK_BLOCKED)
        # Never connected to the private redirect target.
        for call in spy.call_args_list:
            self.assertNotIn("10.0.0.5", call.args[0])
        self.assertNoRemoteLeak()

    def test_free_disk_precheck(self):
        srv = self._server(_serve_bytes(b"a" * 500))  # Content-Length: 500

        with (
            mock.patch.object(remote, "free_disk_bytes", return_value=10),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            self._acquire(srv.url())
        self.assertEqual(ctx.exception.code, errors.RESOURCE_LIMIT_EXCEEDED)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_RESOURCE_LIMIT)
        self.assertNoRemoteLeak()


# ---------------------------------------------------------------------------
# DRM detection (SEC-017)
# ---------------------------------------------------------------------------
class TestDrm(_RemoteTestBase):
    def test_detect_drm_bytes_positive(self):
        for marker in (
            b"....pssh....",
            b"....tenc....",
            b"<MPD><ContentProtection/></MPD>",
            b"#EXT-X-KEY:METHOD=SAMPLE-AES,URI=skd://x",
            b"prefix\xed\xef\x8b\xa9\x79\xd6\x4a\xce\xa3\xc8\x27\xdc\xd5\x1d\x21\xed",
        ):
            with self.subTest(marker=marker[:12]):
                self.assertTrue(remote.detect_drm_bytes(marker))

    def test_detect_drm_bytes_negative(self):
        for ordinary in (b"GIF89a....", b"\x00\x00\x00\x18ftypmp42", b"just some text"):
            with self.subTest(ordinary=ordinary[:8]):
                self.assertFalse(remote.detect_drm_bytes(ordinary))

    def test_url_drm_marker(self):
        self.assertTrue(remote.url_has_drm_marker("https://h/video.ism"))
        self.assertTrue(remote.url_has_drm_marker("https://h/x.isml?y=1"))
        self.assertFalse(remote.url_has_drm_marker("https://h/video.mp4"))

    def test_drm_url_rejected_before_download(self):
        with (
            mock.patch.object(
                remote.socket, "create_connection", side_effect=AssertionError("connected")
            ),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            remote.acquire_remote_source(
                "https://h/stream.ism",
                max_download_bytes=1000,
                max_download_seconds=5,
            )
        self.assertEqual(ctx.exception.code, errors.DRM_PROTECTED)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_MEDIA)

    def test_drm_content_rejected_mid_download(self):
        srv = self._server(_serve_bytes(b"\x00\x00\x00\x20pssh" + b"\x00" * 100))
        with self.assertRaises(errors.EngineError) as ctx:
            self._acquire(srv.url())
        self.assertEqual(ctx.exception.code, errors.DRM_PROTECTED)
        self.assertNoRemoteLeak()


# ---------------------------------------------------------------------------
# Redaction across the whole acquisition (SEC-015)
# ---------------------------------------------------------------------------
class TestRedactionEndToEnd(_RemoteTestBase):
    def test_token_never_appears_on_success_path(self):
        body = b"payload-bytes"
        srv = self._server(_serve_bytes(body))
        reporter, stream = self._reporter()
        url = srv.url("/v.mp4", query=f"token={SECRET_TOKEN}&sig=deadbeef")
        result = self._acquire(url, reporter=reporter)
        # The request DID carry the query (server returned the body).
        self.assertEqual(result.bytes_downloaded, len(body))
        # ...but the token is nowhere in any output path.
        self.assertNotIn(SECRET_TOKEN, result.redacted_url)
        self.assertNotIn(SECRET_TOKEN, json.dumps(result.to_public()))
        self.assertNotIn(SECRET_TOKEN, stream.getvalue())
        for w in result.warnings:
            self.assertNotIn(SECRET_TOKEN, w)

    def test_token_never_appears_on_failure_path(self):
        srv = self._server(_serve_bytes(b"nope", status=403))
        url = srv.url("/v.mp4", query=f"token={SECRET_TOKEN}")
        with self.assertRaises(errors.EngineError) as ctx:
            self._acquire(url)
        blob = json.dumps(ctx.exception.to_dict())
        self.assertNotIn(SECRET_TOKEN, blob)
        self.assertNotIn(SECRET_TOKEN, ctx.exception.message)


# ---------------------------------------------------------------------------
# Retention (FR-020)
# ---------------------------------------------------------------------------
class TestRetention(_RemoteTestBase):
    def test_retained_reports_path(self):
        srv = self._server(_serve_bytes(b"keepme"))
        result = self._acquire(srv.url())
        result.retained = True
        pub = result.to_public()
        self.assertTrue(pub["retained"])
        self.assertEqual(pub["path"], result.local_path)

    def test_not_retained_reports_no_path(self):
        srv = self._server(_serve_bytes(b"tmp"))
        result = self._acquire(srv.url())
        pub = result.to_public()
        self.assertFalse(pub["retained"])
        self.assertIsNone(pub["path"])


# ---------------------------------------------------------------------------
# http acknowledgment warning (SEC-013)
# ---------------------------------------------------------------------------
class TestHttpWarning(_RemoteTestBase):
    def test_http_download_emits_unencrypted_warning(self):
        srv = self._server(_serve_bytes(b"cleartext"))
        result = self._acquire(srv.url(), allow_insecure_http=True)
        self.assertTrue(any("unencrypted" in w.lower() for w in result.warnings))


# ---------------------------------------------------------------------------
# yt-dlp adapter (FR-022)
# ---------------------------------------------------------------------------
class TestYtdlp(_RemoteTestBase):
    def test_require_ytdlp_missing(self):
        with (
            mock.patch.object(dependencies, "find_ytdlp", return_value=None),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            dependencies.require_ytdlp()
        self.assertEqual(ctx.exception.code, errors.YTDLP_MISSING)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_DEPENDENCY_MISSING)
        self.assertEqual(ctx.exception.status, errors.STATUS_DEPENDENCY_MISSING)

    def test_adapter_missing_yields_ytdlp_missing_no_temp(self):
        with (
            mock.patch.object(dependencies, "find_ytdlp", return_value=None),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            remote.acquire_remote_source(
                "https://videos.example.com/watch?v=abc",
                adapter="ytdlp",
                max_download_bytes=1000,
                max_download_seconds=5,
            )
        self.assertEqual(ctx.exception.code, errors.YTDLP_MISSING)
        self.assertNoRemoteLeak()

    def test_adapter_present_downloads_via_subprocess(self):
        # Simulate a present yt-dlp that writes an output file (no shell, arg array).
        def fake_run(cmd, **kw):
            self.assertNotIn("shell", kw)  # never shell=True
            self.assertIn("-o", cmd)
            template = cmd[cmd.index("-o") + 1]
            out = template.replace("%(ext)s", "mp4")
            with open(out, "wb") as fh:
                fh.write(b"YTDLP-DOWNLOADED-MEDIA")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(dependencies, "require_ytdlp", return_value="/usr/bin/yt-dlp"),
            mock.patch.object(remote.subprocess, "run", side_effect=fake_run),
        ):
            result = self._acquire_ytdlp("https://videos.example.com/watch?v=abc")
        self.assertEqual(result.adapter, "ytdlp")
        self.assertEqual(result.bytes_downloaded, len(b"YTDLP-DOWNLOADED-MEDIA"))
        self.assertTrue(os.path.isfile(result.local_path))

    def test_adapter_reports_drm_from_stderr(self):
        def fake_run(cmd, **kw):
            return mock.Mock(returncode=1, stdout="", stderr="ERROR: This video is DRM protected")

        with (
            mock.patch.object(dependencies, "require_ytdlp", return_value="/usr/bin/yt-dlp"),
            mock.patch.object(remote.subprocess, "run", side_effect=fake_run),
            self.assertRaises(errors.EngineError) as ctx,
        ):
            remote.acquire_remote_source(
                "https://videos.example.com/watch?v=abc",
                adapter="ytdlp",
                max_download_bytes=1000000,
                max_download_seconds=5,
            )
        self.assertEqual(ctx.exception.code, errors.DRM_PROTECTED)
        self.assertNoRemoteLeak()

    def _acquire_ytdlp(self, url: str) -> remote.RemoteResult:
        return remote.acquire_remote_source(
            url, adapter="ytdlp", max_download_bytes=1000000, max_download_seconds=5
        )


# ---------------------------------------------------------------------------
# doctor yt-dlp reporting (spec section 6.3)
# ---------------------------------------------------------------------------
class TestDoctorYtdlp(unittest.TestCase):
    def test_ytdlp_absent_is_not_a_failure(self):
        with mock.patch.object(dependencies, "find_ytdlp", return_value=None):
            report = dependencies.run_doctor()
        self.assertFalse(report["ytdlp"]["available"])
        self.assertIsNone(report["ytdlp"]["version"])
        # The optional adapter check must be ok=True and must not gate health.
        adapter = next(c for c in report["checks"] if c["name"] == "ytdlp_adapter")
        self.assertTrue(adapter["ok"])
        self.assertTrue(adapter.get("optional"))

    def test_ytdlp_present_reports_version(self):
        with (
            mock.patch.object(dependencies, "find_ytdlp", return_value="/opt/yt-dlp"),
            mock.patch.object(dependencies, "ytdlp_version", return_value="2025.01.01"),
        ):
            report = dependencies.run_doctor()
        self.assertTrue(report["ytdlp"]["available"])
        self.assertEqual(report["ytdlp"]["version"], "2025.01.01")
        self.assertEqual(report["ytdlp"]["path"], "/opt/yt-dlp")


if __name__ == "__main__":
    unittest.main()
