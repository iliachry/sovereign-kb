"""Antifragility probes for the ``principal-verification`` lens (``af.*``).

Antifragile means the system gets *stronger* from stress. Robustness (fail-closed gates,
rollback, non-regression checks) means it merely *survives* stress; none of these probes
credit robustness. Every value is measured from git history or files on disk; nothing is
estimated or weighted.

  af.failure_to_gate   Share of ``fix`` commits that left a regression test behind: in the same
                       commit, or later via a ``Regression-test-for: <sha>`` commit trailer.
  af.rule_retirement   Rule IDs (e.g. ``GWI-SIM-U01``) added vs. retired since a baseline
                       commit; monotone accretion is flagged (via negativa).
  af.stress_evidence   Property/fuzz tests or mutation-testing results present on disk
                       (static detection: the probe does not execute them).
  af.barbell_isolation Core modules declared in ``[tool.gwi.barbell]`` must not import
                       modules declared experimental (Python imports only).

Probes that cannot apply (no git history, too few fix commits, no rule IDs, no barbell
declaration) emit an ``INFO`` finding, which is neither measured nor unmeasured coverage.
"""

from __future__ import annotations

import ast
import datetime
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

FIX_WINDOW_DAYS = 365
FIX_MAX_COMMITS = 400
FIX_MIN_SAMPLE = 3
FIX_TEST_RATIO_TARGET = 0.5
REGRESSION_TRAILER = "Regression-test-for"
RULE_WINDOW_DAYS = 180
RULE_MIN_IDS = 5
RULE_ACCRETION_MIN_ADDED = 5
PENALTY_FAILURE_TO_GATE = 0.6
PENALTY_RULE_ACCRETION = 0.4
PENALTY_NO_STRESS_EVIDENCE = 0.5
PENALTY_BARBELL_LEAK = 0.6

_FIX_SUBJECT_RE = re.compile(r"^\s*(?:fix|bugfix|hotfix)(?:\([^)]*\))?!?:|^\s*fix(?:es|ed)?\b", re.IGNORECASE)
_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec|e2e)/|(?:^|/)test_[^/]+$|_test\.[a-z]+$|\.(?:test|spec)\.[a-z]+$",
    re.IGNORECASE,
)
_RULE_ID_GREP = "[A-Z]{2,6}-[A-Z]{2,6}-[A-Z]?[0-9]{2,3}"
_RULE_ID_RE = re.compile(r"(?<![\w-])[A-Z]{2,6}-[A-Z]{2,6}-[A-Z]?\d{2,3}(?![\w-])")
_MUTATION_PATHS = (".mutmut-cache", "mutants", "mutants.out", "reports/mutation")
_MUTATION_CONFIG_RE = re.compile(r"^\s*\[(?:tool\.mutmut|mutmut)\]", re.MULTILINE)
_STRYKER_GLOB = "stryker.conf.*"
# Shared with GWI-SCI-06 (`gwi_rigor.check_property_and_mutation_rigor`); lives here so core
# gate code never imports the experimental `frontier_organon` module for it.
PROPERTY_TEST_RE = re.compile(
    r"^\s*@given\s*\(|^\s*from\s+hypothesis\s+import|^\s*import\s+hypothesis|\bproptest!\s*\{|"
    r"\bfc\.assert\s*\(\s*fc\.(?:asyncProperty|property)\b|^\s*#\[kani::proof\]",
    re.MULTILINE,
)


def _git(root: Path, *args: str, timeout: float = 20.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return proc.returncode, proc.stdout


def _has_git_history(root: Path) -> bool:
    code, out = _git(root, "rev-parse", "--verify", "--quiet", "HEAD")
    return code == 0 and bool(out.strip())


def is_test_path(rel_path: str) -> bool:
    return bool(_TEST_PATH_RE.search(rel_path.replace("\\", "/")))


def measure_failure_to_gate(root: Path, window_days: int = FIX_WINDOW_DAYS) -> dict[str, Any]:
    """Count fix commits in the window and how many of them left a regression test behind.

    A fix is covered when it touches a test file itself, or when a later commit that touches a
    test file names it in a ``Regression-test-for: <sha>`` trailer (backfilled regression test).
    """
    code, out = _git(
        root,
        "log",
        "--no-merges",
        f"-n{FIX_MAX_COMMITS}",
        f"--since={window_days}.days",
        f"--format=%x01%H%x02%s%x02%(trailers:key={REGRESSION_TRAILER},valueonly,separator=%x20)",
        "--name-only",
    )
    if code != 0:
        return {"available": False}
    fixes: list[dict[str, Any]] = []
    backfilled: set[str] = set()
    for block in out.split("\x01")[1:]:
        header, _, files_blob = block.partition("\n")
        sha, _, rest = header.partition("\x02")
        subject, _, trailer_vals = rest.partition("\x02")
        files = [ln.strip() for ln in files_blob.splitlines() if ln.strip()]
        touches_test = any(is_test_path(f) for f in files)
        if touches_test:
            backfilled.update(v.lower() for v in trailer_vals.split() if len(v) >= 7)
        if _FIX_SUBJECT_RE.search(subject):
            fixes.append({"sha": sha, "subject": subject, "touches_test": touches_test})
    for f in fixes:
        f["backfilled"] = not f["touches_test"] and any(f["sha"].lower().startswith(b) for b in backfilled)
    same_commit = sum(1 for f in fixes if f["touches_test"])
    later = sum(1 for f in fixes if f["backfilled"])
    covered = same_commit + later
    return {
        "available": True,
        "window_days": window_days,
        "fix_commits": len(fixes),
        "fix_commits_with_test": covered,
        "covered_same_commit": same_commit,
        "covered_by_backfill": later,
        "ratio": round(covered / len(fixes), 4) if fixes else None,
        "uncovered": [
            f"{f['sha'][:7]} {f['subject'][:72]}" for f in fixes if not (f["touches_test"] or f["backfilled"])
        ][:5],
    }


def _rule_ids_at(root: Path, rev: str) -> set[str] | None:
    """Rule IDs in tracked files (outside ``tests/``) at ``rev``, matched on whole tokens only."""
    code, out = _git(root, "grep", "-I", "-h", "-E", _RULE_ID_GREP, rev, "--", ".", ":(exclude)tests")
    if code not in (0, 1):  # 1 == no match
        return None
    return set(_RULE_ID_RE.findall(out))


def _rule_ids_touched_since(root: Path, base: str) -> set[str] | None:
    """Rule IDs on any added line in commits after ``base`` (outside ``tests/``)."""
    code, out = _git(
        root,
        "log",
        "--format=",
        "-p",
        "-U0",
        "--no-ext-diff",
        f"{base}..HEAD",
        "-G",
        _RULE_ID_GREP,
        "--",
        ".",
        ":(exclude)tests",
        timeout=60.0,
    )
    if code != 0:
        return None
    added_lines = "\n".join(ln[1:] for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    return set(_RULE_ID_RE.findall(added_lines))


def measure_rule_retirement(root: Path, window_days: int = RULE_WINDOW_DAYS) -> dict[str, Any]:
    """Compare rule IDs at HEAD with every rule ID that existed at the baseline or was added since.

    ``retired`` counts IDs that existed at the baseline commit, or were introduced after it, and are
    gone from HEAD; so a rule added and later removed inside the window still counts as retired.
    """
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=window_days)).isoformat()
    _, base = _git(root, "rev-list", "-1", f"--before={cutoff}", "HEAD")
    base = base.strip()
    baseline_kind = "window"
    if not base:
        _, roots = _git(root, "rev-list", "--max-parents=0", "HEAD")
        base = roots.split()[-1] if roots.split() else ""
        baseline_kind = "root_commit"
    if not base:
        return {"available": False}
    head_ids = _rule_ids_at(root, "HEAD")
    base_ids = _rule_ids_at(root, base)
    touched = _rule_ids_touched_since(root, base)
    if head_ids is None or base_ids is None or touched is None:
        return {"available": False}
    added = sorted(head_ids - base_ids)
    retired = sorted((base_ids | touched) - head_ids)
    return {
        "available": True,
        "baseline": base[:10],
        "baseline_kind": baseline_kind,
        "window_days": window_days,
        "rule_ids_now": len(head_ids),
        "added": len(added),
        "retired": len(retired),
        "added_sample": added[:5],
        "retired_sample": retired[:5],
    }


def measure_stress_evidence(root: Path) -> dict[str, Any]:
    """Find property/fuzz tests and mutation-testing config or results (static; nothing is run)."""
    from sovereign_kb.gwi import _iter_source_files

    property_files: list[str] = []
    test_files = 0
    for fpath in _iter_source_files(root, (".py", ".ts", ".tsx", ".js", ".rs")):
        rel = str(fpath.relative_to(root))
        is_rs = fpath.suffix == ".rs"
        if not is_test_path(rel) and not is_rs:
            continue
        src = fpath.read_text(encoding="utf-8", errors="ignore")
        if is_rs and "#[test]" not in src and "proptest!" not in src and "kani::proof" not in src:
            continue
        test_files += 1
        if PROPERTY_TEST_RE.search(src):
            property_files.append(rel)
    if (root / "fuzz" / "Cargo.toml").is_file():
        property_files.append("fuzz/Cargo.toml (cargo-fuzz)")

    mutation: list[str] = [p for p in _MUTATION_PATHS if (root / p).exists()]
    mutation += [p.name for p in root.glob(_STRYKER_GLOB)]
    for cfg in ("pyproject.toml", "setup.cfg"):
        cp = root / cfg
        if cp.is_file() and _MUTATION_CONFIG_RE.search(cp.read_text(encoding="utf-8", errors="ignore")):
            mutation.append(f"{cfg} mutmut config")
    return {
        "test_files": test_files,
        "property_fuzz_files": property_files,
        "mutation_evidence": mutation,
        "executed": False,
    }


_TOML_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$", re.MULTILINE)
_TOML_STR_RE = re.compile(r"""["']([^"']+)["']""")


def _parse_barbell_minimal(text: str) -> dict[str, Any] | None:
    """Python 3.10 fallback: read string arrays from the ``[tool.gwi.barbell]`` section only."""
    sections = list(_TOML_SECTION_RE.finditer(text))
    for i, sec in enumerate(sections):
        if sec.group(1).strip() != "tool.gwi.barbell":
            continue
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        body = re.sub(r"#[^\n]*", "", text[sec.end() : end])
        out: dict[str, Any] = {}
        for key in ("core", "experimental"):
            m = re.search(rf"^\s*{key}\s*=\s*\[(.*?)\]", body, re.MULTILINE | re.DOTALL)
            if m:
                out[key] = _TOML_STR_RE.findall(m.group(1))
        return out
    return None


def _load_barbell(root: Path) -> dict[str, list[str]] | None:
    pp = root / "pyproject.toml"
    if not pp.is_file():
        return None
    try:
        text = pp.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # Python 3.10
        bb = _parse_barbell_minimal(text)
    else:
        try:
            bb = tomllib.loads(text).get("tool", {}).get("gwi", {}).get("barbell")
        except tomllib.TOMLDecodeError:
            return None
    if not isinstance(bb, dict):
        return None
    core = [str(m) for m in bb.get("core", []) if str(m).strip()]
    exp = [str(m) for m in bb.get("experimental", []) if str(m).strip()]
    return {"core": core, "experimental": exp} if core and exp else None


def _find_module_file(root: Path, name: str) -> Path | None:
    rel = name.replace(".", "/")
    for base in (root / "src", root):
        for cand in (base / f"{rel}.py", base / rel / "__init__.py"):
            if cand.is_file():
                return cand
        hits = sorted(base.glob(f"*/{rel}.py")) + sorted(base.glob(f"*/{rel}/__init__.py"))
        if hits:
            return hits[0]
    return None


def _module_source_files(path: Path) -> list[Path]:
    """Files that make up a declared module: the file itself, or every module of a package (`__init__.py`)."""
    if path.name == "__init__.py":
        return sorted(path.parent.rglob("*.py"))
    return [path]


def _imported_modules(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
    except SyntaxError:
        return set()
    mods: set[str] = set()
    for node in ast.walk(tree):  # includes lazy imports inside functions
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods.update(f"{node.module}.{a.name}" for a in node.names)
    return mods


def _matches_module(imported: str, declared: str) -> bool:
    return imported == declared or imported.endswith(f".{declared}") or f".{declared}." in f".{imported}."


def measure_barbell_isolation(root: Path) -> dict[str, Any]:
    """Report every import edge from a declared core module into a declared experimental module."""
    bb = _load_barbell(root)
    if bb is None:
        return {"declared": False}
    leaks: list[str] = []
    missing: list[str] = []
    for core in bb["core"]:
        path = _find_module_file(root, core)
        if path is None:
            missing.append(core)
            continue
        imported: set[str] = set()
        for src in _module_source_files(path):
            imported |= _imported_modules(src)
        for imp in sorted(imported):
            for exp in bb["experimental"]:
                if _matches_module(imp, exp) and f"{core} -> {exp}" not in leaks:
                    leaks.append(f"{core} -> {exp}")
    return {
        "declared": True,
        "core": bb["core"],
        "experimental": bb["experimental"],
        "leaks": leaks,
        "missing_core_modules": missing,
    }


def apply_antifragility_probes(ctx: SimpleNamespace) -> None:
    """Append ``af.*`` findings/measurements to the principal-verification lens state."""
    root: Path = ctx.root
    findings: list[str] = ctx.probe_findings
    measurements: list[dict[str, Any]] = ctx.probe_measurements
    score: float = ctx.score

    def _record(pid: str, status: str, text: str, value: Any, target: str) -> None:
        findings.append(f"{status} [{pid}]: {text}")
        if status != "INFO":
            measurements.append({"id": pid, "value": value, "target": target, "status": status})

    if not root.is_dir():
        return
    has_git = _has_git_history(root)

    # af.failure_to_gate
    tgt = f">= {int(FIX_TEST_RATIO_TARGET * 100)}% of fix commits leave a regression test"
    ftg: dict[str, Any] = measure_failure_to_gate(root) if has_git else {"available": False}
    if not ftg.get("available"):
        _record("af.failure_to_gate", "INFO", "No git history; failure-to-gate rate not applicable", None, tgt)
    elif ftg["fix_commits"] < FIX_MIN_SAMPLE:
        _record(
            "af.failure_to_gate",
            "INFO",
            f"Only {ftg['fix_commits']} fix commit(s) in {ftg['window_days']} days; sample too small to score",
            ftg,
            tgt,
        )
    elif ftg["ratio"] >= FIX_TEST_RATIO_TARGET:
        _record(
            "af.failure_to_gate",
            "PASS",
            f"{ftg['fix_commits_with_test']}/{ftg['fix_commits']} fix commits ({ftg['ratio']:.0%}) left a "
            f"regression test ({ftg['covered_same_commit']} same commit, {ftg['covered_by_backfill']} backfilled)",
            ftg,
            tgt,
        )
    else:
        score -= PENALTY_FAILURE_TO_GATE
        _record(
            "af.failure_to_gate",
            "WARN",
            f"Only {ftg['fix_commits_with_test']}/{ftg['fix_commits']} fix commits ({ftg['ratio']:.0%}) touched a "
            f"test; failures are being patched without leaving a regression check (e.g. {', '.join(ftg['uncovered'][:2])})",
            ftg,
            tgt,
        )

    # af.rule_retirement
    tgt = f"rules retired > 0 when >= {RULE_ACCRETION_MIN_ADDED} added in the window"
    rr = measure_rule_retirement(root) if has_git else {"available": False}
    if not rr.get("available") or rr["rule_ids_now"] < RULE_MIN_IDS:
        _record(
            "af.rule_retirement",
            "INFO",
            "No rule-ID catalogue found (fewer than 5 IDs like `GWI-SIM-U01`); retirement not applicable",
            rr,
            tgt,
        )
    elif rr["added"] >= RULE_ACCRETION_MIN_ADDED and rr["retired"] == 0:
        score -= PENALTY_RULE_ACCRETION
        _record(
            "af.rule_retirement",
            "WARN",
            f"Monotone rule accretion: +{rr['added']} rule IDs, 0 retired since {rr['baseline']} "
            f"({rr['baseline_kind']}); {rr['rule_ids_now']} rules now. Retire or merge rules (via negativa)",
            rr,
            tgt,
        )
    else:
        _record(
            "af.rule_retirement",
            "PASS",
            f"+{rr['added']} / -{rr['retired']} rule IDs since {rr['baseline']} ({rr['rule_ids_now']} now)",
            rr,
            tgt,
        )

    # af.stress_evidence
    tgt = ">= 1 property/fuzz test file or mutation-testing config/results"
    se = measure_stress_evidence(root)
    if se["property_fuzz_files"] or se["mutation_evidence"]:
        parts = []
        if se["property_fuzz_files"]:
            parts.append(f"{len(se['property_fuzz_files'])} property/fuzz test file(s)")
        if se["mutation_evidence"]:
            parts.append(f"mutation testing: {', '.join(se['mutation_evidence'])}")
        _record("af.stress_evidence", "PASS", f"{'; '.join(parts)} (detected on disk, not executed)", se, tgt)
    elif se["test_files"] == 0:
        _record("af.stress_evidence", "INFO", "No test files found; stress evidence not applicable", se, tgt)
    else:
        score -= PENALTY_NO_STRESS_EVIDENCE
        _record(
            "af.stress_evidence",
            "WARN",
            f"{se['test_files']} test file(s) but 0 property/fuzz tests and no mutation testing; "
            "the suite only checks inputs someone thought of",
            se,
            tgt,
        )

    # af.barbell_isolation
    tgt = "0 imports from declared core modules into declared experimental modules"
    bi = measure_barbell_isolation(root)
    if not bi["declared"]:
        _record(
            "af.barbell_isolation",
            "INFO",
            "No `[tool.gwi.barbell]` core/experimental declaration in pyproject.toml; isolation not checked",
            bi,
            tgt,
        )
    elif bi["leaks"]:
        score -= PENALTY_BARBELL_LEAK
        _record(
            "af.barbell_isolation",
            "WARN",
            f"{len(bi['leaks'])} core -> experimental import(s): {', '.join(bi['leaks'][:3])}; "
            "a broken experiment can take the core down",
            bi,
            tgt,
        )
    elif len(bi["missing_core_modules"]) == len(bi["core"]):
        _record(
            "af.barbell_isolation",
            "INFO",
            f"None of the {len(bi['core'])} declared core module(s) were found on disk; isolation not checked",
            bi,
            tgt,
        )
    else:
        note = (
            f" ({len(bi['missing_core_modules'])} declared core module(s) not found)"
            if bi["missing_core_modules"]
            else ""
        )
        _record(
            "af.barbell_isolation",
            "PASS",
            f"{len(bi['core'])} core module(s) import none of {len(bi['experimental'])} experimental module(s){note}",
            bi,
            tgt,
        )

    ctx.score = score
