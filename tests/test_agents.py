import json
import tempfile
import unittest
from pathlib import Path

from mergen_crew.eval.agents import (
    PRESETS,
    Usage,
    build_argv,
    parse_any,
    parse_claude,
    parse_codex,
    parse_gemini,
    parse_price,
    resolve_agent,
    resolve_cost,
)

CLAUDE_OUT = json.dumps(
    {
        "type": "result",
        "is_error": False,
        "total_cost_usd": 0.42,
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 0, "output_tokens": 50},
    }
)
CODEX_OUT = "\n".join(
    json.dumps(e)
    for e in [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 300}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}},
    ]
)
GEMINI_OUT = json.dumps(
    {"response": "ok", "stats": {"models": {"gemini-x": {"tokens": {"prompt": 5000, "candidates": 400, "total": 5400}}}}}
)


class ParserTest(unittest.TestCase):
    def test_claude(self):
        u = parse_claude(CLAUDE_OUT)
        self.assertEqual((u.cost_usd, u.input_tokens, u.output_tokens), (0.42, 100, 50))

    def test_claude_error_is_noted(self):
        u = parse_claude(json.dumps({"is_error": True, "subtype": "error_max_budget_usd", "total_cost_usd": 1.0}))
        self.assertIn("error_max_budget_usd", u.note)

    def test_codex_sums_turns(self):
        u = parse_codex(CODEX_OUT)
        self.assertEqual((u.cost_usd, u.input_tokens, u.output_tokens), (None, 1300, 320))

    def test_gemini(self):
        u = parse_gemini(GEMINI_OUT)
        self.assertEqual((u.input_tokens, u.output_tokens), (5000, 400))

    def test_parse_any_and_garbage(self):
        self.assertEqual(parse_any(CODEX_OUT).input_tokens, 1300)
        self.assertEqual(parse_any(GEMINI_OUT).output_tokens, 400)
        self.assertEqual(parse_any(CLAUDE_OUT).cost_usd, 0.42)
        u = parse_any("plain text, no json")
        self.assertIsNone(u.cost_usd)
        self.assertIsNone(u.input_tokens)


class CommandTest(unittest.TestCase):
    def test_stdin_when_no_prompt_placeholder(self):
        argv, stdin = build_argv(PRESETS["codex"].command, prompt="P", prompt_file=Path("/x"), budget=1.5)
        self.assertEqual(stdin, "P")
        self.assertEqual(argv[:2], ["codex", "exec"])

    def test_prompt_as_argument(self):
        argv, stdin = build_argv(PRESETS["gemini"].command, prompt="multi\nline prompt", prompt_file=Path("/x"), budget=1)
        self.assertIsNone(stdin)
        self.assertEqual(argv[-1], "multi\nline prompt")

    def test_budget_and_prompt_file(self):
        argv, stdin = build_argv("agent --max {budget} --file {prompt_file}", prompt="P", prompt_file=Path("/p.md"), budget=2.5)
        self.assertEqual(argv, ["agent", "--max", "2.50", "--file", "/p.md"])
        self.assertIsNone(stdin)
        argv, _ = build_argv(PRESETS["claude"].command, prompt="P", prompt_file=Path("/x"), budget=2.5)
        self.assertIn("2.50", argv)

    def test_resolve_agent(self):
        self.assertTrue(resolve_agent("claude").budget_enforced)
        self.assertFalse(resolve_agent("claude", "my-wrapper {budget}").budget_enforced)
        self.assertFalse(resolve_agent("codex").budget_enforced)
        with self.assertRaises(ValueError):
            resolve_agent("custom")
        with self.assertRaises(ValueError):
            resolve_agent("nope")

    def test_parse_price(self):
        self.assertEqual(parse_price("3/15"), (3.0, 15.0))
        self.assertIsNone(parse_price(None))
        with self.assertRaises(ValueError):
            parse_price("3")


class CostTest(unittest.TestCase):
    def test_order_reported_then_file_then_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ".crew").mkdir()
            tokens = Usage(input_tokens=1_000_000, output_tokens=100_000)

            self.assertEqual(resolve_cost(Usage(cost_usd=0.5), work, (3, 15)), (0.5, "reported by agent"))

            (work / ".crew" / "cost.json").write_text('{"cost_usd": 0.7}')
            self.assertEqual(resolve_cost(tokens, work, (3, 15)), (0.7, "from .crew/cost.json"))
            self.assertFalse((work / ".crew" / "cost.json").exists())

            cost, source = resolve_cost(tokens, work, (3, 15))
            self.assertAlmostEqual(cost, 4.5)
            self.assertEqual(source, "tokens x --price")

            self.assertEqual(resolve_cost(tokens, work, None), (None, "unknown"))


if __name__ == "__main__":
    unittest.main()
