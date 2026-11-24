"""Portfolio cartridge packers for KB vaults, Poetics LBVR shows, and 3D Digital Twins."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .builder import EvrBuilder
from .codec import _semantic_embed_64d
from .constants import EvrKind
from .keys import _resolve_seal_key
from .reader import EvrReader


def pack_vault_knowledge_cartridge(
    vault_dir: str | Path,
    output_path: str | Path | None = None,
    sign_key: str | None = None,
) -> dict[str, Any]:
    """
    Wedge 3: Pack a Sovereign KB vault into an installable zero-copy Knowledge Cartridge (`cartridge.evr`)
    with 64-byte-aligned dense 64-d document embeddings (`TENSOR`), `[[wikilink]]` topology (`GRAPH`),
    `BM25 + Vec64` postings (`INDEX`), `SQLite3 + FTS5` catalog (`SQLITE`), and Soar/RSI cognitive state (`STATE`).
    """
    v_root = Path(vault_dir).expanduser().resolve()
    if not v_root.is_dir():
        raise ValueError(f"Vault directory '{v_root}' does not exist")
    out_evr = Path(output_path).expanduser().resolve() if output_path else (v_root / "vault_cartridge.evr")

    sign_key = _resolve_seal_key(sign_key)
    builder = EvrBuilder(
        title=f"Sovereign Knowledge Cartridge — {v_root.name}",
        author="Sovereign Organon (Episteme)",
        description=f"Zero-copy 64B-aligned Knowledge Cartridge for {v_root.name} (hips / xrOS / GX10 ready).",
        polyglot=True,
        sign_key=sign_key,
    )

    md_files = sorted(
        p
        for p in v_root.rglob("*.md")
        if not p.is_symlink() and not any(part.startswith(".") for part in p.relative_to(v_root).parts)
    )
    doc_paths: list[str] = []
    flat_embeddings: list[float] = []
    for md_p in md_files:
        rel = md_p.relative_to(v_root).as_posix()
        txt = md_p.read_text(encoding="utf-8", errors="replace")
        builder.add_text(f"notes/{rel}", txt, kind=EvrKind.DOCUMENT)
        doc_paths.append(f"notes/{rel}")
        flat_embeddings.extend(_semantic_embed_64d(txt[:16384]))

    if not doc_paths:
        builder.add_text("notes/INDEX.md", "# Empty Vault Cartridge\n", kind=EvrKind.DOCUMENT)
        doc_paths.append("notes/INDEX.md")
        flat_embeddings.extend(_semantic_embed_64d("# Empty Vault Cartridge"))

    n_docs = len(doc_paths)
    builder.add_tensor(
        "tensors/vault_embeddings_64d.tensor",
        values=flat_embeddings,
        shape=[n_docs, 64],
        dtype="f32",
        metadata={"doc_count": n_docs, "embedding_dim": 64, "doc_paths": doc_paths[:128]},
    )

    soar_p = v_root / ".kb" / "learned_chunks.soar"
    if soar_p.is_file():
        builder.add_bytes("soar/learned_chunks.soar", soar_p.read_bytes(), kind=EvrKind.CODE)

    policy_p = v_root / ".kb" / "retrieval_policy.json"
    if policy_p.is_file():
        try:
            builder.add_state("state/retrieval_policy.json", json.loads(policy_p.read_text(encoding="utf-8")))
        except Exception:
            pass
    else:
        builder.add_state(
            "state/retrieval_policy.json",
            {"vault_name": v_root.name, "notes_packed": n_docs, "cartridge_format": "evr-1.0"},
        )

    written = builder.write(out_evr)
    with EvrReader(written) as reader:
        ver = reader.verify(sign_key=sign_key)
        summ = reader.summary()
        summ["verification"] = ver
        summ["notes_packed"] = n_docs
        summ["embedding_tensor_shape"] = [n_docs, 64]
    return summ


def pack_poetics_show_capsule(
    target_path: str | Path | None = None,
    output_path: str | Path | None = None,
    sign_key: str | None = None,
) -> dict[str, Any]:
    """
    Wedge 4: Pack a Poetics LBVR / Pods Offline Show Package & Law L10 Session Capsule (`show.evr`)
    combining 64B-aligned media/subtitles (`MEDIA`), 3D stage/pod geometry (`SPATIAL`),
    BLE biometric HR/HRV telemetry (`TENSOR`), cue timeline (`TABLE` -> `SQLite3`),
    and Law L10 visitor resonance offering & venue entitlement (`STATE`).
    """
    out_evr = Path(output_path or target_path or "poetics_show_capsule.evr").expanduser().resolve()
    sign_key = _resolve_seal_key(sign_key)
    builder = EvrBuilder(
        title="Poetics LBVR & Pods Offline Show Capsule (.evr v1.0)",
        author="Poetics Spatial Cinema Engine",
        description="Offline-first venue show package and Law L10 visitor resonance session capsule.",
        polyglot=True,
        sign_key=sign_key,
    )

    builder.add_text(
        "docs/show_bible.md",
        """# Poetics Offline Show & Session Capsule
See [[spatial/pod_stage.obj]] and [[tables/cue_timeline.csv]] for deterministic frame-locked playback.
""",
        kind=EvrKind.DOCUMENT,
    )
    builder.add_text(
        "media/captions_en.srt",
        "1\n00:00:00,000 --> 00:00:04,000\nWelcome to the Poetics Spatial Pod.\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\nOffer one authentic memory to unlock the scene.\n",
        kind=EvrKind.MEDIA,
    )
    builder.add_text(
        "spatial/pod_stage.obj",
        "v -2.0 0.0 -2.0\nv 2.0 0.0 -2.0\nv 2.0 0.0 2.0\nv -2.0 0.0 2.0\nv 0.0 2.6 0.0\n"
        "f 1 2 5\nf 2 3 5\nf 3 4 5\nf 4 1 5\n",
        kind=EvrKind.SPATIAL,
    )
    builder.add_text(
        "tables/cue_timeline.csv",
        "cue_id,timestamp_ms,lighting_preset,spatial_audio_bus,haptic_intensity\n"
        "CUE_01,0,warm_amber_dusk,ambisonics_bed,0.20\n"
        "CUE_02,4000,cobalt_horizon,dialogue_center,0.65\n"
        "CUE_03,8000,goldwood_bloom,choir_zenith,0.90\n",
        kind=EvrKind.TABLE,
    )
    biometric_hr_hrv = [
        round(72.0 + 6.0 * math.sin(i * 0.25) if c == 0 else 48.0 + 5.0 * math.cos(i * 0.25), 3)
        for i in range(16)
        for c in range(2)
    ]
    builder.add_tensor(
        "tensors/ble_biometrics_hr_hrv.tensor",
        values=biometric_hr_hrv,
        shape=[16, 2],
        dtype="f32",
        metadata={"channels": ["heart_rate_bpm", "rmssd_hrv_ms"], "sampling_hz": 1.0},
    )
    builder.add_state(
        "state/law_l10_offering_and_entitlement.json",
        {
            "venue_id": "poetics-pod-athens-01",
            "offline_entitlement": "VALID_OFFLINE_SHOW_LEASE",
            "law_l10_generative_offering": {
                "offering_type": "emotional_resonance_word",
                "visitor_word": "Nostalgia",
                "parasitic_friction": 0,
                "generative_friction_satisfied": True,
            },
        },
    )
    written = builder.write(out_evr)
    with EvrReader(written) as reader:
        ver = reader.verify(sign_key=sign_key)
        summ = reader.summary()
        summ["verification"] = ver
    return summ


def pack_digital_twin_snapshot(
    target_path: str | Path | None = None,
    output_path: str | Path | None = None,
    sign_key: str | None = None,
) -> dict[str, Any]:
    """
    Wedge 5: Pack an NTUA 3D Digital Twin & 2D Shallow-Water Equations (`SWE`) Flood Snapshot (`twin.evr`)
    with 64B-aligned WebGPU-ready depth/velocity tensors (`TENSOR`), 3D terrain mesh (`SPATIAL`),
    hydrometric gauge time-series (`TABLE` -> `SQLite3`), and explicit CRS/datum metadata (`STATE`).
    """
    out_evr = Path(output_path or target_path or "digital_twin_snapshot.evr").expanduser().resolve()
    sign_key = _resolve_seal_key(sign_key)
    builder = EvrBuilder(
        title="3D GIS & 2D SWE Flood Digital Twin Snapshot (.evr v1.0)",
        author="Sovereign Geospatial Digital Twin Engine",
        description="Range-addressable 3D terrain + 64B-aligned WebGPU SWE depth/velocity fields + CRS metadata.",
        polyglot=True,
        sign_key=sign_key,
    )

    builder.add_text(
        "docs/scenario_brief.md",
        """# 3D Flood Digital Twin Scenario Snapshot
All `TENSOR` fields obey 64-byte alignment (`offset % 64 == 0`), satisfying WebGPU `StorageBuffer`
16-byte alignment (`64 = 4 * 16`) for direct zero-copy GPU upload.
""",
        kind=EvrKind.DOCUMENT,
    )
    builder.add_text(
        "spatial/terrain_channel.obj",
        "v 0.0 12.5 0.0\nv 10.0 11.8 0.0\nv 10.0 11.2 10.0\nv 0.0 12.0 10.0\nf 1 2 3\nf 1 3 4\n",
        kind=EvrKind.SPATIAL,
    )
    swe_depth = [
        round(max(0.0, 1.85 * math.exp(-((r - 3.5) ** 2 + (c - 3.5) ** 2) / 6.0)), 4)
        for r in range(8)
        for c in range(8)
    ]
    builder.add_tensor(
        "tensors/swe_water_depth_m.tensor",
        values=swe_depth,
        shape=[8, 8],
        dtype="f32",
        metadata={"field": "water_depth_h", "units": "m", "webgpu_storage_aligned": True},
    )
    builder.add_text(
        "tables/gauge_stations.csv",
        "station_id,crs_epsg,discharge_m3s,stage_m,uncertainty_m\n"
        "GAUGE_KIFISOS_01,2100,142.5,3.18,0.04\n"
        "GAUGE_ILISSOS_02,2100,68.2,1.94,0.03\n",
        kind=EvrKind.TABLE,
    )
    builder.add_state(
        "state/crs_and_solver_state.json",
        {
            "horizontal_crs": "EPSG:2100 (GGRS87 / Greek Grid)",
            "display_crs": "EPSG:4326 (WGS84)",
            "vertical_datum": "EGM2008 Orthometric Height (m)",
            "solver": "2D Shallow-Water Equations (HLLC Finite-Volume)",
            "courant_cfl": 0.45,
            "mass_conservation_error_pct": 0.00012,
        },
    )
    written = builder.write(out_evr)
    with EvrReader(written) as reader:
        ver = reader.verify(sign_key=sign_key)
        summ = reader.summary()
        summ["verification"] = ver
    return summ
