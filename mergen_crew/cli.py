"""`crew` command line.

Phase 1 only has the evaluation commands::

    crew eval seed  --repo PATH --out SUITE --test-command CMD [--count N]
    crew eval run   --suite SUITE --system {null,baseline,module:factory} [--agent claude|codex|gemini|custom] [--budget USD]
    crew eval score --suite SUITE RUN [RUN ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .eval.model import EVIDENCE_TIERS, load_suite
from .eval.runner import load_results, run_suite
from .eval.scoring import format_table, score_suite
from .eval.seeding import seed_suite
from .eval.agents import PRESETS, parse_price
from .eval.systems import load_system


def _cmd_seed(args: argparse.Namespace) -> int:
    report = seed_suite(
        Path(args.repo),
        Path(args.out),
        test_command=args.test_command,
        count=args.count,
        clean_cases=args.clean,
        seed=args.seed,
        survivors_only=not args.include_caught,
        timeout=args.timeout,
    )
    suite_meta = {
        "name": Path(args.out).name,
        "source_repo": Path(args.repo).name,
        "seed": args.seed,
        "survivors_only": not args.include_caught,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    (Path(args.out) / "suite.json").write_text(json.dumps(suite_meta, indent=2) + "\n")
    bug_cases = sum(1 for c in report.cases if c.bugs)
    print(
        f"wrote {bug_cases} bug cases + {len(report.cases) - bug_cases} clean cases to {args.out}\n"
        f"  {report.candidates} candidate mutations, {report.tried} tried, "
        f"{report.caught_by_existing_tests} rejected because existing tests caught them"
    )
    if bug_cases < args.count:
        print(f"  warning: asked for {args.count} bug cases, found {bug_cases}", file=sys.stderr)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    suite_dir = Path(args.suite)
    cases = load_suite(suite_dir)
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c.id in wanted]
    # Notes are per agent, so one harness never reads another's memory.
    notes_dir = None if args.no_memory else Path(args.notes_dir or Path(".crew-eval/notes") / suite_dir.name / args.agent)
    system = load_system(
        args.system,
        agent=args.agent,
        agent_command=args.agent_command,
        notes_dir=notes_dir,
        timeout=args.agent_timeout,
        price=parse_price(args.price),
    )
    agent = getattr(system, "agent", None)
    if agent is not None and not agent.budget_enforced:
        print(
            f"note: {agent.name} does not enforce the dollar budget itself; sessions are bounded by "
            f"--agent-timeout ({args.agent_timeout:.0f}s). Compare on cost or tokens per true finding.",
            file=sys.stderr,
        )
    out = Path(args.out or Path(".crew-eval/runs") / f"{suite_dir.name}-{system.name}-{datetime.now():%Y%m%d-%H%M%S}")
    if out.exists() and any(out.iterdir()):
        print(f"error: {out} is not empty", file=sys.stderr)
        return 2
    results = run_suite(system, cases, out, budget_usd=args.budget, suite=str(suite_dir))
    score = score_suite(system.name, cases, results, min_tier=args.min_tier)
    (out / "score.json").write_text(json.dumps(score.to_json(), indent=2) + "\n")
    print()
    print(format_table([score]))
    print(f"\nresults: {out}")
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    all_cases = load_suite(Path(args.suite))
    scores = []
    for run_dir in args.runs:
        system, results = load_results(Path(run_dir))
        ran = {r.case_id for r in results}
        cases = [c for c in all_cases if c.id in ran]
        label = f"{system} ({Path(run_dir).name})" if len(args.runs) > 1 and args.label_dirs else system
        scores.append(score_suite(label, cases, results, min_tier=args.min_tier))
    print(format_table(scores))
    if args.json:
        print(json.dumps([s.to_json() for s in scores], indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crew", description="Mergen-Crew: an AI engineering team that owns parts of your repository.")
    sub = parser.add_subparsers(dest="command", required=True)

    ev = sub.add_parser("eval", help="benchmark and baseline (phase 1)")
    ev_sub = ev.add_subparsers(dest="eval_command", required=True)

    seed = ev_sub.add_parser("seed", help="build a benchmark suite by seeding bugs into a Python repository")
    seed.add_argument("--repo", required=True, help="repository to seed (its tests must pass)")
    seed.add_argument("--out", required=True, help="suite directory to create")
    seed.add_argument("--test-command", required=True, help='e.g. "python3 -m pytest -q"')
    seed.add_argument("--count", type=int, default=10, help="number of single-bug cases (default 10)")
    seed.add_argument("--clean", type=int, default=1, help="number of no-bug control cases (default 1)")
    seed.add_argument("--seed", type=int, default=0, help="random seed")
    seed.add_argument("--include-caught", action="store_true", help="also keep mutants the existing tests already catch")
    seed.add_argument("--timeout", type=float, default=300.0, help="test-suite timeout in seconds")
    seed.set_defaults(func=_cmd_seed)

    run = ev_sub.add_parser("run", help="run a system on every case of a suite and score it")
    run.add_argument("--suite", required=True)
    run.add_argument("--system", default="baseline", help="null | baseline | module.path:factory")
    run.add_argument("--budget", type=float, default=10.0, help="dollar budget per case (default 10)")
    run.add_argument("--out", help="results directory (default .crew-eval/runs/<suite>-<system>-<time>)")
    run.add_argument("--cases", help="comma-separated case ids to run")
    run.add_argument("--agent", default="claude", choices=[*PRESETS, "custom"], help="coding agent the baseline drives (default claude)")
    run.add_argument(
        "--agent-command",
        help="override the agent's command line. Placeholders: {prompt}, {prompt_file}, {budget}; "
        "without a prompt placeholder the prompt goes on stdin. Required with --agent custom",
    )
    run.add_argument("--agent-timeout", type=float, default=3600.0, help="seconds per agent session (default 3600)")
    run.add_argument("--price", help="USD per million input/output tokens, e.g. 3/15; used when the agent does not report dollars")
    run.add_argument("--notes-dir", help="where baseline notes persist across runs (default .crew-eval/notes/<suite>/<agent>)")
    run.add_argument("--no-memory", action="store_true", help="wipe baseline notes between runs (memory ablation)")
    run.add_argument("--min-tier", default="E1", choices=EVIDENCE_TIERS, help="lowest evidence tier that counts as reported (default E1)")
    run.set_defaults(func=_cmd_run)

    score = ev_sub.add_parser("score", help="score one or more result directories side by side")
    score.add_argument("--suite", required=True)
    score.add_argument("runs", nargs="+")
    score.add_argument("--min-tier", default="E1", choices=EVIDENCE_TIERS)
    score.add_argument("--label-dirs", action="store_true", help="label columns with the result directory name")
    score.add_argument("--json", action="store_true", help="also print full scores as JSON")
    score.set_defaults(func=_cmd_score)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
