"""Goldwood 3D Simulation/Game engine profile (`evaluate_sim_profile`)."""

from __future__ import annotations

import json
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

from .utils import (
    _check_bash_negated_grep_trap,
    _extract_catalog_ids,
    _iter_source_files,
)


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

    dimensions: list[dict[str, Any]] = [
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
