"""Security: opt-in remote source acquisition (spec §22.6, FR-018..023,
SEC-012..017). Real ffmpeg; a loopback HTTP media server and bare TCP listeners
stand in for the network -- the public internet is never touched (§22.6).

Assertions are made against the structured JSON contract (spec §13) -- status,
exit code, error code -- and against direct network/filesystem side effects
(a listener that must receive no connection; no leftover ``vtg-remote-*`` temp
dir). The single deliberate exception is the redaction test, which by design
greps ALL output streams for a signed-URL token.

Determinism: the size-ceiling and timeout conditions are forced by configuration
knobs (a tiny ``maxDownloadBytes``; a 1-second ``maxDownloadSeconds`` against a
server that drips and then holds the connection for 60 s), never by racing real
transfer speeds.
"""

import contextlib
import io
import json
import os
import sys
import unittest
from typing import ClassVar
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.media_server import HOSTILE_M3U8, LOOPBACK, RemoteEngineTestCase

# fixtures.base (imported transitively above) puts the engine scripts dir on
# sys.path, so the in-process redaction test can import the CLI module directly.
from vtg import cli as vtg_cli
from vtg import errors as vtg_errors

# A signed-URL-style token + embedded credentials that must appear NOWHERE in any
# engine output (SEC-015). They are transmitted to the origin but never echoed.
SECRET_TOKEN = "SIGtok3nVALUEmustNEVERleakANYWHERE0987654321"
SECRET_USERINFO = "aladdin:opensesame"


class TestRemoteSecurity(RemoteEngineTestCase):
    video_bytes: ClassVar[bytes]

    @classmethod
    def generate_media(cls) -> None:
        cls.video_bytes = cls.make_media_bytes(size="320x240", fps=15, duration=2.0)

    def _create(
        self,
        url: str,
        *flags: str,
        cfg: dict | None = None,
        timeout: int = 60,
        env: dict | None = None,
    ):
        if cfg is not None:
            self.write_config(**cfg)
        return self.run_engine(
            ["create", "--input", url, "--start", "0", "--end", "1", "--profile", "small", *flags],
            timeout=timeout,
            env=env,
        )

    # -- disabled by default (FR-018) -------------------------------------
    def test_disabled_by_default_rejects_with_zero_network(self):
        # A URL supplied with the default configuration is rejected with
        # REMOTE_DISABLED / exit 8 / status remote_disabled BEFORE any network
        # access. A listener bound to the URL must never receive a connection.
        lis = self.listener()
        res = self._create(lis.url("/v.mp4"))  # no remote flags, no config
        self.assert_exit(res, 8)
        self.assert_status(res, "remote_disabled")
        self.assert_error_code(res, "REMOTE_DISABLED")
        self.assertFalse(lis.connected.is_set(), "engine opened a network connection")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()

    # -- scheme allowlist (SEC-013) ---------------------------------------
    def test_ftp_scheme_rejected_without_fetch(self):
        # With remote enabled, a non-https/http scheme is rejected with
        # UNSUPPORTED_URL_SCHEME / exit 5 and is never fetched.
        lis = self.listener()
        res = self._create(
            f"ftp://{LOOPBACK}:{lis.port}/x.mp4",
            "--allow-remote",
            "--allow-remote-address",
            LOOPBACK,
        )
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_URL_SCHEME")
        self.assertFalse(lis.connected.is_set())
        self.assert_no_remote_temp()

    def test_file_scheme_rejected_without_open(self):
        # file:// is rejected with UNSUPPORTED_URL_SCHEME / exit 5 and never
        # opened (the scheme is validated before any temp dir or fs access).
        res = self._create("file:///etc/hostname", "--allow-remote")
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_URL_SCHEME")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()

    def test_http_rejected_without_insecure_ack(self):
        # http is rejected as UNSUPPORTED_URL_SCHEME unless --allow-insecure-http
        # is supplied; the server must not be contacted.
        srv = self.media_server(body=self.video_bytes)
        res = self._create(
            srv.url("/media.mp4"), "--allow-remote", "--allow-remote-address", LOOPBACK
        )
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_URL_SCHEME")
        self.assertFalse(srv.received_any(), "http URL was fetched without acknowledgment")
        self.assert_no_remote_temp()

    def test_http_allowed_with_insecure_ack_warns(self):
        # With --allow-insecure-http the download proceeds and an unencrypted
        # transfer warning is surfaced in the structured result (SEC-013).
        srv = self.media_server(body=self.video_bytes)
        res = self._create(srv.url("/media.mp4"), *self.remote_flags())
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertTrue(
            any("unencrypted" in w.lower() for w in res.result["warnings"]),
            f"expected an unencrypted-http warning; got {res.result['warnings']}",
        )
        self.assert_no_remote_temp()

    # -- SSRF (SEC-014) ----------------------------------------------------
    def test_ssrf_loopback_blocked_without_approval(self):
        # A loopback/private address is blocked with PRIVATE_NETWORK_BLOCKED /
        # exit 8 unless explicitly approved; no connection is made.
        srv = self.media_server(body=self.video_bytes)
        res = self._create(srv.url("/media.mp4"), "--allow-remote", "--allow-insecure-http")
        self.assert_exit(res, 8)
        self.assert_error_code(res, "PRIVATE_NETWORK_BLOCKED")
        self.assertFalse(srv.received_any(), "connected to an unapproved loopback address")
        self.assert_no_remote_temp()

    def test_ssrf_redirect_from_approved_to_private_blocked(self):
        # 127.0.0.1 is approved for the first hop, but a redirect to an
        # unapproved private address (10.0.0.5) is re-checked and blocked
        # (SEC-014 re-evaluates every redirect target).
        srv = self.media_server(body=self.video_bytes)
        res = self._create(
            srv.url("/redirect/go.mp4", query="to=http://10.0.0.5/secret.mp4"), *self.remote_flags()
        )
        self.assert_exit(res, 8)
        self.assert_error_code(res, "PRIVATE_NETWORK_BLOCKED")
        # The approved first hop was contacted; the private redirect target was not.
        self.assertTrue(any(r.startswith("/redirect/go.mp4") for r in srv.received_requests()))
        self.assert_no_remote_temp()

    # -- download hardening (FR-021 / SEC-016) ----------------------------
    def test_size_ceiling_enforced_midstream(self):
        # A body larger than maxDownloadBytes, served with NO declared length, is
        # aborted on bytes actually received: REMOTE_TOO_LARGE / exit 13, and no
        # partial file remains. maxDownloadBytes=65536 forces the breach
        # deterministically against a 512 KiB body.
        srv = self.media_server(body=self.video_bytes, oversize_bytes=512 * 1024)
        res = self._create(
            srv.url("/oversize/big.mp4"),
            *self.remote_flags(),
            cfg={"limits": {"maxDownloadBytes": 65536}},
        )
        self.assert_exit(res, 13)
        self.assert_error_code(res, "REMOTE_TOO_LARGE")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()

    def test_download_timeout_slow_drip(self):
        # A drip that holds the connection open far past the 1-second
        # maxDownloadSeconds is aborted with REMOTE_DOWNLOAD_FAILED / exit 14 and
        # leaves no partial file. The 60 s server hold guarantees the 1 s client
        # limit always wins.
        srv = self.media_server(body=self.video_bytes, drip_hold=60.0)
        res = self._create(
            srv.url("/drip/slow.mp4"),
            *self.remote_flags(),
            cfg={"limits": {"maxDownloadSeconds": 1}},
        )
        self.assert_exit(res, 14)
        self.assert_error_code(res, "REMOTE_DOWNLOAD_FAILED")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()

    def test_truncated_transfer_fails_and_cleans(self):
        # A declared Content-Length far larger than the bytes actually sent
        # (connection closed early) is an incomplete transfer:
        # REMOTE_DOWNLOAD_FAILED / exit 14 with no residual temp file.
        srv = self.media_server(body=self.video_bytes)
        res = self._create(
            srv.url("/truncate/x.mp4", query="declared=1000000&sent=400"), *self.remote_flags()
        )
        self.assert_exit(res, 14)
        self.assert_error_code(res, "REMOTE_DOWNLOAD_FAILED")
        self.assert_no_remote_temp()

    # -- redaction (SEC-015) ----------------------------------------------
    def test_signed_url_token_redacted_everywhere(self):
        # A signed-URL query and embedded credentials are transmitted to the
        # origin but MUST NOT appear in stdout, stderr, or the structured result;
        # only scheme, host, and path survive. This test greps all streams by
        # design (the sole exception to the log-text rule).
        srv = self.media_server(body=self.video_bytes)
        signed = srv.url("/v.mp4", query=f"X-Token={SECRET_TOKEN}&Expires=99&Signature=deadbeef")
        # Also smuggle userinfo into the authority to prove it is stripped too.
        signed = signed.replace(f"{LOOPBACK}:", f"{SECRET_USERINFO}@{LOOPBACK}:")
        res = self.run_engine(["inspect", "--input", signed, *self.remote_flags()])
        self.assert_exit(res, 0)
        result_blob = json.dumps(res.result)
        for stream_name, blob in (
            ("stdout", res.stdout),
            ("stderr", res.stderr),
            ("result", result_blob),
        ):
            self.assertNotIn(SECRET_TOKEN, blob, f"token leaked in {stream_name}")
            self.assertNotIn("opensesame", blob, f"credential leaked in {stream_name}")
        # ...but scheme, host, and path ARE retained.
        redacted = res.result["remoteSource"]["url"]
        self.assertEqual(redacted, f"http://{LOOPBACK}:{srv.port}/v.mp4")
        self.assertEqual(res.result["source"]["path"], redacted)
        # The signed query really was sent to the origin (redaction is on output).
        self.assertTrue(
            any(SECRET_TOKEN in r for r in srv.received_requests()),
            "the signed query should have been transmitted to the server",
        )

    # -- cleanup after success (FR-020 / §16) -----------------------------
    def test_temp_download_cleaned_after_success(self):
        # A successful, non-retained acquisition deletes the secure temp download
        # (no leftover unless --keep-remote-source).
        srv = self.media_server(body=self.video_bytes)
        res = self._create(srv.url("/media.mp4"), *self.remote_flags())
        self.assert_exit(res, 0)
        self.assertFalse(res.result["remoteSource"]["retained"])
        self.assertIsNone(res.result["remoteSource"]["path"])
        self.assert_no_remote_temp()

    # -- DRM integrity (SEC-017) ------------------------------------------
    def test_drm_marker_url_rejected(self):
        # A DRM manifest extension (.ism) is rejected with DRM_PROTECTED / exit 5
        # before any fetch; no circumvention is attempted.
        lis = self.listener()
        res = self._create(
            lis.url("/stream.ism", scheme="https"),
            "--allow-remote",
            "--allow-remote-address",
            LOOPBACK,
        )
        self.assert_exit(res, 5)
        self.assert_error_code(res, "DRM_PROTECTED")
        self.assertFalse(lis.connected.is_set(), "a DRM-marked URL was fetched")
        self.assert_no_remote_temp()

    def test_drm_content_rejected_midstream(self):
        # A downloaded body carrying an unambiguous DRM marker (a CENC 'pssh' box)
        # in its first chunk is rejected with DRM_PROTECTED / exit 5 and cleaned up.
        srv = self.media_server(body=b"\x00\x00\x00\x20pssh" + b"\x00" * 300)
        res = self._create(srv.url("/media.mp4"), *self.remote_flags())
        self.assert_exit(res, 5)
        self.assert_error_code(res, "DRM_PROTECTED")
        self.assert_no_remote_temp()

    # -- yt-dlp adapter pre-checks (SEC-013/SEC-014, FIX C1) --------------
    def test_ytdlp_adapter_rejects_file_scheme_before_spawn(self):
        # The yt-dlp adapter now runs the SEC-013 scheme allowlist BEFORE launching
        # yt-dlp: file:// is rejected as UNSUPPORTED_URL_SCHEME / exit 5 and never
        # opened. Running with yt-dlp stripped from PATH proves the scheme check
        # precedes the YTDLP_MISSING detection (otherwise this would be exit 3).
        res = self._create(
            "file:///etc/hostname",
            "--allow-remote",
            "--remote-adapter",
            "ytdlp",
            env=self.env_without_ytdlp(),
        )
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_URL_SCHEME")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()

    def test_ytdlp_adapter_blocks_private_host_before_spawn(self):
        # A loopback host WITHOUT --allow-remote-address is blocked by the SEC-014
        # SSRF pre-check with PRIVATE_NETWORK_BLOCKED / exit 8 BEFORE any yt-dlp
        # spawn. The bare listener must see ZERO connections, and yt-dlp is stripped
        # from PATH so exit 8 (not YTDLP_MISSING/3) proves the SSRF check runs first
        # regardless of yt-dlp presence. https keeps the scheme check from firing.
        lis = self.listener()
        res = self._create(
            lis.url("/watch", scheme="https"),
            "--allow-remote",
            "--remote-adapter",
            "ytdlp",
            env=self.env_without_ytdlp(),
        )
        self.assert_exit(res, 8)
        self.assert_error_code(res, "PRIVATE_NETWORK_BLOCKED")
        self.assertFalse(lis.connected.is_set(), "yt-dlp adapter connected to a blocked host")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()

    # -- INTERNAL_ERROR URL redaction (SEC-015, FIX m2) -------------------
    def test_internal_error_redacts_signed_url(self):
        # A raw signed URL riding along in an unexpected exception must be redacted
        # by the INTERNAL_ERROR sink: scheme/host/path survive, the token does not.
        # Driven in-process by forcing a handler to raise with a URL in its message.
        signed = f"https://cdn.example.com/v.mp4?X-Token={SECRET_TOKEN}&Signature=deadbeef"

        def boom(_args: object) -> int:
            raise RuntimeError(f"boom while fetching {signed}")

        buf = io.StringIO()
        with (
            mock.patch.dict(vtg_cli._HANDLERS, {"doctor": boom}),
            contextlib.redirect_stdout(buf),
        ):
            code = vtg_cli.main(["doctor", "--json"])
        out = buf.getvalue()
        self.assertEqual(code, vtg_errors.EXIT_INTERNAL)
        self.assertNotIn(SECRET_TOKEN, out, "signed token leaked through INTERNAL_ERROR")
        self.assertNotIn("Signature=deadbeef", out)
        result = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(result["error"]["code"], "INTERNAL_ERROR")
        # Scheme, host, and path are retained (redaction, not obliteration).
        self.assertIn("https://cdn.example.com/v.mp4", result["error"]["message"])

    # -- conversion isolation (SEC-012) -----------------------------------
    def test_downloaded_playlist_cannot_trigger_network(self):
        # A hostile HLS playlist (lying Content-Type: video/mp4) is downloaded and
        # then inspected under the SEC-010 protocol whitelist, which rejects the
        # reference-following container: UNSUPPORTED_MEDIA_CONTAINER / exit 5. The
        # network URL it references is never contacted.
        embedded = self.listener()
        playlist = HOSTILE_M3U8.format(url=embedded.url("/seg.ts")).encode()
        srv = self.media_server(body=playlist)
        res = self._create(srv.url("/playlist.m3u8", query="ctype=video/mp4"), *self.remote_flags())
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_MEDIA_CONTAINER")
        self.assertFalse(embedded.connected.is_set(), "downloaded playlist triggered a connection")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()


if __name__ == "__main__":
    unittest.main()
