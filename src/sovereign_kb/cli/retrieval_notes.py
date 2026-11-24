"""4-way hybrid retrieval, interactive Q&A, capture, me, resume, and status commands."""

from __future__ import annotations

import argparse
import array
import datetime
import json
import math
import operator
import os
import re
import sqlite3
import sys
import urllib.request
from collections import OrderedDict
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .config_db import (
    EMBED_MODEL,
    SCHEMA_VERSION,
    _vault_lock,
    fold_accents,
    get_chat_endpoints,
    get_db,
    resolve_hips_bin,
    resolve_vault_dir,
)
from .markdown_embed import (
    _CACHE_LOCK,
    _is_vault_no_embed,
    compile_fts_query,
    get_embedding,
    list_vault_md_files,
)
from .sync_hips import (
    _VEC_RANK_CACHE_MAX,
    _ensure_corpus_vectors,
    _get_vault_corpus,
    _is_index_stale,
    cmd_sync,
    hips_retrieve,
    sync_hips_index,
)


def hybrid_retrieve(
    conn: sqlite3.Connection,
    vault_dir: Path,
    query: str,
    top_k: int = 6,
    doc_type_filter: str | None = None,
    tag_filter: str | None = None,
    engine: str = "auto",
    policy: dict | None = None,
) -> list[dict]:
    from sovereign_kb.rsi import load_retrieval_policy

    active_policy = policy if policy is not None else load_retrieval_policy(vault_dir)
    bm25_w = active_policy.get("bm25_weights", [1.5, 4.0, 1.5, 2.5, 3.0, 1.0, 1.0])
    if not isinstance(bm25_w, list) or len(bm25_w) != 7:
        bm25_w = [1.5, 4.0, 1.5, 2.5, 3.0, 1.0, 1.0]
    bm25_args = ", ".join(f"{float(w):.3f}" for w in bm25_w)

    corpus = _get_vault_corpus(conn, vault_dir)
    doc_meta = corpus["doc_meta"]

    allowed_docs: set[str] | None = None
    if doc_type_filter or tag_filter:
        allowed_docs = set()
        for rp, dm in doc_meta.items():
            if doc_type_filter and dm["doc_type"].lower() != doc_type_filter.lower():
                continue
            if tag_filter and tag_filter.lower() not in dm["tags"].lower():
                continue
            allowed_docs.add(rp)
        if not allowed_docs:
            return []
    sorted_allowed_tuple = tuple(sorted(allowed_docs)) if allowed_docs is not None else None

    hips_key_ranks: Any = {}
    hips_key_scores: Any = {}
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    if engine in ("auto", "hips", "hybrid"):
        if resolve_hips_bin() and not hips_idx_dir.is_dir():
            sync_hips_index(conn, vault_dir)
        hips_limit = 200 if allowed_docs is not None else 35
        hips_key_ranks, hips_key_scores = hips_retrieve(vault_dir, query, limit=hips_limit)

    fts_query, exact_identifiers = compile_fts_query(query)
    fts_ranks: dict[int, int] = {}
    if engine != "hips" and fts_query:
        fts_cache_key = (fts_query, bm25_args, sorted_allowed_tuple)
        with _CACHE_LOCK:
            cached_fts = corpus["fts_cache"].get(fts_cache_key)
        if cached_fts is not None:
            fts_ranks = dict(cached_fts)
        else:
            raw_fts: dict[int, int] = {}
            try:
                if sorted_allowed_tuple is not None:
                    placeholders = ", ".join("?" for _ in sorted_allowed_tuple)
                    rows = conn.execute(
                        f"""
                        SELECT rowid, bm25(chunks_fts, {bm25_args}) AS score
                        FROM chunks_fts
                        WHERE chunks_fts MATCH ? AND rel_path IN ({placeholders})
                        ORDER BY score
                        LIMIT 35
                        """,
                        (fts_query, *sorted_allowed_tuple),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"""
                        SELECT rowid, bm25(chunks_fts, {bm25_args}) AS score
                        FROM chunks_fts
                        WHERE chunks_fts MATCH ?
                        ORDER BY score
                        LIMIT 35
                        """,
                        (fts_query,),
                    ).fetchall()
                for rank_counter, r in enumerate(rows, start=1):
                    raw_fts[r["rowid"]] = rank_counter
            except sqlite3.OperationalError:
                pass
            with _CACHE_LOCK:
                corpus["fts_cache"][fts_cache_key] = MappingProxyType(raw_fts)
            fts_ranks = dict(raw_fts)

    no_embed = _is_vault_no_embed(vault_dir)
    q_vec = get_embedding(query) if (engine != "hips" and not no_embed) else None
    all_chunks = corpus["chunks"]
    doc_to_cids = corpus["doc_to_cids"]
    if allowed_docs is None:
        chunk_map = all_chunks
    else:
        chunk_map = {cid: all_chunks[cid] for rp in allowed_docs for cid in doc_to_cids.get(rp, ())}

    hips_ranks: dict[int, int] = {}
    hips_scores: dict[int, float] = {}
    if hips_key_ranks:
        hkey_to_cids = corpus["hkey_to_cids"]
        for hkey, h_rank in hips_key_ranks.items():
            h_score = hips_key_scores[hkey]
            for cid in hkey_to_cids.get(hkey, ()):
                if allowed_docs is None or cid in chunk_map:
                    hips_ranks[cid] = h_rank
                    hips_scores[cid] = h_score

    vec_ranks: dict[int, int] | MappingProxyType[Any, Any] = {}
    vec_scores: dict[int, float] | MappingProxyType[Any, Any] = {}
    if q_vec:
        _ensure_corpus_vectors(conn, corpus)
        q_norm = math.sqrt(sum(x * x for x in q_vec))
        q_unit = [x / q_norm for x in q_vec] if q_norm > 0.0 else q_vec
        vec_cache_key = (bytes(array.array("d", q_unit)), sorted_allowed_tuple)
        vec_rank_cache: OrderedDict[Any, Any] = corpus["vec_rank_cache"]
        with _CACHE_LOCK:
            cached_vec_res = vec_rank_cache.get(vec_cache_key)
            if cached_vec_res is not None:
                vec_rank_cache.move_to_end(vec_cache_key)
        if cached_vec_res is not None:
            vec_ranks, vec_scores = cached_vec_res
        else:
            scored = [
                (sum(map(operator.mul, q_unit, c_vec)) / c_norm, cid)
                for cid, rp, c_vec, c_norm in corpus["vec_entries"]
                if allowed_docs is None or rp in allowed_docs
            ]
            for cid, rp in corpus["zero_vec_entries"]:
                if allowed_docs is None or rp in allowed_docs:
                    scored.append((0.0, cid))
            raw_vec_scores = {cid: sim for sim, cid in scored}
            scored.sort(reverse=True)
            raw_vec_ranks = {cid: rank_idx for rank_idx, (_sim, cid) in enumerate(scored[:30], start=1)}
            ro_res = (MappingProxyType(raw_vec_ranks), MappingProxyType(raw_vec_scores))
            with _CACHE_LOCK:
                vec_rank_cache[vec_cache_key] = ro_res
                vec_rank_cache.move_to_end(vec_cache_key)
                if len(vec_rank_cache) > _VEC_RANK_CACHE_MAX:
                    vec_rank_cache.popitem(last=False)
            vec_ranks, vec_scores = ro_res

    rrf_scores: dict[int, float] = {}
    k_rrf = float(active_policy.get("k_rrf", 30.0))
    w_hips = float(active_policy.get("w_hips", 1.15))
    w_fts_with_hips = float(active_policy.get("w_fts_with_hips", 0.85))
    w_fts_solo = float(active_policy.get("w_fts_solo", 1.0))
    w_vec = float(active_policy.get("w_vec", 1.20))
    exact_id_boost = float(active_policy.get("exact_id_boost", 0.025))
    episodic_dampening = float(active_policy.get("episodic_dampening", 0.55))
    graph_1hop_boost = float(active_policy.get("graph_1hop_boost", 1.15))
    graph_neighbor_lead = float(active_policy.get("graph_neighbor_lead_factor", 0.28))

    has_hips = len(hips_ranks) > 0
    query_terms = list(dict.fromkeys(fold_accents(t) for t in re.findall(r"\w+", query) if len(t) >= 2))
    exact_id_matches: dict[int, int] | MappingProxyType[Any, Any] = {}
    if engine != "hips" and exact_identifiers:
        eid_key = (tuple(exact_identifiers), sorted_allowed_tuple)
        with _CACHE_LOCK:
            cached_eid = corpus["exact_id_cache"].get(eid_key)
        if cached_eid is not None:
            exact_id_matches = cached_eid
        else:
            raw_eid: dict[int, int] = {}
            for cid, c in chunk_map.items():
                m_cnt = sum(1 for eid in exact_identifiers if eid in c["_lower_hay"])
                if m_cnt > 0:
                    raw_eid[cid] = m_cnt
            ro_eid = MappingProxyType(raw_eid)
            with _CACHE_LOCK:
                corpus["exact_id_cache"][eid_key] = ro_eid
            exact_id_matches = ro_eid

    if engine == "hips":
        candidate_iter = ((cid, chunk_map[cid]) for cid in sorted(hips_ranks))
    else:
        cand_ids = set(hips_ranks)
        cand_ids.update(fts_ranks)
        cand_ids.update(vec_ranks)
        cand_ids.update(exact_id_matches)
        candidate_iter = ((cid, chunk_map[cid]) for cid in sorted(cand_ids))

    for cid, c in candidate_iter:
        score = 0.0
        doc_t = str(doc_meta.get(c["rel_path"], {}).get("doc_type", "")).lower()
        is_ep_or_ledger = (
            doc_t in ("episodic", "rsi-ledger", "distillation-ledger")
            or c["rel_path"].startswith("04-episodic/")
            or c["rel_path"] == "02-operating-system/autonomous-rsi-distillations.md"
        )
        if engine == "hips":
            if cid in hips_ranks:
                score += 2.0 / (k_rrf + hips_ranks[cid])
        else:
            if cid in hips_ranks:
                score += w_hips / (k_rrf + hips_ranks[cid])
            if cid in fts_ranks:
                fts_w = w_fts_with_hips if has_hips else w_fts_solo
                score += fts_w / (k_rrf + fts_ranks[cid])
            if cid in vec_ranks:
                score += w_vec / (k_rrf + vec_ranks[cid])

        if engine != "hips" and exact_identifiers:
            matched_ids = exact_id_matches.get(cid, 0)
            if matched_ids > 0:
                id_mult = 0.35 if (is_ep_or_ledger and (doc_type_filter or "").lower() != "episodic") else 1.0
                score += exact_id_boost * matched_ids * id_mult
                if cid not in fts_ranks:
                    fts_ranks[cid] = 1

        if score > 0 and engine != "hips" and len(query_terms) >= 2:
            folded_hay = c["_folded_hay"]
            if folded_hay is None:
                folded_hay = fold_accents(f"{c['rel_path']} {c['heading_path']} {c['content']}")
                c["_folded_hay"] = folded_hay
            matched_qt = sum(1 for qt in query_terms if qt in folded_hay)
            cov_ratio = matched_qt / len(query_terms)
            if cov_ratio > 0:
                score += 0.008 * cov_ratio + (0.006 if cov_ratio >= 0.999 else 0.0)

        if score > 0:
            if doc_t in (
                "identity",
                "operating-system",
                "now",
                "venture",
                "research",
                "life-domain",
                "domain",
                "moc",
                "decision",
            ):
                score *= 1.20
            elif doc_t in ("rsi-ledger", "distillation-ledger") or (
                c["rel_path"] == "02-operating-system/autonomous-rsi-distillations.md"
            ):
                score *= 0.60
            elif doc_t == "episodic" or c["rel_path"].startswith("04-episodic/"):
                if (doc_type_filter or "").lower() == "episodic" or "episodic" in query_terms:
                    score *= 1.20
                else:
                    score *= episodic_dampening
            rrf_scores[cid] = score

    top_initial_docs: list[str] = []
    if engine != "hips":
        for cid, _ in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True):
            rp = chunk_map[cid]["rel_path"]
            if doc_meta.get(rp, {}).get("doc_type") in ("moc", "rsi-ledger", "distillation-ledger"):
                continue
            if rp not in top_initial_docs:
                top_initial_docs.append(rp)
            if len(top_initial_docs) >= 3:
                break

    neighbor_docs: dict[str, float] = {}
    adj_links = corpus["adj_links"]
    for doc_path in top_initial_docs:
        parent_best = max(
            (rrf_scores[cid] for cid in doc_to_cids.get(doc_path, ()) if cid in rrf_scores),
            default=0.0,
        )
        for nb in adj_links.get(doc_path, ()):
            if nb in top_initial_docs or doc_meta.get(nb, {}).get("doc_type") in (
                "moc",
                "rsi-ledger",
                "distillation-ledger",
            ):
                continue
            if allowed_docs is not None and nb not in allowed_docs:
                continue
            neighbor_docs[nb] = max(neighbor_docs.get(nb, 0.0), parent_best)

    for nb_doc, parent_score in neighbor_docs.items():
        nb_chunks = doc_to_cids.get(nb_doc, ())
        if not nb_chunks:
            continue
        any_scored = False
        for cid in nb_chunks:
            if cid in rrf_scores:
                rrf_scores[cid] = min(rrf_scores[cid] * graph_1hop_boost, parent_score * 0.98)
                any_scored = True
        if not any_scored and doc_meta.get(nb_doc, {}).get("doc_type") in ("template", "decision", "operating-system"):
            lead_cid = min(nb_chunks, key=lambda i: chunk_map[i]["start_line"])
            rrf_scores[lead_cid] = graph_neighbor_lead * parent_score

    ranked_ids = sorted(rrf_scores.keys(), key=lambda i: rrf_scores[i], reverse=True)[:top_k]
    results = []
    for cid in ranked_ids:
        c = chunk_map[cid]
        results.append(
            {
                "id": cid,
                "rel_path": c["rel_path"],
                "doc_type": doc_meta.get(c["rel_path"], {}).get("doc_type", "note"),
                "abs_path": str(vault_dir / c["rel_path"]),
                "heading": c["heading"],
                "heading_path": c["heading_path"],
                "start_line": c["start_line"],
                "end_line": c["end_line"],
                "content": c["content"],
                "rrf_score": round(rrf_scores[cid], 4),
                "vec_sim": round(vec_scores[cid], 3) if cid in vec_scores else None,
                "fts_rank": fts_ranks.get(cid),
                "hips_rank": hips_ranks.get(cid),
                "hips_score": round(hips_scores[cid], 3) if cid in hips_scores else None,
            }
        )
    return results


def cmd_search(args) -> None:
    vault_dir = resolve_vault_dir(getattr(args, "vault", None), must_exist=True)
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

    query = " ".join(args.query)
    engine = getattr(args, "engine", "auto")
    results = hybrid_retrieve(
        conn,
        vault_dir,
        query,
        top_k=args.limit,
        doc_type_filter=getattr(args, "type", None),
        tag_filter=getattr(args, "tag", None),
        engine=engine,
    )
    conn.close()
    from sovereign_kb.audit_registry import is_bundled_starter_vault_path

    if (vault_dir / ".kb").is_dir() and not is_bundled_starter_vault_path(vault_dir) and len(query.strip()) >= 6:
        try:
            low_margin = False
            margin_val: float | None = None
            if len(results) >= 2:
                s1 = float(results[0].get("rrf_score", 0.0))
                s2 = float(results[1].get("rrf_score", 0.0))
                if s1 > 0:
                    margin_val = (s1 - s2) / s1
                    low_margin = margin_val < 0.05
            if not results or low_margin:
                tel_path = vault_dir / ".kb" / "query_telemetry.jsonl"
                tel_rec = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "query": query.strip(),
                    "engine": engine,
                    "hits": len(results),
                    "top1_path": results[0].get("rel_path") if results else None,
                    "margin": round(margin_val, 6) if margin_val is not None else None,
                }
                with tel_path.open("a", encoding="utf-8") as tf:
                    tf.write(json.dumps(tel_rec, sort_keys=True) + "\n")
        except Exception:
            pass
    if getattr(args, "json", False):
        print(
            json.dumps(
                {"query": query, "engine": engine, "count": len(results), "results": results},
                indent=2,
            )
        )
        return

    if not results:
        print(f"No matches found for: {query}")
        return

    explain = getattr(args, "explain", False)
    if explain:
        print(f"=== Hybrid Search Results for: '{query}' (engine={engine}) ===")
    else:
        match_lbl = "match" if len(results) == 1 else "matches"
        print(f"=== Search Results for: '{query}' ({len(results)} {match_lbl}) ===")
    for idx, r in enumerate(results, start=1):
        snippet = r["content"].strip().replace("\n", "\n    ")
        if len(snippet) > 420:
            snippet = snippet[:420] + "..."
        if explain:
            cos_str = f"{r['vec_sim']:.3f}" if r["vec_sim"] is not None else "lexical-only"
            hips_str = (
                f"hips_wand={r['hips_score']:.2f}#r{r['hips_rank']}" if r.get("hips_score") is not None else "hips=none"
            )
            print(
                f"\n[{idx}] {r['rel_path']}#L{r['start_line']}-L{r['end_line']} — {r['heading_path']} "
                f"(RRF={r['rrf_score']}, {hips_str}, cos={cos_str}, fts_rank={r['fts_rank']})"
            )
        else:
            print(f"\n[{idx}] {r['rel_path']}#L{r['start_line']}-L{r['end_line']} — {r['heading_path']}")
        print(f"    {snippet}")


def call_llm(messages: list[dict]) -> tuple[str, str]:
    for url, model, label in get_chat_endpoints():
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 1200,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"].get("content", "").strip()
                if content:
                    return content, label
        except Exception:
            continue
    return "Error: No configured local LLM endpoint responded.", "none"


def cmd_ask(args) -> None:
    vault_dir = resolve_vault_dir(getattr(args, "vault", None), must_exist=True)
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

    question = " ".join(args.question)
    _cli_pkg = sys.modules.get("sovereign_kb.cli")
    _hybrid = getattr(_cli_pkg, "hybrid_retrieve", hybrid_retrieve)
    results = _hybrid(conn, vault_dir, question, top_k=6)
    conn.close()

    context_blocks = []
    readme_path = vault_dir / "00-identity/README-me.md"
    if readme_path.is_file():
        tier0_readme = readme_path.read_text(encoding="utf-8", errors="replace")
        context_blocks.append(f"--- TIER-0 IDENTITY (00-identity/README-me.md) ---\n{tier0_readme}")
    for r in results:
        context_blocks.append(
            f"--- SOURCE: {r['rel_path']} (Lines {r['start_line']}-{r['end_line']}, Section: {r['heading_path']}) ---\n{r['content']}"
        )

    system_prompt = (
        "You are a sovereign personal knowledge base synthesizer. "
        "Answer the question accurately and concisely using ONLY the provided vault context. "
        "Cite source files inline like (02-operating-system/autonomous-engineering-framework.md)."
    )
    user_prompt = "\n\n".join(context_blocks) + f"\n\nQUESTION: {question}"

    answer, engine = call_llm(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    if engine == "none":
        print(answer, file=sys.stderr)
        sys.exit(1)
    print(f"[{engine}]\n{answer}")


def cmd_capture(args) -> None:
    vault_dir = resolve_vault_dir(getattr(args, "vault", None))
    note_text = " ".join(args.text).strip()
    today = datetime.date.today().isoformat()
    now_hm = datetime.datetime.now().strftime("%H:%M")
    target = vault_dir / "04-episodic" / f"{today}-log.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    with _vault_lock(vault_dir):
        index_path = vault_dir / "INDEX.md"
        existing_md = [p for p in list_vault_md_files(vault_dir) if p != target]
        if not index_path.is_file() and not existing_md:
            index_path.write_text(
                f'---\ntitle: "INDEX — Knowledge Base Map of Content"\ntype: moc\nupdated: {today}\ntags: [index, moc]\n---\n\n'
                "# INDEX — Knowledge Base Map of Content\n\n## Notes & Logs\n",
                encoding="utf-8",
            )

        related_lines: list[str] = []
        if index_path.is_file():
            related_lines.append('  - "[[INDEX]]"')
        if (vault_dir / "01-now" / "now.md").is_file():
            related_lines.append('  - "[[01-now/now]]"')
        related_block = ("related:\n" + "\n".join(related_lines) + "\n") if related_lines else ""

        header = (
            f"---\ntitle: {today} — Episodic Log\ntype: episodic\nupdated: {today}\n"
            f"tags: [episodic, log, capture]\n{related_block}---\n\n# {today} — Episodic Log\n\n"
        )
        try:
            fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, header.encode("utf-8"))
            finally:
                os.close(fd)
        except FileExistsError:
            pass

        # Ensure INDEX.md links to the new episodic log so Gate 3 (Graph Connectivity) stays 100% green
        rel_link = f"04-episodic/{today}-log"
        if index_path.is_file():
            idx_text = index_path.read_text(encoding="utf-8", errors="replace")
            if rel_link not in idx_text:
                idx_text = idx_text.rstrip() + f"\n- [[{rel_link}]] — Episodic Log ({today})\n"
                index_path.write_text(idx_text, encoding="utf-8")

        with target.open("a", encoding="utf-8") as f:
            f.write(f"## {now_hm}\n- {note_text}\n\n")

    print(f"[sovereign-kb capture] Appended to {target.relative_to(vault_dir)}")
    if getattr(args, "sync", False):
        cmd_sync(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=_is_vault_no_embed(vault_dir)))


def cmd_me(args) -> None:
    vault_dir = resolve_vault_dir(getattr(args, "vault", None), must_exist=True)
    printed = False
    for rel in ["00-identity/README-me.md", "01-now/now.md"]:
        p = vault_dir / rel
        if p.exists():
            print(f"================ {rel} ================")
            print(p.read_text(encoding="utf-8", errors="replace"))
            printed = True
    if not printed:
        for fallback_rel in ["INDEX.md", "AGENTS.md", "README.md"]:
            p = vault_dir / fallback_rel
            if p.exists():
                print(f"================ {fallback_rel} ================")
                print(p.read_text(encoding="utf-8", errors="replace"))
                printed = True
                break
    if not printed:
        print(
            f"[skb me] No Tier-0 context files (00-identity/README-me.md, 01-now/now.md, or INDEX.md) found in '{vault_dir}'. Run 'skb init' first.",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_resume(args) -> None:
    """Print zero-context-tax Creative Mode Resumption Packet(s) from 01-now/now.md."""
    vault_dir = resolve_vault_dir(getattr(args, "vault", None), must_exist=True)
    mode = (getattr(args, "mode", None) or "all").strip().lower()
    now_path = vault_dir / "01-now" / "now.md"
    if not now_path.is_file():
        print(f"Error: {now_path} not found.", file=sys.stderr)
        sys.exit(1)

    text = now_path.read_text(encoding="utf-8", errors="replace")
    packets: dict[str, dict[str, str]] = {}
    current_key: str | None = None
    current_title = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        m = re.match(r"^###\s+5\.\d+\s+(.+?)\s*\(`(math|compute|spatial|writing)`\)\s*$", line.strip())
        if m:
            if current_key and current_lines:
                packets[current_key] = {
                    "mode": current_key,
                    "title": current_title,
                    "body": "\n".join(current_lines).strip(),
                }
            current_title = m.group(1).strip()
            current_key = m.group(2).strip().lower()
            current_lines = [line]
            continue
        if current_key is not None:
            if line.startswith("## ") and not line.startswith("### "):
                packets[current_key] = {
                    "mode": current_key,
                    "title": current_title,
                    "body": "\n".join(current_lines).strip(),
                }
                current_key = None
                current_lines = []
            else:
                current_lines.append(line)

    if current_key and current_lines:
        packets[current_key] = {
            "mode": current_key,
            "title": current_title,
            "body": "\n".join(current_lines).strip(),
        }

    selected = packets if mode == "all" else ({mode: packets[mode]} if mode in packets else {})
    if not selected:
        print(
            f"Error: Unknown or missing resumption mode '{mode}'. Available: all, {', '.join(packets.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps({"vault": str(vault_dir), "mode": mode, "packets": selected}, indent=2, ensure_ascii=False))
        return

    print(f"=== Sovereign-KB Creative Mode Resumption ({mode.upper()}) ===")
    for _, pkt in selected.items():
        print(f"\n{pkt['body']}")


def cmd_status(args) -> None:
    vault_dir = resolve_vault_dir(getattr(args, "vault", None), must_exist=True)
    conn = get_db(vault_dir)
    md_files = list_vault_md_files(vault_dir)
    is_stale = _is_index_stale(conn, vault_dir, md_files)
    if is_stale:
        conn.close()
        cmd_sync(
            argparse.Namespace(
                vault=str(vault_dir),
                force=False,
                no_embed=_is_vault_no_embed(vault_dir, False),
                quiet=True,
            )
        )
        conn = get_db(vault_dir)
        is_stale = False
    docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    vecs = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
    links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    conn.close()
    hips_bin = resolve_hips_bin()
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    hips_bytes = sum(f.stat().st_size for f in hips_idx_dir.rglob("*") if f.is_file()) if hips_idx_dir.is_dir() else 0
    hips_status = (
        f"{hips_idx_dir} ({hips_bytes:,} bytes, binary={hips_bin})"
        if hips_idx_dir.is_dir() and hips_bin
        else (f"unbuilt (binary={hips_bin})" if hips_bin else "hips binary not installed (zero-dep FTS5 active)")
    )
    print(f"Vault Path      : {vault_dir}")
    print(f"SQLite Index    : {vault_dir / '.kb' / 'index.db'} (schema v{SCHEMA_VERSION})")
    print(f"HIPS WAND Index : {hips_status}")
    print(f"Documents       : {docs}")
    print(f"Indexed Chunks  : {chunks} ({vecs} with {EMBED_MODEL} vectors)")
    print(f"Wikilink Edges  : {links}")
