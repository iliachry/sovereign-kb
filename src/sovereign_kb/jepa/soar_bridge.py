"""Neuro-Symbolic Soar 9.6 <-> Vault JEPA Impasse Resolver (System 1 intuition + System 2 symbolic rules)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sovereign_kb.retrieval import retrieve_from_vault
from sovereign_kb.soar.chunk_store import ChunkStore
from sovereign_kb.soar.deliberation import EnergyResolver, solve
from sovereign_kb.soar.kernel import SoarKernel
from sovereign_kb.soar.parser import format_production
from sovereign_kb.soar.vault_agent import learned_chunks_path

from .features import embed_query_for_corpus, load_vault_corpus
from .model import JEPAConfig, VaultJEPA
from .ops import DEFAULT_CHECKPOINT_REL, train_vault_jepa


def _soar_sym(raw: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", raw.strip()).strip("-")
    if not cleaned:
        return fallback
    if cleaned[0].isdigit():
        cleaned = f"n-{cleaned}"
    return cleaned[:48]


def resolve_impasse_with_jepa(
    vault_dir: Path,
    query: str,
    candidates: list[dict[str, Any]] | None = None,
    persist_chunks: bool = False,
) -> dict[str, Any]:
    """
    Score competing retrieval candidates using System 1 ``VaultJEPA`` latent energy,
    project them into a Soar 9.6 Working Memory tie impasse (``S1 -> S2``), resolve
    dominance in substate ``S2``, and compile a ``chunk*`` rule for 0-impasse replay.
    """
    v_root = Path(vault_dir).expanduser().resolve()
    ckpt_path = v_root / DEFAULT_CHECKPOINT_REL
    if not ckpt_path.is_file():
        corpus_init = load_vault_corpus(v_root)
        train_vault_jepa(v_root, config=JEPAConfig(input_dim=corpus_init.input_dim, epochs=12))

    corpus = load_vault_corpus(v_root)
    model, _ = VaultJEPA.load(ckpt_path)

    if candidates is None:
        base_payload = retrieve_from_vault(v_root, query, top_k=4)
        candidates = list(base_payload.get("results", [])) if isinstance(base_payload, dict) else list(base_payload)
        if len(candidates) < 2:
            from .ops import predict_latent

            roll = predict_latent(v_root, query, action="WIKILINK", steps=1, top_k=4)
            candidates = list(roll.get("rollout", [{}])[0].get("matches", []))

    # Deduplicate by rel_path while preserving order
    dedup: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for c in candidates:
        rp = str(c.get("rel_path", ""))
        if rp and rp not in seen_paths:
            seen_paths.add(rp)
            dedup.append(c)

    if not dedup:
        return {
            "query": query,
            "resolved": False,
            "winner_doc": None,
            "first_run_impasses": 0,
            "replay_impasses": 0,
            "zero_impasse_replay": False,
            "compiled_chunks": [],
            "scored_candidates": [],
        }

    q_vec, _ = embed_query_for_corpus(v_root, corpus, query)
    sq = model.encode_context(q_vec)
    sq_pred = model.predict(sq, "WIKILINK")

    scored: list[dict[str, Any]] = []
    for idx, cand in enumerate(dedup[:4]):
        rp = str(cand["rel_path"])
        if rp in corpus.doc_vectors:
            sy = model.encode_target(corpus.doc_vectors[rp])
            e = 0.65 * model.energy(sq_pred, sy) + 0.35 * model.energy(sq, sy)
        else:
            e = 1.0
        jepa_int = max(1, int(round(1000.0 * max(0.01, 1.0 - 0.5 * e))))
        scored.append(
            {
                "rel_path": rp,
                "doc_sym": _soar_sym(Path(rp).stem, f"doc{idx}"),
                "jepa_energy": round(e, 4),
                "jepa_score": jepa_int,
                "rrf_score": float(cand.get("rrf_score", 0.0)),
            }
        )

    scored.sort(key=lambda item: (item["jepa_energy"], -item["rrf_score"], item["rel_path"]))

    winner = scored[0]
    runner_up = scored[1] if len(scored) >= 2 else None

    if runner_up is None:
        return {
            "query": query,
            "resolved": True,
            "winner_doc": winner["rel_path"],
            "winner_jepa_energy": winner["jepa_energy"],
            "runner_up_doc": None,
            "first_run_impasses": 0,
            "replay_impasses": 0,
            "zero_impasse_replay": False,
            "compiled_chunks": [],
            "scored_candidates": scored,
        }

    # Project the top-2 candidates into a Soar tie. No S2 evaluation rule is written by hand: the
    # shared deliberation loop asks VaultJEPA (lowest energy wins) while the agent sits in S2, compiles
    # the choice into a general S2 rule, chunks it, and replays with the chunk only.
    top = scored[:2]
    sym_to_cand = {f"C{i + 1}-{c['doc_sym']}": c for i, c in enumerate(top)}
    energy_by_sym = {sym: float(c["jepa_energy"]) for sym, c in sym_to_cand.items()}
    cand_wmes = " ".join(f"^cand <c{i + 1}>" for i in range(len(top)))
    cand_objs = "\n".join(f"    (<c{i + 1}> ^doc {sym})" for i, sym in enumerate(sym_to_cand))

    soar_src = f"""
sp {{jepa-soar*propose*init
    (state <s> ^superstate nil -^name)
-->
    (<s> ^operator <o> + =)
    (<o> ^name init-jepa-tie)
}}

sp {{jepa-soar*apply*init
    :o-support
    (state <s> ^operator <o>)
    (<o> ^name init-jepa-tie)
-->
    (<s> ^name skb-jepa-tie ^phase tie-pending {cand_wmes})
{cand_objs}
}}

sp {{jepa-soar*propose*select-doc
    (state <s> ^name skb-jepa-tie ^phase tie-pending ^cand <c>)
    (<c> ^doc <d>)
-->
    (<s> ^operator <o> +)
    (<o> ^name select-doc ^doc <d>)
}}

sp {{jepa-soar*apply*select-doc
    :o-support
    (state <s> ^name skb-jepa-tie ^phase tie-pending ^operator <o>)
    (<o> ^name select-doc ^doc <d>)
-->
    (<s> ^phase tie-pending -)
    (<s> ^phase resolved ^selected-doc <d>)
}}

sp {{jepa-soar*detect*goal
    (state <s> ^name skb-jepa-tie ^phase resolved ^selected-doc <d>)
-->
    (<s> ^goal-achieved yes)
    (halt)
}}
"""

    kernel = SoarKernel(enable_chunking=True)
    kernel.load_soar_code(soar_src, clear_existing=True)
    resolver = EnergyResolver(lambda attrs: energy_by_sym.get(str(attrs.get("doc", ""))), label="jepa")
    report = solve(kernel, resolver, max_cycles=25)

    first_impasses = int(report["run1_impasses"])
    replay_impasses = int(report["replay_impasses"])
    compiled_chunks = [c["name"] for c in report["chunks"]]
    chosen_sym = next(
        (str(w.value) for w in kernel.wmes.values() if w.id == "S1" and w.attr == "selected-doc"),
        None,
    )
    chosen = sym_to_cand.get(chosen_sym or "", winner)
    runner = next((c for c in top if c is not chosen), runner_up)

    if persist_chunks and compiled_chunks and v_root.is_dir():
        try:
            ChunkStore.for_vault(v_root).upsert_chunks_from_kernel(kernel, format_production, domain="skb-jepa-tie")
            cp = learned_chunks_path(v_root)
            cp.parent.mkdir(parents=True, exist_ok=True)
            chunk_text = "\n\n".join(
                format_production(kernel.productions[n]) for n in compiled_chunks if n in kernel.productions
            )
            if chunk_text:
                existing = cp.read_text(encoding="utf-8") if cp.is_file() else ""
                if chunk_text not in existing:
                    cp.write_text((existing + "\n\n" + chunk_text).strip() + "\n", encoding="utf-8")
        except Exception:
            pass

    return {
        "query": query,
        "resolved": bool(report["solved"]),
        "winner_doc": chosen["rel_path"],
        "winner_jepa_energy": chosen["jepa_energy"],
        "runner_up_doc": runner["rel_path"],
        "first_run_impasses": first_impasses,
        "replay_impasses": replay_impasses,
        "zero_impasse_replay": (
            first_impasses >= 1 and replay_impasses == 0 and bool(report["replay_solved"]) and len(compiled_chunks) >= 1
        ),
        "compiled_chunks": compiled_chunks,
        "scored_candidates": scored,
        "deliberations": report["deliberations"],
    }
