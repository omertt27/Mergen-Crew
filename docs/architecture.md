# Architecture: Autonomous AI Engineering Team

Open-source system design · v0.2 · Nov 3, 2026 · @omer

> In this repo the project is **Mergen-Crew**: the package is `mergen_crew` (section 14's `engineering-team/`) and the CLI is `crew` (section 6's `team`). The two embedded diagrams (sections 3 and 15) did not survive the Markdown export.

## What changed since v0.1

v0.2 keeps the core abstraction and reorders everything around one question: can the system find real bugs cheaply, with few false positives, better than a simple baseline?

| Area | v0.1 | v0.2 |
| --- | --- | --- |
| Trigger | Autonomous, recurring self-directed loop | User-triggered runs with scope, budget and mode; no background loop |
| What persists | Identity, memory and activity | Identity, ownership and memory; activity happens only inside runs |
| Roadmap order | Indexing infrastructure first, evaluation last | Benchmark and baseline in weeks 1–2; every component must move the numbers |
| Baseline | None | A plain coding agent with notes files, same scope and budget; the architecture must beat it |
| Role partitioning | One directory per agent, auto-partitioned | Subsystem × role matrix: vertical primary roles, horizontal secondary roles, user-edited |
| Default output | Pull request | Verified changes applied directly to the local codebase as revertable commits; no PRs or GitHub required |
| Scarce budget | Dollars | Reviewer attention: small, separately revertable changes, capped per run |
| Evidence | "Reproduce" undefined | Explicit evidence tiers; no oracle means no code change |
| Verification | Independent check | Adversarial verifier in a separate context, ideally a different model |
| Engineering Lead | LLM agent in MVP | Removed from MVP; deterministic coordinator |
| Cross-domain fixes | Split patches per owner | One author, other owner as required reviewer |
| Memory | Revision-tagged | Plus symbol-diff invalidation and taint tracking |
| Credentials | "Scoped credentials" | Workers only touch isolated copies; one integration step writes to the codebase |
| Test environments | Not addressed | Dedicated bootstrapping component with flakiness checks |
| Prioritization | Fixed formula | Logged, calibrated, then learned across runs |
| Cost per investigation | $0.25 | $1–5 planning assumption; cost per verified finding is the metric |
| Success criterion | One bug in seven days | Precision, acceptance rate, and beating the baseline |

## 1. Product vision

The system keeps durable ownership of parts of a repository, finds work whenever the user starts a run, and proves what it finds before asking a human to look.

The central abstraction is long-term ownership, not a task. Point it at a codebase once, and the system:

- Partitions the repository into subsystems and roles, and lets the user edit the map.
- Keeps persistent memory and investigation history per subsystem.
- Runs only when the user starts a run, on the scope, budget and mode the user picks.
- Reproduces suspected problems with objective evidence.
- Applies verified changes directly to the codebase, and reports what it could not fix, with evidence.
- Learns from human triage decisions what this team considers valuable.

Persistence means durable identity, ownership, knowledge and accountability. It does not mean a continuously running LLM process.

### The falsifiable core question

Every design choice in this document serves one question: **does this system find real, previously unknown problems at an acceptable cost per verified finding, with few enough false positives that maintainers keep it on, and does it beat a simple baseline?**

If the answer is no, the team metaphor, the coordination layer and the dashboard do not matter. The roadmap is ordered to answer this question first.

## 2. Evaluation first

The benchmark and the baseline are built in week 1, and no later component ships unless it improves the benchmark numbers.

### Benchmark

A set of repository snapshots with known defects. The system receives the snapshot but not the issue text or bug location.

- **Contamination control:** famous bug datasets are likely in model training data. Use bugs fixed after the model's training cutoff, or inject bugs through mutation testing and semantic bug seeding.
- **Mix of defect types:** logic errors, missing error handling, concurrency issues, authorization gaps, and at least some "no bug here" subsystems to measure false positives.
- **Scoring:** recall (seeded or known bugs found), precision (reported findings that are real), cost per true finding, and wall-clock time.

### Baseline

The architecture must beat the simplest thing that could work:

> An off-the-shelf coding agent runs per subsystem, with the same scope and budget, with a prompt such as "find and prove bugs in this area", plus a markdown notes file the agent reads and updates.

The comparison of verified bugs per $100 and finding precision, system versus baseline, is the headline result of the MVP. If the full system does not clearly win, simplify toward the baseline.

### Ablations

Each major design bet gets an ablation run on the benchmark:

| Bet | Ablation |
| --- | --- |
| Role-specialized engineers help | Same domain memory, generic agent |
| Deterministic candidate generation helps | Agent explores freely with ripgrep only |
| Long-term memory helps | Memory wiped between runs |
| Adversarial verifier helps | Implementer self-verifies |

### Success criteria for the MVP

- Finding precision of at least 50%, judged by a human on every reported finding.
- Beats the baseline on verified findings per $100 by a clear margin on the benchmark.
- On a live repository over 10 user-started runs: at least two previously unknown, reproducible findings accepted by maintainers, zero duplicate changes, zero unauthorized actions.
- Maintainer-rated noise stays below a threshold the maintainer sets before the trial.

## 3. High-level architecture

The system separates deciding (control plane), doing (sandboxed workers), and writing (integration into the codebase), with the eval harness scoring all of it. Every run is started by the user.

&#91;embedded content: system architecture · 4 layers plus eval harness\]

Workers change isolated copies and emit patches with evidence; only the integration step writes to the codebase, and human triage decisions flow back into memory for the next run.

## 4. Core components

The MVP is a modular monolith plus isolated workers; only execution needs process or container isolation.

| Component | Responsibility | Implementation | MVP? |
| --- | --- | --- | --- |
| Eval harness | Score the system against benchmark and baseline | Snapshot runner, scoring scripts | Yes, week 1 |
| Ownership map | Partition code into subsystems and roles | Partitioning pipeline (section 5), user-edited YAML | Yes |
| Environment bootstrapper | Get each repo's tests running reliably | Cached container images, setup recipes | Yes |
| Run planner | Plan and rank targets inside a user-started run | Logged scores, learned across runs | Yes |
| Work coordinator | Deduplicate, lease, prevent collisions | PostgreSQL transactions + leases | Yes |
| Agent runtime | Run reasoning and coding sessions | Pluggable adapter over an existing coding agent | Yes |
| Memory service | Subsystem knowledge and investigation history | PostgreSQL + pgvector, taint and staleness flags | Yes |
| Verification engine | Prove findings, check fixes adversarially | Separate-context verifier, flakiness reruns | Yes |
| Run report | Run report: changes applied, findings not fixed | Dashboard or CLI; keep or revert per change | Yes |
| Integration step | Apply verified patches to the codebase | Re-apply, re-verify, commit to team branch; sole writer | Yes |
| Safety gateway | Authorize dangerous actions, audit | Policy engine, optionally Mergen | Yes |
| Codebase indexer | Symbol graph, complexity, coverage signals | Tree-sitter, LSP | Later, if ablation shows gains |
| Engineering Lead | Cross-team judgment | LLM agent with limited authority | Later, if needed |

## 5. Partitioning the codebase into roles

Roles are not directories: the codebase is split into **subsystems** (where code lives), each subsystem gets **one primary role plus secondary roles** (what kind of risk it carries), and engineers are assigned a balanced set of matrix cells.

A one-directory-per-agent split fails because the two axes differ. Frontend and infra are mostly *vertical*: they map to places in the tree. Security, reliability and test quality are *horizontal*: an auth check in the frontend, a SQL query in a backend helper, and a secret in a CI file all belong to security. A partition that only cuts by folder either gives security nothing or gives it everything.

### Two kinds of roles

| Kind | Roles | Derived from |
| --- | --- | --- |
| Vertical (layer) | Frontend, Backend/API, Data, Infra/CI, Mobile, SDK | Where the subsystem sits: framework, file types, runtime |
| Horizontal (concern) | Security, Reliability/Concurrency, Test quality, Performance, Dependencies | Risk surfaces found inside the subsystem |

Every subsystem gets exactly one vertical role as **primary** (accountability) and zero or more horizontal roles as **secondary** (they may investigate and review, but the primary owns the subsystem's memory and backlog).

### The partitioning pipeline

1. **Exclude** generated code, vendored dependencies, lockfiles, fixtures and build output (from `.gitignore`, `linguist-generated`, known paths).
2. **Seed boundaries** from what the repo already declares: monorepo workspaces and packages, language package boundaries (Go packages, Python packages, Cargo crates), and CODEOWNERS. These are strong priors humans already agreed on.
3. **Refine by structure:** build an import graph and a co-change graph from git history (files that change together in the same commits). Cluster with community detection, keeping seeded boundaries unless the evidence strongly disagrees. Co-change matters most in monoliths with weak folder structure.
4. **Size each subsystem to one investigation:** a subsystem should be explorable in one agent session, meaning its public interface plus core files fit in a working context budget (roughly 5k to 30k lines as a starting heuristic). Split clusters above the cap, merge tiny ones into their nearest neighbor.
5. **Tag traits, deterministically first:** layer (UI, API, data, infra, build), language and framework, and risk surfaces detected from imports and patterns: auth and session libraries, crypto, raw SQL, deserialization, file and network IO, subprocess calls, locks and async primitives, payment SDKs.
6. **Summarize with one cheap LLM pass per subsystem:** a short description, its public interface, a confirmation or correction of the tags, and anything the heuristics missed. This is a one-time cost per subsystem, refreshed only when it changes significantly.
7. **Derive the role catalog for this repo:** a role exists only if it has enough surface. A repo with no auth surface gets no Security engineer; a backend-only service gets no Frontend engineer. Vertical primary comes from the layer tag; horizontal secondaries come from risk tags above a weight threshold.
8. **Balance portfolios:** group matrix cells into engineers, capped at 3 to 5 engineers per repo by default. Balance by **risk-weighted size** (lines × risk score × churn), not file count. Small horizontal roles fold into the nearest generalist lens.
9. **Mark contracts:** files at boundaries between subsystems (shared types, API schemas, protobufs, public interfaces) become **contract files** with every adjacent owner as reviewer. Most cross-team bugs live here.
10. **Human review:** the proposal is written to `ownership.yaml` and shown as a map. The user edits it; edits are **pinned** and always win over later automatic proposals.

### Example: one repository's matrix

P = primary, S = secondary. Paths are illustrative.

| Subsystem | Frontend | Backend | Infra/CI | Security | Reliability | Test quality |
| --- | --- | --- | --- | --- | --- | --- |
| `apps/web` | P |  |  | S (auth UI, token storage) |  | S |
| `services/api` |  | P |  | S (authz checks) | S (async jobs) | S |
| `packages/policy` |  | P |  | S (heavy) | S (concurrency) | S |
| `packages/shared-types` (contract) | S | S |  |  |  |  |
| `infra/terraform` |  |  | P | S (IAM, secrets) |  |  |
| `.github/workflows` |  |  | P | S (token scopes) |  |  |

This repo gets four engineers: Frontend, Backend, Infra, and Security. Reliability and test quality do not have enough surface for their own engineers, so they run as extra lenses inside the Backend and Frontend engineers.

### Records

```yaml
subsystem:
  id: sub-policy                # stable; survives renames via git lineage
  paths: [packages/policy/**]
  primary_role: backend
  secondary_roles:
    security: 0.9               # risk weight from tags
    reliability: 0.5
  contracts: [packages/shared-types/policy.ts]
  human_owner: "@alice"
  pinned: false
  revision: 4f9c2a1

engineer:
  id: eng-security-01
  role: security
  cells:                        # matrix cells this engineer covers
    - {subsystem: sub-policy, as: secondary}
    - {subsystem: sub-api, as: secondary}
    - {subsystem: sub-terraform, as: secondary}
  permissions:
    write_tests: true
    file_findings: true
    apply_changes: verified_only
    write_to_main_branch: false
```

### Keeping the partition current

- Re-partitioning runs only when the user asks, or as a suggestion after a large structural change (a new package, a big move). It is shown as a diff to the current map, never applied silently.
- Subsystem ids are stable across renames, so memory moves with the code.
- Small repos (under roughly 20k lines) skip the matrix: one generalist engineer applies each role's lens in turn.
- Whether specialized roles beat one generalist with the same memory is tested by ablation (section 2).

## 6. User-triggered runs

Nothing runs unless the user starts it: each run has a scope, a budget and a mode chosen by the user, and between runs the engineers keep their memory so the next run picks up where the last one stopped.

Persistence stays in what engineers *know*, not in what they *do* on their own. This makes cost predictable, keeps the user in control of when repository code is executed, and removes the hardest part of autonomy: deciding when it is worth spending money.

### Starting a run

Runs start from the dashboard or a CLI, for example `team run security packages/auth --budget 10 --mode apply`.

| Setting | Options | Default |
| --- | --- | --- |
| Scope | Whole team · one role · one subsystem · a path · a question ("is session reuse possible?") | Whole team |
| Budget | Dollar cap and wall-clock cap for the run | $10, 60 minutes |
| Mode | Apply verified changes to the codebase · findings only (read-only) | Apply verified changes |
| Focus | Continue from last run's next targets · fresh targets · changes since a commit or tag | Continue |

If the user wants a recurring run, they wire it to their own trigger (a CI job on release branches, for example). It is still their decision and their schedule; the system has no background loop.

### Run lifecycle

1. **Plan:** the run planner lists candidate targets inside the scope, using cheap signals and memory: churn since the last run, untested paths, risk tags from the partition, the last run's suggested next targets, and rejected finding classes as negative signal.
2. **Rank:** targets are scored and the top ones fit into the budget. Scores and outcomes are logged so ranking can be calibrated over many runs, and later learned with a bandit across runs.
3. **Investigate:** each engineer in scope takes targets from its own matrix cells, under leases, until the budget or the target list runs out.
4. **Verify:** every candidate finding goes through the evidence gate and the adversarial verifier (section 7).
5. **Report:** the run ends with a report: findings by evidence tier, what was investigated and found clean, unconfirmed hypotheses, cost, and suggested next targets.
6. **Review:** the user keeps or reverts each change and triages the findings. Decisions update memory before the next run.

A run stops early when no remaining target clears the value threshold, and returns the unused budget instead of inventing low-value work.

### Candidate signals

- Churn and bug-fix history since the last run.
- Low or missing test coverage, where it can be collected.
- Risk surfaces from the partition tags: auth, parsing, SQL, deserialization, concurrency.
- Error-handling paths and TODO/FIXME density.
- Time since the subsystem was last investigated.

Symbol-graph signals such as complexity and centrality are added only if an ablation shows they improve finding precision.

## 7. Evidence tiers and adversarial verification

A finding without an objective oracle is reported as unconfirmed and never becomes a code change.

### Evidence tiers

| Tier | Finding type | Required evidence | Allowed output |
| --- | --- | --- | --- |
| E3 | Bug | New test fails on base revision, passes after fix, stable across reruns | Change applied to the codebase |
| E3 | Security issue | Working proof of concept in the sandbox, plus regression test | Reported to the user first; change applied only after approval |
| E2 | Improvement | Measurable metric moves: benchmark, coverage, removed dead code, fewer warnings | Change applied to the codebase |
| E1 | Suspected bug | Failing test exists but no fix yet, or fix not yet verified | Finding only |
| E0 | Hypothesis | Reasoning or static signal, no reproduction | Recorded as unconfirmed, not shown by default |

### Adversarial verifier

The verifier runs in a separate context, ideally on a different model, and its job is to break the finding rather than confirm it. It checks for:

- Tests that assert the buggy behavior, or that only exercise mocks.
- Tests that pass for a reason unrelated to the claimed fix.
- "Fixes" that change intended behavior: the verifier looks for docs, comments, other tests or call sites that rely on the old behavior.
- Overbroad patches that touch more than the finding requires.
- Flakiness: the failing test is run N times (default 5) on the base revision and N times after the fix; any inconsistency downgrades the tier.

The implementer never judges its own fix alone. A verifier rejection returns the finding to E1 with the verifier's reasoning attached.

## 8. Output: changes to the codebase and the run report

The main output is verified changes made directly to the codebase; anything the team cannot fix and verify goes into the run report instead.

There is no pull-request flow and no GitHub dependency. The team works on a local repository, and git is the review and undo mechanism.

### How changes land

- Changes go to a team branch in the local repository (for example `ai-team/run-142`), or to the current branch if the user opts in. Nothing is written to the main branch without the user.
- One commit per change. The message names the finding, the evidence tier and the regression test, so each change can be read and reverted on its own.
- Every change ships with the test that proves it.
- The user's uncommitted work is never touched: files with local modifications are skipped and the finding is reported instead.
- Before writing, the integration step re-applies the patch to the current state of the codebase and re-runs verification. A conflict or new failure turns the change back into a finding.

### Run report

Each run ends with a report in the dashboard or CLI:

| Section | Contents |
| --- | --- |
| Changes applied | Diff, commit, evidence tier, test, verifier verdict |
| Findings not fixed | Reproduction or failing test, suggested approach, why it was not applied |
| Investigated and clean | Targets checked with nothing found, so they are not re-checked needlessly |
| Unconfirmed | Hypotheses without evidence, hidden by default |
| Cost and next targets | Spend against budget, suggested targets for the next run |

### Review

The user keeps, reverts or asks for a revision of each change (for example `team revert 142.3`). Every decision, and the reason for a revert, is stored in subsystem memory so the same class of change is not repeated.

### Attention limits

Reviewer attention is still the scarce resource: the user has to understand what changed in their own codebase.

| Limit | Default | Configurable |
| --- | --- | --- |
| Changes applied per run | 5 | Yes |
| Lines changed per change | 200; larger fixes are reported with a proposed patch | Yes |
| Findings shown per run | 5 | Yes |
| Security-relevant changes | Applied only after the user approves | No |

Publishing changes elsewhere, such as opening a pull request from the team branch, is left to the user and their own tools.

## 9. Memory: revision-bound, diff-invalidated, taint-aware

Memory is a core feature, so it gets the same rigor as code: every entry knows what revision it describes, what symbols it references, and whether it came from untrusted content.

### Memory layers

| Layer | Contents |
| --- | --- |
| Organizational | Architecture notes, interfaces, confirmed defects, decisions, triage policy learned from humans |
| Subsystem | Known failure modes, investigated hypotheses, rejected finding classes, next targets |
| Evidence | Commit SHA, test results, stack traces, reproductions, linked commits, verifier verdicts |

### Staleness: diff-based invalidation

- Every memory stores the revision it was written at and the symbols and paths it references.
- At the start of each run, the system diffs symbols changed since the previous run and marks referencing memories **stale**.
- Stale memories are retrieved as historical context, clearly labeled, never as current fact.
- An investigation that re-confirms a stale memory refreshes it to the new revision.

### Taint: untrusted-origin tracking

Memories derived from repository content (code comments, docs, issues, test output) are **tainted**. A prompt injection that lands in long-term memory persists into every future session, which is worse than a one-off injection.

- Tainted memories are presented to the model as quoted data, never in the instruction position.
- A tainted memory cannot change permissions, budgets, ownership, or triage policy.
- Only human triage decisions and verifier verdicts produce untainted memory.

### Deduplication fingerprint

Each investigation has a fingerprint from hypothesis category, affected symbols, and evidence signature (failing assertion, stack frame). This catches duplicates without merging distinct bugs in the same function.

## 10. Coordination and concurrency

Coordination in the MVP is deterministic code over PostgreSQL; there is no LLM coordinator until evidence shows one is needed.

### Cross-domain fixes

When a fix spans two subsystems, one engineer writes the whole change, and the other subsystem's primary lens and human owner become required reviewers. v0.1 split one fix into separate per-owner patches, which is fragile to integrate and verify.

### Concurrency rules

1. **Leases:** an investigation or implementation is claimed with a lease (expiry + heartbeat). An expired lease returns the work to the queue.
2. **Dedup before claim:** the fingerprint is checked inside the same transaction that takes the lease.
3. **Isolated worktrees:** every implementation gets its own worktree and branch.
4. **Cross-subsystem writes:** allowed in the worktree, but flagged, and they add the other owner as required reviewer.
5. **Moving base:** before applying a change, the integration step re-applies it to the current state of the codebase and re-runs verification. A conflict or new failure returns the finding to E1.
6. **Human activity:** files with uncommitted local changes, or symbols touched by recent human commits, are never modified; the finding goes into the run report instead.
7. **Expiry:** untriaged findings older than 30 days, or whose symbols changed, are re-verified or closed.

### Engineer states

`IDLE` · `EXPLORING` · `INVESTIGATING` · `IMPLEMENTING` · `VERIFYING` · `WAITING_TRIAGE` · `BLOCKED` · `PAUSED`

## 11. Environment bootstrapping

Getting an arbitrary repository's tests to run reliably is a first-class component, because every evidence tier above E0 depends on it.

### Bootstrap phase (once per repo, refreshed on dependency changes)

1. Detect the stack from manifests and CI config (`package.json`, `pyproject.toml`, `go.mod`, `.github/workflows`).
2. Reuse the repository's own CI steps where possible; they are the best record of how tests actually run.
3. Build a container image with dependencies installed, then cache it by lockfile hash.
4. Start required services (databases, queues) from docker-compose or CI service definitions.
5. Run the full test suite once to record a **baseline**: which tests pass, fail, or flake on the current revision.
6. Store the recipe so a human can fix it once when automatic setup fails.

### Flakiness handling

- Tests that fail intermittently in the baseline are marked flaky and excluded as evidence.
- New failing tests are rerun 5 times before counting as a reproduction.
- A repository where the suite cannot be made to run is limited to E0–E1 findings, and the dashboard says so.

### Network

Dependency installation happens during image build with allowlisted registries. Investigation and test runs have no network egress.

## 12. Execution, security boundaries and credentials

Workers run untrusted code with no credentials and no network; they emit a patch plus evidence, and only the integration step writes to the codebase.

### Deployable pieces

- **Control plane (Python, FastAPI):** repositories, subsystems, engineers, run orchestration, leases, budgets, triage dashboard API.
- **Worker plane (Python + Docker):** agent sessions, exploration, tests, patches. Each run gets a fresh container from the cached image, with CPU, memory and time limits.
- **Integration step:** the only component that writes to the codebase. It receives a patch and evidence bundle, re-applies it to the current codebase, re-verifies, and commits it to the team branch.
- **State plane:** PostgreSQL with pgvector; PostgreSQL-backed job queue. Redis only if throughput requires it.

### Default policies

| Operation | Default |
| --- | --- |
| Read repository, search, analyze | Allowed |
| Run tests, modify worktree | Allowed in sandbox, no network |
| Install dependencies | Image build only, allowlisted registries |
| Credentials inside worker | Never |
| Read production secrets, change infrastructure | Denied |
| Write to the codebase | Integration step only, team branch, per-run limits |
| Merge team branch into main | Human approval |
| Delete branches or repositories | Denied |
| Security findings | Private to human owner |

### Prompt injection

Repository content, issues, docs and test output are untrusted input. They cannot change permissions, budgets, ownership or policies, and memory derived from them is tainted (section 9). Every applied change appears as a diff in the run report, and nothing is written that the verifier has not seen.

Mergen can serve as the policy gateway and as a live testbed, but a standalone policy engine remains supported so the project can be adopted independently.

## 13. Cost model

Plan for $1–5 per agentic investigation, not $0.25, and judge the system by cost per verified finding rather than spend per run.

Agentic sessions that read repository context, run tests and iterate typically use far more tokens than a single call. The figures below are planning assumptions to be replaced by measured numbers from the benchmark.

| Run type | Investigations | At $1 each | At $5 each |
| --- | --- | --- | --- |
| Focused (one subsystem or one question) | about 5 | $5 | $25 |
| One role across its cells | 10–15 | $10–15 | $50–75 |
| Whole team (4 engineers × 8) | about 32 | $32 | $160 |

Model cost only; excludes compute, sandboxes, image builds and human review time.

### Controls

- Every run has a hard dollar cap set by the user; an optional monthly cap per repository sits above it.
- Deterministic candidate generation runs first, so the model is activated only for targets above the value threshold.
- Investigations have a token and wall-clock ceiling; hitting it records the attempt as unconfirmed.
- A run ends early and returns unused budget when no target clears the value threshold.

### The metric that matters

Verified, accepted findings per $100. A system spending $2,400 a month that produces four accepted findings may be worth it; one spending $300 that produces none is not.

## 14. Repository structure and data model

The MVP layout puts the eval harness and environment bootstrapping at the top level, and leaves the full indexer as a later module.

```
engineering-team/
├── apps/
│   ├── api/
│   └── dashboard/            # run reports, findings, budgets
├── eval/                     # benchmark snapshots, baseline runner, scoring
├── core/
│   ├── orchestrator/         # run planner, leases, coordinator
│   ├── subsystems/           # partitioning pipeline, role matrix
│   ├── engineers/            # lenses, discovery, investigation
│   ├── memory/               # store, staleness, taint, fingerprints
│   ├── environment/          # bootstrap recipes, image cache, flakiness
│   ├── execution/            # agent adapter, sandbox, worktrees
│   ├── verification/         # evidence tiers, adversarial verifier
│   ├── integration/          # apply patches, re-verify, commit, revert
│   ├── governance/           # policies, permissions, budgets, audit
│   └── intelligence/         # signals now; tree-sitter/LSP later
├── workers/
├── migrations/
├── tests/
└── docker-compose.yml
```

### Tables

| Table | Stores |
| --- | --- |
| `repositories` | Connected repos, config, rate limits |
| `environments` | Bootstrap recipes, image hashes, test baselines, flaky tests |
| `subsystems` | Paths, primary and secondary roles, contracts, human owner, pinned flag, revision |
| `engineers` | Roles, matrix cells, permissions |
| `investigations` | Hypothesis, fingerprint, target, score, outcome |
| `findings` | Evidence tier, verifier verdict, triage decision and reason |
| `leases` | Claims with expiry and heartbeat |
| `memories` | Content, layer, revision, referenced symbols, stale flag, taint flag |
| `runs` | Scope, budget, mode, report, tokens, cost, status |
| `artifacts` | Test logs, patches, proofs, commit references |
| `approvals` | Actions awaiting human authorization |
| `eval_runs` | Benchmark results per system version and ablation |

## 15. Roadmap

The MVP takes about 12 weeks in four gated phases, and the first phase builds measurement, not infrastructure.

&#91;embedded content: MVP roadmap · 4 phases, 4 gates, 12 weeks\]

A failed gate means fixing or simplifying before moving on, not pushing ahead; if phase 1 cannot beat the baseline, the plan shifts toward the baseline design. Week counts are targets for a small team, not guarantees.

## 16. Metrics and open questions

Precision and accepted findings per dollar lead; activity metrics like investigations run are diagnostic only.

| Metric | Measures | Role |
| --- | --- | --- |
| Finding precision | Share of reported findings humans confirm | Primary |
| Accepted findings per $100 | Cost efficiency of useful output | Primary |
| Lift over baseline | Whether the architecture earns its complexity | Primary |
| Recall on benchmark | Share of seeded or known bugs found | Primary (benchmark only) |
| Maintainer noise rating | Whether the team would keep it on | Guardrail |
| Regression rate of applied changes | Safety of changes | Guardrail |
| Duplicate finding rate | Memory and coordination quality | Diagnostic |
| Verifier rejection rate | Implementer quality, verifier strictness | Diagnostic |
| Stale-memory hit rate | Whether invalidation works | Diagnostic |
| Subsystem coverage | Breadth of exploration | Diagnostic |
| Human prompts per accepted finding | Degree of autonomy | Diagnostic |

### Open questions

- Do role-specialized engineers beat one generalist with the same subsystem memory?
- Does import and co-change clustering produce better subsystems than workspace and CODEOWNERS boundaries alone?
- What subsystem size works best for one investigation session?
- How many risk tags must a subsystem have before a horizontal role earns its own engineer?
- Which candidate signals actually predict confirmed findings?
- What evidence standard works for improvements with no clear metric, such as readability or API design?
- How should the system behave on repositories whose test suites cannot be made to run?
- Is a separate verifier model worth the extra cost, or does a separate context on the same model suffice?
- What noise threshold do real maintainers tolerate before they stop running it?
