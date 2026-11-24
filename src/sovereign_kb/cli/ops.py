"""Git sync, direct HIPS CLI wrapper, rust certify, and RSI/Soar/Organon CLI commands."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config_db import _vault_lock, get_db, resolve_hips_bin, resolve_vault_dir
from .markdown_embed import (
    _is_vault_no_embed,
    build_stem_lookup,
    extract_wikilinks,
    list_vault_md_files,
    parse_frontmatter,
)
from .retrieval_notes import call_llm, hybrid_retrieve
from .sync_hips import _is_index_stale, cmd_sync, hips_retrieve, sync_hips_index
from .verify import cmd_verify


def _git_stash_count(repo: Path) -> int:
    out = subprocess.run(["git", "-C", str(repo), "stash", "list"], capture_output=True, text=True, check=False).stdout
    return len([ln for ln in out.splitlines() if ln.strip()])


def pull_rebase_autostash(repo: Path) -> str | None:
    """Run `git pull --rebase --autostash` and report a failed autostash reapply.

    When reapplying the autostash conflicts, git still exits 0: it leaves conflict markers in the
    working tree and keeps the uncommitted changes in a new stash. Committing on top of that would
    publish the markers and silently drop the stashed edits, so return an error message instead.
    Returns None when the pull and reapply both succeeded.
    """
    stashes_before = _git_stash_count(repo)
    res = subprocess.run(
        ["git", "-C", str(repo), "pull", "--rebase", "--autostash"], capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        return f"`git pull --rebase --autostash` failed (exit {res.returncode}): {(res.stderr or res.stdout).strip()[-400:]}"
    unmerged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "--diff-filter=U"], capture_output=True, text=True, check=False
    ).stdout.split()
    if unmerged or _git_stash_count(repo) > stashes_before:
        files = ", ".join(unmerged) if unmerged else "(see stash)"
        return (
            f"pulled changes conflict with your uncommitted edits in {files}. Your edits are kept in "
            f"stash@{{0}} and nothing was committed. Resolve the conflict markers in {repo}, then run "
            f"`git -C {repo} stash drop` and retry."
        )
    return None


def cmd_git_sync(args) -> None:
    vault_dir = resolve_vault_dir(getattr(args, "vault", None))
    mode = getattr(args, "mode", "sync")
    with _vault_lock(vault_dir):
        git_dir = vault_dir / ".git"
        if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            print(
                f"Error: {vault_dir} is currently mid-rebase. Resolve or abort rebase before running 'sovereign-kb {mode}'.",
                file=sys.stderr,
            )
            sys.exit(1)

        remote_url = (
            subprocess.run(
                ["git", "-C", str(vault_dir), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            or "origin"
        )
        print(f"[sovereign-kb {mode}] Pulling latest vault changes from {remote_url}...")
        pull_error = pull_rebase_autostash(vault_dir)
        if pull_error:
            print(f"[sovereign-kb {mode}] Error: {pull_error}", file=sys.stderr)
            sys.exit(1)
        cmd_sync(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=_is_vault_no_embed(vault_dir)))
        if mode in ("sync", "push"):
            status = subprocess.run(
                ["git", "-C", str(vault_dir), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if (
                status
                and not getattr(args, "no_rsi", False)
                and ((vault_dir / ".kb" / "rsi-ledger.jsonl").is_file() or (vault_dir / "04-episodic").is_dir())
            ):
                cmd_rsi(
                    argparse.Namespace(
                        vault=str(vault_dir),
                        cmd="rsi",
                        rsi_cmd="cycle",
                        json=False,
                        use_llm=False,
                        verify_rust=False,
                        no_embed=_is_vault_no_embed(vault_dir),
                    )
                )
                status = subprocess.run(
                    ["git", "-C", str(vault_dir), "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
            if status:
                subprocess.run(["git", "-C", str(vault_dir), "add", "-A"], check=True)
                try:
                    cmd_verify(argparse.Namespace(vault=str(vault_dir), json=False, heal=False, no_auto_sync=True))
                except SystemExit:
                    subprocess.run(["git", "-C", str(vault_dir), "reset", "--quiet"], check=False)
                    raise
                msg = getattr(args, "message", None) or (
                    f"chore(kb): auto-sync vault ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})"
                )
                subprocess.run(["git", "-C", str(vault_dir), "commit", "-m", msg], check=True)

            ahead_proc = subprocess.run(
                ["git", "-C", str(vault_dir), "rev-list", "--count", "@{u}..HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            ahead_count = int(ahead_proc.stdout.strip() or "0") if ahead_proc.returncode == 0 else (1 if status else 0)
            if ahead_count > 0:
                if not status:
                    cmd_verify(argparse.Namespace(vault=str(vault_dir), json=False, heal=False, no_auto_sync=True))
                subprocess.run(["git", "-C", str(vault_dir), "push", "origin", "main"], check=True)
                print(f"[sovereign-kb {mode}] Pushed {ahead_count} commit(s) to {remote_url}.")
            else:
                print(f"[sovereign-kb {mode}] Working tree clean; up to date with {remote_url}.")


def cmd_hips(args) -> None:
    """Direct interface to the `hips` Block-Max WAND engine over the active Sovereign-KB vault."""
    hips_bin = resolve_hips_bin()
    if not hips_bin:
        print("Error: `hips` binary not found. Set KB_HIPS_BIN or install `hips` to ~/.local/bin/hips", file=sys.stderr)
        sys.exit(1)

    vault_dir = resolve_vault_dir(getattr(args, "vault", None))
    action = getattr(args, "hips_cmd", None) or "status"
    conn = get_db(vault_dir)
    if _is_index_stale(conn, vault_dir):
        conn.close()
        cmd_sync(
            argparse.Namespace(
                vault=str(vault_dir),
                force=False,
                no_embed=_is_vault_no_embed(vault_dir),
                quiet=getattr(args, "json", False),
            )
        )
        conn = get_db(vault_dir)

    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    jsonl_path = vault_dir / ".kb" / "hips-chunks.jsonl"

    if action == "index":
        t0 = time.perf_counter()
        ok, count = sync_hips_index(conn, vault_dir)
        conn.close()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if not ok:
            print("Error: Failed to build HIPS Block-Max WAND index.", file=sys.stderr)
            sys.exit(1)
        size_b = sum(f.stat().st_size for f in hips_idx_dir.rglob("*") if f.is_file())
        print(
            f"[sovereign-kb hips index] Built Block-Max WAND index ({count} chunks, {size_b:,} bytes) "
            f"in {elapsed_ms:.1f} ms -> {hips_idx_dir}"
        )
        return

    if not hips_idx_dir.is_dir():
        sync_hips_index(conn, vault_dir)

    if action == "status":
        size_b = sum(f.stat().st_size for f in hips_idx_dir.rglob("*") if f.is_file()) if hips_idx_dir.is_dir() else 0
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        t0 = time.perf_counter()
        ranks, _ = hips_retrieve(vault_dir, "Block-Max WAND Type-42 font embedding", limit=5)
        lat_ms = (time.perf_counter() - t0) * 1000.0
        print("=== HIPS Block-Max WAND Engine Status ===")
        print(f"  Binary Path   : {hips_bin}")
        print(f"  Index Dir     : {hips_idx_dir} ({size_b:,} bytes)")
        print(f"  JSONL Corpus  : {jsonl_path} ({chunks} chunks)")
        print(f"  Warm Probe    : {len(ranks)} hits in {lat_ms:.2f} ms")
        return

    if action == "search":
        q_list = getattr(args, "query", None) or ["Block-Max WAND"]
        query = " ".join(q_list)
        t0 = time.perf_counter()
        ranks, scores = hips_retrieve(vault_dir, query, limit=getattr(args, "limit", 10))
        lat_ms = (time.perf_counter() - t0) * 1000.0
        if getattr(args, "json", False):
            conn.close()
            out = [
                {"rank": r, "path": k[0], "start_line": k[1], "end_line": k[2], "hips_score": round(scores[k], 4)}
                for k, r in sorted(ranks.items(), key=lambda x: x[1])
            ]
            print(json.dumps({"query": query, "latency_ms": round(lat_ms, 3), "results": out}, indent=2))
            return
        print(f"=== HIPS Block-Max WAND Search: '{query}' ({len(ranks)} hits in {lat_ms:.2f} ms) ===")
        for (rel_path, s_line, e_line), r_idx in sorted(ranks.items(), key=lambda x: x[1]):
            row = conn.execute(
                "SELECT heading_path, content FROM chunks WHERE rel_path = ? AND start_line = ? AND end_line = ?",
                (rel_path, s_line, e_line),
            ).fetchone()
            heading = row["heading_path"] if row else rel_path
            snippet = (row["content"].strip().replace("\n", " ")[:220] + "...") if row else ""
            print(
                f"  [{r_idx}] {rel_path}#L{s_line}-L{e_line} — {heading} "
                f"(wand_score={scores[(rel_path, s_line, e_line)]:.3f})"
            )
            if snippet:
                print(f"      {snippet}")
        conn.close()
        return

    conn.close()
    if action == "bench":
        queries = getattr(args, "query", None) or [
            "Block-Max WAND",
            "Type-42 font embedding Okabe-Ito",
            "6-Pillar Deterministic Autonomous Engineering Framework",
            "Gelenbe queueing retransmission",
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
            tf.write("\n".join(queries) + "\n")
            qfile = tf.name
        try:
            subprocess.run(
                [str(hips_bin), "-v", "bench", "--index", str(hips_idx_dir), "--queries", qfile, "--mode", "bm25"],
                check=False,
            )
        finally:
            Path(qfile).unlink(missing_ok=True)
        return

    if action == "serve":
        addr = getattr(args, "addr", "127.0.0.1:18080")
        print(f"[sovereign-kb hips serve] Serving HIPS HTTP API on http://{addr} (index={hips_idx_dir})...")
        os.execv(str(hips_bin), [str(hips_bin), "-v", "serve", "--index", str(hips_idx_dir), "--addr", addr])

    if action == "mcp":
        os.execv(str(hips_bin), [str(hips_bin), "mcp"])


def cmd_certify(args) -> None:
    """Run the 20-challenge deterministic Rust Systems & Memory Safety Certification (`RUST-CERT-01..20`)."""
    from sovereign_kb.rust_cert import run_rust_certification

    vault_dir = resolve_vault_dir(getattr(args, "vault", None))
    out_dir = Path(args.output_dir).expanduser().resolve() if getattr(args, "output_dir", None) else None
    no_embed = getattr(args, "no_embed", False) or os.environ.get("KB_NO_EMBED") == "1"

    report = run_rust_certification(
        vault_dir=vault_dir,
        get_db_fn=get_db,
        hybrid_retrieve_fn=getattr(sys.modules.get("sovereign_kb.cli"), "hybrid_retrieve", hybrid_retrieve),
        sync_fn=cmd_sync,
        output_dir=out_dir,
        no_embed=no_embed,
    )

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        print(f"=== Sovereign-KB Autonomous Rust Certification: {report['status']} ===")
        print(f"  Credential ID     : {report['credential_id']}")
        print(f"  Issued To         : {report['issued_to']}")
        print(f"  Compiler          : {report['compiler']} (-D warnings -O)")
        print(f"  KB Retrieval Hits : {report['kb_retrieval_score']}")
        print(f"  rustc Test Score  : {report['rustc_test_score']}")
        print(
            f"  Timing Breakdown  : compile={report['compile_ms']} ms, "
            f"exec={report['execution_ms']} ms, total={report['total_elapsed_ms']} ms"
        )
        print(f"  SHA-256 Digest    : {report['verification_fingerprint_sha256']}")
        print("\n  Domain Breakdown:")
        for r in report["results"]:
            status_tag = "PASS" if (r["kb_retrieved"] and r["rustc_verified"]) else "FAIL"
            print(
                f"    [{status_tag}] {r['id']} | {r['title']} "
                f"(RRF={r['rrf_score']}, cos={r['vec_sim']}, hips_rank={r['hips_rank']}, fts_rank={r['fts_rank']})"
            )

    if report["status"] != "CERTIFIED":
        sys.exit(1)


def cmd_rsi(args) -> None:
    """
    Monotonically-Gated Recursive Self-Improvement Engine (`sovereign-kb rsi` / `evolve` / `tune` / `consolidate`).
    """
    from sovereign_kb.rsi import (
        CANONICAL_NEGATIVE_PROBES,
        compute_lyapunov_potential,
        compute_vault_health_queues,
        evaluate_retrieval_benchmark,
        heal_and_consolidate_vault,
        load_retrieval_policy,
        read_rsi_ledger,
        record_query_feedback,
        run_rsi_cycle,
        tune_retrieval_policy,
        verify_ledger_chain,
    )

    vault_dir = resolve_vault_dir(getattr(args, "vault", None))
    no_embed = getattr(args, "no_embed", False) or os.environ.get("KB_NO_EMBED") == "1"
    action = getattr(args, "rsi_cmd", None) or "cycle"
    if args.cmd == "tune":
        action = "tune"
    elif args.cmd == "consolidate":
        action = "consolidate"

    if action == "status" or action == "ledger":
        conn = get_db(vault_dir)
        if conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0:
            conn.close()
            cmd_sync(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=no_embed))
            conn = get_db(vault_dir)
        from sovereign_kb.rsi import build_benchmark_suite, split_benchmark_suite

        policy = load_retrieval_policy(vault_dir)
        all_probes = build_benchmark_suite(conn, vault_dir)
        _, heldout_probes = split_benchmark_suite(conn, vault_dir, probes=all_probes)
        bench = evaluate_retrieval_benchmark(
            conn,
            vault_dir,
            getattr(sys.modules.get("sovereign_kb.cli"), "hybrid_retrieve", hybrid_retrieve),
            policy=policy,
            probes=all_probes,
            negative_probes=CANONICAL_NEGATIVE_PROBES,
        )
        heldout_bench = (
            evaluate_retrieval_benchmark(
                conn,
                vault_dir,
                getattr(sys.modules.get("sovereign_kb.cli"), "hybrid_retrieve", hybrid_retrieve),
                policy=policy,
                probes=heldout_probes,
            )
            if heldout_probes
            else bench
        )
        queues = compute_vault_health_queues(conn, vault_dir, latency_p95_ms=bench.get("latency_p95_ms", 0.0))
        lyap = compute_lyapunov_potential(bench["objective_j"], queues)
        conn.close()
        entries = read_rsi_ledger(vault_dir)
        chain_ok, count = verify_ledger_chain(vault_dir)
        payload = {
            "vault_dir": str(vault_dir),
            "objective_j": bench["objective_j"],
            "heldout_objective_j": heldout_bench["objective_j"],
            "lyapunov_v": lyap["lyapunov_v"],
            "lyapunov_v_det": lyap["lyapunov_v_det"],
            "vault_queues": queues,
            "latency_p95_ms": bench.get("latency_p95_ms", 0.0),
            "negative_rejection_rate": bench.get("negative_rejection_rate", 1.0),
            "negative_fp_count": bench.get("negative_fp_count", 0),
            "mrr_at_5": bench["mrr_at_5"],
            "ndcg_at_5": bench["ndcg_at_5"],
            "recall_at_1": bench["recall_at_1"],
            "ledger_intrusions": bench.get("ledger_intrusions", 0),
            "probes_count": bench["probes_count"],
            "heldout_probes_count": heldout_bench["probes_count"],
            "misses": bench.get("misses", [])[:5],
            "policy": policy,
            "ledger_cycles": count,
            "ledger_chain_verified": chain_ok,
            "recent_cycles": entries[-5:],
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print("=== Sovereign-KB Recursive Self-Improvement (RSI) Status ===")
            print(f"  Vault Dir         : {vault_dir}")
            print(
                f"  Lyapunov Obj J    : {bench['objective_j']:.6f} ({bench['probes_count']} probes) | "
                f"Held-Out J: {heldout_bench['objective_j']:.6f} ({heldout_bench['probes_count']} probes)"
            )
            print(
                f"  Lyapunov Pot V    : {lyap['lyapunov_v']:.6f} (det={lyap['lyapunov_v_det']:.6f}) | "
                f"p95={bench.get('latency_p95_ms', 0.0):.2f}ms, "
                f"q_stale={queues['q_staleness']:.3f}, q_bloat={queues['q_bloat']:.3f}"
            )
            print(
                f"  Retrieval Metrics : MRR@5={bench['mrr_at_5']:.4f}, "
                f"NDCG@5={bench['ndcg_at_5']:.4f}, Recall@1={bench['recall_at_1']:.4f}, "
                f"NegReject={bench.get('negative_rejection_rate', 1.0):.2%}, "
                f"LedgerIntrusions={bench.get('ledger_intrusions', 0)}"
            )
            print(
                f"  Active Policy     : k_rrf={policy['k_rrf']}, w_hips={policy['w_hips']}, "
                f"w_fts={policy['w_fts_with_hips']}, w_vec={policy['w_vec']}, "
                f"id_boost={policy['exact_id_boost']}, ep_damp={policy.get('episodic_dampening', 0.55)}, "
                f"graph_boost={policy['graph_1hop_boost']}"
            )
            print(f"  Ledger Integrity  : {'PASS' if chain_ok else 'FAIL'} ({count} SHA-256 chained cycles)")
            if entries:
                last = entries[-1]
                print(
                    f"  Latest Cycle      : {last['cycle_id']} [{last['status']}] "
                    f"J: {last['j_before']} -> {last['j_after']} (delta={last['delta_j']:+.6f})"
                )
            if bench.get("misses"):
                print(f"  Top Rank-1 Misses : {len(bench['misses'])} probe(s)")
                for m in bench["misses"][:3]:
                    print(
                        f"    - [{m['id']}] '{m['query']}' -> expected {m['target_path']} "
                        f"(got@1: {m['top1_got']}, rank={m['rank']})"
                    )
        return

    if action == "tune":
        conn = get_db(vault_dir)
        if conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0:
            conn.close()
            cmd_sync(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=no_embed))
            conn = get_db(vault_dir)
        res = tune_retrieval_policy(
            conn, vault_dir, getattr(sys.modules.get("sovereign_kb.cli"), "hybrid_retrieve", hybrid_retrieve)
        )
        conn.close()
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2))
        else:
            print("=== Sovereign-KB Loop 1: Retrieval Policy Meta-Optimization ===")
            print(f"  Candidates Tested : {res['candidates_evaluated']}")
            print(
                f"  Objective J       : {res['before']['objective_j']:.6f} -> {res['after']['objective_j']:.6f} "
                f"(delta={res['delta_j']:+.6f})"
            )
            print(
                f"  MRR@5 / Recall@1  : {res['before']['mrr_at_5']:.4f} -> {res['after']['mrr_at_5']:.4f} / "
                f"{res['before']['recall_at_1']:.4f} -> {res['after']['recall_at_1']:.4f}"
            )
            print(f"  Policy SHA-256    : {res['policy_sha256']} (updated={res['updated']})")
        return

    if action == "consolidate":
        res = heal_and_consolidate_vault(
            vault_dir=vault_dir,
            list_md_fn=list_vault_md_files,
            parse_fm_fn=parse_frontmatter,
            extract_links_fn=extract_wikilinks,
            build_stem_fn=build_stem_lookup,
            sync_fn=cmd_sync,
            call_llm_fn=call_llm,
            use_llm=getattr(args, "use_llm", False),
            no_embed=no_embed,
        )
        res.pop("snapshots", None)
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2))
        else:
            print("=== Sovereign-KB Loop 2: Self-Healing & Episodic-to-Semantic Consolidation ===")
            print(f"  Episodic Logs Scanned : {res['episodic_logs_scanned']}")
            print(f"  Distilled Entries     : {res['distilled_entries']} -> {res['distillation_note']}")
            print(f"  Healed Frontmatter    : {len(res['healed_frontmatter'])} file(s)")
            print(f"  Healed Orphan Notes   : {len(res['healed_orphans'])} file(s)")
        return

    if action == "feedback":
        q_arg = getattr(args, "query", None) or ""
        t_arg = getattr(args, "target", None) or ""
        h_arg = getattr(args, "heading", None) or ""
        o_arg = getattr(args, "outcome", "miss") or "miss"
        fb = record_query_feedback(vault_dir, query=q_arg, target_path=t_arg, target_heading=h_arg, outcome=o_arg)
        if getattr(args, "json", False):
            print(json.dumps(fb, indent=2))
        else:
            if fb.get("recorded"):
                print(
                    f"Recorded query feedback probe {fb['probe']['id']}: "
                    f"'{fb['probe']['query']}' -> {fb['probe']['target_path']} (total={fb['total_probes']})"
                )
            else:
                print(f"Query feedback not recorded: {fb.get('reason', 'invalid')}")
        return

    if action == "compile-prompts":
        from sovereign_kb.frontier_organon import compile_organon_prompts

        compiled = compile_organon_prompts(vault_dir)
        if getattr(args, "json", False):
            print(json.dumps(compiled, indent=2))
        else:
            print("=== Sovereign-KB Compile-Time Prompt & Exemplar Optimizer (`organon-mipro-v1`) ===")
            print(f"  Held-Out Objective J_prompt : {compiled['objective_j_prompt']:.4f}")
            print(f"  Artifact SHA-256            : {compiled['artifact_sha256']}")
            print(f"  Golden Exemplars Selected   : {len(compiled['stages']['stage1_triage']['few_shot_exemplars'])}")
            print(
                f"  Anti-Regression Guardrails  : {len(compiled['stages']['stage3_implement']['anti_regression_guardrails'])}"
            )
        return

    if action == "sleep-consolidate":
        from sovereign_kb.frontier_organon import export_sleep_wake_lora_dataset

        manifest = export_sleep_wake_lora_dataset(vault_dir)
        if getattr(args, "json", False):
            print(json.dumps(manifest, indent=2))
        else:
            print("=== Sovereign-KB Parametric Sleep-Wake Consolidation (`LoRA` / `GRPO` for `gx10`) ===")
            print(f"  Base Model / Target Node    : {manifest['base_model']} on {manifest['target_node']}")
            print(f"  SFT Trajectories Exported   : {manifest['sft_trajectory_count']} -> {manifest['sft_path']}")
            print(
                f"  GRPO Preference Pairs       : {manifest['grpo_preference_pair_count']} -> {manifest['grpo_path']}"
            )
            print(
                f"  Mining Guardrail Enforced   : {manifest['mining_guardrail_enforced']} ({manifest['gx10_script_path']})"
            )
        return

    report = run_rsi_cycle(
        vault_dir=vault_dir,
        get_db_fn=get_db,
        hybrid_retrieve_fn=getattr(sys.modules.get("sovereign_kb.cli"), "hybrid_retrieve", hybrid_retrieve),
        sync_fn=cmd_sync,
        list_md_fn=list_vault_md_files,
        parse_fm_fn=parse_frontmatter,
        extract_links_fn=extract_wikilinks,
        build_stem_fn=build_stem_lookup,
        call_llm_fn=call_llm,
        use_llm=getattr(args, "use_llm", False),
        no_embed=no_embed,
        verify_rust=getattr(args, "verify_rust", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        print(f"=== Sovereign-KB Recursive Self-Improvement Cycle ({report['cycle_id']}): {report['status']} ===")
        print(
            f"  Lyapunov Objective J : {report['before']['objective_j']:.6f} -> {report['after']['objective_j']:.6f} "
            f"(delta={report['delta_j']:+.6f}, monotonic={report['lyapunov_monotonic']})"
        )
        if "lyapunov_v_after" in report:
            vq = report.get("vault_queues", {})
            print(
                f"  Lyapunov Potential V : {report['lyapunov_v_before']:.6f} -> {report['lyapunov_v_after']:.6f} "
                f"(delta_v={report.get('delta_v', 0.0):+.6f}, q_stale={vq.get('q_staleness', 0.0):.3f}, "
                f"q_bloat={vq.get('q_bloat', 0.0):.3f}, p95={report['after'].get('latency_p95_ms', 0.0):.2f}ms)"
            )
        print(
            f"  Retrieval MRR@5      : {report['before']['mrr_at_5']:.4f} -> {report['after']['mrr_at_5']:.4f} "
            f"({report['after']['probes_count']} benchmark probes)"
        )
        print(
            f"  Loop 2 Consolidation : {report['consolidation']['distilled_entries']} kept / "
            f"{report['consolidation'].get('raw_bullets_scanned', 0)} scanned "
            f"({report['consolidation'].get('rejected_noise_bullets', 0)} noise/dups stripped) -> "
            f"{report['consolidation']['distillation_note']} "
            f"(healed_fm={len(report['consolidation']['healed_frontmatter'])}, "
            f"healed_orphans={len(report['consolidation']['healed_orphans'])})"
        )
        print(
            f"  Loop 1 Policy Tuning : tested={report['tuning']['candidates_evaluated']}, "
            f"policy_sha={report['tuning']['policy_sha256']}"
        )
        if report["rust_certification"] != "SKIPPED":
            print(f"  Loop 3 Rust Compiler : {report['rust_certification']}")
        print(
            f"  SHA-256 Chain Ledger : {report['ledger']['entry_hash'][:24]}... "
            f"(cycles={report['ledger']['total_cycles']}, chain_ok={report['ledger']['chain_verified']})"
        )
