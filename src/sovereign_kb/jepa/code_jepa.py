"""Polyglot AST Code-Graph JEPA (`skb jepa code`): latent symbol coupling & impact prediction."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from sovereign_kb.frontier_organon import CodeGraphIndexer

from .features import JEPATransition, dot, hash_text_vector
from .model import JEPAConfig, VaultJEPA, compute_collapse_metrics


def run_code_graph_jepa(
    repo_dir: Path,
    query_symbol: str | None = None,
    top_k: int = 5,
    epochs: int = 20,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Build a polyglot AST symbol & call graph via ``CodeGraphIndexer``, train ``VaultJEPA``
    over call, sequential, and test-to-impl symbol transitions, benchmark held-out coupling,
    and predict top-k latently impacted symbols for ``query_symbol``.
    """
    r_root = Path(repo_dir).expanduser().resolve()
    rep = CodeGraphIndexer(r_root).build_graph(persist=False, export_hips=False)
    nodes = rep["nodes"]
    edges = rep["edges"]
    if len(nodes) < 4:
        raise ValueError(f"Code graph at '{r_root}' has too few symbols ({len(nodes)}) for JEPA.")

    sym_vecs: dict[str, list[float]] = {}
    sym_meta: dict[str, dict[str, Any]] = {}
    by_file: dict[str, list[Any]] = {}
    for n in nodes:
        txt = f"{n.language} {n.kind} {n.name} {n.signature} {n.docstring} {' '.join(n.calls)}"
        sym_vecs[n.symbol_id] = hash_text_vector(txt, dim=128)
        sym_meta[n.symbol_id] = {
            "symbol_id": n.symbol_id,
            "name": n.name,
            "kind": n.kind,
            "file_path": n.file_path,
            "start_line": n.start_line,
            "in_degree": n.in_degree,
        }
        by_file.setdefault(n.file_path, []).append(n)

    transitions: list[JEPATransition] = []
    for e in edges:
        if e.target_id and e.source_id in sym_vecs and e.target_id in sym_vecs and e.source_id != e.target_id:
            is_test = "test_" in sym_meta[e.source_id]["name"].lower() or "tests/" in e.file_path
            action = "EPISODIC_TO_SEMANTIC" if is_test else "WIKILINK"
            transitions.append(
                JEPATransition(
                    src_key=e.source_id,
                    action=action,
                    tgt_key=e.target_id,
                    src_vec=sym_vecs[e.source_id],
                    tgt_vec=sym_vecs[e.target_id],
                    src_path=e.source_id,
                    tgt_path=e.target_id,
                )
            )

    for f_nodes in by_file.values():
        f_sorted = sorted(f_nodes, key=lambda x: x.start_line)
        for i in range(len(f_sorted) - 1):
            n1, n2 = f_sorted[i], f_sorted[i + 1]
            transitions.append(
                JEPATransition(
                    src_key=n1.symbol_id,
                    action="NEXT_SECTION",
                    tgt_key=n2.symbol_id,
                    src_vec=sym_vecs[n1.symbol_id],
                    tgt_vec=sym_vecs[n2.symbol_id],
                    src_path=n1.symbol_id,
                    tgt_path=n2.symbol_id,
                )
            )

    if len(transitions) < 4:
        raise ValueError(f"Code graph at '{r_root}' has too few symbol transitions ({len(transitions)}).")

    rng = random.Random(seed)
    rng.shuffle(transitions)
    transitions = transitions[:800]
    n_test = max(1, int(round(0.2 * len(transitions))))
    if n_test >= len(transitions):
        n_test = len(transitions) - 1
    held_t = transitions[:n_test]
    train_t = transitions[n_test:]

    cfg = JEPAConfig(input_dim=128, proj_dim=64, latent_dim=32, hidden_dim=48, epochs=epochs, seed=seed)
    model = VaultJEPA(cfg)
    train_stats = model.train(train_t)

    tgt_latents = {sid: model.encode_target(vec) for sid, vec in sym_vecs.items()}
    pos_e: list[float] = []
    neg_e: list[float] = []
    latents: list[list[float]] = []
    all_sids = list(sym_vecs.keys())
    for tr in held_t:
        sx = model.encode_context(tr.src_vec)
        sy = model.encode_target(tr.tgt_vec)
        shat = model.predict(sx, tr.action)
        pos_e.append(model.energy(shat, sy))
        latents.append(sx)
        nk = rng.choice(all_sids)
        neg_e.append(model.energy(shat, tgt_latents[nk]))
    collapse = compute_collapse_metrics(latents, pos_e, neg_e, cfg.latent_dim)

    # Evaluate held-out symbol coupling ranking on up to 100 transitions
    eval_sub = held_t[:100]
    mrr_raw = 0.0
    mrr_jepa = 0.0
    r10_raw = 0
    r10_jepa = 0
    for tr in eval_sub:
        sx = model.encode_context(tr.src_vec)
        shat = model.predict(sx, tr.action)
        cands = [sid for sid in all_sids if sid != tr.src_key]
        raw_r = sorted(cands, key=lambda s: dot(tr.src_vec, sym_vecs[s]), reverse=True)
        jepa_r = sorted(cands, key=lambda s: model.energy(shat, tgt_latents[s]))
        for is_j, rlist in ((False, raw_r), (True, jepa_r)):
            try:
                rk = rlist.index(tr.tgt_key) + 1
            except ValueError:
                rk = len(rlist) + 1
            if rk <= 5:
                if is_j:
                    mrr_jepa += 1.0 / rk
                else:
                    mrr_raw += 1.0 / rk
            if rk <= 10:
                if is_j:
                    r10_jepa += 1
                else:
                    r10_raw += 1

    n_ev = float(len(eval_sub))
    # Predict impacted symbols for query_symbol (or top-centrality symbol)
    seed_sym: str | None = None
    if query_symbol:
        ql = query_symbol.lower()
        matches = [sid for sid, m in sym_meta.items() if ql in m["name"].lower() or ql in sid.lower()]
        if matches:
            matches.sort(key=lambda s: -sym_meta[s]["in_degree"])
            seed_sym = matches[0]
    if seed_sym is None:
        seed_sym = max(all_sids, key=lambda s: sym_meta[s]["in_degree"])

    sx_seed = model.encode_context(sym_vecs[seed_sym])
    shat_seed = model.predict(sx_seed, "WIKILINK")
    impact_ranked = sorted(
        (sid for sid in all_sids if sid != seed_sym),
        key=lambda s: model.energy(shat_seed, tgt_latents[s]),
    )[: max(1, top_k)]
    impacts = [
        {
            **sym_meta[sid],
            "energy": round(model.energy(shat_seed, tgt_latents[sid]), 4),
        }
        for sid in impact_ranked
    ]

    return {
        "repo_dir": str(r_root),
        "total_symbols": len(nodes),
        "resolved_edges": len(edges),
        "train_transitions": len(train_t),
        "heldout_transitions": len(held_t),
        "train_stats": train_stats,
        "collapse_metrics": collapse,
        "raw_mrr5": round(mrr_raw / n_ev, 4),
        "jepa_mrr5": round(mrr_jepa / n_ev, 4),
        "raw_r10": round(r10_raw / n_ev, 4),
        "jepa_r10": round(r10_jepa / n_ev, 4),
        "seed_symbol": sym_meta[seed_sym],
        "predicted_impacts": impacts,
    }
