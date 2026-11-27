"""Dataset builders for all 4 sovereign vault training tracks."""

from __future__ import annotations

import json
import math
import random
import re
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .corpus import NoteChunk, NoteDocument, load_chunks, load_notes

TOKEN_RE = re.compile(r"[a-zA-Z0-9_\u0370-\u03ff\u1f00-\u1fff]{2,}")
DOC_BOUNDARY = "\n\n<|endoftext|>\n\n"

CRITIC_SYSTEM_PROMPT = (
    "You are the Sovereign Tri-Council & Goldwood Index (GWI v1.4.0) Technical Critic. "
    "Evaluate artifacts strictly against Laws L0-L10 and GWI gates (G >= 9.0, P0/P1/P2 defects). "
    "Return valid JSON with keys: verdict ('APPROVED'|'CHANGES_REQUESTED'), "
    "gwi_score_est (float), p0_findings (list[str]), p1_findings (list[str]), "
    "violated_laws (list[str]), and concise_remediation (str)."
)

REQUIRED_CRITIC_KEYS = {
    "verdict",
    "gwi_score_est",
    "p0_findings",
    "p1_findings",
    "violated_laws",
    "concise_remediation",
}

DEFAULT_TWIN_SYSTEM_PROMPT = (
    "You are a Sovereign AI Twin grounded strictly in this Markdown vault. "
    "Reason from first principles, cite verified rules, and stay concise and architectural."
)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


class _TfidfIndex:
    """Lightweight stdlib TF-IDF index for mining lexical hard negatives."""

    def __init__(self, chunks: list[NoteChunk]) -> None:
        self.chunks = chunks
        doc_freqs: Counter[str] = Counter()
        self.tf_vecs: list[dict[str, float]] = []
        self.norms: list[float] = []
        n_docs = max(1, len(chunks))

        tokenized = [_tokenize(f"{c.heading_path} {c.text}") for c in chunks]
        for toks in tokenized:
            for tok in set(toks):
                doc_freqs[tok] += 1

        self.idf = {tok: math.log((n_docs + 1) / (df + 1)) + 1.0 for tok, df in doc_freqs.items()}

        for toks in tokenized:
            counts = Counter(toks)
            total = max(1, len(toks))
            vec = {tok: (cnt / total) * self.idf.get(tok, 1.0) for tok, cnt in counts.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self.tf_vecs.append(vec)
            self.norms.append(norm)

    def most_similar_other(self, query: str, exclude_paths: set[str], top_k: int = 8) -> list[NoteChunk]:
        q_toks = _tokenize(query)
        if not q_toks:
            return []
        q_counts = Counter(q_toks)
        total = max(1, len(q_toks))
        q_vec = {tok: (cnt / total) * self.idf.get(tok, 1.0) for tok, cnt in q_counts.items()}
        q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0

        scored: list[tuple[float, int]] = []
        for idx, (d_vec, d_norm, chunk) in enumerate(zip(self.tf_vecs, self.norms, self.chunks, strict=True)):
            if chunk.rel_path in exclude_paths:
                continue
            dot = sum(w * d_vec.get(tok, 0.0) for tok, w in q_vec.items())
            if dot > 0.0:
                scored.append((dot / (q_norm * d_norm), idx))
        scored.sort(reverse=True)
        return [self.chunks[idx] for _, idx in scored[:top_k]]


def _build_stem_lookup(notes: list[NoteDocument]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for note in notes:
        stem = note.rel_path[:-3] if note.rel_path.endswith(".md") else note.rel_path
        lookup[stem] = note.rel_path
        lookup[Path(stem).name] = note.rel_path
    return lookup


def build_retrieval_triples(
    vault_dir: Path,
    scope: str = "public-safe",
    seed: int = 42,
) -> dict[str, list[dict[str, Any]]]:
    """Build train/val (query, positive, hard_negative) triples without cross-split leakage."""
    rng = random.Random(seed)
    notes = load_notes(vault_dir, scope=scope)
    chunks = load_chunks(vault_dir, scope=scope, max_words=380, overlap_words=40)
    stem_lookup = _build_stem_lookup(notes)

    chunks_by_path: dict[str, list[NoteChunk]] = {}
    for ch in chunks:
        chunks_by_path.setdefault(ch.rel_path, []).append(ch)

    splits: dict[str, list[dict[str, Any]]] = {"train": [], "val": []}
    for split_name in ("train", "val"):
        split_chunks = [c for c in chunks if c.split == split_name]
        if len(split_chunks) < 2:
            continue
        index = _TfidfIndex(split_chunks)
        seen_pairs: set[tuple[str, str]] = set()

        def pick_hard_negative(
            query: str,
            exclude_paths: set[str],
            _index: _TfidfIndex = index,
            _chunks: list[NoteChunk] = split_chunks,
        ) -> str | None:
            cands = _index.most_similar_other(query, exclude_paths=exclude_paths, top_k=5)
            if cands:
                return rng.choice(cands[:3]).text
            fallback = [c for c in _chunks if c.rel_path not in exclude_paths]
            return rng.choice(fallback).text if fallback else None

        for ch in split_chunks:
            query = f"{ch.heading_path} ({ch.domain})"
            pair_key = (query, ch.chunk_id)
            if pair_key not in seen_pairs:
                neg = pick_hard_negative(query, exclude_paths={ch.rel_path})
                if neg:
                    seen_pairs.add(pair_key)
                    splits[split_name].append(
                        {
                            "query": query,
                            "positive": ch.text,
                            "hard_negative": neg,
                            "source": "heading",
                            "rel_path": ch.rel_path,
                        }
                    )

            for target in list(ch.wikilinks) + list(ch.related):
                target_path = stem_lookup.get(target) or stem_lookup.get(Path(target).name)
                if not target_path or target_path == ch.rel_path:
                    continue
                target_chunks = [tc for tc in chunks_by_path.get(target_path, []) if tc.split == split_name]
                if not target_chunks:
                    continue
                pos_chunk = target_chunks[0]
                snippet = " ".join(ch.text.split()[:60])
                edge_query = f"Related to {ch.title} ({ch.heading_path}): {snippet}"
                edge_key = (edge_query[:120], pos_chunk.chunk_id)
                if edge_key in seen_pairs:
                    continue
                neg_edge = pick_hard_negative(edge_query, exclude_paths={ch.rel_path, target_path})
                if not neg_edge:
                    continue
                seen_pairs.add(edge_key)
                splits[split_name].append(
                    {
                        "query": edge_query,
                        "positive": pos_chunk.text,
                        "hard_negative": neg_edge,
                        "source": "wikilink",
                        "rel_path": ch.rel_path,
                    }
                )
    return splits


def write_retrieval_datasets(
    vault_dir: Path,
    out_dir: Path,
    scope: str = "public-safe",
    seed: int = 42,
) -> dict[str, int]:
    """Write retrieval_train.jsonl and retrieval_val.jsonl."""
    out_dir.mkdir(parents=True, exist_ok=True)
    splits = build_retrieval_triples(vault_dir, scope=scope, seed=seed)
    counts: dict[str, int] = {}
    for split_name, rows in splits.items():
        out_path = out_dir / f"retrieval_{split_name}.jsonl"
        with out_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts[split_name] = len(rows)
    return counts


def write_nanolm_corpus(
    vault_dir: Path,
    out_dir: Path,
    scope: str = "public-safe",
) -> dict[str, dict[str, int]]:
    """Write nanolm_train.txt and nanolm_val.txt with document boundary tokens."""
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = load_notes(vault_dir, scope=scope)
    grouped: dict[str, list[str]] = {"train": [], "val": []}
    for note in notes:
        body = note.body.strip()
        if len(body.split()) < 15:
            continue
        grouped[note.split].append(f"# {note.title} [{note.domain}]\n\n{body}")

    stats: dict[str, dict[str, int]] = {}
    for split_name, docs in grouped.items():
        text = DOC_BOUNDARY.join(docs) + (DOC_BOUNDARY if docs else "")
        out_path = out_dir / f"nanolm_{split_name}.txt"
        out_path.write_text(text, encoding="utf-8")
        stats[split_name] = {
            "docs": len(docs),
            "words": len(text.split()),
            "bytes": len(text.encode("utf-8")),
        }
    return stats


def is_valid_critic_json(raw_json: str) -> bool:
    """Verify chosen critic output is valid JSON with all required GWI keys."""
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and REQUIRED_CRITIC_KEYS.issubset(parsed.keys())


def _call_teacher_json(
    api_base: str,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    timeout: int = 90,
) -> list[dict[str, Any]]:
    url = api_base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = str(data["choices"][0]["message"]["content"]).strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(content)
    return [x for x in parsed if isinstance(x, dict)] if isinstance(parsed, list) else []


def collect_critic_sources(vault_dir: Path, scope: str = "public-safe") -> list[dict[str, Any]]:
    """Collect audit, lens, and standards notes from any sovereign-kb vault."""
    notes = load_notes(vault_dir, scope=scope)
    sources: list[dict[str, Any]] = []
    for note in notes:
        low = note.rel_path.lower()
        if not any(
            tok in low for tok in ("audit", "triad", "remediation", "goldwood", "subtractive", "lens", "framework")
        ):
            continue
        words = note.body.split()
        for idx in range(0, min(len(words), 2500), 500):
            excerpt = " ".join(words[idx : idx + 500])
            if len(excerpt.split()) < 40:
                continue
            sources.append(
                {
                    "rel_path": note.rel_path,
                    "title": note.title,
                    "window_idx": idx // 500,
                    "split": note.split,
                    "excerpt": excerpt,
                }
            )
    return sources


def build_critic_dataset(
    vault_dir: Path,
    out_dir: Path,
    api_base: str = "http://127.0.0.1:8000/v1",
    model: str = "qwen3.6-35b-a3b",
    api_key: str = "",
    max_windows: int = 120,
    scope: str = "public-safe",
    workers: int = 4,
) -> dict[str, int]:
    """Synthesize Track 2 GWI Critic SFT + preference pairs via an OpenAI-compatible teacher."""
    out_dir.mkdir(parents=True, exist_ok=True)
    all_sources = collect_critic_sources(vault_dir, scope=scope)
    val_src = [s for s in all_sources if s["split"] == "val"][: max(8, max_windows // 7)]
    train_src = [s for s in all_sources if s["split"] == "train"][: max_windows - len(val_src)]
    sources = train_src + val_src

    teacher_sys = (
        "You are a dataset synthesis engine. Given a real audit or Goldwood GWI standard excerpt, "
        "generate 2 distinct critique evaluation pairs in a JSON array. Each element MUST have keys: "
        "'prompt', 'chosen' (valid JSON string with verdict, gwi_score_est, p0_findings, p1_findings, "
        "violated_laws, concise_remediation), and 'rejected' (a vague rubber-stamped response). "
        "Output ONLY raw valid JSON array."
    )

    def _one(src: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
        prompt = f"Source: {src['title']} ({src['rel_path']} window {src['window_idx']})\n\nExcerpt:\n{src['excerpt']}"
        try:
            items = _call_teacher_json(api_base, model, api_key, teacher_sys, prompt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError):
            return str(src["split"]), str(src["rel_path"]), []
        return str(src["split"]), str(src["rel_path"]), items

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(_one, sources))

    rows_by_split: dict[str, list[dict[str, Any]]] = {"train": [], "val": []}
    for split_name, rel_path, items in results:
        for item in items:
            p_txt = str(item.get("prompt") or "").strip()
            chosen_raw = item.get("chosen")
            rejected_raw = item.get("rejected")
            chosen = chosen_raw if isinstance(chosen_raw, str) else json.dumps(chosen_raw, ensure_ascii=False)
            rejected = rejected_raw if isinstance(rejected_raw, str) else json.dumps(rejected_raw, ensure_ascii=False)
            if len(p_txt) < 20 or len(chosen) < 20 or len(rejected) < 10 or not is_valid_critic_json(chosen):
                continue
            rows_by_split[split_name].append(
                {
                    "system": CRITIC_SYSTEM_PROMPT,
                    "prompt": p_txt,
                    "chosen": chosen,
                    "rejected": rejected,
                    "rel_path": rel_path,
                    "split": split_name,
                }
            )

    counts: dict[str, int] = {}
    for split_name, rows in rows_by_split.items():
        out_path = out_dir / f"critic_{split_name}.jsonl"
        with out_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts[split_name] = len(rows)
    return counts


def build_twin_dataset(
    vault_dir: Path,
    out_dir: Path,
    api_base: str = "http://127.0.0.1:8000/v1",
    model: str = "qwen3.6-35b-a3b",
    api_key: str = "",
    max_chunks: int = 90,
    scope: str = "public-safe",
    workers: int = 4,
    system_prompt: str = DEFAULT_TWIN_SYSTEM_PROMPT,
) -> dict[str, int]:
    """Distill Track 3 Personal Twin SFT pairs from vault chunks."""
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = load_chunks(vault_dir, scope=scope, max_words=380, overlap_words=30)
    val_chunks = [c for c in chunks if c.split == "val"][: max(10, max_chunks // 6)]
    train_chunks = [c for c in chunks if c.split == "train"][: max_chunks - len(val_chunks)]
    selected = train_chunks + val_chunks

    teacher_sys = (
        "You are a dataset distillation engine. Given a grounded excerpt from a Markdown knowledge base, "
        "produce 2 high-signal instruction/answer pairs in a JSON array. Each element MUST have keys 'user' "
        "and 'assistant'. Output ONLY a raw JSON array."
    )

    def _one(ch: NoteChunk) -> tuple[str, str, list[dict[str, Any]]]:
        prompt = f"Context: {ch.title} | {ch.heading_path} ({ch.rel_path})\n\nExcerpt:\n{ch.text}"
        try:
            pairs = _call_teacher_json(api_base, model, api_key, teacher_sys, prompt, max_tokens=900)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError):
            return ch.split, ch.rel_path, []
        return ch.split, ch.rel_path, pairs

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(_one, selected))

    rows_by_split: dict[str, list[dict[str, Any]]] = {"train": [], "val": []}
    for split_name, rel_path, pairs in results:
        for pair in pairs:
            u_txt = str(pair.get("user") or "").strip()
            a_txt = str(pair.get("assistant") or "").strip()
            if len(u_txt) < 12 or len(a_txt) < 20:
                continue
            rows_by_split[split_name].append(
                {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": u_txt},
                        {"role": "assistant", "content": a_txt},
                    ],
                    "rel_path": rel_path,
                    "split": split_name,
                }
            )

    counts: dict[str, int] = {}
    for split_name, rows in rows_by_split.items():
        out_path = out_dir / f"twin_{split_name}.jsonl"
        with out_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts[split_name] = len(rows)
    return counts
