"""Scoring: recall, precision, cost per true finding, wall-clock.

A finding matches a seeded bug when it names the same file and either

* points at a line within ``line_tolerance`` of the bug's line range, or
* names the same enclosing symbol (``Cart.total``; a bare ``total`` also matches).

Each bug can be matched by at most one finding; extra findings on the same bug
count as duplicates, not as true positives.

Unmatched findings are counted as false positives, but they are listed in the
score so a human can relabel them: a finding that is not a seeded bug may still
be a real pre-existing bug. The architecture's precision target (>= 50%) is
judged by a human on live repositories; on the benchmark it is an upper bound
on noise.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .model import BenchmarkCase, Finding, SeededBug, tier_rank


@dataclass
class CaseResult:
    case_id: str
    findings: list[Finding]
    cost_usd: float | None
    wall_seconds: float
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class CaseScore:
    case_id: str
    bugs: int
    true_positives: int
    false_positives: int
    duplicates: int
    matched: dict[str, int] = field(default_factory=dict)  # bug id -> finding index
    unmatched_findings: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = None
    tokens: int | None = None
    wall_seconds: float = 0.0
    error: str | None = None


@dataclass
class SuiteScore:
    system: str
    min_tier: str
    cases: int
    bugs: int
    reported: int
    true_positives: int
    false_positives: int
    duplicates: int
    false_positives_on_clean_cases: int
    errors: int
    recall: float | None
    precision: float | None
    cost_usd: float | None
    cost_unknown_cases: int
    cost_per_true_finding: float | None
    true_findings_per_100_usd: float | None
    tokens: int | None  # input + output; comparable across agents when dollar cost is unknown
    tokens_per_true_finding: float | None
    wall_seconds: float
    per_case: list[CaseScore]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _symbol_match(bug: SeededBug, finding: Finding) -> bool:
    if not bug.symbol or not finding.symbol:
        return False
    if finding.symbol == bug.symbol:
        return True
    # A bare name ("total") matches the last component of a qualified one ("Cart.total").
    return "." not in finding.symbol and finding.symbol == bug.symbol.rsplit(".", 1)[-1]


def matches(bug: SeededBug, finding: Finding, line_tolerance: int = 3) -> bool:
    if finding.file != bug.file:
        return False
    if bug.line_start - line_tolerance <= finding.line <= bug.line_end + line_tolerance:
        return True
    return _symbol_match(bug, finding)


def score_case(case: BenchmarkCase, result: CaseResult, *, min_tier: str = "E1", line_tolerance: int = 3) -> CaseScore:
    floor = tier_rank(min_tier)
    reported = [f for f in result.findings if tier_rank(f.evidence_tier) >= floor]
    matched: dict[str, int] = {}
    unmatched: list[dict[str, Any]] = []
    duplicates = 0
    for i, finding in enumerate(reported):
        bug = next((b for b in case.bugs if matches(b, finding, line_tolerance)), None)
        if bug is None:
            unmatched.append(asdict(finding))
        elif bug.id in matched:
            duplicates += 1
        else:
            matched[bug.id] = i
    return CaseScore(
        case_id=case.id,
        bugs=len(case.bugs),
        true_positives=len(matched),
        false_positives=len(unmatched),
        duplicates=duplicates,
        matched=matched,
        unmatched_findings=unmatched,
        cost_usd=result.cost_usd,
        tokens=(result.input_tokens + result.output_tokens)
        if result.input_tokens is not None and result.output_tokens is not None
        else None,
        wall_seconds=result.wall_seconds,
        error=result.error,
    )


def score_suite(
    system: str,
    cases: list[BenchmarkCase],
    results: list[CaseResult],
    *,
    min_tier: str = "E1",
    line_tolerance: int = 3,
) -> SuiteScore:
    by_id = {r.case_id: r for r in results}
    missing = [c.id for c in cases if c.id not in by_id]
    if missing:
        raise ValueError(f"no results for cases: {', '.join(missing)}")
    per_case = [score_case(c, by_id[c.id], min_tier=min_tier, line_tolerance=line_tolerance) for c in cases]

    bugs = sum(s.bugs for s in per_case)
    tp = sum(s.true_positives for s in per_case)
    fp = sum(s.false_positives for s in per_case)
    dup = sum(s.duplicates for s in per_case)
    reported = tp + fp + dup
    known_costs = [s.cost_usd for s in per_case if s.cost_usd is not None]
    cost_unknown = len(per_case) - len(known_costs)
    # A total that silently skips unknown costs would understate spend, so report None instead.
    cost = sum(known_costs) if cost_unknown == 0 else None
    clean_ids = {c.id for c in cases if not c.bugs}
    tokens = None if any(s.tokens is None for s in per_case) else sum(s.tokens for s in per_case)

    return SuiteScore(
        system=system,
        min_tier=min_tier,
        cases=len(per_case),
        bugs=bugs,
        reported=reported,
        true_positives=tp,
        false_positives=fp,
        duplicates=dup,
        false_positives_on_clean_cases=sum(s.false_positives for s in per_case if s.case_id in clean_ids),
        errors=sum(1 for s in per_case if s.error),
        recall=tp / bugs if bugs else None,
        precision=tp / (tp + fp) if (tp + fp) else None,
        cost_usd=cost,
        cost_unknown_cases=cost_unknown,
        cost_per_true_finding=(cost / tp) if (cost is not None and tp) else None,
        true_findings_per_100_usd=(100 * tp / cost) if cost else None,
        tokens=tokens,
        tokens_per_true_finding=(tokens / tp) if (tokens is not None and tp) else None,
        wall_seconds=sum(s.wall_seconds for s in per_case),
        per_case=per_case,
    )


def _fmt(value: float | None, pattern: str) -> str:
    return "n/a" if value is None else pattern.format(value)


def format_table(scores: list[SuiteScore]) -> str:
    """Side-by-side comparison, e.g. system vs. baseline (the MVP headline result)."""
    rows = [
        ("cases", lambda s: str(s.cases)),
        ("seeded bugs", lambda s: str(s.bugs)),
        ("reported",lambda s: f"{s.reported} (>= {s.min_tier})"),
        ("true positives", lambda s: str(s.true_positives)),
        ("false positives", lambda s: str(s.false_positives)),
        ("  on clean cases", lambda s: str(s.false_positives_on_clean_cases)),
        ("duplicates", lambda s: str(s.duplicates)),
        ("recall", lambda s: _fmt(s.recall, "{:.0%}")),
        ("precision", lambda s: _fmt(s.precision, "{:.0%}")),
        ("cost", lambda s: _fmt(s.cost_usd, "${:.2f}") + (f" ({s.cost_unknown_cases} unknown)" if s.cost_unknown_cases else "")),
        ("cost / true finding", lambda s: _fmt(s.cost_per_true_finding, "${:.2f}")),
        ("true findings / $100", lambda s: _fmt(s.true_findings_per_100_usd, "{:.1f}")),
        ("tokens", lambda s: _fmt(s.tokens, "{:,}")),
        ("tokens / true finding", lambda s: _fmt(s.tokens_per_true_finding, "{:,.0f}")),
        ("wall-clock", lambda s: f"{s.wall_seconds:.0f}s"),
        ("errors", lambda s: str(s.errors)),
    ]
    header = ["metric", *[s.system for s in scores]]
    table = [header] + [[name, *[fn(s) for s in scores]] for name, fn in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in table]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)
