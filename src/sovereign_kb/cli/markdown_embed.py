"""Markdown parsing, frontmatter/wikilink extraction, chunking, embeddings, and FTS query compiler."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .config_db import EMBED_MODEL, fold_accents, get_embed_endpoints


def list_vault_md_files(vault_dir: Path) -> list[Path]:
    if not vault_dir.exists():
        return []
    return sorted(
        p for p in vault_dir.rglob("*.md") if not any(part.startswith(".") for part in p.relative_to(vault_dir).parts)
    )


def build_stem_lookup(vault_dir: Path, md_files: list[Path] | None = None) -> dict[str, str]:
    if md_files is None:
        md_files = list_vault_md_files(vault_dir)
    lookup: dict[str, str] = {}
    stem_depths: dict[str, int] = {}
    for p in md_files:
        rel_path_obj = p.relative_to(vault_dir)
        rel = str(rel_path_obj)
        rel_no_ext = str(rel_path_obj.with_suffix(""))
        depth = len(rel_path_obj.parts)
        lookup[rel] = rel
        lookup[rel_no_ext] = rel
        if p.stem not in lookup or depth < stem_depths.get(p.stem, 999):
            lookup[p.stem] = rel
            lookup[f"{p.stem}.md"] = rel
            stem_depths[p.stem] = depth
    return lookup


def _find_frontmatter_end(text: str) -> int:
    """Return index of closing '\\n---\\n' for valid YAML frontmatter, or -1 if absent."""
    if not text.startswith("---\n"):
        return -1
    end = text.find("\n---\n", 4)
    if end == -1 and text.endswith("\n---"):
        end = len(text) - 4
    if end == -1:
        return -1
    block = text[4:end]
    if len(block.splitlines()) > 120:
        return -1
    has_yaml_key = any(":" in ln and not ln.lstrip().startswith(("#", "-")) for ln in block.splitlines())
    return end if has_yaml_key else -1


def parse_frontmatter(text: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "title": "",
        "type": "note",
        "tier": "",
        "updated": "",
        "tags": "",
        "tags_list": [],
        "related": [],
        "has_frontmatter": False,
        "yaml_error": "",
    }
    end = _find_frontmatter_end(text)
    if end == -1:
        return meta

    meta["has_frontmatter"] = True
    block = text[4:end]
    current_list_key: str | None = None
    current_block_key: str | None = None

    for line in block.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        lstripped = line.lstrip(" \t")
        if lstripped.startswith("- ") or lstripped == "-":
            if current_block_key is None and not meta["yaml_error"]:
                meta["yaml_error"] = f"Unexpected YAML list item under inline key: '{line.strip()}'"
            item = lstripped[1:].strip().strip('"').strip("'")
            if not item:
                continue
            if current_list_key == "related":
                meta["related"].append(item)
            elif current_list_key == "tags":
                meta["tags_list"].append(item)
            continue

        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            current_list_key = None
            if not v or v.startswith("#"):
                current_block_key = k
                if k in ("related", "tags"):
                    current_list_key = k
                continue
            current_block_key = None
            v_clean = v.strip('"').strip("'")
            if k == "tags":
                if v.startswith("[") and v.endswith("]"):
                    items = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
                    meta["tags_list"] = items
                    meta["tags"] = ", ".join(items)
                else:
                    meta["tags"] = v_clean
                    meta["tags_list"] = [x.strip() for x in v_clean.split(",") if x.strip()]
            elif k == "related" and v.startswith("[") and v.endswith("]"):
                items = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
                meta["related"] = items
            elif k in ("date", "last_modified", "modified"):
                if not meta["updated"]:
                    meta["updated"] = v_clean
            elif k in meta:
                meta[k] = v_clean
        elif ":" in line and line.startswith((" ", "\t")):
            sub_k, sub_v = line.split(":", 1)
            if not sub_v.strip() or sub_v.strip().startswith("#"):
                current_block_key = sub_k.strip()

    if meta["tags_list"] and not meta["tags"]:
        meta["tags"] = ", ".join(meta["tags_list"])
    return meta


def extract_wikilinks(
    text: str,
    stem_lookup: dict[str, str] | None = None,
    source_rel_path: str | None = None,
) -> list[str]:
    """Extract [[wikilinks]] outside inline/fenced code and normalize to vault rel_path."""
    cleaned_lines = []
    fence_char: str | None = None
    fence_len = 0
    for line in text.splitlines():
        m_fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if m_fence:
            run = m_fence.group(1)
            if fence_char is None:
                fence_char = run[0]
                fence_len = len(run)
                continue
            elif run[0] == fence_char and len(run) >= fence_len:
                fence_char = None
                fence_len = 0
                continue
        if fence_char is None:
            line_no_inline = re.sub(r"`[^`]+`", "", line)
            cleaned_lines.append(line_no_inline)

    cleaned_text = "\n".join(cleaned_lines)
    matches = re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", cleaned_text)
    source_parent = str(Path(source_rel_path).parent) if source_rel_path and "/" in source_rel_path else ""
    out = []
    for m in matches:
        raw = m.strip().rstrip("\\").strip()
        if not raw:
            continue
        if stem_lookup:
            resolved = None
            if source_parent and "/" not in raw:
                local_cand = f"{source_parent}/{raw}"
                resolved = stem_lookup.get(local_cand) or stem_lookup.get(local_cand.removesuffix(".md"))
            if not resolved:
                resolved = stem_lookup.get(raw) or stem_lookup.get(raw.removesuffix(".md"))
            if resolved:
                out.append(resolved)
                continue
        if not raw.endswith(".md"):
            raw += ".md"
        out.append(raw)
    return sorted(set(out))


def split_markdown_chunks(text: str, title: str) -> list[tuple[str, str, int, int, str]]:
    """
    Fence-aware Markdown chunker with H1 > H2 > H3 breadcrumb tracking.
    Returns list of (leaf_heading, heading_path, start_line, end_line, body).
    """
    lines = text.splitlines()
    chunks: list[tuple[str, str, int, int, str]] = []
    current_heading = title or "Overview"
    h_stack: dict[int, str] = {1: current_heading}
    current_path = current_heading
    current_lines: list[str] = []
    start_line = 1

    has_valid_fm = _find_frontmatter_end(text) != -1
    in_frontmatter = False
    fence_char: str | None = None
    fence_len = 0

    for idx, line in enumerate(lines, start=1):
        if idx == 1 and has_valid_fm and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
                start_line = idx + 1
            continue

        m_fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if m_fence:
            run = m_fence.group(1)
            if fence_char is None:
                fence_char = run[0]
                fence_len = len(run)
            elif run[0] == fence_char and len(run) >= fence_len and re.match(r"^\s*(`{3,}|~{3,})\s*$", line):
                fence_char = None
                fence_len = 0
            current_lines.append(line)
            continue

        if fence_char is None:
            m_head = re.match(r"^(#{1,3})\s+(.+)$", line)
            if m_head:
                body = "\n".join(current_lines).strip()
                if body:
                    chunks.append((current_heading, current_path, start_line, idx - 1, body))
                level = len(m_head.group(1))
                heading_text = m_head.group(2).strip()
                current_heading = heading_text
                h_stack[level] = heading_text
                for deeper in range(level + 1, 4):
                    h_stack.pop(deeper, None)
                crumbs = [h_stack[lvl] for lvl in sorted(h_stack.keys()) if h_stack.get(lvl)]
                dedup_crumbs: list[str] = []
                for c in crumbs:
                    if not dedup_crumbs or dedup_crumbs[-1] != c:
                        dedup_crumbs.append(c)
                current_path = " > ".join(dedup_crumbs)
                current_lines = [line]
                start_line = idx
                continue

        current_lines.append(line)

    body = "\n".join(current_lines).strip()
    if body:
        chunks.append((current_heading, current_path, start_line, len(lines), body))
    return chunks


_CACHE_LOCK = threading.RLock()
_ACTIVE_EMBED_URL: str | None = None
_EMBED_CIRCUIT_OPEN: bool = False
_EMBED_QUERY_CACHE: OrderedDict[tuple[str, tuple[str, ...], int, str], tuple[float, ...]] = OrderedDict()
_EMBED_QUERY_CACHE_MAX = 512


def get_embedding(text: str) -> list[float] | None:
    global _ACTIVE_EMBED_URL, _EMBED_CIRCUIT_OPEN
    _pkg: Any = sys.modules.get("sovereign_kb.cli")
    _circuit = getattr(_pkg, "_EMBED_CIRCUIT_OPEN", _EMBED_CIRCUIT_OPEN) if _pkg is not None else _EMBED_CIRCUIT_OPEN
    _active = getattr(_pkg, "_ACTIVE_EMBED_URL", _ACTIVE_EMBED_URL) if _pkg is not None else _ACTIVE_EMBED_URL
    if os.environ.get("KB_NO_EMBED") == "1" or _circuit:
        return None

    endpoints = tuple(get_embed_endpoints())
    cache_key = (EMBED_MODEL, endpoints, id(urllib.request.urlopen), text[:3600])
    with _CACHE_LOCK:
        cached_vec = _EMBED_QUERY_CACHE.get(cache_key)
        if cached_vec is not None:
            _EMBED_QUERY_CACHE.move_to_end(cache_key)
            return list(cached_vec)

    candidates = [_active] if _active else list(endpoints)

    for url in candidates:
        if not url:
            continue
        for limit in (3600, 1800):
            payload = json.dumps({"model": EMBED_MODEL, "prompt": text[:limit]}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    vec = data.get("embedding")
                    if isinstance(vec, list) and len(vec) > 0:
                        _ACTIVE_EMBED_URL = url
                        if _pkg is not None:
                            _pkg._ACTIVE_EMBED_URL = url
                        with _CACHE_LOCK:
                            _EMBED_QUERY_CACHE[cache_key] = tuple(float(x) for x in vec)
                            _EMBED_QUERY_CACHE.move_to_end(cache_key)
                            if len(_EMBED_QUERY_CACHE) > _EMBED_QUERY_CACHE_MAX:
                                _EMBED_QUERY_CACHE.popitem(last=False)
                        return vec
            except urllib.error.HTTPError:
                continue
            except Exception:
                if (_pkg is not None and getattr(_pkg, "_ACTIVE_EMBED_URL", None) == url) or _ACTIVE_EMBED_URL == url:
                    _ACTIVE_EMBED_URL = None
                    if _pkg is not None:
                        _pkg._ACTIVE_EMBED_URL = None
                break
    if not _circuit:
        _EMBED_CIRCUIT_OPEN = True
        if _pkg is not None:
            _pkg._EMBED_CIRCUIT_OPEN = True
        print(
            "[skb] Notice: Local embedding endpoint (Ollama) not reachable; indexing with SQLite FTS5 lexical search (pass --no-embed to silence).",
            file=sys.stderr,
        )
    return None


def cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def compile_fts_query(query: str) -> tuple[str, list[str]]:
    """
    Compile user query into safe FTS5 syntax while preserving hyphenated/dotted identifiers
    (e.g. 'api-gateway' -> '"api gateway"' OR "api" OR "gateway") and Greek/Latin accent folding.
    """
    clauses: list[str] = []
    exact_identifiers: list[str] = []

    for raw_tok in query.split():
        clean_raw = raw_tok.strip(".,;!?()[]{}<>\"'")
        if not clean_raw:
            continue

        if any(sep in clean_raw for sep in ("-", ".", "_", "/", ":")) or (
            any(c.isdigit() for c in clean_raw) and any(c.isalpha() for c in clean_raw)
        ):
            if len(clean_raw) >= 2:
                exact_identifiers.append(clean_raw.lower())

        sub_parts = [p for p in re.findall(r"\w+", clean_raw) if len(p) >= 2 or p.isdigit()]
        if not sub_parts:
            continue

        if len(sub_parts) > 1:
            phrase = " ".join(sub_parts)
            clauses.append(f'"{phrase}"')
            folded_phrase = fold_accents(phrase)
            if folded_phrase != phrase.lower():
                clauses.append(f'"{folded_phrase}"')
            for sp in sub_parts:
                if len(sp) >= 2:
                    clauses.append(f'"{sp}"')
        else:
            tok = sub_parts[0]
            clauses.append(f'"{tok}"')
            folded_tok = fold_accents(tok)
            if folded_tok != tok.lower():
                clauses.append(f'"{folded_tok}"')

    seen: set[str] = set()
    dedup_clauses = []
    for c in clauses:
        if c not in seen:
            seen.add(c)
            dedup_clauses.append(c)

    return " OR ".join(dedup_clauses), exact_identifiers


def _is_vault_no_embed(vault_dir: Path, explicit_flag: bool = False) -> bool:
    if explicit_flag or os.environ.get("KB_NO_EMBED") == "1":
        return True
    vcfg = vault_dir / ".kb" / "vault_config.json"
    if vcfg.is_file():
        try:
            return bool(json.loads(vcfg.read_text(encoding="utf-8")).get("no_embed", False))
        except Exception:
            return False
    return False
