"""Bi-Polar Specialist Craft Lens registry (`LENS_EVALUATORS`) and dispatcher (`evaluate_craft_lenses`).

Each lens is one `_lens_<id>(ctx)` evaluator in `lenses_verga_craft`, `lenses_verga_ux` or `lenses_chrysos`.
It receives the per-lens state as a `SimpleNamespace` (`score`, `probe_findings`, `probe_measurements`,
`veto_triggered`, `unmeasured_count` and the shared audit context), mutates it in place, and writes rebound
scalars back onto `ctx`. `sovereign_kb.gwi_lenses` re-exports this registry (same dict object).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sovereign_kb.gwi_core import VERGA_UNMEASURED_CAP, compute_coupled_goldwood_g
from sovereign_kb.gwi_science import SCI_LENS_ID

from .lenses_catalog import CRAFT_LENSES
from .lenses_chrysos import (
    _lens_ai_ml_science,
    _lens_data_api_architect,
    _lens_principal_verification,
    _lens_science_integrity,
    _lens_web_perf_edge,
)
from .lenses_probes import detect_active_lenses
from .lenses_verga_craft import _lens_spatial_cinema, _lens_technical_artist, _lens_typography_editor
from .lenses_verga_ux import _lens_ux_ergonomics

LENS_EVALUATORS: dict[str, Callable[[SimpleNamespace], None]] = {
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

LENS_MIN_MEASURED_PROBES = 3
LENS_THIN_EVIDENCE_PENALTY = 0.15
_MEASURED_PREFIXES = ("PASS", "WARN", "VETO", "FAIL")


def lens_probe_coverage(probe_findings: list[str]) -> dict[str, Any]:
    """Count measured vs. unmeasured probes from a lens's findings ledger.

    A finding starting with PASS/WARN/VETO/FAIL is a measured probe; UNMEASURED is not.
    """
    measured = sum(1 for f in probe_findings if str(f).lstrip().startswith(_MEASURED_PREFIXES))
    unmeasured = sum(1 for f in probe_findings if str(f).lstrip().startswith("UNMEASURED"))
    total = measured + unmeasured
    return {
        "measured": measured,
        "unmeasured": unmeasured,
        "ratio": round(measured / total, 4) if total else 0.0,
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

        lens_fn = LENS_EVALUATORS.get(lid)
        if lens_fn is not None:
            ctx = SimpleNamespace(
                collected_subtractive_metrics=collected_subtractive_metrics,
                probe_findings=probe_findings,
                probe_measurements=probe_measurements,
                report=report,
                resolved=resolved,
                root=root,
                sci_audit=sci_audit,
                score=score,
                strict_lenses=strict_lenses,
                unmeasured_count=unmeasured_count,
                veto_triggered=veto_triggered,
            )
            lens_fn(ctx)
            sci_audit = ctx.sci_audit
            score = ctx.score
            unmeasured_count = ctx.unmeasured_count
            veto_triggered = ctx.veto_triggered

        coverage = lens_probe_coverage(probe_findings)
        if strict_lenses and not veto_triggered:
            if unmeasured_count > 0 or coverage["measured"] == 0:
                score = min(score, VERGA_UNMEASURED_CAP)
            else:
                # Thin evidence must not score like broad evidence: each measured probe short of
                # LENS_MIN_MEASURED_PROBES costs LENS_THIN_EVIDENCE_PENALTY.
                score -= LENS_THIN_EVIDENCE_PENALTY * max(0, LENS_MIN_MEASURED_PROBES - coverage["measured"])

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
                "probe_coverage": coverage,
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
