"""Semantic bug seeding for Python repositories.

Contamination control (architecture doc, section 2): famous bug datasets are
likely in model training data, so the benchmark injects fresh bugs instead.

Design choices:

* Mutations are applied as *text* edits at AST node positions, never by
  re-generating the file with ``ast.unparse``. Only the mutated span changes, so
  formatting does not reveal which file was touched.
* By default only *surviving* mutants are kept: the repository's own test suite
  still passes with the bug in place. A bug the existing tests already catch is
  not a "previously unknown" bug, and finding it would only measure whether the
  system ran the tests.
* At most one seeded bug per function, so cases are diverse.
"""

from __future__ import annotations

import ast
import os
import random
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .model import BenchmarkCase, SeededBug, Subsystem

IGNORED_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache", "build", "dist", ".tox"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
FIXED_MTIME = 1_700_000_000  # normalized mtime for every snapshot file, so edits do not show up in `ls -lt`


@dataclass
class Mutation:
    file: str  # repository-relative POSIX path
    operator: str
    kind: str
    start: int  # absolute character offsets into the file text
    end: int
    replacement: str
    line_start: int
    line_end: int
    symbol: str | None
    original: str

    def apply(self, text: str) -> str:
        assert text[self.start : self.end] == self.original, "source changed since mutation was planned"
        return text[: self.start] + self.replacement + text[self.end :]

    @property
    def description(self) -> str:
        return f"{self.operator}: `{self.original}` -> `{self.replacement}`"


_CMP_FLIP: dict[type, tuple[str, str]] = {
    # op type: (regex for the operator token, replacement token)
    ast.Lt: (r"<", "<="),
    ast.LtE: (r"<=", "<"),
    ast.Gt: (r">", ">="),
    ast.GtE: (r">=", ">"),
    ast.Eq: (r"==", "!="),
    ast.NotEq: (r"!=", "=="),
    ast.In: (r"\bin\b", "not in"),
    ast.NotIn: (r"\bnot\s+in\b", "in"),
    ast.Is: (r"\bis\b", "is not"),
    ast.IsNot: (r"\bis\s+not\b", "is"),
}
_BOOL_FLIP: dict[type, tuple[str, str]] = {ast.And: (r"\band\b", "or"), ast.Or: (r"\bor\b", "and")}
_ARITH_FLIP: dict[type, tuple[str, str]] = {ast.Add: (r"\+", "-"), ast.Sub: (r"-", "+")}


class _MutationFinder(ast.NodeVisitor):
    def __init__(self, rel_path: str, text: str, tree: ast.AST):
        self.rel = rel_path
        self.text = text
        self.lines = text.splitlines(keepends=True)
        self.line_offsets = [0]
        for line in self.lines:
            self.line_offsets.append(self.line_offsets[-1] + len(line))
        self.parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        self.scope: list[str] = []
        self.function_depth = 0
        self.found: list[Mutation] = []

    # -- positions -------------------------------------------------------------------------
    def _offset(self, lineno: int, col_bytes: int) -> int:
        # ast column offsets are UTF-8 byte offsets; convert to a character offset.
        line = self.lines[lineno - 1]
        col_chars = len(line.encode("utf-8")[:col_bytes].decode("utf-8", errors="ignore"))
        return self.line_offsets[lineno - 1] + col_chars

    def _start(self, node: ast.AST) -> int:
        return self._offset(node.lineno, node.col_offset)

    def _end(self, node: ast.AST) -> int:
        return self._offset(node.end_lineno, node.end_col_offset)

    def _add(self, node: ast.AST, operator: str, kind: str, start: int, end: int, replacement: str) -> None:
        if self.function_depth == 0:
            return  # only seed inside functions: realistic, and testable
        original = self.text[start:end]
        if original == replacement:
            return
        self.found.append(
            Mutation(
                file=self.rel,
                operator=operator,
                kind=kind,
                start=start,
                end=end,
                replacement=replacement,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                symbol=".".join(self.scope) or None,
                original=original,
            )
        )

    def _gap_token(self, node: ast.AST, left: ast.AST, right: ast.AST, table: dict, op: ast.AST, operator: str, kind: str) -> None:
        entry = table.get(type(op))
        if entry is None:
            return
        pattern, replacement = entry
        a, b = self._end(left), self._start(right)
        gap = self.text[a:b]
        if "#" in gap:
            return
        m = re.search(pattern, gap)
        if m:
            self._add(node, operator, kind, a + m.start(), a + m.end(), replacement)

    # -- scopes ------------------------------------------------------------------------------
    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.function_depth += 1
        # Skip decorators, defaults and annotations; only seed in the body.
        for stmt in node.body:
            self.visit(stmt)
        self.function_depth -= 1
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    # -- operators -----------------------------------------------------------------------
    def visit_If(self, node: ast.If) -> None:
        if _is_main_guard(node):
            return
        test_src = self.text[self._start(node.test) : self._end(node.test)]
        if "\n" not in test_src:
            self._add(node.test, "negate_condition", "logic", self._start(node.test), self._end(node.test), f"not ({test_src})")
        if len(node.body) == 1 and isinstance(node.body[0], ast.Raise):
            raise_node = node.body[0]
            self._add(raise_node, "drop_guard", "missing_error_handling", self._start(raise_node), self._end(raise_node), "pass")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if len(node.ops) == 1:
            self._gap_token(node, node.left, node.comparators[0], _CMP_FLIP, node.ops[0], "cmp_flip", "logic")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if len(node.values) == 2:
            self._gap_token(node, node.values[0], node.values[1], _BOOL_FLIP, node.op, "bool_flip", "logic")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if not _is_string_like(node.left) and not _is_string_like(node.right):
            self._gap_token(node, node.left, node.right, _ARITH_FLIP, node.op, "arith_flip", "logic")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if type(node.value) is not int:
            return
        parent = self.parents.get(node)
        boundary = isinstance(parent, (ast.Compare, ast.BinOp, ast.Subscript, ast.Slice)) or (
            isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id == "range"
        )
        if boundary:
            self._add(node, "off_by_one", "logic", self._start(node), self._end(node), str(node.value + 1))


def _is_main_guard(node: ast.If) -> bool:
    t = node.test
    return (
        isinstance(t, ast.Compare)
        and isinstance(t.left, ast.Name)
        and t.left.id == "__name__"
        and len(t.comparators) == 1
        and isinstance(t.comparators[0], ast.Constant)
        and t.comparators[0].value == "__main__"
    )


def _is_string_like(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)))


def find_mutations(rel_path: str, text: str) -> list[Mutation]:
    """All candidate mutations in one Python source file."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    finder = _MutationFinder(rel_path, text, tree)
    finder.visit(tree)
    return finder.found


# -- repository helpers ------------------------------------------------------------------


def is_test_file(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    name = parts[-1]
    return (
        any(p in {"tests", "test", "testing"} for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def source_files(repo: Path) -> list[str]:
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
        for f in sorted(files):
            if f.endswith(".py"):
                rel = Path(root, f).relative_to(repo).as_posix()
                if not is_test_file(rel):
                    out.append(rel)
    return out


def default_subsystems(files: list[str]) -> list[Subsystem]:
    """One subsystem per top-level directory holding source; root-level files form "root".

    A stand-in until the partitioning pipeline (architecture doc, section 5) exists.
    """
    groups: dict[str, list[str]] = {}
    for rel in files:
        parts = Path(rel).parts
        key = parts[0] if len(parts) > 1 else "root"
        groups.setdefault(key, []).append(rel)
    subs = []
    for key in sorted(groups):
        paths = sorted(groups[key]) if key == "root" else [f"{key}/**"]
        subs.append(Subsystem(id=f"sub-{key}", paths=paths))
    return subs


def subsystem_for(file: str, subsystems: list[Subsystem]) -> Subsystem | None:
    from fnmatch import fnmatch

    for sub in subsystems:
        for pattern in sub.paths:
            if fnmatch(file, pattern) or (pattern.endswith("/**") and file.startswith(pattern[:-2])):
                return sub
    return None


def copy_tree(src: Path, dst: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in IGNORED_DIRS or Path(n).suffix in IGNORED_SUFFIXES}

    # shutil.copy, not copy2: do not carry over source metadata.
    shutil.copytree(src, dst, ignore=ignore, copy_function=shutil.copy)


def normalize_mtimes(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        os.utime(path, (FIXED_MTIME, FIXED_MTIME), follow_symlinks=False)


def run_tests(repo: Path, test_command: str, timeout: float) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        return subprocess.run(
            shlex.split(test_command), cwd=repo, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        # A mutant that hangs the suite counts as caught.
        return subprocess.CompletedProcess(exc.cmd, returncode=124, stdout="", stderr="timeout")


# -- suite generation ----------------------------------------------------------------------


@dataclass
class SeedReport:
    cases: list[BenchmarkCase]
    candidates: int
    tried: int
    caught_by_existing_tests: int


def seed_suite(
    repo: Path,
    out_dir: Path,
    *,
    test_command: str,
    count: int = 10,
    clean_cases: int = 1,
    seed: int = 0,
    survivors_only: bool = True,
    subsystems: list[Subsystem] | None = None,
    timeout: float = 300.0,
    max_attempts: int | None = None,
) -> SeedReport:
    """Write a benchmark suite of ``count`` single-bug cases plus ``clean_cases`` controls."""
    repo = repo.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty")
    files = source_files(repo)
    if not files:
        raise ValueError(f"no non-test Python files under {repo}")
    subsystems = subsystems or default_subsystems(files)

    with tempfile.TemporaryDirectory(prefix="crew-seed-") as tmp:
        base = Path(tmp) / "base"
        copy_tree(repo, base)
        result = run_tests(base, test_command, timeout)
        if result.returncode != 0:
            raise RuntimeError(
                "the test suite must pass on the unmodified repository before seeding.\n"
                f"command: {test_command}\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
            )

    candidates = [m for rel in files for m in find_mutations(rel, (repo / rel).read_text(encoding="utf-8"))]
    rng = random.Random(seed)
    rng.shuffle(candidates)
    max_attempts = max_attempts or max(count * 25, 50)

    cases: list[BenchmarkCase] = []
    used_symbols: set[tuple[str, str | None]] = set()
    tried = caught = 0
    out_dir.mkdir(parents=True, exist_ok=True)

    for mutation in candidates:
        if len(cases) >= count or tried >= max_attempts:
            break
        key = (mutation.file, mutation.symbol)
        if key in used_symbols:
            continue
        tried += 1
        with tempfile.TemporaryDirectory(prefix="crew-mutant-") as tmp:
            work = Path(tmp) / "repo"
            copy_tree(repo, work)
            target = work / mutation.file
            mutated = mutation.apply(target.read_text(encoding="utf-8"))
            try:
                ast.parse(mutated)
            except SyntaxError:
                continue
            target.write_text(mutated, encoding="utf-8")
            if survivors_only and run_tests(work, test_command, timeout).returncode != 0:
                caught += 1
                continue
            used_symbols.add(key)
            case_id = f"case-{len(cases) + 1:03d}"
            bug = SeededBug(
                id=f"{case_id}-bug",
                file=mutation.file,
                line_start=mutation.line_start,
                line_end=mutation.line_end,
                kind=mutation.kind,
                mutation=mutation.operator,
                description=mutation.description,
                symbol=mutation.symbol,
                original=mutation.original,
                mutated=mutation.replacement,
            )
            buggy_sub = subsystem_for(mutation.file, subsystems)
            case = BenchmarkCase(
                id=case_id,
                test_command=test_command,
                source=_source_info(repo),
                subsystems=[Subsystem(s.id, list(s.paths), clean=s is not buggy_sub) for s in subsystems],
                bugs=[bug],
            )
            _write_case(case, work, out_dir / case_id)
            cases.append(case)

    for i in range(clean_cases):
        case_id = f"clean-{i + 1:03d}"
        case = BenchmarkCase(
            id=case_id,
            test_command=test_command,
            source=_source_info(repo),
            subsystems=[Subsystem(s.id, list(s.paths), clean=True) for s in subsystems],
            bugs=[],
        )
        with tempfile.TemporaryDirectory(prefix="crew-clean-") as tmp:
            work = Path(tmp) / "repo"
            copy_tree(repo, work)
            _write_case(case, work, out_dir / case_id)
        cases.append(case)

    return SeedReport(cases=cases, candidates=len(candidates), tried=tried, caught_by_existing_tests=caught)


def _write_case(case: BenchmarkCase, tree: Path, case_dir: Path) -> None:
    case.save(case_dir)
    copy_tree(tree, case_dir / "snapshot")
    normalize_mtimes(case_dir / "snapshot")


def _source_info(repo: Path) -> dict[str, str]:
    info = {"repo": repo.name}
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
        info["commit"] = sha
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return info
