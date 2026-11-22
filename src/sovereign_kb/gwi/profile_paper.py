"""Goldwood Scientific Manuscript (`paper`) profile (`evaluate_paper_profile`)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import (
    ARCHETYPE_WEIGHTS,
    DEFAULT_K,
    DEFAULT_THETA,
    compute_goldwood_index,
    compute_kolmogorov_elegance,
    compute_snrc,
)

from .utils import _MD_BOLD_LEAK_RE, _OVERFULL_PT_RE


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

    dimensions: list[dict[str, Any]] = [
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
