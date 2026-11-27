"""Vault corpus loading, deterministic vector extraction, and JEPA transition mining."""

from __future__ import annotations

import array
import hashlib
import math
import random
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

ACTIONS: tuple[str, ...] = (
    "WIKILINK",
    "NEXT_SECTION",
    "EPISODIC_NEXT",
    "EPISODIC_TO_SEMANTIC",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\u0370-\u03ff\u1f00-\u1fff]+")
_EPISODIC_DATE_RE = re.compile(r"^04-episodic/(\d{4}-\d{2}-\d{2})")


def l2_normalize(vec: list[float]) -> list[float]:
    """Return L2-normalized copy of ``vec`` (or zero vector if norm is near zero)."""
    norm_sq = sum(v * v for v in vec)
    if norm_sq <= 1e-24:
        return [0.0] * len(vec)
    inv = 1.0 / math.sqrt(norm_sq)
    return [v * inv for v in vec]


def dot(a: list[float], b: list[float]) -> float:
    """Dot product of two equal-length float vectors."""
    return sum(x * y for x, y in zip(a, b, strict=True))


def hash_text_vector(text: str, dim: int = 128) -> list[float]:
    """Deterministic signed feature-hashing vector over word unigrams + char 3-grams."""
    vec = [0.0] * dim
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    if not tokens:
        vec[0] = 1.0
        return vec

    for tok in tokens:
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign
        if len(tok) >= 3:
            for i in range(len(tok) - 2):
                gram = tok[i : i + 3]
                gd = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                g_idx = int.from_bytes(gd[:4], "little") % dim
                g_sign = 1.0 if (gd[4] & 1) == 0 else -1.0
                vec[g_idx] += 0.35 * g_sign
    return l2_normalize(vec)


def _unpack_f32_blob(blob: bytes | bytearray | memoryview) -> list[float]:
    arr = array.array("f")
    arr.frombytes(bytes(blob))
    return list(arr)


@dataclass(frozen=True)
class JEPATransition:
    """A single ``(src_key, action, tgt_key)`` transition in the vault world model."""

    src_key: str
    action: str
    tgt_key: str
    src_vec: list[float]
    tgt_vec: list[float]
    src_path: str
    tgt_path: str


@dataclass
class VaultCorpus:
    """In-memory representation of vault documents, embeddings, links, and section pairs."""

    vault_dir: Path
    doc_paths: list[str]
    doc_titles: dict[str, str]
    doc_types: dict[str, str]
    doc_tags: dict[str, str]
    doc_texts: dict[str, str]
    doc_vectors: dict[str, list[float]]
    links: list[tuple[str, str]]
    section_pairs: list[tuple[str, str, list[float], list[float]]]
    input_dim: int
    embedding_mode: str  # "dense" or "hashed"
    doc_tokens: dict[str, set[str]] = field(default_factory=dict)
    native_embedded_docs: set[str] = field(default_factory=set)


def _mean_vectors(vectors: list[list[float]], dim: int) -> list[float]:
    if not vectors:
        return [0.0] * dim
    acc = [0.0] * dim
    for v in vectors:
        for i, val in enumerate(v):
            acc[i] += val
    return l2_normalize(acc)


def load_vault_corpus(vault_dir: Path) -> VaultCorpus:
    """Load documents, embeddings (dense or deterministic hashed), links, and section pairs from ``.kb/index.db``."""
    v_root = Path(vault_dir).expanduser().resolve()
    db_path = v_root / ".kb" / "index.db"
    if not db_path.is_file():
        raise FileNotFoundError(f"Vault index not found at '{db_path}'. Run `skb sync` first.")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        doc_rows = conn.execute(
            "SELECT rel_path, COALESCE(title, ''), COALESCE(doc_type, ''), COALESCE(tags, ''), COALESCE(content, '') "
            "FROM documents ORDER BY rel_path"
        ).fetchall()
        chunk_rows = conn.execute(
            "SELECT rel_path, COALESCE(content, ''), embedding FROM chunks ORDER BY rel_path, id"
        ).fetchall()
        link_rows = conn.execute(
            "SELECT source_path, target_path FROM links ORDER BY source_path, target_path"
        ).fetchall()
    finally:
        conn.close()

    doc_paths: list[str] = []
    doc_titles: dict[str, str] = {}
    doc_types: dict[str, str] = {}
    doc_tags: dict[str, str] = {}
    doc_texts: dict[str, str] = {}
    doc_tokens: dict[str, set[str]] = {}
    for rel_path, title, doc_type, tags, content in doc_rows:
        doc_paths.append(rel_path)
        doc_titles[rel_path] = title
        doc_types[rel_path] = doc_type
        doc_tags[rel_path] = tags
        doc_texts[rel_path] = content
        doc_tokens[rel_path] = set(_TOKEN_RE.findall(f"{title} {tags} {content[:4000]}".lower()))

    doc_set = set(doc_paths)
    chunks_by_doc: dict[str, list[tuple[str, list[float] | None]]] = {p: [] for p in doc_paths}
    dense_dim: int | None = None
    for rel_path, content, emb_blob in chunk_rows:
        if rel_path not in doc_set:
            continue
        vec: list[float] | None = None
        if emb_blob is not None and len(emb_blob) >= 16 and len(emb_blob) % 4 == 0:
            vec = l2_normalize(_unpack_f32_blob(emb_blob))
            if dense_dim is None:
                dense_dim = len(vec)
            elif len(vec) != dense_dim:
                vec = None
        chunks_by_doc[rel_path].append((content, vec))

    docs_with_dense = sum(1 for p in doc_paths if any(v is not None for _, v in chunks_by_doc[p]))
    use_dense = dense_dim is not None and len(doc_paths) > 0 and (docs_with_dense / len(doc_paths)) >= 0.50

    doc_vectors: dict[str, list[float]] = {}
    section_pairs: list[tuple[str, str, list[float], list[float]]] = []
    native_embedded_docs: set[str] = set()

    if use_dense and dense_dim is not None:
        input_dim = dense_dim
        embedding_mode = "dense"
        for p in doc_paths:
            d_vecs = [v for _, v in chunks_by_doc[p] if v is not None]
            if d_vecs:
                doc_vectors[p] = _mean_vectors(d_vecs, input_dim)
                native_embedded_docs.add(p)
            # Extract consecutive dense chunk pairs for NEXT_SECTION
            if len(d_vecs) >= 2:
                for i in range(len(d_vecs) - 1):
                    section_pairs.append((f"{p}#chunk:{i}", f"{p}#chunk:{i + 1}", d_vecs[i], d_vecs[i + 1]))

        # Fill any documents lacking dense embeddings via 1-hop neighbor average + small unique text residual
        missing_docs = [p for p in doc_paths if p not in doc_vectors]
        if missing_docs:
            adj: dict[str, list[str]] = {p: [] for p in doc_paths}
            for s, t in link_rows:
                if s in doc_set and t in doc_set and s != t:
                    adj[s].append(t)
                    adj[t].append(s)
            embedded_docs = [p for p in doc_paths if p in doc_vectors]
            for p in missing_docs:
                nbr_vecs = [doc_vectors[n] for n in adj[p] if n in doc_vectors]
                if nbr_vecs:
                    base_vec = _mean_vectors(nbr_vecs, input_dim)
                else:
                    toks = doc_tokens[p]
                    scored: list[tuple[float, str]] = []
                    for cand in embedded_docs:
                        ov = len(toks & doc_tokens[cand])
                        if ov > 0:
                            scored.append((float(ov), cand))
                    scored.sort(reverse=True)
                    if scored:
                        base_vec = _mean_vectors([doc_vectors[c] for _, c in scored[:5]], input_dim)
                    else:
                        base_vec = _mean_vectors([doc_vectors[c] for c in embedded_docs[:8]], input_dim)
                res_vec = hash_text_vector(f"{doc_titles[p]} {doc_texts[p][:2000]}", dim=input_dim)
                doc_vectors[p] = l2_normalize([0.85 * b + 0.15 * r for b, r in zip(base_vec, res_vec, strict=True)])
    else:
        input_dim = 128
        embedding_mode = "hashed"
        native_embedded_docs = set(doc_paths)
        for p in doc_paths:
            header = f"{doc_titles[p]} {doc_tags[p]} "
            text = doc_texts[p]
            doc_vectors[p] = hash_text_vector(header + text, dim=input_dim)
            doc_chunks = [c for c, _ in chunks_by_doc[p] if c.strip()]
            if len(doc_chunks) >= 2:
                chunk_vecs = [hash_text_vector(c, dim=input_dim) for c in doc_chunks]
                for i in range(len(chunk_vecs) - 1):
                    section_pairs.append((f"{p}#chunk:{i}", f"{p}#chunk:{i + 1}", chunk_vecs[i], chunk_vecs[i + 1]))
            elif len(text) >= 120:
                mid = len(text) // 2
                v1 = hash_text_vector(header + text[:mid], dim=input_dim)
                v2 = hash_text_vector(header + text[mid:], dim=input_dim)
                section_pairs.append((f"{p}#half:0", f"{p}#half:1", v1, v2))

    clean_links = [(s, t) for s, t in link_rows if s in doc_set and t in doc_set and s != t]
    return VaultCorpus(
        vault_dir=v_root,
        doc_paths=doc_paths,
        doc_titles=doc_titles,
        doc_types=doc_types,
        doc_tags=doc_tags,
        doc_texts=doc_texts,
        doc_vectors=doc_vectors,
        links=clean_links,
        section_pairs=section_pairs,
        input_dim=input_dim,
        embedding_mode=embedding_mode,
        doc_tokens=doc_tokens,
        native_embedded_docs=native_embedded_docs,
    )


def embed_query_for_corpus(vault_dir: Path, corpus: VaultCorpus, query: str) -> tuple[list[float], str | None]:
    """
    Resolve ``query`` into an input vector matching ``corpus.input_dim``.
    Returns ``(vector, matched_doc_path_or_none)``.
    """
    q_clean = query.strip()
    if q_clean in corpus.doc_vectors:
        return list(corpus.doc_vectors[q_clean]), q_clean
    for p in corpus.doc_paths:
        if p.endswith(q_clean) or Path(p).stem == q_clean:
            return list(corpus.doc_vectors[p]), p

    if corpus.embedding_mode == "hashed":
        return hash_text_vector(q_clean, dim=corpus.input_dim), None

    # Dense mode: try live embedding server first, then guaranteed offline token-overlap projection
    try:
        from sovereign_kb.cli.markdown_embed import get_embedding

        emb = get_embedding(q_clean)
        if emb is not None and len(emb) == corpus.input_dim:
            return l2_normalize([float(x) for x in emb]), None
    except Exception:
        pass

    q_toks = set(_TOKEN_RE.findall(q_clean.lower()))
    scored: list[tuple[float, str]] = []
    for p in corpus.doc_paths:
        ov = len(q_toks & corpus.doc_tokens.get(p, set()))
        if ov > 0:
            scored.append((float(ov), p))
    scored.sort(reverse=True)
    if scored:
        acc = [0.0] * corpus.input_dim
        for weight, p in scored[:6]:
            vec = corpus.doc_vectors[p]
            for i, val in enumerate(vec):
                acc[i] += weight * val
        return l2_normalize(acc), None

    return _mean_vectors(list(corpus.doc_vectors.values())[:8], corpus.input_dim), None


def extract_transitions(
    corpus: VaultCorpus,
    seed: int = 42,
    max_section_pairs: int = 400,
) -> tuple[list[JEPATransition], list[JEPATransition]]:
    """
    Extract all 4 JEPA transition families and return a deterministic stratified
    80/20 ``(train_transitions, heldout_transitions)`` split.
    """
    non_moc_links = [
        (s, t) for s, t in corpus.links if corpus.doc_types.get(s, "") != "moc" and corpus.doc_types.get(t, "") != "moc"
    ]
    use_links = non_moc_links if len(non_moc_links) >= 10 else corpus.links

    by_action: dict[str, list[JEPATransition]] = {a: [] for a in ACTIONS}
    for s, t in use_links:
        if s in corpus.doc_vectors and t in corpus.doc_vectors:
            by_action["WIKILINK"].append(
                JEPATransition(
                    src_key=s,
                    action="WIKILINK",
                    tgt_key=t,
                    src_vec=corpus.doc_vectors[s],
                    tgt_vec=corpus.doc_vectors[t],
                    src_path=s,
                    tgt_path=t,
                )
            )
            if s.startswith("04-episodic/") and not t.startswith("04-episodic/"):
                by_action["EPISODIC_TO_SEMANTIC"].append(
                    JEPATransition(
                        src_key=s,
                        action="EPISODIC_TO_SEMANTIC",
                        tgt_key=t,
                        src_vec=corpus.doc_vectors[s],
                        tgt_vec=corpus.doc_vectors[t],
                        src_path=s,
                        tgt_path=t,
                    )
                )

    # Chronological episodic transitions
    episodic_dated: list[tuple[str, str]] = []
    for p in corpus.doc_paths:
        m = _EPISODIC_DATE_RE.match(p)
        if m and p in corpus.doc_vectors:
            episodic_dated.append((m.group(1), p))
    episodic_dated.sort()
    for i in range(len(episodic_dated) - 1):
        _, s = episodic_dated[i]
        _, t = episodic_dated[i + 1]
        if s != t:
            by_action["EPISODIC_NEXT"].append(
                JEPATransition(
                    src_key=s,
                    action="EPISODIC_NEXT",
                    tgt_key=t,
                    src_vec=corpus.doc_vectors[s],
                    tgt_vec=corpus.doc_vectors[t],
                    src_path=s,
                    tgt_path=t,
                )
            )

    rng = random.Random(seed)
    sec_candidates = list(corpus.section_pairs)
    if len(sec_candidates) > max_section_pairs:
        rng.shuffle(sec_candidates)
        sec_candidates = sec_candidates[:max_section_pairs]
    for s_key, t_key, s_vec, t_vec in sec_candidates:
        src_doc = s_key.split("#", 1)[0]
        tgt_doc = t_key.split("#", 1)[0]
        by_action["NEXT_SECTION"].append(
            JEPATransition(
                src_key=s_key,
                action="NEXT_SECTION",
                tgt_key=t_key,
                src_vec=s_vec,
                tgt_vec=t_vec,
                src_path=src_doc,
                tgt_path=tgt_doc,
            )
        )

    train: list[JEPATransition] = []
    heldout: list[JEPATransition] = []
    for action in ACTIONS:
        items = list(by_action[action])
        rng.shuffle(items)
        if len(items) <= 1:
            train.extend(items)
            continue
        n_test = max(1, int(round(0.2 * len(items))))
        if n_test >= len(items):
            n_test = len(items) - 1
        heldout.extend(items[:n_test])
        train.extend(items[n_test:])

    rng.shuffle(train)
    rng.shuffle(heldout)
    return train, heldout
