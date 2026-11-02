import ast
import tempfile
import unittest
from pathlib import Path

from mergen_crew.eval.model import BenchmarkCase, load_suite
from mergen_crew.eval.seeding import default_subsystems, find_mutations, is_test_file, seed_suite, subsystem_for

FIXTURE = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "shop"
FIXTURE_TESTS = "python3 -m unittest discover -s tests -t ."

SOURCE = '''\
def check(x, items, a, b):
    if x < 10:
        raise ValueError("small")
    if a and b:
        return x + 1
    return "é" + str(x in items)
'''


def by_operator(mutations):
    out = {}
    for m in mutations:
        out.setdefault(m.operator, []).append(m)
    return out


class FindMutationsTest(unittest.TestCase):
    def test_each_operator_is_found(self):
        ops = by_operator(find_mutations("m.py", SOURCE))
        for name in ("cmp_flip", "negate_condition", "drop_guard", "bool_flip", "arith_flip", "off_by_one"):
            self.assertIn(name, ops)

    def test_mutants_parse_and_change_only_their_span(self):
        for m in find_mutations("m.py", SOURCE):
            mutated = m.apply(SOURCE)
            ast.parse(mutated)
            self.assertNotEqual(mutated, SOURCE)
            self.assertEqual(mutated[: m.start], SOURCE[: m.start])
            self.assertEqual(mutated[m.start + len(m.replacement) :], SOURCE[m.end :])

    def test_specific_rewrites(self):
        ops = by_operator(find_mutations("m.py", SOURCE))
        cmp_lines = {(m.original, m.replacement) for m in ops["cmp_flip"]}
        self.assertIn(("<", "<="), cmp_lines)
        self.assertIn(("in", "not in"), cmp_lines)
        self.assertEqual(ops["drop_guard"][0].replacement, "pass")
        self.assertEqual({(m.original, m.replacement) for m in ops["bool_flip"]}, {("and", "or")})

    def test_string_concatenation_is_not_flipped(self):
        # "é" + str(...) would become a TypeError, which is not an interesting bug.
        self.assertEqual(len(by_operator(find_mutations("m.py", SOURCE))["arith_flip"]), 1)

    def test_offsets_handle_non_ascii(self):
        src = 'def f(s):\n    return "ü" + s if len(s) > 3 else s\n'
        (m,) = [m for m in find_mutations("m.py", src) if m.operator == "cmp_flip"]
        self.assertEqual(m.original, ">")
        self.assertIn("len(s) >= 3", m.apply(src))

    def test_symbol_names(self):
        src = "class Cart:\n    def total(self, n):\n        return n > 0\n"
        (m,) = [m for m in find_mutations("c.py", src) if m.operator == "cmp_flip"]
        self.assertEqual(m.symbol, "Cart.total")

    def test_module_level_code_and_main_guard_are_skipped(self):
        src = 'LIMIT = 3 + 1\nif __name__ == "__main__":\n    print(1 < 2)\n'
        self.assertEqual(find_mutations("m.py", src), [])


class HelpersTest(unittest.TestCase):
    def test_is_test_file(self):
        self.assertTrue(is_test_file("tests/test_x.py"))
        self.assertTrue(is_test_file("pkg/x_test.py"))
        self.assertTrue(is_test_file("conftest.py"))
        self.assertFalse(is_test_file("pkg/testing_tools.py"))

    def test_default_subsystems(self):
        subs = default_subsystems(["shop/a.py", "shop/b/c.py", "util/x.py", "main.py"])
        self.assertEqual([s.id for s in subs], ["sub-root", "sub-shop", "sub-util"])
        self.assertEqual(subsystem_for("shop/b/c.py", subs).id, "sub-shop")
        self.assertEqual(subsystem_for("main.py", subs).id, "sub-root")


class SeedSuiteTest(unittest.TestCase):
    def test_seed_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "suite"
            report = seed_suite(FIXTURE, out, test_command=FIXTURE_TESTS, count=3, clean_cases=1, seed=1)
            cases = load_suite(out)
            bug_cases = [c for c in cases if c.bugs]
            self.assertEqual(len(bug_cases), 3)
            self.assertEqual(len(cases), 4)
            self.assertGreaterEqual(report.tried, 3)

            for case in bug_cases:
                (bug,) = case.bugs
                # The snapshot carries the bug, and the ground truth stays out of it.
                snapshot_text = (case.snapshot / bug.file).read_text()
                original_text = (FIXTURE / bug.file).read_text()
                self.assertNotEqual(snapshot_text, original_text)
                self.assertFalse((case.snapshot / "case.json").exists())
                # Exactly one subsystem is not clean: the one holding the bug.
                self.assertEqual(sum(not s.clean for s in case.subsystems), 1)
                # Only one file differs from the original repository.
                changed = [
                    p.relative_to(case.snapshot).as_posix()
                    for p in case.snapshot.rglob("*.py")
                    if p.read_text() != (FIXTURE / p.relative_to(case.snapshot)).read_text()
                ]
                self.assertEqual(changed, [bug.file])

            clean = BenchmarkCase.load(out / "clean-001")
            self.assertEqual(clean.bugs, [])
            self.assertTrue(all(s.clean for s in clean.subsystems))

    def test_refuses_failing_base_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                seed_suite(FIXTURE, Path(tmp) / "s", test_command="python3 -c 'raise SystemExit(1)'", count=1)


if __name__ == "__main__":
    unittest.main()
