"""
GWI `science-integrity` lens — deterministic scientific-honesty & security-soundness probes (`GWI-SCI-01..05`).

Motivation (2026-10-31 4-model council): four repositories scored GWI 9.62–9.66 PASS while independent auditors found
P0/P1 defects the craft lenses could not see — hardcoded constants described as "measured" hardware telemetry,
`pytest` collecting 0 tests behind a green gate, quality gates that only check file existence, seeded "peer reviews"
attributed to real model ids with no model call, signature endpoints with no agent<->key binding, and inline
`onclick` handlers built from template interpolation (stored XSS).

Every probe is a stdlib-only static heuristic tuned for low false positives. Each finding carries:
  - `id`        : GWI rule id (`GWI-SCI-01` .. `GWI-SCI-05B`)
  - `severity`  : `P0` | `P1` | `P2`
  - `blocker`   : True  -> lens VETO (caps the Chrysos pole at <= 6.5 and fails `--strict` / `--strict-lenses`)
                  False -> WARN (small score deduction, never vetoes)
  - `evidence`  : `path:line` references and the exact reason

Probes:
  GWI-SCI-01  Measurement provenance: "measured / telemetry / benchmarked" + hardware + numeric unit in README/.tex/docs,
              while no script that mentions that hardware performs acquisition (subprocess / nvidia-smi / powermetrics /
              perf / timers / raw-trace reads) and no raw trace files are committed. Suppressed when the same paragraph
              says simulated / illustrative / assumed / modelled / estimated / datasheet / not measured.
  GWI-SCI-02  Zero tests: a test config (pytest config, tests/ dir, conftest, package.json `test`) or a README test claim
              exists, but the suite has 0 tests. Static count by default; real `pytest --collect-only -q` under
              `--strict-lenses` (or `GWI_SCI_COLLECT=1`). README test-count drift stays with `readme_check.py`.
  GWI-SCI-03  Hollow quality gate: `scripts/quality_gate.sh` (or a Makefile gate/check/verify target) that runs no test
              runner (following `npm run`, `make` and nested `scripts/*.sh` one level deep).
  GWI-SCI-04  Synthetic attribution: seed/fixture/genesis/demo files that attribute authored reviews/content to real
              model ids (`claude-*`, `gpt-*`, `qwen*`, `gemini-*`, ...) without a provenance marker
              (`synthetic_demo`, `provenance: fixture`, `gwi:synthetic-ok`) and without any model call.
  GWI-SCI-05A Inline event handlers (`onclick="..."`) built with template interpolation (`${...}`, `' + x + '`,
              Python f-string `{x}`) in shipped HTML/JS/Python.
  GWI-SCI-05B Signature verification against a caller-supplied public key with no key-registry lookup (warning).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

SCI_LENS_ID = "science-integrity"

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
_TEST_DIRS = {"tests", "test", "__tests__", "e2e", "spec"}
_MAX_FILE_BYTES = 1_500_000

# --------------------------------------------------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------------------------------------------------


def _walk(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".venv"))
        for fn in sorted(filenames):
            if fn.lower().endswith(suffixes):
                p = Path(dirpath) / fn
                try:
                    if p.stat().st_size <= _MAX_FILE_BYTES:
                        out.append(p)
                except OSError:
                    pass
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def _in_test_tree(p: Path, root: Path) -> bool:
    try:
        parts = p.relative_to(root).parts
    except ValueError:
        parts = p.parts
    if any(part in _TEST_DIRS for part in parts[:-1]):
        return True
    return bool(re.search(r"(^test_|_test$|\.test$|\.spec$|^conftest$)", p.stem))


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _finding(rule: str, severity: str, blocker: bool, title: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "id": rule,
        "severity": severity,
        "blocker": blocker,
        "status": "VETO" if blocker else "WARN",
        "title": title,
        "evidence": list(dict.fromkeys(evidence))[:8],
    }


# --------------------------------------------------------------------------------------------------------------------
# GWI-SCI-01 — Measurement provenance
# --------------------------------------------------------------------------------------------------------------------

_CLAIM_VERB_RE = re.compile(
    r"\b(?:measured|measurements?|telemetry|benchmarked|profiled|hardware[- ]timed|wall[- ]clock[- ]timed|"
    r"empirically\s+(?:timed|measured))\b",
    re.IGNORECASE,
)
_HW_TOKEN_RE = re.compile(
    r"\b(?:Axelera|Metis|AIPU|GB10|Grace[\s~]+Blackwell|Blackwell|RTX[\s~]*\d{3,4}|A100|H100|H200|B200|V100|"
    r"Jetson|Orin|Apple[\s~]+M[1-4]|M[1-4][\s~]+(?:Pro|Max|Ultra)|TPU|NPU|GPU|FPGA|Snapdragon|Raspberry[\s~]+Pi|ESP32)\b"
)
_GENERIC_HW = {"tpu", "npu", "gpu", "fpga"}
_NUM_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:\\,|\\;|~|\$|\\text\{|\{|\s)*\s*(?:W|ms|mJ|J|TOPS|FPS|fps|watts?|joules?|GB/s|us|µs)\b"
)
_DISCLAIMER_RE = re.compile(
    r"\b(?:simulat\w*|illustrativ\w*|assum\w*|hypothetical\w*|modell?ed|analytic(?:al)?\s+model|synthetic\w*|"
    r"projected|estimat\w*|nominal|datasheet|data[- ]sheet|spec[- ]sheet|vendor[- ](?:reported|specified)|"
    r"not\s+(?:yet\s+)?measured|placeholder|back[- ]of[- ]the[- ]envelope|parametri[sz]ed\s+model)\b",
    re.IGNORECASE,
)
_ACQUISITION_RE = re.compile(
    r"subprocess\.|os\.popen|os\.system\(|Popen\(|nvidia-smi|powermetrics|tegrastats|\bperf\s+stat\b|pynvml|\bnvml\w*|"
    r"psutil|energy_uj|\brapl\b|axmonitor|axdevice|\bjtop\b|likwid|time\.perf_counter|time\.monotonic|"
    r"time\.time\(\)|time\.process_time|\btimeit\b|torch\.cuda\.Event|torch\.cuda\.synchronize|performance\.now\(|"
    r"Instant::now|std::time|json\.load\(|np\.load\(|np\.loadtxt|np\.genfromtxt|pd\.read_|read_csv|csv\.reader|"
    r"csv\.DictReader|\.read_text\(|\.read_bytes\(|open\(\s*[^,()]+\)|open\([^)]*['\"]r[bt]?['\"]"
)
_TRACE_NAME_RE = re.compile(
    r"(?:trace|telemetry|nvidia[-_]smi|powermetrics|perf[-_]stat|tegrastats|power|energy|raw)", re.I
)
_TRACE_SUFFIXES = (".csv", ".jsonl", ".log", ".tsv", ".nsys-rep", ".qdrep", ".trace", ".perf", ".parquet", ".txt")
_TRACE_DIRS = {"traces", "trace", "raw", "telemetry", "measurements", "logs_raw", "raw_data"}


def _claim_sources(root: Path) -> list[Path]:
    srcs: list[Path] = []
    for name in ("README.md", "PAPER_SPEC.md"):
        if (root / name).is_file():
            srcs.append(root / name)
    for p in sorted(root.glob("*.tex")) + sorted(root.glob("paper/*.tex")) + sorted(root.glob("sections/*.tex")):
        srcs.append(p)
    for p in sorted(root.glob("docs/*.md")) + sorted(root.glob("docs/*/*.md")):
        srcs.append(p)
    return srcs


def _paragraphs(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    pos = 0
    for block in re.split(r"(\n\s*\n)", text):
        if block.strip() and not re.fullmatch(r"\n\s*\n", block):
            out.append((pos, block))
        pos += len(block)
    return out


def _norm_hw(tok: str) -> str:
    return re.sub(r"[\s~]+", "", tok).lower()


def _raw_traces(root: Path) -> list[str]:
    hits: list[str] = []
    for p in _walk(root, _TRACE_SUFFIXES):
        rel_parts = [x.lower() for x in p.relative_to(root).parts]
        if any(part in _TRACE_DIRS for part in rel_parts[:-1]) or _TRACE_NAME_RE.search(p.name):
            if p.suffix.lower() == ".txt" and not any(part in _TRACE_DIRS for part in rel_parts[:-1]):
                continue
            hits.append(_rel(p, root))
    return hits


def check_measurement_provenance(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    passes: list[str] = []
    claims: list[tuple[str, str]] = []  # (evidence, normalized hw token)
    for src in _claim_sources(root):
        text = _read(src)
        for start, para in _paragraphs(text):
            # strip LaTeX comments so commented-out drafts never count
            para_clean = re.sub(r"(?<!\\)%[^\n]*", "", para) if src.suffix == ".tex" else para
            m_verb = _CLAIM_VERB_RE.search(para_clean)
            if not m_verb:
                continue
            hw_hits = _HW_TOKEN_RE.findall(para_clean)
            if not hw_hits or not _NUM_UNIT_RE.search(para_clean):
                continue
            if _DISCLAIMER_RE.search(para_clean):
                continue
            line = _line_of(text, start + max(0, para.find(m_verb.group(0))))
            for tok in dict.fromkeys(hw_hits):
                claims.append((f"{_rel(src, root)}:{line} claims '{m_verb.group(0)}' on {tok.strip()}", _norm_hw(tok)))

    if not claims:
        passes.append("PASS [GWI-SCI-01]: 0 un-hedged 'measured/telemetry' hardware claims in README/.tex/docs")
        return findings, passes

    scripts = [
        p for p in _walk(root, (".py", ".sh", ".js", ".mjs", ".ts", ".rs", ".ipynb")) if not _in_test_tree(p, root)
    ]
    script_text = {p: _read(p) for p in scripts}
    script_norm = {p: re.sub(r"[\s~_-]+", "", t).lower() for p, t in script_text.items()}
    acquiring = {p for p, t in script_text.items() if _ACQUISITION_RE.search(t)}
    traces = _raw_traces(root)

    unsupported: list[str] = []
    by_loc: dict[str, dict[str, list[str]]] = {}
    for ev, tok in claims:
        loc = by_loc.setdefault(ev.split(" on ", 1)[0], {"tokens": [], "scripts": [], "nowhere": []})
        hw_label = ev.split(" on ", 1)[1]
        if tok in _GENERIC_HW:
            related = []
        else:
            related = [p for p, nt in script_norm.items() if tok in nt]
        if related:
            if any(p in acquiring for p in related) or traces:
                continue
            loc["tokens"].append(hw_label)
            loc["scripts"].extend(_rel(p, root) for p in related)
        else:
            if acquiring or traces:
                continue
            loc["nowhere"].append(hw_label)
    for head, info in by_loc.items():
        if info["tokens"]:
            gens = ", ".join(sorted(dict.fromkeys(info["scripts"]))[:3])
            unsupported.append(
                f"{head} on {', '.join(dict.fromkeys(info['tokens']))} -> generator(s) {gens} contain only literal "
                "constants (no acquisition call, no raw trace committed)"
            )
        if info["nowhere"]:
            unsupported.append(
                f"{head} on {', '.join(dict.fromkeys(info['nowhere']))} -> no acquisition code or raw trace anywhere "
                "in the repository"
            )

    if unsupported:
        findings.append(
            _finding(
                "GWI-SCI-01",
                "P0",
                True,
                "Hardware numbers described as measured/telemetry but the generator hardcodes them "
                "(relabel as simulated/illustrative/assumed or commit raw traces + acquisition code)",
                sorted(dict.fromkeys(unsupported)),
            )
        )
    else:
        passes.append(
            f"PASS [GWI-SCI-01]: {len(claims)} measurement claim(s) backed by acquisition code"
            + (f" or {len(traces)} raw trace file(s)" if traces else "")
        )
    return findings, passes


# --------------------------------------------------------------------------------------------------------------------
# GWI-SCI-02 — Zero collected tests
# --------------------------------------------------------------------------------------------------------------------

_PY_TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+test\w*\s*\(", re.MULTILINE)
_JS_TEST_DEF_RE = re.compile(r"(?<![\w.])(?:it|test)(?:\.(?:each\([^)]*\)|only|concurrent))?\s*\(\s*['\"`]")
_RS_TEST_DEF_RE = re.compile(r"#\[(?:tokio::)?test\]")
_README_TEST_CLAIM_RE = re.compile(
    r"\b\d+\s+(?:passed|tests?|unit tests?|test cases)\b|`pytest|\bvitest\b|\bjest\b", re.I
)
_NPM_DEFAULT_TEST = "no test specified"


def _python_test_config(root: Path) -> tuple[bool, bool]:
    """Return (explicit_config, declared_dep)."""
    explicit = (root / "pytest.ini").is_file() or (root / "conftest.py").is_file() or (root / "tests").is_dir()
    declared = False
    pp = root / "pyproject.toml"
    if pp.is_file():
        txt = _read(pp)
        if "[tool.pytest" in txt:
            explicit = True
        if re.search(r"""["']pytest\b""", txt):
            declared = True
    for cfg, marker in (("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        if (root / cfg).is_file() and marker in _read(root / cfg):
            explicit = True
    return explicit, declared


def _package_json(root: Path) -> dict[str, Any]:
    pj = root / "package.json"
    if not pj.is_file():
        return {}
    try:
        data = json.loads(_read(pj))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _static_test_count(root: Path) -> dict[str, int]:
    counts = {"python": 0, "js": 0, "rust": 0}
    for p in _walk(root, (".py",)):
        if re.match(r"(test_.*|.*_test)\.py$", p.name):
            counts["python"] += len(_PY_TEST_DEF_RE.findall(_read(p)))
    for p in _walk(root, (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")):
        if re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", p.name):
            counts["js"] += len(_JS_TEST_DEF_RE.findall(_read(p)))
    for p in _walk(root, (".rs",)):
        counts["rust"] += len(_RS_TEST_DEF_RE.findall(_read(p)))
    return counts


def _pytest_collect(root: Path, timeout: int = 180) -> tuple[int | None, str]:
    """Run real `pytest --collect-only -q`. Returns (count or None if unmeasurable, detail)."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    venv_py = root / ".venv" / "bin" / "python"
    if venv_py.is_file():
        cmd = [str(venv_py), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        via = ".venv"
    elif shutil.which("uv") and (root / "pyproject.toml").is_file():
        # Keep the target tree read-only: build the env in a per-path cache dir, never `<root>/.venv`.
        cache = Path.home() / ".cache" / "gwi-sci" / hashlib.sha256(str(root).encode()).hexdigest()[:16]
        env["UV_PROJECT_ENVIRONMENT"] = str(cache)
        extras: list[str] = []
        if re.search(r"^\s*dev\s*=", _read(root / "pyproject.toml"), re.MULTILINE):
            extras = ["--all-extras"]
        cmd = [
            "uv",
            "run",
            "--quiet",
            *extras,
            "--with",
            "pytest",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        via = "uv"
    else:
        return None, "pytest unavailable (no .venv, no uv)"
    try:
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"collection not run ({type(exc).__name__})"
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    if res.returncode == 5 or re.search(r"\bno tests (?:collected|ran)\b", out):
        return 0, f"`pytest --collect-only -q` via {via}: no tests collected (exit {res.returncode})"
    m = re.search(r"(\d+)\s+tests?\s+collected", out) or re.search(r"collected\s+(\d+)\s+items?", out)
    if m and res.returncode == 0:
        return int(m.group(1)), f"`pytest --collect-only -q` via {via}: {m.group(1)} collected"
    if res.returncode == 0:
        total = 0
        for ln in (res.stdout or "").splitlines():
            m_cnt = re.search(r":\s*(\d+)$", ln.strip())
            if m_cnt:
                total += int(m_cnt.group(1))
            elif "::" in ln:
                total += 1
        return total, f"`pytest --collect-only -q` via {via}: {total} collected"
    tail = " ".join(out.strip().splitlines()[-2:])[:200]
    return None, f"collection errored (exit {res.returncode}): {tail}"


def check_zero_tests(root: Path, profile: str, run_collect: bool) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    passes: list[str] = []
    py_explicit, py_declared = _python_test_config(root)
    pkg = _package_json(root)
    scripts = pkg.get("scripts") or {}
    test_script = str(scripts.get("test", ""))
    js_config = bool(test_script and _NPM_DEFAULT_TEST not in test_script) or any(
        d in {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        for d in ("vitest", "jest", "mocha", "@playwright/test")
    )
    readme = _read(root / "README.md") if (root / "README.md").is_file() else ""
    readme_claims = bool(_README_TEST_CLAIM_RE.search(readme))
    has_cargo = (root / "Cargo.toml").is_file()

    if not (py_explicit or py_declared or js_config or readme_claims or has_cargo):
        return findings, passes

    counts = _static_test_count(root)
    total = sum(counts.values())
    detail = f"static count python={counts['python']}, js={counts['js']}, rust={counts['rust']}"
    collected: int | None = None
    if run_collect and (py_explicit or py_declared):
        collected, cdetail = _pytest_collect(root)
        detail += f"; {cdetail}"
        if collected is not None and counts["js"] == 0 and counts["rust"] == 0:
            total = collected

    if total > 0:
        passes.append(f"PASS [GWI-SCI-02]: test suite non-empty ({detail})")
        return findings, passes

    reasons: list[str] = []
    if py_explicit:
        reasons.append("pytest config / tests/ / conftest.py present")
    elif py_declared:
        reasons.append("pytest declared in pyproject.toml")
    if js_config:
        reasons.append(f"package.json test runner (`{test_script or 'devDependency'}`)")
    if readme_claims:
        reasons.append("README.md claims tests")
    if has_cargo:
        reasons.append("Cargo.toml present (cargo test)")
    strong = py_explicit or js_config or readme_claims or profile in ("app", "sim")
    findings.append(
        _finding(
            "GWI-SCI-02",
            "P0" if strong else "P1",
            strong,
            "Test suite collects 0 tests while the repository configures or claims tests",
            [f"{'; '.join(reasons)} -> {detail}"],
        )
    )
    return findings, passes


# --------------------------------------------------------------------------------------------------------------------
# GWI-SCI-03 — Hollow quality gate
# --------------------------------------------------------------------------------------------------------------------

_TEST_RUNNER_RE = re.compile(
    r"\bpytest\b|-m\s+unittest\b|\bunittest\b|\bcargo\s+(?:test|nextest)\b|\bgo\s+test\b|\bvitest\b|\bjest\b|"
    r"\bmocha\b|\bplaywright\s+test\b|\bnpx\s+playwright\b|\bbun\s+test\b|\bdeno\s+test\b|\bctest\b|\btox\b|\bnox\b|"
    r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b|\bmake\s+test\b|\bgodot\b[^\n]*(?:--headless|-s\s)|\bgut\b|"
    r"smoke_test|run_tests?\b|verify_sim\.py|\bhypothesis\b|\bdotnet\s+test\b|\bmvn\s+test\b|\bgradle\w*\s+test\b|"
    r"Unity[^\n]*-runTests"
)
_NPM_RUN_RE = re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?([A-Za-z0-9:_.-]+)")
_MAKE_CALL_RE = re.compile(r"\bmake\s+(?:-[A-Za-z]+\s+)*([A-Za-z0-9_.-]+)")
_SH_CALL_RE = re.compile(r"(?:bash|sh|\./|\$ROOT_DIR/|\$\{ROOT_DIR\}/)\s*([A-Za-z0-9_./-]+\.sh)\b")
_EXISTENCE_ONLY_RE = re.compile(r"^\s*(?:test\s+-[fesd]|\[\s+-[fesd]|\[\[\s+-[fesd]|ls\s)", re.MULTILINE)


def _strip_sh_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _make_targets(makefile: Path) -> dict[str, str]:
    targets: dict[str, list[str]] = {}
    cur: str | None = None
    for ln in _read(makefile).splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?!=)(.*)$", ln)
        if m and not ln.startswith("\t"):
            cur = m.group(1)
            deps = m.group(2).split("#", 1)[0].split()
            targets.setdefault(cur, []).extend(f"make {d}" for d in deps)
        elif ln.startswith("\t") and cur:
            targets[cur].append(ln.strip())
        elif ln.strip() and not ln.startswith("\t"):
            cur = None
    return {k: "\n".join(v) for k, v in targets.items()}


def _gate_runs_tests(root: Path, text: str, depth: int = 0, seen: set[str] | None = None) -> tuple[bool, bool]:
    """Return (runs_tests, unresolved_indirection)."""
    seen = seen if seen is not None else set()
    body = _strip_sh_comments(text)
    if _TEST_RUNNER_RE.search(body):
        return True, False
    if depth >= 3:
        return False, True
    unresolved = False
    scripts = _package_json(root).get("scripts") or {}
    for name in _NPM_RUN_RE.findall(body):
        key = f"npm:{name}"
        if key in seen:
            continue
        seen.add(key)
        if name in ("install", "ci", "i", "x", "exec", "dlx"):
            continue
        if name in scripts:
            ok, unr = _gate_runs_tests(root, str(scripts[name]), depth + 1, seen)
            if ok:
                return True, False
            unresolved |= unr
        else:
            unresolved = True
    mk = root / "Makefile"
    targets = _make_targets(mk) if mk.is_file() else {}
    for name in _MAKE_CALL_RE.findall(body):
        key = f"make:{name}"
        if key in seen:
            continue
        seen.add(key)
        if name in targets:
            ok, unr = _gate_runs_tests(root, targets[name], depth + 1, seen)
            if ok:
                return True, False
            unresolved |= unr
        else:
            unresolved = True
    for rel in _SH_CALL_RE.findall(body):
        cand = (root / rel.lstrip("./")).resolve()
        key = f"sh:{cand}"
        if key in seen or cand.name == "quality_gate.sh":
            continue
        seen.add(key)
        if cand.is_file():
            ok, unr = _gate_runs_tests(root, _read(cand), depth + 1, seen)
            if ok:
                return True, False
            unresolved |= unr
    return False, unresolved


def check_hollow_gate(root: Path, profile: str) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    passes: list[str] = []
    gate = root / "scripts" / "quality_gate.sh"
    gate_label = ""
    gate_text = ""
    if gate.is_file():
        gate_label, gate_text = "scripts/quality_gate.sh", _read(gate)
    elif (root / "Makefile").is_file():
        targets = _make_targets(root / "Makefile")
        for t in ("gate", "quality-gate", "quality_gate", "quality", "check", "verify", "ci"):
            if t in targets:
                gate_label, gate_text = f"Makefile:{t}", targets[t]
                break
    if not gate_label:
        return findings, passes

    runs, unresolved = _gate_runs_tests(root, gate_text)
    if runs:
        passes.append(f"PASS [GWI-SCI-03]: `{gate_label}` executes a test runner")
        return findings, passes
    if unresolved:
        passes.append(f"PASS [GWI-SCI-03]: `{gate_label}` delegates to unresolved commands (not judged hollow)")
        return findings, passes

    py_explicit, py_declared = _python_test_config(root)
    pkg = _package_json(root)
    declares_tests = (
        py_explicit
        or py_declared
        or bool((pkg.get("scripts") or {}).get("test"))
        or (root / "Cargo.toml").is_file()
        or sum(_static_test_count(root).values()) > 0
    )
    blocker = profile in ("app", "sim") or declares_tests
    exist_checks = len(_EXISTENCE_ONLY_RE.findall(_strip_sh_comments(gate_text)))
    ev = f"`{gate_label}` runs no test runner (pytest/cargo test/vitest/npm test/godot -s ...)"
    if exist_checks:
        ev += f"; {exist_checks} file-existence check(s) (`test -f`) stand in for verification"
    if declares_tests:
        ev += "; the repository declares a test framework the gate never invokes"
    findings.append(
        _finding(
            "GWI-SCI-03",
            "P1" if blocker else "P2",
            blocker,
            "Hollow quality gate: passes without executing any tests",
            [ev],
        )
    )
    return findings, passes


# --------------------------------------------------------------------------------------------------------------------
# GWI-SCI-04 — Synthetic attribution to real model ids
# --------------------------------------------------------------------------------------------------------------------

_MODEL_ID_RE = re.compile(
    r"""["'`]((?:claude|gpt|qwen|gemini|llama|mistral|mixtral|deepseek|grok|o[134])[-_.]?[\w.:-]*\d[\w.:-]*)["'`]""",
    re.IGNORECASE,
)
_PROVENANCE_MARKER_RE = re.compile(
    r"synthetic[_-]demo|SYNTHETIC[ _-]DEMO|gwi:synthetic-ok|"
    r"provenance[\"']?\s*[:=]\s*[\"']?(?:synthetic|fixture|demo|illustrative|hand[-_ ]?written)",
    re.IGNORECASE,
)
_MODEL_CALL_RE = re.compile(
    r"\bopenai\b|\banthropic\b|google\.genai|from\s+google\s+import\s+genai|chat/completions|/v1/messages|"
    r"gx10-query|\boc\s+(?:fable|astra|opus|sonnet|triad)\b|bedrock|litellm|ollama|generate_content\(|"
    r"messages\.create\(|completions\.create\(",
    re.IGNORECASE,
)
_AUTHORED_KEY_RE = re.compile(
    r"\b(?:review|reviews|comment|comments|abstract|body|summary|verdict|critique|rationale)\b", re.I
)
_LONG_PROSE_RE = re.compile(r"""["']([A-Z][^"'\n]{40,})["']""")
_SEED_NAME_RE = re.compile(r"(?:seed|genesis|fixture|demo[_-]?data|sample[_-]?data|mock[_-]?data|corpus)", re.I)


def check_synthetic_attribution(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    passes: list[str] = []
    hits: list[str] = []
    scanned = 0
    for p in _walk(root, (".py", ".js", ".mjs", ".ts", ".json", ".yaml", ".yml", ".sql", ".toml")):
        if _in_test_tree(p, root):
            continue
        rel_parts = p.relative_to(root).parts
        if not (_SEED_NAME_RE.search(p.stem) or any(_SEED_NAME_RE.search(d) for d in rel_parts[:-1])):
            continue
        scanned += 1
        text = _read(p)
        if _PROVENANCE_MARKER_RE.search(text) or _MODEL_CALL_RE.search(text):
            continue
        ids = list(dict.fromkeys(m.group(1) for m in _MODEL_ID_RE.finditer(text)))
        if not ids or not _AUTHORED_KEY_RE.search(text) or len(_LONG_PROSE_RE.findall(text)) < 2:
            continue
        first = _MODEL_ID_RE.search(text)
        line = _line_of(text, first.start()) if first else 1
        hits.append(
            f"{_rel(p, root)}:{line} attributes hardcoded authored content to {', '.join(ids[:4])} "
            "with no model call and no provenance marker (`synthetic_demo` / `provenance: fixture`)"
        )
    if hits:
        findings.append(
            _finding(
                "GWI-SCI-04",
                "P0",
                True,
                "Synthetic content attributed to real model ids (Zero-Synthetic-Data violation)",
                hits,
            )
        )
    else:
        passes.append(f"PASS [GWI-SCI-04]: 0 unmarked model-attributed seed/fixture files ({scanned} scanned)")
    return findings, passes


# --------------------------------------------------------------------------------------------------------------------
# GWI-SCI-05 — Web: inline handler interpolation (A) & caller-supplied signature keys (B)
# --------------------------------------------------------------------------------------------------------------------

_INLINE_HANDLER_JS_RE = re.compile(r"""\bon[a-z]{3,}\s*=\s*(["'])(?:(?!\1)[^\n])*?(?:\$\{|['"]\s*\+\s*[A-Za-z_$])""")
_INLINE_HANDLER_PY_RE = re.compile(r"""\bon[a-z]{3,}\s*=\s*\\?["'][^"'\n]*?\{[A-Za-z_][\w.\[\]'"()]*\}""")
_PY_FSTRING_HINT_RE = re.compile(r"""\bf["']|\bf\"\"\"|\.format\(""")
_SIG_CALLER_KEY_RE = re.compile(
    r"""(?:verify\w*|VerifyKey|verify_key|nacl\.sign\.detached\.verify|ed25519\.verify)\s*\([^)\n]*?"""
    r"""(?:data|body|payload|req(?:uest)?\.(?:json|body|form|args)|request\.get_json\(\)|params|args)\s*"""
    r"""(?:\[\s*["']|\.get\(\s*["']|\.)(?:pubkey|public_key|publicKey|pub_key|verify_key|verifyKey)""",
)
_KEY_REGISTRY_RE = re.compile(
    r"key_registry|KEY_REGISTRY|agent_keys|registered_key|trusted_keys|known_keys|pubkey_registry|\btofu\b|"
    r"trust[_-]on[_-]first[_-]use|SELECT\s+\w*pub\w*\s+FROM\s+(?:agents|keys|agent_keys|registry|identities)\b",
    re.IGNORECASE,
)


def check_web_security(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    passes: list[str] = []
    xss_hits: list[str] = []
    sig_hits: list[str] = []
    files = [
        p
        for p in _walk(root, (".html", ".htm", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".vue", ".svelte", ".py"))
        if not _in_test_tree(p, root) and not p.name.endswith((".min.js", ".bundle.js"))
    ]
    for p in files:
        text = _read(p)
        if p.suffix == ".py":
            for ln_no, ln in enumerate(text.splitlines(), 1):
                if _INLINE_HANDLER_PY_RE.search(ln) and (_PY_FSTRING_HINT_RE.search(ln) or "{{" not in ln):
                    # only lines inside an f-string / .format() template (look back 40 lines for the opener)
                    ctx = "\n".join(text.splitlines()[max(0, ln_no - 40) : ln_no])
                    if _PY_FSTRING_HINT_RE.search(ctx):
                        xss_hits.append(f"{_rel(p, root)}:{ln_no}: {ln.strip()[:120]}")
        else:
            lines = text.splitlines()
            for m in _INLINE_HANDLER_JS_RE.finditer(text):
                ln_no = _line_of(text, m.start())
                snippet = lines[ln_no - 1].strip() if ln_no - 1 < len(lines) else m.group(0)
                xss_hits.append(f"{_rel(p, root)}:{ln_no}: {snippet[:140]}")
        if p.suffix in (".py", ".js", ".mjs", ".ts"):
            sig_m = _SIG_CALLER_KEY_RE.search(text)
            if sig_m and not _KEY_REGISTRY_RE.search(text):
                sig_hits.append(
                    f"{_rel(p, root)}:{_line_of(text, sig_m.start())}: verifies against caller-supplied public key "
                    "with no agent<->key registry lookup (anyone can sign as any agent_id)"
                )
    if xss_hits:
        findings.append(
            _finding(
                "GWI-SCI-05A",
                "P0",
                True,
                "Inline event handler built with template interpolation (stored/reflected XSS sink); "
                "use addEventListener + data-* attributes",
                xss_hits,
            )
        )
    else:
        passes.append(f"PASS [GWI-SCI-05A]: 0 interpolated inline event handlers across {len(files)} shipped file(s)")
    if sig_hits:
        findings.append(
            _finding(
                "GWI-SCI-05B",
                "P1",
                False,
                "Signature check trusts the caller's public key (no identity binding / key registry)",
                sig_hits,
            )
        )
    else:
        passes.append("PASS [GWI-SCI-05B]: no signature verification against caller-supplied keys without a registry")
    return findings, passes


# --------------------------------------------------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------------------------------------------------


def audit_science_integrity(target: Path, profile: str = "app", run_collect: bool = False) -> dict[str, Any]:
    """Run all `GWI-SCI-*` probes on a repository directory (or the parent directory of a file target)."""
    resolved = target.expanduser().resolve()
    root = resolved if resolved.is_dir() else resolved.parent
    run_collect = run_collect or os.environ.get("GWI_SCI_COLLECT", "") == "1"
    findings: list[dict[str, Any]] = []
    passes: list[str] = []
    for fn in (
        lambda: check_measurement_provenance(root),
        lambda: check_zero_tests(root, profile, run_collect),
        lambda: check_hollow_gate(root, profile),
        lambda: check_synthetic_attribution(root),
        lambda: check_web_security(root),
    ):
        f, ps = fn()
        findings.extend(f)
        passes.extend(ps)
    return {
        "lens": SCI_LENS_ID,
        "target": str(root),
        "profile": profile,
        "collection_run": run_collect,
        "findings": findings,
        "passes": passes,
        "blockers": [f for f in findings if f["blocker"]],
        "warnings": [f for f in findings if not f["blocker"]],
        "veto": any(f["blocker"] for f in findings),
    }
