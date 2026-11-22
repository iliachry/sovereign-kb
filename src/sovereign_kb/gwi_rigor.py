"""Core test-rigor and formal-proof probes for the science-integrity lens (`GWI-SCI-06`, `GWI-SCI-07`).

Moved out of the experimental `frontier_organon` module so that core gate code (`gwi_science`)
never imports experimental engines (`af.barbell_isolation`). `frontier_organon` re-exports
these names for backward compatibility.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_antifragility import PROPERTY_TEST_RE as _PROPERTY_TEST_RE

_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "out",
    "coverage",
    "target",
    ".godot",
    "Library",
    "Temp",
    "Obj",
    "Logs",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "test-results",
    "site-packages",
    ".kb",
    ".swm",
}

_MAX_SOURCE_BYTES = 1_500_000


def _iter_source_files(
    root: Path,
    suffixes: tuple[str, ...] = (".py", ".ts", ".tsx", ".js", ".rs", ".cs"),
    max_bytes: int | None = _MAX_SOURCE_BYTES,
) -> list[Path]:
    files: list[Path] = []
    if not root.is_dir():
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".venv"))
        for fn in sorted(filenames):
            if fn.endswith((".d.ts", ".min.js", ".bundle.js")):
                continue
            if fn.lower().endswith(suffixes):
                p = Path(dirpath) / fn
                try:
                    if max_bytes is None or p.stat().st_size <= max_bytes:
                        files.append(p)
                except OSError:
                    pass
    return files


# ====================================================================================================================
# 2. Automated AST Mutation Kill-Ratio Engine & Property-Based Fuzzing Gate (`GWI-SCI-06`)
# ====================================================================================================================


_JS_ASSERT_RE = re.compile(r"\b(?:expect|assert)\s*\(")
_RS_ASSERT_RE = re.compile(r"\b(?:assert!|assert_eq!|assert_ne!|debug_assert!|kani::assert)\s*\(")


def _strip_line_comments(src: str, suffix: str) -> str:
    if suffix == ".py":
        return re.sub(r"#[^\n]*", "", src)
    return re.sub(r"//[^\n]*", "", src)


def check_property_and_mutation_rigor(root: Path) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """
    GWI-SCI-06 — Evaluates test assertion density (flagging assertion-free smoke tests) and
    tracks property-based fuzzing harnesses (`Hypothesis`, `proptest`, `fast-check`, `Kani`).
    """
    findings: list[dict[str, Any]] = []
    passes: list[str] = []
    assertion_free_tests: list[str] = []
    total_test_fns = 0
    total_assertions = 0
    property_test_files: list[str] = []

    for fpath in _iter_source_files(root, (".py", ".ts", ".tsx", ".js", ".rs")):
        rel = str(fpath.relative_to(root))
        src = fpath.read_text(encoding="utf-8", errors="ignore")
        uncommented = _strip_line_comments(src, fpath.suffix.lower())

        is_py_test = fpath.suffix == ".py" and (fpath.name.startswith("test_") or fpath.name.endswith("_test.py"))
        is_js_test = any(fpath.name.endswith(s) for s in (".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx", ".test.js"))
        is_rs_test = fpath.suffix == ".rs" and bool(re.search(r"#\[(?:tokio::|kani::)?(?:test|proof)\]", uncommented))

        if (is_py_test or is_js_test or is_rs_test) and _PROPERTY_TEST_RE.search(uncommented):
            property_test_files.append(rel)

        if is_py_test:
            try:
                tree = ast.parse(src, filename=rel)
            except SyntaxError:
                continue
            for item in ast.walk(tree):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                    total_test_fns += 1
                    n_asserts = sum(
                        1
                        for sub in ast.walk(item)
                        if isinstance(sub, ast.Assert)
                        or (
                            isinstance(sub, ast.Call)
                            and (
                                (isinstance(sub.func, ast.Attribute) and sub.func.attr.startswith(("assert", "raises")))
                                or (isinstance(sub.func, ast.Name) and sub.func.id.startswith("assert"))
                            )
                        )
                    )
                    total_assertions += n_asserts
                    if n_asserts == 0:
                        assertion_free_tests.append(f"{rel}:{item.lineno} `{item.name}` has 0 assertions")
        elif is_js_test:
            js_tests = list(re.finditer(r"\b(?:it|test)\s*\(", uncommented))
            js_asserts = _JS_ASSERT_RE.findall(uncommented)
            total_test_fns += len(js_tests)
            total_assertions += len(js_asserts)
            for idx, m in enumerate(js_tests):
                end_pos = js_tests[idx + 1].start() if idx + 1 < len(js_tests) else len(uncommented)
                block = uncommented[m.start() : end_pos]
                if not _JS_ASSERT_RE.search(block):
                    ln = uncommented.count("\n", 0, m.start()) + 1
                    assertion_free_tests.append(f"{rel}:{ln} JS/TS test block has 0 expect/assert calls")
        elif is_rs_test:
            rs_matches = list(
                re.finditer(
                    r"#\[(?:tokio::)?test\]\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)",
                    uncommented,
                )
            )
            rs_asserts = _RS_ASSERT_RE.findall(uncommented)
            total_test_fns += len(rs_matches)
            total_assertions += len(rs_asserts)
            for idx, m in enumerate(rs_matches):
                end_pos = rs_matches[idx + 1].start() if idx + 1 < len(rs_matches) else len(uncommented)
                block = uncommented[m.start() : end_pos]
                if not _RS_ASSERT_RE.search(block) and "#[should_panic" not in block:
                    ln = uncommented.count("\n", 0, m.start()) + 1
                    assertion_free_tests.append(f"{rel}:{ln} Rust `#[test] fn {m.group(1)}` has 0 assertions")

    avg_assertions = round(total_assertions / max(1, total_test_fns), 2) if total_test_fns > 0 else 0.0
    metrics = {
        "total_test_functions": total_test_fns,
        "total_assertions": total_assertions,
        "assertions_per_test": avg_assertions,
        "assertion_free_test_count": len(assertion_free_tests),
        "property_fuzz_files": property_test_files,
        "has_property_fuzzing": len(property_test_files) > 0,
    }

    if total_test_fns > 0 and (
        total_assertions == 0
        or len(assertion_free_tests) == total_test_fns
        or len(assertion_free_tests) > max(2, int(0.25 * total_test_fns))
    ):
        findings.append(
            {
                "id": "GWI-SCI-06",
                "severity": "P1",
                "blocker": False,
                "status": "WARN",
                "title": "High ratio of assertion-free smoke tests (>25% or 100% of test functions have 0 assertions)",
                "evidence": assertion_free_tests[:8],
            }
        )
    elif total_test_fns > 0:
        passes.append(
            f"PASS [GWI-SCI-06]: {total_assertions} assertions across {total_test_fns} test(s) "
            f"({avg_assertions} assertions/test, {len(property_test_files)} property-fuzz file(s))"
        )
    return findings, passes, metrics


class _SingleOperatorMutator(ast.NodeTransformer):
    """Mutates exactly the `target_idx`-th binary/comparison operator in a Python AST."""

    def __init__(self, target_idx: int) -> None:
        self.target_idx = target_idx
        self.current_idx = 0
        self.mutated_description: str | None = None

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        swap_map: dict[type[ast.operator], tuple[ast.operator, str]] = {
            ast.Add: (ast.Sub(), "+ -> -"),
            ast.Sub: (ast.Add(), "- -> +"),
            ast.Mult: (ast.Div(), "* -> /"),
        }
        op_type = type(node.op)
        if op_type in swap_map:
            if self.current_idx == self.target_idx:
                new_op, desc = swap_map[op_type]
                self.mutated_description = f"line {getattr(node, 'lineno', 1)}: BinOp ({desc})"
                self.current_idx += 1
                return ast.copy_location(ast.BinOp(left=node.left, op=new_op, right=node.right), node)
            self.current_idx += 1
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if len(node.ops) == 1:
            swap_cmp: dict[type[ast.cmpop], tuple[ast.cmpop, str]] = {
                ast.GtE: (ast.Lt(), ">= -> <"),
                ast.Gt: (ast.LtE(), "> -> <="),
                ast.LtE: (ast.Gt(), "<= -> >"),
                ast.Lt: (ast.GtE(), "< -> >="),
                ast.Eq: (ast.NotEq(), "== -> !="),
                ast.NotEq: (ast.Eq(), "!= -> =="),
            }
            op_type = type(node.ops[0])
            if op_type in swap_cmp:
                if self.current_idx == self.target_idx:
                    new_op, desc = swap_cmp[op_type]
                    self.mutated_description = f"line {getattr(node, 'lineno', 1)}: Compare ({desc})"
                    self.current_idx += 1
                    return ast.copy_location(
                        ast.Compare(left=node.left, ops=[new_op], comparators=node.comparators),
                        node,
                    )
                self.current_idx += 1
        return node


def _has_pytest(python_bin: str) -> bool:
    try:
        res = subprocess.run(
            [python_bin, "-c", "import pytest"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def select_test_python(root_dir: Path) -> str | None:
    """Interpreter for running a target's tests: its own `.venv`, else the first of the running
    interpreter and `python3` on PATH that can import pytest. Never a pytest-less interpreter, whose
    exit code 1 would be misread as a failing baseline."""
    venv_py = root_dir / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    for cand in (sys.executable, shutil.which("python3")):
        if cand and _has_pytest(cand):
            return cand
    return None


def run_ast_mutation_probe(
    root: Path,
    target_rel_path: str | None = None,
    max_mutants: int = 6,
    pytest_args: list[str] | None = None,
    timeout_per_mutant: int = 20,
) -> dict[str, Any]:
    """
    Executes real AST Mutation Testing in an isolated temporary directory copy (never mutating `root`
    or external symlinks) after verifying a clean baseline test run (`returncode == 0`), measuring
    the empirical Mutation Kill Ratio M_kill = N_killed / N_viable in [0, 1].
    """
    root_dir = Path(root).expanduser().resolve()
    candidates: list[Path] = []
    if target_rel_path:
        raw_p = Path(target_rel_path)
        if raw_p.is_absolute() or ".." in raw_p.parts:
            raise ValueError(f"target_rel_path must be a safe relative path inside root: {target_rel_path}")
        cand_path = root_dir / raw_p
        if cand_path.exists() and cand_path.is_file():
            candidates = [cand_path]
    else:
        for p in _iter_source_files(root_dir, (".py",)):
            rel = str(p.relative_to(root_dir))
            if not rel.startswith("tests") and not p.name.startswith("test_") and p.name != "__init__.py":
                candidates.append(p)

    if not candidates:
        return {
            "target_root": str(root_dir),
            "mutants_tested": 0,
            "mutants_killed": 0,
            "mutants_survived": 0,
            "mutation_kill_ratio": 0.0,
            "status": "SKIPPED_NO_PYTHON_SOURCES",
            "passed": False,
            "details": [],
        }

    chosen_file = candidates[0]
    rel_chosen = str(chosen_file.relative_to(root_dir))
    source = chosen_file.read_text(encoding="utf-8", errors="ignore")
    try:
        base_tree = ast.parse(source, filename=rel_chosen)
        base_unparsed = ast.unparse(base_tree)
    except SyntaxError:
        return {
            "target_root": str(root_dir),
            "target_file": rel_chosen,
            "mutants_tested": 0,
            "mutants_killed": 0,
            "mutants_survived": 0,
            "mutation_kill_ratio": 0.0,
            "status": "SYNTAX_ERROR",
            "passed": False,
            "details": [],
        }

    # Generate up to max_mutants distinct mutated sources compared against ast.unparse(base_tree)
    mutants: list[tuple[int, str, str]] = []
    seen_mutated_code: set[str] = {base_unparsed}
    for idx in range(max(1, max_mutants) * 3):
        if len(mutants) >= max_mutants:
            break
        tree_copy = ast.parse(source, filename=rel_chosen)
        mutator = _SingleOperatorMutator(idx)
        mutated_tree = mutator.visit(tree_copy)
        ast.fix_missing_locations(mutated_tree)
        if mutator.mutated_description is None:
            break
        mutated_code = ast.unparse(mutated_tree)
        if mutated_code not in seen_mutated_code:
            seen_mutated_code.add(mutated_code)
            mutants.append((idx, mutator.mutated_description, mutated_code))

    if not mutants:
        return {
            "target_root": str(root_dir),
            "target_file": rel_chosen,
            "mutants_tested": 0,
            "mutants_killed": 0,
            "mutants_survived": 0,
            "mutation_kill_ratio": 1.0,
            "status": "NO_MUTABLE_OPERATORS",
            "passed": True,
            "details": [],
        }

    python_bin = select_test_python(root_dir)
    if python_bin is None:
        return {
            "target_root": str(root_dir),
            "target_file": rel_chosen,
            "mutants_tested": 0,
            "mutants_killed": 0,
            "mutants_survived": 0,
            "mutation_kill_ratio": 0.0,
            "status": "PYTEST_UNAVAILABLE",
            "passed": False,
            "details": [],
        }

    killed = 0
    survived = 0
    details: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="organon_mut_") as tmp_str:
        tmp_root = Path(tmp_str) / "repo"
        shutil.copytree(
            root_dir,
            tmp_root,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".kb"),
        )
        mut_file_path = tmp_root / rel_chosen
        # Never write through a symlink to an external file
        if mut_file_path.is_symlink():
            mut_file_path.unlink()
            mut_file_path.write_text(source, encoding="utf-8")
        # A symlinked PARENT directory (copied with symlinks=True) would let the mutant write through
        # into an external file outside the sandbox: refuse instead of mutating.
        tmp_root_resolved = tmp_root.resolve()
        try:
            mut_file_path.resolve().relative_to(tmp_root_resolved)
        except ValueError:
            return {
                "target_root": str(root_dir),
                "target_file": rel_chosen,
                "mutants_tested": 0,
                "mutants_killed": 0,
                "mutants_survived": 0,
                "mutation_kill_ratio": 0.0,
                "status": "UNSAFE_SYMLINK_TARGET",
                "passed": False,
                "details": [],
            }

        env = dict(os.environ)
        src_dir = tmp_root / "src"
        env["PYTHONPATH"] = f"{src_dir}:{tmp_root}" if src_dir.is_dir() else str(tmp_root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cmd = [python_bin, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"] + (pytest_args or [])

        # Step 0: Verify unmutated baseline test suite passes (returncode == 0)
        try:
            baseline_res = subprocess.run(
                cmd,
                cwd=str(tmp_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_per_mutant,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "target_root": str(root_dir),
                "target_file": rel_chosen,
                "mutants_tested": 0,
                "mutants_killed": 0,
                "mutants_survived": 0,
                "mutation_kill_ratio": 0.0,
                "status": "BASELINE_TIMEOUT",
                "passed": False,
                "details": [],
            }

        if baseline_res.returncode != 0:
            status_code = "NO_TESTS_COLLECTED" if baseline_res.returncode == 5 else "BASELINE_FAILED"
            return {
                "target_root": str(root_dir),
                "target_file": rel_chosen,
                "mutants_tested": 0,
                "mutants_killed": 0,
                "mutants_survived": 0,
                "mutation_kill_ratio": 0.0,
                "status": status_code,
                "baseline_returncode": baseline_res.returncode,
                "passed": False,
                "details": [],
            }

        try:
            for m_idx, desc, m_code in mutants:
                mut_file_path.write_text(m_code, encoding="utf-8")
                outcome = "survived"
                try:
                    res = subprocess.run(
                        cmd,
                        cwd=str(tmp_root),
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=timeout_per_mutant,
                        check=False,
                    )
                    if res.returncode == 1:
                        is_killed = True
                        outcome = "killed"
                    elif res.returncode == 2:
                        # Collection/import error introduced by the mutant (baseline collected and passed
                        # with rc == 0 above), so the mutant broke the module: killed.
                        is_killed = True
                        outcome = "collection_error_killed"
                    elif res.returncode == 0:
                        is_killed = False
                        outcome = "survived"
                    else:
                        is_killed = False
                        outcome = f"infra_error_rc_{res.returncode}"
                except subprocess.TimeoutExpired:
                    is_killed = True
                    outcome = "timeout_killed"
                if is_killed:
                    killed += 1
                else:
                    survived += 1
                details.append({"mutant_index": m_idx, "mutation": desc, "killed": is_killed, "outcome": outcome})
        finally:
            mut_file_path.write_text(source, encoding="utf-8")

    total = killed + survived
    kill_ratio = round(killed / max(1, total), 4)
    passed = total > 0 and kill_ratio >= 0.80
    return {
        "target_root": str(root_dir),
        "target_file": rel_chosen,
        "mutants_tested": total,
        "mutants_killed": killed,
        "mutants_survived": survived,
        "mutation_kill_ratio": kill_ratio,
        "status": "PASS" if passed else "WARN",
        "passed": passed,
        "details": details,
    }


# ====================================================================================================================
# 3. Machine-Checked Formal Proof & Symbolic Theorem Verifier (`GWI-SCI-07`)
# ====================================================================================================================

_LEAN_THEOREM_RE = re.compile(r"^\s*(?:theorem|lemma)\s+([A-Za-z0-9_']+)", re.MULTILINE)
_LEAN_SORRY_RE = re.compile(r"\b(?:sorry|admit)\b")
_TLA_THEOREM_RE = re.compile(r"^\s*(?:THEOREM|INVARIANT)\b", re.MULTILINE)
_KANI_PROOF_RE = re.compile(r"^\s*#\[kani::proof\]", re.MULTILINE)
_TEX_THEOREM_RE = re.compile(r"\\begin\{(?:theorem|lemma|proposition|corollary)\}")


def _strip_lean_comments_and_strings(text: str) -> str:
    """Strips Lean 4 block comments `/- ... -/` (preserving line count) and `-- ...` line comments."""

    def _blank_newlines(m: re.Match[str]) -> str:
        return "\n" * m.group(0).count("\n")

    no_block = re.sub(r"/-.*?-/", _blank_newlines, text, flags=re.DOTALL)
    no_str = re.sub(r'"(?:\\.|[^"\\])*"', '""', no_block)
    return re.sub(r"--[^\n]*", "", no_str)


def _is_symbolic_verification_script(py_path: Path, src: str) -> bool:
    """Checks via Python AST whether `py_path` genuinely imports `sympy` or `z3`."""
    try:
        tree = ast.parse(src, filename=str(py_path))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name in ("sympy", "z3") or alias.name.startswith(("sympy.", "z3.")) for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in ("sympy", "z3") or mod.startswith(("sympy.", "z3.")):
                return True
    return False


def check_formal_proofs(root: Path, execute_symbolic: bool = False) -> dict[str, Any]:
    """
    GWI-SCI-07 — Audits formal proofs (Lean 4 `.lean`, TLA+ `.tla`, Rust `#[kani::proof]`)
    and executable symbolic proof scripts (`SymPy` / `Z3`) against `.tex` theorems.
    Flags unproved `sorry` / `admit` placeholders in Lean 4 files and failed symbolic proof executions.
    """
    root_dir = Path(root).expanduser().resolve()
    lean_files = _iter_source_files(root_dir, (".lean",))
    tla_files = _iter_source_files(root_dir, (".tla",))
    rs_files = _iter_source_files(root_dir, (".rs",))
    py_files = _iter_source_files(root_dir, (".py",))
    tex_files = [
        p
        for p in list(root_dir.glob("*.tex"))
        + list(root_dir.glob("paper/*.tex"))
        + list(root_dir.glob("sections/*.tex"))
        if p.is_file()
    ]

    lean_theorems: list[str] = []
    lean_sorries: list[str] = []
    for lf in lean_files:
        rel = str(lf.relative_to(root_dir))
        txt = lf.read_text(encoding="utf-8", errors="ignore")
        clean = _strip_lean_comments_and_strings(txt)
        for m in _LEAN_THEOREM_RE.finditer(clean):
            lean_theorems.append(f"{rel}:{clean.count(chr(10), 0, m.start()) + 1} `{m.group(1)}`")
        for m in _LEAN_SORRY_RE.finditer(clean):
            lean_sorries.append(f"{rel}:{clean.count(chr(10), 0, m.start()) + 1} contains unproved `{m.group(0)}`")

    tla_specs: list[str] = []
    for tf in tla_files:
        rel = str(tf.relative_to(root_dir))
        txt = tf.read_text(encoding="utf-8", errors="ignore")
        if _TLA_THEOREM_RE.search(txt):
            tla_specs.append(rel)

    kani_harnesses: list[str] = []
    for rf in rs_files:
        rel = str(rf.relative_to(root_dir))
        txt = _strip_line_comments(rf.read_text(encoding="utf-8", errors="ignore"), ".rs")
        for m in _KANI_PROOF_RE.finditer(txt):
            kani_harnesses.append(f"{rel}:{txt.count(chr(10), 0, m.start()) + 1}")

    symbolic_scripts: list[Path] = []
    for pf in py_files:
        txt = pf.read_text(encoding="utf-8", errors="ignore")
        if _is_symbolic_verification_script(pf, txt):
            symbolic_scripts.append(pf)

    tex_theorem_count = 0
    for xf in tex_files:
        txt = xf.read_text(encoding="utf-8", errors="ignore")
        clean_tex = re.sub(r"(?<!\\)%[^\n]*", "", txt)
        tex_theorem_count += len(_TEX_THEOREM_RE.findall(clean_tex))

    executed_results: list[dict[str, Any]] = []
    failed_symbolic: list[str] = []
    if execute_symbolic and symbolic_scripts:
        import sys

        venv_py = root_dir / ".venv" / "bin" / "python"
        py_bin = str(venv_py) if venv_py.is_file() else (sys.executable or shutil.which("python3") or "python3")
        for sp_path in symbolic_scripts[:3]:
            rel = str(sp_path.relative_to(root_dir))
            if "test_" in sp_path.name or "verify" in sp_path.name or "proof" in sp_path.name:
                src_txt = sp_path.read_text(encoding="utf-8", errors="ignore")
                has_pytest_fns = bool(re.search(r"^\s*def\s+test_", src_txt, re.MULTILINE))
                cmd = (
                    [py_bin, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(sp_path)]
                    if has_pytest_fns
                    else [py_bin, str(sp_path)]
                )
                try:
                    res = subprocess.run(
                        cmd,
                        cwd=str(root_dir),
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    ok = res.returncode == 0
                    executed_results.append({"script": rel, "returncode": res.returncode, "passed": ok})
                    if not ok:
                        failed_symbolic.append(f"{rel} exited with code {res.returncode}")
                except subprocess.TimeoutExpired:
                    executed_results.append({"script": rel, "returncode": -1, "passed": False})
                    failed_symbolic.append(f"{rel} timed out after 30s")

    findings: list[dict[str, Any]] = []
    passes: list[str] = []

    if lean_sorries:
        findings.append(
            {
                "id": "GWI-SCI-07",
                "severity": "P0",
                "blocker": True,
                "status": "VETO",
                "title": "Lean 4 formal proof contains unproved `sorry` / `admit` placeholders",
                "evidence": lean_sorries[:8],
            }
        )
    if failed_symbolic:
        findings.append(
            {
                "id": "GWI-SCI-07",
                "severity": "P0",
                "blocker": True,
                "status": "VETO",
                "title": "Symbolic verification script failed execution (`SymPy` / `Z3`)",
                "evidence": failed_symbolic[:8],
            }
        )

    overall_passed = len(lean_sorries) == 0 and len(failed_symbolic) == 0
    if overall_passed and (lean_theorems or tla_specs or kani_harnesses or symbolic_scripts):
        passes.append(
            f"PASS [GWI-SCI-07]: Formal/symbolic verification artifacts audited "
            f"(lean_theorems={len(lean_theorems)}, tla_specs={len(tla_specs)}, "
            f"kani_proofs={len(kani_harnesses)}, sympy_z3_scripts={len(symbolic_scripts)}, "
            f"tex_theorems={tex_theorem_count})"
        )

    verification_mode = "executed_symbolic" if executed_results else "static_no_sorry_audit"
    cert_payload = {
        "target": str(root_dir),
        "verification_mode": verification_mode,
        "lean_theorems": lean_theorems,
        "lean_sorries": lean_sorries,
        "tla_specs": tla_specs,
        "kani_harnesses": kani_harnesses,
        "symbolic_scripts": [str(p.relative_to(root_dir)) for p in symbolic_scripts],
        "tex_theorem_count": tex_theorem_count,
        "executed_symbolic": executed_results,
        "findings": findings,
        "passes": passes,
        "passed": overall_passed,
    }
    cert_payload["certificate_sha256"] = hashlib.sha256(
        json.dumps(cert_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return cert_payload
