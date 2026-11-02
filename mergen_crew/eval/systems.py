"""Systems under test.

A system receives a fresh working copy of a case's snapshot (a git repo with one
commit), what it may know about the case (:class:`CaseView`), and a dollar
budget. It returns findings and what it spent.

* ``null``     reports nothing. Smoke-tests the harness; scores 0 recall.
* ``baseline`` the simplest thing that could work (architecture doc, section 2):
  an off-the-shelf coding agent per subsystem, same scope and budget, told to
  "find and prove bugs in this area", plus a markdown notes file it reads and
  updates. The agent is pluggable (Claude Code, Codex, Gemini CLI, any command;
  see :mod:`agents`). The full Mergen-Crew system has to beat this.

Any other system plugs in as ``module.path:factory``, where ``factory()``
returns an object with ``name`` and ``run(workdir, case, budget_usd)``.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from .agents import AgentPreset, Usage, build_argv, resolve_agent, resolve_cost
from .model import CaseView, Finding, SystemOutput

BASELINE_PROMPT = """\
You are investigating one subsystem of this repository.

Subsystem: {subsystem_id}
Paths: {paths}

Find and prove bugs in this area.

- Run the test suite with: {test_command}
- For each bug: write a regression test that fails on the current code, fix the
  code, and confirm the test passes and stays green across reruns.
- Your notes file is {notes_path}. Read it first; it persists across runs. Before
  you finish, update it with what you investigated, what looked clean, and the
  targets you would check next.
- Write your findings as a JSON array to {findings_path}. Each item:
  {{"file": "<repo-relative path>", "line": <int>, "symbol": "<function or Class.method>",
    "title": "<one line>", "description": "<what is wrong and how you proved it>",
    "kind": "logic|missing_error_handling|concurrency|security|performance|other",
    "evidence_tier": "E0|E1|E2|E3", "test": "<path of the regression test or null>"}}
  Evidence tiers: E3 = new test fails before the fix, passes after, stable across
  reruns. E1 = failing test exists but no verified fix. E0 = suspicion only.
  Write [] if you found nothing. Do not report style issues.
- Your budget for this subsystem is ${budget:.2f}.
"""


class System(Protocol):
    name: str

    def run(self, workdir: Path, case: CaseView, budget_usd: float) -> SystemOutput: ...


class NullSystem:
    name = "null"

    def run(self, workdir: Path, case: CaseView, budget_usd: float) -> SystemOutput:
        return SystemOutput(findings=[], cost_usd=0.0, input_tokens=0, output_tokens=0)


class BaselineAgent:
    """One coding-agent session per subsystem, with a persistent notes file.

    ``notes_dir=None`` wipes memory between runs: that is the "long-term memory
    helps" ablation from the architecture doc.
    """

    def __init__(
        self,
        agent: AgentPreset | None = None,
        notes_dir: Path | None = None,
        timeout: float = 3600.0,
        price: tuple[float, float] | None = None,
    ):
        self.agent = agent or resolve_agent("claude")
        self.notes_dir = notes_dir
        self.timeout = timeout
        self.price = price
        self.name = f"baseline-{self.agent.name}" + ("" if notes_dir else "-no-memory")

    def run(self, workdir: Path, case: CaseView, budget_usd: float) -> SystemOutput:
        subsystems = case.subsystems or [{"id": "sub-all", "paths": ["**"]}]
        per_sub_budget = budget_usd / len(subsystems)
        findings: list[Finding] = []
        costs: list[float | None] = []
        tokens_in: list[int | None] = []
        tokens_out: list[int | None] = []
        log: list[str] = []

        for sub in subsystems:
            sub_id = sub["id"]
            crew = workdir / ".crew"
            notes_rel = Path(".crew/notes") / f"{sub_id}.md"
            findings_rel = Path(".crew/findings") / f"{sub_id}.json"
            notes_abs, findings_abs = workdir / notes_rel, workdir / findings_rel
            for d in (notes_abs.parent, findings_abs.parent):
                d.mkdir(parents=True, exist_ok=True)

            persisted = self._persisted_notes(case.id, sub_id)
            if persisted and persisted.exists():
                shutil.copy(persisted, notes_abs)
            elif not notes_abs.exists():
                notes_abs.write_text(f"# Notes: {sub_id}\n\n")

            prompt = BASELINE_PROMPT.format(
                subsystem_id=sub_id,
                paths=", ".join(sub["paths"]),
                test_command=case.test_command,
                notes_path=notes_rel.as_posix(),
                findings_path=findings_rel.as_posix(),
                budget=per_sub_budget,
            )
            prompt_file = crew / f"prompt-{sub_id}.md"
            prompt_file.write_text(prompt)
            argv, stdin = build_argv(self.agent.command, prompt=prompt, prompt_file=prompt_file, budget=per_sub_budget)

            try:
                proc = subprocess.run(argv, input=stdin, cwd=workdir, capture_output=True, text=True, timeout=self.timeout)
                usage = self.agent.parse(proc.stdout)
                note = usage.note
                if proc.returncode != 0:
                    note = f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[-500:].strip()}"
            except FileNotFoundError:
                raise RuntimeError(f"agent command not found: {argv[0]!r} (is {self.agent.name} installed?)") from None
            except subprocess.TimeoutExpired:
                usage, note = Usage(), f"timeout after {self.timeout:.0f}s"

            cost, cost_source = resolve_cost(usage, workdir, self.price)
            costs.append(cost)
            tokens_in.append(usage.input_tokens)
            tokens_out.append(usage.output_tokens)
            log.append(f"{sub_id}: {note or 'ok'}; cost {cost_source}")

            findings.extend(_read_findings(findings_abs, log, sub_id))
            if persisted:
                persisted.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(notes_abs, persisted)

        return SystemOutput(
            findings=findings,
            cost_usd=_total(costs),
            input_tokens=_total(tokens_in),
            output_tokens=_total(tokens_out),
            notes="\n".join(log),
        )

    def _persisted_notes(self, case_id: str, sub_id: str) -> Path | None:
        return self.notes_dir / case_id / f"{sub_id}.md" if self.notes_dir else None


def _total(values):
    """Sum, or None if any part is unknown: a partial sum would understate spend."""
    return None if any(v is None for v in values) else sum(values)


def _read_findings(path: Path, log: list[str], sub_id: str) -> list[Finding]:
    if not path.exists():
        log.append(f"{sub_id}: no findings file written")
        return []
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, list):
            raise ValueError("findings file is not a JSON array")
        return [Finding.from_json(item) for item in raw]
    except (ValueError, KeyError, TypeError) as exc:
        log.append(f"{sub_id}: unreadable findings file: {exc}")
        return []


def load_system(
    spec: str,
    *,
    agent: str = "claude",
    agent_command: str | None = None,
    notes_dir: Path | None = None,
    timeout: float = 3600.0,
    price: tuple[float, float] | None = None,
) -> System:
    if spec == "null":
        return NullSystem()
    if spec == "baseline":
        return BaselineAgent(agent=resolve_agent(agent, agent_command), notes_dir=notes_dir, timeout=timeout, price=price)
    if ":" not in spec:
        raise ValueError(f"unknown system {spec!r}; use null, baseline, or module.path:factory")
    module_name, attr = spec.split(":", 1)
    return getattr(importlib.import_module(module_name), attr)()
