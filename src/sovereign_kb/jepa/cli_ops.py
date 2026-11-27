"""CLI handler for `skb jepa` subcommands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .model import JEPAConfig
from .ops import (
    benchmark_jepa,
    detect_surprise,
    discover_gaps,
    jepa_hybrid_search,
    jepa_status,
    plan_latent_trajectory,
    predict_latent,
    train_vault_jepa,
)


def _make_config(args: Any) -> JEPAConfig:
    return JEPAConfig(
        proj_dim=int(getattr(args, "proj_dim", 128)),
        latent_dim=int(getattr(args, "latent_dim", 48)),
        epochs=int(getattr(args, "epochs", 25)),
        backend=str(getattr(args, "backend", "stdlib")),
        seed=int(getattr(args, "seed", 42)),
    )


def cmd_jepa(args: Any, resolve_vault_dir_fn: Callable[..., Path]) -> None:
    subcmd = getattr(args, "jepa_cmd", "status") or "status"
    as_json = bool(getattr(args, "json", False))

    if subcmd == "code":
        from .code_jepa import run_code_graph_jepa

        target_raw = getattr(args, "target", None) or "."
        repo_dir = Path(target_raw).expanduser().resolve()
        query_sym = getattr(args, "goal", None)
        limit = max(1, int(getattr(args, "limit", 5)))
        epochs = int(getattr(args, "epochs", 20))
        seed = int(getattr(args, "seed", 42))
        res = run_code_graph_jepa(repo_dir, query_symbol=query_sym, top_k=limit, epochs=epochs, seed=seed)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        print(f"=== Polyglot Code-Graph JEPA (`{repo_dir.name}`) ===")
        print(
            f"  Symbols={res['total_symbols']} | Edges={res['resolved_edges']} "
            f"| Train={res['train_transitions']} | Heldout={res['heldout_transitions']}"
        )
        print(
            f"  Coupling    : Raw MRR@5={res['raw_mrr5']:.4f} R@10={res['raw_r10']:.4f} "
            f"-> JEPA MRR@5={res['jepa_mrr5']:.4f} R@10={res['jepa_r10']:.4f}"
        )
        seed_s = res["seed_symbol"]
        print(f"  Seed Symbol : {seed_s['name']} ({seed_s['file_path']}:{seed_s['start_line']})")
        for imp in res["predicted_impacts"]:
            print(f"    • {imp['name']:32s} ({imp['file_path']}:{imp['start_line']}) E={imp['energy']:.4f}")
        return

    vault_dir = resolve_vault_dir_fn(getattr(args, "vault", None))

    if subcmd == "train":
        evr_raw = getattr(args, "evr", None)
        evr_out = Path(evr_raw) if evr_raw else None
        res = train_vault_jepa(vault_dir, config=_make_config(args), evr_out=evr_out)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        cm = res["collapse_metrics"]
        print(f"=== Sovereign-KB Unified Vault JEPA Trained (`{vault_dir.name}`) ===")
        print(
            f"  Mode/Dims   : {res['embedding_mode']} ({res['input_dim']}d -> {res['effective_in_dim']}d -> {res['latent_dim']}d) "
            f"| backend={res['train_stats']['backend']}"
        )
        print(
            f"  Transitions : train={res['train_transitions']} | heldout={res['heldout_transitions']} "
            f"| {res['action_counts']}"
        )
        print(
            f"  Energy      : init={res['untrained_heldout_energy']:.4f} -> trained={res['trained_heldout_energy']:.4f} "
            f"| neg={cm['mean_neg_energy']:.4f} | margin={cm['energy_margin']:+.4f}"
        )
        print(
            f"  Anti-Collapse: eff_rank={cm['effective_rank']:.2f}/{res['latent_dim']} "
            f"| min_std={cm['min_dim_std']:.4f} | collapsed={cm['collapsed']}"
        )
        print(f"  Checkpoint  : {res['checkpoint_path']} (sha256={res['model_sha256']})")
        if res["evr_path"]:
            print(f"  EVR Capsule : {res['evr_path']}")
        return

    if subcmd == "predict":
        target = getattr(args, "target", None) or "01-now/now.md"
        action = getattr(args, "action", "WIKILINK") or "WIKILINK"
        steps = max(1, int(getattr(args, "steps", 1)))
        limit = max(1, int(getattr(args, "limit", 5)))
        res = predict_latent(vault_dir, target, action=action, steps=steps, top_k=limit)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        print(f"=== Vault JEPA Latent Rollout (`{res['query']}`, action={res['action']}, steps={res['steps']}) ===")
        for step_blk in res["rollout"]:
            print(f"  [Step {step_blk['step']}]")
            for m in step_blk["matches"]:
                print(f"    • {m['rel_path']:48s} E={m['energy']:.4f} compat={m['compatibility']:.3f} ({m['title']})")
        return

    if subcmd == "plan":
        start = getattr(args, "target", None) or "01-now/now.md"
        goal = getattr(args, "goal", None) or "02-operating-system/goldwood-index-gwi-standard.md"
        steps = max(1, int(getattr(args, "steps", 4)))
        beam = max(1, int(getattr(args, "beam", 4)))
        res = plan_latent_trajectory(vault_dir, start=start, goal=goal, max_steps=steps, beam_width=beam)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        print(
            f"=== Vault JEPA Latent Beam Plan (`{res['start']}` -> `{res['goal']}`, "
            f"E: {res['initial_energy']:.4f} -> {res['final_energy']:.4f}, dE={res['energy_reduction']:+.4f}) ==="
        )
        for st in res["best_plan"]:
            print(
                f"  Step {st['step']} [{st['action']:20s}] -> {st['grounded_doc']} (E_goal={st['energy_to_goal']:.4f})"
            )
        return

    if subcmd == "search":
        query = getattr(args, "target", None) or ""
        if not query.strip():
            raise ValueError('Provide a search query: `skb jepa search "<query>"`')
        limit = max(1, int(getattr(args, "limit", 5)))
        res = jepa_hybrid_search(vault_dir, query=query, top_k=limit)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        print(f"=== Vault JEPA 5-Way Hybrid Search (`{res['query']}`, {res['count']} hits) ===")
        for idx, r in enumerate(res["results"], start=1):
            je = r.get("jepa_energy")
            je_str = f"jepa_E={je:.4f}" if je is not None else "jepa_E=none"
            print(f"  [{idx}] {r['rel_path']} (RRF={r['rrf_score']}, {je_str})")
        return

    if subcmd == "gaps":
        action = getattr(args, "action", "WIKILINK") or "WIKILINK"
        limit = max(1, int(getattr(args, "limit", 10)))
        res = discover_gaps(vault_dir, top_k=limit, action=action)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        print(
            f"=== Vault JEPA Missing-Link Discovery (action={res['action']}, scanned={res['candidates_scanned']}) ==="
        )
        for g in res["gaps"]:
            print(
                f"  • {g['source_path']}  -->  {g['target_path']}  "
                f"[E={g['energy']:.4f}, compat={g['compatibility']:.3f}]"
            )
        return

    if subcmd == "surprise":
        action = getattr(args, "action", None)
        limit = max(1, int(getattr(args, "limit", 10)))
        res = detect_surprise(vault_dir, top_k=limit, action=action)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        print(
            f"=== Vault JEPA Surprise / Drift Detection (scored={res['transitions_scored']}, "
            f"mean_E={res['mean_energy']:.4f}) ==="
        )
        for s in res["surprises"]:
            print(f"  • [{s['action']}] {s['source_key']}  -->  {s['target_key']}  (E={s['energy']:.4f})")
        return

    if subcmd == "bench":
        res = benchmark_jepa(vault_dir, config=_make_config(args))
        if as_json:
            print(json.dumps(res, indent=2))
            return
        r = res["ranking"]
        cm = res["collapse_metrics"]
        print(f"=== Sovereign-KB Vault JEPA Benchmark (`{vault_dir.name}`) ===")
        print(
            f"  Docs={res['doc_count']} | Train={res['train_transitions']} | Heldout={res['heldout_transitions']} "
            f"| Mode={res['embedding_mode']}"
        )
        if r.get("count", 0) > 0:
            print(
                f"  Raw Cosine : MRR@5={r['raw_cosine']['mrr_at_5']:.4f} | R@5={r['raw_cosine']['recall_at_5']:.4f} | R@10={r['raw_cosine']['recall_at_10']:.4f}"
            )
            print(
                f"  Vault JEPA : MRR@5={r['jepa']['mrr_at_5']:.4f} | R@5={r['jepa']['recall_at_5']:.4f} | R@10={r['jepa']['recall_at_10']:.4f} (lift={r['lift_jepa_vs_raw_mrr5']:+.4f})"
            )
            print(
                f"  Hybrid RRF : MRR@5={r['hybrid_rrf']['mrr_at_5']:.4f} | R@5={r['hybrid_rrf']['recall_at_5']:.4f} | R@10={r['hybrid_rrf']['recall_at_10']:.4f} (lift={r['lift_hybrid_vs_raw_mrr5']:+.4f})"
            )
        th = res.get("two_hop_rollout", {})
        if th.get("count", 0) > 0:
            print(
                f"  2-Hop Roll : n={th['count']} | Raw MRR@5={th['raw_cosine']['mrr_at_5']:.4f} R@10={th['raw_cosine']['recall_at_10']:.4f} "
                f"-> JEPA MRR@5={th['jepa_2step']['mrr_at_5']:.4f} R@10={th['jepa_2step']['recall_at_10']:.4f} (lift={th['lift_mrr5']:+.4f})"
            )
        print(
            f"  Health     : eff_rank={cm['effective_rank']:.2f} | margin={cm['energy_margin']:+.4f} "
            f"| collapsed={cm['collapsed']}"
        )
        return

    if subcmd == "hjepa":
        from .hierarchical import train_hierarchical_jepa

        res = train_hierarchical_jepa(vault_dir, config=_make_config(args))
        if as_json:
            print(json.dumps(res, indent=2))
            return
        cm = res["collapse_metrics"]
        print(f"=== Sovereign-KB Hierarchical H-JEPA Trained (`{vault_dir.name}`) ===")
        print(
            f"  Chunks={res['chunks_indexed']} | Train={res['train_transitions']} | Heldout={res['heldout_transitions']} "
            f"| Mode={res['embedding_mode']}"
        )
        print(
            f"  Chunk Eval  : n={res['chunk_eval_count']} | Raw MRR@5={res['raw_chunk_mrr5']:.4f} "
            f"R@10={res['raw_chunk_r10']:.4f} -> H-JEPA MRR@5={res['hjepa_chunk_mrr5']:.4f} "
            f"R@10={res['hjepa_chunk_r10']:.4f}"
        )
        print(
            f"  Health      : eff_rank={cm['effective_rank']:.2f} | margin={cm['energy_margin']:+.4f} "
            f"| collapsed={cm['collapsed']}"
        )
        return

    if subcmd == "soar":
        from .soar_bridge import resolve_impasse_with_jepa

        query = getattr(args, "target", None) or "Goldwood Index"
        persist = bool(getattr(args, "persist_chunks", False))
        res = resolve_impasse_with_jepa(vault_dir, query=query, persist_chunks=persist)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        print(f"=== Neuro-Symbolic Soar <-> JEPA Impasse Resolution (`{res['query']}`) ===")
        print(
            f"  Winner   : {res['winner_doc']} (E={res.get('winner_jepa_energy')}) "
            f"| Runner-up: {res.get('runner_up_doc')}"
        )
        print(
            f"  Soar EBC : impasses={res['first_run_impasses']} -> replay={res['replay_impasses']} "
            f"| zero_impasse_replay={res['zero_impasse_replay']} | chunks={res['compiled_chunks']}"
        )
        return

    if subcmd == "heal":
        from .heal import heal_vault_links_with_jepa

        limit = max(1, int(getattr(args, "limit", 3)))
        apply = bool(getattr(args, "apply", False))
        res = heal_vault_links_with_jepa(vault_dir, max_edges=limit, apply=apply)
        if as_json:
            print(json.dumps(res, indent=2))
            return
        mode = "APPLIED" if res["applied"] else ("ROLLED BACK" if res.get("rolled_back") else "DRY-RUN")
        print(f"=== Vault JEPA Lyapunov-Gated Graph Heal (`{vault_dir.name}`, {mode}) ===")
        if "j_before" in res:
            print(f"  Lyapunov J  : {res['j_before']:.6f} -> {res['j_after']:.6f} (delta={res['delta_j']:+.6f})")
        edges_list = res.get("added_edges", res.get("proposed_edges", []))
        for e in edges_list:
            print(f"  • {e['source_path']}  -->  {e['target_path']}  (E={e['energy']:.4f})")
        return

    res = jepa_status(vault_dir)
    if as_json:
        print(json.dumps(res, indent=2))
        return
    if not res["trained"]:
        print(f"Vault JEPA: not trained yet (`{res['checkpoint_path']}`). Run `skb jepa train`.")
        return
    meta = res.get("metadata", {})
    cm = meta.get("collapse_metrics", {})
    print(f"=== Sovereign-KB Vault JEPA Status (`{vault_dir.name}`) ===")
    print(f"  Checkpoint : {res['checkpoint_path']} (sha256={res['model_sha256']})")
    print(f"  Trained At : {meta.get('trained_at', 'unknown')} | Mode={meta.get('embedding_mode', 'unknown')}")
    if cm:
        print(
            f"  Health     : eff_rank={cm.get('effective_rank')} | margin={cm.get('energy_margin')} "
            f"| collapsed={cm.get('collapsed')}"
        )
