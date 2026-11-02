"""Adapters over off-the-shelf coding-agent CLIs (Claude Code, Codex, Gemini CLI, anything else).

An agent is a command template plus a parser for what it prints. Placeholders:

* ``{prompt}``       the prompt as one argument
* ``{prompt_file}``  path of a file holding the prompt
* ``{budget}``       this session's dollar budget, e.g. ``2.50``

If the template has neither prompt placeholder, the prompt goes on stdin.

Cost is taken, in order, from: what the agent itself reports in dollars; a
``.crew/cost.json`` (``{"cost_usd": 1.23}``) that a wrapper script may write;
tokens times the ``--price`` you give. Otherwise it is recorded as unknown, never
guessed. Only Claude Code enforces the dollar budget itself; other agents are
bounded by the session timeout, so compare them on cost per true finding, not
on whether they stayed under budget.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class Usage:
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    note: str = ""


@dataclass
class AgentPreset:
    name: str
    command: str
    parse: Callable[[str], Usage]
    budget_enforced: bool  # does the agent itself stop at {budget}?


def _json_objects(stdout: str) -> list[dict[str, Any]]:
    """The whole output as one JSON object, or one object per line (JSONL)."""
    stdout = stdout.strip()
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
        return [data] if isinstance(data, dict) else []
    except json.JSONDecodeError:
        pass
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def parse_claude(stdout: str) -> Usage:
    """`claude -p --output-format json`: one object with total_cost_usd and usage."""
    # Only Claude's result object; other agents' JSON also carries "usage" keys.
    results = [o for o in _json_objects(stdout) if o.get("type") == "result" or "total_cost_usd" in o]
    if not results:
        return Usage(note="no Claude result object in agent output")
    data = results[-1]
    usage = data.get("usage") or {}
    inp = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")) if usage else None
    cost = data.get("total_cost_usd")
    return Usage(
        cost_usd=float(cost) if cost is not None else None,
        input_tokens=inp,
        output_tokens=int(usage["output_tokens"]) if "output_tokens" in usage else None,
        note=f"agent error: {data.get('subtype', '')}" if data.get("is_error") else "",
    )


def parse_codex(stdout: str) -> Usage:
    """`codex exec --json`: JSONL events; token usage arrives on turn.completed events."""
    inp = out = 0
    seen = False
    for obj in _json_objects(stdout):
        usage = obj.get("usage")
        if obj.get("type") == "turn.completed" and isinstance(usage, dict):
            seen = True
            inp += int(usage.get("input_tokens") or 0)
            out += int(usage.get("output_tokens") or 0)
    if not seen:
        return Usage(note="no token usage in codex output")
    return Usage(input_tokens=inp, output_tokens=out)


def parse_gemini(stdout: str) -> Usage:
    """`gemini -o json`: stats.models.<model>.tokens.{prompt,candidates}."""
    objs = _json_objects(stdout)
    models = ((objs[-1].get("stats") or {}).get("models") or {}) if objs else {}
    if not models:
        return Usage(note="no token stats in gemini output")
    inp = sum(int((m.get("tokens") or {}).get("prompt") or 0) for m in models.values())
    out = sum(int((m.get("tokens") or {}).get("candidates") or 0) for m in models.values())
    return Usage(input_tokens=inp, output_tokens=out)


def parse_any(stdout: str) -> Usage:
    """For custom commands: try every known format."""
    for parser in (parse_claude, parse_codex, parse_gemini):
        usage = parser(stdout)
        if usage.cost_usd is not None or usage.input_tokens is not None:
            return usage
    return Usage(note="no usage found in agent output")


PRESETS: dict[str, AgentPreset] = {
    "claude": AgentPreset(
        name="claude",
        command="claude -p --output-format json --max-budget-usd {budget} --allowedTools Bash,Read,Edit,Write,Grep,Glob",
        parse=parse_claude,
        budget_enforced=True,
    ),
    "codex": AgentPreset(
        name="codex",
        # workspace-write: may edit the working copy and run commands, no network.
        command="codex exec --sandbox workspace-write --skip-git-repo-check --ephemeral --json -",
        parse=parse_codex,
        budget_enforced=False,
    ),
    "gemini": AgentPreset(
        name="gemini",
        command="gemini --approval-mode yolo --output-format json -p {prompt}",
        parse=parse_gemini,
        budget_enforced=False,
    ),
}


def resolve_agent(agent: str, command: str | None = None) -> AgentPreset:
    """A preset by name, optionally with its command replaced; or a fully custom command."""
    if agent in PRESETS:
        preset = PRESETS[agent]
        return AgentPreset(preset.name, command or preset.command, preset.parse, preset.budget_enforced and command is None)
    if agent == "custom":
        if not command:
            raise ValueError("--agent custom needs --agent-command")
        return AgentPreset("custom", command, parse_any, budget_enforced=False)
    raise ValueError(f"unknown agent {agent!r}; choose from {', '.join([*PRESETS, 'custom'])}")


def build_argv(command: str, *, prompt: str, prompt_file: Path, budget: float) -> tuple[list[str], str | None]:
    """Fill in placeholders. Returns (argv, stdin), where stdin is None if the prompt is in argv."""
    parts = shlex.split(command)
    uses_prompt_arg = any("{prompt}" in p or "{prompt_file}" in p for p in parts)
    argv = [
        p.replace("{budget}", f"{budget:.2f}").replace("{prompt_file}", str(prompt_file)).replace("{prompt}", prompt)
        for p in parts
    ]
    return argv, (None if uses_prompt_arg else prompt)


def parse_price(spec: str | None) -> tuple[float, float] | None:
    """'3/15' -> (3.0, 15.0): USD per million input / output tokens."""
    if not spec:
        return None
    try:
        a, b = spec.split("/")
        return float(a), float(b)
    except ValueError:
        raise ValueError(f"--price must look like IN/OUT in USD per million tokens, e.g. 3/15; got {spec!r}") from None


def resolve_cost(usage: Usage, workdir: Path, price: tuple[float, float] | None) -> tuple[float | None, str]:
    """Agent-reported cost, else a wrapper's .crew/cost.json, else tokens x price, else unknown."""
    if usage.cost_usd is not None:
        return usage.cost_usd, "reported by agent"
    cost_file = workdir / ".crew" / "cost.json"
    if cost_file.exists():
        try:
            value = json.loads(cost_file.read_text())["cost_usd"]
            cost_file.unlink()  # one file per session
            return float(value), "from .crew/cost.json"
        except (ValueError, KeyError, TypeError):
            pass
    if price and usage.input_tokens is not None and usage.output_tokens is not None:
        return (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / 1_000_000, "tokens x --price"
    return None, "unknown"
