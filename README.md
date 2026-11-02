# Mergen-Crew

An AI engineering team that keeps long-term ownership of parts of your repository, finds problems whenever you start a run, and proves what it finds before asking you to look.

You point it at a codebase once. It splits the code into subsystems and roles (Frontend, Backend, Infra, Security, …), keeps memory per subsystem, and on each run you start it investigates, reproduces problems with objective evidence, and applies verified fixes as small, separately revertable commits. Anything it cannot prove stays a finding in the run report and never becomes a code change.

The full design is in [docs/architecture.md](docs/architecture.md) (v0.2).

> **Status: phase 1 of 4.** Only the evaluation harness and the baseline exist so far. Everything else waits for this question to have a number: does the system find real bugs, cheaply, with few false positives, and better than a plain coding agent with a notes file?

## Principles

- **User-triggered, never a background loop.** Every run has a scope, a dollar budget and a mode you pick. Persistence is in what the engineers *know*, not in what they do on their own.
- **No oracle, no code change.** Findings carry evidence tiers (E0 hypothesis → E3 test fails before the fix, passes after, stable across reruns). Only E2/E3 changes are applied.
- **Adversarial verification.** A separate context, ideally a different model, tries to break each finding before you see it.
- **Your repo, your git.** Changes land on a local team branch as one commit per change, each with its regression test. No pull requests or GitHub required. Your uncommitted work is never touched.
- **Reviewer attention is the scarce budget.** At most 5 changes and 5 findings per run by default.
- **Measure first.** Every component has to beat a baseline on a benchmark, or it is simplified away.

## Phase 1: the evaluation harness

```
seed bugs into a repo  ->  run a system on each case  ->  score against ground truth
   crew eval seed             crew eval run                 crew eval score
```

### Install

Python 3.11+, no runtime dependencies.

```bash
pip install -e .          # installs the `crew` command
# or, without installing:
python3 -m mergen_crew --help
```

### 1. Build a benchmark suite

Famous bug datasets are probably in model training data, so the harness seeds fresh bugs into a Python repository instead:

```bash
crew eval seed --repo path/to/repo --out eval/benchmarks/myrepo-v1 \
  --test-command "python3 -m pytest -q" --count 10 --clean 1
```

- **Mutation operators:** flipped comparisons and boolean operators, `+`/`-` swaps, off-by-one boundaries, negated conditions, and dropped guard `raise`s (missing error handling).
- **Only surviving mutants are kept.** The repo's own test suite still passes with the bug in place, so each bug is "previously unknown" in the sense that matters. Pass `--include-caught` to keep the rest.
- **The bug location doesn't leak:** the edit is text-level, so formatting is unchanged, there is one bug per case, file mtimes are normalized, and the snapshot has no git history.
- **Clean cases** (`--clean N`) contain no seeded bug and measure false positives.

Each case is `case.json` (ground truth, never shown to the system) plus `snapshot/` (the repo the system gets). A ready-made suite, `eval/benchmarks/shop-v1`, is seeded from the small fixture library in `eval/fixtures/shop`.

### 2. Run a system

```bash
crew eval run --suite eval/benchmarks/shop-v1 --system baseline --budget 10
```

Each case gets a fresh copy of its snapshot as a one-commit git repo. What the system changes is saved as a `.patch` next to its findings in `.crew-eval/runs/…`.

Systems:

| System | What it is |
| --- | --- |
| `null` | Reports nothing. Smoke-tests the harness. |
| `baseline` | The bar to beat (architecture doc, section 2): one off-the-shelf coding agent session per subsystem, with the case budget split evenly. It is told to "find and prove bugs in this area" and gets a markdown notes file that persists across runs. `--no-memory` wipes notes between runs (the memory ablation). |
| `module.path:factory` | Any other system, including Mergen-Crew itself once it exists. `factory()` returns an object with `name` and `run(workdir, case, budget_usd)`. |

#### Choosing the agent

The baseline isn't tied to one harness. Pick it with `--agent`:

| `--agent` | Command it runs | Usage it reads | Budget enforced by agent |
| --- | --- | --- | --- |
| `claude` (default) | `claude -p --output-format json --max-budget-usd {budget} …` | Dollar cost + tokens | Yes |
| `codex` | `codex exec --sandbox workspace-write --json -` | Tokens (JSONL `turn.completed`) | No |
| `gemini` | `gemini --approval-mode yolo --output-format json -p {prompt}` | Tokens (`stats.models`) | No |
| `custom` | Your `--agent-command` | Any of the above formats | No |

```bash
crew eval run --suite eval/benchmarks/shop-v1 --system baseline --agent codex --price 1.25/10
crew eval run --suite eval/benchmarks/shop-v1 --system baseline --agent custom \
  --agent-command "aider --yes-always --message-file {prompt_file}"
```

- **Prompt placement:** `--agent-command` overrides any preset's command line. It accepts `{prompt}` (the prompt as one argument), `{prompt_file}` (a path to it) and `{budget}`. With no prompt placeholder, the prompt goes on stdin.
- **Cost** comes from the agent if it reports dollars (Claude Code). Otherwise it comes from a `.crew/cost.json` (`{"cost_usd": …}`) that your wrapper writes. Otherwise it is tokens × `--price IN/OUT` (USD per million tokens). Failing all three it is recorded as **unknown**, never guessed. The score always shows tokens, and tokens per true finding, so agents can be compared even without prices.
- **Budget:** only Claude Code stops itself at the dollar budget. Other agents are bounded by `--agent-timeout` (default 3600s per session), and `crew` prints a note saying so. Compare them on cost or tokens per true finding, not on staying under budget.

> **Warning:** the baseline agent runs on your machine, in a temp directory, with permission to run commands (Codex's preset uses its own no-network `workspace-write` sandbox). It also executes the benchmark repo's tests. That is fine for suites you seeded from code you trust. Container isolation with no network (architecture doc, sections 11–12) comes later. Until then, wrap the agent with `--agent-command` (for example in `docker run …`) for anything else.

### 3. Score

```bash
crew eval score --suite eval/benchmarks/shop-v1 .crew-eval/runs/<run-a> .crew-eval/runs/<run-b>
```

This prints the systems side by side: recall, precision, false positives on clean cases, duplicates, cost, **cost per true finding** and **true findings per $100**. A finding matches a seeded bug if it names the same file and either a line within ±3 of the bug or the same function. Only findings at E1 or higher count by default (`--min-tier`).

Unmatched findings count as false positives, but they are listed in `score.json` for a human to check: a finding that isn't a seeded bug may still be a real one.

## Roadmap

About 12 weeks in four gated phases. A failed gate means fixing or simplifying before moving on. *(The phase breakdown below is a draft: the roadmap diagram in the design doc did not survive export.)*

1. **Benchmark and baseline** (now): harness, seeded suites, baseline numbers.
2. **The system's core loop:** environment bootstrapping, candidate generation, investigation, evidence gate, adversarial verifier, integration step. It must beat the baseline on findings per $100 and precision.
3. **Ownership and memory:** partitioning into the subsystem × role matrix, `ownership.yaml`, and revision-bound, diff-invalidated, taint-aware memory, each justified by an ablation.
4. **Live trial:** 10 user-started runs on a real repository, with at least 2 previously unknown findings accepted by maintainers and zero unauthorized actions.

MVP success criteria and the ablation plan are in [docs/architecture.md](docs/architecture.md), sections 2 and 15.

## Repository layout

```
mergen_crew/
  cli.py            # `crew` command
  eval/             # phase 1: model, seeding, agents (claude/codex/gemini/custom),
                    #   systems (null, baseline), runner, scoring
  core/             # the system itself (planned modules listed in core/__init__.py)
eval/
  fixtures/shop/    # small library with thin tests, used to seed the sample suite
  benchmarks/       # generated suites (case.json + snapshot/ per case)
docs/
  architecture.md   # design, v0.2
tests/              # harness tests
```

## Development

```bash
python3 -m unittest discover -s tests -t .
```

## Safety

Mergen can serve as the policy gateway that reviews what the team's agents do, but it is optional: a standalone policy engine stays supported, so Mergen-Crew can be adopted on its own.
