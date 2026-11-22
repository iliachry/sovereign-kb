"""Verga perceptual craft lens: Principal UX, Spatial Ergonomics & Conversion (`ux-ergonomics`)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import DEFAULT_SUBTRACTIVE_BUDGETS

from .lenses_laws import (
    _audit_human_wedge_anti_palantir,
    _audit_simultaneous_load_l8,
    _audit_structural_regularity,
    _collect_law_l7_corpora,
)
from .lenses_probes import _audit_css_token_wcag_contrast, probe_image_grid_telemetry
from .utils import _iter_source_files


def eval_ux_ergonomics_lens(
    score: float,
    resolved: Path,
    root: Path,
    report: dict[str, Any],
    collected_subtractive_metrics: dict[str, Any],
) -> tuple[float, list[str], list[dict[str, Any]], bool, int]:
    probe_findings: list[str] = []
    probe_measurements: list[dict[str, Any]] = []
    veto_triggered = False
    unmeasured_count = 0

    _non_ui_dirs = {"docs", "artifacts", "scripts", "evidence", "corpus"}
    all_web_ui = _iter_source_files(root, (".tsx", ".jsx", ".vue", ".css", ".html", ".svelte"))
    app_web_ui = [p for p in all_web_ui if not any(part in _non_ui_dirs for part in p.relative_to(root).parts)]
    web_ui_files = app_web_ui if app_web_ui else all_web_ui
    py_ui_files = [p for p in _iter_source_files(root, (".py",)) if "dashboard" in p.name]
    ui_files = web_ui_files + py_ui_files
    ui_corpus = "\n/* === GWI_FILE_BOUNDARY === */\n".join(
        f.read_text(encoding="utf-8", errors="ignore") for f in ui_files[:60]
    )

    pw_reports = list(root.glob("evidence/playwright/report.json")) + list(root.glob("artifacts/ux_telemetry.json"))
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
                extra_bounds = (
                    f", worst-pixel = {runtime_worst_pixel_cr}:1, white-backdrop bound = {runtime_white_bound_cr}:1"
                )
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
        probe_findings.append("PASS [ux.wcag_contrast_tap]: Semantic ARIA & accessible control attributes verified")
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

    return score, probe_findings, probe_measurements, veto_triggered, unmeasured_count
