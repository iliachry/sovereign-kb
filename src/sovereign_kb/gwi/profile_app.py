"""Goldwood App/Web/Backend quality evaluation profile (`evaluate_app_profile`)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import (
    ARCHETYPE_WEIGHTS,
    DEFAULT_K,
    DEFAULT_SUBTRACTIVE_BUDGETS,
    DEFAULT_THETA,
    aggregate_tier_pair,
    compute_goldwood_index,
    compute_kolmogorov_elegance,
    compute_snrc,
)

from .lenses_laws import _audit_human_wedge_anti_palantir, _audit_structural_regularity
from .utils import (
    _JS_RANDOM_RE,
    _PY_RANDOM_RE,
    _PY_SEEDED_RE,
    _SECRET_LEAK_RE,
    ALLOW_RANDOM_MARKER,
    _check_bash_negated_grep_trap,
    _extract_catalog_ids,
    _is_test_file,
    _iter_source_files,
)


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
    test_files = [
        p for p in _iter_source_files(root, (".ts", ".tsx", ".js", ".mjs", ".py", ".rs")) if _is_test_file(p, root)
    ]
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
        if not _is_test_file(p, root)
    ]
    py_files = [p for p in _iter_source_files(root, (".py",)) if not _is_test_file(p, root)]
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
        if _is_test_file(sp, root) or sp.name in ("gwi.py", "agents.py"):
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
