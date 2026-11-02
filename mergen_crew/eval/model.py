"""Data model for benchmark cases, findings and results.

On disk a benchmark suite looks like::

    <suite>/
      suite.json                  # name, description
      <case-id>/
        case.json                 # ground truth: NEVER shown to the system under test
        snapshot/                 # the repository the system receives

Everything a system under test may see is in :class:`CaseView`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

EVIDENCE_TIERS = ("E0", "E1", "E2", "E3")


def tier_rank(tier: str) -> int:
    try:
        return EVIDENCE_TIERS.index(tier)
    except ValueError:
        raise ValueError(f"unknown evidence tier {tier!r}; expected one of {EVIDENCE_TIERS}") from None


@dataclass
class Subsystem:
    id: str
    paths: list[str]
    # Ground truth: no seeded bug lives here. Hidden from the system under test.
    clean: bool = False


@dataclass
class SeededBug:
    id: str
    file: str
    line_start: int
    line_end: int
    kind: str  # logic | missing_error_handling | ...
    mutation: str  # operator name, e.g. cmp_flip
    description: str
    symbol: str | None = None  # enclosing function, e.g. "Cart.total"
    original: str = ""
    mutated: str = ""


@dataclass
class BenchmarkCase:
    id: str
    test_command: str
    subsystems: list[Subsystem]
    bugs: list[SeededBug]
    source: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None  # directory holding case.json and snapshot/

    @property
    def snapshot(self) -> Path:
        assert self.path is not None
        return self.path / "snapshot"

    def view(self) -> "CaseView":
        return CaseView(
            id=self.id,
            test_command=self.test_command,
            subsystems=[{"id": s.id, "paths": list(s.paths)} for s in self.subsystems],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "test_command": self.test_command,
            "source": self.source,
            "subsystems": [asdict(s) for s in self.subsystems],
            "bugs": [asdict(b) for b in self.bugs],
        }

    @classmethod
    def load(cls, case_dir: Path) -> "BenchmarkCase":
        data = json.loads((case_dir / "case.json").read_text())
        return cls(
            id=data["id"],
            test_command=data["test_command"],
            source=data.get("source", {}),
            subsystems=[Subsystem(**s) for s in data["subsystems"]],
            bugs=[SeededBug(**b) for b in data["bugs"]],
            path=case_dir,
        )

    def save(self, case_dir: Path) -> None:
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "case.json").write_text(json.dumps(self.to_json(), indent=2) + "\n")
        self.path = case_dir


@dataclass
class CaseView:
    """What the system under test is allowed to know about a case."""

    id: str
    test_command: str
    subsystems: list[dict[str, Any]]


@dataclass
class Finding:
    file: str
    line: int
    title: str
    evidence_tier: str = "E0"
    description: str = ""
    kind: str | None = None
    symbol: str | None = None
    test: str | None = None  # path of the regression test, if any

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Finding":
        tier = str(data.get("evidence_tier", "E0")).upper()
        tier_rank(tier)
        return cls(
            file=str(data["file"]).removeprefix("./"),
            line=int(data.get("line", 0)),
            title=str(data.get("title", "")),
            evidence_tier=tier,
            description=str(data.get("description", "")),
            kind=data.get("kind"),
            symbol=data.get("symbol"),
            test=data.get("test"),
        )


@dataclass
class SystemOutput:
    findings: list[Finding]
    cost_usd: float | None = None  # None = unknown, which scoring reports explicitly
    input_tokens: int | None = None
    output_tokens: int | None = None
    notes: str = ""


def load_suite(suite_dir: Path) -> list[BenchmarkCase]:
    cases = [BenchmarkCase.load(p.parent) for p in sorted(suite_dir.glob("*/case.json"))]
    if not cases:
        raise FileNotFoundError(f"no cases (*/case.json) under {suite_dir}")
    return cases
