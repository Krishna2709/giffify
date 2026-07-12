"""Path resolution and project-boundary tests (SEC-002, SEC-003, SEC-005)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest  # noqa: F401

from vtg import (
    errors,
    paths,
)


class TestUrlDetection(unittest.TestCase):
    def test_urls_detected(self):
        for url in (
            "http://x/y.mp4",
            "https://x/y.mp4",
            "s3://bucket/x.mp4",
            "ftp://h/x.mp4",
            "rtsp://h/x",
        ):
            with self.subTest(url=url):
                self.assertTrue(paths.is_url(url))

    def test_non_urls(self):
        for p in (
            "/abs/video.mp4",
            "./rel/video.mp4",
            "video.mp4",
            "C:\\videos\\demo.mp4",
            "D:/x.mp4",
            "file:///local/x.mp4",
        ):
            with self.subTest(p=p):
                self.assertFalse(paths.is_url(p))

    def test_reject_if_remote(self):
        with self.assertRaises(errors.EngineError) as ctx:
            paths.reject_if_remote("https://example.com/v.mp4")
        self.assertEqual(ctx.exception.code, errors.UNSUPPORTED_REMOTE_SOURCE)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_INVALID_MEDIA)

    def test_local_path_not_rejected(self):
        paths.reject_if_remote("/local/video.mp4")  # no raise


class TestProjectBoundary(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_is_within(self):
        self.assertTrue(paths.is_within(os.path.join(self.root, "output"), self.root))
        self.assertTrue(paths.is_within(self.root, self.root))
        self.assertFalse(paths.is_within("/some/other/place", self.root))

    def test_output_dir_inside_ok(self):
        out = paths.resolve_output_directory(
            "./output", project_root=self.root, allow_outside_project=False
        )
        self.assertTrue(out.startswith(self.root))

    def test_output_dir_outside_rejected(self):
        outside = tempfile.mkdtemp()
        try:
            with self.assertRaises(errors.EngineError) as ctx:
                paths.resolve_output_directory(
                    outside, project_root=self.root, allow_outside_project=False
                )
            self.assertEqual(ctx.exception.code, errors.PROJECT_BOUNDARY_VIOLATION)
            self.assertEqual(ctx.exception.exit_code, errors.EXIT_PERMISSION)
        finally:
            import shutil

            shutil.rmtree(outside, ignore_errors=True)

    def test_output_dir_outside_allowed_with_flag(self):
        outside = tempfile.mkdtemp()
        try:
            out = paths.resolve_output_directory(
                outside, project_root=self.root, allow_outside_project=True
            )
            self.assertEqual(out, os.path.normpath(outside))
        finally:
            import shutil

            shutil.rmtree(outside, ignore_errors=True)

    def test_resolve_within_directory_rejects_escape(self):
        with self.assertRaises(errors.EngineError):
            paths.resolve_within_directory(self.root, "sub/evil.gif")
        with self.assertRaises(errors.EngineError):
            paths.resolve_within_directory(self.root, "../evil.gif")

    def test_resolve_within_directory_ok(self):
        got = paths.resolve_within_directory(self.root, "clip.gif")
        self.assertEqual(got, os.path.join(self.root, "clip.gif"))


class TestProjectRoot(unittest.TestCase):
    def test_marker_detected(self):
        root = tempfile.mkdtemp()
        try:
            open(os.path.join(root, ".video-to-gif.json"), "w").close()
            sub = os.path.join(root, "a", "b")
            os.makedirs(sub)
            detected = paths.resolve_project_root(sub)
            self.assertTrue(os.path.exists(os.path.join(detected, ".video-to-gif.json")))
            self.assertEqual(os.path.normpath(detected), os.path.normpath(root))
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
