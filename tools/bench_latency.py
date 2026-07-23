#!/usr/bin/env python3
"""Latency benchmark for the video-to-gif CLI.

Runs each command as a real subprocess N times against a synthetic source and
reports min/p50/p95/max wall-clock milliseconds. The p95 budget defaults to
300 ms; the script exits non-zero when any command exceeds it, so it can gate
a change the same way the test suites do.

Every case also carries the exit code it MUST produce, and a mismatch fails the
run independently of latency. Without that, a command that stops doing work and
fails fast (say a batch that exits 7 on a collision before encoding anything)
would be reported as the fastest result in the suite.

    python3 tools/bench_latency.py
    python3 tools/bench_latency.py --json
    python3 tools/bench_latency.py --runs 30 --budget-ms 300

The source clip is generated once per run with tools/generate_test_video.py
(never committed) and every command executes inside a throwaway project root,
so nothing lands in the repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src" / "skill" / "video-to-gif" / "scripts" / "video_to_gif.py"
GENERATOR = ROOT / "tools" / "generate_test_video.py"

DEFAULT_RUNS = 15
DEFAULT_BUDGET_MS = 300.0


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (pct in 0..1) over an unsorted sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def make_source(work: Path) -> Path:
    """Generate a short synthetic video to benchmark against."""
    target = work / "source.mp4"
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "video", str(target), "--duration", "5"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not target.exists():
        raise SystemExit(
            f"failed to generate benchmark source (exit {proc.returncode}):\n"
            f"{proc.stderr.strip()[:600]}"
        )
    return target


class Case(NamedTuple):
    """One benchmarked command and the exit code it must produce.

    ``expect_exit`` is the correctness half of the gate: a timing number only
    means something if the command actually did the work, so a case whose exit
    code drifts fails the run no matter how fast it was.
    """

    label: str
    args: list[str]
    expect_exit: int


def build_cases(work: Path, source: Path) -> list[Case]:
    config = work / ".video-to-gif.json"
    config.write_text(json.dumps({"schemaVersion": 1, "profile": "balanced"}), encoding="utf-8")

    manifest = work / "clips.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "input": str(source),
                "outputDirectory": "./output",
                "profile": "small",
                # The benchmark re-runs every command warmup+runs times against one
                # output directory. The engine defaults to collisionPolicy "fail" and
                # never overwrites, so without this the first batch run creates the
                # four GIFs and every later run exits 7 (OUTPUT_COLLISION). Overwrite
                # keeps each run doing the full four-clip encode, and matches the
                # --collision-policy overwrite already used by the create/preview cases.
                "collisionPolicy": "overwrite",
                "clips": [
                    {"name": f"clip-{index}", "start": index, "duration": 1} for index in range(4)
                ],
            }
        ),
        encoding="utf-8",
    )

    create_args = [
        "create",
        "--input",
        str(source),
        "--start",
        "00:00:01",
        "--end",
        "00:00:02",
        "--profile",
        "small",
        "--output-name",
        "bench.gif",
        "--collision-policy",
        "overwrite",
        "--json",
    ]

    # Every command here is expected to do its work and succeed; exit 0 is the
    # only acceptable outcome for all of them.
    return [
        Case("--version", ["--version"], 0),
        Case("doctor", ["doctor", "--json"], 0),
        Case("inspect", ["inspect", "--input", str(source), "--json"], 0),
        Case("validate-config", ["validate-config", "--config", str(config), "--json"], 0),
        Case("validate-manifest", ["validate-manifest", "--manifest", str(manifest), "--json"], 0),
        Case("batch --dry-run", ["batch", "--manifest", str(manifest), "--dry-run", "--json"], 0),
        Case(
            "preview",
            [
                "preview",
                "--input",
                str(source),
                "--at",
                "00:00:01",
                "--output-name",
                "bench.png",
                "--collision-policy",
                "overwrite",
                "--json",
            ],
            0,
        ),
        Case("create 1s", create_args, 0),
        Case("batch 4 clips", ["batch", "--manifest", str(manifest), "--json"], 0),
    ]


def measure(case: Case, cwd: Path, runs: int, warmup: int) -> dict:
    samples: list[float] = []
    exit_code = -1
    stderr_tail = ""
    # Every run's exit code is checked, not just the last one: a command that
    # succeeds once and then collides (or vice versa) is not a valid sample set.
    unexpected: list[int] = []
    for index in range(runs + warmup):
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(CLI), *case.args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if index >= warmup:
            samples.append(elapsed_ms)
        exit_code = proc.returncode
        if exit_code != case.expect_exit:
            unexpected.append(exit_code)
            stderr_tail = (proc.stderr or "").strip()[-400:]
    return {
        "exitCode": exit_code,
        "expectedExit": case.expect_exit,
        "unexpectedExits": sorted(set(unexpected)),
        "exitOk": not unexpected,
        "runs": len(samples),
        "minMs": round(min(samples), 1),
        "p50Ms": round(statistics.median(samples), 1),
        "p95Ms": round(percentile(samples, 0.95), 1),
        "maxMs": round(max(samples), 1),
        "stderrTail": stderr_tail if unexpected else "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="measured runs per command")
    parser.add_argument("--warmup", type=int, default=1, help="discarded warm-up runs per command")
    parser.add_argument("--budget-ms", type=float, default=DEFAULT_BUDGET_MS)
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    parser.add_argument("--only", action="append", help="benchmark only these command labels")
    options = parser.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="vtg-bench-"))
    try:
        source = make_source(work)
        cases = build_cases(work, source)
        if options.only:
            wanted = set(options.only)
            cases = [case for case in cases if case.label in wanted]

        results = []
        for case in cases:
            result = measure(case, work, options.runs, options.warmup)
            result["command"] = case.label
            result["overBudget"] = result["p95Ms"] > options.budget_ms
            # Latency and correctness are separate gates; a case passes only when
            # it met the budget AND produced the exit code it is supposed to.
            result["pass"] = result["exitOk"] and not result["overBudget"]
            results.append(result)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    breaches = [r for r in results if r["overBudget"]]
    wrong_exit = [r for r in results if not r["exitOk"]]
    failures = [r for r in results if not r["pass"]]
    worst = max((r["p95Ms"] for r in results), default=0.0)

    if options.json:
        print(
            json.dumps(
                {
                    "budgetMs": options.budget_ms,
                    "runs": options.runs,
                    "worstP95Ms": worst,
                    "pass": not failures,
                    "overBudget": [r["command"] for r in breaches],
                    "wrongExitCode": [r["command"] for r in wrong_exit],
                    "results": results,
                },
                indent=2,
            )
        )
    else:
        print(f"{'command':20} {'exit':>4} {'want':>5} {'min':>9} {'p50':>9} {'p95':>9} {'max':>9}")
        for result in results:
            flags = ""
            if result["overBudget"]:
                flags += "  OVER"
            if not result["exitOk"]:
                flags += "  EXIT"
            print(
                f"{result['command']:20} {result['exitCode']:>4} {result['expectedExit']:>5} "
                f"{result['minMs']:>9.1f} {result['p50Ms']:>9.1f} "
                f"{result['p95Ms']:>9.1f} {result['maxMs']:>9.1f}{flags}"
            )
        print(f"\nworst p95 {worst:.1f} ms against a {options.budget_ms:.0f} ms budget")
        if breaches:
            print("over budget: " + ", ".join(r["command"] for r in breaches))
        for result in wrong_exit:
            print(
                f"wrong exit code: {result['command']} produced "
                f"{result['unexpectedExits']}, expected {result['expectedExit']}"
                + (f"\n  {result['stderrTail']}" if result["stderrTail"] else "")
            )

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
