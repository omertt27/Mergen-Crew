"""Snapshot runner: give each case to a system in a fresh working copy and record what it did.

Results layout::

    <out>/
      run.json                 # system, suite, budget
      cases/<case-id>.json     # findings, cost, wall-clock, error, system log
      cases/<case-id>.patch    # what the system changed in its working copy
      score.json               # written by `crew eval run` / `crew eval score`
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from .model import BenchmarkCase, Finding
from .scoring import CaseResult
from .seeding import copy_tree
from .systems import System

_GIT = ["git", "-c", "user.name=crew-eval", "-c", "user.email=crew-eval@localhost", "-c", "commit.gpgsign=false"]


def _git(workdir: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=workdir, capture_output=True, text=True, check=True).stdout


def prepare_workdir(case: BenchmarkCase, workdir: Path) -> None:
    """Copy only the snapshot (never case.json) and make it a one-commit git repo."""
    copy_tree(case.snapshot, workdir)
    _git(workdir, "init", "-q")
    _git(workdir, "add", "-A")
    _git(workdir, "commit", "-q", "-m", "snapshot")


def run_suite(
    system: System,
    cases: list[BenchmarkCase],
    out_dir: Path,
    *,
    budget_usd: float,
    suite: str = "",
    log=print,
) -> list[CaseResult]:
    cases_dir = out_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run.json").write_text(
        json.dumps({"system": system.name, "suite": suite, "budget_usd_per_case": budget_usd, "cases": [c.id for c in cases]}, indent=2) + "\n"
    )

    results = []
    for case in cases:
        log(f"[{system.name}] {case.id} ...")
        with tempfile.TemporaryDirectory(prefix=f"crew-run-{case.id}-") as tmp:
            workdir = Path(tmp) / "repo"
            prepare_workdir(case, workdir)
            started = time.monotonic()
            error = notes = None
            findings: list[Finding] = []
            cost = tokens_in = tokens_out = None
            try:
                output = system.run(workdir, case.view(), budget_usd)
                findings, cost, notes = output.findings, output.cost_usd, output.notes
                tokens_in, tokens_out = output.input_tokens, output.output_tokens
            except Exception as exc:  # one broken case must not end the suite
                error = f"{type(exc).__name__}: {exc}"
            wall = time.monotonic() - started
            patch = _diff(workdir)

        result = CaseResult(
            case_id=case.id,
            findings=findings,
            cost_usd=cost,
            wall_seconds=wall,
            error=error,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
        )
        results.append(result)
        (cases_dir / f"{case.id}.json").write_text(json.dumps({**asdict(result), "system_log": notes}, indent=2) + "\n")
        (cases_dir / f"{case.id}.patch").write_text(patch)
        log(f"[{system.name}] {case.id}: {len(findings)} findings, cost {_money(cost)}, {wall:.0f}s" + (f", ERROR {error}" if error else ""))
    return results


def _diff(workdir: Path) -> str:
    try:
        _git(workdir, "add", "-A", "--", ".", ":(exclude).crew")
        return _git(workdir, "diff", "--cached", "HEAD")
    except subprocess.CalledProcessError as exc:
        return f"# could not compute diff: {exc.stderr}\n"


def _money(cost: float | None) -> str:
    return "unknown" if cost is None else f"${cost:.2f}"


def load_results(out_dir: Path) -> tuple[str, list[CaseResult]]:
    meta = json.loads((out_dir / "run.json").read_text())
    results = []
    for path in sorted((out_dir / "cases").glob("*.json")):
        data = json.loads(path.read_text())
        results.append(
            CaseResult(
                case_id=data["case_id"],
                findings=[Finding(**f) for f in data["findings"]],
                cost_usd=data["cost_usd"],
                wall_seconds=data["wall_seconds"],
                error=data.get("error"),
                input_tokens=data.get("input_tokens"),
                output_tokens=data.get("output_tokens"),
            )
        )
    return meta["system"], results
