"""Executable acceptance suite for version 0.2.0 remote sources (spec §23).

One test per acceptance criterion (AC-0.2.1 .. AC-0.2.14), named
``test_ac_0_2_NN_*`` with the criterion text quoted in the docstring. All media
is synthetic and generated at runtime; the engine is exercised end to end as a
subprocess with real ffmpeg against a loopback HTTP media server (never the
public internet, spec §22.6), and every assertion is made against the structured
JSON result contract (spec §13) and filesystem side effects.

Substitution note (applies to AC-0.2.2 and others that "download"): the criteria
name HTTPS. A hermetic HTTPS server would require a certificate the engine's
default TLS context would trust, which cannot be arranged without patching the
engine, so the fixture serves over loopback ``http`` with ``--allow-insecure-http``.
The download-to-temp, local-conversion, and cleanup semantics under test are
identical; the https-vs-http distinction is exercised by the scheme-allowlist
tests (AC-0.2.4 and the security suite).
"""

import json
import os
import shutil
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.media_server import HOSTILE_M3U8, LOOPBACK, RemoteEngineTestCase

SIGNED_TOKEN = "SIGtok3nVALUEmustNEVERleak0987654321"


class TestAcceptanceRemote(RemoteEngineTestCase):
    video_bytes: ClassVar[bytes]

    @classmethod
    def generate_media(cls) -> None:
        cls.video_bytes = cls.make_media_bytes(size="320x240", fps=15, duration=2.0)

    def _create(self, url: str, *flags: str, cfg: dict | None = None, timeout: int = 60):
        if cfg is not None:
            self.write_config(**cfg)
        return self.run_engine(
            ["create", "--input", url, "--start", "0", "--end", "1", "--profile", "small", *flags],
            timeout=timeout,
        )

    # -- AC-0.2.1 ----------------------------------------------------------
    def test_ac_0_2_01_disabled_by_default(self):
        """AC-0.2.1: A remote URL supplied with default configuration is rejected
        with REMOTE_DISABLED and exit code 8, and no network access occurs."""
        lis = self.listener()
        res = self._create(lis.url("/v.mp4"))  # default config, no remote flags
        self.assert_exit(res, 8)
        self.assert_status(res, "remote_disabled")
        self.assert_error_code(res, "REMOTE_DISABLED")
        self.assertFalse(lis.connected.is_set(), "engine performed network access")
        self.assertEqual(self.list_output(), [])

    # -- AC-0.2.2 ----------------------------------------------------------
    def test_ac_0_2_02_direct_download_and_cleanup(self):
        """AC-0.2.2: With remote sources enabled, a direct HTTPS media URL is
        downloaded to secure temporary storage, converted by the local pipeline
        into a GIF, and the download is deleted after the job. (Loopback http
        substitution per the module note.)"""
        srv = self.media_server(body=self.video_bytes)
        res = self._create(srv.url("/media.mp4"), *self.remote_flags())
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 1)
        self.assert_valid_gif(self.output_path(os.path.basename(res.created[0]["path"])))
        self.assertFalse(res.result["remoteSource"]["retained"])
        # The secure temp download is deleted after the job (FR-020 / §16).
        self.assert_no_remote_temp()

    # -- AC-0.2.3 ----------------------------------------------------------
    def test_ac_0_2_03_retained_source(self):
        """AC-0.2.3: With --keep-remote-source, the downloaded file is retained
        and its path is reported in the structured result."""
        self.allow_retained_remote()
        srv = self.media_server(body=self.video_bytes)
        res = self._create(srv.url("/media.mp4"), "--keep-remote-source", *self.remote_flags())
        self.assert_exit(res, 0)
        rs = res.result["remoteSource"]
        self.assertTrue(rs["retained"])
        self.assertIsNotNone(rs["path"])
        self.assertTrue(os.path.isfile(rs["path"]), "retained download should persist")

    # -- AC-0.2.4 ----------------------------------------------------------
    def test_ac_0_2_04_scheme_rejection(self):
        """AC-0.2.4: A file URL is rejected with UNSUPPORTED_URL_SCHEME and is
        never fetched or opened."""
        res = self._create("file:///etc/hostname", "--allow-remote")
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_URL_SCHEME")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()

    # -- AC-0.2.5 ----------------------------------------------------------
    def test_ac_0_2_05_ssrf_protection(self):
        """AC-0.2.5: A URL resolving to a loopback or private-network address is
        rejected with PRIVATE_NETWORK_BLOCKED unless the specific address was
        explicitly approved."""
        # Without approval: blocked.
        srv = self.media_server(body=self.video_bytes)
        blocked = self._create(srv.url("/media.mp4"), "--allow-remote", "--allow-insecure-http")
        self.assert_exit(blocked, 8)
        self.assert_error_code(blocked, "PRIVATE_NETWORK_BLOCKED")
        self.assertFalse(srv.received_any())
        # With the address explicitly approved: permitted (downloads + converts).
        approved = self._create(srv.url("/media.mp4"), *self.remote_flags())
        self.assert_exit(approved, 0)
        self.assert_status(approved, "success")
        self.assert_no_remote_temp()

    # -- AC-0.2.6 ----------------------------------------------------------
    def test_ac_0_2_06_redaction(self):
        """AC-0.2.6: A signed URL's query string and any embedded credentials
        never appear in logs, progress events, or structured results."""
        srv = self.media_server(body=self.video_bytes)
        url = srv.url("/v.mp4", query=f"X-Token={SIGNED_TOKEN}&Signature=abc")
        url = url.replace(f"{LOOPBACK}:", f"aladdin:opensesame@{LOOPBACK}:")
        res = self.run_engine(["inspect", "--input", url, *self.remote_flags()])
        self.assert_exit(res, 0)
        for blob in (res.stdout, res.stderr, json.dumps(res.result)):
            self.assertNotIn(SIGNED_TOKEN, blob)
            self.assertNotIn("opensesame", blob)
        # Scheme, host, and path are retained.
        self.assertEqual(res.result["remoteSource"]["url"], f"http://{LOOPBACK}:{srv.port}/v.mp4")

    # -- AC-0.2.7 ----------------------------------------------------------
    def test_ac_0_2_07_size_ceiling(self):
        """AC-0.2.7: A download exceeding maxDownloadBytes is aborted with
        REMOTE_TOO_LARGE, and no partial file remains."""
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

    # -- AC-0.2.8 ----------------------------------------------------------
    def test_ac_0_2_08_timeout(self):
        """AC-0.2.8: A download exceeding maxDownloadSeconds is aborted with
        REMOTE_DOWNLOAD_FAILED, and no partial file remains."""
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

    # -- AC-0.2.9 ----------------------------------------------------------
    def test_ac_0_2_09_partial_download_cleanup(self):
        """AC-0.2.9: An interrupted or failed download leaves no residual
        temporary file."""
        # A truncated transfer (large declared length, connection closed early) is
        # a failed download; the partial file must be removed under §16.
        srv = self.media_server(body=self.video_bytes)
        res = self._create(
            srv.url("/truncate/x.mp4", query="declared=1000000&sent=400"), *self.remote_flags()
        )
        self.assert_exit(res, 14)
        self.assert_error_code(res, "REMOTE_DOWNLOAD_FAILED")
        self.assert_no_remote_temp()

    # -- AC-0.2.10 ---------------------------------------------------------
    def test_ac_0_2_10_optional_ytdlp_adapter(self):
        """AC-0.2.10: When yt-dlp is present and requested, a video-page URL is
        acquired; when yt-dlp is absent, requesting the adapter yields
        YTDLP_MISSING with exit code 3. The adapter is never bundled.

        Hermetic-scope note: real video-page acquisition needs the yt-dlp binary
        AND a reachable page (public internet), which this no-internet suite
        forbids, so the "present -> acquires" clause is verified by the mocked
        unit test tests/unit/test_remote.py::TestYtdlp.
        test_adapter_present_downloads_via_subprocess and by manual QA. The
        binary-independent clause -- requesting the adapter without yt-dlp yields
        YTDLP_MISSING / exit 3 and makes no network access -- is asserted here
        deterministically by stripping yt-dlp from PATH (ffmpeg stays resolvable
        via VTG_* overrides), so it holds regardless of the host. When the host
        has yt-dlp installed, the present-and-acquires clause is skipped."""
        lis = self.listener()
        res = self.run_engine(
            [
                "create",
                "--input",
                lis.url("/watch"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                "--remote-adapter",
                "ytdlp",
                "--allow-remote",
                "--allow-remote-address",
                LOOPBACK,
            ],
            env=self.env_without_ytdlp(),
        )
        self.assert_exit(res, 3)
        self.assert_error_code(res, "YTDLP_MISSING")
        self.assert_status(res, "dependency_missing")
        self.assertFalse(lis.connected.is_set(), "no acquisition before the missing-adapter check")
        if shutil.which("yt-dlp") is not None:
            self.skipTest(
                "yt-dlp is installed on this host; real video-page acquisition needs the "
                "binary and a reachable page (public internet), which the hermetic suite "
                "forbids. The YTDLP_MISSING/exit-3 clause was asserted above; the "
                "present-and-acquires clause is covered by the mocked unit test."
            )

    # -- AC-0.2.11 ---------------------------------------------------------
    def test_ac_0_2_11_drm_rejection(self):
        """AC-0.2.11: A DRM-protected source is rejected with DRM_PROTECTED and
        exit code 5, with no circumvention attempted."""
        lis = self.listener()
        res = self._create(
            lis.url("/stream.ism", scheme="https"),
            "--allow-remote",
            "--allow-remote-address",
            LOOPBACK,
        )
        self.assert_exit(res, 5)
        self.assert_error_code(res, "DRM_PROTECTED")
        self.assertFalse(lis.connected.is_set(), "a DRM-marked source was fetched")
        self.assert_no_remote_temp()

    # -- AC-0.2.12 ---------------------------------------------------------
    def test_ac_0_2_12_rights_confirmation(self):
        """AC-0.2.12: The agent obtains a lawful-basis confirmation once per
        source before acquisition, and the skill records nothing.

        Agent-layer note: obtaining the per-source lawful-basis confirmation is an
        agent responsibility (spec §19.6, documented in
        references/remote-sources.md); the non-interactive engine never prompts,
        and --allow-remote is its per-invocation authorization surface. What is
        engine-testable here is "records nothing": after a successful acquisition
        the engine persists no record of the source. This asserts that the signed
        token and URL query appear in NO file the engine wrote, and that the only
        files left in the project are the pyproject marker and the output GIF (no
        history/log/state file)."""
        srv = self.media_server(body=self.video_bytes)
        url = srv.url("/clip.mp4", query=f"X-Token={SIGNED_TOKEN}&Signature=xyz")
        res = self._create(url, *self.remote_flags())
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        # Enumerate everything the engine left behind in the project.
        project_files = []
        for root, _dirs, files in os.walk(self.project):
            for name in files:
                project_files.append(os.path.join(root, name))
        # Nothing the engine wrote records the signed token.
        for path in project_files:
            with open(path, "rb") as fh:
                data = fh.read()
            self.assertNotIn(SIGNED_TOKEN.encode(), data, f"token recorded in {path}")
        # The only artifacts are the project marker and the produced GIF(s):
        # no state/history/log file records the acquisition.
        relative = {os.path.relpath(p, self.project) for p in project_files}
        non_output = {r for r in relative if not r.startswith("output" + os.sep)}
        self.assertEqual(
            non_output, {"pyproject.toml"}, f"unexpected persisted files: {non_output}"
        )
        # And nothing persisted outside the project either (not retained).
        self.assert_no_remote_temp()

    # -- AC-0.2.13 ---------------------------------------------------------
    def test_ac_0_2_13_conversion_isolation(self):
        """AC-0.2.13: The downloaded file is inspected and converted under the
        SEC-010 protocol whitelist, and a hostile downloaded playlist cannot
        trigger network access."""
        embedded = self.listener()
        playlist = HOSTILE_M3U8.format(url=embedded.url("/seg.ts")).encode()
        srv = self.media_server(body=playlist)
        res = self._create(srv.url("/playlist.m3u8", query="ctype=video/mp4"), *self.remote_flags())
        self.assert_exit(res, 5)
        self.assert_error_code(res, "UNSUPPORTED_MEDIA_CONTAINER")
        self.assertFalse(embedded.connected.is_set(), "downloaded playlist triggered a connection")
        self.assertEqual(self.list_output(), [])
        self.assert_no_remote_temp()

    # -- AC-0.2.14 ---------------------------------------------------------
    def test_ac_0_2_14_backward_compatibility(self):
        """AC-0.2.14: All version 0.1.0 command-line invocations, configuration,
        and local behavior are unchanged."""
        # A plain local-file create works exactly as in v0.1.0 -- no remote flags,
        # no remoteSource block -- producing the same structured result shape.
        local = os.path.join(self.project, "local.mp4")
        with open(local, "wb") as fh:
            fh.write(self.video_bytes)
        res = self.run_engine(
            ["create", "--input", local, "--start", "0", "--end", "1", "--profile", "small"]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 1)
        self.assert_valid_gif(self.output_path(os.path.basename(res.created[0]["path"])))
        self.assertNotIn("remoteSource", res.result)  # additive block absent for local input
        self.assertEqual(res.result["schemaVersion"], 1)
        # Local-only behavior is preserved: remote acquisition is off by default,
        # so a URL under the default configuration performs no network access.
        lis = self.listener()
        remote = self.run_engine(
            ["create", "--input", lis.url("/v.mp4"), "--start", "0", "--end", "1"]
        )
        self.assert_exit(remote, 8)
        self.assert_error_code(remote, "REMOTE_DISABLED")
        self.assertFalse(lis.connected.is_set())


if __name__ == "__main__":
    unittest.main()
