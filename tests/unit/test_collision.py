"""Collision policy tests via plan_outputs (spec FR-012, section 22.1)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest

from vtg.cli import plan_outputs
from vtg.models import ClipSpec


def _resolver(_clip):
    return vtgtest.make_settings()


class TestCollisionPolicies(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.source = vtgtest.make_source(path=os.path.join("/proj", "demo.mp4"))

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def _touch(self, name):
        open(os.path.join(self.out, name), "w").close()

    def _plan(self, clips, policy):
        return plan_outputs(
            clips,
            self.source,
            output_dir=self.out,
            collision_policy=policy,
            resolve_settings=_resolver,
        )

    def test_fail_marks_collision(self):
        self._touch("a.gif")
        planned = self._plan([ClipSpec(0, 0, 1000, name="a")], "fail")
        self.assertEqual(planned[0].action, "collision")
        self.assertTrue(planned[0].collided)

    def test_overwrite_action(self):
        self._touch("a.gif")
        planned = self._plan([ClipSpec(0, 0, 1000, name="a")], "overwrite")
        self.assertEqual(planned[0].action, "overwrite")

    def test_skip_action(self):
        self._touch("a.gif")
        planned = self._plan([ClipSpec(0, 0, 1000, name="a")], "skip")
        self.assertEqual(planned[0].action, "skip")

    def test_unique_renames(self):
        self._touch("a.gif")
        planned = self._plan([ClipSpec(0, 0, 1000, name="a")], "unique")
        self.assertEqual(planned[0].action, "write")
        self.assertEqual(planned[0].filename, "a-1.gif")

    def test_no_collision_writes(self):
        planned = self._plan([ClipSpec(0, 0, 1000, name="fresh")], "fail")
        self.assertEqual(planned[0].action, "write")
        self.assertFalse(planned[0].collided)

    def test_intra_job_collision_fail(self):
        # Two clips resolving to the same name collide within the job.
        clips = [ClipSpec(0, 0, 1000, name="dup"), ClipSpec(1, 2000, 3000, name="dup")]
        planned = self._plan(clips, "fail")
        self.assertEqual(planned[0].action, "write")
        self.assertEqual(planned[1].action, "collision")

    def test_intra_job_collision_unique(self):
        clips = [ClipSpec(0, 0, 1000, name="dup"), ClipSpec(1, 2000, 3000, name="dup")]
        planned = self._plan(clips, "unique")
        self.assertEqual(planned[0].filename, "dup.gif")
        self.assertEqual(planned[1].filename, "dup-1.gif")

    def test_ask_behaves_like_fail(self):
        self._touch("a.gif")
        planned = self._plan([ClipSpec(0, 0, 1000, name="a")], "ask")
        self.assertEqual(planned[0].action, "collision")

    def test_default_name_when_unnamed(self):
        planned = self._plan([ClipSpec(0, 60000, 65000)], "fail")
        self.assertEqual(planned[0].filename, "demo_00-01-00.000_to_00-01-05.000.gif")


if __name__ == "__main__":
    unittest.main()
