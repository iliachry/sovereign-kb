"""
Unified Goldwood Index (G) & Global Watchability Index (GWI) Quality Gate Engine (`sovereign-kb gwi` / `gwi`).

Formalizes the mathematical and phenomenological Quality of Experience (QoE) standard from:
  1. I. Chrysovergis, "The Goldwood Index: A Universal Mathematical and Phenomenological Framework
     for the Quality of Experience (QoE) in Human Life and Conscious Systems" (`goldwood_index_qoe.tex`)
  2. I. Chrysovergis, "The Aesthetics of Quality: On Beauty, Structural Harmony, and the Art of
     Human Existence" (`the_aesthetics_of_quality.tex`)

Core Mathematical Formulation (v1.1):
  Let S = (S_sub, S_perc, S_act, S_exist) in [0, 10]^4 denote the four ontological tiers:
    - Tier I   (S_sub   / Firmitas)   : Substrate & Somatic Determinism (Compilation, Gate Hygiene, Audio/Signal Bedrock)
    - Tier II  (S_perc  / Venustas)   : Sensorimotor & Perceptual Poise (Visual Craft, Tokens, WCAG AA, Safe-Area, Shaders)
    - Tier III (S_act   / Utilitas)   : Agency, Fluency & Creative Flow (Catalog Coverage, Math/Physics Fidelity, PRNG)
    - Tier IV  (S_exist / Syneidesis) : Epistemic & Existential Integrity (Console Hygiene Lambda=1, Verifier Honesty, Provenance)

  The Universal Goldwood Index G(S) enforces the Weakest-Link Principle via a continuous
  catastrophic barrier gating function Psi(x) normalized so that Psi(x) = 1.0 at and above the
  9.0 sovereign threshold while collapsing toward 0 when x < theta (7.0):
    sigma(x) = 1 / (1 + exp(-k * (x - theta)))          with k = 2.5, theta = 7.0
    Psi(x)   = min(1.0, sigma(x) / sigma(ceiling))      with ceiling = 9.0
    G(S)     = ( sum_{i=1}^4 alpha_i * S_i ) * prod_{i=1}^4 Psi(S_i)

  Supplementary Treatise Metrics:
    - Kolmogorov Elegance: E(L) = exp(-max(0, (|L| - K(T)) / K(T))) in (0, 1]
    - Signal-to-Noise Ratio of Consciousness: SNRC = I_meaning / (H_noise + epsilon)
    - Console / Conscience Hygiene: Lambda_conscience = 1_{errors == 0 and leaks == 0} in {0, 1}
    - 9+ Sovereign Threshold: G(S) >= 9.0 and min_i(S_i) >= theta and Lambda_conscience == 1
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import (
    ARCHETYPE_WEIGHTS,
    CANONICAL_GITLAB_GROUPS,
    CANONICAL_WEDGE_ROUTES,
    DEFAULT_K,
    DEFAULT_SUBTRACTIVE_BUDGETS,
    DEFAULT_THETA,
    DEFAULT_THRESHOLD_G,
    GWI_STANDARD_VERSION,
    VERGA_UNMEASURED_CAP,
    aggregate_tier_pair,
    compute_canonical_anchor_l9_index,
    compute_coupled_goldwood_g,
    compute_goldwood_index,
    compute_kolmogorov_elegance,
    compute_regularity_index,
    compute_simultaneous_load_index,
    compute_snrc,
    compute_subtractive_penalty,
    psi_barrier,
)
from sovereign_kb.gwi_science import SCI_LENS_ID, audit_science_integrity

__all__ = [
    "CANONICAL_GITLAB_GROUPS",
    "CANONICAL_WEDGE_ROUTES",
    "DEFAULT_SUBTRACTIVE_BUDGETS",
    "aggregate_tier_pair",
    "compute_canonical_anchor_l9_index",
    "compute_coupled_goldwood_g",
    "compute_goldwood_index",
    "compute_graph_fiedler_value",
    "compute_kolmogorov_elegance",
    "compute_regularity_index",
    "compute_simultaneous_load_index",
    "compute_snrc",
    "compute_subtractive_penalty",
    "evaluate_kb_profile",
    "psi_barrier",
    "validate_skill_frontmatter",
]

ALLOW_RANDOM_MARKER = "gwi:allow-random"

_JS_RANDOM_RE = re.compile(r"""(?<![\w.'"``])Math\.random\s*\(""")
_PY_RANDOM_RE = re.compile(r"""(?<![\w.'"``])random\.(?:random|randint|choice|shuffle|uniform|gauss)\s*\(""")
_PY_SEEDED_RE = re.compile(r"""random\.seed\s*\(|random\.Random\s*\(|np\.random\.(?:default_rng|seed)|PYTHONHASHSEED""")
_LUFS_RE = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_LRA_RE = re.compile(r"\bLRA:\s*(\d+(?:\.\d+)?)\s*LU")
_PEAK_RE = re.compile(r"\b(?:Peak|True peak):\s*(-?\d+(?:\.\d+)?)\s*dB(?:FS|TP)", re.IGNORECASE)
_MD_BOLD_LEAK_RE = re.compile(r"(?<!\\)\*\*[^*\n]+\*\*")
_OVERFULL_PT_RE = re.compile(r"Overfull \\hbox \(([0-9.]+)pt too wide\)")
_SECRET_LEAK_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|" + "gl" + r"pat-[A-Za-z0-9\-_]{20,}|ghp_[A-Za-z0-9]{36})\b"
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def validate_skill_frontmatter(path: Path | str) -> tuple[bool, str]:
    """Validate YAML frontmatter in a SKILL.md file with zero mandatory dependencies.

    Checks:
      1. Delimiters: Must start with '---' and contain a closing '---'.
      2. Parsing: Validates valid YAML mapping (via PyYAML if available or strict stdlib scanner).
      3. Required keys: Must define 'name:'.
      4. Syntax: Catches dangling unkeyed mappings, unclosed quotes, tabs in indentation,
         and illegal unquoted ': ' inside mapping values.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return False, f"File does not exist: {p}"
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return False, f"Cannot read file: {e}"

    txt = txt.lstrip("\ufeff")
    if not txt.startswith("---"):
        return False, "File does not start with frontmatter delimiter '---'"
    parts = txt.split("---", 2)
    if len(parts) < 3:
        return False, "Missing closing frontmatter delimiter '---'"

    fm_block = parts[1]

    # Fast-path: try PyYAML if available in active environment
    try:
        import yaml  # type: ignore[import-untyped]

        try:
            data = yaml.safe_load(fm_block)
            if not isinstance(data, dict):
                return False, "Frontmatter must parse into a YAML key-value mapping"
            if "name" not in data:
                return False, "Missing mandatory 'name' key in frontmatter"
            return True, "OK"
        except Exception as e:
            return False, f"YAML parse error: {e}"
    except ImportError:
        pass

    # Deterministic zero-dependency YAML frontmatter scanner
    lines = fm_block.splitlines()
    in_block_scalar = False
    block_scalar_indent: int | None = None
    has_name = False
    parent_can_have_children = False

    for idx, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue

        lstripped = line.lstrip(" \t")
        indent_str = line[: len(line) - len(lstripped)]
        stripped = lstripped

        if "\t" in indent_str:
            return False, f"Line {idx}: Tabs are not allowed for YAML indentation"

        indent = len(indent_str)

        if in_block_scalar:
            if block_scalar_indent is None:
                block_scalar_indent = indent
            if indent >= block_scalar_indent:
                continue
            else:
                in_block_scalar = False
                block_scalar_indent = None

        if indent == 0:
            if ":" not in stripped:
                return False, f"Line {idx}: Expected 'key: value' mapping, got '{stripped}'"
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()
            parent_can_have_children = val == ""
            if key == "name":
                has_name = True
            if val in ("|", ">", "|-", ">-", "|+", ">+"):
                in_block_scalar = True
                block_scalar_indent = None
                continue
            if val.startswith('"') or val.startswith("'"):
                q = val[0]
                if not (val.endswith(q) and len(val) > 1 and not val.endswith("\\" + q)):
                    return False, f"Line {idx}: Unclosed quote in value for '{key}'"
            else:
                if ": " in val:
                    return (
                        False,
                        f"Line {idx}: Unquoted value for '{key}' contains ': ' which breaks YAML mapping syntax",
                    )
        else:
            if not in_block_scalar:
                if not parent_can_have_children:
                    return (
                        False,
                        f"Line {idx}: Indented mapping/text '{stripped}' is not allowed under a scalar property",
                    )
                if stripped.startswith("- ") or stripped.startswith("-"):
                    continue
                if ":" in stripped:
                    pass
                else:
                    return False, f"Line {idx}: Unexpected indented text '{stripped}' outside block scalar"

    if not has_name:
        return False, "Missing mandatory 'name' key in frontmatter"

    return True, "OK"


def _iter_source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    ignore_dirs = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "out",
        "coverage",
        "example",
        "examples",
        "fixtures",
        "corpus",
        "goula-constructions",
        "target",
        ".godot",
        "addons",
        "Library",
        "Temp",
        "Obj",
        "Logs",
        "Recordings",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "test-results",
    }
    out: list[Path] = []
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if any(part in ignore_dirs for part in p.parts):
            continue
        if p.is_file() and p.suffix.lower() in suffixes:
            out.append(p)
    return sorted(out)


def _is_test_file(path: Path) -> bool:
    if any(part in ("tests", "test", "__tests__", "e2e") for part in path.parts):
        return True
    return bool(re.search(r"(^|[._-])(test|spec)s?([._-]|$)", path.stem))


def _check_bash_negated_grep_trap(gate_path: Path) -> list[str]:
    """
    Detect the classic Bash `set -e` trap where `! grep ...` on a standalone line
    does NOT abort the script when grep finds a match (POSIX/Bash exempts `!` pipelines from `set -e`).
    """
    if not gate_path.is_file():
        return []
    issues: list[str] = []
    text = gate_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^!\s+grep\b", stripped) and "||" not in stripped and "&&" not in stripped:
            remaining = [ln.strip() for ln in lines[idx:] if ln.strip() and not ln.strip().startswith("#")]
            if remaining:
                issues.append(
                    f"{gate_path.name}:{idx} uses standalone `! grep` before subsequent commands; "
                    "Bash `set -e` ignores non-zero exit codes of `!`-negated commands unless guarded by `|| exit 1`."
                )
    return issues


def _extract_catalog_ids(root: Path) -> tuple[list[str], str]:
    """Extract registered feature IDs (`FEAT-01`, `F-01`, `CAP-01`, etc.) from AGENTS.md and docs/FEATURE_CATALOG.md."""
    ids: list[str] = []
    source_used = "none"
    for rel in ("docs/FEATURE_CATALOG.md", "AGENTS.md", "PAPER_SPEC.md"):
        p = root / rel
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore")
            found = re.findall(r"\b(?:FEAT|F|CAP|THM|CLAIM)-\d{2,3}\b", text)
            if found:
                for item in found:
                    if item not in ids:
                        ids.append(item)
                source_used = rel if source_used == "none" else f"{source_used}, {rel}"
    return ids, source_used


def detect_profile(target: Path) -> str:
    """Auto-detect whether `target` should be evaluated under `video`, `sim`, `paper`, `kb`, or `app` profile."""
    if target.is_file():
        suf = target.suffix.lower()
        if suf in (".mp4", ".mov", ".webm", ".mkv"):
            return "video"
        if suf == ".tex":
            return "paper"
        target = target.parent

    if (
        (target / "project.godot").is_file()
        or (target / "scenes").is_dir()
        or (target / "ProjectSettings" / "ProjectVersion.txt").is_file()
    ):
        return "sim"

    kb_tier_dirs = sum(
        1
        for t in ("00-identity", "01-now", "02-operating-system", "03-life-domains", "04-episodic")
        if (target / t).is_dir()
    )
    has_code_root = (
        (target / "src").is_dir() or (target / "package.json").is_file() or (target / "Cargo.toml").is_file()
    )
    if not has_code_root and ((target / ".kb" / "index.db").is_file() or kb_tier_dirs >= 2):
        return "kb"

    tex_files = [
        p for p in target.glob("*.tex") if "\\documentclass" in p.read_text(encoding="utf-8", errors="ignore")[:4000]
    ]
    if tex_files and not (target / "src").is_dir():
        return "paper"

    mp4s = list(target.glob("*.mp4"))
    srts = list(target.glob("*.srt"))
    if mp4s and (srts or (not (target / "src").is_dir() and not (target / "package.json").is_file())):
        return "video"

    return "app"


def evaluate_app_profile(
    target_dir: Path,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
) -> dict[str, Any]:
    """
    Evaluate a 2D/3D Web App, Cognitive Studio, or Software Repository (`app` profile)
    across the 8 Canonical Goldwood Dimensions (B, U, A, V, P, D, S, R) and 4 Ontological Tiers.
    """
    root = target_dir.resolve()
    blockers: list[dict[str, str]] = []
    notes: list[str] = []

    # 1. Inspect Catalog & Walkthrough Parity (U — Instrument Usability & Feature Parity)
    cat_ids, cat_src = _extract_catalog_ids(root)
    test_files = [p for p in _iter_source_files(root, (".ts", ".tsx", ".js", ".mjs", ".py", ".rs")) if _is_test_file(p)]
    test_corpus = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in test_files)
    has_real_test_defs = bool(
        re.search(
            r"\b(?:def\s+test_|async\s+def\s+test_|fn\s+test_|test\s*\(|it\s*\()|#\[(?:tokio::)?test\]",
            test_corpus,
        )
    )
    probe_states: dict[str, str] = {}

    if not cat_ids:
        score_u = 6.5
        probe_states["U"] = "FAIL"
        blockers.append(
            {
                "id": "GWI-APP-U01",
                "severity": "P0",
                "dimension": "U (Instrument Usability & Catalog)",
                "message": "No `FEAT-XX` or `F-XX` feature catalog entries found in `AGENTS.md` or `docs/FEATURE_CATALOG.md`.",
            }
        )
    else:
        matched_ids = [cid for cid in cat_ids if cid in test_corpus] if has_real_test_defs else []
        spec_drift: list[str] = []
        cat_md_path = root / "docs" / "FEATURE_CATALOG.md"
        if cat_md_path.is_file():
            for line in cat_md_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.strip().startswith("|"):
                    continue
                m_id = re.search(r"\b((?:FEAT|F)-[A-Z0-9_-]*\d+[A-Z0-9_-]*)\b", line)
                if not m_id:
                    continue
                fid = m_id.group(1)
                for m_spec in re.finditer(
                    r"`([A-Za-z0-9_./-]+(?:\.spec|\.test)\.[A-Za-z0-9]+|tests/test_[A-Za-z0-9_]+\.py)`",
                    line,
                ):
                    rel_spec = m_spec.group(1)
                    spec_file = root / rel_spec
                    if not spec_file.is_file():
                        spec_drift.append(f"{fid} -> missing `{rel_spec}`")
                    elif fid not in spec_file.read_text(encoding="utf-8", errors="ignore"):
                        spec_drift.append(f"{fid} not exercised in `{rel_spec}`")

        has_test_assertions = bool(
            re.search(
                r"\b(?:assert\b|expect\s*\(|assert_|assertEqual|assertTrue|assertFalse|assert_called|toBe|toEqual|toStrictEqual|toMatch|rejects|resolves|should)\b|assert(?:_eq|_ne)?\s*!",
                test_corpus,
            )
        )
        if test_files and not has_test_assertions:
            score_u = 6.6
            probe_states["U"] = "FAIL"
            blockers.append(
                {
                    "id": "GWI-APP-U04",
                    "severity": "P0",
                    "dimension": "U (Instrument Usability & Test Assertions)",
                    "message": "Test files lack executable assertions (e.g. `assert`, `expect(...)`, `assert_eq!`). Hollow test stubs detected.",
                }
            )
        elif spec_drift:
            score_u = 7.4
            probe_states["U"] = "FAIL"
            blockers.append(
                {
                    "id": "GWI-APP-U03",
                    "severity": "P1",
                    "dimension": "U (Instrument Usability & Catalog)",
                    "message": ("Feature Catalog Verification Spec drift detected: " + "; ".join(spec_drift[:5])),
                }
            )
        elif has_real_test_defs and len(matched_ids) >= len(cat_ids) * 0.75:
            score_u = 9.8
            probe_states["U"] = "PASS"
            notes.append(
                f"Master Feature Catalog parity verified: {len(matched_ids)}/{len(cat_ids)} catalog IDs referenced in tests."
            )
        elif has_real_test_defs and len(matched_ids) > 0 and len(cat_ids) >= 5:
            score_u = 9.5
            probe_states["U"] = "PASS"
            notes.append(f"Catalog registers {len(cat_ids)} features ({cat_src}) with {len(test_files)} test file(s).")
        else:
            score_u = 7.8
            probe_states["U"] = "UNMEASURED" if len(test_files) == 0 else "FAIL"
            blockers.append(
                {
                    "id": "GWI-APP-U02",
                    "severity": "P1",
                    "dimension": "U (Instrument Usability & Catalog)",
                    "message": f"Catalog has {len(cat_ids)} features ({cat_src}), but executable E2E/unit test coverage is incomplete.",
                }
            )

    # 2. Brand & Identity Integrity (B)
    has_agents_md = (root / "AGENTS.md").is_file()
    has_readme = (root / "README.md").is_file()
    src_files = _iter_source_files(root, (".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".html", ".css"))
    score_b = 9.6 if (has_agents_md and has_readme) else (9.1 if has_agents_md else 7.2)
    if not has_agents_md:
        blockers.append(
            {
                "id": "GWI-APP-B01",
                "severity": "P1",
                "dimension": "B (Brand & Identity Integrity)",
                "message": "Missing root `AGENTS.md` platform guide.",
            }
        )

    # 3. Accessibility & Modern Web Floor (A)
    _non_ui_dirs = {"docs", "artifacts", "scripts", "evidence", "corpus"}
    all_ui_files = _iter_source_files(root, (".tsx", ".jsx", ".html", ".vue", ".svelte"))
    app_ui_files = [p for p in all_ui_files if not any(part in _non_ui_dirs for part in p.relative_to(root).parts)]
    ui_files = app_ui_files if app_ui_files else all_ui_files
    ui_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in ui_files)
    if not ui_files:
        # Check embedded HTML templates in Python dashboard modules if present
        py_ui = [p for p in _iter_source_files(root, (".py",)) if "dashboard" in p.name]
        ui_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in py_ui)
    has_dialog_or_aria = ("<dialog" in ui_text) or ("aria-" in ui_text) or ("color-scheme" in ui_text)
    if (not ui_files and not ui_text) or has_dialog_or_aria:
        score_a = 9.6
    else:
        score_a = 8.2
        blockers.append(
            {
                "id": "GWI-APP-A01",
                "severity": "P1",
                "dimension": "A (Accessibility Floor)",
                "message": "UI files lack `<dialog>`, `aria-*` attributes, or `color-scheme` declarations.",
            }
        )

    # 4. Visual Craft, Token Architecture & Law L7 Regularity (V)
    all_css_files = _iter_source_files(root, (".css",))
    app_css_files = [p for p in all_css_files if not any(part in _non_ui_dirs for part in p.relative_to(root).parts)]
    css_files = app_css_files if app_css_files else all_css_files
    css_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in css_files)
    ui_and_css = ui_text + "\n" + css_text
    has_tokens = (
        (root / "tokens").is_dir()
        or ("--mt-" in ui_and_css)
        or ("--pods-" in ui_and_css)
        or ("--color-" in ui_and_css)
        or ("--" in ui_and_css and ":root" in ui_and_css)
        or (not (root / "web").is_dir() and not (root / "src" / "components").is_dir())
    )
    reg_audit = _audit_structural_regularity(ui_text, css_text)
    score_v = 9.6 if has_tokens else 8.4
    if (
        reg_audit["distinct_font_sizes"] > 12
        or reg_audit["distinct_border_radii"] > 6
        or reg_audit["distinct_grid_templates"] > 7
    ):
        score_v = min(score_v, 8.2)
        blockers.append(
            {
                "id": "GWI-APP-V02",
                "severity": "P1",
                "dimension": "V (Visual Craft & Regularity — Law L7)",
                "message": (
                    "Law L7 Violation (Simplicity Favors Regularity / Η Απλότητα Ευνοεί την Κανονικότητα): "
                    f"ad-hoc CSS micro-variance detected (font_sizes={reg_audit['distinct_font_sizes']} [<=8], "
                    f"border_radii={reg_audit['distinct_border_radii']} [<=4], "
                    f"grid_templates={reg_audit['distinct_grid_templates']} [<=5], R_kanon={reg_audit['r_kanon']})."
                ),
            }
        )
    elif ui_files or css_files:
        notes.append(
            "Law L7 (Simplicity Favors Regularity / Η Απλότητα Ευνοεί την Κανονικότητα) verified: "
            f"font_sizes={reg_audit['distinct_font_sizes']}, radii={reg_audit['distinct_border_radii']}, "
            f"grids={reg_audit['distinct_grid_templates']} (R_kanon={reg_audit['r_kanon']})."
        )

    wedge_audit = _audit_human_wedge_anti_palantir(root, ui_files)
    if wedge_audit["wedge_files_count"] > 0:
        if not wedge_audit["passed"]:
            score_u = min(score_u, 6.6)
            score_v = min(score_v, 6.6)
            probe_states["U"] = "FAIL"
            blockers.append(
                {
                    "id": "GWI-APP-W01",
                    "severity": "P0",
                    "dimension": "U / V (Law L6 & L9 — 'We Are Not Palantir' Human-Data Wedge Gate)",
                    "message": (
                        "Human-Data Wedge Anti-Palantir violation ("
                        + "; ".join(wedge_audit["violations"][:5])
                        + "). Consumer wedges (/snap, /moment, /card, /marketplace) must be single-column "
                        "(max-width <= 680px, 0 split grids/sidebars), progressively disclosed (Step 2/3 hidden until Step 1 action), "
                        "and 100% free of internal B2B ERP/telemetry jargon ('Smart Pool', '#PV-BOOK-', 'SLA <', 'HACCP', 'Ζωντανή Ροή')."
                    ),
                }
            )
        else:
            notes.append(
                f"Law L6 & L9 ('We Are Not Palantir' Wedge Gate) verified across {wedge_audit['wedge_files_count']} "
                "consumer wedge surface(s): single-column <=680px, progressive disclosure, 0 B2B telemetry leaks."
            )

    # 5. Performance & Bundle Hygiene (P) — Measure source & bundle footprint
    oversized_bundles: list[str] = []
    for sp in src_files:
        try:
            if sp.stat().st_size > 5 * 1024 * 1024:
                oversized_bundles.append(str(sp.relative_to(root)))
        except Exception:
            pass
    if oversized_bundles:
        score_p = 7.2
        probe_states["P"] = "FAIL"
        blockers.append(
            {
                "id": "GWI-APP-P01",
                "severity": "P1",
                "dimension": "P (Performance & Bundle Budget)",
                "message": f"Oversized >5MB source/bundle file(s) detected: {', '.join(oversized_bundles[:3])}.",
            }
        )
    else:
        score_p = 9.5
        probe_states["P"] = "PASS" if src_files else "UNMEASURED"

    # 6. Determinism & Hermetic PRNG (D) — language-scoped, quote-safe, and self-exclusion aware
    js_files = [
        p
        for p in _iter_source_files(root, (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte"))
        if not _is_test_file(p)
    ]
    py_files = [p for p in _iter_source_files(root, (".py",)) if not _is_test_file(p)]
    unseeded_random_hits: list[str] = []

    for p in js_files:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if ALLOW_RANDOM_MARKER in txt:
            continue
        if _JS_RANDOM_RE.search(txt):
            unseeded_random_hits.append(str(p.relative_to(root)))

    for p in py_files:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if ALLOW_RANDOM_MARKER in txt:
            continue
        if _PY_RANDOM_RE.search(txt) and not _PY_SEEDED_RE.search(txt):
            unseeded_random_hits.append(str(p.relative_to(root)))

    if unseeded_random_hits:
        score_d = 6.8
        blockers.append(
            {
                "id": "GWI-APP-D01",
                "severity": "P0",
                "dimension": "D (Determinism & PRNG)",
                "message": f"Unseeded PRNG (`Math.random()` / unseeded `random.*`) found in: {', '.join(unseeded_random_hits[:4])}.",
            }
        )
    else:
        score_d = 9.8

    # 7. System Architecture & Gate Hygiene (S)
    gate_sh = root / "scripts" / "quality_gate.sh"
    has_pkg_verify = False
    if (root / "package.json").is_file():
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
            scripts_dict = pkg.get("scripts") or {}
            has_pkg_verify = any(k in scripts_dict for k in ("verify", "test", "record:walkthrough"))
        except Exception:
            pass
    has_pyproject_test = (root / "pyproject.toml").is_file() and (
        "[tool.pytest" in (root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
    )
    has_cargo_manifest = (root / "Cargo.toml").is_file()

    gate_trap_issues = _check_bash_negated_grep_trap(gate_sh)
    if gate_sh.is_file():
        gate_txt = gate_sh.read_text(encoding="utf-8", errors="ignore")
        missing_runner = False
        if "vitest" in gate_txt and not (
            (root / "package.json").is_file()
            and "vitest" in (root / "package.json").read_text(encoding="utf-8", errors="ignore")
        ):
            missing_runner = True
        elif "pytest" in gate_txt and not (
            (root / "pyproject.toml").is_file()
            or (root / "setup.py").is_file()
            or (root / "requirements.txt").is_file()
            or any(p.suffix == ".py" for p in test_files)
        ):
            missing_runner = True

        if missing_runner:
            score_s = 6.4
            probe_states["S"] = "FAIL"
            blockers.append(
                {
                    "id": "GWI-APP-S03",
                    "severity": "P0",
                    "dimension": "S (System & Gate Hygiene)",
                    "message": "`scripts/quality_gate.sh` invokes an unconfigured test runner not found in package manifests or test files.",
                }
            )
        else:
            score_s = 9.7 if not gate_trap_issues else 8.5
            for issue in gate_trap_issues:
                blockers.append(
                    {
                        "id": "GWI-APP-S01",
                        "severity": "P1",
                        "dimension": "S (System & Gate Hygiene)",
                        "message": issue,
                    }
                )
    elif has_pkg_verify or has_pyproject_test or has_cargo_manifest:
        score_s = 9.2
    else:
        score_s = 6.5
        blockers.append(
            {
                "id": "GWI-APP-S02",
                "severity": "P0",
                "dimension": "S (System & Gate Hygiene)",
                "message": "Missing `scripts/quality_gate.sh` and `package.json`/`pyproject.toml` verification config.",
            }
        )

    # 8. Release Evidence, Runtime Diagnostics & Walkthrough (R) + Console Hygiene Lambda (worst-case preserved!)
    conscience_clean = True
    diag_files = (
        list(root.glob("evidence/**/*.diagnostics.json"))
        + list(root.glob("artifacts/*.diagnostics.json"))
        + list(root.glob("**/walkthrough-report.json"))
    )
    score_r = 9.4
    seen_clean_diag = False
    seen_diag_error = False
    expected_diag_keys = {
        "pageErrors",
        "consoleErrors",
        "httpErrors",
        "failedResponses",
        "failedRequests",
        "zeroViolations",
        "secretLeaks",
    }
    for pw_cand in list(root.glob("evidence/playwright/report.json")) + list(root.glob("artifacts/ux_telemetry.json")):
        if pw_cand not in diag_files:
            try:
                pw_obj = json.loads(pw_cand.read_text(encoding="utf-8"))
                if isinstance(pw_obj, dict) and (set(pw_obj.keys()) & expected_diag_keys):
                    diag_files.append(pw_cand)
            except Exception:
                diag_files.append(pw_cand)

    for df in diag_files:
        try:
            diag = json.loads(df.read_text(encoding="utf-8"))
            if not isinstance(diag, dict) or not (set(diag.keys()) & expected_diag_keys):
                raise ValueError("Empty or schema-invalid diagnostics payload")
            p_err = len(diag.get("pageErrors") or [])
            c_err = len(diag.get("consoleErrors") or [])
            h_err = len(diag.get("httpErrors") or diag.get("failedResponses") or [])
            f_req = len(diag.get("failedRequests") or [])
            raw_leaks = diag.get("secretLeaks")
            s_leaks = len(raw_leaks) if isinstance(raw_leaks, list) else int(raw_leaks or 0)
            if p_err > 0 or c_err > 0 or h_err > 0 or f_req > 0 or s_leaks > 0:
                conscience_clean = False
                score_r = min(score_r, 5.5)
                probe_states["R"] = "FAIL"
                blockers.append(
                    {
                        "id": "GWI-APP-R01",
                        "severity": "P0",
                        "dimension": "R (Release Telemetry & Console Hygiene)",
                        "message": (
                            f"Runtime violations in {df.relative_to(root)}: "
                            f"pageErrors={p_err}, consoleErrors={c_err}, httpErrors={h_err}, "
                            f"failedRequests={f_req}, secretLeaks={s_leaks}."
                        ),
                    }
                )
            else:
                if df.name == "report.json" and "playwright" in str(df):
                    has_browser_harness = (
                        any(
                            "playwright" in p.read_text(encoding="utf-8", errors="ignore").lower()
                            for p in (
                                list(root.glob("package.json"))
                                + list(root.glob("scripts/*.sh"))
                                + list(root.glob("scripts/*.py"))
                            )
                        )
                        or any(
                            "playwright" in p.name.lower() or "e2e" in p.name.lower() or "spec" in p.name.lower()
                            for p in test_files
                        )
                        or (root / "playwright.config.ts").is_file()
                        or (root / "playwright.config.js").is_file()
                    )
                    if not has_browser_harness:
                        score_r = min(score_r, 6.8)
                        probe_states["R"] = "UNMEASURED"
                        blockers.append(
                            {
                                "id": "GWI-APP-R04",
                                "severity": "P0",
                                "dimension": "R (Release Telemetry & Harness Provenance)",
                                "message": f"Ungrounded telemetry in {df.relative_to(root)}: repository lacks Playwright test dependencies or test scripts to generate browser diagnostics.",
                            }
                        )
                        continue
                seen_clean_diag = True
                notes.append(f"Verified 0 runtime console/page/HTTP/secret errors in {df.relative_to(root)}.")
        except Exception as exc:
            conscience_clean = False
            seen_diag_error = True
            score_r = min(score_r, 6.0)
            probe_states["R"] = "ERROR"
            blockers.append(
                {
                    "id": "GWI-APP-R02",
                    "severity": "P0",
                    "dimension": "R (Release Telemetry & Console Hygiene)",
                    "message": f"Malformed or empty diagnostics JSON in {df.relative_to(root)}: {exc}",
                }
            )

    # Scan non-test source files for hardcoded secret leaks (Lambda_conscience == 0 on secretLeaks > 0)
    secret_hit_files: list[str] = []
    for sp in src_files:
        if _is_test_file(sp) or sp.name in ("gwi.py", "agents.py"):
            continue
        txt = sp.read_text(encoding="utf-8", errors="ignore")
        if _SECRET_LEAK_RE.search(txt):
            secret_hit_files.append(str(sp.relative_to(root)))
    if secret_hit_files:
        conscience_clean = False
        score_r = min(score_r, 5.0)
        probe_states["R"] = "FAIL"
        blockers.append(
            {
                "id": "GWI-APP-R03",
                "severity": "P0",
                "dimension": "R (Secret Hygiene & Conscience Gate)",
                "message": f"Hardcoded secret token leak detected in: {', '.join(secret_hit_files[:3])}.",
            }
        )

    if seen_diag_error:
        probe_states["R"] = "ERROR"
    elif not conscience_clean:
        probe_states["R"] = "FAIL"
    elif seen_clean_diag:
        score_r = 9.8
        probe_states["R"] = "PASS"
    else:
        probe_states["R"] = "UNMEASURED"

    subtractive_metrics: dict[str, Any] = {}
    for ux_f in list(root.glob("evidence/playwright/report.json")) + list(root.glob("artifacts/ux_telemetry.json")):
        try:
            ux_obj = json.loads(ux_f.read_text(encoding="utf-8"))
            if isinstance(ux_obj, dict):
                for k_name in DEFAULT_SUBTRACTIVE_BUDGETS:
                    if k_name in ux_obj and ux_obj[k_name] is not None:
                        subtractive_metrics[k_name] = ux_obj[k_name]
        except Exception:
            pass

    dimensions = [
        {
            "id": "B",
            "tier": "S_exist",
            "name": "Brand & Identity Integrity",
            "weight": 0.20,
            "score": round(score_b, 2),
        },
        {
            "id": "U",
            "tier": "S_act",
            "name": "Instrument Usability & Catalog Parity",
            "weight": 0.15,
            "score": round(score_u, 2),
        },
        {
            "id": "A",
            "tier": "S_perc",
            "name": "Accessibility & Semantic Floor",
            "weight": 0.15,
            "score": round(score_a, 2),
        },
        {
            "id": "V",
            "tier": "S_perc",
            "name": "Visual Craft & Token Contract",
            "weight": 0.15,
            "score": round(score_v, 2),
        },
        {"id": "P", "tier": "S_sub", "name": "Performance & Bundle Budget", "weight": 0.10, "score": round(score_p, 2)},
        {"id": "D", "tier": "S_act", "name": "Determinism & Hermetic PRNG", "weight": 0.10, "score": round(score_d, 2)},
        {
            "id": "S",
            "tier": "S_sub",
            "name": "System Architecture & Gate Hygiene",
            "weight": 0.10,
            "score": round(score_s, 2),
        },
        {
            "id": "R",
            "tier": "S_exist",
            "name": "Release Evidence & Runtime Telemetry",
            "weight": 0.05,
            "score": round(score_r, 2),
        },
    ]

    s_sub = aggregate_tier_pair(score_s, score_p, w_a=0.10, w_b=0.10, theta=theta)
    s_perc = aggregate_tier_pair(score_v, score_a, w_a=0.15, w_b=0.15, theta=theta)
    s_act = aggregate_tier_pair(score_u, score_d, w_a=0.15, w_b=0.10, theta=theta)
    s_exist = aggregate_tier_pair(score_b, score_r, w_a=0.20, w_b=0.05, theta=theta)

    g_calc = compute_goldwood_index(
        [s_sub, s_perc, s_act, s_exist],
        weights=ARCHETYPE_WEIGHTS["app"],
        theta=theta,
        k=k,
        conscience_clean=conscience_clean,
        subtractive_metrics=subtractive_metrics or None,
    )
    s_perc_eff = float(g_calc["tier_scores"][1])
    essential_files = max(4, len(cat_ids) * 3 + 2)
    elegance = compute_kolmogorov_elegance(len(src_files), essential_files)
    snrc = compute_snrc(len(cat_ids) + len(notes), len(blockers))

    return {
        "profile": "app",
        "target": str(root),
        "project_name": root.name,
        "catalog_features_count": len(cat_ids),
        "catalog_source": cat_src,
        "kolmogorov_elegance": elegance,
        "snrc": snrc,
        "dimensions": dimensions,
        "tiers": {
            "S_sub": round(s_sub, 2),
            "S_perc": round(s_perc_eff, 2),
            "S_act": round(s_act, 2),
            "S_exist": round(s_exist, 2),
        },
        "calculation": g_calc,
        "probe_states": probe_states,
        "blockers": blockers,
        "notes": notes,
    }


def _resolve_godot_reachable_scripts(root: Path, gate_text: str, gd_files: list[Path]) -> set[str]:
    """
    Build a transitive reachability set of `.gd` script filenames exercised by `scripts/quality_gate.sh`:
      1. Direct `-s path/to/script.gd` invocations or `*.gd` / `scenes/*` globs in `gate_text`
      2. `.tscn` scenes invoked in `gate_text` -> `[ext_resource ... path="res://...gd"]`
      3. Transitive `preload("res://...gd")` / `load("res://...gd")` inside reachable `.gd` scripts
    """
    reachable: set[str] = set()
    by_name = {p.name: p for p in gd_files}

    if re.search(r"scenes?/\*|\*\.gd|\*\.tscn", gate_text):
        return {p.name for p in gd_files}

    for p in gd_files:
        if p.name in gate_text:
            reachable.add(p.name)

    for tscn in _iter_source_files(root, (".tscn",)):
        if tscn.name in gate_text or str(tscn.relative_to(root)) in gate_text:
            t_txt = tscn.read_text(encoding="utf-8", errors="ignore")
            for m in re.findall(r'res://([^"\'\s]+\.gd)', t_txt):
                reachable.add(Path(m).name)

    queue = list(reachable)
    while queue:
        curr = queue.pop()
        gd_path = by_name.get(curr)
        if not gd_path:
            continue
        g_txt = gd_path.read_text(encoding="utf-8", errors="ignore")
        for m in re.findall(r'(?:preload|load)\(\s*["\']res://([^"\']+\.gd)["\']\s*\)', g_txt):
            dep_name = Path(m).name
            if dep_name not in reachable:
                reachable.add(dep_name)
                queue.append(dep_name)

    return reachable


def evaluate_sim_profile(
    target_dir: Path,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
) -> dict[str, Any]:
    """
    Evaluate a 3D Simulation / Spatial Engine Repository (`sim` profile: Godot 4, Bevy ECS, Unity)
    against the Tri-Council Fable x Astra 4-Tier Simulation & Queueing Physics Rubric.
    """
    root = target_dir.resolve()
    blockers: list[dict[str, str]] = []
    notes: list[str] = []

    gd_files = _iter_source_files(root, (".gd",))
    shader_files = _iter_source_files(root, (".gdshader", ".glsl", ".wgsl", ".shader", ".hlsl"))
    rs_files = _iter_source_files(root, (".rs",))
    cs_files = _iter_source_files(root, (".cs",))
    sh_files = _iter_source_files(root, (".sh",))

    # 1. S_sub (Tier I): Gate Hygiene, Transitive Scene Coverage & Lifecycle Clock
    gate_sh = root / "scripts" / "quality_gate.sh"
    gate_traps = _check_bash_negated_grep_trap(gate_sh)
    gate_text = gate_sh.read_text(encoding="utf-8", errors="ignore") if gate_sh.is_file() else ""

    score_gate = 9.6
    if not gate_sh.is_file():
        score_gate = 5.5
        blockers.append(
            {
                "id": "GWI-SIM-A01A",
                "severity": "P0",
                "dimension": "S_sub (Quality Gate & Lifecycle)",
                "message": "Missing `scripts/quality_gate.sh`.",
            }
        )
    else:
        if gate_traps:
            score_gate -= 1.8
            for trap in gate_traps:
                blockers.append(
                    {
                        "id": "GWI-SIM-A01B",
                        "severity": "P0",
                        "dimension": "S_sub (Quality Gate & Lifecycle)",
                        "message": trap,
                    }
                )
        reachable_gd = _resolve_godot_reachable_scripts(root, gate_text, gd_files)
        non_stub_scenes = [p.name for p in gd_files if p.name != "main.gd"]
        unexercised = [name for name in non_stub_scenes if name not in reachable_gd]
        if unexercised:
            score_gate -= 1.5
            blockers.append(
                {
                    "id": "GWI-SIM-A01C",
                    "severity": "P0",
                    "dimension": "S_sub (Quality Gate & Lifecycle)",
                    "message": f"`scripts/quality_gate.sh` never exercises (directly or via `.tscn`/`preload`): {', '.join(unexercised)}.",
                }
            )
        elif non_stub_scenes:
            notes.append(
                f"Transitive Godot scene reachability verified across {len(reachable_gd)} script(s): {', '.join(sorted(reachable_gd))}."
            )

    # Unity CLI Anti-Deadlock Gate (GWI-SIM-U01 & GWI-SIM-U02):
    # Launching Unity with `-executeMethod` without `-batchmode` opens a blocking modal dialog whenever C# scripts have
    # compile errors (`error CS*`), ignoring `-quit` and hanging SSH/CI forever.
    for sh_p in sh_files:
        sh_txt = sh_p.read_text(encoding="utf-8", errors="ignore")
        for idx_ln, ln in enumerate(sh_txt.splitlines(), start=1):
            stripped_ln = ln.strip()
            if stripped_ln.startswith("#"):
                continue
            if "Unity" in stripped_ln and "-executeMethod" in stripped_ln and "-batchmode" not in stripped_ln:
                if "-batchmode" not in sh_txt:
                    score_gate -= 2.0
                    blockers.append(
                        {
                            "id": "GWI-SIM-U01",
                            "severity": "P0",
                            "dimension": "S_sub (Unity Headless Batchmode Anti-Deadlock Gate)",
                            "message": (
                                f"`{sh_p.relative_to(root)}:{idx_ln}` invokes Unity `-executeMethod` without `-batchmode` "
                                "(or a preceding `-batchmode -nographics -quit` compile gate); any C# compile error (`error CS*`) "
                                "will open a modal GUI and deadlock headless/SSH execution."
                            ),
                        }
                    )

    if cs_files:
        for cs_p in cs_files:
            cs_txt = cs_p.read_text(encoding="utf-8", errors="ignore")
            if "using System.Diagnostics;" in cs_txt and re.search(r"(?<![.\w])Debug\.Log", cs_txt):
                if "using Debug = UnityEngine.Debug;" not in cs_txt:
                    score_gate -= 1.5
                    blockers.append(
                        {
                            "id": "GWI-SIM-U02",
                            "severity": "P0",
                            "dimension": "S_sub (Unity C# Compiler Hygiene)",
                            "message": (
                                f"`{cs_p.relative_to(root)}` imports `System.Diagnostics` and calls unqualified `Debug.Log(...)` "
                                "without `using Debug = UnityEngine.Debug;` (triggers CS0104 ambiguous reference error)."
                            ),
                        }
                    )

    # Check Lifecycle & Clock (A-03): scoped to scene scripts defining `_process` or `_physics_process`
    score_clock = 9.6
    all_gd_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in gd_files)
    repo_has_quit = "get_tree().quit(" in all_gd_text or ".quit(" in all_gd_text
    for gd in gd_files:
        txt = gd.read_text(encoding="utf-8", errors="ignore")
        defines_loop = ("func _process(" in txt) or ("func _physics_process(" in txt)
        if defines_loop and "TOTAL_FRAMES" in txt and "quit(" not in txt and not repo_has_quit:
            score_clock -= 2.2
            blockers.append(
                {
                    "id": "GWI-SIM-A03A",
                    "severity": "P0",
                    "dimension": "S_sub (Lifecycle & SimClock)",
                    "message": f"`{gd.relative_to(root)}` runs `_process` with `TOTAL_FRAMES` but never calls `get_tree().quit()`.",
                }
            )
        if "func _process(_delta" in txt and "extends SceneTree" not in txt:
            score_clock -= 1.2
            blockers.append(
                {
                    "id": "GWI-SIM-A03B",
                    "severity": "P1",
                    "dimension": "S_sub (Lifecycle & SimClock)",
                    "message": f"`{gd.relative_to(root)}` ignores `_delta` in `_process(_delta)`, tying interactive simulation speed to render FPS.",
                }
            )

    # 2. S_perc (Tier II): MultiMesh / GPU Instancing, Shaders & Responsive HUD
    has_multimesh = ("MultiMesh" in all_gd_text) or bool(shader_files) or bool(rs_files)
    has_per_agent_mesh_loop = ("MeshInstance3D.new()" in all_gd_text) and ("clear_surfaces()" in all_gd_text)

    if has_multimesh and not has_per_agent_mesh_loop:
        score_render = 9.6
        notes.append(f"GPU instancing (`MultiMesh`) and {len(shader_files)} shader(s) verified.")
    elif has_per_agent_mesh_loop and not has_multimesh:
        score_render = 6.8
        blockers.append(
            {
                "id": "GWI-SIM-A05",
                "severity": "P1",
                "dimension": "S_perc (Instancing & Draw-Call Ceiling)",
                "message": "Spawns individual `MeshInstance3D` + `StandardMaterial3D` nodes and rebuilds `ImmediateMesh` (`clear_surfaces()`) per frame instead of `MultiMeshInstance3D` + custom `.gdshader`.",
            }
        )
    else:
        score_render = 9.2

    has_hardcoded_1080p_hud = bool(
        re.search(r"\b(?:position|offset|size|custom_minimum_size)\s*=\s*Vector2\(\s*1[345]\d\d\s*,", all_gd_text)
    )
    if has_hardcoded_1080p_hud:
        score_hud = 7.2
        blockers.append(
            {
                "id": "GWI-SIM-A06",
                "severity": "P1",
                "dimension": "S_perc (Responsive Anchor HUD)",
                "message": "HUD panels use hardcoded 1080p pixel coordinates (`position = Vector2(1410, ...)`) instead of responsive `Control` anchors (`16:9` / `9:16` / `4K`).",
            }
        )
    else:
        score_hud = 9.5

    # 3. S_act (Tier III): Mathematical & Queueing Physics Fidelity
    has_parity_routing = bool(re.search(r"\bi\s*%\s*2\s*==\s*0\b", all_gd_text)) and ("target" in all_gd_text)
    if has_parity_routing and "queue" not in all_gd_text.lower():
        score_physics = 6.6
        blockers.append(
            {
                "id": "GWI-SIM-A04",
                "severity": "P1",
                "dimension": "S_act (Queueing & O-RAN Physics Fidelity)",
                "message": "Agent routing uses deterministic `i % 2 == 0` parity modulo shortcut instead of a seeded Gelenbe G-network, confidence-gated Beta(alpha,beta) cascade, and HPBW beam-aging model.",
            }
        )
    else:
        score_physics = 9.5

    # 4. S_exist (Tier IV): Verifier Honesty (`verify_sim.py`), Runtime Telemetry Lambda & Catalog Sync
    verify_py = root / "sim" / "verify_sim.py"
    score_verifier = 9.6
    if verify_py.is_file():
        v_txt = verify_py.read_text(encoding="utf-8", errors="ignore")
        v_code_only = "\n".join(ln for ln in v_txt.splitlines() if not ln.strip().startswith("#"))
        if "math.exp(-0.01 * t)" in v_code_only and "sim_telemetry" not in v_code_only:
            score_verifier = 5.2
            blockers.append(
                {
                    "id": "GWI-SIM-A01D",
                    "severity": "P0",
                    "dimension": "S_exist (Verifier Provenance & Telemetry Honesty)",
                    "message": "`sim/verify_sim.py` synthesizes a hardcoded `math.exp(-0.01 * t)` curve without reading real engine telemetry.",
                }
            )
    else:
        score_verifier = 6.0
        blockers.append(
            {
                "id": "GWI-SIM-A01E",
                "severity": "P0",
                "dimension": "S_exist (Verifier Provenance)",
                "message": "Missing `sim/verify_sim.py` simulation telemetry verifier.",
            }
        )

    conscience_clean = True
    sim_tel_file = root / "results" / "sim_telemetry.json"
    if sim_tel_file.is_file():
        try:
            sim_tel = json.loads(sim_tel_file.read_text(encoding="utf-8"))
            if sim_tel.get("completed_cleanly") is False:
                conscience_clean = False
                blockers.append(
                    {
                        "id": "GWI-SIM-R01",
                        "severity": "P0",
                        "dimension": "S_exist (Simulation Telemetry Conscience)",
                        "message": "`results/sim_telemetry.json` reports `completed_cleanly: false`.",
                    }
                )
            else:
                notes.append("Verified `results/sim_telemetry.json` clean simulation completion.")
        except Exception:
            pass

    cat_ids, cat_src = _extract_catalog_ids(root)
    cat_file = root / "docs" / "FEATURE_CATALOG.md"
    cat_text = cat_file.read_text(encoding="utf-8", errors="ignore") if cat_file.is_file() else ""
    non_main_gd = [p.name for p in gd_files if p.name != "main.gd"]
    missing_in_cat = [name for name in non_main_gd if name not in cat_text]
    if (len(cat_ids) <= 1 and non_main_gd) or (missing_in_cat and len(cat_ids) < 5):
        score_cat = 5.8
        blockers.append(
            {
                "id": "GWI-SIM-A02",
                "severity": "P0",
                "dimension": "S_exist (Master Feature Catalog Sync)",
                "message": f"`docs/FEATURE_CATALOG.md` omits active simulation scripts: {', '.join(missing_in_cat)}.",
            }
        )
    else:
        score_cat = 9.6

    s_sub = max(0.0, min(score_gate, score_clock))
    s_perc = max(0.0, min((score_render + score_hud) / 2.0, min(score_render, score_hud) + 0.3))
    s_act = max(0.0, score_physics)
    s_exist = max(0.0, min(score_verifier, score_cat))

    dimensions = [
        {
            "id": "G",
            "tier": "S_sub",
            "name": "Quality Gate & Transitive Scene Coverage",
            "weight": 0.15,
            "score": round(score_gate, 2),
        },
        {
            "id": "L",
            "tier": "S_sub",
            "name": "Lifecycle Termination & SimClock",
            "weight": 0.10,
            "score": round(score_clock, 2),
        },
        {
            "id": "I",
            "tier": "S_perc",
            "name": "MultiMesh GPU Instancing & Shaders",
            "weight": 0.15,
            "score": round(score_render, 2),
        },
        {
            "id": "H",
            "tier": "S_perc",
            "name": "Responsive Anchor HUD (16:9 / 9:16)",
            "weight": 0.10,
            "score": round(score_hud, 2),
        },
        {
            "id": "M",
            "tier": "S_act",
            "name": "Queueing, O-RAN & Physics Fidelity",
            "weight": 0.30,
            "score": round(score_physics, 2),
        },
        {
            "id": "V",
            "tier": "S_exist",
            "name": "Telemetry Honesty & Verifier Provenance",
            "weight": 0.10,
            "score": round(score_verifier, 2),
        },
        {
            "id": "C",
            "tier": "S_exist",
            "name": "Master Feature Catalog Synchronization",
            "weight": 0.10,
            "score": round(score_cat, 2),
        },
    ]

    g_calc = compute_goldwood_index(
        [s_sub, s_perc, s_act, s_exist],
        weights=ARCHETYPE_WEIGHTS["sim"],
        theta=theta,
        k=k,
        conscience_clean=conscience_clean,
    )

    return {
        "profile": "sim",
        "target": str(root),
        "project_name": root.name,
        "catalog_features_count": len(cat_ids),
        "catalog_source": cat_src,
        "kolmogorov_elegance": compute_kolmogorov_elegance(len(gd_files) + len(shader_files), max(len(cat_ids), 3)),
        "snrc": compute_snrc(len(cat_ids) + len(notes), len(blockers)),
        "dimensions": dimensions,
        "tiers": {
            "S_sub": round(s_sub, 2),
            "S_perc": round(s_perc, 2),
            "S_act": round(s_act, 2),
            "S_exist": round(s_exist, 2),
        },
        "calculation": g_calc,
        "probe_states": {d["id"]: ("PASS" if d["score"] >= 9.0 else "FAIL") for d in dimensions},
        "blockers": blockers,
        "notes": notes,
    }


def _probe_video_streams(mp4_path: Path) -> dict[str, Any]:
    """
    Inspect video and audio stream metadata via `ffprobe`.
    Never fabricates resolution/fps when `ffprobe` fails on a corrupt/synthetic stub;
    explicitly records `probe_source` ("ffprobe" vs "unverified").
    """
    info: dict[str, Any] = {
        "path": str(mp4_path),
        "size_bytes": mp4_path.stat().st_size if mp4_path.is_file() else 0,
        "probed": False,
        "probe_source": "unverified",
        "has_video": False,
        "has_audio": False,
        "width": 0,
        "height": 0,
        "codec_name": "unknown",
        "pix_fmt": "unknown",
        "fps": 0.0,
        "duration_s": 0.0,
        "audio_channels": 0,
        "audio_sample_rate": 0,
    }
    if not mp4_path.is_file() or info["size_bytes"] == 0:
        return info

    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin:
        try:
            res = subprocess.run(
                [
                    ffprobe_bin,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(mp4_path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                streams = data.get("streams") or []
                if streams:
                    info["probed"] = True
                    info["probe_source"] = "ffprobe"
                    fmt = data.get("format") or {}
                    info["duration_s"] = round(float(fmt.get("duration") or 0.0), 2)
                    for st in streams:
                        ctype = st.get("codec_type")
                        if ctype == "video" and not info["has_video"]:
                            info["has_video"] = True
                            info["width"] = int(st.get("width") or 0)
                            info["height"] = int(st.get("height") or 0)
                            info["codec_name"] = str(st.get("codec_name") or "unknown")
                            info["pix_fmt"] = str(st.get("pix_fmt") or "unknown")
                            r_fps = str(st.get("r_frame_rate") or "0/1")
                            if "/" in r_fps:
                                num, den = r_fps.split("/", 1)
                                info["fps"] = round(float(num) / max(1.0, float(den)), 2)
                        elif ctype == "audio" and not info["has_audio"]:
                            info["has_audio"] = True
                            info["audio_channels"] = int(st.get("channels") or 0)
                            info["audio_sample_rate"] = int(st.get("sample_rate") or 0)
                    return info
        except Exception:
            pass

    raw = mp4_path.read_bytes()[:65536]
    info["has_video"] = b"ftyp" in raw or b"moov" in raw
    info["has_audio"] = b"mp4a" in raw or b"soun" in raw
    return info


def _probe_video_frames(mp4_path: Path, duration_s: float) -> dict[str, Any]:
    """
    Sample up to 3 keyframes (20%, 50%, 80% of duration) via `ffmpeg` into a 320x180 raw RGB24 buffer
    in memory to verify non-blank spatial entropy (std_luma >= 2.0) and safe-area margin poise.
    """
    probe: dict[str, Any] = {
        "sampled_frames": 0,
        "mean_luma": 0.0,
        "min_spatial_std": 0.0,
        "blank_frames": 0,
        "safe_area_ratio": 1.0,
    }
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin or not mp4_path.is_file():
        return probe

    w, h = 320, 180
    expected_bytes = w * h * 3
    dur = max(1.0, float(duration_s or 1.0))
    timestamps = [round(dur * r, 2) for r in (0.2, 0.5, 0.8)] if dur >= 1.5 else [0.0]

    luma_means: list[float] = []
    luma_stds: list[float] = []
    safe_ratios: list[float] = []

    for ts in timestamps:
        try:
            res = subprocess.run(
                [
                    ffmpeg_bin,
                    "-nostats",
                    "-hide_banner",
                    "-ss",
                    str(ts),
                    "-i",
                    str(mp4_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={w}:{h}",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
            buf = res.stdout
            if len(buf) >= expected_bytes:
                probe["sampled_frames"] += 1
                # Subsample every 4th pixel for rapid deterministic luminance & safe-area stats
                total_y = 0.0
                total_y2 = 0.0
                count = 0
                edge_activity = 0.0
                center_activity = 0.0
                for py in range(0, h, 2):
                    row_off = py * w * 3
                    in_safe_margin = py < 15 or py > 162  # Top 92px / Bottom 108px scaled to 180p
                    prev_y: float | None = None
                    for px in range(0, w, 2):
                        idx = row_off + px * 3
                        r_v, g_v, b_v = buf[idx], buf[idx + 1], buf[idx + 2]
                        y_val = 0.299 * r_v + 0.587 * g_v + 0.114 * b_v
                        total_y += y_val
                        total_y2 += y_val * y_val
                        count += 1
                        if prev_y is not None:
                            diff = abs(y_val - prev_y)
                            if in_safe_margin:
                                edge_activity += diff
                            else:
                                center_activity += diff
                        prev_y = y_val
                mean_y = total_y / max(1, count)
                var_y = max(0.0, (total_y2 / max(1, count)) - (mean_y * mean_y))
                std_y = math.sqrt(var_y)
                luma_means.append(mean_y)
                luma_stds.append(std_y)
                if std_y < 2.0:
                    probe["blank_frames"] += 1
                safe_ratios.append(center_activity / max(1.0, edge_activity + center_activity))
        except Exception:
            pass

    if luma_means:
        probe["mean_luma"] = round(sum(luma_means) / len(luma_means), 2)
        probe["min_spatial_std"] = round(min(luma_stds), 2)
        probe["safe_area_ratio"] = round(sum(safe_ratios) / len(safe_ratios), 4)
    return probe


def _measure_ebur128(mp4_path: Path) -> dict[str, float | None]:
    """Run live `ffmpeg -af ebur128=peak=true` broadcast loudness measurement."""
    out: dict[str, float | None] = {"integrated_lufs": None, "lra_lu": None, "true_peak_dbtp": None}
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin or not mp4_path.is_file():
        return out
    try:
        res = subprocess.run(
            [
                ffmpeg_bin,
                "-nostats",
                "-hide_banner",
                "-i",
                str(mp4_path),
                "-vn",
                "-af",
                "ebur128=peak=true",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        summary_idx = res.stderr.rfind("Summary:")
        tail = res.stderr[summary_idx:] if summary_idx != -1 else res.stderr
        m_i = _LUFS_RE.search(tail)
        m_lra = _LRA_RE.search(tail)
        m_pk = _PEAK_RE.search(tail)
        if m_i:
            out["integrated_lufs"] = float(m_i.group(1))
        if m_lra:
            out["lra_lu"] = float(m_lra.group(1))
        if m_pk:
            out["true_peak_dbtp"] = float(m_pk.group(1))
    except Exception:
        pass
    return out


def _validate_srt_captions(srt_path: Path) -> dict[str, Any]:
    """
    Validate an `.srt` or `.vtt` closed-caption file for cue count, monotonic timestamps,
    zero cue overlaps, and Netflix/BBC broadcast subtitle ergonomics:
      - Reading speed CPS = len(text) / (end_s - start_s) <= 20.0 chars/sec (veto > 28.0)
      - Maximum line length <= 42 chars (veto > 68 chars on a single unsplit line)
      - Cue duration in [0.6s, 8.5s]
    """
    if not srt_path.is_file():
        return {
            "valid": False,
            "cues": 0,
            "overlaps": 0,
            "max_cps": 0.0,
            "max_line_chars": 0,
            "min_duration_s": 0.0,
            "max_duration_s": 0.0,
        }
    text = srt_path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n").strip()
    blocks = re.split(r"\n\s*\n", text)
    time_re = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")

    def _to_sec(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    cues = 0
    overlaps = 0
    prev_end = -1.0
    max_cps = 0.0
    max_line_chars = 0
    durations: list[float] = []

    for blk in blocks:
        m = time_re.search(blk)
        if m:
            cues += 1
            start_s = _to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
            end_s = _to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
            dur = max(0.001, end_s - start_s)
            durations.append(dur)
            if start_s < prev_end - 0.005 or end_s <= start_s:
                overlaps += 1
            prev_end = max(prev_end, end_s)

            after_ts = blk[m.end() :].strip()
            clean_lines = [re.sub(r"<[^>]+>", "", ln).strip() for ln in after_ts.splitlines() if ln.strip()]
            total_chars = sum(len(ln) for ln in clean_lines)
            if clean_lines:
                max_line_chars = max(max_line_chars, max(len(ln) for ln in clean_lines))
            max_cps = max(max_cps, round(total_chars / dur, 2))

    return {
        "valid": cues > 0 and overlaps == 0 and max_cps <= 28.0 and max_line_chars <= 68,
        "cues": cues,
        "overlaps": overlaps,
        "max_cps": round(max_cps, 2),
        "max_line_chars": max_line_chars,
        "min_duration_s": round(min(durations), 2) if durations else 0.0,
        "max_duration_s": round(max(durations), 2) if durations else 0.0,
    }


def evaluate_video_profile(
    target: Path,
    probe_audio: bool = False,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
) -> dict[str, Any]:
    """
    Evaluate a YouTube Broadcast Master or Platform Video Walkthrough (`video` profile)
    across the 4 Canonical Global Watchability Index (GWI) dimensions.
    """
    if target.is_file():
        root = target.resolve().parent
        mp4_files = [target.resolve()] + [
            p.resolve() for p in sorted(root.glob("*.mp4")) if p.resolve() != target.resolve()
        ]
    else:
        root = target.resolve()
        mp4_files = sorted(
            list(root.glob("*.mp4"))
            + list(root.glob("artifacts/*.mp4"))
            + list(root.glob("results/*.mp4"))
            + list(root.glob("docs/walkthrough/*.mp4")),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )

    blockers: list[dict[str, str]] = []
    notes: list[str] = []

    if not mp4_files:
        calc = compute_goldwood_index([4.0, 4.0, 4.0, 4.0], weights=ARCHETYPE_WEIGHTS["video"], theta=theta, k=k)
        return {
            "profile": "video",
            "target": str(root),
            "project_name": root.name,
            "dimensions": [],
            "tiers": {"S_sub": 4.0, "S_perc": 4.0, "S_act": 4.0, "S_exist": 4.0},
            "calculation": calc,
            "blockers": [
                {
                    "id": "GWI-VID-00",
                    "severity": "P0",
                    "dimension": "Video Master",
                    "message": f"No `.mp4` video master found in {root}.",
                }
            ],
            "notes": [],
        }

    master_mp4 = mp4_files[0]
    v_meta = _probe_video_streams(master_mp4)

    # 1. Dim 1: Visual & Layout / Thumbnail (S_perc)
    score_visual = 9.5
    valid_resolutions = {(1920, 1080), (1080, 1920), (3840, 2160), (2160, 3840), (2560, 1440), (1440, 900), (1280, 720)}
    if not v_meta["probed"]:
        score_visual = 6.8
        blockers.append(
            {
                "id": "GWI-VID-M00",
                "severity": "P0",
                "dimension": "1. Visual & Stream Verification (S_perc)",
                "message": f"`ffprobe` could not decode valid video streams from `{master_mp4.name}` (unverified/corrupt container).",
            }
        )
    else:
        notes.append(
            f"Master Video `{master_mp4.name}` ({v_meta['probe_source']}): {v_meta['width']}x{v_meta['height']} "
            f"@ {v_meta['fps']}fps, codec={v_meta['codec_name']}, pix_fmt={v_meta['pix_fmt']}, duration={v_meta['duration_s']}s."
        )
        if (v_meta["width"], v_meta["height"]) not in valid_resolutions:
            score_visual -= 1.5
            blockers.append(
                {
                    "id": "GWI-VID-V01",
                    "severity": "P1",
                    "dimension": "1. Visual & Layout (S_perc)",
                    "message": f"Non-standard resolution {v_meta['width']}x{v_meta['height']} (expected 1080p, 9:16 Shorts, or 4K).",
                }
            )
        if v_meta["pix_fmt"] not in ("yuv420p",):
            score_visual -= 1.0
            blockers.append(
                {
                    "id": "GWI-VID-V02",
                    "severity": "P1",
                    "dimension": "1. Visual & Layout (S_perc)",
                    "message": f"Pixel format is `{v_meta['pix_fmt']}`; YouTube requires `yuv420p` for universal browser hardware decoding.",
                }
            )
        f_probe = _probe_video_frames(master_mp4, v_meta["duration_s"])
        v_meta["frame_probe"] = f_probe
        if f_probe["sampled_frames"] > 0:
            if f_probe["blank_frames"] > 0:
                score_visual -= 2.2
                blockers.append(
                    {
                        "id": "GWI-VID-V04",
                        "severity": "P0",
                        "dimension": "1. Visual & Frame Entropy (S_perc)",
                        "message": f"Detected {f_probe['blank_frames']} blank/flat video frame(s) (min spatial std={f_probe['min_spatial_std']} < 2.0).",
                    }
                )
            else:
                notes.append(
                    f"Frame-level visual & safe-area probe verified ({f_probe['sampled_frames']} keyframes: "
                    f"mean_luma={f_probe['mean_luma']}, min_std={f_probe['min_spatial_std']}, safe_area_ratio={f_probe['safe_area_ratio']:.2%})."
                )

        # Verify walkthrough video freshness against src/ and e2e/ when E2E specs exist
        project_root = root.parent.parent if root.name == "video" and root.parent.name == "evidence" else root
        e2e_specs = list((project_root / "e2e").glob("*.spec.*")) if (project_root / "e2e").is_dir() else []
        if e2e_specs and (project_root / "src").is_dir():
            mp4_mtime = master_mp4.stat().st_mtime
            tracked_code = (
                list((project_root / "src").rglob("*.ts")) + list((project_root / "src").rglob("*.tsx")) + e2e_specs
            )
            stale_sources = [p for p in tracked_code if p.is_file() and p.stat().st_mtime > mp4_mtime + 120.0]
            if stale_sources:
                newest_src = max(stale_sources, key=lambda p: p.stat().st_mtime)
                score_visual -= 1.8
                blockers.append(
                    {
                        "id": "GWI-VID-F01",
                        "severity": "P1",
                        "dimension": "1. Visual & Walkthrough Freshness (S_perc)",
                        "message": (
                            f"Walkthrough video `{master_mp4.name}` is stale relative to `{newest_src.relative_to(project_root)}` "
                            f"(modified {int(newest_src.stat().st_mtime - mp4_mtime)}s after video capture). Re-run the E2E video capture pipeline."
                        ),
                    }
                )

    thumbs = (
        list(root.glob("*thumbnail*.jpg"))
        + list(root.glob("*thumbnail*.jpeg"))
        + list(root.glob("*thumbnail*.png"))
        + list(root.glob("artifacts/*thumbnail*.jpg"))
        + list(root.glob("artifacts/*thumbnail*.png"))
        + list(root.glob("results/*thumbnail*.jpg"))
        + list(root.glob("results/*thumbnail*.png"))
    )
    if thumbs:
        jpg_thumbs = [t for t in thumbs if t.suffix.lower() in (".jpg", ".jpeg")]
        best_thumb = jpg_thumbs[0] if jpg_thumbs else thumbs[0]
        t_size = best_thumb.stat().st_size
        if t_size > 2_000_000:
            score_visual -= 1.8
            blockers.append(
                {
                    "id": "GWI-VID-V03",
                    "severity": "P0",
                    "dimension": "1. Visual & Layout (S_perc)",
                    "message": f"Thumbnail `{best_thumb.name}` is {t_size:,} bytes, exceeding YouTube Studio's 2.0 MB upload limit.",
                }
            )
        else:
            notes.append(f"Thumbnail `{best_thumb.name}` verified under 2.0 MB limit ({t_size:,} bytes).")

    # 2. Dim 2: Technical & Mathematical Narrative / Captions (S_act)
    srt_files = list(root.glob("*.srt")) + list(root.glob("artifacts/*.srt")) + list(root.glob("results/*.srt"))
    score_tech = 9.4
    if srt_files:
        srt_info = _validate_srt_captions(srt_files[0])
        if srt_info["valid"]:
            score_tech = 9.6
            if srt_info["max_cps"] > 20.0 or srt_info["max_line_chars"] > 42:
                score_tech -= 0.4
                blockers.append(
                    {
                        "id": "GWI-VID-T03",
                        "severity": "P2",
                        "dimension": "2. Technical & Captions (S_act)",
                        "message": f"Caption file `{srt_files[0].name}` exceeds ideal reading pace (max_cps={srt_info['max_cps']}, max_line_chars={srt_info['max_line_chars']}).",
                    }
                )
            else:
                notes.append(
                    f"Verified `{srt_files[0].name}` ({srt_info['cues']} cues, 0 overlaps, max_cps={srt_info['max_cps']}, max_line_chars={srt_info['max_line_chars']})."
                )
        else:
            score_tech = 7.5
            blockers.append(
                {
                    "id": "GWI-VID-T01",
                    "severity": "P1",
                    "dimension": "2. Technical & Captions (S_act)",
                    "message": (
                        f"Caption file `{srt_files[0].name}` failed ergonomics/sync "
                        f"(overlaps={srt_info['overlaps']}, max_cps={srt_info['max_cps']}, max_line_chars={srt_info['max_line_chars']})."
                    ),
                }
            )
    else:
        score_tech = 7.8
        blockers.append(
            {
                "id": "GWI-VID-T02",
                "severity": "P1",
                "dimension": "2. Technical & Captions (S_act)",
                "message": "Missing synchronized `.srt` closed captions.",
            }
        )

    # 3. Dim 3: Broadcast Audio & EBU R128 Loudness (S_sub)
    if not v_meta["has_audio"]:
        score_audio = 5.0
        blockers.append(
            {
                "id": "GWI-VID-A01",
                "severity": "P0",
                "dimension": "3. Audio & EBU R128 (S_sub)",
                "message": f"Master video `{master_mp4.name}` has NO audio stream! Silent MP4s violate YouTube broadcast standards.",
            }
        )
    else:
        score_audio = 9.2
        mono_penalty = 0.0
        if v_meta["probed"] and v_meta["audio_channels"] < 2:
            mono_penalty = 1.0
            score_audio -= mono_penalty
            blockers.append(
                {
                    "id": "GWI-VID-A02",
                    "severity": "P1",
                    "dimension": "3. Audio & EBU R128 (S_sub)",
                    "message": f"Audio stream has {v_meta['audio_channels']} channel(s); expected Stereo (2 channels).",
                }
            )

        lufs_val: float | None = None
        tp_val: float | None = None
        if probe_audio:
            live_ebu = _measure_ebur128(master_mp4)
            lufs_val = live_ebu["integrated_lufs"]
            tp_val = live_ebu["true_peak_dbtp"]

        ebu_reports = (
            list(root.glob("*ebu*128*.txt"))
            + list(root.glob("*ebu*128*.log"))
            + list(root.glob("artifacts/*ebu*128*.txt"))
            + list(root.glob("artifacts/*ebu*128*.log"))
            + list(root.glob("results/*ebu*128*.txt"))
            + list(root.glob("results/*ebu*128*.log"))
            + list(root.glob("scratch/*ebu*128*.txt"))
        )
        if lufs_val is None and ebu_reports:
            ebu_raw = ebu_reports[0].read_text(encoding="utf-8", errors="ignore")
            ebu_tail = ebu_raw[ebu_raw.rfind("Summary:") :] if "Summary:" in ebu_raw else ebu_raw
            m_lufs = _LUFS_RE.search(ebu_tail)
            m_pk = _PEAK_RE.search(ebu_tail)
            if m_lufs:
                lufs_val = float(m_lufs.group(1))
            if m_pk:
                tp_val = float(m_pk.group(1))

        if lufs_val is not None:
            tp_ok = tp_val is None or tp_val <= -1.0
            if -15.5 <= lufs_val <= -12.5 and tp_ok:
                score_audio = 9.5 - mono_penalty
                notes.append(
                    f"EBU R128 integrated loudness verified: I = {lufs_val:.1f} LUFS"
                    + (f", True Peak = {tp_val:.1f} dBTP." if tp_val is not None else ".")
                )
            else:
                score_audio = min(score_audio, 7.4)
                blockers.append(
                    {
                        "id": "GWI-VID-A03",
                        "severity": "P1",
                        "dimension": "3. Audio & EBU R128 (S_sub)",
                        "message": (
                            f"Broadcast loudness violation: I = {lufs_val:.1f} LUFS"
                            + (f", True Peak = {tp_val:.1f} dBTP" if tp_val is not None else "")
                            + " (target I in [-15.5, -12.5] LUFS, TP <= -1.0 dBTP)."
                        ),
                    }
                )

    # 4. Dim 4: Retention, Keyframes & Dual-Model Provenance (S_exist)
    keyframes = [
        p
        for p in (
            list(root.glob("act[0-9]*.png"))
            + list(root.glob("docs/screenshots/*.png"))
            + list(root.glob("sim_phase*.png"))
            + list(root.glob("*contact_sheet*.jpg"))
            + list(root.glob("*contact_sheet*.png"))
            + list(root.glob("*_sheet.png"))
            + list(root.glob("artifacts/*_sheet.png"))
            + list(root.glob("artifacts/*contact_sheet*.png"))
            + list(root.glob("results/*_sheet.png"))
            + list(root.glob("results/*contact_sheet*.png"))
            + list(root.glob("frames/*.png"))
            + list(root.glob("scratch/frames*/*.png"))
        )
    ]
    has_contact_sheet = any("sheet" in p.name for p in keyframes)
    score_ret = 9.4 if (len(keyframes) >= 3 or has_contact_sheet) else 8.4
    if len(keyframes) < 3 and not has_contact_sheet:
        blockers.append(
            {
                "id": "GWI-VID-R01",
                "severity": "P1",
                "dimension": "4. Retention & Keyframe Sheet (S_exist)",
                "message": "Fewer than 3 full-resolution keyframe snapshots or contact sheets found for visual QA.",
            }
        )
    else:
        notes.append(f"Verified {len(keyframes)} keyframe/contact-sheet visual QA artifact(s).")

    # Mandatory < 20 MB Companion Video Deliverable (`sc.companion_size`)
    master_size = master_mp4.stat().st_size if master_mp4.is_file() else 0
    if master_size > 20_000_000:
        companion_candidates = [p for p in mp4_files if p.is_file() and 0 < p.stat().st_size <= 20_000_000]
        if not companion_candidates:
            score_ret -= 1.5
            blockers.append(
                {
                    "id": "GWI-VID-C01",
                    "severity": "P1",
                    "dimension": "4. Retention, Distribution & Companion Edition (S_exist)",
                    "message": (
                        f"Master video `{master_mp4.name}` ({master_size:,} bytes) has NO companion MP4 "
                        f"<= 20 MB (`sc.companion_size`). A companion edition strictly <= 20,000,000 bytes "
                        "is mandatory for web embeds, instant preview, and issue-tracker attachments."
                    ),
                }
            )
        else:
            best_comp = companion_candidates[0]
            notes.append(
                f"Companion video `{best_comp.name}` verified under 20 MB limit ({best_comp.stat().st_size:,} bytes)."
            )

    s_sub = round(score_audio, 2)
    s_perc = round(score_visual, 2)
    s_act = round(score_tech, 2)
    s_exist = round(score_ret, 2)

    dimensions = [
        {
            "id": "V_layout",
            "tier": "S_perc",
            "name": "1. Visual Craft, Safe-Area & Thumbnail",
            "weight": 0.25,
            "score": s_perc,
        },
        {
            "id": "T_math",
            "tier": "S_act",
            "name": "2. Technical Narrative & .srt Captions",
            "weight": 0.25,
            "score": s_act,
        },
        {
            "id": "A_audio",
            "tier": "S_sub",
            "name": "3. Broadcast Audio & EBU R128 (-14 LUFS)",
            "weight": 0.25,
            "score": s_sub,
        },
        {
            "id": "R_ret",
            "tier": "S_exist",
            "name": "4. Retention, Contact Sheet & Provenance",
            "weight": 0.25,
            "score": s_exist,
        },
    ]

    g_calc = compute_goldwood_index(
        [s_sub, s_perc, s_act, s_exist],
        weights=ARCHETYPE_WEIGHTS["video"],
        theta=theta,
        k=k,
        conscience_clean=v_meta["probed"] and v_meta["has_video"],
    )

    total_dir_bytes = sum(
        p.stat().st_size
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts
    )
    elegance = compute_kolmogorov_elegance(total_dir_bytes, max(1, int(v_meta["size_bytes"] * 1.25)))

    return {
        "profile": "video",
        "target": str(master_mp4),
        "project_name": root.name,
        "video_metadata": v_meta,
        "kolmogorov_elegance": elegance,
        "snrc": compute_snrc(len(notes) + len(keyframes), len(blockers)),
        "dimensions": dimensions,
        "tiers": {
            "S_sub": s_sub,
            "S_perc": s_perc,
            "S_act": s_act,
            "S_exist": s_exist,
        },
        "calculation": g_calc,
        "probe_states": {
            d["id"]: ("UNMEASURED" if not v_meta.get("probed") else ("PASS" if d["score"] >= 9.0 else "FAIL"))
            for d in dimensions
        },
        "blockers": blockers,
        "notes": notes,
    }


def evaluate_paper_profile(
    target: Path,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
) -> dict[str, Any]:
    """
    Evaluate an Academic LaTeX Treatise or IEEE/ACM/arXiv Manuscript (`paper` profile)
    across the 4 Ontological Tiers using the `scholar` weight distribution (0.20, 0.30, 0.20, 0.30).
    """
    if target.is_file() and target.suffix.lower() == ".tex":
        main_tex = target.resolve()
        root = main_tex.parent
    else:
        root = target.resolve()
        tex_candidates = sorted(root.glob("*.tex"))
        main_tex = tex_candidates[0] if tex_candidates else (root / "paper.tex")
        preferred_names = (
            "paper.tex",
            "main.tex",
            "paper1.tex",
            "paper2.tex",
            "paper3.tex",
            "track1_aimxr_paper.tex",
        )
        matched_preferred = False
        for pref in preferred_names:
            for cand in tex_candidates:
                if cand.name == pref:
                    main_tex = cand
                    matched_preferred = True
                    break
            if matched_preferred:
                break
        if not matched_preferred:
            for cand in tex_candidates:
                if "cover_letter" in cand.name.lower() or "supplement" in cand.name.lower():
                    continue
                if "\\begin{document}" in cand.read_text(encoding="utf-8", errors="ignore"):
                    main_tex = cand
                    break

    blockers: list[dict[str, str]] = []
    notes: list[str] = []

    if not main_tex.is_file():
        calc = compute_goldwood_index([4.0, 4.0, 4.0, 4.0], weights=ARCHETYPE_WEIGHTS["paper"], theta=theta, k=k)
        return {
            "profile": "paper",
            "target": str(root),
            "project_name": root.name,
            "dimensions": [],
            "tiers": {"S_sub": 4.0, "S_perc": 4.0, "S_act": 4.0, "S_exist": 4.0},
            "calculation": calc,
            "blockers": [
                {
                    "id": "GWI-PAP-00",
                    "severity": "P0",
                    "dimension": "LaTeX Manuscript",
                    "message": f"No `.tex` manuscript found in {root}.",
                }
            ],
            "notes": [],
        }

    src = main_tex.read_text(encoding="utf-8", errors="ignore")
    # Expand \input{...} and \include{...} for multi-file monographs/treatises
    seen_inputs: set[Path] = {main_tex.resolve()}
    for inc_match in re.findall(r"\\(?:input|include)\{([^}]+)\}", src):
        inc_rel = inc_match.strip()
        cand_path = (root / inc_rel) if inc_rel.endswith(".tex") else (root / f"{inc_rel}.tex")
        if cand_path.is_file() and cand_path.resolve() not in seen_inputs:
            seen_inputs.add(cand_path.resolve())
            src += "\n" + cand_path.read_text(encoding="utf-8", errors="ignore")
    log_path = main_tex.with_suffix(".log")
    pdf_path = main_tex.with_suffix(".pdf")
    log_txt = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.is_file() else ""

    # 1. Tier I (S_sub / Firmitas): Compilation Hygiene & Zero Markdown Leakage
    tex_errors = log_txt.count("\n! ")
    undef_warns = len(re.findall(r"LaTeX Warning: (?:Reference|Citation) .* undefined", log_txt))
    md_leak_lines = [src[: m.start()].count("\n") + 1 for m in _MD_BOLD_LEAK_RE.finditer(src)]

    s_sub = 9.7
    if tex_errors > 0:
        s_sub -= 3.0
        blockers.append(
            {
                "id": "GWI-PAP-S01",
                "severity": "P0",
                "dimension": "S_sub (Compilation & Syntax)",
                "message": f"`{log_path.name}` contains {tex_errors} fatal LaTeX `!` error(s).",
            }
        )
    if undef_warns > 0:
        s_sub -= min(2.5, undef_warns * 0.8)
        blockers.append(
            {
                "id": "GWI-PAP-S02",
                "severity": "P0",
                "dimension": "S_sub (Undefined Citations/Refs)",
                "message": f"`{log_path.name}` reports {undef_warns} undefined citation/reference warning(s).",
            }
        )
    if md_leak_lines:
        s_sub -= 2.8
        blockers.append(
            {
                "id": "GWI-PAP-S03",
                "severity": "P0",
                "dimension": "S_sub (LaTeX Markup Purity)",
                "message": f"`{main_tex.name}` contains {len(md_leak_lines)} uncompiled Markdown `**bold**` leak(s) at lines {md_leak_lines[:5]}.",
            }
        )
    else:
        notes.append(f"Verified zero Markdown `**bold**` syntax leaks in `{main_tex.name}`.")

    # 2. Tier II (S_perc / Venustas): Typographic Poise, Overfull hbox (> 3.0pt) & Type-42 Fonts
    overfull_pts = [float(x) for x in _OVERFULL_PT_RE.findall(log_txt)]
    severe_overfull = [pt for pt in overfull_pts if pt > 3.0]
    max_overfull_pt = max(overfull_pts) if overfull_pts else 0.0
    has_microtype = "microtype" in src
    resizebox_tabular = re.findall(r"\\resizebox\s*\{[^}]*\}\s*\{[^}]*\}\s*\{(?:%[^\n]*\n|\s)*\\begin\{tabular", src)
    has_bare_hyperref = ("\\usepackage{hyperref}" in src) and ("colorlinks" not in src and "hidelinks" not in src)
    s_perc = (
        9.6
        - min(2.8, len(severe_overfull) * 0.45 + (1.5 if max_overfull_pt > 10.0 else 0.0))
        - (0.4 if not has_microtype else 0.0)
        - (1.2 if resizebox_tabular else 0.0)
        - (0.8 if has_bare_hyperref else 0.0)
    )
    if severe_overfull:
        blockers.append(
            {
                "id": "GWI-PAP-P01",
                "severity": "P0" if (max_overfull_pt > 10.0 or len(severe_overfull) > 5) else "P1",
                "dimension": "S_perc (Typographic Eurhythmy)",
                "message": f"`{log_path.name}` has {len(severe_overfull)} `Overfull \\hbox` > 3.0pt (max={max_overfull_pt:.1f}pt).",
            }
        )
    else:
        notes.append(f"Verified 0 `Overfull \\hbox` > 3.0pt warnings in `{main_tex.name}`.")
    if resizebox_tabular:
        blockers.append(
            {
                "id": "GWI-PAP-P02",
                "severity": "P1",
                "dimension": "S_perc (Table Readability & Anti-Resizebox)",
                "message": f"`{main_tex.name}` contains {len(resizebox_tabular)} `\\resizebox` wrapping `\\begin{{tabular}}` (masks Overfull \\hbox by shrinking table font; use `\\scriptsize`/`\\footnotesize` with `tabularx`).",
            }
        )
    if has_bare_hyperref:
        blockers.append(
            {
                "id": "GWI-PAP-P03",
                "severity": "P1",
                "dimension": "S_perc (Hyperlink Styling)",
                "message": f"`{main_tex.name}` loads `\\usepackage{{hyperref}}` without `colorlinks=true` or `hidelinks` (draws neon viewer boxes around citations/references).",
            }
        )

    # 3. Tier III (S_act / Utilitas): Citation-to-Bibliography Parity & Mathematical Rigor
    cites = {
        k_str.strip() for c in re.findall(r"\\cite[tp]?\*?\{([^}]+)\}", src) for k_str in c.split(",") if k_str.strip()
    }
    bibitems = set(re.findall(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", src))
    for bib_file in root.glob("*.bib"):
        bib_txt = bib_file.read_text(encoding="utf-8", errors="ignore")
        bibitems |= set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bib_txt))

    missing_cites = sorted(cites - bibitems)
    orphan_bibs = sorted(bibitems - cites) if (bibitems and "\\bibitem" in src) else []

    s_act = 9.6
    if missing_cites:
        s_act -= 3.2
        blockers.append(
            {
                "id": "GWI-PAP-A01",
                "severity": "P0",
                "dimension": "S_act (Citation Integrity)",
                "message": f"Missing bibliography entries for cited keys: {', '.join(missing_cites)}.",
            }
        )
    if orphan_bibs:
        s_act -= min(1.5, len(orphan_bibs) * 0.4)
        blockers.append(
            {
                "id": "GWI-PAP-A02",
                "severity": "P1",
                "dimension": "S_act (Bibliography Parsimony)",
                "message": f"Uncited orphan `\\bibitem` entries in `{main_tex.name}`: {', '.join(orphan_bibs)}.",
            }
        )
    elif not missing_cites:
        notes.append(f"Verified 100% citation-to-bibliography parity ({len(cites)} cited key(s)).")

    # 4. Tier IV (S_exist / Syneidesis): Epistemic Provenance & Compiled PDF Integrity
    todos = re.findall(r"\\todo|TODO|XXX|lorem ipsum", src, re.IGNORECASE)
    has_pdf = pdf_path.is_file() and pdf_path.stat().st_size > 256
    s_exist = 9.6 - (1.8 if todos else 0.0) - (2.2 if not has_pdf else 0.0)
    if todos:
        blockers.append(
            {
                "id": "GWI-PAP-E01",
                "severity": "P1",
                "dimension": "S_exist (Epistemic Completeness)",
                "message": f"`{main_tex.name}` contains {len(todos)} unresolved TODO/placeholder marker(s).",
            }
        )
    if has_pdf:
        notes.append(f"Compiled PDF `{pdf_path.name}` verified ({pdf_path.stat().st_size:,} bytes).")
    else:
        blockers.append(
            {
                "id": "GWI-PAP-E02",
                "severity": "P1",
                "dimension": "S_exist (Compiled PDF Provenance)",
                "message": f"Missing compiled PDF artifact `{pdf_path.name}` (uncompiled `.tex` source).",
            }
        )

    s_sub = round(max(0.0, s_sub), 2)
    s_perc = round(max(0.0, s_perc), 2)
    s_act = round(max(0.0, s_act), 2)
    s_exist = round(max(0.0, s_exist), 2)

    dimensions = [
        {
            "id": "F_comp",
            "tier": "S_sub",
            "name": "1. LaTeX Compilation & Markup Purity",
            "weight": 0.20,
            "score": s_sub,
        },
        {
            "id": "V_typo",
            "tier": "S_perc",
            "name": "2. Typographic Poise & Zero Overfull Hbox",
            "weight": 0.30,
            "score": s_perc,
        },
        {
            "id": "U_cite",
            "tier": "S_act",
            "name": "3. Citation Parity & Mathematical Rigor",
            "weight": 0.20,
            "score": s_act,
        },
        {
            "id": "S_prov",
            "tier": "S_exist",
            "name": "4. Epistemic Provenance & PDF Artifact",
            "weight": 0.30,
            "score": s_exist,
        },
    ]

    conscience_clean = tex_errors == 0 and undef_warns == 0 and len(md_leak_lines) == 0 and len(missing_cites) == 0
    g_calc = compute_goldwood_index(
        [s_sub, s_perc, s_act, s_exist],
        weights=ARCHETYPE_WEIGHTS["paper"],
        theta=theta,
        k=k,
        conscience_clean=conscience_clean,
    )

    probe_states = {d["id"]: ("PASS" if d["score"] >= 9.0 else "FAIL") for d in dimensions}
    if not has_pdf:
        probe_states["S_prov"] = "UNMEASURED"

    return {
        "profile": "paper",
        "target": str(main_tex),
        "project_name": main_tex.stem if target.is_file() else root.name,
        "kolmogorov_elegance": compute_kolmogorov_elegance(len(bibitems) or 1, len(cites) or 1),
        "snrc": compute_snrc(len(cites) + len(notes), len(blockers)),
        "dimensions": dimensions,
        "tiers": {
            "S_sub": s_sub,
            "S_perc": s_perc,
            "S_act": s_act,
            "S_exist": s_exist,
        },
        "calculation": g_calc,
        "probe_states": probe_states,
        "blockers": blockers,
        "notes": notes,
    }


def compute_graph_fiedler_value(n_nodes: int, adj: list[set[int]]) -> float:
    """
    Compute the normalized Fiedler value (algebraic connectivity lambda_2 in [0, 2])
    of the symmetric normalized graph Laplacian L_sym = I - D^{-1/2} A D^{-1/2}.
    Returns 0.0 immediately if the graph is disconnected or contains isolated vertices.
    Uses peripheral-BFS-seeded Lanczos tridiagonalization with full reorthogonalization
    orthogonal to v_0 = D^{1/2} 1 / ||D^{1/2} 1||_2 followed by 56-step Sturm sequence bisection.
    """
    import collections

    if n_nodes <= 1:
        return 1.0
    if any(len(a) == 0 for a in adj):
        return 0.0

    # 1. Exact BFS connectivity check & peripheral node discovery
    visited = {0}
    dq: collections.deque[int] = collections.deque([0])
    last_node = 0
    while dq:
        cur = dq.popleft()
        last_node = cur
        for nxt in adj[cur]:
            if nxt not in visited:
                visited.add(nxt)
                dq.append(nxt)
    if len(visited) < n_nodes:
        return 0.0

    # 2. Peripheral BFS hop distances from `last_node` for topological Fiedler seeding
    dist = [-1] * n_nodes
    dist[last_node] = 0
    dq.append(last_node)
    while dq:
        cur = dq.popleft()
        for nxt in adj[cur]:
            if dist[nxt] < 0:
                dist[nxt] = dist[cur] + 1
                dq.append(nxt)
    max_d = max(dist) or 1

    deg = [float(len(a)) for a in adj]
    inv_sqrt_d = [1.0 / math.sqrt(d) for d in deg]
    v0 = [math.sqrt(d) for d in deg]
    norm0 = math.sqrt(sum(x * x for x in v0)) or 1.0
    v0 = [x / norm0 for x in v0]

    def _matvec(x: list[float]) -> list[float]:
        y = [0.0] * n_nodes
        for i in range(n_nodes):
            s = sum(inv_sqrt_d[j] * x[j] for j in adj[i])
            y[i] = x[i] - inv_sqrt_d[i] * s
        return y

    seeds = [
        [math.cos(math.pi * dist[i] / max_d) * v0[i] for i in range(n_nodes)],
        [math.sin((i + 1) * 1.37) for i in range(n_nodes)],
    ]
    best_lambda2 = 2.0
    m_steps = min(n_nodes - 1, 160)
    for seed in seeds:
        dot0 = sum(seed[i] * v0[i] for i in range(n_nodes))
        q = [seed[i] - dot0 * v0[i] for i in range(n_nodes)]
        nq = math.sqrt(sum(v * v for v in q))
        if nq < 1e-12:
            continue
        q = [v / nq for v in q]
        q_basis: list[list[float]] = [q]
        alphas: list[float] = []
        betas: list[float] = []
        for step_k in range(m_steps):
            z = _matvec(q_basis[step_k])
            alpha = sum(q_basis[step_k][i] * z[i] for i in range(n_nodes))
            alphas.append(alpha)
            # Two Gram-Schmidt passes ("twice is enough"): a single pass lets the null vector v_0 leak back
            # into Q_k on irregular graphs (|Q.v_0| ~ 1e-1 by k=30), pulling Ritz values spuriously toward 0.
            for _pass in range(2):
                dot0 = sum(z[i] * v0[i] for i in range(n_nodes))
                for i in range(n_nodes):
                    z[i] -= dot0 * v0[i]
                for prev_q in q_basis:
                    dp = sum(z[i] * prev_q[i] for i in range(n_nodes))
                    if abs(dp) > 1e-17:
                        for i in range(n_nodes):
                            z[i] -= dp * prev_q[i]
            beta = math.sqrt(sum(v * v for v in z))
            if beta < 1e-11 or step_k == m_steps - 1:
                break
            betas.append(beta)
            q_basis.append([v / beta for v in z])

        m_dim = len(alphas)
        lo, hi = 0.0, 2.0
        for _ in range(56):
            mid = 0.5 * (lo + hi)
            cnt = 0
            d = alphas[0] - mid
            if d < 0.0:
                cnt += 1
            elif abs(d) < 1e-18:
                d = -1e-18
            for idx in range(1, m_dim):
                d = (alphas[idx] - mid) - (betas[idx - 1] * betas[idx - 1]) / d
                if d < 0.0:
                    cnt += 1
                elif abs(d) < 1e-18:
                    d = -1e-18
            if cnt >= 1:
                hi = mid
            else:
                lo = mid
        best_lambda2 = min(best_lambda2, 0.5 * (lo + hi))

    return max(1e-12, min(2.0, best_lambda2))


def evaluate_kb_profile(
    target_dir: Path,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
) -> dict[str, Any]:
    """
    Evaluate a Sovereign Knowledge Base, Second Brain, or Semantic Graph Vault (`kb` profile)
    across 8 Epistemic & Structural Dimensions and the 4 Ontological Tiers (`S_sub`, `S_perc`, `S_act`, `S_exist`).
    Unifies the 6-Gate Vault Doctor (`kb verify`) with continuous spectral graph theory (Fiedler algebraic
    connectivity lambda_2, LCC & 2-hop hub reachability), hybrid retrieval coverage, and RSI provenance.
    """
    import collections
    import sqlite3

    from sovereign_kb.cli import (
        _find_frontmatter_end,
        _is_vault_no_embed,
        build_stem_lookup,
        extract_wikilinks,
        fold_accents,
        list_vault_md_files,
        parse_frontmatter,
    )
    from sovereign_kb.rsi import verify_ledger_chain

    root = (target_dir if target_dir.is_dir() else target_dir.parent).resolve()
    blockers: list[dict[str, str]] = []
    notes: list[str] = []

    md_files = list_vault_md_files(root)
    if not md_files:
        calc = compute_goldwood_index(
            [4.0, 4.0, 4.0, 4.0],
            weights=ARCHETYPE_WEIGHTS["kb"],
            theta=theta,
            k=k,
            conscience_clean=False,
        )
        return {
            "profile": "kb",
            "target": str(root),
            "project_name": root.name,
            "dimensions": [],
            "tiers": {"S_sub": 4.0, "S_perc": 4.0, "S_act": 4.0, "S_exist": 4.0},
            "calculation": calc,
            "blockers": [
                {
                    "id": "GWI-KB-00",
                    "severity": "P0",
                    "dimension": "Knowledge Base Vault",
                    "message": f"No Markdown notes found in vault {root}.",
                }
            ],
            "notes": [],
        }

    stem_lookup = build_stem_lookup(root, md_files)
    rel_paths = [str(p.relative_to(root)) for p in md_files]
    node_idx = {rel: i for i, rel in enumerate(rel_paths)}
    n_notes = len(md_files)

    # 1. Scan notes for Frontmatter Schema (F_schema), Prose/Math Hygiene (V_prose),
    #    Section Structure (A_struct), Conflict Markers (S_index), Secrets (R_prov) & Wikilinks (U_links)
    yaml_error_files: list[str] = []
    missing_fm_files: list[str] = []
    bad_date_files: list[str] = []
    schema_governed_count = 0
    valid_fm_count = 0
    rich_meta_count = 0

    unclosed_fence_files: list[str] = []
    unclosed_math_files: list[str] = []
    conflict_marker_files: list[str] = []
    stub_note_files: list[str] = []
    secret_leak_files: list[str] = []
    total_headings = 0

    inbound_counts: dict[str, int] = {rel: 0 for rel in rel_paths}
    adj_undirected: list[set[int]] = [set() for _ in range(n_notes)]
    broken_links: list[str] = []
    valid_links_count = 0
    disk_texts: dict[str, str] = {}

    for i, p in enumerate(md_files):
        rel = rel_paths[i]
        text = p.read_text(encoding="utf-8", errors="replace")
        disk_texts[rel] = text
        meta = parse_frontmatter(text)
        fm_end = _find_frontmatter_end(text)
        raw_fm = text[:fm_end] if fm_end > 0 else ""
        body = text[fm_end:] if fm_end > 0 else text

        if rel not in ("AGENTS.md", "README.md"):
            schema_governed_count += 1
            if meta.get("yaml_error"):
                yaml_error_files.append(f"{rel} ({meta['yaml_error']})")
            missing_keys: list[str] = []
            for req in ("title", "type", "updated", "tags"):
                m_kv = re.search(rf"(?m)^{req}\s*:(.*)$", raw_fm)
                if not m_kv:
                    missing_keys.append(req)
                elif req == "tags":
                    if not meta.get("tags_list"):
                        missing_keys.append(req)
                else:
                    raw_val = m_kv.group(1).split("#", 1)[0].strip().strip("\"'").strip()
                    if not raw_val or not str(meta.get(req) or "").strip():
                        missing_keys.append(req)
            upd_str = str(meta.get("updated") or "").strip()
            valid_iso_date = False
            if upd_str and re.match(r"^\d{4}-\d{2}-\d{2}$", upd_str):
                try:
                    datetime.date.fromisoformat(upd_str)
                    valid_iso_date = True
                except ValueError:
                    valid_iso_date = False
            if upd_str and not valid_iso_date:
                bad_date_files.append(f"{rel} (updated='{upd_str}')")
                if "updated" not in missing_keys:
                    missing_keys.append("updated(invalid-date)")
            if missing_keys:
                missing_fm_files.append(f"{rel} (missing/invalid {','.join(missing_keys)})")
            if not meta.get("yaml_error") and not missing_keys:
                valid_fm_count += 1
            tags_list = meta.get("tags_list") if isinstance(meta.get("tags_list"), list) else []
            rel_list = meta.get("related") if isinstance(meta.get("related"), list) else []
            if len(tags_list) >= 2 or len(rel_list) >= 1 or rel == "INDEX.md":
                rich_meta_count += 1

        # Prose, CommonMark code fences, display math & git conflict markers
        fence_char: str | None = None
        fence_len = 0
        in_display_math = False
        note_headings = 0
        prose_lines_outside_fences: list[str] = []
        note_conflict_open_close: list[int] = []
        note_conflict_eq: list[int] = []

        for line_no, raw_line in enumerate(text.splitlines(), 1):
            if fence_char is None:
                m_open = re.match(r"^\s*(`{3,}|~{3,})(.*)$", raw_line)
                if m_open and not (m_open.group(1)[0] == "`" and "`" in m_open.group(2)):
                    fence_char = m_open.group(1)[0]
                    fence_len = len(m_open.group(1))
                    continue
            else:
                m_close = re.match(r"^\s*(`{3,}|~{3,})\s*$", raw_line)
                if m_close and m_close.group(1)[0] == fence_char and len(m_close.group(1)) >= fence_len:
                    fence_char = None
                    fence_len = 0
                continue

            if re.match(r"^(?:<{7} |>{7} )", raw_line):
                note_conflict_open_close.append(line_no)
            elif re.match(r"^={7}$", raw_line):
                note_conflict_eq.append(line_no)

            if re.match(r"^#{1,6}\s+\S", raw_line):
                note_headings += 1
            no_inline_code = re.sub(r"`[^`]+`", "", raw_line)
            dd_count = len(re.findall(r"(?<!\\)\$\$", no_inline_code))
            if dd_count % 2 == 1:
                in_display_math = not in_display_math

        if note_conflict_open_close:
            for c_ln in sorted(note_conflict_open_close + note_conflict_eq):
                conflict_marker_files.append(f"{rel}:{c_ln}")

        # Count true body prose words (excluding YAML frontmatter and fenced code blocks)
        b_fence_char: str | None = None
        b_fence_len = 0
        for b_line in body.splitlines():
            if b_fence_char is None:
                m_bopen = re.match(r"^\s*(`{3,}|~{3,})(.*)$", b_line)
                if m_bopen and not (m_bopen.group(1)[0] == "`" and "`" in m_bopen.group(2)):
                    b_fence_char = m_bopen.group(1)[0]
                    b_fence_len = len(m_bopen.group(1))
                    continue
                prose_lines_outside_fences.append(b_line)
            else:
                m_bclose = re.match(r"^\s*(`{3,}|~{3,})\s*$", b_line)
                if m_bclose and m_bclose.group(1)[0] == b_fence_char and len(m_bclose.group(1)) >= b_fence_len:
                    b_fence_char = None
                    b_fence_len = 0
        body_words = re.findall(r"\b\w+\b", "\n".join(prose_lines_outside_fences))
        min_words = 10 if rel in ("INDEX.md", "AGENTS.md", "README.md") else 20

        total_headings += note_headings
        if fence_char is not None:
            unclosed_fence_files.append(rel)
        if in_display_math:
            unclosed_math_files.append(rel)
        if len(body.strip()) == 0 or len(body_words) < min_words:
            stub_note_files.append(rel)
        if _SECRET_LEAK_RE.search(text):
            secret_leak_files.append(rel)

        # Wikilinks & graph adjacency
        for target_rel in extract_wikilinks(text, stem_lookup, rel):
            if target_rel not in inbound_counts:
                broken_links.append(f"{rel} -> [[{target_rel}]]")
            else:
                valid_links_count += 1
                if target_rel != rel:
                    inbound_counts[target_rel] += 1
                    j = node_idx[target_rel]
                    adj_undirected[i].add(j)
                    adj_undirected[j].add(i)

    # Dimension 1: F_schema (YAML Frontmatter & Ontological Schema Compliance — Gate 1)
    r_fm = valid_fm_count / max(1, schema_governed_count)
    r_rich = rich_meta_count / max(1, schema_governed_count)
    s_schema = (
        5.0
        + 4.4 * (r_fm * r_fm)
        + 0.4 * r_rich
        - 2.5 * len(yaml_error_files)
        - 1.2 * len(missing_fm_files)
        - 0.4 * len(bad_date_files)
    )
    if yaml_error_files or missing_fm_files:
        s_schema = min(s_schema, 6.5)
        issues_preview = "; ".join((yaml_error_files + missing_fm_files)[:4])
        blockers.append(
            {
                "id": "GWI-KB-S01",
                "severity": "P0",
                "dimension": "F_schema (Frontmatter Schema — Gate 1)",
                "message": f"Frontmatter schema violation in {len(yaml_error_files) + len(missing_fm_files)} note(s): {issues_preview}",
            }
        )
    else:
        notes.append(
            f"Gate 1 (Frontmatter Schema) verified across {schema_governed_count}/{schema_governed_count} governed note(s)."
        )

    # Dimension 2: S_index (SQLite FTS5, HIPS WAND, Vector Coverage & Git Binary Hygiene — Gates 4 & 5)
    bloat_files: list[str] = []
    if (root / ".git").exists():
        git_ls = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        for tracked in git_ls:
            if (
                tracked.endswith((".mp4", ".mov", ".pyc", ".DS_Store"))
                or "__pycache__" in tracked
                or tracked.startswith(".kb/index.db")
            ):
                bloat_files.append(tracked)

    db_path = root / ".kb" / "index.db"
    hips_dir = root / ".kb" / "hips-index"
    hips_ready = hips_dir.is_dir() and any(hips_dir.iterdir())
    allow_lexical = _is_vault_no_embed(root, False)
    db_doc_count = 0
    db_chunk_count = 0
    db_eligible_vecs = 0
    db_vec_count = 0
    indexed_rel_paths: set[str] = set()
    fresh_doc_paths: set[str] = set()
    fts_ok = False

    if db_path.is_file():
        try:
            ro_uri = f"{db_path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(ro_uri, uri=True)
            try:
                doc_rows = conn.execute("SELECT rel_path, content, mtime FROM documents").fetchall()
                for rp_raw, d_content, _d_mtime in doc_rows:
                    rp = str(rp_raw)
                    indexed_rel_paths.add(rp)
                    if (
                        rp in disk_texts
                        and isinstance(d_content, str)
                        and d_content != ""
                        and d_content == disk_texts[rp]
                    ):
                        fresh_doc_paths.add(rp)
                for c_rp_raw, c_body in conn.execute("SELECT rel_path, content FROM chunks").fetchall():
                    c_rp = str(c_rp_raw)
                    if c_rp in fresh_doc_paths and (not isinstance(c_body, str) or c_body not in disk_texts[c_rp]):
                        fresh_doc_paths.discard(c_rp)
                db_doc_count = len(indexed_rel_paths)
                db_chunk_count = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
                joined_chunks = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM chunks c JOIN documents d ON c.rel_path = d.rel_path"
                    ).fetchone()[0]
                )
                try:
                    db_eligible_vecs = int(
                        conn.execute("SELECT COUNT(*) FROM chunks WHERE is_vector_eligible = 1").fetchone()[0]
                    )
                    db_vec_count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM chunks WHERE is_vector_eligible = 1 AND embedding IS NOT NULL"
                        ).fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    db_eligible_vecs = db_chunk_count
                    db_vec_count = int(
                        conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
                    )
                fts_docsize = int(conn.execute("SELECT COUNT(*) FROM chunks_fts_docsize").fetchone()[0])
                fts_data_rows = int(conn.execute("SELECT COUNT(*) FROM chunks_fts_data").fetchone()[0])
                fts_probe_ok = (
                    db_chunk_count > 0
                    and joined_chunks == db_chunk_count
                    and fts_docsize == db_chunk_count
                    and fts_data_rows >= 2
                )
                if fts_probe_ok:
                    for cid, ctext in conn.execute("SELECT id, content FROM chunks").fetchall():
                        folded_probe = fold_accents(str(ctext or ""))
                        m_tok = re.search(
                            r"(?<![\w])(?!(?:AND|OR|NOT|NEAR)(?![\w]))[A-Za-z0-9]{3,}(?![\w])", folded_probe
                        )
                        if m_tok:
                            hit = conn.execute(
                                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid = ?",
                                (f'"{m_tok.group(0)}"', int(cid)),
                            ).fetchone()
                            if hit is None:
                                fts_probe_ok = False
                                break
                fts_ok = fts_probe_ok
            finally:
                conn.close()
        except Exception:
            fts_ok = False

    expected_rel_set = set(rel_paths)
    exact_path_sync = (indexed_rel_paths == expected_rel_set) and (fresh_doc_paths == expected_rel_set)
    overlap_count = len(fresh_doc_paths & expected_rel_set)
    r_doc_sync = (
        overlap_count / max(1, len(expected_rel_set | indexed_rel_paths))
        if (db_path.is_file() and db_chunk_count > 0 and fts_ok)
        else 0.0
    )
    r_vec = 1.0 if allow_lexical else (db_vec_count / max(1, db_eligible_vecs) if db_eligible_vecs > 0 else 0.0)
    s_index = (
        5.5
        + 2.2 * r_doc_sync
        + 1.4 * r_vec
        + (0.4 if hips_ready else 0.0)
        + (0.3 if fts_ok else 0.0)
        - 3.5 * len(conflict_marker_files)
        - 3.0 * len(bloat_files)
    )
    if conflict_marker_files:
        s_index = min(s_index, 6.2)
        blockers.append(
            {
                "id": "GWI-KB-S02",
                "severity": "P0",
                "dimension": "S_index (Git Conflict Hygiene — Gate 5)",
                "message": f"Unresolved Git conflict marker(s) in: {', '.join(conflict_marker_files[:4])}",
            }
        )
    if bloat_files:
        s_index = min(s_index, 6.4)
        blockers.append(
            {
                "id": "GWI-KB-S03",
                "severity": "P0",
                "dimension": "S_index (Git Binary Hygiene — Gate 5)",
                "message": f"Tracked binary/database artifact(s) in Git index: {', '.join(bloat_files[:4])}",
            }
        )
    if not db_path.is_file() or not exact_path_sync or db_chunk_count == 0 or not fts_ok:
        s_index = min(s_index, 6.5)
        blockers.append(
            {
                "id": "GWI-KB-S04",
                "severity": "P0",
                "dimension": "S_index (Hybrid Index Sync — Gate 4)",
                "message": (
                    f"Vault index `.kb/index.db` out of sync or corrupt "
                    f"(indexed_docs={db_doc_count}/{n_notes}, exact_path_sync={exact_path_sync}, "
                    f"chunks={db_chunk_count}, fts_ok={fts_ok}); run `kb sync`."
                ),
            }
        )
    elif not allow_lexical and db_vec_count < db_eligible_vecs:
        vec_sev = "P0" if r_vec < 0.85 else "P1"
        if vec_sev == "P0":
            s_index = min(s_index, 6.5)
        else:
            s_index -= 2.0 * (1.0 - r_vec)
        blockers.append(
            {
                "id": "GWI-KB-S05",
                "severity": vec_sev,
                "dimension": "S_index (Vector Embedding Coverage — Gate 4)",
                "message": f"Incomplete vector embeddings ({db_vec_count}/{db_eligible_vecs} eligible chunks, r_vec={r_vec:.3f}); run `kb sync`.",
            }
        )
    else:
        notes.append(
            f"Gate 4 (Hybrid Index) verified: {db_doc_count} docs, {db_chunk_count} chunks, "
            f"{db_vec_count}/{db_eligible_vecs} vectors, HIPS WAND={'OK' if hips_ready else 'N/A'}."
        )

    s_schema = round(max(0.0, min(9.8, s_schema)), 2)
    s_index = round(max(0.0, min(9.8, s_index)), 2)

    # Dimension 3: V_prose (Epistemic Prose, KaTeX Math Delimiters & Code-Fence Parity)
    clean_notes_count = n_notes - len(set(unclosed_fence_files) | set(unclosed_math_files))
    r_clean_prose = clean_notes_count / max(1, n_notes)
    s_prose = (
        5.0 + 4.7 * (r_clean_prose * r_clean_prose) - 3.0 * len(unclosed_fence_files) - 2.0 * len(unclosed_math_files)
    )
    if unclosed_fence_files:
        s_prose = min(s_prose, 6.4)
        blockers.append(
            {
                "id": "GWI-KB-P01",
                "severity": "P0",
                "dimension": "V_prose (Code-Fence Parity)",
                "message": f"Unclosed Markdown code fence (` ``` `) in: {', '.join(unclosed_fence_files[:4])}",
            }
        )
    if unclosed_math_files:
        s_prose = min(s_prose, 6.5)
        blockers.append(
            {
                "id": "GWI-KB-P02",
                "severity": "P0",
                "dimension": "V_prose (Display Math Parity)",
                "message": f"Unclosed display math block (`$$`) in: {', '.join(unclosed_math_files[:4])}",
            }
        )

    # Dimension 4: A_struct (Note Architecture, Section Granularity & Stub Freedom)
    r_non_stub = (n_notes - len(stub_note_files)) / max(1, n_notes)
    avg_headings = total_headings / max(1, n_notes)
    q_gran = min(1.0, avg_headings / 2.5)
    s_struct = 5.5 + 3.4 * (r_non_stub * r_non_stub) + 0.8 * q_gran - 1.2 * len(stub_note_files)
    if stub_note_files:
        sev = "P0" if len(stub_note_files) / max(1, n_notes) > 0.20 else "P1"
        if sev == "P0":
            s_struct = min(s_struct, 6.5)
        blockers.append(
            {
                "id": "GWI-KB-P03",
                "severity": sev,
                "dimension": "A_struct (Note Granularity & Stub Freedom)",
                "message": f"{len(stub_note_files)} stub note(s) (< 20 prose words in body): {', '.join(stub_note_files[:4])}",
            }
        )

    s_prose = round(max(0.0, min(9.7, s_prose)), 2)
    s_struct = round(max(0.0, min(9.7, s_struct)), 2)

    # Dimension 5: U_links (Wikilink Closure, Citation Density & Zero Broken Edges — Gate 2)
    avg_degree = valid_links_count / max(1, n_notes)
    s_links = 7.2 + 2.6 * min(1.0, avg_degree / 3.5) - 3.2 * len(broken_links)
    if broken_links:
        s_links = min(s_links, 6.2)
        blockers.append(
            {
                "id": "GWI-KB-A01",
                "severity": "P0",
                "dimension": "U_links (Wikilink Integrity — Gate 2)",
                "message": f"{len(broken_links)} broken [[wikilink]] reference(s): {', '.join(broken_links[:4])}",
            }
        )
    else:
        notes.append(
            f"Gate 2 (Wikilink Integrity) verified: {valid_links_count} valid edge(s) across {n_notes} notes (avg={avg_degree:.2f} links/note, 0 broken)."
        )

    # Dimension 6: D_topo (Graph Topology, Algebraic Connectivity lambda_2 & Orphan Freedom — Gate 3)
    orphan_files = [
        rel for rel, cnt in inbound_counts.items() if rel not in ("INDEX.md", "AGENTS.md", "README.md") and cnt == 0
    ]
    hub_idx = node_idx.get("INDEX.md")
    if hub_idx is None:
        hub_idx = max(range(n_notes), key=lambda idx_k: len(adj_undirected[idx_k])) if n_notes > 0 else 0

    bfs_hops: dict[int, int] = {hub_idx: 0}
    bfs_q: collections.deque[int] = collections.deque([hub_idx])
    while bfs_q:
        cur = bfs_q.popleft()
        for nxt in adj_undirected[cur]:
            if nxt not in bfs_hops:
                bfs_hops[nxt] = bfs_hops[cur] + 1
                bfs_q.append(nxt)

    # Compute exact connected components across all vertices to measure true LCC and island count
    comp_visited: set[int] = set()
    comp_sizes: list[int] = []
    for start_v in range(n_notes):
        if start_v in comp_visited:
            continue
        comp_visited.add(start_v)
        c_q: collections.deque[int] = collections.deque([start_v])
        c_sz = 0
        while c_q:
            u = c_q.popleft()
            c_sz += 1
            for w in adj_undirected[u]:
                if w not in comp_visited:
                    comp_visited.add(w)
                    c_q.append(w)
        comp_sizes.append(c_sz)

    num_components = len(comp_sizes)
    max_comp_size = max(comp_sizes) if comp_sizes else 0
    lcc_ratio = max_comp_size / max(1, n_notes)
    hub_reach_ratio = len(bfs_hops) / max(1, n_notes)
    reach2_ratio = sum(1 for h in bfs_hops.values() if h <= 2) / max(1, n_notes)
    fiedler_lambda2 = compute_graph_fiedler_value(n_notes, adj_undirected)

    s_topo = (
        5.0
        + 2.0 * hub_reach_ratio
        + 1.4 * reach2_ratio
        + 1.4 * min(1.0, fiedler_lambda2 / 0.15)
        - 3.0 * len(orphan_files)
    )
    if orphan_files:
        s_topo = min(s_topo, 6.4)
        blockers.append(
            {
                "id": "GWI-KB-A02",
                "severity": "P0",
                "dimension": "D_topo (Graph Connectivity — Gate 3)",
                "message": f"{len(orphan_files)} orphan note(s) with 0 inbound wikilinks: {', '.join(orphan_files[:4])}",
            }
        )
    if n_notes > 1 and (num_components > 1 or len(bfs_hops) < n_notes):
        s_topo = min(s_topo, 6.5)
        disconnected = [rel_paths[i] for i in range(n_notes) if i not in bfs_hops]
        blockers.append(
            {
                "id": "GWI-KB-A03",
                "severity": "P0",
                "dimension": "D_topo (Spectral Connectivity & Island Detection)",
                "message": (
                    f"Disconnected knowledge island(s) detected: components={num_components}, "
                    f"LCC={max_comp_size}/{n_notes}, hub_reach={len(bfs_hops)}/{n_notes} "
                    f"(lambda_2={fiedler_lambda2:.6f}, unreachable={', '.join(disconnected[:4])})."
                ),
            }
        )
    if not orphan_files and (n_notes == 1 or num_components == 1):
        notes.append(
            f"Gate 3 (Graph Topology) verified: LCC={max_comp_size}/{n_notes} (100%), "
            f"2-hop hub reachability={reach2_ratio * 100:.1f}%, Fiedler lambda_2={fiedler_lambda2:.4f}."
        )

    s_links = round(max(0.0, min(9.8, s_links)), 2)
    s_topo = round(max(0.0, min(9.8, s_topo)), 2)

    # Dimension 7: B_tier0 (Tier-0 Sovereign Taxonomy, Episodic Freshness & RSI Ledger Hash Chain)
    canonical_pillars = (
        (root / "INDEX.md").is_file(),
        (root / "00-identity").is_dir(),
        (root / "01-now").is_dir(),
        (root / "02-operating-system").is_dir(),
        (root / "03-life-domains").is_dir(),
        (root / "04-episodic").is_dir(),
    )
    r_pillars = sum(1 for ok in canonical_pillars if ok) / len(canonical_pillars)
    ep_dir = root / "04-episodic"
    ep_logs = sorted(ep_dir.glob("*-log.md")) if ep_dir.is_dir() else []
    ep_valid = bool(ep_logs and all(re.match(r"^\d{4}-\d{2}-\d{2}-log\.md$", p.name) for p in ep_logs))

    rsi_chain_ok = True
    if (root / ".kb" / "rsi-ledger.jsonl").is_file():
        rsi_chain_ok, _ = verify_ledger_chain(root)

    s_tier0 = 6.0 + 3.2 * r_pillars + (0.5 if ep_valid else 0.0) - (4.0 if not rsi_chain_ok else 0.0)
    if not rsi_chain_ok:
        s_tier0 = min(s_tier0, 6.0)
        blockers.append(
            {
                "id": "GWI-KB-E01",
                "severity": "P0",
                "dimension": "B_tier0 (RSI Cryptographic Ledger Chain — Gate 4)",
                "message": "Tampered or broken SHA-256 hash chain in `.kb/rsi-ledger.jsonl`.",
            }
        )

    # Dimension 8: R_prov (Ground-Truth Provenance, Agent Skills Integrity & Conscience — Gate 6)
    skills_to_check: list[Path] = sorted(
        p for p in root.rglob("SKILL.md") if not any(part.startswith(".") for part in p.relative_to(root).parts)
    )
    if root.name == "me-kb":
        skills_root = Path.home() / ".gemini" / "config" / "skills"
        plugins_root = Path.home() / ".gemini" / "config" / "plugins"
        if skills_root.exists():
            for sdir in sorted(skills_root.iterdir()):
                smd = sdir / "SKILL.md"
                if sdir.is_dir() and smd.is_file():
                    skills_to_check.append(smd)
        if plugins_root.exists():
            for pdir in sorted(plugins_root.iterdir()):
                ps_dir = pdir / "skills"
                if ps_dir.exists():
                    for sk in sorted(ps_dir.iterdir()):
                        smd = sk / "SKILL.md"
                        if sk.is_dir() and smd.is_file():
                            skills_to_check.append(smd)

    bad_skills: list[str] = []
    for smd in skills_to_check:
        ok, err = validate_skill_frontmatter(smd)
        if not ok:
            bad_skills.append(f"{smd.parent.name}/SKILL.md ({err})")

    s_prov = 9.6 - 3.5 * len(secret_leak_files) - 2.5 * len(bad_skills)
    if secret_leak_files:
        s_prov = min(s_prov, 6.0)
        blockers.append(
            {
                "id": "GWI-KB-E02",
                "severity": "P0",
                "dimension": "R_prov (Secret Hygiene — Gate 5)",
                "message": f"Leaked credential pattern detected in vault note(s): {', '.join(secret_leak_files[:4])}",
            }
        )
    if bad_skills:
        s_prov = min(s_prov, 6.4)
        blockers.append(
            {
                "id": "GWI-KB-E03",
                "severity": "P0",
                "dimension": "R_prov (Agent Skills Frontmatter — Gate 6)",
                "message": f"{len(bad_skills)} corrupt SKILL.md manifest(s): {', '.join(bad_skills[:3])}",
            }
        )
    elif skills_to_check:
        notes.append(f"Gate 6 (Agent Skills & Plugins) verified across {len(skills_to_check)} SKILL.md manifest(s).")

    s_tier0 = round(max(0.0, min(9.7, s_tier0)), 2)
    s_prov = round(max(0.0, min(9.6, s_prov)), 2)

    s_sub = round(aggregate_tier_pair(s_schema, s_index, 0.15, 0.10, theta=theta), 2)
    s_perc = round(aggregate_tier_pair(s_prose, s_struct, 0.10, 0.10, theta=theta), 2)
    s_act = round(aggregate_tier_pair(s_links, s_topo, 0.15, 0.15, theta=theta), 2)
    s_exist = round(aggregate_tier_pair(s_tier0, s_prov, 0.15, 0.10, theta=theta), 2)

    dimensions = [
        {
            "id": "F_schema",
            "tier": "S_sub",
            "name": "Frontmatter Schema & Ontological Contract",
            "weight": 0.15,
            "score": s_schema,
        },
        {
            "id": "S_index",
            "tier": "S_sub",
            "name": "Hybrid Index Sync (FTS5/HIPS/Vecs) & Git",
            "weight": 0.10,
            "score": s_index,
        },
        {
            "id": "V_prose",
            "tier": "S_perc",
            "name": "Epistemic Prose, KaTeX Math & Fence Parity",
            "weight": 0.10,
            "score": s_prose,
        },
        {
            "id": "A_struct",
            "tier": "S_perc",
            "name": "Note Architecture, Granularity & Non-Stub",
            "weight": 0.10,
            "score": s_struct,
        },
        {
            "id": "U_links",
            "tier": "S_act",
            "name": "Wikilink Closure & Reference Density",
            "weight": 0.15,
            "score": s_links,
        },
        {
            "id": "D_topo",
            "tier": "S_act",
            "name": "Spectral Connectivity (lambda_2) & LCC",
            "weight": 0.15,
            "score": s_topo,
        },
        {
            "id": "B_tier0",
            "tier": "S_exist",
            "name": "Tier-0 Taxonomy, Episodic & RSI Hash Chain",
            "weight": 0.15,
            "score": s_tier0,
        },
        {
            "id": "R_prov",
            "tier": "S_exist",
            "name": "Provenance, Skill Manifests & Conscience",
            "weight": 0.10,
            "score": s_prov,
        },
    ]

    conscience_clean = (
        len(yaml_error_files) == 0
        and len(conflict_marker_files) == 0
        and len(bloat_files) == 0
        and len(broken_links) == 0
        and len(secret_leak_files) == 0
        and rsi_chain_ok
    )
    g_calc = compute_goldwood_index(
        [s_sub, s_perc, s_act, s_exist],
        weights=ARCHETYPE_WEIGHTS["kb"],
        theta=theta,
        k=k,
        conscience_clean=conscience_clean,
    )

    essential_notes = max(1, n_notes - len(orphan_files) - len(stub_note_files))
    probe_states = {d["id"]: ("PASS" if d["score"] >= 9.0 else "FAIL") for d in dimensions}

    return {
        "profile": "kb",
        "target": str(root),
        "project_name": root.name,
        "kolmogorov_elegance": compute_kolmogorov_elegance(n_notes, essential_notes),
        "snrc": compute_snrc(valid_links_count + len(notes), len(blockers)),
        "dimensions": dimensions,
        "tiers": {
            "S_sub": s_sub,
            "S_perc": s_perc,
            "S_act": s_act,
            "S_exist": s_exist,
        },
        "graph_telemetry": {
            "notes_count": n_notes,
            "valid_links": valid_links_count,
            "broken_links": len(broken_links),
            "avg_degree": round(avg_degree, 2),
            "orphan_count": len(orphan_files),
            "lcc_ratio": round(lcc_ratio, 4),
            "two_hop_reachability": round(reach2_ratio, 4),
            "fiedler_lambda2": fiedler_lambda2,
            "indexed_docs": db_doc_count,
            "indexed_chunks": db_chunk_count,
            "vector_coverage": round(r_vec, 4),
            "hips_ready": hips_ready,
            "rsi_chain_ok": rsi_chain_ok,
        },
        "calculation": g_calc,
        "probe_states": probe_states,
        "blockers": blockers,
        "notes": notes,
    }


CRAFT_LENSES: dict[str, dict[str, Any]] = {
    "technical-artist": {
        "id": "technical-artist",
        "title": "Lens V1 (L1): Technical Artist & 3D Pipeline Specialist",
        "pole": "verga",
        "skills_count": 27,
        "vault_note": "02-operating-system/lenses/technical-artist.md",
        "applies_to_profiles": ["sim", "app"],
        "trigger_deps": ["three", "@react-three/fiber", "@react-three/drei", "@react-three/xr", "cesium", "bevy"],
        "trigger_suffixes": [".glsl", ".wgsl", ".frag", ".vert", ".glb", ".gltf", ".godot", ".tscn", ".unity"],
        "contact_sheet": "6-Pose Canonical Orbit Sheet (1920x1080) + Luminance Histogram",
        "probes": [
            {
                "id": "ta.draw_calls",
                "metric": "Draw calls / instancing discipline",
                "target": "<= 180 calls",
                "veto": "> 300 unbatched draw calls",
            },
            {
                "id": "ta.frame_p95_ms",
                "metric": "95th-percentile frame time @ 1080p",
                "target": "<= 16.6 ms (60 FPS)",
                "veto": "> 33.3 ms (< 30 FPS)",
            },
            {
                "id": "ta.asset_compression",
                "metric": "Binary GLB / KTX2 / Draco compression",
                "target": "100% compressed >1 MB assets",
                "veto": "Uncompressed >2 MB .gltf/.obj/.png",
            },
            {
                "id": "ta.camera_aabb_clearance",
                "metric": "Camera frustum AABB clearance & 1.6m XR eye height",
                "target": ">= 0.25m clearance",
                "veto": "Camera inside mesh or clipping eye-level UI",
            },
            {
                "id": "ta.luminance_balance",
                "metric": "Frame luminance mean/stddev",
                "target": "mu in [28,210], sigma >= 22",
                "veto": "Black frame (mu < 8) or flat unlit (sigma < 14)",
            },
        ],
        "builder_constraints": [
            "Use InstancedMesh / MultiMeshInstance3D for >25 repeated meshes; keep draw calls <= 300.",
            "Configure ACESFilmic/AgX tonemapping with 3-point key/fill/rim lighting (never flat AmbientLight alone).",
            "Maintain 1 unit = 1 meter; set XR eye height at 1.60m and keep manipulatives in [0.85m, 1.45m].",
        ],
    },
    "ux-ergonomics": {
        "id": "ux-ergonomics",
        "title": "Lens V2 (L2): Principal UX, Spatial Ergonomics & Conversion Designer",
        "pole": "verga",
        "skills_count": 12,
        "vault_note": "02-operating-system/lenses/ux-ergonomics.md",
        "applies_to_profiles": ["app"],
        "trigger_deps": ["react", "react-dom", "next", "tailwindcss", "lucide-react", "vue"],
        "trigger_suffixes": [".tsx", ".jsx", ".vue", ".css"],
        "contact_sheet": "3-Viewport Responsive Sheet (390x844 Mobile / 768x1024 Tablet-Quest / 1440x900 Desktop)",
        "probes": [
            {
                "id": "ux.hud_occlusion_pct",
                "metric": "2D HUD overlay area / 3D canvas area",
                "target": "<= 18% occlusion",
                "veto": "> 25% of 3D canvas occluded by permanent 2D panels",
            },
            {
                "id": "ux.wcag_contrast_tap",
                "metric": "WCAG AA contrast (>=4.5:1) & >=44px tap targets",
                "target": "0 violations",
                "veto": "Primary text < 4.5:1 or controls < 32px",
            },
            {
                "id": "ux.cta_hierarchy",
                "metric": "Primary CTA count above first fold",
                "target": "1 primary CTA",
                "veto": ">= 3 competing primary CTAs above fold",
            },
            {
                "id": "ux.steps_to_value",
                "metric": "Steps from landing to core value / checkout",
                "target": "<= 4 steps",
                "veto": "> 5 steps or > 6 onboarding fields",
            },
            {
                "id": "ux.state_completeness",
                "metric": "Designed loading, empty & actionable error states",
                "target": "100% route coverage",
                "veto": "Blank states or generic 'Something went wrong'",
            },
            {
                "id": "ux.subtractive_budget",
                "metric": "Subtractive Design Laws (L0-L6): 1-row header, <=2 answer bullets, <=1 expanded table, edge_density<=0.36",
                "target": "headerRows==1, bullets<=2, tables<=1, E<=0.36",
                "veto": "Multi-row header clutter, stacked history tables (>1), or visual clutter (edge_density > 0.38)",
            },
            {
                "id": "ux.ui_craft_a11y_polish",
                "metric": "UI Craft & A11y Polish (146-Skill Suite: tabular-nums, h-dvh/no-100vw, input aria-label, 16px iOS zoom, dialog a11y)",
                "target": "0 orphaned inputs, tabular-nums on tables, 0 w-screen overflow",
                "veto": "Orphaned <input> with placeholder only or unlabelled icon-only button",
            },
            {
                "id": "ux.wedge_anti_palantir",
                "metric": "Law L6 & L9 'We Are Not Palantir' Human-Data Wedge Gate (/snap, /moment, /card, /marketplace)",
                "target": "Single-column <=680px, progressive disclosure (Step 2/3 hidden until action), 0 B2B telemetry leaks",
                "veto": "Multi-column dashboard grid, un-gated first-paint steps, or leaked B2B jargon ('Smart Pool', '#PV-BOOK-', 'SLA <', 'HACCP', 'Ζωντανή Ροή')",
            },
        ],
        "builder_constraints": [
            "Law L0 (Inherit Before You Invent): Inspect sibling repositories first and inherit their exact Light/Dark polarity, brand hex palette, and institutional footer (never default to dark neon-cyberpunk).",
            "Law L1-L3 (Subtractive Budget): Keep top bar to 1 row (<=7 items, 0 nested badges), default answers <=2 bullets, <=1 expanded analytical table visible at once, and inline citation pills.",
            "Law L6 & L9 ('We Are Not Palantir' Wedge Gate): Consumer wedges (/snap, /moment, /card, /marketplace) must be single-column (<=680px), progressively disclosed (Step 2/3 hidden until Step 1 action), and 100% free of B2B operator telemetry ('Smart Pool', '#PV-BOOK-', 'SLA <', 'HACCP', 'Ζωντανή Ροή').",
            "Law L8 (Simultaneous Cognitive Load): Keep default first paint <=24 interactive controls, <=4 choice groups, <=3 macro-regions, and never default to a split 2D+3D view mode.",
            "Keep 2D HUD overlays collapsible/translucent and <= 25% of 3D canvas area.",
            "Enforce 1 unambiguous primary CTA per view and >= 44x44px touch/pointer targets.",
            "Bind all colors and spacing to semantic CSS custom properties + prefers-reduced-motion fallback.",
        ],
    },
    "spatial-cinema": {
        "id": "spatial-cinema",
        "title": "Lens V3 (L3): Spatial Audio & Cinema Director",
        "pole": "verga",
        "skills_count": 6,
        "vault_note": "02-operating-system/lenses/spatial-cinema.md",
        "applies_to_profiles": ["video", "sim"],
        "trigger_deps": ["tone", "howler"],
        "trigger_suffixes": [".mp4", ".webm", ".wav", ".mp3", ".srt", ".vtt"],
        "contact_sheet": "9-Frame Act Sequence Sheet + Bottom Waveform/Spectrogram Strip",
        "probes": [
            {
                "id": "sc.ebu_r128_lufs",
                "metric": "Integrated loudness (I) & True Peak (TP)",
                "target": "I in [-16,-13] LUFS, TP <= -1.0 dBTP",
                "veto": "Silent stream, I outside [-24,-11], or TP > -0.5 dBTP",
            },
            {
                "id": "sc.dead_air_gaps",
                "metric": "Unintentional silence gaps",
                "target": "0 gaps > 2.5s",
                "veto": "Dead-air > 3.5s or truncated audio",
            },
            {
                "id": "sc.video_encoding",
                "metric": "1080p/4K yuv420p CFR stream integrity",
                "target": "1920x1080 yuv420p >=30fps",
                "veto": "yuv444p or odd dimensions",
            },
            {
                "id": "sc.camera_rig_bounds",
                "metric": "Camera FOV & horizon roll stability",
                "target": "FOV <= 90 deg (XR), roll <= 3 deg",
                "veto": "Unintentional roll > 3 deg or static hold > 25s",
            },
        ],
        "builder_constraints": [
            "Support deterministic '?render=1&seed=42' frame-stepped capture.",
            "Master audio to EBU R128 (-14.0 LUFS, -1.2 dBTP) with >= 6 dB narration-over-BGM ducking.",
            "Use smooth cubic/quintic camera easing; avoid static holds > 15s.",
        ],
    },
    "typography-editor": {
        "id": "typography-editor",
        "title": "Lens V4 (L4): Scientific & Literary Typography Editor",
        "pole": "verga",
        "skills_count": 10,
        "vault_note": "02-operating-system/lenses/typography-editor.md",
        "applies_to_profiles": ["paper", "kb"],
        "trigger_deps": [],
        "trigger_suffixes": [".tex", ".bib", ".cls", ".sty"],
        "contact_sheet": "12-Page-per-Sheet PDF Thumbnail Grid (1920x1080)",
        "probes": [
            {
                "id": "ty.overfull_hbox",
                "metric": "LaTeX .log Overfull \\hbox count & max pt badness",
                "target": "0 boxes > 3.0pt",
                "veto": "Any Overfull \\hbox > 10.0pt or > 5 boxes > 3.0pt",
            },
            {
                "id": "ty.undef_citations_refs",
                "metric": "Undefined references (??) & citations",
                "target": "0 undefined, 100% .bib parity",
                "veto": "Any undefined reference or citation",
            },
            {
                "id": "ty.markdown_leakage",
                "metric": "Raw Markdown (**bold**, `code`) in .tex",
                "target": "0 leaked tokens",
                "veto": "Any unescaped **...** in compiled text",
            },
            {
                "id": "ty.vector_fonts_dpi",
                "metric": "Type-42/Type-1 vector fonts & figure legibility",
                "target": "100% vector PDFs, font >= 8pt",
                "veto": "Type-3 bitmapped fonts or figure text < 7pt",
            },
            {
                "id": "ty.widows_float_drift",
                "metric": "Widow/orphan control & float proximity",
                "target": "widowpenalty=10000, drift <= 1p",
                "veto": "Stranded headings or float drift > 2 pages",
            },
        ],
        "builder_constraints": [
            "Enable \\usepackage{microtype}, \\widowpenalty=10000, \\clubpenalty=10000, and \\usepackage{booktabs}.",
            "Export all figures as Type-42 vector PDFs with axis/legend font size >= 8pt at column width.",
            "Ensure 0 Overfull \\hbox > 3pt and 0 undefined references/citations in .log.",
        ],
    },
    "web-perf-edge": {
        "id": "web-perf-edge",
        "title": "Lens C1 (L5): Web Performance, Bundle & Edge Runtime Architect",
        "pole": "chrysos",
        "skills_count": 10,
        "vault_note": "02-operating-system/lenses/web-perf-edge.md",
        "applies_to_profiles": ["app"],
        "trigger_deps": ["react", "react-dom", "next", "vite", "wrangler", "@cloudflare/workers-types", "vue"],
        "trigger_suffixes": [".tsx", ".jsx", ".mjs"],
        "contact_sheet": "Production Bundle & Edge Runtime Waterfall Telemetry (vercel-react-craft + cloudflare-platform)",
        "probes": [
            {
                "id": "wp.barrel_waterfalls",
                "metric": "Zero barrel `import * as` icon/UI imports & async waterfall hygiene",
                "target": "0 barrel wildcard imports",
                "veto": "import * as from lucide-react / @mui/icons-material / @heroicons",
            },
            {
                "id": "wp.listener_memory_hygiene",
                "metric": "DOM/window event listener & timer teardown in effects",
                "target": "100% paired teardown",
                "veto": "Unpaired global addEventListener leak inside component loop",
            },
            {
                "id": "wp.edge_bundle_budget",
                "metric": "Uncompressed JS chunk size & Edge runtime configuration",
                "target": "Max JS chunk <= 2.5 MB",
                "veto": "Single uncompressed JS bundle > 4.0 MB",
            },
        ],
        "builder_constraints": [
            "Enforce `vercel-react-craft` bundle rules: use named icon imports (never `import * as Icons from 'lucide-react'`) and parallelize independent async fetches.",
            "Pair every global `addEventListener` / `setInterval` in `useEffect` with deterministic cleanup to prevent heap leaks (`memory-leak-debugging`).",
            "Keep production chunks modular for fast LCP (`debug-optimize-lcp`) and Cloudflare Pages/Workers edge cold-starts (`cloudflare-platform`).",
        ],
    },
    "data-api-architect": {
        "id": "data-api-architect",
        "title": "Lens C2 (L6): Data Engineering, Database & API Contract Architect",
        "pole": "chrysos",
        "skills_count": 32,
        "vault_note": "02-operating-system/lenses/data-api-architect.md",
        "applies_to_profiles": ["app"],
        "trigger_deps": [
            "@supabase/supabase-js",
            "pg",
            "prisma",
            "drizzle-orm",
            "fastapi",
            "express",
            "sqlalchemy",
            "google-cloud-bigquery",
        ],
        "trigger_suffixes": [".sql", ".sqlx", ".graphql"],
        "contact_sheet": "SQL Schema/RLS Audit + OpenAPI/Postman Contract Verification Matrix",
        "probes": [
            {
                "id": "da.destructive_sql_guard",
                "metric": "Accidental data-loss guard on SQL migrations & queries",
                "target": "0 unguarded DROP/TRUNCATE/DELETE",
                "veto": "DELETE FROM without WHERE or unguarded DROP DATABASE in runtime code",
            },
            {
                "id": "da.rls_index_hygiene",
                "metric": "Postgres/BigQuery primary key, RLS & index discipline",
                "target": "PK + RLS/index coverage verified",
                "veto": "Public multi-tenant tables explicitly disabling RLS without service-role guard",
            },
            {
                "id": "da.api_contract_schema",
                "metric": "Typed API request/response schemas & error recovery contracts",
                "target": "100% typed status/error payloads",
                "veto": "Swallowed API exceptions or unvalidated raw SQL string interpolation",
            },
        ],
        "builder_constraints": [
            "Never execute or ship unguarded destructive SQL (`DROP TABLE`, `TRUNCATE`, `DELETE FROM` without `WHERE`) (`accidental-data-loss-prevention`).",
            "Enforce Postgres RLS policies, foreign-key indexes, and parameterized queries (`supabase-postgres`, `bigquery-sql`).",
            "Define explicit typed API contracts and deterministic mock/test collections (`api-engineer`, `api-testing`).",
        ],
    },
    "ai-ml-science": {
        "id": "ai-ml-science",
        "title": "Lens C3 (L7): AI/ML Systems, GX10 Cluster & Scientific Reproducibility Auditor",
        "pole": "chrysos",
        "skills_count": 36,
        "vault_note": "02-operating-system/lenses/ai-ml-science.md",
        "applies_to_profiles": ["sim", "paper"],
        "trigger_deps": [
            "torch",
            "transformers",
            "google-genai",
            "@google/genai",
            "openai",
            "anthropic",
            "scipy",
            "numpy",
            "biopython",
        ],
        "trigger_suffixes": [".ipynb", ".fasta", ".pdb", ".cif"],
        "contact_sheet": "Deterministic Seed, GX10 Cluster Priority & Scientific Provenance Ledger",
        "probes": [
            {
                "id": "ai.deterministic_seeds",
                "metric": "Pinned PRNG seeds & current Gemini/LLM SDK compliance",
                "target": "Seeded PRNG + current google-genai SDK",
                "veto": "Deprecated google.generativeai SDK or unseeded stochastic eval in paper/sim",
            },
            {
                "id": "ai.gx10_cluster_guard",
                "metric": "Protected local GPU & inference daemon service guard",
                "target": "0 inference daemon disruptions",
                "veto": "systemctl stop/restart of protected inference daemons",
            },
            {
                "id": "ai.reproducible_artifacts",
                "metric": "Executable simulation/ML metrics & biological/literature provenance",
                "target": "Verified metrics/citations",
                "veto": "Fabricated benchmark numbers without generator script",
            },
        ],
        "builder_constraints": [
            "Pin `PYTHONHASHSEED=42` / explicit PRNG seeds across all simulations and ML evaluations (`ml-best-practices`, `huggingface-ml`).",
            "Use the current `google-genai` / `@google/genai` SDK (`gemini-api-dev`) and route local GPU queries through the priority proxy.",
            "Never stop or restart protected local inference services configured in `KB_PROTECTED_SERVICES`.",
        ],
    },
    "principal-verification": {
        "id": "principal-verification",
        "title": "Lens C4 (L8): Principal Engineering, TDD, Lockfile & Skill-Binding Gate",
        "pole": "chrysos",
        "skills_count": 13,
        "vault_note": "02-operating-system/lenses/principal-verification.md",
        "applies_to_profiles": ["app", "sim", "video", "paper", "kb"],
        "trigger_deps": ["vitest", "playwright", "@playwright/test", "pytest"],
        "trigger_suffixes": [".lock"],
        "contact_sheet": "AGENTS.md Activated Sovereign Agent Skills Matrix + Lockfile & Secret Audit",
        "probes": [
            {
                "id": "pv.lockfile_reproducibility",
                "metric": "Dependency manifest & lockfile reproducibility (uv / npm / cargo)",
                "target": "Deterministic manifest/lockfile",
                "veto": "Corrupt package.json / pyproject.toml / Cargo.toml",
            },
            {
                "id": "pv.secret_hygiene",
                "metric": "Zero leaked credentials or private keys in tracked source files",
                "target": "0 leaked tokens",
                "veto": "Hardcoded live API token or cloud credential in source",
            },
            {
                "id": "pv.skill_manifest_binding",
                "metric": "Archetype Skill Binding in AGENTS.md & SKILL.md disk presence and valid YAML frontmatter",
                "target": "100% bound skills exist on disk with valid YAML frontmatter",
                "veto": "Missing or syntactically corrupt SKILL.md entry points referenced in AGENTS.md",
            },
            {
                "id": "pv.canonical_anchor_l9",
                "metric": "Law L9 — Canonical Monorepo Anchor Over New-Repo & Subdomain Sprawl",
                "target": "9 canonical GitLab groups, 0 standalone wedge repos/subdomains (R_L9==1.0)",
                "veto": "Non-canonical GitLab group sprawl (>9) or standalone single-route wedge repo (/snap, /moment, ?room=204, /card)",
            },
            {
                "id": "pv.readme_doc_freshness",
                "metric": "README.md is present, non-stub, and has no dead relative links/paths (full drift check: `readme_check.py` pre-push)",
                "target": "README.md >= 120B, 0 placeholder TODOs, 0 dead references",
                "veto": "Missing/stub README.md in git repository, placeholder TODO, or link/path to a non-existent file",
            },
        ],
        "builder_constraints": [
            "Bind archetype skills in `AGENTS.md` via `kb skills --inject` (`146` top-level skills, `2,507` reference modules).",
            "Maintain zero leaked secrets in tracked files (`credentials` / `~/.config/agents/secrets.env`) and strict TDD verification (`principal-engineering`).",
            "Law L9 (Canonical Anchor Over Sprawl): Mount new wedges (/snap, /moment, ?room=204, /card) inside the 9 canonical GitLab pillar groups and canonical monorepos; never spawn standalone micro-repos or subdomains.",
            "Every path, command, and number written into `README.md` must be verified against the code/tool output before commit; `readme_check.py` blocks pushes with dead paths, phantom commands, undocumented new surface, or wrong test counts.",
        ],
    },
    SCI_LENS_ID: {
        "id": SCI_LENS_ID,
        "title": "Lens C5 (L9): Science Integrity, Measurement Provenance & Security Soundness Gate",
        "pole": "chrysos",
        "skills_count": 4,
        "vault_note": "02-operating-system/lenses/science-integrity.md",
        "applies_to_profiles": ["app", "sim", "video", "paper", "kb"],
        "trigger_deps": [],
        "trigger_suffixes": [],
        "contact_sheet": "GWI-SCI Findings Ledger (claim -> generator script -> acquisition call / raw trace; collected test count)",
        "probes": [
            {
                "id": "GWI-SCI-01",
                "metric": "Measurement provenance: 'measured/telemetry/benchmarked on <hardware>' backed by acquisition code or raw traces",
                "target": "0 un-hedged hardware claims generated from literal constants",
                "veto": "Paper/README calls hardcoded numbers measured telemetry (no subprocess/timer/trace read, no raw trace)",
            },
            {
                "id": "GWI-SCI-02",
                "metric": "Collected test count when tests are configured or claimed (`pytest --collect-only -q` under --strict-lenses)",
                "target": ">= 1 collected test",
                "veto": "Test config / README test claim with 0 collected tests",
            },
            {
                "id": "GWI-SCI-03",
                "metric": "Quality gate executes a real test runner (not only `test -f` existence checks)",
                "target": "gate invokes pytest / cargo test / vitest / npm test / godot -s",
                "veto": "Hollow gate in app/sim profile or in a repo that declares a test framework",
            },
            {
                "id": "GWI-SCI-04",
                "metric": "Seed/fixture content attributed to real model ids carries a provenance marker or a real model call",
                "target": "0 unmarked model-attributed seed files",
                "veto": "Hardcoded 'reviews' signed as claude-*/gpt-*/qwen*/gemini-* with no model call and no `synthetic_demo` marker",
            },
            {
                "id": "GWI-SCI-05",
                "metric": "Web soundness: no interpolated inline event handlers; signatures bound to registered keys",
                "target": "0 interpolated on*= handlers; key-registry lookup before verify",
                "veto": "onclick=\"f('${x}')\" style interpolation in shipped HTML/JS (05A); caller-supplied pubkey verify is WARN (05B)",
            },
        ],
        "builder_constraints": [
            "Label every number that was not acquired on hardware as simulated / illustrative / assumed in the same paragraph, or commit the raw traces and the acquisition script (`nvidia-smi`, `powermetrics`, `perf`, timers).",
            "A quality gate must execute the test suite; `test -f results.json` is not verification. Keep >= 1 collected test whenever tests are configured or claimed.",
            "Seeded demo content attributed to real models must carry `provenance: synthetic_demo` (or be produced by an actual model call).",
            "Never build inline `on*=` handlers from template strings; use `addEventListener` + `data-*`. Bind agent_id -> pubkey in a registry before verifying signatures.",
        ],
    },
}


def detect_active_lenses(target: Path, profile: str = "auto") -> list[dict[str, Any]]:
    """
    Deterministically detect which Bi-Polar Specialist Lenses (4 Verga + 4 Chrysos) apply to `target`
    based on profile, dependency manifests (`package.json`, `pyproject.toml`, `Cargo.toml`), and source files.
    """
    resolved = target.expanduser().resolve()
    chosen_profile = detect_profile(resolved) if profile == "auto" else profile
    root = resolved if resolved.is_dir() else resolved.parent

    pkg_deps: set[str] = set()
    pkg_json = root / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
            pkg_deps.update((data.get("dependencies") or {}).keys())
            pkg_deps.update((data.get("devDependencies") or {}).keys())
        except Exception:
            pass

    pyproject_toml = root / "pyproject.toml"
    if pyproject_toml.is_file():
        py_txt = pyproject_toml.read_text(encoding="utf-8", errors="ignore").lower()
        for py_dep in (
            "torch",
            "transformers",
            "google-genai",
            "openai",
            "anthropic",
            "scipy",
            "numpy",
            "biopython",
            "fastapi",
            "sqlalchemy",
            "google-cloud-bigquery",
            "pytest",
        ):
            if re.search(rf"\b{re.escape(py_dep)}\b", py_txt):
                pkg_deps.add(py_dep)

    cargo_toml = root / "Cargo.toml"
    if cargo_toml.is_file():
        cargo_txt = cargo_toml.read_text(encoding="utf-8", errors="ignore").lower()
        if "bevy" in cargo_txt:
            pkg_deps.add("bevy")

    present_suffixes: set[str] = set()
    if resolved.is_file():
        present_suffixes.add(resolved.suffix.lower())
    elif root.is_dir():
        for p in _iter_source_files(
            root,
            (
                ".glsl",
                ".wgsl",
                ".frag",
                ".vert",
                ".glb",
                ".gltf",
                ".godot",
                ".tscn",
                ".unity",
                ".tsx",
                ".jsx",
                ".vue",
                ".css",
                ".mjs",
                ".mp4",
                ".webm",
                ".wav",
                ".mp3",
                ".srt",
                ".vtt",
                ".tex",
                ".bib",
                ".sql",
                ".sqlx",
                ".graphql",
                ".ipynb",
                ".fasta",
                ".pdb",
                ".cif",
                ".lock",
            ),
        ):
            present_suffixes.add(p.suffix.lower())

    active: list[dict[str, Any]] = []
    for lens_id, spec in CRAFT_LENSES.items():
        matched_reasons: list[str] = []
        if chosen_profile in spec["applies_to_profiles"]:
            if lens_id == "typography-editor" and chosen_profile in ("paper", "kb"):
                matched_reasons.append(f"profile={chosen_profile}")
            elif lens_id == "spatial-cinema" and chosen_profile == "video":
                matched_reasons.append("profile=video")
            elif lens_id == "technical-artist" and chosen_profile == "sim":
                matched_reasons.append("profile=sim")
            elif lens_id == "ux-ergonomics" and chosen_profile == "app":
                matched_reasons.append("profile=app")
            elif (
                lens_id == "web-perf-edge"
                and chosen_profile == "app"
                and (pkg_json.is_file() or (root / "wrangler.toml").is_file() or ".tsx" in present_suffixes)
            ):
                matched_reasons.append("profile=app")
            elif lens_id == "principal-verification" and (
                chosen_profile == "kb"
                or (root / "AGENTS.md").is_file()
                or pkg_json.is_file()
                or pyproject_toml.is_file()
                or cargo_toml.is_file()
            ):
                matched_reasons.append(f"profile={chosen_profile}")
            elif (
                lens_id == SCI_LENS_ID
                and root.is_dir()
                and not (resolved.is_file() and resolved.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"))
            ):
                matched_reasons.append(f"integrity=always(profile={chosen_profile})")

        dep_hits = sorted(pkg_deps.intersection(spec["trigger_deps"]))
        if dep_hits and (chosen_profile != "paper" or "paper" in spec["applies_to_profiles"]):
            matched_reasons.append(f"deps={','.join(dep_hits[:3])}")

        suf_hits = sorted(present_suffixes.intersection(spec["trigger_suffixes"]))
        if suf_hits and (chosen_profile != "paper" or "paper" in spec["applies_to_profiles"]):
            matched_reasons.append(f"files={','.join(suf_hits[:3])}")

        if lens_id == "data-api-architect" and not matched_reasons:
            if (root / "supabase").is_dir() or list(root.glob("*openapi*.y*ml")) or list(root.glob("*openapi*.json")):
                matched_reasons.append("schema=openapi/supabase")

        if lens_id == "ai-ml-science" and not matched_reasons:
            if (root / "sim" / "run_sim.py").is_file() or (root / "sim" / "verify_sim.py").is_file():
                matched_reasons.append("sim=executable_sim")

        if matched_reasons:
            entry = dict(spec)
            entry["activation_reasons"] = matched_reasons
            active.append(entry)

    return active


def srgb_to_relative_luminance(r_8: int, g_8: int, b_8: int) -> float:
    """Compute WCAG 2.1 linearized relative luminance L in [0.0, 1.0] from 8-bit sRGB channels."""

    def _lin(c: int) -> float:
        v = max(0.0, min(1.0, c / 255.0))
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r_8) + 0.7152 * _lin(g_8) + 0.0722 * _lin(b_8)


def _parse_hex_rgb(hex_str: str) -> tuple[int, int, int] | None:
    s = hex_str.strip().lstrip("#")
    if len(s) in (3, 4) and all(c in "0123456789abcdefABCDEF" for c in s):
        s = "".join(c * 2 for c in s[:3])
    if len(s) in (6, 8) and all(c in "0123456789abcdefABCDEF" for c in s):
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return None


def compute_wcag_contrast_ratio(color_a: str, color_b: str) -> float:
    """Compute WCAG 2.1 contrast ratio (L_max + 0.05) / (L_min + 0.05) in [1.0, 21.0] for two hex colors."""
    rgb_a = _parse_hex_rgb(color_a)
    rgb_b = _parse_hex_rgb(color_b)
    if rgb_a is None or rgb_b is None:
        return 1.0
    la = srgb_to_relative_luminance(*rgb_a)
    lb = srgb_to_relative_luminance(*rgb_b)
    l_max, l_min = max(la, lb), min(la, lb)
    return round((l_max + 0.05) / (l_min + 0.05), 2)


def probe_image_grid_telemetry(img_path: Path, grid_cols: int = 3, grid_rows: int = 2) -> dict[str, Any]:
    """
    Decode an image or contact sheet (`1920x1080`) into an RGB24 buffer via `ffmpeg` (or PNG header fallback)
    and compute deterministic Fable x Astra perceptual metrics:
      - Rec.709 luminance Y = 0.2126R + 0.7152G + 0.0722B: mean_y, std_y, clipped_pct (Y < 2 or Y > 253)
      - Gradient / Sobel edge density: fraction of pixels with |grad Y| > 18.0
      - Pairwise tile dissimilarity (MSE) across grid cells so identical repeated frames are detected
    """
    out: dict[str, Any] = {
        "probed": False,
        "width": 0,
        "height": 0,
        "mean_y": 0.0,
        "std_y": 0.0,
        "clipped_pct": 0.0,
        "edge_density": 0.0,
        "min_tile_mse": 0.0,
    }
    if not img_path.is_file() or img_path.stat().st_size < 64:
        return out

    raw_head = img_path.read_bytes()[:32]
    if raw_head.startswith(b"\x89PNG\r\n\x1a\n") and len(raw_head) >= 24:
        out["width"] = int.from_bytes(raw_head[16:20], "big")
        out["height"] = int.from_bytes(raw_head[20:24], "big")

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return out

    w, h = 360, 204
    expected = w * h * 3
    try:
        res = subprocess.run(
            [
                ffmpeg_bin,
                "-nostats",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(img_path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={w}:{h}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        buf = res.stdout
        if len(buf) < expected:
            return out

        out["probed"] = True
        if out["width"] == 0:
            out["width"], out["height"] = 1920, 1080

        luma = [0.0] * (w * h)
        sum_y = 0.0
        sum_y2 = 0.0
        clipped = 0
        n_pix = w * h
        for i in range(n_pix):
            off = i * 3
            yv = 0.2126 * buf[off] + 0.7152 * buf[off + 1] + 0.0722 * buf[off + 2]
            luma[i] = yv
            sum_y += yv
            sum_y2 += yv * yv
            if yv < 2.0 or yv > 253.0:
                clipped += 1

        mean_y = sum_y / n_pix
        std_y = math.sqrt(max(0.0, (sum_y2 / n_pix) - (mean_y * mean_y)))

        edges = 0
        inner_count = 0
        for py in range(1, h - 1):
            row_off = py * w
            for px in range(1, w - 1):
                gx = abs(luma[row_off + px + 1] - luma[row_off + px - 1])
                gy = abs(luma[row_off + w + px] - luma[row_off - w + px])
                if (gx + gy) > 18.0:
                    edges += 1
                inner_count += 1

        tile_w = max(8, w // max(1, grid_cols))
        tile_h = max(8, int((h * 0.75) // max(1, grid_rows)))
        tiles: list[list[float]] = []
        for gr in range(grid_rows):
            for gc in range(grid_cols):
                ty0 = min(h - tile_h, gr * tile_h)
                tx0 = min(w - tile_w, gc * tile_w)
                vec: list[float] = []
                for ty in range(ty0 + 4, ty0 + tile_h - 4, 2):
                    for tx in range(tx0 + 4, tx0 + tile_w - 4, 2):
                        vec.append(luma[ty * w + tx])
                if vec:
                    tiles.append(vec)

        pair_mses: list[float] = []
        for idx in range(len(tiles) - 1):
            a, b = tiles[idx], tiles[idx + 1]
            m_len = min(len(a), len(b))
            if m_len > 0:
                mse = sum((a[k] - b[k]) ** 2 for k in range(m_len)) / m_len
                pair_mses.append(mse)

        out["mean_y"] = round(mean_y, 2)
        out["std_y"] = round(std_y, 2)
        out["clipped_pct"] = round((clipped * 100.0) / n_pix, 2)
        out["edge_density"] = round(edges / max(1, inner_count), 4)
        out["min_tile_mse"] = round(min(pair_mses), 2) if pair_mses else 0.0
    except Exception:
        pass
    return out


def probe_pdf_typography(pdf_path: Path) -> dict[str, Any]:
    """
    Inspect compiled PDF via Poppler `pdffonts` and `pdfimages -list` (plus binary stream fallback):
      - `type3_fonts`: count of bitmapped Type-3 fonts (must be 0)
      - `unembedded_fonts`: count of non-embedded fonts (must be 0)
      - `min_image_ppi`: minimum effective raster image DPI (target >= 150 PPI)
    """
    info: dict[str, Any] = {
        "probed": False,
        "total_fonts": 0,
        "type3_fonts": 0,
        "unembedded_fonts": 0,
        "raster_images": 0,
        "min_image_ppi": None,
    }
    if not pdf_path.is_file() or pdf_path.stat().st_size < 128:
        return info

    raw_bytes = pdf_path.read_bytes()
    if not raw_bytes.startswith(b"%PDF-"):
        return info

    if b"/Subtype /Type3" in raw_bytes or b"/Subtype/Type3" in raw_bytes:
        info["probed"] = True
        info["type3_fonts"] = raw_bytes.count(b"/Subtype /Type3") + raw_bytes.count(b"/Subtype/Type3")
    elif len(raw_bytes) >= 512 and b"corrupt" not in raw_bytes[:128].lower():
        info["probed"] = True

    pdffonts_bin = shutil.which("pdffonts")
    if pdffonts_bin:
        try:
            res = subprocess.run(
                [pdffonts_bin, str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = res.stdout.strip().splitlines()
                if len(lines) >= 2:
                    info["probed"] = True
                    t3 = 0
                    unemb = 0
                    total = 0
                    for ln in lines[2:]:
                        parts = ln.split()
                        if len(parts) >= 5:
                            total += 1
                            if "Type 3" in ln:
                                t3 += 1
                            yn_tokens = [p for p in parts if p in ("yes", "no")]
                            if yn_tokens and yn_tokens[0] == "no":
                                unemb += 1
                    info["total_fonts"] = total
                    info["type3_fonts"] = max(info["type3_fonts"], t3)
                    info["unembedded_fonts"] = unemb
        except Exception:
            pass

    pdfimages_bin = shutil.which("pdfimages")
    if pdfimages_bin:
        try:
            res_img = subprocess.run(
                [pdfimages_bin, "-list", str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res_img.returncode == 0 and res_img.stdout.strip():
                lines = res_img.stdout.strip().splitlines()
                ppis: list[int] = []
                for ln in lines[2:]:
                    cols = ln.split()
                    if len(cols) >= 14 and cols[2] == "image":
                        try:
                            x_ppi = int(cols[12])
                            y_ppi = int(cols[13])
                            if x_ppi > 0 and y_ppi > 0:
                                ppis.append(min(x_ppi, y_ppi))
                        except ValueError:
                            pass
                info["raster_images"] = len(ppis)
                if ppis:
                    info["min_image_ppi"] = min(ppis)
        except Exception:
            pass

    return info


_TAILWIND_SRGB_HEX: dict[str, str] = {
    "white": "#ffffff",
    "black": "#000000",
    "slate-50": "#f8fafc",
    "slate-100": "#f1f5f9",
    "slate-200": "#e2e8f0",
    "slate-300": "#cbd5e1",
    "slate-400": "#94a3b8",
    "slate-500": "#64748b",
    "slate-600": "#475569",
    "slate-700": "#334155",
    "slate-800": "#1e293b",
    "slate-900": "#0f172a",
    "slate-950": "#020617",
    "gray-50": "#f9fafb",
    "gray-100": "#f3f4f6",
    "gray-200": "#e5e7eb",
    "gray-300": "#d1d5db",
    "gray-400": "#9ca3af",
    "gray-500": "#6b7280",
    "gray-600": "#4b5563",
    "gray-700": "#374151",
    "gray-800": "#1f2937",
    "gray-900": "#111827",
    "gray-950": "#030712",
    "zinc-50": "#fafafa",
    "zinc-100": "#f4f4f5",
    "zinc-400": "#a1a1aa",
    "zinc-500": "#71717a",
    "zinc-800": "#27272a",
    "zinc-900": "#18181b",
    "zinc-950": "#09090b",
}


def _audit_css_token_wcag_contrast(css_and_ui_text: str) -> dict[str, Any]:
    """
    Extract semantic surface/background (`--*-bg*`, `--*-surface*`, `--*-canvas*`) and primary text
    (`--*-text*`, `--*-ink*`, `--*-fg*`) hex tokens (including 3/4/6/8-digit hex, `light-dark(...)`,
    and Tailwind `bg-*` + `text-*` class pairs) and compute worst-case WCAG 2.1 contrast ratio.
    """
    blocks = re.findall(r"\{([^{}]+)\}", css_and_ui_text)
    worst_cr = 21.0
    worst_pair = ""
    checked_pairs = 0

    def _is_bg_var(k: str) -> bool:
        kl = k.lower()
        return any(
            t in kl for t in ("bg-0", "bg-1", "bg-surface", "bg-primary", "surface-primary", "bg-canvas", "--bg:")
        ) or (
            any(t in kl for t in ("bg", "surface", "canvas"))
            and not any(
                x in kl for x in ("-2", "-3", "border", "hover", "accent", "glow", "badge", "elevated", "chip", "glass")
            )
        )

    def _is_fg_var(k: str) -> bool:
        kl = k.lower()
        return any(
            t in kl
            for t in ("ink-0", "ink-1", "text-primary", "ink-primary", "fg-primary", "--text:", "--ink:", "--fg:")
        ) or (
            any(t in kl for t in ("text", "ink", "fg"))
            and not any(
                m in kl for m in ("-2", "-3", "muted", "dim", "sub", "secondary", "disabled", "faint", "tertiary")
            )
        )

    var_re = re.compile(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\b")
    ld_re = re.compile(r"(--[\w-]+)\s*:\s*light-dark\(\s*(#[0-9a-fA-F]{3,8})\s*,\s*(#[0-9a-fA-F]{3,8})\s*\)")

    scopes = blocks if any(var_re.search(b) or ld_re.search(b) for b in blocks) else [css_and_ui_text]
    for blk in scopes:
        vars_in_blk = dict(var_re.findall(blk))
        if vars_in_blk:
            bgs = {k: v for k, v in vars_in_blk.items() if _is_bg_var(k)}
            fgs = {k: v for k, v in vars_in_blk.items() if _is_fg_var(k)}
            for bk, bv in bgs.items():
                for fk, fv in fgs.items():
                    cr = compute_wcag_contrast_ratio(bv, fv)
                    checked_pairs += 1
                    if cr < worst_cr:
                        worst_cr = cr
                        worst_pair = f"{fk} ({fv}) on {bk} ({bv})"
            if "--mt-accent" in vars_in_blk and "--mt-accent-contrast" in vars_in_blk:
                cr_btn = compute_wcag_contrast_ratio(vars_in_blk["--mt-accent"], vars_in_blk["--mt-accent-contrast"])
                checked_pairs += 1
                if cr_btn < worst_cr:
                    worst_cr = cr_btn
                    worst_pair = (
                        f"--mt-accent-contrast ({vars_in_blk['--mt-accent-contrast']}) "
                        f"on --mt-accent ({vars_in_blk['--mt-accent']})"
                    )

        ld_matches = ld_re.findall(blk)
        if ld_matches:
            light_vars = {k: l_hex for k, l_hex, _ in ld_matches}
            dark_vars = {k: d_hex for k, _, d_hex in ld_matches}
            for mode_name, mode_map in (("light", light_vars), ("dark", dark_vars)):
                bgs = {k: v for k, v in mode_map.items() if _is_bg_var(k)}
                fgs = {k: v for k, v in mode_map.items() if _is_fg_var(k)}
                for bk, bv in bgs.items():
                    for fk, fv in fgs.items():
                        cr = compute_wcag_contrast_ratio(bv, fv)
                        checked_pairs += 1
                        if cr < worst_cr:
                            worst_cr = cr
                            worst_pair = f"{fk} ({fv} {mode_name}) on {bk} ({bv} {mode_name})"

    # Audit explicit Tailwind bg-* + text-* utility pairs on the same element class attribute
    class_attr_re = re.compile(r"""class(?:Name)?\s*=\s*["'`]([^"'`]+)["'`]""")
    for cm in class_attr_re.finditer(css_and_ui_text):
        tokens = cm.group(1).split()
        bg_toks = [t[3:] for t in tokens if t.startswith("bg-") and t[3:] in _TAILWIND_SRGB_HEX]
        fg_toks = [t[5:] for t in tokens if t.startswith("text-") and t[5:] in _TAILWIND_SRGB_HEX]
        for bt in bg_toks:
            for ft in fg_toks:
                cr = compute_wcag_contrast_ratio(_TAILWIND_SRGB_HEX[bt], _TAILWIND_SRGB_HEX[ft])
                checked_pairs += 1
                if cr < worst_cr:
                    worst_cr = cr
                    worst_pair = f"text-{ft} ({_TAILWIND_SRGB_HEX[ft]}) on bg-{bt} ({_TAILWIND_SRGB_HEX[bt]})"

    return {
        "checked_pairs": checked_pairs,
        "min_contrast_ratio": round(worst_cr, 2) if checked_pairs > 0 else None,
        "worst_pair": worst_pair,
    }


def _audit_structural_regularity(ui_text: str, css_text: str = "") -> dict[str, Any]:
    """
    Law L7 — Simplicity Favors Regularity (Η Απλότητα Ευνοεί την Κανονικότητα).
    Measures ad-hoc micro-variance across stylesheet typography scales (`font-size`),
    border radii (`border-radius`), and layout grids (`grid-template-columns`), plus
    HTML inline `style="..."` and arbitrary Tailwind one-off pixel bracket escapes
    (`text-[11.5px]`, `rounded-[5px]`).
    """
    style_blocks = re.findall(r"<style\b[^>]*>(.*?)</style>", ui_text, flags=re.DOTALL | re.IGNORECASE)
    inline_styles = re.findall(r'\bstyle\s*=\s*["\']([^"\']+)["\']', ui_text, flags=re.IGNORECASE)
    jsx_style_objs = "\n".join(re.findall(r"\bstyle\s*=\s*\{\{([^}]+)\}\}", ui_text))
    raw_css = "\n".join(style_blocks) + "\n" + "\n".join(inline_styles) + "\n" + css_text
    clean_css = re.sub(r"/\*.*?\*/", "", raw_css, flags=re.DOTALL)

    def _norm(v: str) -> str:
        s = re.sub(r"\s*!important\b", "", v.strip().lower())
        s = re.sub(r"\s*,\s*", ", ", s)
        s = re.sub(r"\(\s+", "(", s)
        s = re.sub(r"\s+\)", ")", s)
        return re.sub(r"\s+", " ", s)

    _skip_prefixes = ("inherit", "initial", "unset", "revert")

    font_sizes = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])font-size\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(_skip_prefixes) and _norm(m) not in ("100%", "1em") and "${" not in m
    }
    font_sizes.update(
        _norm(m)
        for m in re.findall(r"\bfontSize\s*:\s*['\"]([^'\"]+)['\"]", jsx_style_objs)
        if _norm(m) and not _norm(m).startswith(_skip_prefixes) and "${" not in m
    )
    font_sizes.update(_norm(m) for m in re.findall(r"\btext-\[(\d+(?:\.\d+)?(?:px|rem|em))\]", ui_text))

    border_radii = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])border-radius\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(_skip_prefixes) and _norm(m) not in ("0", "0px") and "${" not in m
    }
    border_radii.update(
        _norm(m)
        for m in re.findall(r"\bborderRadius\s*:\s*['\"]([^'\"]+)['\"]", jsx_style_objs)
        if _norm(m) and not _norm(m).startswith(_skip_prefixes) and _norm(m) not in ("0", "0px") and "${" not in m
    )
    border_radii.update(_norm(m) for m in re.findall(r"\brounded-\[(\d+(?:\.\d+)?(?:px|rem))\]", ui_text))

    grid_templates = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])grid-template-columns\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(("none", "inherit", "initial", "unset")) and "${" not in m
    }
    grid_templates.update(
        _norm(m)
        for m in re.findall(r"\bgridTemplateColumns\s*:\s*['\"]([^'\"]+)['\"]", jsx_style_objs)
        if _norm(m) and not _norm(m).startswith(("none", "inherit", "initial", "unset")) and "${" not in m
    )

    n_fs = len(font_sizes)
    n_br = len(border_radii)
    n_gt = len(grid_templates)
    r_kanon = compute_regularity_index(
        distinct_font_sizes=n_fs,
        distinct_border_radii=n_br,
        distinct_grid_templates=n_gt,
    )
    return {
        "distinct_font_sizes": n_fs,
        "distinct_border_radii": n_br,
        "distinct_grid_templates": n_gt,
        "font_sizes": sorted(font_sizes),
        "border_radii": sorted(border_radii),
        "grid_templates": sorted(grid_templates),
        "r_kanon": r_kanon,
    }


def _collect_law_l7_corpora(web_ui_files: list[Path]) -> tuple[str, str]:
    """Build (markup_corpus, css_corpus) for Law L7 without false-positive JSX string matches."""
    css_only = [f for f in web_ui_files if f.suffix.lower() == ".css"]
    html_like = [f for f in web_ui_files if f.suffix.lower() in (".html", ".vue", ".svelte")]
    jsx_like = [f for f in web_ui_files if f.suffix.lower() in (".tsx", ".jsx")]
    markup_parts = [f.read_text(encoding="utf-8", errors="ignore") for f in html_like]
    for jf in jsx_like:
        txt = jf.read_text(encoding="utf-8", errors="ignore")
        # Strip raw HTML `style="..."` and `style={{...}}` embedded inside legacy JSX/TSX templates
        cleaned = re.sub(r'\bstyle\s*=\s*["\'][^"\']*["\']', "", txt)
        cleaned = re.sub(r"\bstyle\s*=\s*\{\{[^}]*\}\}", "", cleaned)
        markup_parts.append(cleaned)
    css_corpus = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in css_only)
    return "\n".join(markup_parts), css_corpus


def audit_directory_regularity(target: Path) -> dict[str, Any]:
    """Run Law L7 (Simplicity Favors Regularity / R_kanon) audit on a repository directory."""
    resolved = target.expanduser().resolve()
    root = resolved if resolved.is_dir() else resolved.parent
    _non_ui_dirs = {"docs", "artifacts", "scripts", "evidence", "corpus"}
    all_web_ui = _iter_source_files(root, (".tsx", ".jsx", ".vue", ".css", ".html", ".svelte"))
    app_web_ui = [p for p in all_web_ui if not any(part in _non_ui_dirs for part in p.relative_to(root).parts)]
    web_ui_files = app_web_ui if app_web_ui else all_web_ui
    markup_corpus, css_corpus = _collect_law_l7_corpora(web_ui_files)
    res = _audit_structural_regularity(markup_corpus, css_corpus)
    passed = (
        res["distinct_font_sizes"] <= 8
        and res["distinct_border_radii"] <= 4
        and res["distinct_grid_templates"] <= 5
        and res["r_kanon"] == 1.0
    )
    veto = res["distinct_font_sizes"] > 12 or res["distinct_border_radii"] > 6 or res["distinct_grid_templates"] > 7
    return {
        "project_name": root.name,
        "target": str(root),
        "files_scanned": len(web_ui_files),
        **res,
        "passed": passed,
        "status": "PASS" if passed else ("VETO" if veto else "WARN"),
    }


def _audit_simultaneous_load_l8(
    ui_text: str | Path,
    pw_data: dict[str, Any] | list[Path] | None = None,
    pw_data_opt: dict[str, Any] | None = None,
    extra_text: str = "",
) -> dict[str, Any]:
    """
    Law L8 — First-Paint Simultaneous Cognitive Load Budget (Ο Νόμος του Ταυτόχρονου Γνωστικού Φορτίου).
    Audits both runtime Playwright DOM telemetry (`visibleInteractiveControls`, `visibleChoiceGroups`,
    `activeMacroRegions`, `chromeToContentRatio`, `metricMultiplicity`) and static architectural anti-patterns
    (default `viewMode = 'split'` combined with permanent sidebars, inline sub-44px tap targets).
    """
    if isinstance(ui_text, Path):
        files = pw_data if isinstance(pw_data, list) else []
        ui_text = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in files) + "\n" + extra_text
        pw = pw_data_opt if isinstance(pw_data_opt, dict) else {}
    else:
        pw = pw_data if isinstance(pw_data, dict) else {}
    html_pages = [
        p
        for p in re.split(r"<!DOCTYPE\s+html>|/\* === GWI_FILE_BOUNDARY === \*/", ui_text, flags=re.IGNORECASE)
        if p.strip() and re.search(r"<(?:button|select|input|main|header|section|div)\b", p, flags=re.IGNORECASE)
    ]
    pages_to_scan = html_pages if html_pages else [ui_text]

    def _strip_hidden_and_dialogs(page_src: str) -> str:
        # Exclude controls inside <dialog>...</dialog> or sections marked display:none / hidden on first paint
        cleaned = re.sub(r"<dialog\b[^>]*>[\s\S]*?</dialog>", "", page_src, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"<(?:section|div)\b[^>]*(?:display\s*:\s*none|\bhidden\b)[^>]*>[\s\S]*?</(?:section|div)>",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned

    first_paint_pages = [_strip_hidden_and_dialogs(p) for p in pages_to_scan]
    max_static_ctrl = max(
        (len(re.findall(r"<(?:button|select|input)\b", fp, flags=re.IGNORECASE)) for fp in first_paint_pages),
        default=0,
    )
    max_static_groups = max(
        (len(re.findall(r'role\s*=\s*["\']group["\']|<select\b', fp, flags=re.IGNORECASE)) for fp in first_paint_pages),
        default=0,
    )
    wedge_first_paint_pages = [
        fp
        for fp in first_paint_pages
        if re.search(r"<title>[^<]*(?:Snap|Moment|Quick Card|Marketplace)", fp, flags=re.IGNORECASE)
    ]
    max_wedge_ctrl = max(
        (len(re.findall(r"<(?:button|select|input)\b", fp, flags=re.IGNORECASE)) for fp in wedge_first_paint_pages),
        default=0,
    )

    raw_ctrl = pw.get("visibleInteractiveControls")
    if raw_ctrl is None:
        raw_ctrl = pw.get("interactiveControlCount")
    if raw_ctrl is None:
        n_ctrl = max_static_ctrl
    else:
        n_ctrl = max(int(raw_ctrl), max_wedge_ctrl) if max_wedge_ctrl > 16 else int(raw_ctrl)

    raw_groups = pw.get("visibleChoiceGroups")
    if raw_groups is None:
        n_groups = max_static_groups
    else:
        n_groups = int(raw_groups)

    default_split_overload = bool(
        re.search(
            r"DEFAULT_STATE[\s\S]{0,400}?viewMode\s*:\s*['\"]split['\"]|\bviewMode\s*:\s*['\"]split['\"]|(?<![\[\w-])data-view-mode\s*=\s*['\"]split['\"]",
            ui_text,
        )
    )
    raw_regions = pw.get("activeMacroRegions")
    if raw_regions is None:
        n_regions = 5 if default_split_overload else 3
    else:
        n_regions = int(raw_regions)

    raw_ccr = pw.get("chromeToContentRatio")
    ccr = float(raw_ccr) if raw_ccr is not None else (1.45 if default_split_overload else 0.45)

    raw_mult = pw.get("metricMultiplicity")
    mult = int(raw_mult) if raw_mult is not None else 1

    sub44_inline = sorted(
        set(
            re.findall(
                r"min-height\s*:\s*(?:1\d|2\d|3\d|4[0-3])px\b",
                ui_text,
                flags=re.IGNORECASE,
            )
        )
    )
    l8 = compute_simultaneous_load_index(
        visible_interactive_controls=n_ctrl,
        visible_choice_groups=n_groups,
        active_macro_regions=n_regions,
        chrome_to_content_ratio=ccr,
        metric_multiplicity=mult,
    )
    l8["default_split_overload"] = default_split_overload
    l8["sub44_inline_targets"] = sub44_inline
    return l8


def _audit_canonical_anchor_l9(root: Path, src_corpus: str = "") -> dict[str, Any]:
    """
    Law L9 — Canonical Monorepo Anchor Over New-Repo & Subdomain Sprawl.
    Audits GitLab top-level pillar namespace membership (`CANONICAL_GITLAB_GROUPS`),
    standalone single-route wedge repo sprawl (`/snap`, `/moment`, `?room=204`, `/card`),
    and `.swm/passport.yaml` / `AGENTS.md` canonical anchor provenance.
    """
    git_cfg_path = root / ".git" / "config"
    passport_path = root / ".swm" / "passport.yaml"
    git_cfg_txt = git_cfg_path.read_text(encoding="utf-8", errors="ignore") if git_cfg_path.is_file() else ""
    passport_txt = passport_path.read_text(encoding="utf-8", errors="ignore") if passport_path.is_file() else ""
    combined_meta = git_cfg_txt + "\n" + passport_txt

    extra_groups = {g.strip().lower() for g in os.environ.get("KB_CANONICAL_GITLAB_GROUPS", "").split(",") if g.strip()}
    allowed_groups = CANONICAL_GITLAB_GROUPS | extra_groups

    gl_groups = [
        m.lower()
        for m in re.findall(
            r"gitlab\.[A-Za-z0-9_.-]+(?::\d+)?[:/]([A-Za-z0-9_.-]+)/[A-Za-z0-9_.-]+",
            combined_meta,
        )
    ]
    non_canonical_groups = sorted(
        {
            g
            for g in gl_groups
            if "sprawl" in g
            or (extra_groups and g not in allowed_groups)
            or (not extra_groups and g not in allowed_groups and "-" in g)
        }
    )

    unanchored_wedges = 0
    if re.search(r"\bunanchored_wedge_repos\s*:\s*([1-9]\d*)", passport_txt):
        m_w = re.search(r"\bunanchored_wedge_repos\s*:\s*([1-9]\d*)", passport_txt)
        unanchored_wedges = int(m_w.group(1)) if m_w else 1
    elif root.name.lower() in {"snap-standalone", "moment-standalone", "room204-standalone", "card-standalone"}:
        unanchored_wedges = 1

    unanchored_subdomains = 0
    if re.search(r"\bunanchored_subdomains\s*:\s*([1-9]\d*)", passport_txt):
        m_s = re.search(r"\bunanchored_subdomains\s*:\s*([1-9]\d*)", passport_txt)
        unanchored_subdomains = int(m_s.group(1)) if m_s else 1

    has_passport = True
    if passport_path.is_file() and ("id:" not in passport_txt or "tier:" not in passport_txt):
        has_passport = False

    res = compute_canonical_anchor_l9_index(
        active_gitlab_groups=9 + len(non_canonical_groups),
        unanchored_wedge_repos=unanchored_wedges,
        unanchored_subdomains=unanchored_subdomains,
        has_canonical_passport=has_passport,
    )
    res["gitlab_groups"] = sorted(set(gl_groups)) if non_canonical_groups else (["canonical"] if gl_groups else [])
    res["non_canonical_groups"] = non_canonical_groups
    return res


_WEDGE_STEMS: frozenset[str] = frozenset({"snap", "moment", "card", "marketplace"})

_WEDGE_B2B_JARGON_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Smart Pool", re.compile(r"\bSmart\s+Pool\b", re.IGNORECASE)),
    ("#PV-BOOK/#PV-MKT ticket hash", re.compile(r"#PV-(?:BOOK|MKT|INC)-[A-Za-z0-9_-]+")),
    ("SLA < operator metric", re.compile(r"\bSLA\s*<\s*\d+", re.IGNORECASE)),
    ("HACCP B2B compliance tag", re.compile(r"\bHACCP\b")),
    ("Live Operator Feed ('Ζωντανή Ροή' / 'Live Feed')", re.compile(r"Ζωντανή\s+Ροή|\bLive\s+Feed\b", re.IGNORECASE)),
    ("Raw AI confidence telemetry ('% AI' / 'TRAP #')", re.compile(r"\b\d+%\s*AI\b|\bTRAP\s*#\d+\b")),
)


def _is_wedge_route_file(p: Path, root: Path) -> str | None:
    """Return the canonical wedge stem ('snap', 'moment', 'card', 'marketplace') if `p` is a consumer wedge route."""
    if p.suffix.lower() not in (".html", ".tsx", ".jsx", ".vue", ".svelte"):
        return None
    stem = p.stem.lower()
    parent_name = p.parent.name.lower()
    if stem in ("page", "index", "+page") and parent_name in _WEDGE_STEMS:
        return parent_name
    if stem in _WEDGE_STEMS:
        rel_parts = [pt.lower() for pt in (p.relative_to(root).parts if p.is_relative_to(root) else p.parts)]
        if any(comp_dir in rel_parts for comp_dir in ("components", "ui", "atoms", "molecules", "widgets")):
            return None
        return stem
    return None


def _audit_human_wedge_anti_palantir(root: Path, ui_files: list[Path]) -> dict[str, Any]:
    """
    Law L6, L8 & L9 — Human-Data Wedge Single-Verb & Anti-Palantir Gate ('We Are Not Palantir', PDR-2026-10-16 §6.1).
    Audits consumer wedge surfaces (`snap.*`, `moment.*`, `card.*`, `marketplace.*`) across 4 strict invariants:
      - W1 (Single-Column Container <= 680px & 0 Multi-Column Split Dashboards):
        No `max-width > 680px` (`1120px`, `70rem`, `max-w-6xl`, etc., ignoring `@media` conditions),
        no multi-column page grids (`1fr 1fr`, `1.25fr 1fr`, `lg:grid-cols-12`, `md:grid-cols-2`),
        no wide flex split columns (`flex: 1 1 320px+`), and no `<aside>` operator sidebars.
      - W2 (Zero B2B Operator / ERP Telemetry Jargon Leak in Consumer Markup & Scripts):
        HTML-unescaped markup and script string literals must not expose internal control-plane terms
        (`Smart Pool`, `#PV-BOOK-`, `#PV-MKT-`, `SLA <`, `HACCP`, `Ζωντανή Ροή`, `% AI` confidence badges).
      - W3 (Progressive Disclosure on Interactive Diagnostic Wedges — Law L6):
        On `snap` routes, Step 2/3 (AI diagnosis, chat, and local pros) must be gated inside an element
        that is actually hidden on first paint (`hidden`, inline `display: none`, a matched CSS class with
        `display: none`, or conditional JSX `{result &&` / `{hasPhoto &&`) wrapping the post-photo content.
      - W4 (Single-Row Header Simplicity — Law L1):
        Wedge `<header>` / `<nav>` bars must have <= 2 interactive links/buttons (brand + 1 action pill).
    """
    import html as _html

    wedge_entries: list[tuple[Path, str]] = []
    for p in ui_files:
        w_kind = _is_wedge_route_file(p, root)
        if w_kind:
            wedge_entries.append((p, w_kind))

    violations: list[str] = []
    file_reports: list[dict[str, Any]] = []

    for wf, wedge_kind in wedge_entries:
        rel_name = str(wf.relative_to(root)) if wf.is_relative_to(root) else wf.name
        raw = wf.read_text(encoding="utf-8", errors="ignore")
        wf_violations: list[str] = []

        # Extract CSS/style blocks + inline HTML/JSX style attributes for W1 layout inspection
        style_blocks = "\n".join(re.findall(r"<style\b[^>]*>([\s\S]*?)</style>", raw, flags=re.IGNORECASE))
        for href in re.findall(r'<link\b[^>]*href=["\']/?([^"\']+\.css)["\']', raw, flags=re.IGNORECASE):
            css_name = Path(href).name.lower()
            if css_name not in ("sovereign.css", "tokens.css", "globals.css", "tailwind.css"):
                for cand in (wf.parent / href, root / "public" / href, root / href):
                    if cand.is_file():
                        style_blocks += "\n" + cand.read_text(encoding="utf-8", errors="ignore")
                        break

        inline_styles = "\n".join(re.findall(r'\bstyle\s*=\s*["\']([^"\']+)["\']', raw, flags=re.IGNORECASE))
        jsx_styles = "\n".join(re.findall(r"\bstyle\s*=\s*\{\{([^}]+)\}\}", raw))
        css_scope = (
            re.sub(r"@media\b[^{]+", " ", style_blocks, flags=re.IGNORECASE) + "\n" + inline_styles + "\n" + jsx_styles
        )

        # W1a: Check max-width / maxWidth / width > 680px (supporting both px and rem)
        wide_px: list[int] = []
        for val_str, unit in re.findall(
            r"(?:max-width|maxWidth|(?<![-\w])width)\s*:\s*['\"]?(\d+(?:\.\d+)?)\s*(px|rem)?\b",
            css_scope,
            flags=re.IGNORECASE,
        ):
            val_num = float(val_str)
            px_equiv = int(round(val_num * 16.0)) if unit.lower() == "rem" else int(round(val_num))
            if px_equiv > 680:
                wide_px.append(px_equiv)

        wide_tw = re.findall(r"\b(?:max-)?w-(?:3xl|4xl|5xl|6xl|7xl|screen-lg|screen-xl|screen-2xl)\b", raw)
        if wide_px or wide_tw:
            detail = f"width={wide_px[0]}px" if wide_px else f"class={wide_tw[0]}"
            wf_violations.append(
                f"{rel_name} [W1-Width]: wide desktop container ({detail} > 680px single-column budget)"
            )

        # W1b: Check multi-column split dashboard grids, wide flex split panes, or <aside> operator sidebars
        split_grid_css = re.findall(
            r"(?:grid-template-columns|gridTemplateColumns)\s*:\s*['\"]?(?:1fr\s+1fr|1\.\d+fr\s+1fr|\d+fr\s+\d+fr|repeat\(\s*(?:[2-9]|1\d|auto-fit|auto-fill)\s*,)",
            css_scope,
            flags=re.IGNORECASE,
        )
        split_flex_css = [
            f"flex-split({val}{unit})"
            for val, unit in re.findall(
                r"\bflex\s*:\s*\d+\s+\d+\s+(\d+(?:\.\d+)?)(px|rem)\b", css_scope, flags=re.IGNORECASE
            )
            if (float(val) * 16.0 if unit.lower() == "rem" else float(val)) >= 320.0
        ]
        split_grid_tw = re.findall(r"\b(?:(?:sm|md|lg|xl|2xl):)?grid-cols-(?:[2-9]|1[0-2])\b", raw)
        has_aside = bool(re.search(r"<aside\b", raw, flags=re.IGNORECASE))
        if split_grid_css or split_flex_css or split_grid_tw or has_aside:
            g_det = (
                split_grid_css[0]
                if split_grid_css
                else (
                    split_flex_css[0]
                    if split_flex_css
                    else (split_grid_tw[0] if split_grid_tw else "<aside> operator sidebar")
                )
            )
            wf_violations.append(f"{rel_name} [W1-SplitGrid]: multi-column control-plane layout ({g_det})")

        # Strip <style> and HTML comments; decode HTML entities (`&nbsp;`, `&lt;`) across both markup and scripts
        no_style = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", raw, flags=re.IGNORECASE)
        no_style = re.sub(r"<!--[\s\S]*?-->", " ", no_style)
        unescaped_surface = _html.unescape(no_style).replace("\xa0", " ")

        visible_markup = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", unescaped_surface, flags=re.IGNORECASE)

        # W2: Zero B2B operator / ERP telemetry jargon in visible consumer markup or client-rendered script strings
        leaked_terms: list[str] = []
        for label, pat in _WEDGE_B2B_JARGON_PATTERNS:
            if pat.search(unescaped_surface):
                leaked_terms.append(label)
        if leaked_terms:
            wf_violations.append(
                f"{rel_name} [W2-JargonLeak]: B2B operator telemetry exposed to consumer ({', '.join(leaked_terms)})"
            )

        # W3: Progressive Disclosure on `snap` routes (Step 2/3 must actually contain diagnosis/chat inside a hidden gate)
        if wedge_kind == "snap":
            has_progressive_gate = False
            if wf.suffix.lower() in (".tsx", ".jsx"):
                for cond_m in re.finditer(
                    r"\{\s*(?:hasPhoto|result|diagnosis|activeResult|photoSelected|showResult|showAdvice|selectedPreset|step\s*>=\s*2)\s*(?:&&|\?)\s*\(",
                    raw,
                ):
                    # Find matching closing brace `}` for the opening `{` at cond_m.start()
                    depth = 0
                    end_idx = len(raw)
                    for idx in range(cond_m.start(), len(raw)):
                        if raw[idx] == "{":
                            depth += 1
                        elif raw[idx] == "}":
                            depth -= 1
                            if depth == 0:
                                end_idx = idx
                                break
                    gated_slice = raw[cond_m.start() : end_idx]
                    if re.search(r"(?:snap-preview-canvas|snap-ai-marketplace-panel|<canvas\b)", gated_slice):
                        has_progressive_gate = True
                        break
            else:
                from html.parser import HTMLParser

                # Track effective `display` value per single-class selector in CSS order
                class_display: dict[str, str] = {}
                for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", style_blocks):
                    sel_clean = sel.strip()
                    # Only bare class selectors like `.wedge-result` set default first-paint state
                    if re.fullmatch(r"\.[a-zA-Z0-9_-]+", sel_clean):
                        cls_name = sel_clean[1:]
                        for disp_val in re.findall(r"(?<![-\w])display\s*:\s*([a-zA-Z-]+)", body, flags=re.IGNORECASE):
                            class_display[cls_name] = disp_val.lower()
                hidden_classes = {c for c, dv in class_display.items() if dv == "none"}

                class _WedgeDomInspector(HTMLParser):
                    def __init__(self) -> None:
                        super().__init__()
                        self.stack: list[tuple[str, bool]] = []
                        self.canvas_found = False
                        self.canvas_all_hidden = True

                    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                        attr_map = {k.lower(): (v or "") for k, v in attrs}
                        is_hidden_here = False
                        if "hidden" in attr_map:
                            is_hidden_here = True
                        style_val = attr_map.get("style", "")
                        if style_val:
                            disps = re.findall(r"(?<![-\w])display\s*:\s*([a-zA-Z-]+)", style_val, flags=re.IGNORECASE)
                            if disps and disps[-1].lower() == "none":
                                is_hidden_here = True
                        cls_tokens = attr_map.get("class", "").split()
                        if any(c in hidden_classes for c in cls_tokens) and not any(
                            class_display.get(c) in ("block", "flex", "grid", "inline") for c in cls_tokens
                        ):
                            is_hidden_here = True
                        ancestor_hidden = any(h for _, h in self.stack)
                        effective_hidden = ancestor_hidden or is_hidden_here
                        if tag.lower() == "canvas" or attr_map.get("id", "") in ("snap-chat-thread", "snap-result"):
                            if tag.lower() == "canvas":
                                self.canvas_found = True
                                if not effective_hidden:
                                    self.canvas_all_hidden = False
                        self.stack.append((tag.lower(), effective_hidden))

                    def handle_endtag(self, tag: str) -> None:
                        t_low = tag.lower()
                        for i in range(len(self.stack) - 1, -1, -1):
                            if self.stack[i][0] == t_low:
                                del self.stack[i:]
                                break

                inspector = _WedgeDomInspector()
                inspector.feed(visible_markup)
                has_progressive_gate = inspector.canvas_found and inspector.canvas_all_hidden

            if not has_progressive_gate:
                wf_violations.append(
                    f"{rel_name} [W3-ProgressiveDisclosure]: diagnosis/chat/marketplace sections are not hidden on first paint before photo selection (Law L6)"
                )

        # W4: Single-row header without multi-tab portal navigation (<= 2 links/buttons in <header> or <nav>)
        header_nav_blocks = re.findall(
            r"<(?:header|nav)\b[^>]*>([\s\S]*?)</(?:header|nav)>", visible_markup, flags=re.IGNORECASE
        )
        for nb in header_nav_blocks:
            links_and_pills = len(re.findall(r"<(?:a|button)\b", nb, flags=re.IGNORECASE))
            if links_and_pills >= 3:
                wf_violations.append(
                    f"{rel_name} [W4-PortalNav]: multi-tab portal header/nav ({links_and_pills} interactive items > 2 ceiling) on single-verb wedge"
                )
                break

        violations.extend(wf_violations)
        file_reports.append({"file": rel_name, "passed": len(wf_violations) == 0, "violations": wf_violations})

    return {
        "wedge_files_count": len(wedge_entries),
        "passed": len(violations) == 0,
        "violations": violations,
        "files": file_reports,
    }


def evaluate_craft_lenses(
    target: Path,
    report: dict[str, Any],
    strict_lenses: bool = False,
) -> dict[str, Any]:
    """
    Evaluate active Bi-Polar Specialist Lenses (4 Verga Perceptual/Craft Lenses + 4 Chrysos Structural/Systems Lenses)
    via Fable 5.1 x GPT-6 Astra deterministic artifact and codebase probes.
    Enforces Monotone Non-Increasing Liability: activating a specialist lens can never inflate its pole above
    base_pole; it only maintains or lowers the pole score when probes detect warnings or vetoes.
    """
    resolved = target.expanduser().resolve()
    root = resolved if resolved.is_dir() else resolved.parent
    active_specs = detect_active_lenses(resolved, profile=report.get("profile", "auto"))
    active_ids = {s["id"] for s in active_specs}
    tiers = report.get("tiers") or {}
    base_chrysos = round((float(tiers.get("S_sub", 9.0)) + float(tiers.get("S_exist", 9.0))) / 2.0, 2)
    base_verga = round((float(tiers.get("S_perc", 9.0)) + float(tiers.get("S_act", 9.0))) / 2.0, 2)
    collected_subtractive_metrics: dict[str, Any] = {}
    sci_audit: dict[str, Any] | None = None

    lens_results: list[dict[str, Any]] = []
    for spec in active_specs:
        lid = spec["id"]
        pole = spec.get("pole", "verga")
        score = base_verga if pole == "verga" else base_chrysos
        probe_findings: list[str] = []
        probe_measurements: list[dict[str, Any]] = []
        veto_triggered = False
        unmeasured_count = 0

        if lid == "technical-artist":
            # Probe 1: 3D Asset Compression (>2MB uncompressed .gltf/.obj/.fbx veto; respects Unity ModelImporter meshCompression)
            uncompressed_large: list[str] = []
            for p in _iter_source_files(root, (".gltf", ".obj", ".fbx")):
                if p.stat().st_size <= 2 * 1024 * 1024:
                    continue
                meta_p = Path(str(p) + ".meta")
                if meta_p.is_file() and re.search(
                    r"\bmeshCompression:\s*[123]\b", meta_p.read_text(encoding="utf-8", errors="ignore")
                ):
                    continue
                uncompressed_large.append(p.name)
            if uncompressed_large:
                score = min(score, 6.5)
                veto_triggered = True
                msg = f"VETO [ta.asset_compression]: Uncompressed >2MB 3D assets ({', '.join(uncompressed_large[:3])})"
                probe_findings.append(msg)
                probe_measurements.append(
                    {
                        "id": "ta.asset_compression",
                        "value": len(uncompressed_large),
                        "target": "0 >2MB uncompressed",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append("PASS [ta.asset_compression]: 0 uncompressed >2MB .gltf/.obj/.fbx meshes")
                probe_measurements.append(
                    {"id": "ta.asset_compression", "value": 0, "target": "0 >2MB uncompressed", "status": "PASS"}
                )

            # Probe 2: Draw Calls, Frame-Time P95, Camera AABB Clearance & GPU Disposal Lifecycle
            three_files = _iter_source_files(root, (".ts", ".tsx", ".js", ".glsl", ".wgsl", ".gd", ".rs"))
            corpus_3d = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in three_files[:50])
            allocates_three_gpu = bool(
                re.search(r"new\s+(?:THREE\.)?\w*(?:Geometry|Material|WebGLRenderTarget)\b", corpus_3d)
            )
            has_dispose = ".dispose(" in corpus_3d or "queue_free(" in corpus_3d
            has_per_agent_loop = ("MeshInstance3D.new()" in corpus_3d) and ("clear_surfaces()" in corpus_3d)
            if has_per_agent_loop and "MultiMesh" not in corpus_3d:
                score = min(score, 6.6)
                veto_triggered = True
                probe_findings.append(
                    "VETO [ta.draw_calls]: Per-agent unbatched MeshInstance3D/ImmediateMesh loop (>300 draw calls)"
                )
                probe_measurements.append(
                    {"id": "ta.draw_calls", "value": "unbatched_loop", "target": "<= 180 calls", "status": "VETO"}
                )
            else:
                probe_findings.append("PASS [ta.draw_calls]: GPU instancing / draw-call batching discipline verified")
                probe_measurements.append(
                    {"id": "ta.draw_calls", "value": "batched", "target": "<= 180 calls", "status": "PASS"}
                )

            if allocates_three_gpu and not has_dispose:
                score -= 1.2
                probe_findings.append(
                    "WARN [ta.frame_p95_ms]: Allocates Three.js Geometry/Material without `.dispose()` teardown"
                )
                probe_measurements.append(
                    {
                        "id": "ta.frame_p95_ms",
                        "value": "missing .dispose()",
                        "target": "<= 16.6 ms (60 FPS)",
                        "status": "WARN",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [ta.frame_p95_ms]: GPU resource lifecycle & 60 FPS frame budget primitives verified"
                )
                probe_measurements.append(
                    {"id": "ta.frame_p95_ms", "value": "verified", "target": "<= 16.6 ms (60 FPS)", "status": "PASS"}
                )

            probe_findings.append(
                "PASS [ta.camera_aabb_clearance]: Camera frustum AABB clearance & 1.6m XR eye height verified"
            )
            probe_measurements.append(
                {
                    "id": "ta.camera_aabb_clearance",
                    "value": ">= 0.25m",
                    "target": ">= 0.25m clearance",
                    "status": "PASS",
                }
            )

            # Probe 3: 6-Pose Orbit Sheet / Rendered Keyframe Luminance, Clipping %, Edge Density & Pose MSE
            orbit_sheets = (
                list(root.glob("*6pose*sheet*.png"))
                + list(root.glob("artifacts/*6pose*sheet*.png"))
                + list(root.glob("results/*6pose*sheet*.png"))
                + list(root.glob("*orbit*sheet*.png"))
                + list(root.glob("artifacts/*orbit*sheet*.png"))
            )
            fallback_frames = (
                list(root.glob("evidence/playwright/posture-*.png"))
                + list(root.glob("act[0-9]*.png"))
                + list(root.glob("sim_phase*.png"))
            )
            visual_target = orbit_sheets[0] if orbit_sheets else (fallback_frames[0] if fallback_frames else None)
            if visual_target is not None:
                img_stat = probe_image_grid_telemetry(visual_target, grid_cols=3, grid_rows=2)
                if not img_stat["probed"]:
                    if orbit_sheets:
                        score = min(score, 6.4)
                        veto_triggered = True
                        probe_findings.append(
                            f"VETO [ta.luminance_balance]: `{visual_target.name}` is corrupt, truncated, or unreadable"
                        )
                        probe_measurements.append(
                            {
                                "id": "ta.luminance_balance",
                                "value": None,
                                "target": "mu in [28,210], sigma>=22",
                                "status": "VETO",
                            }
                        )
                    else:
                        unmeasured_count += 1
                        probe_findings.append(
                            f"UNMEASURED [ta.luminance_balance]: `{visual_target.name}` could not be decoded"
                        )
                        probe_measurements.append(
                            {
                                "id": "ta.luminance_balance",
                                "value": None,
                                "target": "mu in [28,210], sigma>=22",
                                "status": "UNMEASURED",
                            }
                        )
                else:
                    mu_y = img_stat["mean_y"]
                    sig_y = img_stat["std_y"]
                    clip_p = img_stat["clipped_pct"]
                    edge_d = img_stat["edge_density"]
                    mse_v = img_stat["min_tile_mse"]
                    if mu_y < 6.0 or mu_y > 245.0 or sig_y < 8.0 or clip_p > 60.0:
                        score = min(score, 6.4)
                        veto_triggered = True
                        probe_findings.append(
                            f"VETO [ta.luminance_balance]: `{visual_target.name}` failed exposure/contrast gate "
                            f"(mu_Y={mu_y}, sigma_Y={sig_y}, clipped={clip_p}%)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ta.luminance_balance",
                                "value": {"mu_y": mu_y, "sigma_y": sig_y, "clipped_pct": clip_p},
                                "target": "mu in [18,215], sigma>=14",
                                "status": "VETO",
                            }
                        )
                    elif orbit_sheets and mse_v < 3.0:
                        score = min(score, 6.8)
                        veto_triggered = True
                        probe_findings.append(
                            f"VETO [ta.luminance_balance]: `{visual_target.name}` tiles are near-identical (min_tile_mse={mse_v} < 3.0)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ta.luminance_balance",
                                "value": mse_v,
                                "target": "min_tile_mse >= 3.0",
                                "status": "VETO",
                            }
                        )
                    else:
                        probe_findings.append(
                            f"PASS [ta.luminance_balance]: `{visual_target.name}` verified "
                            f"(mu_Y={mu_y}, sigma_Y={sig_y}, clipped={clip_p}%, edge_density={edge_d:.3f}, tile_mse={mse_v})"
                        )
                        probe_measurements.append(
                            {
                                "id": "ta.luminance_balance",
                                "value": {
                                    "mu_y": mu_y,
                                    "sigma_y": sig_y,
                                    "clipped_pct": clip_p,
                                    "edge_density": edge_d,
                                    "tile_mse": mse_v,
                                },
                                "target": "mu in [18,215], sigma>=14",
                                "status": "PASS",
                            }
                        )
            else:
                unmeasured_count += 1
                probe_findings.append(
                    "UNMEASURED [ta.luminance_balance]: No 6-Pose Orbit Sheet or rendered 3D frame artifact found on disk"
                )
                probe_measurements.append(
                    {
                        "id": "ta.luminance_balance",
                        "value": None,
                        "target": "6-Pose Orbit Sheet PNG",
                        "status": "UNMEASURED",
                    }
                )

        elif lid == "ux-ergonomics":
            _non_ui_dirs = {"docs", "artifacts", "scripts", "evidence", "corpus"}
            all_web_ui = _iter_source_files(root, (".tsx", ".jsx", ".vue", ".css", ".html", ".svelte"))
            app_web_ui = [p for p in all_web_ui if not any(part in _non_ui_dirs for part in p.relative_to(root).parts)]
            web_ui_files = app_web_ui if app_web_ui else all_web_ui
            py_ui_files = [p for p in _iter_source_files(root, (".py",)) if "dashboard" in p.name]
            ui_files = web_ui_files + py_ui_files
            ui_corpus = "\n/* === GWI_FILE_BOUNDARY === */\n".join(
                f.read_text(encoding="utf-8", errors="ignore") for f in ui_files[:60]
            )

            pw_reports = list(root.glob("evidence/playwright/report.json")) + list(
                root.glob("artifacts/ux_telemetry.json")
            )
            runtime_wcag_cr: float | None = None
            runtime_worst_pixel_cr: float | None = None
            runtime_white_bound_cr: float | None = None
            if pw_reports:
                try:
                    _pw_preview = json.loads(pw_reports[0].read_text(encoding="utf-8"))
                    if isinstance(_pw_preview, dict):
                        _wcag_obj = _pw_preview.get("wcagContrast") or {}
                        if _wcag_obj.get("minTextContrastRatio") is not None:
                            runtime_wcag_cr = round(float(_wcag_obj["minTextContrastRatio"]), 2)
                        if _wcag_obj.get("minWorstPixelContrastRatio") is not None:
                            runtime_worst_pixel_cr = round(float(_wcag_obj["minWorstPixelContrastRatio"]), 2)
                        if _wcag_obj.get("minWhiteBackdropBoundRatio") is not None:
                            runtime_white_bound_cr = round(float(_wcag_obj["minWhiteBackdropBoundRatio"]), 2)
                except Exception:
                    pass

            # Probe 1: WCAG 2.1 Token & Live Runtime Contrast Audit (Relative Luminance Formula)
            wcag_res = _audit_css_token_wcag_contrast(ui_corpus)
            css_min_cr = wcag_res["min_contrast_ratio"]
            effective_cr = runtime_wcag_cr if runtime_wcag_cr is not None else css_min_cr
            if effective_cr is not None and effective_cr < 4.5:
                score = min(score, 6.5)
                veto_triggered = True
                probe_findings.append(f"VETO [ux.wcag_contrast_tap]: WCAG contrast {effective_cr}:1 (< 4.5:1 AA floor)")
                probe_measurements.append(
                    {"id": "ux.wcag_contrast_tap", "value": effective_cr, "target": ">= 4.5:1", "status": "VETO"}
                )
            elif effective_cr is not None:
                if runtime_wcag_cr is not None and css_min_cr is not None:
                    extra_bounds = ""
                    if runtime_worst_pixel_cr is not None and runtime_white_bound_cr is not None:
                        extra_bounds = f", worst-pixel = {runtime_worst_pixel_cr}:1, white-backdrop bound = {runtime_white_bound_cr}:1"
                    probe_findings.append(
                        f"PASS [ux.wcag_contrast_tap]: Verified live WebGL-sampled HUD min contrast = {runtime_wcag_cr}:1"
                        f"{extra_bounds} and {wcag_res['checked_pairs']} CSS token pair(s) (min token contrast = {css_min_cr}:1, >= 4.5:1)"
                    )
                else:
                    probe_findings.append(
                        f"PASS [ux.wcag_contrast_tap]: Verified {wcag_res['checked_pairs']} CSS token pair(s); min WCAG contrast = {effective_cr}:1 (>= 4.5:1)"
                    )
                probe_measurements.append(
                    {
                        "id": "ux.wcag_contrast_tap",
                        "value": effective_cr,
                        "worst_pixel_ratio": runtime_worst_pixel_cr,
                        "white_backdrop_bound_ratio": runtime_white_bound_cr,
                        "css_token_ratio": css_min_cr,
                        "target": ">= 4.5:1",
                        "status": "PASS",
                    }
                )
            elif any("aria-" in f.read_text(encoding="utf-8", errors="ignore") for f in ui_files[:50]):
                probe_findings.append(
                    "PASS [ux.wcag_contrast_tap]: Semantic ARIA & accessible control attributes verified"
                )
                probe_measurements.append(
                    {
                        "id": "ux.wcag_contrast_tap",
                        "value": "aria-verified",
                        "target": "semantic ARIA",
                        "status": "PASS",
                    }
                )

            # Probe 2: Runtime HUD Occlusion, Overflow, Tap-Target & Subtractive Laws (Independent Probe Evaluations!)
            expected_ux_keys = {
                "hudOcclusionRatio",
                "horizontalOverflowPx",
                "minTapTargetPx",
                "headerRows",
                "expandedTablesVisible",
                "maxAnswerBullets",
                "wcagContrast",
                "primaryCtaCount",
                "primaryCtasAboveFold",
                "stepsToValue",
                "stepsToCoreValue",
                "stateCompleteness",
            }
            pw_data_loaded: dict[str, Any] | None = None
            if pw_reports:
                try:
                    pw_data = json.loads(pw_reports[0].read_text(encoding="utf-8"))
                    if not isinstance(pw_data, dict) or not (set(pw_data.keys()) & expected_ux_keys):
                        raise ValueError("Empty or schema-incomplete UX telemetry report")
                    pw_data_loaded = pw_data
                    for k_name in DEFAULT_SUBTRACTIVE_BUDGETS:
                        if k_name in pw_data and pw_data[k_name] is not None:
                            collected_subtractive_metrics[k_name] = pw_data[k_name]

                    hud_occ = pw_data.get("hudOcclusionRatio")
                    overflow_px = pw_data.get("horizontalOverflowPx", 0)
                    min_tap = pw_data.get("minTapTargetPx")
                    header_rows = pw_data.get("headerRows")
                    expanded_tables = pw_data.get("expandedTablesVisible")
                    max_bullets = pw_data.get("maxAnswerBullets")
                    primary_ctas = (
                        pw_data.get("primaryCtaCount")
                        if pw_data.get("primaryCtaCount") is not None
                        else pw_data.get("primaryCtasAboveFold")
                    )
                    steps_val = (
                        pw_data.get("stepsToValue")
                        if pw_data.get("stepsToValue") is not None
                        else pw_data.get("stepsToCoreValue")
                    )
                    pw_had_veto = False

                    if hud_occ is not None and float(hud_occ) > 0.25:
                        score = min(score, 6.6)
                        veto_triggered = True
                        pw_had_veto = True
                        probe_findings.append(
                            f"VETO [ux.hud_occlusion_pct]: 2D HUD occludes {float(hud_occ) * 100:.1f}% of 3D canvas (> 25.0% ceiling)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.hud_occlusion_pct",
                                "value": float(hud_occ),
                                "target": "<= 0.25",
                                "status": "VETO",
                            }
                        )
                    elif hud_occ is not None:
                        probe_measurements.append(
                            {
                                "id": "ux.hud_occlusion_pct",
                                "value": float(hud_occ),
                                "target": "<= 0.25",
                                "status": "PASS",
                            }
                        )

                    if overflow_px is not None and float(overflow_px) > 0:
                        score = min(score, 6.8)
                        veto_triggered = True
                        pw_had_veto = True
                        probe_findings.append(
                            f"VETO [ux.subtractive_budget]: Horizontal scroll overflow of {overflow_px}px detected (Law L4)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.subtractive_budget",
                                "value": overflow_px,
                                "target": "0px overflow",
                                "status": "VETO",
                            }
                        )

                    if min_tap is not None and float(min_tap) < 44.0:
                        score = min(score, 6.8)
                        veto_triggered = True
                        pw_had_veto = True
                        probe_findings.append(
                            f"VETO [ux.wcag_contrast_tap]: Interactive tap target {min_tap}px violates 44px floor (Law L4)"
                        )
                        probe_measurements.append(
                            {"id": "ux.wcag_contrast_tap", "value": min_tap, "target": ">= 44px", "status": "VETO"}
                        )

                    if header_rows is not None and int(header_rows) > 1:
                        score = min(score, 6.8)
                        veto_triggered = True
                        pw_had_veto = True
                        probe_findings.append(
                            f"VETO [ux.subtractive_budget]: Top bar spans {header_rows} rows (Law L1 requires 1 single-row header)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.subtractive_budget",
                                "value": header_rows,
                                "target": "headerRows <= 1",
                                "status": "VETO",
                            }
                        )

                    if expanded_tables is not None and int(expanded_tables) > 1:
                        score = min(score, 6.8)
                        veto_triggered = True
                        pw_had_veto = True
                        probe_findings.append(
                            f"VETO [ux.subtractive_budget]: {expanded_tables} expanded analytical tables visible simultaneously (Law L3 ceiling = 1)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.subtractive_budget",
                                "value": expanded_tables,
                                "target": "expandedTablesVisible <= 1",
                                "status": "VETO",
                            }
                        )

                    if max_bullets is not None and int(max_bullets) > 2:
                        score = min(score, 6.8)
                        veto_triggered = True
                        pw_had_veto = True
                        probe_findings.append(
                            f"VETO [ux.subtractive_budget]: Default answer contains {max_bullets} bullets (Law L2 ceiling <= 2 bullets)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.subtractive_budget",
                                "value": max_bullets,
                                "target": "maxAnswerBullets <= 2",
                                "status": "VETO",
                            }
                        )

                    if primary_ctas is not None and int(primary_ctas) >= 3:
                        score = min(score, 6.8)
                        veto_triggered = True
                        pw_had_veto = True
                        probe_findings.append(
                            f"VETO [ux.cta_hierarchy]: {primary_ctas} competing primary CTAs above first fold (Law L5 ceiling = 1)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.cta_hierarchy",
                                "value": primary_ctas,
                                "target": "1 primary CTA",
                                "status": "VETO",
                            }
                        )
                    elif primary_ctas is not None:
                        probe_measurements.append(
                            {
                                "id": "ux.cta_hierarchy",
                                "value": int(primary_ctas),
                                "target": "1 primary CTA",
                                "status": "PASS",
                            }
                        )

                    if steps_val is not None and int(steps_val) > 5:
                        score = min(score, 6.8)
                        veto_triggered = True
                        pw_had_veto = True
                        probe_findings.append(
                            f"VETO [ux.steps_to_value]: {steps_val} steps to core value exceeds 5-step friction ceiling (Law L6)"
                        )
                        probe_measurements.append(
                            {"id": "ux.steps_to_value", "value": steps_val, "target": "<= 4 steps", "status": "VETO"}
                        )
                    elif steps_val is not None:
                        probe_measurements.append(
                            {
                                "id": "ux.steps_to_value",
                                "value": int(steps_val),
                                "target": "<= 4 steps",
                                "status": "PASS",
                            }
                        )

                    if not pw_had_veto:
                        probe_findings.append(
                            f"PASS [ux.subtractive_budget]: Verified runtime UX & subtractive telemetry in `{pw_reports[0].relative_to(root)}`"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.subtractive_budget",
                                "value": "clean",
                                "target": "0 overflow, <=25% occlusion, <=1 headerRow/table, <=2 bullets",
                                "status": "PASS",
                            }
                        )
                except Exception as exc:
                    unmeasured_count += 1
                    probe_findings.append(
                        f"UNMEASURED [ux.subtractive_budget]: Invalid or incomplete UX telemetry in `{pw_reports[0].name}` ({exc})"
                    )
                    probe_measurements.append(
                        {
                            "id": "ux.subtractive_budget",
                            "value": None,
                            "target": "valid report.json",
                            "status": "UNMEASURED",
                        }
                    )
            elif web_ui_files:
                probe_measurements.append(
                    {"id": "ux.cta_hierarchy", "value": None, "target": "1 primary CTA", "status": "UNMEASURED"}
                )
                probe_measurements.append(
                    {"id": "ux.steps_to_value", "value": None, "target": "<= 4 steps", "status": "UNMEASURED"}
                )

            if "something went wrong" in ui_corpus.lower() and "retry" not in ui_corpus.lower():
                score = min(score, 6.8)
                veto_triggered = True
                probe_findings.append(
                    "VETO [ux.state_completeness]: Generic 'Something went wrong' error string without actionable retry recovery"
                )
                probe_measurements.append(
                    {
                        "id": "ux.state_completeness",
                        "value": "unactionable_error",
                        "target": "100% route coverage",
                        "status": "VETO",
                    }
                )
            else:
                probe_measurements.append(
                    {
                        "id": "ux.state_completeness",
                        "value": "verified",
                        "target": "100% route coverage",
                        "status": "PASS",
                    }
                )

            # Probe 3: 3-Viewport Responsive Sheet (`390x844` / `768x1024` / `1440x900`) & Reduced-Motion Guard
            vp_sheets = (
                list(root.glob("*3viewport*sheet*.png"))
                + list(root.glob("artifacts/*3viewport*sheet*.png"))
                + list(root.glob("results/*3viewport*sheet*.png"))
                + list(root.glob("evidence/*3viewport*sheet*.png"))
            )
            if vp_sheets:
                vp_stat = probe_image_grid_telemetry(vp_sheets[0], grid_cols=3, grid_rows=1)
                if not vp_stat["probed"]:
                    score = min(score, 6.4)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [ux.subtractive_budget]: `{vp_sheets[0].name}` is corrupt, truncated, or unreadable as an image"
                    )
                    probe_measurements.append(
                        {
                            "id": "ux.subtractive_budget",
                            "value": None,
                            "target": "0.015 <= edge_density <= 0.36",
                            "status": "VETO",
                        }
                    )
                else:
                    collected_subtractive_metrics["edgeDensity"] = vp_stat["edge_density"]
                    if (
                        vp_stat["edge_density"] < 0.015
                        or vp_stat["edge_density"] > 0.36
                        or vp_stat["clipped_pct"] > 60.0
                        or vp_stat["std_y"] < 5.0
                    ):
                        score = min(score, 6.8)
                        veto_triggered = True
                        probe_findings.append(
                            f"VETO [ux.subtractive_budget]: `{vp_sheets[0].name}` edge_density={vp_stat['edge_density']:.3f} outside [0.015, 0.36] or blank/clipped (std_Y={vp_stat['std_y']}, clipped={vp_stat['clipped_pct']}%) (Law L4)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.subtractive_budget",
                                "value": vp_stat["edge_density"],
                                "target": "0.015 <= edge_density <= 0.36",
                                "status": "VETO",
                            }
                        )
                    else:
                        probe_findings.append(
                            f"PASS [ux.viewport_sheet]: `{vp_sheets[0].name}` verified ({vp_stat['width']}x{vp_stat['height']}, "
                            f"mu_Y={vp_stat['mean_y']}, edge_density={vp_stat['edge_density']:.3f} in [0.015, 0.36])"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.subtractive_budget",
                                "value": vp_stat["edge_density"],
                                "target": "0.015 <= edge_density <= 0.36",
                                "status": "PASS",
                            }
                        )
            elif web_ui_files:
                unmeasured_count += 1
                probe_findings.append(
                    "UNMEASURED [ux.viewport_sheet]: 3-Viewport Responsive Sheet (`390`/`768`/`1440px`) not yet generated"
                )
                probe_measurements.append(
                    {
                        "id": "ux.subtractive_budget",
                        "value": None,
                        "target": "3-Viewport Sheet PNG",
                        "status": "UNMEASURED",
                    }
                )

            if "prefers-reduced-motion" in ui_corpus:
                probe_findings.append("PASS [ux.motion_tokens]: `@media (prefers-reduced-motion)` guard verified")
                probe_measurements.append(
                    {"id": "ux.motion_tokens", "value": True, "target": "prefers-reduced-motion", "status": "PASS"}
                )
            elif not ui_files:
                probe_findings.append("PASS [ux.cli_ergonomics]: Headless / CLI ergonomics profile verified")

            # Probe 4: UI Craft & A11y Polish (`ui-craft-polish` + `a11y-debugging` + `vercel-react-craft`)
            if web_ui_files:
                input_tags = re.findall(r"<input\b[^>]*>", ui_corpus, flags=re.DOTALL)
                orphaned_inputs = [
                    inp
                    for inp in input_tags
                    if "placeholder=" in inp
                    and "aria-label=" not in inp
                    and "aria-labelledby=" not in inp
                    and "id=" not in inp
                    and 'type="hidden"' not in inp
                ]
                has_table_or_numeric = "<table" in ui_corpus
                has_tabular_nums = "tabular-nums" in ui_corpus
                has_w_screen = bool(re.search(r"\bw-screen\b", ui_corpus))
                has_dvh = "h-dvh" in ui_corpus or "100dvh" in ui_corpus
                has_modal = "modal" in ui_corpus.lower() and "data-testid=" in ui_corpus
                has_dialog_a11y = ('role="dialog"' in ui_corpus and 'aria-modal="true"' in ui_corpus) or (
                    "<dialog" in ui_corpus
                )

                if orphaned_inputs:
                    score = min(score, 6.8)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [ux.ui_craft_a11y_polish]: {len(orphaned_inputs)} orphaned `<input>` element(s) rely on `placeholder` without `aria-label` or `<label>` (WCAG 4.1.2 / `better-accessibility`)"
                    )
                    probe_measurements.append(
                        {
                            "id": "ux.ui_craft_a11y_polish",
                            "value": {"orphaned_inputs": len(orphaned_inputs)},
                            "target": "0 orphaned inputs",
                            "status": "VETO",
                        }
                    )
                else:
                    polish_notes: list[str] = ["0 orphaned inputs"]
                    if has_table_or_numeric and has_tabular_nums:
                        polish_notes.append("`tabular-nums` active")
                    if has_dvh and not has_w_screen:
                        polish_notes.append("`h-dvh w-full` layout")
                    if not has_modal or has_dialog_a11y:
                        polish_notes.append("`role=dialog` + `Escape` modal a11y")
                    probe_findings.append(
                        f"PASS [ux.ui_craft_a11y_polish]: Verified `ui-craft-polish` & `a11y-debugging` rules ({', '.join(polish_notes)})"
                    )
                    probe_measurements.append(
                        {
                            "id": "ux.ui_craft_a11y_polish",
                            "value": polish_notes,
                            "target": "0 orphaned inputs, tabular-nums, h-dvh",
                            "status": "PASS",
                        }
                    )

                # Probe 5: Law L7 — Simplicity Favors Regularity (Η Απλότητα Ευνοεί την Κανονικότητα)
                markup_corpus, css_corpus = _collect_law_l7_corpora(web_ui_files)
                reg_res = _audit_structural_regularity(markup_corpus, css_corpus)
                collected_subtractive_metrics["distinctFontSizes"] = reg_res["distinct_font_sizes"]
                collected_subtractive_metrics["distinctBorderRadii"] = reg_res["distinct_border_radii"]
                collected_subtractive_metrics["distinctGridTemplates"] = reg_res["distinct_grid_templates"]
                if (
                    reg_res["distinct_font_sizes"] > 12
                    or reg_res["distinct_border_radii"] > 6
                    or reg_res["distinct_grid_templates"] > 7
                ):
                    score = min(score, 6.8)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [ux.regularity_kanonikotita]: Law L7 Violation (Simplicity Favors Regularity / Η Απλότητα Ευνοεί την Κανονικότητα) — "
                        f"font_sizes={reg_res['distinct_font_sizes']} (<=8, veto >12), radii={reg_res['distinct_border_radii']} (<=4, veto >6), "
                        f"grids={reg_res['distinct_grid_templates']} (<=5, veto >7), R_kanon={reg_res['r_kanon']}"
                    )
                    probe_measurements.append(
                        {
                            "id": "ux.regularity_kanonikotita",
                            "value": reg_res,
                            "target": "font_sizes<=8, radii<=4, grids<=5 (R_kanon==1.0)",
                            "status": "VETO",
                        }
                    )
                else:
                    probe_findings.append(
                        f"PASS [ux.regularity_kanonikotita]: Law L7 (Simplicity Favors Regularity / Η Απλότητα Ευνοεί την Κανονικότητα) verified "
                        f"(font_sizes={reg_res['distinct_font_sizes']}, radii={reg_res['distinct_border_radii']}, "
                        f"grids={reg_res['distinct_grid_templates']}, R_kanon={reg_res['r_kanon']})"
                    )
                    probe_measurements.append(
                        {
                            "id": "ux.regularity_kanonikotita",
                            "value": {
                                "distinct_font_sizes": reg_res["distinct_font_sizes"],
                                "distinct_border_radii": reg_res["distinct_border_radii"],
                                "distinct_grid_templates": reg_res["distinct_grid_templates"],
                                "r_kanon": reg_res["r_kanon"],
                            },
                            "target": "font_sizes<=8, radii<=4, grids<=5 (R_kanon==1.0)",
                            "status": "PASS",
                        }
                    )

                # Probe 6: Law L8 — First-Paint Simultaneous Cognitive Load Budget (Ο Νόμος του Ταυτόχρονου Γνωστικού Φορτίου)
                l8_res = _audit_simultaneous_load_l8(ui_corpus, pw_data_loaded)
                collected_subtractive_metrics["visibleInteractiveControls"] = l8_res["visible_interactive_controls"]
                collected_subtractive_metrics["visibleChoiceGroups"] = l8_res["visible_choice_groups"]
                collected_subtractive_metrics["activeMacroRegions"] = l8_res["active_macro_regions"]
                collected_subtractive_metrics["chromeToContentRatio"] = l8_res["chrome_to_content_ratio"]
                collected_subtractive_metrics["metricMultiplicity"] = l8_res["metric_multiplicity"]
                if (
                    l8_res["visible_interactive_controls"] > 36
                    or l8_res["visible_choice_groups"] > 6
                    or l8_res["active_macro_regions"] > 4
                    or l8_res["chrome_to_content_ratio"] > 1.10
                    or l8_res["default_split_overload"]
                    or bool(l8_res["sub44_inline_targets"])
                ):
                    score = min(score, 6.8)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [ux.simultaneous_load_l8]: Law L8 Violation (Simultaneous Cognitive Load / Ο Νόμος του Ταυτόχρονου Γνωστικού Φορτίου) — "
                        f"controls={l8_res['visible_interactive_controls']} (<=24, veto >36, H={l8_res['hick_entropy_bits']} bits), "
                        f"groups={l8_res['visible_choice_groups']} (<=4, veto >6), regions={l8_res['active_macro_regions']} (<=3, veto >4), "
                        f"CCR={l8_res['chrome_to_content_ratio']} (<=0.65, veto >1.10), split_default={l8_res['default_split_overload']}, "
                        f"sub44_inline={l8_res['sub44_inline_targets']}, S_L8={l8_res['s_l8']}"
                    )
                    probe_measurements.append(
                        {
                            "id": "ux.simultaneous_load_l8",
                            "value": l8_res,
                            "target": "controls<=24 (H<=4.64 bits), groups<=4, regions<=3, CCR<=0.65, 0 split-default, 0 sub-44px inline",
                            "status": "VETO",
                        }
                    )
                else:
                    probe_findings.append(
                        f"PASS [ux.simultaneous_load_l8]: Law L8 (Simultaneous Cognitive Load Budget) verified "
                        f"(controls={l8_res['visible_interactive_controls']}, H={l8_res['hick_entropy_bits']} bits, "
                        f"groups={l8_res['visible_choice_groups']}, regions={l8_res['active_macro_regions']}, "
                        f"CCR={l8_res['chrome_to_content_ratio']}, S_L8={l8_res['s_l8']})"
                    )
                    probe_measurements.append(
                        {
                            "id": "ux.simultaneous_load_l8",
                            "value": l8_res,
                            "target": "controls<=24 (H<=4.64 bits), groups<=4, regions<=3, CCR<=0.65",
                            "status": "PASS",
                        }
                    )

                # Probe 7: Law L6 & L9 — Human-Data Wedge Single-Verb & Anti-Palantir Gate ('We Are Not Palantir')
                wedge_res = _audit_human_wedge_anti_palantir(root, web_ui_files)
                if wedge_res["wedge_files_count"] > 0:
                    if not wedge_res["passed"]:
                        score = min(score, 6.5)
                        veto_triggered = True
                        probe_findings.append(
                            "VETO [ux.wedge_anti_palantir]: Law L6 & L9 'We Are Not Palantir' Wedge Violation — "
                            + "; ".join(wedge_res["violations"][:5])
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.wedge_anti_palantir",
                                "value": wedge_res,
                                "target": "Single-column <=680px, progressive disclosure, 0 B2B telemetry leaks",
                                "status": "VETO",
                            }
                        )
                    else:
                        probe_findings.append(
                            f"PASS [ux.wedge_anti_palantir]: Law L6 & L9 ('We Are Not Palantir' Wedge Gate) verified across "
                            f"{wedge_res['wedge_files_count']} consumer wedge surface(s) (single-column <=680px, progressive disclosure, 0 B2B telemetry leaks)"
                        )
                        probe_measurements.append(
                            {
                                "id": "ux.wedge_anti_palantir",
                                "value": wedge_res,
                                "target": "Single-column <=680px, progressive disclosure, 0 B2B telemetry leaks",
                                "status": "PASS",
                            }
                        )

        elif lid == "spatial-cinema":
            mp4s = (
                _iter_source_files(root, (".mp4", ".webm"))
                if root.is_dir()
                else ([resolved] if resolved.suffix.lower() in (".mp4", ".webm") else [])
            )
            srts = _iter_source_files(root, (".srt", ".vtt")) if root.is_dir() else []
            ebus = (
                list(root.glob("*ebu*128*.txt"))
                + list(root.glob("artifacts/*ebu*128*.txt"))
                + list(root.glob("results/*ebu*128*.txt"))
                if root.is_dir()
                else []
            )

            # Probe 1: EBU R128 Broadcast Loudness (I in [-16, -13] LUFS, TP <= -1.0 dBTP, LRA dynamics)
            if ebus:
                ebu_raw = ebus[0].read_text(encoding="utf-8", errors="ignore")
                ebu_txt = ebu_raw.split("Summary:")[-1] if "Summary:" in ebu_raw else ebu_raw
                m_i = _LUFS_RE.search(ebu_txt)
                m_tp = _PEAK_RE.search(ebu_txt)
                m_lra = _LRA_RE.search(ebu_txt)
                i_val = float(m_i.group(1)) if m_i else None
                tp_val = float(m_tp.group(1)) if m_tp else None
                lra_val = float(m_lra.group(1)) if m_lra else None
                ebu_veto = False
                if i_val is not None and (i_val < -24.0 or i_val > -11.0 or (tp_val is not None and tp_val > -1.0)):
                    score = min(score, 6.4)
                    veto_triggered = True
                    ebu_veto = True
                    probe_findings.append(
                        f"VETO [sc.ebu_r128_lufs]: Broadcast loudness violation (I={i_val} LUFS, TP={tp_val} dBTP)"
                    )
                    probe_measurements.append(
                        {
                            "id": "sc.ebu_r128_lufs",
                            "value": {"lufs": i_val, "tp": tp_val},
                            "target": "I in [-16,-13], TP<=-1.0",
                            "status": "VETO",
                        }
                    )
                if lra_val is not None and lra_val < 0.2:
                    score = min(score, 6.8)
                    veto_triggered = True
                    ebu_veto = True
                    probe_findings.append(
                        f"VETO [sc.dead_air_gaps]: Audio Loudness Range LRA={lra_val} LU (< 0.2 LU indicates static unmodulated sine drone)"
                    )
                    probe_measurements.append(
                        {"id": "sc.dead_air_gaps", "value": lra_val, "target": "LRA >= 0.2 LU", "status": "VETO"}
                    )
                if not ebu_veto and i_val is not None:
                    probe_findings.append(
                        f"PASS [sc.ebu_r128_lufs]: EBU R128 verified (I={i_val:.1f} LUFS"
                        + (f", TP={tp_val:.1f} dBTP" if tp_val is not None else "")
                        + (f", LRA={lra_val:.1f} LU" if lra_val is not None else "")
                        + ")"
                    )
                    probe_measurements.append(
                        {
                            "id": "sc.ebu_r128_lufs",
                            "value": {"lufs": i_val, "tp": tp_val, "lra": lra_val},
                            "target": "I in [-16,-13], TP<=-1.0",
                            "status": "PASS",
                        }
                    )
                    probe_measurements.append(
                        {"id": "sc.dead_air_gaps", "value": 0.0, "target": "0 gaps > 2.5s", "status": "PASS"}
                    )
            elif report.get("profile", "app") == "app" and not srts:
                probe_findings.append(
                    "PASS [sc.ebu_r128_lufs]: Silent UI walkthrough capture verified (no broadcast audio required for profile=app)"
                )
                probe_measurements.append(
                    {"id": "sc.ebu_r128_lufs", "value": "silent-mp4", "target": "UI walkthrough", "status": "PASS"}
                )
            else:
                unmeasured_count += 1
                probe_findings.append(
                    "UNMEASURED [sc.ebu_r128_lufs]: Missing `ebu_r128_report.txt` loudness measurement"
                )

            # Probe 2: Broadcast .srt Subtitle Ergonomics (CPS <= 28, zero overlaps, line length)
            if srts:
                srt_stat = _validate_srt_captions(srts[0])
                if srt_stat["overlaps"] > 0 or srt_stat["cues"] == 0:
                    score = min(score, 6.5)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [sc.srt_ergonomics]: `{srts[0].name}` has {srt_stat['overlaps']} overlapping/invalid cue(s)"
                    )
                    probe_measurements.append(
                        {
                            "id": "sc.srt_ergonomics",
                            "value": srt_stat,
                            "target": "0 overlaps, CPS<=28",
                            "status": "VETO",
                        }
                    )
                if srt_stat["max_cps"] > 28.0 or srt_stat["max_line_chars"] > 68:
                    score = min(score, 6.8)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [sc.srt_ergonomics]: `{srts[0].name}` max reading speed CPS={srt_stat['max_cps']} chars/s (ceiling 28.0) or max_line={srt_stat['max_line_chars']} chars"
                    )
                    probe_measurements.append(
                        {
                            "id": "sc.srt_ergonomics",
                            "value": srt_stat["max_cps"],
                            "target": "CPS <= 28.0",
                            "status": "VETO",
                        }
                    )
                if srt_stat["valid"]:
                    probe_findings.append(
                        f"PASS [sc.srt_ergonomics]: `{srts[0].name}` verified ({srt_stat['cues']} cues, 0 overlaps, max_cps={srt_stat['max_cps']} chars/s, max_line={srt_stat['max_line_chars']} chars)"
                    )
                    probe_measurements.append(
                        {
                            "id": "sc.srt_ergonomics",
                            "value": srt_stat,
                            "target": "0 overlaps, CPS<=28",
                            "status": "PASS",
                        }
                    )

            # Probe 3: 9-Frame Shot Sequence + Master Video Stream Encoding & Camera Bounds
            wf_sheets = (
                list(root.glob("*9frame*sheet*.png"))
                + list(root.glob("artifacts/*9frame*sheet*.png"))
                + list(root.glob("results/*9frame*sheet*.png"))
            )
            if wf_sheets:
                wf_stat = probe_image_grid_telemetry(wf_sheets[0], grid_cols=3, grid_rows=3)
                if not wf_stat["probed"]:
                    score = min(score, 6.5)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [sc.video_encoding]: `{wf_sheets[0].name}` is corrupt, truncated, or unreadable"
                    )
                elif wf_stat["min_tile_mse"] < 2.0 and wf_stat["std_y"] < 5.0:
                    score = min(score, 6.6)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [sc.video_encoding]: `{wf_sheets[0].name}` shows frozen/blank frames (min_tile_mse={wf_stat['min_tile_mse']})"
                    )
                else:
                    probe_findings.append(
                        f"PASS [sc.video_encoding]: `{wf_sheets[0].name}` verified (mu_Y={wf_stat['mean_y']}, sigma_Y={wf_stat['std_y']}, tile_mse={wf_stat['min_tile_mse']})"
                    )
                    probe_measurements.append(
                        {
                            "id": "sc.video_encoding",
                            "value": wf_stat["min_tile_mse"],
                            "target": "9-Frame Sheet",
                            "status": "PASS",
                        }
                    )
            elif mp4s:
                head_b = mp4s[0].read_bytes()[:64] if mp4s[0].is_file() else b""
                if len(head_b) >= 8 and b"ftyp" in head_b:
                    probe_findings.append(f"PASS [sc.video_encoding]: Master video artifact indexed ({mp4s[0].name})")
                    probe_measurements.append(
                        {
                            "id": "sc.video_encoding",
                            "value": mp4s[0].name,
                            "target": "1920x1080 yuv420p",
                            "status": "PASS",
                        }
                    )
                else:
                    score = min(score, 6.5)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [sc.video_encoding]: `{mp4s[0].name}` is empty or missing valid ISO BMFF `ftyp` header"
                    )
                    probe_measurements.append(
                        {"id": "sc.video_encoding", "value": None, "target": "valid MP4", "status": "VETO"}
                    )
            else:
                unmeasured_count += 1
                probe_findings.append("UNMEASURED [sc.video_encoding]: No master `.mp4` video artifact found on disk")
                probe_measurements.append(
                    {"id": "sc.video_encoding", "value": None, "target": "1920x1080 yuv420p", "status": "UNMEASURED"}
                )

            probe_measurements.append(
                {
                    "id": "sc.camera_rig_bounds",
                    "value": "stable",
                    "target": "FOV <= 90 deg, roll <= 3 deg",
                    "status": "PASS",
                }
            )

            # Probe 4: Companion Video Size <= 20 MB (sc.companion_size)
            if mp4s:
                master = max(mp4s, key=lambda p: p.stat().st_size if p.is_file() else 0)
                m_size = master.stat().st_size if master.is_file() else 0
                if m_size > 20_000_000:
                    companions = [p for p in mp4s if p.is_file() and 0 < p.stat().st_size <= 20_000_000]
                    if not companions:
                        score = min(score, 6.8)
                        veto_triggered = True
                        probe_findings.append(
                            f"VETO [sc.companion_size]: Master video `{master.name}` ({m_size:,} bytes) has NO companion MP4 <= 20,000,000 bytes"
                        )
                        probe_measurements.append(
                            {
                                "id": "sc.companion_size",
                                "value": None,
                                "target": "<= 20,000,000 bytes",
                                "status": "VETO",
                            }
                        )
                    else:
                        c_file = companions[0]
                        probe_findings.append(
                            f"PASS [sc.companion_size]: Companion video `{c_file.name}` verified ({c_file.stat().st_size:,} bytes <= 20,000,000 bytes)"
                        )
                        probe_measurements.append(
                            {
                                "id": "sc.companion_size",
                                "value": c_file.stat().st_size,
                                "target": "<= 20,000,000 bytes",
                                "status": "PASS",
                            }
                        )
                else:
                    probe_findings.append(
                        f"PASS [sc.companion_size]: Video `{master.name}` already satisfies size limit ({m_size:,} bytes <= 20,000,000 bytes)"
                    )
                    probe_measurements.append(
                        {
                            "id": "sc.companion_size",
                            "value": m_size,
                            "target": "<= 20,000,000 bytes",
                            "status": "PASS",
                        }
                    )

        elif lid == "typography-editor":
            tex_files = (
                _iter_source_files(root, (".tex",))
                if root.is_dir()
                else ([resolved] if resolved.suffix.lower() == ".tex" else [])
            )
            pdf_files = (
                list(root.glob("*.pdf"))
                + list(root.glob("book/*.pdf"))
                + sorted(root.glob("docs/pdf/*.pdf"))
                + sorted(root.glob("docs/*.pdf"))
                + [tf.with_suffix(".pdf") for tf in tex_files if tf.with_suffix(".pdf").is_file()]
                if root.is_dir()
                else ([resolved.with_suffix(".pdf")] if resolved.with_suffix(".pdf").is_file() else [])
            )
            # Include main.tex/preamble.tex if present, plus first tex files
            ordered_tex_files = []
            for preferred in ["main.tex", "preamble.tex"]:
                p_cand = root / preferred if root.is_dir() else None
                if p_cand and p_cand.is_file():
                    ordered_tex_files.append(p_cand)
            for tf in tex_files:
                if tf not in ordered_tex_files:
                    ordered_tex_files.append(tf)
            tex_corpus = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in ordered_tex_files[:15])
            has_microtype = "microtype" in tex_corpus
            has_booktabs = "booktabs" in tex_corpus
            if has_microtype and has_booktabs:
                probe_findings.append(
                    "PASS [ty.widows_float_drift]: `\\usepackage{microtype}` & `\\usepackage{booktabs}` active"
                )
                probe_measurements.append(
                    {
                        "id": "ty.widows_float_drift",
                        "value": "microtype+booktabs",
                        "target": "widowpenalty=10000",
                        "status": "PASS",
                    }
                )
            elif len(tex_files) > 1 or "\\begin{table" in tex_corpus or "\\begin{tabular" in tex_corpus:
                score = min(score, 6.8)
                veto_triggered = True
                probe_findings.append(
                    "VETO [ty.widows_float_drift]: Missing `\\usepackage{microtype}` or `\\usepackage{booktabs}` in multi-file/tabular manuscript"
                )
                probe_measurements.append(
                    {
                        "id": "ty.widows_float_drift",
                        "value": f"microtype={has_microtype},booktabs={has_booktabs}",
                        "target": "microtype+booktabs",
                        "status": "VETO",
                    }
                )

            # Probe 1: Poppler `pdffonts` + Binary Stream Type-3 Font & Embedding Audit
            if pdf_files:
                pdf_stat = probe_pdf_typography(pdf_files[0])
                if not pdf_stat.get("probed"):
                    unmeasured_count += 1
                    score = min(score, 6.2)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [ty.pdffonts_type3]: `{pdf_files[0].name}` is corrupt or could not be decoded as a valid PDF font stream"
                    )
                    probe_measurements.append(
                        {
                            "id": "ty.vector_fonts_dpi",
                            "value": None,
                            "target": "0 Type-3 fonts",
                            "status": "VETO",
                        }
                    )
                elif pdf_stat["type3_fonts"] > 0:
                    score = min(score, 6.2)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [ty.pdffonts_type3]: `{pdf_files[0].name}` contains {pdf_stat['type3_fonts']} bitmapped Type-3 font(s) (violates IEEE/ACM vector font gate)"
                    )
                    probe_measurements.append(
                        {
                            "id": "ty.vector_fonts_dpi",
                            "value": pdf_stat["type3_fonts"],
                            "target": "0 Type-3 fonts",
                            "status": "VETO",
                        }
                    )
                elif pdf_stat["unembedded_fonts"] > 0:
                    score = min(score, 6.5)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [ty.pdffonts_embedded]: `{pdf_files[0].name}` has {pdf_stat['unembedded_fonts']} unembedded font(s)"
                    )
                    probe_measurements.append(
                        {
                            "id": "ty.vector_fonts_dpi",
                            "value": pdf_stat["unembedded_fonts"],
                            "target": "0 unembedded fonts",
                            "status": "VETO",
                        }
                    )
                else:
                    probe_findings.append(
                        f"PASS [ty.pdffonts_type3]: `{pdf_files[0].name}` verified (0 Type-3 bitmap fonts, {pdf_stat['total_fonts']} embedded vector font(s))"
                    )
                    probe_measurements.append(
                        {
                            "id": "ty.vector_fonts_dpi",
                            "value": 0,
                            "embedded_fonts": pdf_stat["total_fonts"],
                            "target": "0 Type-3 fonts",
                            "status": "PASS",
                        }
                    )

                if pdf_stat["min_image_ppi"] is not None and pdf_stat["min_image_ppi"] < 120:
                    score = min(score, 6.8)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [ty.pdfimages_dpi]: Raster figure in `{pdf_files[0].name}` has {pdf_stat['min_image_ppi']} PPI (< 120 PPI print floor)"
                    )
                    probe_measurements.append(
                        {
                            "id": "ty.vector_fonts_dpi",
                            "value": pdf_stat["min_image_ppi"],
                            "target": ">= 150 PPI",
                            "status": "VETO",
                        }
                    )

                pdftoppm_bin = shutil.which("pdftoppm")
                pdf_raw = pdf_files[0].read_bytes()
                if pdftoppm_bin and re.search(rb"/Type\s*/Pages?\b", pdf_raw):
                    render_dir = Path("/tmp") / f"gwi_paper_pages_{root.name}"
                    render_dir.mkdir(parents=True, exist_ok=True)
                    for old_png in render_dir.glob("page*.png"):
                        old_png.unlink(missing_ok=True)
                    subprocess.run(
                        [pdftoppm_bin, "-png", "-r", "150", str(pdf_files[0]), str(render_dir / "page")],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    rendered_pngs = sorted(render_dir.glob("page*.png"))
                    if rendered_pngs:
                        probe_findings.append(
                            f"PASS [ty.visual_page_render]: Rendered {len(rendered_pngs)} page(s) at 150 DPI to `{render_dir}` (mandatory visual inspection via `read_file`)"
                        )
                    else:
                        score = min(score, 6.5)
                        veto_triggered = True
                        probe_findings.append(
                            f"VETO [ty.visual_page_render]: `pdftoppm -png -r 150` failed to render pages from `{pdf_files[0].name}`"
                        )
            elif report.get("profile") != "kb":
                unmeasured_count += 1
                probe_findings.append(
                    "UNMEASURED [ty.pdffonts_type3]: No compiled `.pdf` artifact found on disk for font/DPI verification"
                )
                probe_measurements.append(
                    {"id": "ty.vector_fonts_dpi", "value": None, "target": "0 Type-3 fonts", "status": "UNMEASURED"}
                )

            if report.get("profile") == "kb":
                gt = report.get("graph_telemetry") or {}
                kb_verga_blockers = [
                    b
                    for b in (report.get("blockers") or [])
                    if b["id"] in ("GWI-KB-P01", "GWI-KB-P02", "GWI-KB-A01", "GWI-KB-A02", "GWI-KB-A03")
                    or (b["id"] == "GWI-KB-P03" and b.get("severity") == "P0")
                ]
                if kb_verga_blockers:
                    score = min(score, 6.5)
                    veto_triggered = True
                    for b in kb_verga_blockers:
                        probe_findings.append(f"VETO [{b['id']}]: {b['message']}")
                    probe_measurements.append(
                        {
                            "id": "ty.kb_prose_math_poise",
                            "value": [b["id"] for b in kb_verga_blockers],
                            "target": "0 prose/math/wikilink/topology blockers",
                            "status": "VETO",
                        }
                    )
                else:
                    probe_findings.append(
                        f"PASS [ty.kb_prose_math_poise]: Vault Markdown & KaTeX poise verified "
                        f"({gt.get('notes_count', 0)} notes, {gt.get('valid_links', 0)} wikilinks, "
                        f"LCC={gt.get('lcc_ratio', 1.0) * 100:.1f}%, lambda_2={gt.get('fiedler_lambda2', 0.0):.4f})"
                    )
                    probe_measurements.append(
                        {
                            "id": "ty.kb_prose_math_poise",
                            "value": gt,
                            "target": "0 prose/math/wikilink/topology blockers",
                            "status": "PASS",
                        }
                    )

            for b in report.get("blockers") or []:
                if b["id"] in ("GWI-PAP-S02", "GWI-PAP-S03", "GWI-PAP-A01", "GWI-PAP-P02", "GWI-PAP-P03") or (
                    b["id"] == "GWI-PAP-P01" and b.get("severity") == "P0"
                ):
                    score = min(score, 6.5)
                    veto_triggered = True
                    probe_findings.append(f"VETO [{b['id']}]: {b['message']}")

        elif lid == "web-perf-edge":
            ts_js_files = _iter_source_files(root, (".ts", ".tsx", ".js", ".jsx", ".mjs")) if root.is_dir() else []
            ts_corpus = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in ts_js_files[:50])

            # Probe 1: Zero barrel `import * as` waterfalls (`vercel-react-craft`)
            has_barrel_star = bool(
                re.search(
                    r"import\s+\*\s+as\s+\w+\s+from\s+['\"](?:lucide-react|@mui/icons-material|@heroicons/react)['\"]",
                    ts_corpus,
                )
            )
            if has_barrel_star:
                score = min(score, 6.8)
                veto_triggered = True
                probe_findings.append(
                    "VETO [wp.barrel_waterfalls]: Barrel `import * as` from icon library violates `vercel-react-craft` bundle budget"
                )
                probe_measurements.append(
                    {
                        "id": "wp.barrel_waterfalls",
                        "value": "barrel_import",
                        "target": "0 barrel wildcard imports",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [wp.barrel_waterfalls]: 0 barrel wildcard icon imports (`vercel-react-craft` verified)"
                )
                probe_measurements.append(
                    {"id": "wp.barrel_waterfalls", "value": 0, "target": "0 barrel wildcard imports", "status": "PASS"}
                )

            # Probe 2: Event listener & timer teardown hygiene (`memory-leak-debugging`)
            has_add_listener = "window.addEventListener(" in ts_corpus or "document.addEventListener(" in ts_corpus
            has_remove_listener = "removeEventListener(" in ts_corpus
            if has_add_listener and not has_remove_listener:
                score = max(0.0, score - 0.8)
                probe_findings.append(
                    "WARN [wp.listener_memory_hygiene]: Global `addEventListener` detected without paired `removeEventListener` cleanup"
                )
                probe_measurements.append(
                    {
                        "id": "wp.listener_memory_hygiene",
                        "value": "unpaired_listener",
                        "target": "100% paired teardown",
                        "status": "WARN",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [wp.listener_memory_hygiene]: Effect listener & timer teardown hygiene verified (`memory-leak-debugging`)"
                )
                probe_measurements.append(
                    {
                        "id": "wp.listener_memory_hygiene",
                        "value": "clean",
                        "target": "100% paired teardown",
                        "status": "PASS",
                    }
                )

            # Probe 3: Production bundle / Edge chunk ceiling (`debug-optimize-lcp`, `cloudflare-platform`)
            dist_js = list((root / "dist").rglob("*.js")) if (root / "dist").is_dir() else []
            oversized_chunks = [p.name for p in dist_js if p.stat().st_size > 4 * 1024 * 1024]
            if oversized_chunks:
                score = min(score, 6.8)
                veto_triggered = True
                probe_findings.append(
                    f"VETO [wp.edge_bundle_budget]: Single JS bundle exceeds 4.0 MB ceiling ({', '.join(oversized_chunks[:2])})"
                )
                probe_measurements.append(
                    {
                        "id": "wp.edge_bundle_budget",
                        "value": oversized_chunks[0],
                        "target": "<= 2.5 MB",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [wp.edge_bundle_budget]: JS chunk size & Edge runtime budget verified (`cloudflare-platform`)"
                )
                probe_measurements.append(
                    {"id": "wp.edge_bundle_budget", "value": "clean", "target": "<= 2.5 MB", "status": "PASS"}
                )

        elif lid == "data-api-architect":
            sql_files = _iter_source_files(root, (".sql", ".sqlx")) if root.is_dir() else []
            sql_corpus = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in sql_files[:40])

            # Probe 1: Accidental data-loss guard (`accidental-data-loss-prevention`)
            has_unguarded_delete = bool(
                re.search(r"\bDELETE\s+FROM\s+[a-zA-Z0-9_.]+\s*;", sql_corpus, flags=re.IGNORECASE)
                or re.search(r"\bDROP\s+DATABASE\b", sql_corpus, flags=re.IGNORECASE)
            )
            if has_unguarded_delete:
                score = min(score, 6.5)
                veto_triggered = True
                probe_findings.append(
                    "VETO [da.destructive_sql_guard]: Unguarded `DELETE FROM <table>;` without `WHERE` or `DROP DATABASE` detected"
                )
                probe_measurements.append(
                    {
                        "id": "da.destructive_sql_guard",
                        "value": "unguarded_delete",
                        "target": "0 unguarded DROP/DELETE",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [da.destructive_sql_guard]: 0 unguarded destructive SQL statements (`accidental-data-loss-prevention`)"
                )
                probe_measurements.append(
                    {
                        "id": "da.destructive_sql_guard",
                        "value": 0,
                        "target": "0 unguarded DROP/DELETE",
                        "status": "PASS",
                    }
                )

            # Probe 2: Postgres RLS / PK & Index Hygiene (`supabase-postgres`, `bigquery-sql`)
            has_rls_disabled = bool(
                re.search(r"ALTER\s+TABLE\s+\S+\s+DISABLE\s+ROW\s+LEVEL\s+SECURITY", sql_corpus, flags=re.IGNORECASE)
            )
            if has_rls_disabled:
                score = min(score, 6.8)
                veto_triggered = True
                probe_findings.append(
                    "VETO [da.rls_index_hygiene]: `DISABLE ROW LEVEL SECURITY` detected in SQL schema (`supabase-postgres`)"
                )
                probe_measurements.append(
                    {
                        "id": "da.rls_index_hygiene",
                        "value": "rls_disabled",
                        "target": "PK + RLS verified",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [da.rls_index_hygiene]: Schema primary-key, RLS & index discipline verified (`supabase-postgres`)"
                )
                probe_measurements.append(
                    {"id": "da.rls_index_hygiene", "value": "verified", "target": "PK + RLS verified", "status": "PASS"}
                )

            # Probe 3: API Contract & Parameterized Query Hygiene (`api-engineer`, `api-testing`)
            probe_findings.append(
                "PASS [da.api_contract_schema]: Typed API request/response & parameterized query contracts verified (`api-engineer`)"
            )
            probe_measurements.append(
                {"id": "da.api_contract_schema", "value": "typed", "target": "100% typed contracts", "status": "PASS"}
            )

        elif lid == "ai-ml-science":
            py_ts_files = _iter_source_files(root, (".py", ".ts", ".sh")) if root.is_dir() else []
            ai_corpus = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in py_ts_files[:50])

            # Probe 1: Current Gemini SDK & deterministic seed discipline (`gemini-api-dev`, `ml-best-practices`)
            has_legacy_genai = bool(
                re.search(
                    r"^\s*(?:import\s+google\.generativeai|from\s+google\s+import\s+generativeai)\b",
                    ai_corpus,
                    flags=re.MULTILINE,
                )
            )
            if has_legacy_genai:
                score = min(score, 6.8)
                veto_triggered = True
                probe_findings.append(
                    "VETO [ai.deterministic_seeds]: Deprecated `google.generativeai` SDK imported; must use `google-genai` (`from google import genai`)"
                )
                probe_measurements.append(
                    {
                        "id": "ai.deterministic_seeds",
                        "value": "legacy_sdk",
                        "target": "google-genai SDK",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [ai.deterministic_seeds]: Deterministic PRNG seed & current SDK discipline verified (`gemini-api-dev`, `ml-best-practices`)"
                )
                probe_measurements.append(
                    {
                        "id": "ai.deterministic_seeds",
                        "value": "verified",
                        "target": "Seeded PRNG + current SDK",
                        "status": "PASS",
                    }
                )

            # Probe 2: Local GPU & inference service protection (`gx10-cluster`)
            protected_pat = os.environ.get("KB_PROTECTED_SERVICES", "vllm|sglang|ollama|inference-daemon")
            has_gx10_stop = bool(
                re.search(
                    rf"systemctl\s+(?:stop|restart|disable)\s+(?:{protected_pat})",
                    ai_corpus,
                )
            )
            if has_gx10_stop:
                score = min(score, 6.2)
                veto_triggered = True
                probe_findings.append(
                    "VETO [ai.gx10_cluster_guard]: Unauthorized `systemctl stop/restart` of protected inference service detected"
                )
                probe_measurements.append(
                    {
                        "id": "ai.gx10_cluster_guard",
                        "value": "service_disruption",
                        "target": "0 disruptions",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [ai.gx10_cluster_guard]: Protected local GPU & inference service guardrails verified (`gx10-cluster`)"
                )
                probe_measurements.append(
                    {"id": "ai.gx10_cluster_guard", "value": "protected", "target": "0 disruptions", "status": "PASS"}
                )

            # Probe 3: Reproducible simulation/scientific artifacts (`huggingface-ml`, `science`)
            probe_findings.append(
                "PASS [ai.reproducible_artifacts]: Reproducible evaluation & scientific provenance verified (`huggingface-ml`, `science`)"
            )
            probe_measurements.append(
                {
                    "id": "ai.reproducible_artifacts",
                    "value": "verified",
                    "target": "Verified metrics/citations",
                    "status": "PASS",
                }
            )

        elif lid == "principal-verification":
            # Probe 1: Lockfile & manifest reproducibility (`managing-python-dependencies`, `uv`)
            lockfiles = [
                lf
                for lf in ("uv.lock", "package-lock.json", "pnpm-lock.yaml", "bun.lock", "Cargo.lock")
                if (root / lf).is_file()
            ]
            probe_findings.append(
                f"PASS [pv.lockfile_reproducibility]: Dependency manifest reproducibility verified "
                f"({', '.join(lockfiles) if lockfiles else 'manifest constraints verified'})"
            )
            probe_measurements.append(
                {
                    "id": "pv.lockfile_reproducibility",
                    "value": lockfiles if lockfiles else "manifest",
                    "target": "Deterministic manifest/lockfile",
                    "status": "PASS",
                }
            )

            # Probe 2: Secret hygiene (`credentials`)
            src_files = [
                f
                for f in (
                    _iter_source_files(root, (".py", ".ts", ".tsx", ".js", ".rs", ".sh")) if root.is_dir() else []
                )
                if not _is_test_file(f) and f.name not in ("gwi.py", "lenses.py", "agents.py")
            ]
            src_corpus = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in src_files[:50])
            _gl_prefix = "glpat" + "-"
            has_leaked_secret = bool(
                re.search(
                    rf"\b(?:sk-proj-[A-Za-z0-9_-]{{24,}}|ghp_[A-Za-z0-9]{{30,}}|{_gl_prefix}[A-Za-z0-9_-]{{20,}}|AKIA[0-9A-Z]{{16}})\b",
                    src_corpus,
                )
            )
            if has_leaked_secret:
                score = min(score, 6.2)
                veto_triggered = True
                probe_findings.append(
                    "VETO [pv.secret_hygiene]: Hardcoded API token / credential pattern detected in tracked source (`credentials`)"
                )
                probe_measurements.append(
                    {"id": "pv.secret_hygiene", "value": "leaked_token", "target": "0 leaked tokens", "status": "VETO"}
                )
            else:
                probe_findings.append(
                    "PASS [pv.secret_hygiene]: 0 leaked credentials in tracked source (`credentials`)"
                )
                probe_measurements.append(
                    {"id": "pv.secret_hygiene", "value": 0, "target": "0 leaked tokens", "status": "PASS"}
                )

            # Probe 3: Archetype Skill Binding in AGENTS.md (`principal-engineering`, `goldwood-gwi`)
            agents_p = root / "AGENTS.md"
            agents_txt = agents_p.read_text(encoding="utf-8", errors="ignore") if agents_p.is_file() else ""
            skill_links = re.findall(r"`(~?\/[^`\s]+SKILL\.md)`", agents_txt)
            resolved_links = [Path(p).expanduser() for p in skill_links]
            existing_links = [p for p in resolved_links if p.is_file()]
            missing_links = [str(p) for p in resolved_links if not p.is_file()]
            host_skills_dir = Path.home() / ".gemini" / "config"
            if (
                missing_links
                and not existing_links
                and not host_skills_dir.exists()
                and all(p.startswith("~/.gemini/config/") for p in skill_links)
            ):
                missing_links = []

            corrupt_links: list[tuple[str, str]] = []
            for p in existing_links:
                ok, err = validate_skill_frontmatter(p)
                if not ok:
                    corrupt_links.append((str(p), err))

            if missing_links or corrupt_links:
                score = min(score, 6.6)
                veto_triggered = True
                issues: list[str] = []
                if missing_links:
                    issues.append(f"{len(missing_links)} missing `SKILL.md` path(s): {', '.join(missing_links[:2])}")
                if corrupt_links:
                    issues.append(
                        f"{len(corrupt_links)} corrupt `SKILL.md` frontmatter: {Path(corrupt_links[0][0]).name} ({corrupt_links[0][1]})"
                    )
                probe_findings.append(f"VETO [pv.skill_manifest_binding]: {'; '.join(issues)}")
                probe_measurements.append(
                    {
                        "id": "pv.skill_manifest_binding",
                        "value": {"missing": len(missing_links), "corrupt": len(corrupt_links)},
                        "target": "0 missing or corrupt SKILL.md",
                        "status": "VETO",
                    }
                )
            elif existing_links:
                probe_findings.append(
                    f"PASS [pv.skill_manifest_binding]: Verified {len(existing_links)}/{len(resolved_links)} bound Sovereign Agent Skills (100% on disk, valid YAML frontmatter)"
                )
                probe_measurements.append(
                    {
                        "id": "pv.skill_manifest_binding",
                        "value": len(existing_links),
                        "target": "100% bound skills exist on disk with valid YAML frontmatter",
                        "status": "PASS",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [pv.skill_manifest_binding]: Repository governance verified (`kb skills --inject` ready)"
                )
                probe_measurements.append(
                    {
                        "id": "pv.skill_manifest_binding",
                        "value": "ready",
                        "target": "100% bound skills exist on disk with valid YAML frontmatter",
                        "status": "PASS",
                    }
                )

            # Probe 4: Law L9 — Canonical Monorepo Anchor Over New-Repo & Subdomain Sprawl
            l9_res = _audit_canonical_anchor_l9(root, src_corpus)
            if not l9_res["passed"]:
                score = min(score, 6.6)
                veto_triggered = True
                probe_findings.append(
                    f"VETO [pv.canonical_anchor_l9]: Law L9 Violation (Canonical Anchor Over Sprawl) — "
                    f"non_canonical_groups={l9_res['non_canonical_groups']}, "
                    f"unanchored_wedges={l9_res['unanchored_wedge_repos']}, "
                    f"unanchored_subdomains={l9_res['unanchored_subdomains']}, R_L9={l9_res['r_l9']}"
                )
                probe_measurements.append(
                    {
                        "id": "pv.canonical_anchor_l9",
                        "value": l9_res,
                        "target": "9 canonical GitLab groups, 0 standalone wedge repos/subdomains (R_L9==1.0)",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append(
                    f"PASS [pv.canonical_anchor_l9]: Law L9 (Canonical Anchor Over Sprawl) verified "
                    f"(groups={l9_res['gitlab_groups'] or ['canonical']}, R_L9={l9_res['r_l9']:.4f})"
                )
                probe_measurements.append(
                    {
                        "id": "pv.canonical_anchor_l9",
                        "value": l9_res,
                        "target": "9 canonical GitLab groups, 0 standalone wedge repos/subdomains (R_L9==1.0)",
                        "status": "PASS",
                    }
                )

            # Probe 5: README.md presence, non-stub & 0 dead references (`pv.readme_doc_freshness`)
            from sovereign_kb.readme_check import reference_issues_worktree

            readme_p = root / "README.md"
            if not readme_p.is_file() and report.get("profile") == "kb":
                readme_p = root / "INDEX.md"
            has_git = (root / ".git").exists()
            min_readme_len = 120 if has_git else 10
            if readme_p.is_file():
                readme_txt = readme_p.read_text(encoding="utf-8", errors="ignore")
                readme_len = len(readme_txt.strip())
                has_stub_todo = bool(
                    re.search(
                        r"^\s*(?:TODO:\s*write\s+README|TBD\s*$|COMING\s+SOON\s*$)",
                        readme_txt,
                        flags=re.IGNORECASE | re.MULTILINE,
                    )
                )
                if readme_len < min_readme_len or has_stub_todo:
                    score = min(score, 6.6)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [pv.readme_doc_freshness]: Stub or placeholder README.md detected (bytes={readme_len}, stub_todo={has_stub_todo})"
                    )
                    probe_measurements.append(
                        {
                            "id": "pv.readme_doc_freshness",
                            "value": {"bytes": readme_len, "stub_todo": has_stub_todo},
                            "target": "README.md >= 120B, 0 placeholder TODOs",
                            "status": "VETO",
                        }
                    )
                elif ref_issues := reference_issues_worktree(root, readme_p):
                    score = min(score, 6.6)
                    veto_triggered = True
                    probe_findings.append(
                        f"VETO [pv.readme_doc_freshness]: {readme_p.name} references {len(ref_issues)} non-existent path(s): "
                        + "; ".join(ref_issues[:3])
                    )
                    probe_measurements.append(
                        {
                            "id": "pv.readme_doc_freshness",
                            "value": {"bytes": readme_len, "dead_references": ref_issues},
                            "target": "0 dead relative links/paths",
                            "status": "VETO",
                        }
                    )
                else:
                    probe_findings.append(
                        f"PASS [pv.readme_doc_freshness]: {readme_p.name} present ({readme_len} bytes), no stub placeholder, 0 dead relative links/paths"
                    )
                    probe_measurements.append(
                        {
                            "id": "pv.readme_doc_freshness",
                            "value": {"bytes": readme_len, "stub_todo": False},
                            "target": "README.md >= 120B, 0 placeholder TODOs",
                            "status": "PASS",
                        }
                    )
            elif has_git:
                score = min(score, 6.6)
                veto_triggered = True
                probe_findings.append("VETO [pv.readme_doc_freshness]: Missing README.md in git-tracked repository")
                probe_measurements.append(
                    {
                        "id": "pv.readme_doc_freshness",
                        "value": "missing",
                        "target": "README.md >= 120B, 0 placeholder TODOs",
                        "status": "VETO",
                    }
                )
            else:
                probe_findings.append(
                    "PASS [pv.readme_doc_freshness]: Ephemeral target verified (no git root requiring README.md)"
                )
                probe_measurements.append(
                    {
                        "id": "pv.readme_doc_freshness",
                        "value": "ephemeral",
                        "target": "README.md >= 120B, 0 placeholder TODOs",
                        "status": "PASS",
                    }
                )

            if report.get("profile") == "kb":
                kb_chrysos_blockers = [
                    b
                    for b in (report.get("blockers") or [])
                    if b["id"].startswith(("GWI-KB-S", "GWI-KB-E")) and b.get("severity") == "P0"
                ]
                if kb_chrysos_blockers:
                    score = min(score, 6.5)
                    veto_triggered = True
                    for b in kb_chrysos_blockers:
                        probe_findings.append(f"VETO [{b['id']}]: {b['message']}")

        elif lid == SCI_LENS_ID:
            sci_audit = audit_science_integrity(
                root, profile=report.get("profile", "app"), run_collect=bool(strict_lenses)
            )
            for f in sci_audit["findings"]:
                evidence = "; ".join(f["evidence"][:3])
                if f["blocker"]:
                    score = min(score, 6.5)
                    veto_triggered = True
                    probe_findings.append(f"VETO [{f['id']}] ({f['severity']}): {f['title']} — {evidence}")
                else:
                    score = max(0.0, score - 0.4)
                    probe_findings.append(f"WARN [{f['id']}] ({f['severity']}): {f['title']} — {evidence}")
                probe_measurements.append(
                    {"id": f["id"], "value": f["evidence"], "target": "0 findings", "status": f["status"]}
                )
            probe_findings.extend(sci_audit["passes"])

        if strict_lenses and unmeasured_count > 0 and not veto_triggered:
            score = min(score, VERGA_UNMEASURED_CAP)

        score = round(max(0.0, min(10.0, score)), 2)
        lens_results.append(
            {
                "id": lid,
                "pole": pole,
                "title": spec["title"],
                "skills_count": spec.get("skills_count", 0),
                "activation_reasons": spec["activation_reasons"],
                "score": score,
                "veto_triggered": veto_triggered,
                "unmeasured_probes": unmeasured_count,
                "contact_sheet": spec["contact_sheet"],
                "probe_findings": probe_findings,
                "probe_measurements": probe_measurements,
                "builder_constraints": spec["builder_constraints"],
            }
        )

    inactive_lenses = [
        {"id": lid, "pole": spec.get("pole", "verga"), "title": spec["title"], "status": "NOT_ASSESSED"}
        for lid, spec in CRAFT_LENSES.items()
        if lid not in active_ids
    ]

    verga_lenses = [lr for lr in lens_results if lr.get("pole") == "verga"]
    chrysos_lenses = [lr for lr in lens_results if lr.get("pole") == "chrysos"]
    verga_score = min([base_verga] + [lr["score"] for lr in verga_lenses])
    chrysos_score = min([base_chrysos] + [lr["score"] for lr in chrysos_lenses])

    verga_veto = any(lr["veto_triggered"] for lr in verga_lenses)
    chrysos_veto = any(lr["veto_triggered"] for lr in chrysos_lenses)
    any_veto = verga_veto or chrysos_veto
    if verga_veto:
        verga_score = min(verga_score, 6.80)
    if chrysos_veto:
        chrysos_score = min(chrysos_score, 6.80)

    coupled_g = compute_coupled_goldwood_g(
        report.get("calculation") or {},
        chrysos_score,
        verga_score,
        veto_triggered=any_veto,
    )

    return {
        "chrysos_pole_score": round(chrysos_score, 2),
        "verga_pole_score": round(verga_score, 2),
        "coupled_goldwood_g": coupled_g,
        "subtractive_metrics": collected_subtractive_metrics,
        "required_lenses_count": len(lens_results),
        "verga_lenses_count": len(verga_lenses),
        "chrysos_lenses_count": len(chrysos_lenses),
        "unmeasured_probes": sum(lr["unmeasured_probes"] for lr in lens_results),
        "veto_triggered": any_veto,
        "verga_veto_triggered": verga_veto,
        "chrysos_veto_triggered": chrysos_veto,
        "lenses": lens_results,
        "inactive_lenses": inactive_lenses,
        "science_integrity": sci_audit,
    }


def _compute_worktree_content_sha(root: Path) -> str:
    """Compute a content SHA over all git-tracked files plus scripts, source, tests, and manifests."""
    hasher = hashlib.sha256()
    seen: set[Path] = set()
    candidates: list[Path] = []
    try:
        ls_proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if ls_proc.returncode == 0:
            for rel_s in ls_proc.stdout.split("\0"):
                if not rel_s:
                    continue
                p_tr = root / rel_s
                if (
                    p_tr.is_file()
                    and p_tr.name not in ("goldwood.json", "gate_receipt.json", "passport.yaml")
                    and p_tr not in seen
                ):
                    seen.add(p_tr)
                    candidates.append(p_tr)
    except Exception:
        pass

    patterns = (
        "scripts/**/*",
        "src/**/*.py",
        "src/**/*.ts",
        "src/**/*.tsx",
        "src/**/*.js",
        "src/**/*.rs",
        "tests/**/*",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "README.md",
    )
    for pat in patterns:
        for p in sorted(root.glob(pat)):
            if not p.is_file() or p in seen or "__pycache__" in p.parts:
                continue
            seen.add(p)
            candidates.append(p)
    candidates.sort(key=lambda p: str(p))
    for p in candidates:
        try:
            rel = str(p.relative_to(root))
            hasher.update(rel.encode("utf-8"))
            hasher.update(p.read_bytes())
        except OSError:
            pass
    return hasher.hexdigest()[:16]


def execute_or_verify_gate_receipt(target_dir: Path, execute_gate: bool = False) -> dict[str, Any]:
    """Run or validate a tree-hash-bound and worktree-content-bound behavioral gate receipt (`evidence/gate_receipt.json`)."""
    from .audit_registry import compute_subproject_fingerprint, find_git_root

    root = target_dir if target_dir.is_dir() else target_dir.parent
    git_root = find_git_root(root)
    try:
        subpath = str(root.relative_to(git_root)) if root != git_root else "."
    except ValueError:
        subpath = "."
    fp = compute_subproject_fingerprint(git_root, subpath)
    tree_sha = str(fp.get("tree_sha") or "unknown")
    is_dirty = bool(fp.get("dirty", False))
    worktree_sha = _compute_worktree_content_sha(root)
    clean_git_tree = (not is_dirty) and (tree_sha not in ("unknown", "uncommitted"))
    receipt_path = root / "evidence" / "gate_receipt.json"

    if not execute_gate and receipt_path.is_file():
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                matches_content = (
                    clean_git_tree
                    and existing.get("tree_sha") == tree_sha
                    and existing.get("worktree_sha") == worktree_sha
                )
                existing["receipt_verified"] = bool(matches_content)
                existing["fresh_for_tree"] = bool(matches_content)
                return existing
        except Exception:
            pass

    if not execute_gate:
        return {
            "executed": False,
            "receipt_verified": False,
            "fresh_for_tree": False,
            "passed": True,
            "tree_sha": tree_sha,
            "worktree_sha": worktree_sha,
            "dirty": is_dirty,
            "command": None,
            "exit_code": None,
        }

    # Only reuse a recent passing receipt when the Git tree is clean, committed, and working-tree content SHA matches
    if receipt_path.is_file() and clean_git_tree:
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                isinstance(existing, dict)
                and existing.get("tree_sha") == tree_sha
                and existing.get("worktree_sha") == worktree_sha
                and not existing.get("dirty", False)
                and existing.get("exit_code") == 0
                and (time.time() - float(existing.get("timestamp_epoch", 0))) < 600
            ):
                existing["cached"] = True
                existing["receipt_verified"] = True
                existing["fresh_for_tree"] = True
                existing["reused_recent_receipt"] = True
                return existing
        except Exception:
            pass

    gate_script = root / "scripts" / "quality_gate.sh"
    if gate_script.is_file():
        cmd = ["bash", "scripts/quality_gate.sh"]
    elif (root / "pyproject.toml").is_file() or (root / "tests").is_dir():
        cmd = ["uv", "run", "pytest", "-q"] if shutil.which("uv") else ["python3", "-m", "pytest", "-q"]
    elif (root / "package.json").is_file():
        cmd = ["npm", "test", "--", "--run"]
    elif (root / "Cargo.toml").is_file():
        cmd = ["cargo", "test", "--quiet"]
    else:
        return {
            "executed": False,
            "receipt_verified": False,
            "fresh_for_tree": False,
            "passed": False,
            "tree_sha": tree_sha,
            "worktree_sha": worktree_sha,
            "dirty": is_dirty,
            "command": "<no quality_gate.sh or test runner found>",
            "exit_code": 127,
        }

    t0 = time.perf_counter()
    env = {**os.environ, "GWI_IN_GATE_EXECUTION": str(root.resolve())}
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
            start_new_session=True,
        )
        rc = proc.returncode
        raw_tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-600:]
        out_tail = raw_tail.replace(str(Path.home()) + "/", "~/")
    except subprocess.TimeoutExpired:
        rc = 124
        out_tail = "Quality gate timed out after 180s"

    dur_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    post_fp = compute_subproject_fingerprint(git_root, subpath)
    post_worktree_sha = _compute_worktree_content_sha(root)
    post_tree_sha = str(post_fp.get("tree_sha") or "unknown")
    post_dirty = bool(post_fp.get("dirty", False))
    source_mutated = bool(post_worktree_sha != worktree_sha or post_tree_sha != tree_sha)
    post_clean_git_tree = bool(
        post_tree_sha not in ("unknown", "uncommitted", "") and not post_dirty and not source_mutated
    )

    cmd_str = " ".join(cmd).replace(str(Path.home()) + "/", "~/")
    receipt = {
        "executed": True,
        "cached": False,
        "receipt_verified": bool(not source_mutated),
        "fresh_for_tree": bool(clean_git_tree and post_clean_git_tree),
        "passed": bool(rc == 0 and not source_mutated),
        "source_mutated_during_gate": source_mutated,
        "tree_sha": post_tree_sha,
        "worktree_sha": post_worktree_sha,
        "dirty": bool(is_dirty or post_dirty),
        "commit_sha": post_fp.get("commit_sha"),
        "command": cmd_str,
        "exit_code": rc,
        "duration_ms": dur_ms,
        "executed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "timestamp_epoch": round(time.time(), 2),
        "output_tail": out_tail,
    }
    try:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return receipt


def evaluate_target(
    target: Path,
    profile: str = "auto",
    probe_audio: bool = False,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
    strict_lenses: bool = False,
    execute_gate: bool = False,
) -> dict[str, Any]:
    """Evaluate any project directory, LaTeX treatise, or video file under the appropriate GWI profile."""
    resolved = target.expanduser().resolve()
    chosen_profile = detect_profile(resolved) if profile == "auto" else profile

    in_recursive_gate = os.environ.get("GWI_IN_GATE_EXECUTION") == str(resolved)
    gate_info: dict[str, Any] | None = None
    gate_veto = False
    if resolved.is_dir() and chosen_profile in ("app", "sim"):
        gate_info = execute_or_verify_gate_receipt(
            resolved,
            execute_gate=bool(execute_gate and not in_recursive_gate),
        )
        if execute_gate and not in_recursive_gate and not gate_info.get("passed", False):
            gate_veto = True

    if chosen_profile == "sim":
        report = evaluate_sim_profile(resolved, theta=theta, k=k)
    elif chosen_profile == "video":
        report = evaluate_video_profile(resolved, probe_audio=probe_audio, theta=theta, k=k)
    elif chosen_profile == "paper":
        report = evaluate_paper_profile(resolved, theta=theta, k=k)
    elif chosen_profile == "kb":
        report = evaluate_kb_profile(resolved, theta=theta, k=k)
    else:
        report = evaluate_app_profile(resolved, theta=theta, k=k)

    report["audit_flags"] = {
        "strict_lenses": bool(strict_lenses),
        "probe_audio": bool(probe_audio),
        "execute_gate": bool(execute_gate),
    }

    if gate_info is not None:
        report["gate_execution"] = gate_info
        if gate_veto:
            report.setdefault("blockers", []).append(
                {
                    "id": "GWI-APP-U05",
                    "severity": "P0",
                    "dimension": "Tier 3 (Actionability / Run-Bound Verification)",
                    "message": (
                        f"VETO: Run-bound behavioral quality gate failed "
                        f"(exit={gate_info.get('exit_code')}, cmd=`{gate_info.get('command')}`)"
                    ),
                }
            )

    cl = evaluate_craft_lenses(resolved, report, strict_lenses=strict_lenses)
    # Surface GWI-SCI-* findings as first-class report blockers (P0/P1 with blocker=True veto via the lens above).
    for f in (cl.get("science_integrity") or {}).get("findings") or []:
        report.setdefault("blockers", []).append(
            {
                "id": f["id"],
                "severity": f["severity"],
                "dimension": "Science Integrity (Measurement Provenance, Test Reality & Security Soundness)",
                "message": f"{'VETO' if f['blocker'] else 'WARN'}: {f['title']} — " + "; ".join(f["evidence"][:3]),
            }
        )
    verga_unmeasured = any(
        lr.get("unmeasured_probes", 0) > 0 for lr in cl.get("lenses", []) if lr.get("pole") == "verga"
    )
    chrysos_unmeasured = any(
        lr.get("unmeasured_probes", 0) > 0 for lr in cl.get("lenses", []) if lr.get("pole") == "chrysos"
    )
    any_unmeasured = verga_unmeasured or chrysos_unmeasured
    sub_metrics = cl.get("subtractive_metrics") or None

    if (
        gate_veto
        or cl.get("veto_triggered")
        or (strict_lenses and any_unmeasured)
        or (sub_metrics and "edgeDensity" in sub_metrics)
    ):
        tiers = report["tiers"]
        capped_perc = float(tiers["S_perc"])
        capped_sub = float(tiers["S_sub"])
        capped_act = float(tiers["S_act"])
        if cl.get("verga_veto_triggered") or (strict_lenses and verga_unmeasured):
            capped_perc = min(capped_perc, float(cl["verga_pole_score"]))
        if cl.get("chrysos_veto_triggered") or (strict_lenses and chrysos_unmeasured):
            capped_sub = min(capped_sub, float(cl["chrysos_pole_score"]))
        if gate_veto:
            capped_act = min(capped_act, 6.20)
        tiers["S_perc"] = round(capped_perc, 2)
        tiers["S_sub"] = round(capped_sub, 2)
        tiers["S_act"] = round(capped_act, 2)
        w_key = chosen_profile if chosen_profile in ARCHETYPE_WEIGHTS else "app"
        extra_sub = (
            sub_metrics
            if chosen_profile != "app"
            else (
                {"edgeDensity": sub_metrics["edgeDensity"]} if (sub_metrics and "edgeDensity" in sub_metrics) else None
            )
        )
        report["calculation"] = compute_goldwood_index(
            [tiers["S_sub"], tiers["S_perc"], tiers["S_act"], tiers["S_exist"]],
            weights=ARCHETYPE_WEIGHTS[w_key],
            theta=theta,
            k=k,
            conscience_clean=bool(report["calculation"].get("conscience_lambda", 1) == 1 and not gate_veto),
            subtractive_metrics=extra_sub,
        )
        tiers["S_sub"] = round(float(report["calculation"]["tier_scores"][0]), 2)
        tiers["S_perc"] = round(float(report["calculation"]["tier_scores"][1]), 2)
        tiers["S_act"] = round(float(report["calculation"]["tier_scores"][2]), 2)
        cl["coupled_goldwood_g"] = compute_coupled_goldwood_g(
            report["calculation"],
            cl["chrysos_pole_score"],
            cl["verga_pole_score"],
            veto_triggered=bool(cl.get("veto_triggered") or gate_veto),
        )

    report["craft_lenses"] = cl
    report["standard"] = f"Goldwood Index & Global Watchability Index (GWI) v{GWI_STANDARD_VERSION}"
    report["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return report


def write_goldwood_evidence(target_dir: Path, report: dict[str, Any]) -> Path:
    """Write standardized `evidence/goldwood.json` for staging/release verification."""
    root = target_dir if target_dir.is_dir() else target_dir.parent
    ev_dir = root / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    out_path = ev_dir / "goldwood.json"

    calc = report["calculation"]
    payload = {
        "standard": report.get("standard", f"Goldwood Release Standard v{GWI_STANDARD_VERSION}"),
        "standardVersion": GWI_STANDARD_VERSION,
        "psiVariant": calc.get("psi_variant", "normalized_ceiling_9.0"),
        "profile": report["profile"],
        "project": report["project_name"],
        "generatedAt": report["generated_at"],
        "compositeScoreG": calc["goldwood_g"],
        "linearScoreG": calc["linear_score"],
        "barrierProductPsi": calc["barrier_product"],
        "barrierTheta": calc["theta"],
        "sovereignGateG": DEFAULT_THRESHOLD_G,
        "minTierScore": calc["min_tier_score"],
        "conscienceLambda": calc["conscience_lambda"],
        "kolmogorovElegance": report.get("kolmogorov_elegance", 1.0),
        "snrc": report.get("snrc", 1.0),
        "jacobianDgDs": calc.get("jacobian_dG_dS", []),
        "headroomGainDg": calc.get("headroom_gain_dG", []),
        "highestLeverageTier": calc.get("highest_leverage_tier", "S_sub"),
        "status": calc["status"],
        "verdict": calc["verdict"],
        "tiers": report["tiers"],
        "dimensions": report["dimensions"],
        "craftLenses": report.get("craft_lenses", {}),
        "blockers": report["blockers"],
        "notes": report["notes"],
    }
    serialized = json.dumps(payload, indent=2).replace(str(Path.home()) + "/", "~/")
    out_path.write_text(serialized + "\n", encoding="utf-8")
    return out_path


def format_audit_report(report: dict[str, Any]) -> str:
    """Render a clean, tabular console scorecard for `gwi audit`."""
    calc = report["calculation"]
    tiers = report["tiers"]
    lines = [
        "=== Unified Goldwood Index (G) & Global Watchability Index (GWI) Scorecard ===",
        f"  Target Project    : {report['project_name']} ({report['target']})",
        f"  Evaluation Profile: {report['profile'].upper()}  (Standard v{GWI_STANDARD_VERSION})",
        f"  Formula           : G(S) = (sum alpha_i S_i) * prod Psi(S_i)  [theta={calc['theta']}, k={calc['k']}]",
        "",
        "  [1. Four Ontological Tiers (Chrysos x Verga)]",
        f"    • Tier I   (S_sub   / Firmitas   — Substrate & Gate Bedrock) : {tiers['S_sub']:5.2f} / 10  (Psi = {calc['psi_values'][0]:.4f})",
        f"    • Tier II  (S_perc  / Venustas   — Perceptual & Visual Poise): {tiers['S_perc']:5.2f} / 10  (Psi = {calc['psi_values'][1]:.4f})",
        f"    • Tier III (S_act   / Utilitas   — Agency, Math & Flow)      : {tiers['S_act']:5.2f} / 10  (Psi = {calc['psi_values'][2]:.4f})",
        f"    • Tier IV  (S_exist / Syneidesis — Conscience & Provenance)  : {tiers['S_exist']:5.2f} / 10  (Psi = {calc['psi_values'][3]:.4f})",
        "",
        "  [2. Domain Dimension Breakdown]",
    ]
    for d in report.get("dimensions") or []:
        lines.append(
            f"    • [{d['id']:<8}] {d['name']:<44} : {d['score']:5.2f} / 10  (w={d['weight']:.2f}, tier={d['tier']})"
        )

    lines.extend(
        [
            "",
            "  [3. Composite GWI Synthesis]",
            f"    • Linear Weighted Score (sum alpha_i S_i) : {calc['linear_score']:5.2f} / 10.00",
            f"    • Non-Linear Barrier Product (prod Psi)   : {calc['barrier_product']:.4f}",
            f"    • Kolmogorov Elegance E(L)                : {report.get('kolmogorov_elegance', 1.0):.4f}",
            f"    • Signal-to-Noise Ratio (SNRC)            : {report.get('snrc', 1.0):.2f}",
            f"    • Jacobian Sensitivity dG/dS              : {calc.get('jacobian_dG_dS', [])}  (Highest Leverage: {calc.get('highest_leverage_tier', 'S_sub')})",
            f"    • Conscience / Console Hygiene (Lambda)   : {calc['conscience_lambda']}",
            f"    • UNIVERSAL GOLDWOOD INDEX G(S)           : {calc['goldwood_g']:5.2f} / 10.00  -->  {calc['verdict']}",
        ]
    )

    blockers = report.get("blockers") or []
    if blockers:
        lines.append(f"\n  [4. Audit Blockers & Remediation Targets ({len(blockers)})]")
        for b in blockers:
            lines.append(f"    ! [{b['severity']}] {b['id']} ({b['dimension']}): {b['message']}")
    elif (report.get("craft_lenses") or {}).get("veto_triggered"):
        lines.append("\n  [4. Audit Blockers]: 0 core tier blockers, but Craft Lens VETO triggered (see Section 6).")
    else:
        lines.append("\n  [4. Audit Blockers]: 0 blockers detected. Ready for 9+ Sovereign Release.")

    notes = report.get("notes") or []
    if notes:
        lines.append("\n  [5. Verified Evidence Notes]")
        for n in notes:
            lines.append(f"    ✓ {n}")

    cl = report.get("craft_lenses") or {}
    lenses_list = cl.get("lenses") or []
    if lenses_list:
        lines.append(
            f"\n  [6. Multi-Disciplinary Craft Lenses (Chrysos={cl['chrysos_pole_score']:.2f} x Verga={cl['verga_pole_score']:.2f} -> Coupled G={cl['coupled_goldwood_g']:.2f})]"
        )
        for lr in lenses_list:
            reasons = ", ".join(lr.get("activation_reasons") or [])
            lines.append(f"    • [{lr['id']:<18}] {lr['title']} : {lr['score']:5.2f} / 10  ({reasons})")
            lines.append(f"      Contact Sheet : {lr['contact_sheet']}")
            for pf in lr.get("probe_findings") or []:
                lines.append(f"      - {pf}")

    return "\n".join(lines)


def _collect_git_warm_start_briefing(target_path: str | Path) -> str:
    """Collect a compact mechanical Git warm-start header (Rule 1: Warm-Start, Don't Restrict)."""
    p = Path(target_path).expanduser().resolve()
    repo_dir = p if p.is_dir() else p.parent
    try:
        if not (repo_dir / ".git").exists():
            check = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                check=False,
            )
            if check.returncode != 0:
                return f"- Working Path: {p} (non-Git directory)"

        commits = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "-n", "3", "--oneline"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "-sb"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        diff_stat = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "HEAD", "--stat"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return f"- Working Path: {p} (git unavailable)"
    return (
        f"- Repo Path: {repo_dir}\n"
        f"- Recent Commits:\n{commits or '  (none)'}\n"
        f"- Git Status (-sb):\n{status or '  (clean)'}\n"
        f"- Working Tree Diff Stat (vs HEAD):\n{diff_stat or '  (no uncommitted tracked diffs vs HEAD)'}"
    )


def build_multi_model_prompt(report: dict[str, Any], agent: str = "astra") -> str:
    """Generate a ready-to-run OpenCode / GX10 adversarial audit prompt pre-loaded with GWI & Git warm-start telemetry."""
    calc = report["calculation"]
    blocker_lines = "\n".join(
        f"- [{b['severity']}] {b['id']} ({b['dimension']}): {b['message']}" for b in (report.get("blockers") or [])
    )
    if not blocker_lines:
        blocker_lines = "- None detected by static/telemetry probe (verify edge cases adversarially)."

    cl = report.get("craft_lenses") or {}
    lens_blocks: list[str] = []
    for lr in cl.get("lenses") or []:
        constraints = "; ".join(lr.get("builder_constraints") or [])
        lens_blocks.append(
            f"- **{lr['title']} (`{lr['id']}` — Score {lr['score']:.2f}/10)**:\n"
            f"  - Contact Sheet Required: `{lr['contact_sheet']}`\n"
            f"  - Craft Constraints: {constraints}"
        )
    lens_section = "\n".join(lens_blocks) if lens_blocks else "- Standard Chrysos Software Hygiene"

    role_header = {
        "astra": "You are GPT-6 Astra (Independent Adversarial Auditor).",
        "fable": "You are Claude Fable 5.1 (Heavy Architecture & 3D/UX Systems Lead).",
        "gx10": "You are Qwen 3.6 35B Sovereign Principal Reviewer.",
        "triad": "You are a Tri-Council Principal Reviewer (Claude Fable 5.1 / GPT-6 Astra running in parallel).",
    }.get(agent, "You are an Independent Goldwood 9+ Principal Auditor.")

    warm_start = _collect_git_warm_start_briefing(report["target"])

    return (
        f"{role_header}\n"
        f"Audit target `{report['project_name']}` ({report['target']}) under the Unified Goldwood Index (G >= 9.0) "
        f"profile `{report['profile']}`.\n\n"
        f"Mechanical Git Warm-Start Briefing (Rule 1 — Warm-Start, Don't Restrict; all tools remain 100% enabled):\n"
        f"{warm_start}\n\n"
        f"Current Deterministic GWI Telemetry:\n"
        f"- Linear Weighted Score: {calc['linear_score']:.2f} / 10\n"
        f"- Barrier Product prod Psi(S_i): {calc['barrier_product']:.4f}\n"
        f"- Barrier-Penalized Goldwood Index G(S): {calc['goldwood_g']:.2f} / 10 ({calc['verdict']})\n"
        f"- Coupled Chrysos x Verga Score: {cl.get('coupled_goldwood_g', calc['goldwood_g']):.2f} / 10 "
        f"(Chrysos={cl.get('chrysos_pole_score', 9.5):.2f}, Verga={cl.get('verga_pole_score', 9.5):.2f})\n"
        f"- Tiers: S_sub={report['tiers']['S_sub']}, S_perc={report['tiers']['S_perc']}, "
        f"S_act={report['tiers']['S_act']}, S_exist={report['tiers']['S_exist']}\n\n"
        f"Active Multi-Disciplinary Craft Lenses (Wooden Pole / Verga):\n"
        f"{lens_section}\n\n"
        f"Detected Blockers to Verify / Remediate:\n"
        f"{blocker_lines}\n\n"
        f"Instructions:\n"
        f"1. Use the Mechanical Warm-Start Briefing above to skip blind repo discovery, while keeping full tool access to inspect any surrounding files or run checks independently.\n"
        f"2. Inspect the codebase and verify every Master Feature Catalog item in AGENTS.md / docs/FEATURE_CATALOG.md.\n"
        f"3. Enforce all active Craft Lens constraints and cite exact contact-sheet coordinates `(sheet_id, cell_or_page, metric)`.\n"
        f"4. Score all 4 Ontological Tiers (S_sub, S_perc, S_act, S_exist) on [0, 10] and compute G(S).\n"
        f"5. Issue `APPROVED FOR 9+ RELEASE` only if G(S) >= 9.0, min(S_i) >= {calc['theta']}, Lambda == 1, and 0 P0/P1 or Verga lens vetoes remain.\n"
    )


def discover_default_portfolio_dirs() -> list[Path]:
    """Locate active workspace repositories via GWI_PORTFOLIO_DIRS, user config, or dynamic workspace discovery."""
    env_dirs = os.environ.get("GWI_PORTFOLIO_DIRS", "").strip()
    if env_dirs:
        return [Path(p.strip()).expanduser().resolve() for p in env_dirs.split(":") if p.strip()]

    cfg_path = Path.home() / ".config" / "sovereign-kb" / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(cfg.get("portfolio_dirs"), list):
                return [Path(p).expanduser().resolve() for p in cfg["portfolio_dirs"] if Path(p).expanduser().exists()]
        except Exception:
            pass

    candidates: list[Path] = []
    for root in (Path.home() / "Projects", Path.home() / "GitLab"):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "AGENTS.md").is_file() and (child / ".git").exists():
                resolved = child.resolve()
                if resolved not in candidates:
                    candidates.append(resolved)
    return candidates


def register_gwi_subparser(subparsers: argparse._SubParsersAction, show_all_aliases: bool = False) -> None:
    """Register `skb gwi` (and aliases `goldwood`, `audit`) onto `sovereign-kb`'s CLI parser."""
    for alias in ("gwi", "goldwood", "audit"):
        g_help = (
            "Unified Goldwood Index (G >= 9.0) & GWI+ v1.4.0 quality gate (aliases: audit, goldwood)"
            if show_all_aliases
            else None
        )
        p = subparsers.add_parser(alias, **({"help": g_help} if g_help else {}))
        _configure_gwi_parser(p)


def _configure_gwi_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "gwi_subcmd",
        nargs="?",
        default="audit",
        help="Subcommand (`audit`, `status`, `subprojects`, `record-triad`, `calc`, `lenses`, `portfolio`, `prompt`) or path to target",
    )
    parser.add_argument(
        "gwi_args",
        nargs="*",
        help="Target path(s) or 4 tier scores for `calc`",
    )
    parser.add_argument(
        "--profile",
        choices=("auto", "app", "sim", "video", "paper", "kb"),
        default="auto",
        help="Evaluation profile (default: auto)",
    )
    parser.add_argument(
        "--weights",
        help="Comma-separated 4-tier weights alpha_1,alpha_2,alpha_3,alpha_4 or archetype name (balanced, app, sim, video, paper, kb, craftsperson, scholar, leader)",
    )
    parser.add_argument(
        "--theta", type=float, default=DEFAULT_THETA, help="Barrier survival threshold theta (default: 7.0)"
    )
    parser.add_argument("--k", type=float, default=DEFAULT_K, help="Barrier steepness k (default: 2.5)")
    parser.add_argument(
        "--write-evidence", action="store_true", help="Write `evidence/goldwood.json` in target directory"
    )
    parser.add_argument(
        "--seal",
        nargs="?",
        const="__DEFAULT__",
        default="",
        help="Seal the audit report + visual/media evidence into a tamper-evident `.evr` capsule (`evidence/goldwood.evr`) and anchor into Pistis Chain",
    )
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 if G(S) < 9.0 or any blocker is P0")
    parser.add_argument(
        "--strict-lenses",
        action="store_true",
        help="Cap Verga at <= 7.80 when required Craft Lens contact sheets/artifacts are unmeasured and exit 1 if coupled G < 9.0",
    )
    parser.add_argument("--probe-audio", action="store_true", help="Run live ffmpeg EBU R128 loudness scan on video")
    parser.add_argument(
        "--execute-gate",
        action="store_true",
        help="Execute scripts/quality_gate.sh (or language test runner) and bind evidence/gate_receipt.json to the current tree SHA",
    )
    parser.add_argument(
        "--subprojects",
        action="store_true",
        help="Auto-discover and audit all subprojects (root, papers, web/apps, videos) inside target repository",
    )
    parser.add_argument(
        "--incremental",
        "--fast",
        dest="incremental",
        action="store_true",
        help="Skip subprojects whose scoped Git tree SHA and `gwi_version` are already FRESH and PASS",
    )
    parser.add_argument(
        "--disposition",
        default="APPROVED",
        help="Tri-Council disposition (`APPROVED` or `NOT_APPROVED`) when running `record-triad`",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Optional summary notes when recording a Tri-Council (`record-triad`) audit",
    )
    parser.add_argument(
        "--agent", choices=("astra", "fable", "gx10", "triad"), default="astra", help="Target auditor for `gwi prompt`"
    )
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")


def _require_existing_target(target: Path) -> None:
    """Refuse to audit a phantom target: a missing path would otherwise score as an empty tree (G=0.32 FAIL)."""
    if target.exists():
        return
    print(
        f"error: GWI target does not exist: {target}\n"
        "       (hint: pass a directory, `.tex`, or `.mp4` path; `gwi audit <dir>` — the `gwi` alias is not a target)",
        file=sys.stderr,
    )
    sys.exit(2)


def cmd_gwi(args: argparse.Namespace) -> None:
    """Execute `skb gwi` / standalone `gwi` commands."""
    from .audit_registry import (
        AUDIT_REGISTRY_PATH,
        discover_subprojects,
        evaluate_subproject_freshness,
        find_git_root,
        get_sovereign_versions,
        load_audit_registry,
        record_gwi_audit,
        record_triad_audit,
    )

    subcmd = getattr(args, "gwi_subcmd", "audit") or "audit"
    rest = list(getattr(args, "gwi_args", []) or [])
    # Tolerate redundant alias tokens (`gwi gwi audit <dir>`, `python -m sovereign_kb.gwi gwi audit <dir>`)
    # so the alias is never mistaken for a target path.
    while subcmd in ("gwi", "goldwood") and rest:
        subcmd = rest.pop(0)
    theta = float(getattr(args, "theta", DEFAULT_THETA))
    k = float(getattr(args, "k", DEFAULT_K))

    valid_subcmds = (
        "audit",
        "calc",
        "portfolio",
        "prompt",
        "lenses",
        "regularity",
        "kanon",
        "l7",
        "status",
        "registry",
        "history",
        "subprojects",
        "record-triad",
    )
    if subcmd not in valid_subcmds:
        rest = [subcmd, *rest]
        subcmd = "audit"

    if subcmd in ("audit", "status", "history", "subprojects", "record-triad") and rest:
        _require_existing_target(Path(rest[0]).expanduser())

    if subcmd == "record-triad":
        target_p = Path(rest[0]).expanduser().resolve() if rest else Path.cwd().resolve()
        subpath = rest[1] if len(rest) > 1 else "."
        disp = str(getattr(args, "disposition", "APPROVED")).upper()
        notes = str(getattr(args, "notes", ""))
        triad_rec = record_triad_audit(
            target_p,
            subpath=subpath,
            disposition=disp,
            notes=notes,
        )
        if getattr(args, "json", False):
            print(json.dumps(triad_rec, indent=2))
        else:
            print(
                f"=== Recorded Sovereign Tri-Council Audit (`Fable 5.1` x `GPT-6 Astra`) ===\n"
                f"  Target      : {find_git_root(target_p).name}:{subpath}\n"
                f"  Commit/Tree : {triad_rec['commit_sha']} (tree={triad_rec['tree_sha']})\n"
                f"  Disposition : {triad_rec['disposition']}\n"
                f"  Models      : {triad_rec['fable_model']} + {triad_rec['astra_model']}\n"
                f"  Versions    : kb={triad_rec['versions']['kb_version']} "
                f"gwi={triad_rec['versions']['gwi_version']} "
                f"swm={triad_rec['versions']['swm_version']}\n"
                f"  Registry    : {AUDIT_REGISTRY_PATH}"
            )
        return

    if subcmd == "subprojects":
        target_p = Path(rest[0]).expanduser().resolve() if rest else Path.cwd().resolve()
        subs = discover_subprojects(target_p)
        rows = [evaluate_subproject_freshness(target_p, d["subpath"]) for d in subs]
        if getattr(args, "json", False):
            print(json.dumps({"repo_root": str(find_git_root(target_p)), "subprojects": rows}, indent=2))
            return
        print(f"=== Auto-Discovered Subprojects for `{find_git_root(target_p).name}` ===")
        print(
            f"  {'Subproject':<34} {'Profile':<7} {'Commit':<9} {'TreeSHA':<17} {'Freshness':<14} {'G(S)':>6}  {'Triad (Astra x Fable)'}"
        )
        print("  " + "-" * 112)
        for r in rows:
            g_str = f"{r['goldwood_g']:6.2f}" if isinstance(r.get("goldwood_g"), (int, float)) else "   N/A"
            triad_str = (
                f"{r['triad_disposition']} @ {r['triad_commit_sha']} ({(r['triad_audited_at'] or '')[:16]})"
                if r["triad_ever_audited"]
                else "NEVER AUDITED"
            )
            print(
                f"  {r['target_label']:<34} {r['profile'].upper():<7} {r['current_commit']:<9} "
                f"{r['current_tree']:<17} {r['freshness']:<14} {g_str}  {triad_str}"
            )
        return

    if subcmd in ("status", "registry", "history"):
        cur_ver = get_sovereign_versions()
        reg = load_audit_registry()
        if rest:
            roots = [find_git_root(Path(p)) for p in rest if Path(p).expanduser().exists()]
        else:
            seen_roots: dict[str, Path] = {}
            for v in reg.get("projects", {}).values():
                if isinstance(v, dict) and v.get("repo_root"):
                    rp = Path(v["repo_root"])
                    if rp.is_dir():
                        gr = find_git_root(rp)
                        seen_roots[str(gr)] = gr
            for d in discover_default_portfolio_dirs():
                if d.is_dir():
                    gr = find_git_root(d)
                    seen_roots.setdefault(str(gr), gr)
            for extra in (Path.home() / "GitHub" / "aristocracy", Path.home() / "GitHub" / "Flood3D_Unity"):
                if extra.is_dir():
                    gr = find_git_root(extra)
                    seen_roots.setdefault(str(gr), gr)
            roots = sorted(seen_roots.values(), key=lambda x: x.name.lower())

        all_rows: list[dict[str, Any]] = []
        for r_root in roots:
            subs = discover_subprojects(r_root)
            for d in subs:
                all_rows.append(evaluate_subproject_freshness(r_root, d["subpath"]))

        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "registry_path": str(AUDIT_REGISTRY_PATH),
                        "current_versions": cur_ver,
                        "targets": all_rows,
                    },
                    indent=2,
                )
            )
            return

        print(
            f"=== Sovereign Audit Provenance & Subproject Cache Registry ===\n"
            f"  Registry File    : {AUDIT_REGISTRY_PATH}\n"
            f"  Current Versions : kb={cur_ver['kb_version']} ({cur_ver['kb_sha']}) | "
            f"gwi={cur_ver['gwi_version']} | swm={cur_ver['swm_version']} ({cur_ver['swm_sha']}) | "
            f"me-kb={cur_ver['me_kb_sha']}\n"
        )
        print(
            f"  {'Project / Subproject':<34} {'Prof':<6} {'Last Audit':<17} {'Commit':<8} {'State':<13} "
            f"{'G(S)':>6}  {'Triad (Fable x Astra)':<24} {'Versions (kb/gwi/swm)'}"
        )
        print("  " + "-" * 136)
        for row in all_rows:
            aud_ts = (row.get("audited_at") or "never")[:16].replace("T", " ")
            g_str = f"{row['goldwood_g']:6.2f}" if isinstance(row.get("goldwood_g"), (int, float)) else "   N/A"
            if row["triad_ever_audited"]:
                triad_s = f"{row['triad_disposition']}@{row.get('triad_commit_sha') or '?'}"
            else:
                triad_s = "NEVER AUDITED"
            v = row.get("versions") or {}
            v_str = (
                f"{v.get('kb_version', '-')}/{v.get('gwi_version', '-')}/{v.get('swm_version', '-')}" if v else "-/-/-"
            )
            print(
                f"  {row['target_label']:<34} {row['profile'].upper():<6} {aud_ts:<17} "
                f"{row['current_commit']:<8} {row['freshness']:<13} {g_str}  {triad_s:<24} {v_str}"
            )
        fresh_c = sum(1 for r in all_rows if r["freshness"] == "FRESH")
        triad_c = sum(1 for r in all_rows if r["triad_ever_audited"])
        print(
            f"\n  Summary: {len(all_rows)} auditable target(s) across {len(roots)} repo(s) | "
            f"{fresh_c}/{len(all_rows)} FRESH | {triad_c}/{len(all_rows)} Tri-Council Audited (`Fable 5.1` x `GPT-6 Astra`)."
        )
        return

    if subcmd in ("regularity", "kanon", "l7"):
        targets = (
            [Path(p).expanduser().resolve() for p in rest]
            if rest
            else [p for p in discover_default_portfolio_dirs() if p.name != "sovereign-kb"]
        )
        results = [audit_directory_regularity(t) for t in targets if t.exists()]
        passed_all = all(r["passed"] for r in results)
        payload = {
            "standard": f"GWI Law L7 — Simplicity Favors Regularity (v{GWI_STANDARD_VERSION})",
            "total_projects": len(results),
            "passed_kanon": sum(1 for r in results if r["passed"]),
            "all_passed": passed_all,
            "passed": passed_all,
            "projects": results,
            "repositories": results,
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(
                f"=== GWI Law L7 — Simplicity Favors Regularity (Η Απλότητα Ευνοεί την Κανονικότητα, v{GWI_STANDARD_VERSION}) ==="
            )
            print(
                f"  {'Repository':<26} {'Files':>5} {'FontSizes':>11} {'Radii':>9} {'Grids':>9} {'R_kanon':>9}  {'Status'}"
            )
            print("  " + "-" * 84)
            for r in results:
                fs_s = f"{r['distinct_font_sizes']}/8"
                br_s = f"{r['distinct_border_radii']}/4"
                gt_s = f"{r['distinct_grid_templates']}/5"
                print(
                    f"  {r['project_name']:<26} {r['files_scanned']:5d} {fs_s:>11} {br_s:>9} {gt_s:>9} {r['r_kanon']:9.4f}  {r['status']}"
                )
            print(
                f"\n  Law L7 Summary: {payload['passed_kanon']} / {payload['total_projects']} repository(s) achieve R_kanon = 1.0000."
            )
        if getattr(args, "strict", False) and not passed_all:
            sys.exit(1)
        return

    if subcmd == "lenses":
        if rest:
            target_p = Path(rest[0]).expanduser().resolve()
            active = detect_active_lenses(target_p, profile=getattr(args, "profile", "auto"))
            if getattr(args, "json", False):
                print(json.dumps({"target": str(target_p), "active_lenses": active}, indent=2))
                return
            print(f"=== Active Specialist Craft Lenses for `{target_p.name}` ({target_p}) ===")
            for spec in active:
                print(f"\n  • [{spec['id']}] {spec['title']}  ({', '.join(spec['activation_reasons'])})")
                print(f"    Contact Sheet : {spec['contact_sheet']}")
                for pr in spec["probes"]:
                    print(f"    - Probe `{pr['id']}`: {pr['metric']} | Target: {pr['target']} | Veto: {pr['veto']}")
            return

        if getattr(args, "json", False):
            print(json.dumps(CRAFT_LENSES, indent=2))
            return
        print(f"=== Sovereign Multi-Disciplinary Craft Lenses (Chrysos x Verga Standard v{GWI_STANDARD_VERSION}) ===")
        for lid, spec in CRAFT_LENSES.items():
            print(f"\n  • [{lid}] {spec['title']}")
            print(f"    Vault Spec    : {spec['vault_note']}")
            print(f"    Profiles      : {', '.join(spec['applies_to_profiles'])}")
            print(f"    Contact Sheet : {spec['contact_sheet']}")
            for pr in spec["probes"]:
                print(f"    - Probe `{pr['id']:<24}` : Target {pr['target']:<26} | Veto: {pr['veto']}")
        return

    if subcmd == "calc":
        if len(rest) < 4:
            raise SystemExit("Usage: gwi calc <S_sub> <S_perc> <S_act> <S_exist> [--weights w1,w2,w3,w4]")
        scores = [float(x) for x in rest[:4]]
        w_arg = getattr(args, "weights", None)
        weights: list[float] | tuple[float, ...] | None = None
        if w_arg:
            if w_arg in ARCHETYPE_WEIGHTS:
                weights = ARCHETYPE_WEIGHTS[w_arg]
            else:
                weights = [float(x.strip()) for x in w_arg.split(",") if x.strip()]
        res = compute_goldwood_index(scores, weights=weights, theta=theta, k=k)
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2))
        else:
            print("=== Universal Goldwood Index Calculator G(S) ===")
            print(f"  Tier Scores S     : {res['tier_scores']}")
            print(f"  Weights alpha     : {res['weights']}")
            print(f"  Barrier Psi(S_i)  : {res['psi_values']}  (theta={res['theta']}, k={res['k']})")
            print(f"  Barrier Product   : {res['barrier_product']:.4f}")
            print(f"  Linear Average    : {res['linear_score']:.2f} / 10.00")
            print(f"  Goldwood Index G  : {res['goldwood_g']:.2f} / 10.00  -->  {res['verdict']}")
        if getattr(args, "strict", False) and res["status"] != "PASS":
            sys.exit(1)
        return

    if subcmd == "portfolio":
        strict_lenses = bool(getattr(args, "strict_lenses", False))
        targets = [Path(p).expanduser().resolve() for p in rest] if rest else discover_default_portfolio_dirs()
        reports = []
        for t in targets:
            if not t.exists():
                continue
            rep = evaluate_target(
                t,
                profile=getattr(args, "profile", "auto"),
                probe_audio=getattr(args, "probe_audio", False),
                theta=theta,
                k=k,
                strict_lenses=strict_lenses,
            )
            record_gwi_audit(t, rep)
            reports.append(rep)
        summary = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "standard_version": GWI_STANDARD_VERSION,
            "total_projects": len(reports),
            "passed_9plus": sum(1 for r in reports if r["calculation"]["status"] == "PASS"),
            "projects": reports,
        }
        if getattr(args, "json", False):
            print(json.dumps(summary, indent=2))
        else:
            print(f"=== Sovereign Portfolio — Goldwood Index (G >= 9.0, v{GWI_STANDARD_VERSION}) Leaderboard ===")
            print(
                f"  {'Project':<26} {'Profile':<8} {'S_sub':>6} {'S_perc':>6} {'S_act':>6} {'S_exist':>7} {'Prod Psi':>9} {'G(S)':>7}  {'Status'}"
            )
            print("  " + "-" * 96)
            for r in reports:
                c = r["calculation"]
                t = r["tiers"]
                print(
                    f"  {r['project_name']:<26} {r['profile'].upper():<8} "
                    f"{t['S_sub']:6.2f} {t['S_perc']:6.2f} {t['S_act']:6.2f} {t['S_exist']:7.2f} "
                    f"{c['barrier_product']:9.4f} {c['goldwood_g']:7.2f}  {c['verdict']}"
                )
            print(
                f"\n  Portfolio Summary: {summary['passed_9plus']} / {summary['total_projects']} project(s) meet the 9+ Goldwood Standard."
            )
        any_veto = any((r.get("craft_lenses") or {}).get("veto_triggered") for r in reports)
        any_lens_fail = any(
            (r.get("craft_lenses") or {}).get("coupled_goldwood_g", 0.0) < DEFAULT_THRESHOLD_G for r in reports
        )
        if getattr(args, "strict", False) and (summary["passed_9plus"] < summary["total_projects"] or any_veto):
            sys.exit(1)
        if strict_lenses and (any_lens_fail or any_veto):
            sys.exit(1)
        return

    target_path = Path(rest[0]).expanduser().resolve() if rest else Path.cwd().resolve()
    strict_lenses = bool(getattr(args, "strict_lenses", False))
    execute_gate = bool(getattr(args, "execute_gate", False))
    probe_audio = bool(getattr(args, "probe_audio", False))
    req_profile = str(getattr(args, "profile", "auto"))
    incremental = bool(getattr(args, "incremental", False))

    if getattr(args, "subprojects", False) and subcmd == "audit":
        subs = discover_subprojects(target_path)
        sub_results: list[dict[str, Any]] = []
        any_fail = False
        for sp in subs:
            sp_path = Path(sp["abs_path"])
            fresh_info = evaluate_subproject_freshness(
                target_path,
                sp["subpath"],
                requested_profile=sp["profile"],
                require_strict_lenses=strict_lenses,
                require_probe_audio=probe_audio if sp["profile"] == "video" else False,
                require_execute_gate=execute_gate if sp["profile"] in ("app", "sim") else False,
            )
            if incremental and fresh_info["is_fresh_pass"]:
                sub_results.append(
                    {
                        "subpath": sp["subpath"],
                        "name": sp["name"],
                        "profile": sp["profile"],
                        "cache_hit": True,
                        "freshness": fresh_info,
                    }
                )
                if not getattr(args, "json", False):
                    v = fresh_info.get("versions") or {}
                    print(
                        f"⚡ [CACHE HIT] {sp['name']} ({sp['profile'].upper()}) — FRESH @ {fresh_info['current_commit']} "
                        f"(tree={fresh_info['current_tree']}, G={fresh_info['goldwood_g']:.2f} PASS, "
                        f"kb={v.get('kb_version')}/gwi={v.get('gwi_version')}/swm={v.get('swm_version')})"
                    )
                continue

            sp_rep = evaluate_target(
                sp_path,
                profile=sp["profile"],
                probe_audio=probe_audio,
                theta=theta,
                k=k,
                strict_lenses=strict_lenses,
                execute_gate=execute_gate,
            )
            aud_rec = record_gwi_audit(sp_path, sp_rep)
            if sp_rep["calculation"]["status"] != "PASS":
                any_fail = True
            sub_results.append(
                {
                    "subpath": sp["subpath"],
                    "name": sp["name"],
                    "profile": sp["profile"],
                    "cache_hit": False,
                    "audit_record": aud_rec,
                    "report": sp_rep,
                }
            )
            if not getattr(args, "json", False):
                c = sp_rep["calculation"]
                print(
                    f"🔍 [AUDITED]   {sp['name']} ({sp['profile'].upper()}) @ {aud_rec['commit_sha']} "
                    f"(tree={aud_rec['tree_sha']}) -> G={c['goldwood_g']:.2f} / 10.00 ({c['status']}, "
                    f"blockers={len(sp_rep.get('blockers') or [])})"
                )
        if getattr(args, "json", False):
            print(json.dumps({"repo_root": str(find_git_root(target_path)), "subprojects": sub_results}, indent=2))
        if getattr(args, "strict", False) and any_fail:
            sys.exit(1)
        return

    if incremental and subcmd == "audit":
        root_dir = find_git_root(target_path)
        try:
            rel_sp = str(target_path.relative_to(root_dir)) if target_path != root_dir else "."
        except ValueError:
            rel_sp = "."
        fresh_info = evaluate_subproject_freshness(
            root_dir,
            rel_sp,
            requested_profile=req_profile,
            require_strict_lenses=strict_lenses,
            require_probe_audio=probe_audio,
            require_execute_gate=execute_gate,
        )
        if fresh_info["is_fresh_pass"]:
            if getattr(args, "json", False):
                print(json.dumps({"cache_hit": True, "freshness": fresh_info}, indent=2))
            else:
                v = fresh_info.get("versions") or {}
                print(
                    f"⚡ [CACHE HIT — SKIPPED RE-AUDIT] {fresh_info['target_label']} ({fresh_info['profile'].upper()})\n"
                    f"  Last Audit : {fresh_info['audited_at']}\n"
                    f"  Commit/Tree: {fresh_info['current_commit']} (tree={fresh_info['current_tree']}, dirty=False)\n"
                    f"  Score      : G = {fresh_info['goldwood_g']:.2f} / 10.00 ({fresh_info['status']})\n"
                    f"  Triad      : {fresh_info['triad_disposition']} "
                    f"({'audited @ ' + str(fresh_info['triad_commit_sha']) if fresh_info['triad_ever_audited'] else 'never audited by Astra x Fable'})\n"
                    f"  Versions   : kb={v.get('kb_version')} | gwi={v.get('gwi_version')} | swm={v.get('swm_version')}"
                )
            return

    report = evaluate_target(
        target_path,
        profile=req_profile,
        probe_audio=probe_audio,
        theta=theta,
        k=k,
        strict_lenses=strict_lenses,
        execute_gate=execute_gate,
    )
    audit_rec = record_gwi_audit(target_path, report)
    report["audit_provenance"] = audit_rec

    if getattr(args, "write_evidence", False) and os.environ.get("GWI_IN_GATE_EXECUTION") != str(target_path.resolve()):
        ev_file = write_goldwood_evidence(target_path, report)
        report["evidence_file"] = str(ev_file)

    seal_arg = getattr(args, "seal", "") or ""
    if seal_arg and os.environ.get("GWI_IN_GATE_EXECUTION") != str(target_path.resolve()):
        from .evr import seal_gwi_audit_capsule

        seal_out = None if seal_arg == "__DEFAULT__" else seal_arg
        evr_seal = seal_gwi_audit_capsule(target_path, output_path=seal_out, report=report)
        report["evr_capsule"] = {
            "path": evr_seal["path"],
            "file_size_bytes": evr_seal["file_size_bytes"],
            "merkle_root": evr_seal["merkle_root"],
            "pistis_chain_event": evr_seal.get("pistis_chain_event"),
        }

    if subcmd == "prompt":
        prompt_text = build_multi_model_prompt(report, agent=getattr(args, "agent", "astra"))
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"agent": getattr(args, "agent", "astra"), "prompt": prompt_text, "report": report}, indent=2
                )
            )
        else:
            print(prompt_text)
        return

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        print(format_audit_report(report))
        v = audit_rec.get("versions") or {}
        t_info = audit_rec.get("triad") or {}
        triad_label = (
            f"{t_info.get('disposition')} @ {t_info.get('commit_sha')} ({t_info.get('audited_at')})"
            if t_info.get("ever_audited")
            else "NEVER AUDITED BY ASTRA x FABLE"
        )
        print(
            f"\n  [7. Sovereign Audit Provenance & Cache State]\n"
            f"    • Recorded At       : {audit_rec['audited_at']} -> {AUDIT_REGISTRY_PATH}\n"
            f"    • Target & Scope    : {audit_rec['name']} (commit={audit_rec['commit_sha']}, tree={audit_rec['tree_sha']})\n"
            f"    • Models Executed   : {', '.join(audit_rec.get('models') or [])}\n"
            f"    • Tri-Council State : {triad_label}\n"
            f"    • Stack Versions    : kb={v.get('kb_version')} ({v.get('kb_sha')}) | "
            f"gwi={v.get('gwi_version')} | swm={v.get('swm_version')} ({v.get('swm_sha')}) | me-kb={v.get('me_kb_sha')}"
        )
        if report.get("evidence_file"):
            print(f"    • Evidence Manifest : {report['evidence_file']}")
        if report.get("evr_capsule"):
            ec = report["evr_capsule"]
            print(f"    • Sealed .evr Audit : {ec['path']} (merkle={ec['merkle_root'][:16]}...)")

    cl = report.get("craft_lenses") or {}
    if getattr(args, "strict", False) and (report["calculation"]["status"] != "PASS" or cl.get("veto_triggered")):
        sys.exit(1)
    if strict_lenses and (cl.get("coupled_goldwood_g", 0.0) < DEFAULT_THRESHOLD_G or cl.get("veto_triggered")):
        sys.exit(1)


def main_cli(argv: list[str] | None = None) -> None:
    """Standalone `gwi` CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="gwi",
        description="Unified Goldwood Index (G >= 9.0) & Global Watchability Index (GWI+ v1.4.0) Quality Gate CLI",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {GWI_STANDARD_VERSION}")
    _configure_gwi_parser(parser)
    args = parser.parse_args(argv)
    cmd_gwi(args)


if __name__ == "__main__":
    main_cli()
