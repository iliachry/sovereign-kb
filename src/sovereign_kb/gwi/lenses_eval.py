"""Bi-Polar Specialist Craft Lens evaluator dispatcher (`evaluate_craft_lenses`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import VERGA_UNMEASURED_CAP, compute_coupled_goldwood_g
from sovereign_kb.gwi_science import SCI_LENS_ID

from .lenses_catalog import CRAFT_LENSES
from .lenses_chrysos import eval_chrysos_lens
from .lenses_probes import detect_active_lenses
from .lenses_verga_craft import eval_verga_craft_lens
from .lenses_verga_ux import eval_ux_ergonomics_lens


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

        if lid in ("technical-artist", "spatial-cinema", "typography-editor"):
            score, probe_findings, probe_measurements, veto_triggered, unmeasured_count = eval_verga_craft_lens(
                lid, score, resolved, root, report, strict_lenses
            )
        elif lid == "ux-ergonomics":
            score, probe_findings, probe_measurements, veto_triggered, unmeasured_count = eval_ux_ergonomics_lens(
                score, resolved, root, report, collected_subtractive_metrics
            )
        elif lid in ("web-perf-edge", "data-api-architect", "ai-ml-science", "principal-verification", SCI_LENS_ID):
            score, probe_findings, probe_measurements, veto_triggered, unmeasured_count, sci_upd = eval_chrysos_lens(
                lid, score, resolved, root, report, strict_lenses
            )
            if sci_upd is not None:
                sci_audit = sci_upd

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
