"""Hierarchical Chunk <-> Document JEPA (`H-JEPA`) for fine-grained passage retrieval."""

from __future__ import annotations

import datetime
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .features import (
    JEPATransition,
    _unpack_f32_blob,
    dot,
    embed_query_for_corpus,
    hash_text_vector,
    l2_normalize,
    load_vault_corpus,
)
from .model import JEPAConfig, VaultJEPA, compute_collapse_metrics

DEFAULT_HJEPA_REL = ".kb/hjepa_model.json"


@dataclass(frozen=True)
class ChunkRecord:
    """Single Markdown section chunk loaded from ``.kb/index.db``."""

    chunk_id: int
    rel_path: str
    heading_path: str
    start_line: int
    end_line: int
    content: str
    vec: list[float]


def load_chunk_records(vault_dir: Path, input_dim: int, use_dense: bool) -> list[ChunkRecord]:
    """Load chunk records and normalized vectors from ``.kb/index.db``."""
    v_root = Path(vault_dir).expanduser().resolve()
    db_path = v_root / ".kb" / "index.db"
    if not db_path.is_file():
        raise FileNotFoundError(f"Vault index not found at '{db_path}'. Run `skb sync` first.")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, rel_path, COALESCE(heading_path, ''), COALESCE(start_line, 1), "
            "COALESCE(end_line, 1), COALESCE(content, ''), embedding "
            "FROM chunks ORDER BY rel_path, id"
        ).fetchall()
    finally:
        conn.close()

    corpus = load_vault_corpus(v_root)
    records: list[ChunkRecord] = []
    for cid, rel_path, heading_path, start_line, end_line, content, emb_blob in rows:
        vec: list[float] | None = None
        if use_dense and emb_blob is not None and len(emb_blob) >= 16 and len(emb_blob) % 4 == 0:
            cand = l2_normalize(_unpack_f32_blob(emb_blob))
            if len(cand) == input_dim:
                vec = cand
        if vec is None:
            if use_dense and rel_path in corpus.doc_vectors:
                base = corpus.doc_vectors[rel_path]
                res = hash_text_vector(f"{heading_path} {content}", dim=input_dim)
                vec = l2_normalize([0.85 * b + 0.15 * r for b, r in zip(base, res, strict=True)])
            else:
                vec = hash_text_vector(f"{heading_path} {content}", dim=input_dim)
        records.append(
            ChunkRecord(
                chunk_id=int(cid),
                rel_path=str(rel_path),
                heading_path=str(heading_path),
                start_line=int(start_line),
                end_line=int(end_line),
                content=str(content),
                vec=vec,
            )
        )
    return records


def _extract_chunk_transitions(
    records: list[ChunkRecord],
    doc_vectors: dict[str, list[float]],
    links: list[tuple[str, str]],
    seed: int = 42,
    max_per_action: int = 450,
) -> tuple[list[JEPATransition], list[JEPATransition]]:
    by_doc: dict[str, list[ChunkRecord]] = {}
    for r in records:
        by_doc.setdefault(r.rel_path, []).append(r)

    sec_t: list[JEPATransition] = []
    link_t: list[JEPATransition] = []
    c2d_t: list[JEPATransition] = []

    for doc_path, c_list in by_doc.items():
        for i in range(len(c_list) - 1):
            c1, c2 = c_list[i], c_list[i + 1]
            sec_t.append(
                JEPATransition(
                    src_key=f"{doc_path}#c{c1.chunk_id}",
                    action="NEXT_SECTION",
                    tgt_key=f"{doc_path}#c{c2.chunk_id}",
                    src_vec=c1.vec,
                    tgt_vec=c2.vec,
                    src_path=doc_path,
                    tgt_path=doc_path,
                )
            )
        d_vec = doc_vectors.get(doc_path)
        if d_vec is not None:
            for c in c_list:
                c2d_t.append(
                    JEPATransition(
                        src_key=f"{doc_path}#c{c.chunk_id}",
                        action="EPISODIC_TO_SEMANTIC",
                        tgt_key=doc_path,
                        src_vec=c.vec,
                        tgt_vec=d_vec,
                        src_path=doc_path,
                        tgt_path=doc_path,
                    )
                )

    for src_doc, tgt_doc in links:
        src_chunks = by_doc.get(src_doc, [])
        tgt_chunks = by_doc.get(tgt_doc, [])
        if not src_chunks or not tgt_chunks:
            continue
        tgt_stem = Path(tgt_doc).stem.lower()
        matching_src = [c for c in src_chunks if tgt_stem in c.content.lower()]
        c_src = matching_src[0] if matching_src else src_chunks[0]
        c_tgt = tgt_chunks[0]
        link_t.append(
            JEPATransition(
                src_key=f"{src_doc}#c{c_src.chunk_id}",
                action="WIKILINK",
                tgt_key=f"{tgt_doc}#c{c_tgt.chunk_id}",
                src_vec=c_src.vec,
                tgt_vec=c_tgt.vec,
                src_path=src_doc,
                tgt_path=tgt_doc,
            )
        )

    rng = random.Random(seed)
    train: list[JEPATransition] = []
    heldout: list[JEPATransition] = []
    for bucket in (sec_t, link_t, c2d_t):
        rng.shuffle(bucket)
        capped = bucket[:max_per_action]
        if len(capped) <= 1:
            train.extend(capped)
            continue
        n_test = max(1, int(round(0.2 * len(capped))))
        if n_test >= len(capped):
            n_test = len(capped) - 1
        heldout.extend(capped[:n_test])
        train.extend(capped[n_test:])
    rng.shuffle(train)
    rng.shuffle(heldout)
    return train, heldout


def train_hierarchical_jepa(
    vault_dir: Path,
    config: JEPAConfig | None = None,
) -> dict[str, Any]:
    """Train chunk-level ``H-JEPA`` (``NEXT_SECTION``, ``WIKILINK``, ``CHUNK->DOC``) and save ``.kb/hjepa_model.json``."""
    v_root = Path(vault_dir).expanduser().resolve()
    corpus = load_vault_corpus(v_root)
    records = load_chunk_records(v_root, corpus.input_dim, use_dense=(corpus.embedding_mode == "dense"))
    seed = config.seed if config is not None else 42
    train_t, held_t = _extract_chunk_transitions(records, corpus.doc_vectors, corpus.links, seed=seed)
    if not train_t:
        raise ValueError("No chunk-level transitions found for H-JEPA.")

    cfg = config or JEPAConfig(input_dim=corpus.input_dim, epochs=20)
    cfg.input_dim = corpus.input_dim
    model = VaultJEPA(cfg)
    train_stats = model.train(train_t)

    eval_t = held_t if held_t else train_t
    sample_records = records[: min(250, len(records))]
    tgt_latents = {f"{r.rel_path}#c{r.chunk_id}": model.encode_target(r.vec) for r in sample_records}
    rng = random.Random(cfg.seed)
    pos_e: list[float] = []
    neg_e: list[float] = []
    latents: list[list[float]] = []
    tgt_keys = list(tgt_latents.keys())
    for tr in eval_t:
        sx = model.encode_context(tr.src_vec)
        sy = model.encode_target(tr.tgt_vec)
        shat = model.predict(sx, tr.action)
        pos_e.append(model.energy(shat, sy))
        latents.append(sx)
        if tgt_keys:
            nk = rng.choice(tgt_keys)
            neg_e.append(model.energy(shat, tgt_latents[nk]))
    collapse = compute_collapse_metrics(latents, pos_e, neg_e, cfg.latent_dim)

    # Evaluate held-out chunk-level NEXT_SECTION / WIKILINK ranking over sampled chunks
    rec_by_key = {f"{r.rel_path}#c{r.chunk_id}": r for r in sample_records}
    chunk_eval = [tr for tr in eval_t if tr.action != "EPISODIC_TO_SEMANTIC" and tr.tgt_key in rec_by_key][:100]
    mrr_raw = 0.0
    mrr_jepa = 0.0
    r10_raw = 0
    r10_jepa = 0
    if chunk_eval:
        for tr in chunk_eval:
            sx = model.encode_context(tr.src_vec)
            shat = model.predict(sx, tr.action)
            cands = [k for k in rec_by_key if k != tr.src_key]
            raw_rank = sorted(cands, key=lambda k: dot(tr.src_vec, rec_by_key[k].vec), reverse=True)
            jepa_rank = sorted(cands, key=lambda k: model.energy(shat, tgt_latents[k]))
            for is_j, rlist in ((False, raw_rank), (True, jepa_rank)):
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
        mrr_raw /= len(chunk_eval)
        mrr_jepa /= len(chunk_eval)
        r10_raw_f = r10_raw / len(chunk_eval)
        r10_jepa_f = r10_jepa / len(chunk_eval)
    else:
        r10_raw_f = 0.0
        r10_jepa_f = 0.0

    ckpt_path = v_root / DEFAULT_HJEPA_REL
    metadata = {
        "trained_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "chunks_indexed": len(records),
        "train_transitions": len(train_t),
        "heldout_transitions": len(held_t),
        "collapse_metrics": collapse,
    }
    sha = model.save(ckpt_path, metadata=metadata)
    return {
        "vault_dir": str(v_root),
        "checkpoint_path": str(ckpt_path),
        "model_sha256": sha,
        "embedding_mode": corpus.embedding_mode,
        "chunks_indexed": len(records),
        "train_transitions": len(train_t),
        "heldout_transitions": len(held_t),
        "train_stats": train_stats,
        "collapse_metrics": collapse,
        "chunk_eval_count": len(chunk_eval),
        "raw_chunk_mrr5": round(mrr_raw, 4),
        "hjepa_chunk_mrr5": round(mrr_jepa, 4),
        "raw_chunk_r10": round(r10_raw_f, 4),
        "hjepa_chunk_r10": round(r10_jepa_f, 4),
    }


def retrieve_chunks_with_hjepa(
    vault_dir: Path,
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Retrieve exact Markdown line-range chunks scored by H-JEPA (chunk + doc hierarchy)."""
    v_root = Path(vault_dir).expanduser().resolve()
    h_ckpt = v_root / DEFAULT_HJEPA_REL
    d_ckpt = v_root / ".kb" / "jepa_model.json"
    if not h_ckpt.is_file() and not d_ckpt.is_file():
        return []

    corpus = load_vault_corpus(v_root)
    q_vec, _ = embed_query_for_corpus(v_root, corpus, query)
    records = load_chunk_records(v_root, corpus.input_dim, use_dense=(corpus.embedding_mode == "dense"))

    chunk_model = VaultJEPA.load(h_ckpt)[0] if h_ckpt.is_file() else VaultJEPA.load(d_ckpt)[0]
    doc_model = VaultJEPA.load(d_ckpt)[0] if d_ckpt.is_file() else chunk_model

    sq_c = chunk_model.encode_context(q_vec)
    sq_c_pred = chunk_model.predict(sq_c, "NEXT_SECTION")
    sq_d = doc_model.encode_context(q_vec)
    sq_d_pred = doc_model.predict(sq_d, "WIKILINK")

    doc_e: dict[str, float] = {}
    for rp, dv in corpus.doc_vectors.items():
        sy_d = doc_model.encode_target(dv)
        doc_e[rp] = 0.65 * doc_model.energy(sq_d_pred, sy_d) + 0.35 * doc_model.energy(sq_d, sy_d)

    scored: list[tuple[float, ChunkRecord]] = []
    for rec in records:
        if corpus.doc_types.get(rec.rel_path, "") == "moc":
            continue
        sy_c = chunk_model.encode_target(rec.vec)
        ec = 0.55 * chunk_model.energy(sq_c, sy_c) + 0.45 * chunk_model.energy(sq_c_pred, sy_c)
        ed = doc_e.get(rec.rel_path, ec)
        blended_e = 0.60 * ec + 0.40 * ed
        scored.append((blended_e, rec))

    scored.sort(key=lambda item: item[0])
    out: list[dict[str, Any]] = []
    for e, rec in scored[: max(1, top_k)]:
        out.append(
            {
                "id": rec.chunk_id,
                "rel_path": rec.rel_path,
                "doc_type": corpus.doc_types.get(rec.rel_path, "note"),
                "abs_path": str(v_root / rec.rel_path),
                "heading": rec.heading_path.split(">")[-1].strip() if rec.heading_path else rec.rel_path,
                "heading_path": rec.heading_path,
                "start_line": rec.start_line,
                "end_line": rec.end_line,
                "content": rec.content,
                "jepa_energy": round(e, 4),
                "compatibility": round(max(0.0, 1.0 - 0.5 * e), 4),
            }
        )
    return out
