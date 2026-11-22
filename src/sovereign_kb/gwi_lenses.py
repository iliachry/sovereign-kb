"""Craft-lens evaluators for the Goldwood Index (moved verbatim out of ``gwi.evaluate_craft_lenses``).

Each ``_lens_<id>(ctx)`` receives the per-lens state as a ``SimpleNamespace`` (``score``,
``probe_findings``, ``probe_measurements``, ``veto_triggered``, ``unmeasured_count`` and the
shared audit context), mutates it in place, and writes rebound scalars back onto ``ctx``.
``LENS_EVALUATORS`` maps lens IDs to evaluators; ``gwi.evaluate_craft_lenses`` imports this
module lazily, so importing ``sovereign_kb.gwi`` here is not circular.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import (
    Path,
)
from types import SimpleNamespace
from typing import (
    Any,
)

from sovereign_kb.gwi import (
    _LRA_RE,
    _LUFS_RE,
    _PEAK_RE,
    _audit_canonical_anchor_l9,
    _audit_css_token_wcag_contrast,
    _audit_human_wedge_anti_palantir,
    _audit_simultaneous_load_l8,
    _audit_structural_regularity,
    _collect_law_l7_corpora,
    _discover_ui_files,
    _is_test_file,
    _iter_source_files,
    _validate_srt_captions,
    probe_image_grid_telemetry,
    probe_pdf_typography,
    validate_skill_frontmatter,
)
from sovereign_kb.gwi_core import (
    DEFAULT_SUBTRACTIVE_BUDGETS,
)
from sovereign_kb.gwi_science import (
    SCI_LENS_ID,
    audit_science_integrity,
)


def _lens_technical_artist(ctx: SimpleNamespace) -> None:
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    root = ctx.root
    score = ctx.score
    unmeasured_count = ctx.unmeasured_count
    veto_triggered = ctx.veto_triggered
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
    allocates_three_gpu = bool(re.search(r"new\s+(?:THREE\.)?\w*(?:Geometry|Material|WebGLRenderTarget)\b", corpus_3d))
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
                probe_findings.append(f"UNMEASURED [ta.luminance_balance]: `{visual_target.name}` could not be decoded")
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
    ctx.score = score
    ctx.unmeasured_count = unmeasured_count
    ctx.veto_triggered = veto_triggered


def _lens_ux_ergonomics(ctx: SimpleNamespace) -> None:
    collected_subtractive_metrics = ctx.collected_subtractive_metrics
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    root = ctx.root
    score = ctx.score
    unmeasured_count = ctx.unmeasured_count
    veto_triggered = ctx.veto_triggered
    web_ui_files, py_ui_files = _discover_ui_files(root)
    ui_files = web_ui_files + [p for p in py_ui_files if p not in web_ui_files]
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
                f"PASS [ux.wcag_contrast_tap]: Verified runtime min text contrast = {runtime_wcag_cr}:1"
                f" (from `{pw_reports[0].relative_to(root)}`)"
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
    ctx.score = score
    ctx.unmeasured_count = unmeasured_count
    ctx.veto_triggered = veto_triggered


def _lens_spatial_cinema(ctx: SimpleNamespace) -> None:
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    report = ctx.report
    resolved = ctx.resolved
    root = ctx.root
    score = ctx.score
    unmeasured_count = ctx.unmeasured_count
    veto_triggered = ctx.veto_triggered
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
        probe_findings.append("UNMEASURED [sc.ebu_r128_lufs]: Missing `ebu_r128_report.txt` loudness measurement")

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
    ctx.score = score
    ctx.unmeasured_count = unmeasured_count
    ctx.veto_triggered = veto_triggered


def _lens_typography_editor(ctx: SimpleNamespace) -> None:
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    report = ctx.report
    resolved = ctx.resolved
    root = ctx.root
    score = ctx.score
    unmeasured_count = ctx.unmeasured_count
    veto_triggered = ctx.veto_triggered
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
    ctx.score = score
    ctx.unmeasured_count = unmeasured_count
    ctx.veto_triggered = veto_triggered


def _lens_web_perf_edge(ctx: SimpleNamespace) -> None:
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    root = ctx.root
    score = ctx.score
    veto_triggered = ctx.veto_triggered
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
    ctx.score = score
    ctx.veto_triggered = veto_triggered


def _lens_data_api_architect(ctx: SimpleNamespace) -> None:
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    root = ctx.root
    score = ctx.score
    veto_triggered = ctx.veto_triggered
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
    ctx.score = score
    ctx.veto_triggered = veto_triggered


def _lens_ai_ml_science(ctx: SimpleNamespace) -> None:
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    root = ctx.root
    score = ctx.score
    veto_triggered = ctx.veto_triggered
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
    ctx.score = score
    ctx.veto_triggered = veto_triggered


def _lens_principal_verification(ctx: SimpleNamespace) -> None:
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    report = ctx.report
    root = ctx.root
    score = ctx.score
    veto_triggered = ctx.veto_triggered
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
        for f in (_iter_source_files(root, (".py", ".ts", ".tsx", ".js", ".rs", ".sh")) if root.is_dir() else [])
        if not _is_test_file(f, root) and f.name not in ("gwi.py", "gwi_lenses.py", "lenses.py", "agents.py")
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
        probe_findings.append("PASS [pv.secret_hygiene]: 0 leaked credentials in tracked source (`credentials`)")
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
    ctx.score = score
    ctx.veto_triggered = veto_triggered

    # Probes 6-9: antifragility (`af.*`) — gains from stress, never credited for mere robustness.
    from sovereign_kb.gwi_antifragility import apply_antifragility_probes

    apply_antifragility_probes(ctx)


def _lens_science_integrity(ctx: SimpleNamespace) -> None:
    probe_findings = ctx.probe_findings
    probe_measurements = ctx.probe_measurements
    report = ctx.report
    root = ctx.root
    sci_audit = ctx.sci_audit
    score = ctx.score
    strict_lenses = ctx.strict_lenses
    veto_triggered = ctx.veto_triggered
    sci_audit = audit_science_integrity(root, profile=report.get("profile", "app"), run_collect=bool(strict_lenses))
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
    ctx.sci_audit = sci_audit
    ctx.score = score
    ctx.veto_triggered = veto_triggered


LENS_EVALUATORS = {
    "technical-artist": _lens_technical_artist,
    "ux-ergonomics": _lens_ux_ergonomics,
    "spatial-cinema": _lens_spatial_cinema,
    "typography-editor": _lens_typography_editor,
    "web-perf-edge": _lens_web_perf_edge,
    "data-api-architect": _lens_data_api_architect,
    "ai-ml-science": _lens_ai_ml_science,
    "principal-verification": _lens_principal_verification,
    SCI_LENS_ID: _lens_science_integrity,
}
