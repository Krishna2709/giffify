"""Integration: opt-in remote source acquisition end to end (spec §12.8,
FR-018..023, SEC-012..017). Real ffmpeg; the source is downloaded from a
loopback HTTP media server (never the public internet, spec §22.6) and converted
by the existing local pipeline.

Every assertion is made against the structured JSON contract (spec §13): status,
exit code, the additive ``remoteSource`` block, and filesystem side effects --
never on log text (the one place stderr is inspected is the download-progress
event stream, which is itself part of the §13.3 contract).

The fixture serves over loopback ``http`` with ``--allow-insecure-http`` because a
hermetic HTTPS server would need a certificate the engine's default TLS context
would trust, which cannot be arranged without patching the engine; the
download/convert/cleanup semantics under test are identical (see the acceptance
suite's AC-0.2.2 note).
"""

import os
import sys
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from fixtures.media_server import RemoteEngineTestCase


class TestRemoteAcquisition(RemoteEngineTestCase):
    video_bytes: ClassVar[bytes]

    @classmethod
    def generate_media(cls) -> None:
        # One small valid landscape clip; its bytes are what the loopback server
        # streams for every download in this suite.
        cls.video_bytes = cls.make_media_bytes(size="320x240", fps=15, duration=2.0)

    # -- happy path --------------------------------------------------------
    def test_happy_path_url_to_gif(self):
        # A direct media URL is downloaded (loopback approved per SEC-014) and
        # converted into a GIF; the additive remoteSource block carries only a
        # redacted URL, and the temp download is deleted after the job (FR-020).
        srv = self.media_server(body=self.video_bytes)
        res = self.run_engine(
            [
                "create",
                "--input",
                srv.url("/media.mp4"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                *self.remote_flags(),
            ]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 1)
        clip = res.created[0]
        self.assert_valid_gif(self.output_path(os.path.basename(clip["path"])))
        rs = res.result["remoteSource"]
        self.assertEqual(rs["url"], srv.url("/media.mp4"))
        self.assertEqual(rs["adapter"], "direct")
        self.assertEqual(rs["bytesDownloaded"], len(self.video_bytes))
        self.assertFalse(rs["retained"])
        self.assertIsNone(rs["path"])
        # The internal temp path never surfaces; source.path is the redacted URL.
        self.assertEqual(res.result["source"]["path"], srv.url("/media.mp4"))
        self.assert_no_remote_temp()

    # -- inspect on a URL --------------------------------------------------
    def test_inspect_on_url_acquires_then_probes(self):
        # inspect is network-isolated (SEC-010), so a URL is acquired under
        # FR-020 first and ffprobe runs against the downloaded local file.
        srv = self.media_server(body=self.video_bytes)
        res = self.run_engine(["inspect", "--input", srv.url("/media.mp4"), *self.remote_flags()])
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertIn("remoteSource", res.result)
        self.assertEqual(res.result["source"]["path"], srv.url("/media.mp4"))
        self.assertGreater(res.result["source"]["durationMs"], 0)
        self.assert_no_remote_temp()

    # -- batch with a remote manifest input --------------------------------
    def test_batch_with_remote_manifest_input(self):
        srv = self.media_server(body=self.video_bytes)
        manifest = os.path.join(self.project, "clips.json")
        import json

        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schemaVersion": 1,
                    "input": srv.url("/media.mp4"),
                    "profile": "small",
                    "clips": [
                        {"name": "a", "start": "0", "end": "1"},
                        {"name": "b", "start": "1", "end": "2"},
                    ],
                },
                fh,
            )
        res = self.run_engine(["batch", "--manifest", manifest, *self.remote_flags()])
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 2)
        self.assertEqual(set(self.list_output()), {"a.gif", "b.gif"})
        # A single download served the whole batch; it is cleaned up afterwards.
        self.assertIn("remoteSource", res.result)
        self.assert_no_remote_temp()

    # -- retention ---------------------------------------------------------
    def test_keep_remote_source_retains_and_reports_path(self):
        # --keep-remote-source preserves the download like a completed output and
        # reports its path (FR-020). Opt the base leak check out of flagging the
        # deliberate retention, then remove it after asserting it persisted.
        self.allow_retained_remote()
        srv = self.media_server(body=self.video_bytes)
        res = self.run_engine(
            [
                "create",
                "--input",
                srv.url("/media.mp4"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                "--keep-remote-source",
                *self.remote_flags(),
            ]
        )
        self.assert_exit(res, 0)
        rs = res.result["remoteSource"]
        self.assertTrue(rs["retained"])
        self.assertIsNotNone(rs["path"])
        self.assertTrue(os.path.isfile(rs["path"]), "retained download should persist")
        # The reported path is the on-disk temp file (not a redacted URL).
        self.assertTrue(os.path.basename(rs["path"]).startswith("remote-source"))

    # -- download progress -------------------------------------------------
    def test_download_progress_events_use_download_stage(self):
        # FR-023 / §13.3: download progress is emitted on stderr as JSON Lines with
        # stage "download" and bytesReceived; a known size adds totalBytes/percent.
        srv = self.media_server(body=self.video_bytes)
        res = self.run_engine(
            [
                "create",
                "--input",
                srv.url("/media.mp4"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                *self.remote_flags(),
            ]
        )
        self.assert_exit(res, 0)
        downloads = [
            e
            for e in res.events
            if e.get("event") == "stage_progress" and e.get("stage") == "download"
        ]
        self.assertTrue(downloads, "expected at least one download-stage progress event")
        self.assertIn("bytesReceived", downloads[-1])
        self.assertEqual(downloads[-1]["totalBytes"], len(self.video_bytes))
        self.assertEqual(downloads[-1]["bytesReceived"], len(self.video_bytes))

    # -- unknown-length transfer ------------------------------------------
    def test_no_content_length_download_works(self):
        # A response with no Content-Length (connection-close framing) downloads
        # and converts; the download events carry totalBytes null and no percent.
        srv = self.media_server(body=self.video_bytes)
        res = self.run_engine(
            [
                "create",
                "--input",
                srv.url("/nolength/media.mp4"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                *self.remote_flags(),
            ]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 1)
        self.assert_valid_gif(self.output_path(os.path.basename(res.created[0]["path"])))
        downloads = [
            e
            for e in res.events
            if e.get("event") == "stage_progress" and e.get("stage") == "download"
        ]
        self.assertTrue(downloads)
        self.assertIsNone(downloads[-1]["totalBytes"])
        self.assertNotIn("percent", downloads[-1])
        self.assert_no_remote_temp()

    # -- chunked transfer-encoding ----------------------------------------
    def test_chunked_transfer_encoding_download_works(self):
        # Transfer-Encoding: chunked is decoded transparently; the total size is
        # unknown, so it behaves like the no-Content-Length case.
        srv = self.media_server(body=self.video_bytes)
        res = self.run_engine(
            [
                "create",
                "--input",
                srv.url("/chunked/media.mp4"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                *self.remote_flags(),
            ]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 1)
        self.assert_no_remote_temp()

    # -- redirect following with SSRF re-check -----------------------------
    def test_redirect_following_rechecks_allowlist(self):
        # A 302 to another approved-loopback path is followed; the SSRF allowlist
        # is re-evaluated on the redirect target (SEC-014) and 127.0.0.1 is again
        # approved, so the final media downloads and converts.
        srv = self.media_server(body=self.video_bytes)
        res = self.run_engine(
            [
                "create",
                "--input",
                srv.url("/redirect/go.mp4", query="to=/ok/final.mp4"),
                "--start",
                "0",
                "--end",
                "1",
                "--profile",
                "small",
                *self.remote_flags(),
            ]
        )
        self.assert_exit(res, 0)
        self.assert_status(res, "success")
        self.assertEqual(res.summary["created"], 1)
        requests = srv.received_requests()
        self.assertTrue(any(r.startswith("/redirect/go.mp4") for r in requests))
        self.assertIn("/ok/final.mp4", requests)
        self.assert_no_remote_temp()


if __name__ == "__main__":
    unittest.main()
