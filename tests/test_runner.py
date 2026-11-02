import contextlib
import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from mergen_crew.cli import main
from mergen_crew.eval.agents import resolve_agent
from mergen_crew.eval.model import Finding, SystemOutput, load_suite
from mergen_crew.eval.runner import load_results, run_suite
from mergen_crew.eval.scoring import score_suite
from mergen_crew.eval.seeding import seed_suite
from mergen_crew.eval.systems import BaselineAgent, NullSystem

FIXTURE = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "shop"
FIXTURE_TESTS = "python3 -m unittest discover -s tests -t ."


class PeekingSystem:
    """Cheats if it can: reports the bug from case.json. The runner must make that impossible."""

    name = "peeking"

    def run(self, workdir, case, budget_usd):
        self.saw_case_json = (workdir / "case.json").exists() or (workdir.parent / "case.json").exists()
        self.saw_git = (workdir / ".git").is_dir()
        (workdir / "shop" / "new_test.py").write_text("# added by system\n")
        return SystemOutput(findings=[Finding(file="shop/cart.py", line=1, title="guess", evidence_tier="E1")], cost_usd=0.5)


class BrokenSystem:
    name = "broken"

    def run(self, workdir, case, budget_usd):
        raise RuntimeError("boom")


def _fake_agent(tmp: Path) -> str:
    """A stand-in for `claude -p`: writes an empty findings file and a notes line, reports cost."""
    script = tmp / "fake_agent.py"
    script.write_text(
        textwrap.dedent(
            """
            import json, re, sys
            from pathlib import Path
            prompt = sys.stdin.read()
            findings = re.search(r"JSON array to (\\S+)\\.", prompt).group(1)
            notes = re.search(r"notes file is (\\S+)\\.", prompt).group(1)
            Path(findings).write_text("[]")
            with open(notes, "a") as f:
                f.write("visited\\n")
            print(json.dumps({"total_cost_usd": float(sys.argv[1]), "is_error": False}))
            """
        )
    )
    return f"{sys.executable} {script} {{budget}}"


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.suite = cls.tmp / "suite"
        seed_suite(FIXTURE, cls.suite, test_command=FIXTURE_TESTS, count=2, clean_cases=1, seed=3)
        cls.cases = load_suite(cls.suite)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_ground_truth_is_hidden_and_results_are_written(self):
        system = PeekingSystem()
        out = self.tmp / "run-peek"
        results = run_suite(system, self.cases, out, budget_usd=1.0, log=lambda *_: None)
        self.assertFalse(system.saw_case_json)
        self.assertTrue(system.saw_git)
        name, loaded = load_results(out)
        self.assertEqual(name, "peeking")
        self.assertEqual([r.case_id for r in loaded], [r.case_id for r in results])
        patch = (out / "cases" / f"{self.cases[0].id}.patch").read_text()
        self.assertIn("new_test.py", patch)

    def test_broken_system_is_recorded_not_fatal(self):
        results = run_suite(BrokenSystem(), self.cases, self.tmp / "run-broken", budget_usd=1.0, log=lambda *_: None)
        self.assertTrue(all(r.error and "boom" in r.error for r in results))
        self.assertEqual(score_suite("broken", self.cases, results).errors, len(self.cases))

    def test_null_system_scores_zero_recall(self):
        results = run_suite(NullSystem(), self.cases, self.tmp / "run-null", budget_usd=1.0, log=lambda *_: None)
        s = score_suite("null", self.cases, results)
        self.assertEqual(s.recall, 0.0)
        self.assertIsNone(s.precision)

    def test_baseline_splits_budget_and_persists_notes(self):
        notes_dir = self.tmp / "notes"
        agent = BaselineAgent(agent=resolve_agent("custom", _fake_agent(self.tmp)), notes_dir=notes_dir)
        self.assertEqual(agent.name, "baseline-custom")
        case = self.cases[0]
        for i in range(2):
            (result,) = run_suite(agent, [case], self.tmp / f"run-baseline-{i}", budget_usd=10.0, log=lambda *_: None)
        self.assertIsNone(result.error)
        self.assertAlmostEqual(result.cost_usd, 10.0)  # budget split across subsystems, then summed
        sub_id = case.subsystems[0].id
        self.assertEqual((notes_dir / case.id / f"{sub_id}.md").read_text().count("visited"), 2)

    def test_baseline_with_token_only_agent_and_prompt_file(self):
        """A Codex-style agent: prompt via {prompt_file}, JSONL token usage, no dollar figure."""
        script = self.tmp / "fake_codex.py"
        script.write_text(
            textwrap.dedent(
                """
                import json, re, sys
                from pathlib import Path
                prompt = Path(sys.argv[1]).read_text()
                Path(re.search(r"JSON array to (\\S+)\\.", prompt).group(1)).write_text(
                    json.dumps([{"file": "shop/cart.py", "line": 1, "title": "x", "evidence_tier": "E1"}])
                )
                print(json.dumps({"type": "thread.started"}))
                print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 200}}))
                """
            )
        )
        case = self.cases[0]
        cmd = f"{sys.executable} {script} {{prompt_file}}"
        unpriced = BaselineAgent(agent=resolve_agent("custom", cmd), notes_dir=None)
        (r1,) = run_suite(unpriced, [case], self.tmp / "run-codex-1", budget_usd=1.0, log=lambda *_: None)
        n = len(case.subsystems)
        self.assertEqual(len(r1.findings), n)
        self.assertEqual((r1.input_tokens, r1.output_tokens), (1000 * n, 200 * n))
        self.assertIsNone(r1.cost_usd)  # no price given: unknown, not guessed

        priced = BaselineAgent(agent=resolve_agent("custom", cmd), notes_dir=None, price=(3.0, 15.0))
        (r2,) = run_suite(priced, [case], self.tmp / "run-codex-2", budget_usd=1.0, log=lambda *_: None)
        self.assertAlmostEqual(r2.cost_usd, n * (1000 * 3 + 200 * 15) / 1e6)

    def test_missing_agent_binary_is_a_case_error(self):
        agent = BaselineAgent(agent=resolve_agent("custom", "definitely-not-an-agent-xyz"), notes_dir=None)
        (result,) = run_suite(agent, [self.cases[0]], self.tmp / "run-missing", budget_usd=1.0, log=lambda *_: None)
        self.assertIn("agent command not found", result.error)

    def test_cli_run_and_score(self):
        out = self.tmp / "run-cli"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(main(["eval", "run", "--suite", str(self.suite), "--system", "null", "--out", str(out)]), 0)
            self.assertEqual(main(["eval", "score", "--suite", str(self.suite), str(out)]), 0)
        score = json.loads((out / "score.json").read_text())
        self.assertEqual(score["cases"], len(self.cases))
        self.assertIn("recall", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
