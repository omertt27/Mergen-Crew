import unittest

from mergen_crew.eval.model import BenchmarkCase, Finding, SeededBug, Subsystem
from mergen_crew.eval.scoring import CaseResult, format_table, matches, score_suite


def bug(case_id, file="shop/cart.py", start=20, end=21, symbol="Cart.remove"):
    return SeededBug(id=f"{case_id}-bug", file=file, line_start=start, line_end=end, kind="logic", mutation="cmp_flip", description="", symbol=symbol)


def case(case_id, bugs):
    return BenchmarkCase(id=case_id, test_command="true", subsystems=[Subsystem("sub-shop", ["shop/**"])], bugs=bugs)


def finding(file="shop/cart.py", line=20, tier="E3", symbol=None):
    return Finding(file=file, line=line, title="t", evidence_tier=tier, symbol=symbol)


class MatchTest(unittest.TestCase):
    def test_line_window(self):
        b = bug("c")
        self.assertTrue(matches(b, finding(line=23)))
        self.assertTrue(matches(b, finding(line=17)))
        self.assertFalse(matches(b, finding(line=25)))

    def test_symbol(self):
        b = bug("c")
        self.assertTrue(matches(b, finding(line=1, symbol="Cart.remove")))
        self.assertTrue(matches(b, finding(line=1, symbol="remove")))
        self.assertFalse(matches(b, finding(line=1, symbol="Other.remove")))

    def test_file_must_match(self):
        self.assertFalse(matches(bug("c"), finding(file="shop/pricing.py")))


class ScoreSuiteTest(unittest.TestCase):
    def setUp(self):
        self.cases = [case("case-001", [bug("case-001")]), case("case-002", [bug("case-002")]), case("clean-001", [])]

    def test_metrics(self):
        results = [
            CaseResult("case-001", [finding(), finding(line=21)], cost_usd=2.0, wall_seconds=10),  # TP + duplicate
            CaseResult("case-002", [finding(file="shop/pricing.py")], cost_usd=3.0, wall_seconds=5),  # FP, bug missed
            CaseResult("clean-001", [finding(file="shop/pricing.py")], cost_usd=1.0, wall_seconds=5),  # FP on clean
        ]
        s = score_suite("sys", self.cases, results)
        self.assertEqual((s.true_positives, s.false_positives, s.duplicates), (1, 2, 1))
        self.assertEqual(s.false_positives_on_clean_cases, 1)
        self.assertAlmostEqual(s.recall, 0.5)
        self.assertAlmostEqual(s.precision, 1 / 3)
        self.assertEqual(s.cost_usd, 6.0)
        self.assertEqual(s.cost_per_true_finding, 6.0)
        self.assertAlmostEqual(s.true_findings_per_100_usd, 100 / 6)
        self.assertIn("precision", format_table([s, s]))

    def test_tier_floor_hides_hypotheses(self):
        results = [
            CaseResult("case-001", [finding(tier="E0")], 1.0, 1),
            CaseResult("case-002", [], 1.0, 1),
            CaseResult("clean-001", [finding(tier="E0")], 1.0, 1),
        ]
        self.assertEqual(score_suite("sys", self.cases, results).reported, 0)
        self.assertEqual(score_suite("sys", self.cases, results, min_tier="E0").true_positives, 1)

    def test_unknown_cost_is_not_summed(self):
        results = [CaseResult("case-001", [finding()], None, 1), CaseResult("case-002", [], 1.0, 1), CaseResult("clean-001", [], 1.0, 1)]
        s = score_suite("sys", self.cases, results)
        self.assertIsNone(s.cost_usd)
        self.assertEqual(s.cost_unknown_cases, 1)
        self.assertIsNone(s.cost_per_true_finding)

    def test_missing_results_raise(self):
        with self.assertRaises(ValueError):
            score_suite("sys", self.cases, [CaseResult("case-001", [], 0.0, 0)])


if __name__ == "__main__":
    unittest.main()
