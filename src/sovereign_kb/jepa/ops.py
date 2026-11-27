"""High-level Vault JEPA operations: train, predict, gaps, surprise, bench, status."""

from __future__ import annotations

import datetime
import random
from pathlib import Path
from typing import Any

from .features import (
    ACTIONS,
    JEPATransition,
    VaultCorpus,
    dot,
    embed_query_for_corpus,
    extract_transitions,
    load_vault_corpus,
)
from .model import JEPAConfig, VaultJEPA, compute_collapse_metrics

DEFAULT_CHECKPOINT_REL = ".kb/jepa_model.json"


def _evaluate_pair_energies(
    model: VaultJEPA,
    pairs: list[JEPATransition],
    all_doc_targets: dict[str, list[float]],
    seed: int = 42,
) -> tuple[list[float], list[float], list[list[float]]]:
    rng = random.Random(seed)
    doc_keys = list(all_doc_targets.keys())
    pos_energies: list[float] = []
    neg_energies: list[float] = []
    latents: list[list[float]] = []

    for tr in pairs:
        sx = model.encode_context(tr.src_vec)
        sy = model.encode_target(tr.tgt_vec)
        shat = model.predict(sx, tr.action)
        pos_energies.append(model.energy(shat, sy))
        latents.append(sx)
        if len(doc_keys) >= 2:
            neg_key = tr.tgt_path
            for _ in range(5):
                cand = rng.choice(doc_keys)
                if cand != tr.tgt_path and cand != tr.src_path:
                    neg_key = cand
                    break
            neg_energies.append(model.energy(shat, all_doc_targets[neg_key]))
    return pos_energies, neg_energies, latents


def train_vault_jepa(
    vault_dir: Path,
    config: JEPAConfig | None = None,
    evr_out: Path | None = None,
) -> dict[str, Any]:
    """Train ``VaultJEPA`` on ``vault_dir`` and persist ``.kb/jepa_model.json``."""
    v_root = Path(vault_dir).expanduser().resolve()
    corpus = load_vault_corpus(v_root)
    seed = config.seed if config is not None else 42
    train_pairs, heldout_pairs = extract_transitions(corpus, seed=seed)
    if not train_pairs:
        raise ValueError(
            "No JEPA transitions found in vault (need [[wikilink]] edges, episodic notes, or multi-chunk documents)."
        )

    cfg = config or JEPAConfig(input_dim=corpus.input_dim)
    cfg.input_dim = corpus.input_dim
    model = VaultJEPA(cfg)

    eval_pairs = heldout_pairs if heldout_pairs else train_pairs
    pre_targets = {p: model.encode_target(v) for p, v in corpus.doc_vectors.items()}
    pre_pos, _, _ = _evaluate_pair_energies(model, eval_pairs, pre_targets, seed=cfg.seed)
    untrained_mean_energy = round(sum(pre_pos) / len(pre_pos), 6)

    train_stats = model.train(train_pairs)

    post_targets = {p: model.encode_target(v) for p, v in corpus.doc_vectors.items()}
    post_pos, post_neg, post_latents = _evaluate_pair_energies(model, eval_pairs, post_targets, seed=cfg.seed)
    collapse = compute_collapse_metrics(post_latents, post_pos, post_neg, cfg.latent_dim)

    action_counts = {a: sum(1 for tr in train_pairs + heldout_pairs if tr.action == a) for a in ACTIONS}
    ckpt_path = v_root / DEFAULT_CHECKPOINT_REL
    metadata = {
        "trained_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "vault_name": v_root.name,
        "embedding_mode": corpus.embedding_mode,
        "doc_count": len(corpus.doc_paths),
        "train_transitions": len(train_pairs),
        "heldout_transitions": len(heldout_pairs),
        "action_counts": action_counts,
        "untrained_heldout_energy": untrained_mean_energy,
        "collapse_metrics": collapse,
        "train_stats": train_stats,
    }
    sha = model.save(ckpt_path, metadata=metadata)

    evr_path_str: str | None = None
    if evr_out is not None:
        written = model.export_evr(Path(evr_out).expanduser().resolve(), metadata=metadata)
        evr_path_str = str(written)

    return {
        "vault_dir": str(v_root),
        "checkpoint_path": str(ckpt_path),
        "model_sha256": sha,
        "evr_path": evr_path_str,
        "embedding_mode": corpus.embedding_mode,
        "input_dim": corpus.input_dim,
        "effective_in_dim": cfg.effective_in_dim(),
        "latent_dim": cfg.latent_dim,
        "doc_count": len(corpus.doc_paths),
        "train_transitions": len(train_pairs),
        "heldout_transitions": len(heldout_pairs),
        "action_counts": action_counts,
        "untrained_heldout_energy": untrained_mean_energy,
        "trained_heldout_energy": collapse["mean_pos_energy"],
        "train_stats": train_stats,
        "collapse_metrics": collapse,
    }


def _load_model_and_corpus(vault_dir: Path) -> tuple[VaultCorpus, VaultJEPA, dict[str, Any]]:
    v_root = Path(vault_dir).expanduser().resolve()
    ckpt_path = v_root / DEFAULT_CHECKPOINT_REL
    if not ckpt_path.is_file():
        raise FileNotFoundError(f"JEPA checkpoint not found at '{ckpt_path}'. Run `skb jepa train` first.")
    corpus = load_vault_corpus(v_root)
    model, raw = VaultJEPA.load(ckpt_path)
    if model.config.input_dim != corpus.input_dim:
        raise ValueError(
            f"JEPA checkpoint input_dim={model.config.input_dim} does not match corpus input_dim={corpus.input_dim}. "
            "Re-run `skb jepa train`."
        )
    return corpus, model, raw


def predict_latent(
    vault_dir: Path,
    query_or_note: str,
    action: str = "WIKILINK",
    steps: int = 1,
    top_k: int = 5,
) -> dict[str, Any]:
    """Roll out ``steps`` latent JEPA transitions from ``query_or_note`` and rank target notes by energy."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}'. Expected one of {ACTIONS}.")
    corpus, model, _ = _load_model_and_corpus(vault_dir)
    q_vec, matched_doc = embed_query_for_corpus(corpus.vault_dir, corpus, query_or_note)
    tgt_latents = {p: model.encode_target(v) for p, v in corpus.doc_vectors.items()}

    rollout_states = model.rollout(q_vec, action=action, steps=steps)
    step_results: list[dict[str, Any]] = []
    for step_idx, s_hat in enumerate(rollout_states, start=1):
        ranked: list[tuple[float, str]] = []
        for p, sy in tgt_latents.items():
            if step_idx == 1 and matched_doc is not None and p == matched_doc:
                continue
            e = model.energy(s_hat, sy)
            ranked.append((e, p))
        ranked.sort(key=lambda item: item[0])
        matches = [
            {
                "rel_path": p,
                "title": corpus.doc_titles.get(p, ""),
                "doc_type": corpus.doc_types.get(p, ""),
                "energy": round(e, 6),
                "compatibility": round(1.0 - 0.5 * e, 6),
            }
            for e, p in ranked[: max(1, top_k)]
        ]
        step_results.append({"step": step_idx, "matches": matches})

    return {
        "query": query_or_note,
        "matched_doc": matched_doc,
        "action": action,
        "steps": len(rollout_states),
        "rollout": step_results,
    }


def discover_gaps(
    vault_dir: Path,
    top_k: int = 10,
    action: str = "WIKILINK",
) -> dict[str, Any]:
    """Find unlinked ``(src, tgt)`` document pairs with lowest predicted JEPA energy."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}'. Expected one of {ACTIONS}.")
    corpus, model, _ = _load_model_and_corpus(vault_dir)
    existing_links = set(corpus.links)
    tgt_latents = {p: model.encode_target(v) for p, v in corpus.doc_vectors.items()}

    candidates = [
        p
        for p in corpus.doc_paths
        if corpus.doc_types.get(p, "") != "moc"
        and (not corpus.native_embedded_docs or p in corpus.native_embedded_docs)
    ]
    if len(candidates) < 2:
        candidates = list(corpus.doc_paths)

    gaps: list[tuple[float, str, str]] = []
    for src in candidates:
        sx = model.encode_context(corpus.doc_vectors[src])
        shat = model.predict(sx, action)
        for tgt in candidates:
            if src == tgt or (src, tgt) in existing_links:
                continue
            e = model.energy(shat, tgt_latents[tgt])
            gaps.append((e, src, tgt))

    gaps.sort(key=lambda item: item[0])
    limit = max(1, top_k)
    src_counts: dict[str, int] = {}
    tgt_counts: dict[str, int] = {}
    selected: list[tuple[float, str, str]] = []
    for e, src, tgt in gaps:
        if src_counts.get(src, 0) >= 2 or tgt_counts.get(tgt, 0) >= 2:
            continue
        selected.append((e, src, tgt))
        src_counts[src] = src_counts.get(src, 0) + 1
        tgt_counts[tgt] = tgt_counts.get(tgt, 0) + 1
        if len(selected) >= limit:
            break

    top_gaps = [
        {
            "source_path": src,
            "target_path": tgt,
            "source_title": corpus.doc_titles.get(src, ""),
            "target_title": corpus.doc_titles.get(tgt, ""),
            "action": action,
            "energy": round(e, 6),
            "compatibility": round(1.0 - 0.5 * e, 6),
        }
        for e, src, tgt in selected
    ]
    return {
        "action": action,
        "candidates_scanned": len(candidates),
        "gaps": top_gaps,
    }


def detect_surprise(
    vault_dir: Path,
    top_k: int = 10,
    action: str | None = None,
) -> dict[str, Any]:
    """Score existing transitions and return the highest-energy (most surprising/drifted) edges."""
    if action is not None and action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}'. Expected one of {ACTIONS}.")
    corpus, model, _ = _load_model_and_corpus(vault_dir)
    train_t, held_t = extract_transitions(corpus, seed=model.config.seed)
    all_t = train_t + held_t
    if action is not None:
        all_t = [tr for tr in all_t if tr.action == action]

    scored: list[tuple[float, JEPATransition]] = []
    for tr in all_t:
        sx = model.encode_context(tr.src_vec)
        sy = model.encode_target(tr.tgt_vec)
        shat = model.predict(sx, tr.action)
        scored.append((model.energy(shat, sy), tr))

    scored.sort(key=lambda item: item[0], reverse=True)
    mean_e = sum(e for e, _ in scored) / len(scored) if scored else 0.0
    items = [
        {
            "source_key": tr.src_key,
            "target_key": tr.tgt_key,
            "action": tr.action,
            "energy": round(e, 6),
        }
        for e, tr in scored[: max(1, top_k)]
    ]
    return {
        "transitions_scored": len(scored),
        "mean_energy": round(mean_e, 6),
        "surprises": items,
    }


def _eval_ranking(
    pairs: list[JEPATransition],
    doc_paths: list[str],
    raw_vecs: dict[str, list[float]],
    model: VaultJEPA,
    tgt_latents: dict[str, list[float]],
) -> dict[str, Any]:
    """Evaluate raw cosine, JEPA latent energy, and calibrated Hybrid RRF on held-out transitions."""
    if not pairs:
        return {"count": 0}

    mrr5 = {"raw": 0.0, "jepa": 0.0, "hybrid": 0.0}
    rec5 = {"raw": 0, "jepa": 0, "hybrid": 0}
    rec10 = {"raw": 0, "jepa": 0, "hybrid": 0}

    for tr in pairs:
        sx = model.encode_context(tr.src_vec)
        shat = model.predict(sx, tr.action)

        raw_sims = {p: dot(tr.src_vec, raw_vecs[p]) for p in doc_paths if p != tr.src_path}
        jepa_sims = {p: dot(shat, tgt_latents[p]) for p in doc_paths if p != tr.src_path}

        raw_ranked = sorted(raw_sims.keys(), key=lambda p: raw_sims[p], reverse=True)
        jepa_ranked = sorted(jepa_sims.keys(), key=lambda p: jepa_sims[p], reverse=True)

        raw_rank = {p: idx + 1 for idx, p in enumerate(raw_ranked)}
        jepa_rank = {p: idx + 1 for idx, p in enumerate(jepa_ranked)}
        hybrid_ranked = sorted(
            raw_rank.keys(),
            key=lambda p: (
                0.70 / (20.0 + jepa_rank[p]) + 0.30 / (20.0 + raw_rank[p]) + 0.15 * jepa_sims[p] + 0.05 * raw_sims[p]
            ),
            reverse=True,
        )

        for name, ranking in (("raw", raw_ranked), ("jepa", jepa_ranked), ("hybrid", hybrid_ranked)):
            try:
                rank = ranking.index(tr.tgt_path) + 1
            except ValueError:
                rank = len(ranking) + 1
            if rank <= 5:
                mrr5[name] += 1.0 / rank
                rec5[name] += 1
            if rank <= 10:
                rec10[name] += 1

    n = float(len(pairs))
    raw_mrr = mrr5["raw"] / n
    jepa_mrr = mrr5["jepa"] / n
    hyb_mrr = mrr5["hybrid"] / n
    raw_r5 = rec5["raw"] / n
    jepa_r5 = rec5["jepa"] / n
    hyb_r5 = rec5["hybrid"] / n

    return {
        "count": len(pairs),
        "raw_cosine": {
            "mrr_at_5": round(raw_mrr, 4),
            "recall_at_5": round(raw_r5, 4),
            "recall_at_10": round(rec10["raw"] / n, 4),
        },
        "jepa": {
            "mrr_at_5": round(jepa_mrr, 4),
            "recall_at_5": round(jepa_r5, 4),
            "recall_at_10": round(rec10["jepa"] / n, 4),
        },
        "hybrid_rrf": {
            "mrr_at_5": round(hyb_mrr, 4),
            "recall_at_5": round(hyb_r5, 4),
            "recall_at_10": round(rec10["hybrid"] / n, 4),
        },
        "lift_jepa_vs_raw_mrr5": round(jepa_mrr - raw_mrr, 4),
        "lift_hybrid_vs_raw_mrr5": round(hyb_mrr - raw_mrr, 4),
        "lift_jepa_vs_raw_recall5": round(jepa_r5 - raw_r5, 4),
    }


def _eval_two_hop_rollout(
    train_pairs: list[JEPATransition],
    heldout_pairs: list[JEPATransition],
    doc_paths: list[str],
    raw_vecs: dict[str, list[float]],
    model: VaultJEPA,
    tgt_latents: dict[str, list[float]],
    max_eval: int = 200,
) -> dict[str, Any]:
    """Evaluate 2-step latent rollout ``s0 -> s1 -> s2`` on held-out 2-hop transitions."""
    held_by_src: dict[str, list[JEPATransition]] = {}
    for tr in heldout_pairs:
        if tr.tgt_path in raw_vecs and tr.src_path != tr.tgt_path:
            held_by_src.setdefault(tr.src_key, []).append(tr)

    chains: list[tuple[JEPATransition, JEPATransition]] = []
    for tr1 in train_pairs:
        if tr1.src_path not in raw_vecs:
            continue
        for tr2 in held_by_src.get(tr1.tgt_key, ()):
            if tr2.tgt_path not in (tr1.src_path, tr1.tgt_path):
                chains.append((tr1, tr2))
                if len(chains) >= max_eval:
                    break
        if len(chains) >= max_eval:
            break

    if not chains:
        return {"count": 0}

    mrr5_raw = 0.0
    mrr5_jepa = 0.0
    rec10_raw = 0
    rec10_jepa = 0
    for tr1, tr2 in chains:
        s0 = model.encode_context(tr1.src_vec)
        s1 = model.predict(s0, tr1.action)
        s2_hat = model.predict(s1, tr2.action)

        cands = [p for p in doc_paths if p != tr1.src_path]
        raw_ranked = sorted(cands, key=lambda p: dot(tr1.src_vec, raw_vecs[p]), reverse=True)
        jepa_ranked = sorted(cands, key=lambda p: model.energy(s2_hat, tgt_latents[p]))

        for is_jepa, ranking in ((False, raw_ranked), (True, jepa_ranked)):
            try:
                rank = ranking.index(tr2.tgt_path) + 1
            except ValueError:
                rank = len(ranking) + 1
            if rank <= 5:
                if is_jepa:
                    mrr5_jepa += 1.0 / rank
                else:
                    mrr5_raw += 1.0 / rank
            if rank <= 10:
                if is_jepa:
                    rec10_jepa += 1
                else:
                    rec10_raw += 1

    n = float(len(chains))
    return {
        "count": len(chains),
        "raw_cosine": {"mrr_at_5": round(mrr5_raw / n, 4), "recall_at_10": round(rec10_raw / n, 4)},
        "jepa_2step": {"mrr_at_5": round(mrr5_jepa / n, 4), "recall_at_10": round(rec10_jepa / n, 4)},
        "lift_mrr5": round((mrr5_jepa - mrr5_raw) / n, 4),
        "lift_recall10": round((rec10_jepa - rec10_raw) / n, 4),
    }


def benchmark_jepa(vault_dir: Path, config: JEPAConfig | None = None) -> dict[str, Any]:
    """Train on the 80% train split and benchmark Raw Cosine vs Vault JEPA vs Hybrid RRF on held-out links."""
    v_root = Path(vault_dir).expanduser().resolve()
    corpus = load_vault_corpus(v_root)
    seed = config.seed if config is not None else 42
    train_pairs, heldout_pairs = extract_transitions(corpus, seed=seed)
    if not train_pairs:
        raise ValueError("No JEPA transitions found in vault.")

    cfg = config or JEPAConfig(input_dim=corpus.input_dim)
    cfg.input_dim = corpus.input_dim
    model = VaultJEPA(cfg)
    train_stats = model.train(train_pairs)

    tgt_latents = {p: model.encode_target(v) for p, v in corpus.doc_vectors.items()}
    eval_set = heldout_pairs if heldout_pairs else train_pairs
    doc_eval_set = [tr for tr in eval_set if tr.tgt_path in corpus.doc_vectors and tr.src_path != tr.tgt_path]

    pos_e, neg_e, latents = _evaluate_pair_energies(model, eval_set, tgt_latents, seed=cfg.seed)
    collapse = compute_collapse_metrics(latents, pos_e, neg_e, cfg.latent_dim)
    ranking = _eval_ranking(doc_eval_set, corpus.doc_paths, corpus.doc_vectors, model, tgt_latents)
    two_hop = _eval_two_hop_rollout(
        train_pairs, heldout_pairs, corpus.doc_paths, corpus.doc_vectors, model, tgt_latents
    )

    per_action: dict[str, Any] = {}
    for action in ACTIONS:
        sub = [tr for tr in doc_eval_set if tr.action == action]
        if sub:
            per_action[action] = _eval_ranking(sub, corpus.doc_paths, corpus.doc_vectors, model, tgt_latents)

    return {
        "vault_dir": str(v_root),
        "embedding_mode": corpus.embedding_mode,
        "doc_count": len(corpus.doc_paths),
        "train_transitions": len(train_pairs),
        "heldout_transitions": len(heldout_pairs),
        "train_stats": train_stats,
        "collapse_metrics": collapse,
        "ranking": ranking,
        "two_hop_rollout": two_hop,
        "per_action": per_action,
    }


def plan_latent_trajectory(
    vault_dir: Path,
    start: str,
    goal: str,
    max_steps: int = 4,
    beam_width: int = 4,
) -> dict[str, Any]:
    """Goal-conditioned beam search over JEPA action rollouts from ``start`` to ``goal``."""
    corpus, model, _ = _load_model_and_corpus(vault_dir)
    start_vec, start_doc = embed_query_for_corpus(corpus.vault_dir, corpus, start)
    goal_vec, goal_doc = embed_query_for_corpus(corpus.vault_dir, corpus, goal)

    s0 = model.encode_context(start_vec)
    s_goal = model.encode_target(goal_vec)
    init_energy = model.energy(s0, s_goal)
    tgt_latents = {p: model.encode_target(v) for p, v in corpus.doc_vectors.items()}

    initial_visited = {start_doc} if start_doc else set()
    beam: list[tuple[float, float, list[float], list[dict[str, Any]], set[str]]] = [
        (init_energy, init_energy, s0, [], initial_visited)
    ]
    best_overall: tuple[float, float, list[dict[str, Any]]] | None = None
    completed: list[tuple[float, float, list[dict[str, Any]]]] = []

    steps_limit = max(1, min(8, max_steps))
    b_width = max(1, min(12, beam_width))

    for depth in range(1, steps_limit + 1):
        candidates: list[tuple[float, float, list[float], list[dict[str, Any]], set[str]]] = []
        for _sc, _e, s_cur, trace, visited in beam:
            for action in ACTIONS:
                s_next = model.predict(s_cur, action)
                e_goal = model.energy(s_next, s_goal)

                best_doc = ""
                best_doc_e = 999.0
                for p, sy in tgt_latents.items():
                    if p in visited:
                        continue
                    ed = model.energy(s_next, sy)
                    if ed < best_doc_e:
                        best_doc_e = ed
                        best_doc = p

                step_rec = {
                    "step": depth,
                    "action": action,
                    "grounded_doc": best_doc,
                    "grounded_title": corpus.doc_titles.get(best_doc, ""),
                    "energy_to_goal": round(e_goal, 6),
                }
                new_trace = [*trace, step_rec]
                new_visited = set(visited)
                if best_doc:
                    new_visited.add(best_doc)

                score = e_goal + 0.015 * depth
                candidates.append((score, e_goal, s_next, new_trace, new_visited))
                if best_overall is None or score < best_overall[0]:
                    best_overall = (score, e_goal, new_trace)

        candidates.sort(key=lambda item: item[0])
        beam = candidates[:b_width]
        completed.extend((sc, eg, tr) for sc, eg, _s, tr, _v in beam)

    assert best_overall is not None

    completed.sort(key=lambda item: item[0])
    alt_plans = [{"final_energy": round(eg, 6), "steps": tr} for _sc, eg, tr in completed[:3] if tr != best_overall[2]]

    return {
        "start": start,
        "start_doc": start_doc,
        "goal": goal,
        "goal_doc": goal_doc,
        "initial_energy": round(init_energy, 6),
        "final_energy": round(best_overall[1], 6),
        "energy_reduction": round(init_energy - best_overall[1], 6),
        "best_plan": best_overall[2],
        "alternatives": alt_plans,
    }


def rerank_search_with_jepa(
    vault_dir: Path,
    query: str,
    base_results: list[dict[str, Any]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Fuse trained Vault JEPA latent predictions as a 5th retrieval channel on top of ``base_results``."""
    v_root = Path(vault_dir).expanduser().resolve()
    ckpt_path = v_root / DEFAULT_CHECKPOINT_REL
    if not ckpt_path.is_file():
        return base_results[:top_k]

    try:
        corpus, model, _ = _load_model_and_corpus(v_root)
    except (FileNotFoundError, ValueError):
        return base_results[:top_k]
    q_vec, _ = embed_query_for_corpus(v_root, corpus, query)
    sq = model.encode_context(q_vec)
    sq_pred = model.predict(sq, "WIKILINK")

    doc_energies: dict[str, float] = {}
    for p, vec in corpus.doc_vectors.items():
        sy = model.encode_target(vec)
        e_pred = model.energy(sq_pred, sy)
        e_ctx = model.energy(sq, sy)
        doc_energies[p] = 0.65 * e_pred + 0.35 * e_ctx

    scored_results: list[dict[str, Any]] = []
    seen_docs: set[str] = set()
    for r in base_results:
        item = dict(r)
        rp = str(item.get("rel_path", ""))
        seen_docs.add(rp)
        e = doc_energies.get(rp)
        if e is not None:
            compat = max(0.0, 1.0 - 0.5 * e)
            item["jepa_energy"] = round(e, 4)
            item["rrf_score"] = round(float(item.get("rrf_score", 0.0)) + 0.035 * compat, 4)
        else:
            item["jepa_energy"] = None
        scored_results.append(item)

    # Inject exact H-JEPA chunk discoveries (or document lead chunks) missed by lexical/4-way RRF
    from .hierarchical import retrieve_chunks_with_hjepa

    injected = 0
    for chunk_hit in retrieve_chunks_with_hjepa(v_root, query, top_k=8):
        rp = str(chunk_hit["rel_path"])
        if rp in seen_docs:
            continue
        seen_docs.add(rp)
        e = float(chunk_hit["jepa_energy"])
        compat = max(0.0, 1.0 - 0.5 * e)
        scored_results.append(
            {
                "id": chunk_hit["id"],
                "rel_path": rp,
                "doc_type": chunk_hit["doc_type"],
                "abs_path": chunk_hit["abs_path"],
                "heading": chunk_hit["heading"],
                "heading_path": chunk_hit["heading_path"],
                "start_line": chunk_hit["start_line"],
                "end_line": chunk_hit["end_line"],
                "content": str(chunk_hit["content"])[:420],
                "rrf_score": round(0.035 * compat, 4),
                "vec_sim": None,
                "fts_rank": None,
                "hips_rank": None,
                "hips_score": None,
                "jepa_energy": round(e, 4),
            }
        )
        injected += 1
        if injected >= 3:
            break

    scored_results.sort(key=lambda item: float(item.get("rrf_score", 0.0)), reverse=True)
    return scored_results[: max(1, top_k)]


def jepa_hybrid_search(vault_dir: Path, query: str, top_k: int = 5) -> dict[str, Any]:
    """Run 4-way hybrid retrieval fused with Vault JEPA latent predictions (5th channel)."""
    from sovereign_kb.retrieval import retrieve_from_vault

    v_root = Path(vault_dir).expanduser().resolve()
    base_payload = retrieve_from_vault(v_root, query, top_k=max(top_k * 4, 20))
    base = list(base_payload.get("results", [])) if isinstance(base_payload, dict) else list(base_payload)
    fused = rerank_search_with_jepa(v_root, query, base, top_k=top_k)
    return {
        "query": query,
        "count": len(fused),
        "results": fused,
    }


def jepa_status(vault_dir: Path) -> dict[str, Any]:
    """Inspect ``.kb/jepa_model.json`` status and collapse health."""
    v_root = Path(vault_dir).expanduser().resolve()
    ckpt_path = v_root / DEFAULT_CHECKPOINT_REL
    if not ckpt_path.is_file():
        return {"trained": False, "vault_dir": str(v_root), "checkpoint_path": str(ckpt_path)}
    model, raw = VaultJEPA.load(ckpt_path)
    return {
        "trained": True,
        "vault_dir": str(v_root),
        "checkpoint_path": str(ckpt_path),
        "model_sha256": raw.get("model_sha256", ""),
        "config": raw.get("config", {}),
        "metadata": raw.get("metadata", {}),
        "latent_dim": model.config.latent_dim,
    }
