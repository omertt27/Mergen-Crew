"""The Mergen-Crew system itself. Not built yet.

Planned modules (architecture doc, section 14). Each one ships only once the
eval harness shows it moves the benchmark numbers:

    orchestrator/   run planner, leases, coordinator
    subsystems/     partitioning pipeline, role matrix, ownership.yaml
    engineers/      lenses, discovery, investigation
    memory/         store, staleness, taint, fingerprints
    environment/    bootstrap recipes, image cache, flakiness
    execution/      agent adapter, sandbox, worktrees
    verification/   evidence tiers, adversarial verifier
    integration/    apply patches, re-verify, commit, revert
    governance/     policies, permissions, budgets, audit
    intelligence/   signals now; tree-sitter/LSP later

The full system plugs into the harness as ``crew eval run --system
mergen_crew.core.<module>:factory``.
"""
