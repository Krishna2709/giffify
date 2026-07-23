"""Job-wide temporary-disk accounting for SEC-011 (spec line 1811).

SEC-011 scopes the wall-clock timeout per *clip* but the temporary-disk ceiling
per *job*. While clips were encoded one at a time the two coincided, so
``run_guarded`` measuring its own ``temp_paths`` was also measuring the job. Now
that a batch encodes several clips at once, the ceiling has to be summed over
every live clip -- otherwise N workers each carry a full-size budget and the job
can hold N x ``limits.maxTemporaryBytes`` without ever raising exit 13.

Hermetic: ``subprocess.Popen`` and the process-group terminator are faked, so no
ffmpeg and no real child process are involved.
"""

import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import vtgtest

from vtg import errors, ffmpeg


class _FakeProc:
    """Popen stand-in for run_guarded's guard loop.

    ``exit_after_waits=None`` never exits, so the loop always reaches its
    guards; a number makes wait() succeed on that call.
    """

    def __init__(self, *, exit_after_waits=None, returncode=0):
        self.pid = -1
        self.returncode = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.waits = 0
        self._exit_after_waits = exit_after_waits
        self._final_returncode = returncode

    def wait(self, timeout=None):
        self.waits += 1
        if self._exit_after_waits is not None and self.waits >= self._exit_after_waits:
            self.returncode = self._final_returncode
            return self.returncode
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        return ("", "")

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class _GuardHarness(unittest.TestCase):
    """Runs run_guarded against a fake process and a real temp tree on disk."""

    def setUp(self):
        import shutil
        import tempfile

        self.root = tempfile.mkdtemp(prefix="vtg-budget-test-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # Every process the guard loop decided to terminate, in order.
        self.terminated: list[_FakeProc] = []
        patcher = mock.patch.object(ffmpeg, "_terminate_group", side_effect=self.terminated.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def clip_dir(self, name, size_bytes):
        """A temp directory holding exactly ``size_bytes`` of artifacts."""
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "palette.png"), "wb") as fh:
            fh.write(b"\0" * size_bytes)
        return path

    def run_guarded(self, *, temp_paths, max_temp_bytes, temp_budget, proc, timeout_seconds=5.0):
        with mock.patch.object(ffmpeg.subprocess, "Popen", return_value=proc):
            ffmpeg.run_guarded(
                ["ffmpeg", "-version"],
                stage="encode",
                clip_index=0,
                timeout_seconds=timeout_seconds,
                temp_paths=temp_paths,
                max_temp_bytes=max_temp_bytes,
                cancel_event=None,
                temp_budget=temp_budget,
            )


class TestTempBudgetRegistry(unittest.TestCase):
    def test_paths_aggregates_every_live_clip(self):
        budget = ffmpeg.TempBudget()
        budget.register(["/tmp/a-dir", "/tmp/a.gif.tmp"])
        budget.register(["/tmp/b-dir", "/tmp/b.gif.tmp"])
        self.assertEqual(
            sorted(budget.paths()),
            ["/tmp/a-dir", "/tmp/a.gif.tmp", "/tmp/b-dir", "/tmp/b.gif.tmp"],
        )

    def test_release_removes_only_that_clip_and_is_idempotent(self):
        budget = ffmpeg.TempBudget()
        first = budget.register(["/tmp/a"])
        budget.register(["/tmp/b"])
        budget.release(first)
        budget.release(first)  # double release must not disturb the other clip
        self.assertEqual(budget.paths(), ["/tmp/b"])

    def test_tokens_are_unique_under_concurrent_registration(self):
        budget = ffmpeg.TempBudget()
        tokens = []
        lock = threading.Lock()
        start = threading.Event()

        def register(index):
            start.wait()
            token = budget.register([f"/tmp/clip-{index}"])
            with lock:
                tokens.append(token)

        threads = [threading.Thread(target=register, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        self.assertEqual(len(set(tokens)), 16)
        self.assertEqual(len(budget.paths()), 16)


class TestJobWideCeiling(_GuardHarness):
    def test_breach_when_the_job_total_exceeds_the_ceiling(self):
        # Two concurrent clips, 600 bytes each. Neither clip alone breaches a
        # 1000-byte ceiling; the job does. Before job-wide accounting this run
        # completed happily and the batch reported success.
        budget = ffmpeg.TempBudget()
        mine = self.clip_dir("clip-a", 600)
        budget.register([mine])
        budget.register([self.clip_dir("clip-b", 600)])

        with self.assertRaises(errors.EngineError) as ctx:
            self.run_guarded(
                temp_paths=[mine],
                max_temp_bytes=1000,
                temp_budget=budget,
                proc=_FakeProc(),
            )

        self.assertEqual(ctx.exception.code, errors.RESOURCE_LIMIT_EXCEEDED)
        self.assertEqual(ctx.exception.exit_code, errors.EXIT_RESOURCE_LIMIT)
        self.assertEqual(ctx.exception.exit_code, 13)
        self.assertEqual(ctx.exception.stage, "encode")
        # Specifically the disk ceiling, not the wall-clock timeout: both raise
        # RESOURCE_LIMIT_EXCEEDED, so the code alone would not prove the job-wide
        # sum is what fired.
        self.assertIn("Temporary-disk ceiling", ctx.exception.message)
        self.assertEqual(len(self.terminated), 1)  # FFmpeg process group killed

    def test_own_paths_alone_stay_under_the_ceiling(self):
        # Control for the test above: the same clip measured on its own does not
        # breach, so the breach really comes from the job-wide sum.
        mine = self.clip_dir("clip-a", 600)
        self.run_guarded(
            temp_paths=[mine],
            max_temp_bytes=1000,
            temp_budget=None,
            proc=_FakeProc(exit_after_waits=2),
        )
        self.assertEqual(self.terminated, [])

    def test_released_clip_stops_counting(self):
        # A finished clip's artifacts are gone, so the job total must drop again
        # and a later clip must not inherit a breach it did not cause.
        budget = ffmpeg.TempBudget()
        mine = self.clip_dir("clip-a", 600)
        budget.register([mine])
        finished = budget.register([self.clip_dir("clip-b", 600)])
        budget.release(finished)

        self.run_guarded(
            temp_paths=[mine],
            max_temp_bytes=1000,
            temp_budget=budget,
            proc=_FakeProc(exit_after_waits=2),
        )
        self.assertEqual(self.terminated, [])

    def test_convert_clip_registers_and_releases_its_artifacts(self):
        # convert_clip must register [temp_dir, temp_out] for the whole time it
        # holds them and release them once they are off disk, so a failing clip
        # cannot leak budget for the rest of the job.
        budget = ffmpeg.TempBudget()
        seen = []
        out_dir = os.path.join(self.root, "out")
        os.makedirs(out_dir, exist_ok=True)

        def fake_run_guarded(cmd, **kw):
            seen.append(sorted(budget.paths()))
            raise errors.EngineError(
                errors.FFMPEG_FAILED,
                "boom",
                exit_code=errors.EXIT_FFMPEG_FAILED,
                stage=kw["stage"],
            )

        with (
            mock.patch.object(ffmpeg, "run_guarded", side_effect=fake_run_guarded),
            self.assertRaises(errors.EngineError),
        ):
            ffmpeg.convert_clip(
                "ffmpeg",
                vtgtest.make_source(path=os.path.join(self.root, "demo.mp4")),
                start_ms=0,
                duration_ms=1000,
                settings=vtgtest.make_settings(),
                dest_path=os.path.join(out_dir, "out.gif"),
                output_dir=out_dir,
                timeout_seconds=30.0,
                max_temp_bytes=2**31,
                temp_budget=budget,
            )

        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 2)  # temp palette dir + temp GIF
        self.assertEqual(budget.paths(), [])  # released after cleanup


if __name__ == "__main__":
    unittest.main()
