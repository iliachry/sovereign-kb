"""Vault init, incremental sync, HIPS Block-Max WAND sync/search, and LRU vector corpus cache."""

from __future__ import annotations

import argparse
import array
import contextlib
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
from collections import OrderedDict
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .config_db import (
    EMBED_MODEL,
    STARTER_VAULT_DIR,
    _create_tables,
    _get_config_path,
    _reject_if_bundled_starter_vault,
    fold_accents,
    get_db,
    is_doc_vector_eligible,
    resolve_hips_bin,
    resolve_vault_dir,
)
from .markdown_embed import (
    _CACHE_LOCK,
    _is_vault_no_embed,
    build_stem_lookup,
    extract_wikilinks,
    get_embedding,
    list_vault_md_files,
    parse_frontmatter,
    split_markdown_chunks,
    unit_vector_blob,
)


def _build_minimal_starter_files(today: str | None = None) -> dict[str, str]:
    day = today or datetime.date.today().isoformat()
    return {
        "INDEX.md": (
            "---\n"
            "title: Vault Index — Map of Content\n"
            "type: moc\n"
            "tier: 0\n"
            f"updated: {day}\n"
            "tags: [index, moc, navigation]\n"
            "---\n\n"
            "# Sovereign Knowledge Base (`INDEX`)\n\n"
            "## Core Notes\n"
            "- [[00-identity/README-me]] — Executive Profile & Working Context\n"
            "- [[01-now/now]] — Active Priorities & Focus\n"
            f"- [[04-episodic/{day}-genesis]] — Episodic Log\n"
        ),
        "AGENTS.md": (
            "# AGENTS.md — Sovereign Knowledge Base Vault\n\n"
            "## Core Directives for AI Agents\n"
            "1. Read [[00-identity/README-me]] and [[01-now/now]] first.\n"
            '2. Capture project decisions with `skb capture "..."` and search past context with `skb search "..."`.\n'
            "3. Run `skb sync` and `skb verify` after editing Markdown notes.\n"
        ),
        "00-identity/README-me.md": (
            "---\n"
            "title: README-me — Executive Profile & User Manual\n"
            "type: identity\n"
            "tier: 0\n"
            f"updated: {day}\n"
            "tags: [identity, profile, tier-0, summary]\n"
            "related:\n"
            '  - "[[INDEX]]"\n'
            '  - "[[01-now/now]]"\n'
            "---\n\n"
            "# Executive Profile (`Tier-0 Context`)\n\n"
            "- **Name / Handle**: `<Your Name (@handle)>`\n"
            "- **Primary Focus**: Software Engineering · Systems Architecture · Research\n\n"
            "## How I Work\n"
            "Customize this note with your preferred languages, architectural rules, and current context. "
            "Running `skb me` or `skb ask` uses this Tier-0 note as foundational context for local and cloud AI coding agents.\n"
        ),
        "01-now/now.md": (
            "---\n"
            "title: Now — Active Focus\n"
            "type: now\n"
            "tier: 0\n"
            f"updated: {day}\n"
            "tags: [now, focus, priorities]\n"
            "related:\n"
            '  - "[[INDEX]]"\n'
            '  - "[[00-identity/README-me]]"\n'
            "---\n\n"
            "# Now — Current Priorities\n\n"
            "- Track active engineering milestones, architectural decisions, and open questions here.\n"
        ),
        f"04-episodic/{day}-genesis.md": (
            "---\n"
            f"title: {day} — Vault Initialized\n"
            "type: episodic\n"
            f"updated: {day}\n"
            "tags: [episodic, log, genesis]\n"
            "related:\n"
            '  - "[[INDEX]]"\n'
            '  - "[[01-now/now]]"\n'
            "---\n\n"
            f"# {day} — Vault Initialized\n\n"
            "## 09:00\n"
            "- Initialized local Markdown knowledge base with `skb init`.\n"
        ),
    }


MINIMAL_STARTER_FILES: dict[str, str] = _build_minimal_starter_files()


def cmd_init(args) -> None:
    target = Path(args.target_dir or (Path.home() / "sovereign-kb-vault")).expanduser().resolve()
    _reject_if_bundled_starter_vault(target)
    force = getattr(args, "force", False)
    full = getattr(args, "full", False)
    minimal = getattr(args, "minimal", False) or not full
    no_embed = getattr(args, "no_embed", False) or os.environ.get("KB_NO_EMBED") == "1"
    target.mkdir(parents=True, exist_ok=True)

    copied = 0
    if minimal:
        for rel_str, content in sorted(_build_minimal_starter_files().items()):
            dst_path = target / rel_str
            if not dst_path.exists() or force:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                dst_path.write_text(content, encoding="utf-8")
                copied += 1
    else:
        for src_path in sorted(STARTER_VAULT_DIR.rglob("*")):
            rel = src_path.relative_to(STARTER_VAULT_DIR)
            if any(part in (".kb", ".git", "__pycache__") or part.endswith(".pyc") for part in rel.parts):
                continue
            dst_path = target / rel
            if src_path.is_dir():
                dst_path.mkdir(parents=True, exist_ok=True)
            elif not dst_path.exists() or force:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                copied += 1

    gitignore = target / ".gitignore"
    if not gitignore.exists() or force:
        gitignore.write_text(".kb/\n__pycache__/\n*.pyc\n*.mp4\n*.mov\n.DS_Store\n", encoding="utf-8")
    else:
        gi_text = gitignore.read_text(encoding="utf-8", errors="replace")
        gi_lines = {ln.strip() for ln in gi_text.splitlines()}
        if ".kb/" not in gi_lines and ".kb" not in gi_lines:
            gitignore.write_text(gi_text.rstrip() + "\n.kb/\n", encoding="utf-8")

    if not (target / ".git").exists():
        with contextlib.suppress(FileNotFoundError):
            subprocess.run(["git", "-C", str(target), "init", "-b", "main"], capture_output=True, check=False)

    kb_dir = target / ".kb"
    kb_dir.mkdir(parents=True, exist_ok=True)
    if no_embed or minimal:
        vcfg_path = kb_dir / "vault_config.json"
        vcfg_data: dict[str, object] = {}
        if vcfg_path.is_file():
            try:
                vcfg_data = json.loads(vcfg_path.read_text(encoding="utf-8"))
            except Exception:
                vcfg_data = {}
        if no_embed:
            vcfg_data["no_embed"] = True
        if minimal:
            vcfg_data["minimal"] = True
        vcfg_path.write_text(json.dumps(vcfg_data, indent=2), encoding="utf-8")

    cfg_path = _get_config_path()
    if getattr(args, "save_default", False) or (not cfg_path.is_file() and "PYTEST_CURRENT_TEST" not in os.environ):
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps({"vault_dir": str(target)}, indent=2), encoding="utf-8")
    elif cfg_path.is_file():
        try:
            cur_default = (
                Path(json.loads(cfg_path.read_text(encoding="utf-8")).get("vault_dir", "")).expanduser().resolve()
            )
            if cur_default != target:
                print(
                    f"[sovereign-kb init] Note: Default vault remains '{cur_default}'. "
                    f"Pass '--vault {target}' or '--save-default' to switch."
                )
        except Exception:
            pass

    print(f"[sovereign-kb init] Initialized vault at {target} ({copied} starter files written).")
    sync_args = argparse.Namespace(vault=str(target), force=False, no_embed=no_embed)
    cmd_sync(sync_args)


def _verify_fts_index(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('integrity-check', 1)")
    except sqlite3.OperationalError:
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')")
    c_cnt = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    f_cnt = int(conn.execute("SELECT COUNT(*) FROM chunks_fts_docsize").fetchone()[0])
    if c_cnt != f_cnt:
        raise sqlite3.DatabaseError(f"chunks_fts_docsize count ({f_cnt}) != chunks count ({c_cnt})")
    if c_cnt > 0:
        d_cnt = int(conn.execute("SELECT COUNT(*) FROM chunks_fts_data").fetchone()[0])
        if d_cnt < 2:
            raise sqlite3.DatabaseError(f"chunks_fts_data has only {d_cnt} row(s) for {c_cnt} chunk(s)")
        for cid, ctext in conn.execute("SELECT id, content FROM chunks").fetchall():
            folded_probe = fold_accents(str(ctext or ""))
            m_tok = re.search(r"(?<![\w])(?!(?:AND|OR|NOT|NEAR)(?![\w]))[A-Za-z0-9]{3,}(?![\w])", folded_probe)
            if m_tok:
                hit = conn.execute(
                    "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid = ?",
                    (f'"{m_tok.group(0)}"', int(cid)),
                ).fetchone()
                if hit is None:
                    raise sqlite3.DatabaseError(f"chunks_fts MATCH failed for chunk rowid={cid}")


def cmd_sync(args) -> None:
    vault_dir = resolve_vault_dir(getattr(args, "vault", None))
    if getattr(args, "no_embed", False) and getattr(args, "persist_no_embed", False):
        kb_dir = vault_dir / ".kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        vcfg_path = kb_dir / "vault_config.json"
        vcfg_data: dict[str, object] = {}
        if vcfg_path.is_file():
            try:
                vcfg_data = json.loads(vcfg_path.read_text(encoding="utf-8"))
            except Exception:
                vcfg_data = {}
        vcfg_data["no_embed"] = True
        vcfg_path.write_text(json.dumps(vcfg_data, indent=2), encoding="utf-8")

    conn = get_db(vault_dir)
    md_files = list_vault_md_files(vault_dir)
    stem_lookup = build_stem_lookup(vault_dir, md_files)
    updated_count = 0
    embedded_count = 0
    no_embed = _is_vault_no_embed(vault_dir, getattr(args, "no_embed", False))

    existing_docs = {row["rel_path"]: row["mtime"] for row in conn.execute("SELECT rel_path, mtime FROM documents")}
    docs_missing_vecs: set[str] = set()
    if not no_embed:
        missing_rows = [
            row["rel_path"] for row in conn.execute("SELECT DISTINCT rel_path FROM chunks WHERE embedding IS NULL")
        ]
        if missing_rows and get_embedding("ping") is not None:
            docs_missing_vecs = set(missing_rows)

    temp_vec_cache: dict[tuple[str, str], Any] = {}
    try:
        for r in conn.execute("SELECT rel_path, sha, embedding FROM temp._vec_cache"):
            temp_vec_cache[(r["rel_path"], r["sha"])] = r["embedding"]
    except sqlite3.OperationalError:
        pass

    seen_paths: set[str] = set()

    try:
        if getattr(args, "force", False):
            raise sqlite3.DatabaseError("force rebuild")
        _verify_fts_index(conn)
    except sqlite3.DatabaseError:
        with contextlib.suppress(sqlite3.DatabaseError):
            conn.rollback()
        conn.executescript("DROP TABLE IF EXISTS chunks_fts;")
        _create_tables(conn)
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        conn.commit()

    for path in md_files:
        rel_path = str(path.relative_to(vault_dir))
        seen_paths.add(rel_path)
        mtime = path.stat().st_mtime

        if (
            not getattr(args, "force", False)
            and rel_path in existing_docs
            and abs(existing_docs[rel_path] - mtime) < 1e-4
            and rel_path not in docs_missing_vecs
        ):
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        meta = parse_frontmatter(text)
        title = meta["title"] or path.stem
        doc_type = meta["type"] or "note"
        tags = meta["tags"]

        for r in conn.execute(
            "SELECT content, embedding FROM chunks WHERE rel_path = ? AND embedding IS NOT NULL",
            (rel_path,),
        ):
            raw_emb = r["embedding"]
            if raw_emb is None:
                continue
            blob_emb = unit_vector_blob(raw_emb)
            if blob_emb:
                sha = hashlib.sha256(r["content"].encode("utf-8")).hexdigest()
                temp_vec_cache[(rel_path, sha)] = blob_emb

        old_ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE rel_path = ?", (rel_path,))]
        try:
            for cid in old_ids:
                conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
        except sqlite3.DatabaseError:
            conn.commit()
            conn.executescript("DROP TABLE IF EXISTS chunks_fts;")
            _create_tables(conn)
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        conn.execute("DELETE FROM chunks WHERE rel_path = ?", (rel_path,))
        conn.execute("DELETE FROM links WHERE source_path = ?", (rel_path,))

        conn.execute(
            """
            INSERT OR REPLACE INTO documents (rel_path, title, doc_type, tier, updated, tags, mtime, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (rel_path, title, doc_type, str(meta["tier"]), str(meta["updated"]), tags, mtime, text),
        )

        for target in extract_wikilinks(text, stem_lookup, rel_path):
            if target != rel_path:
                conn.execute(
                    "INSERT OR IGNORE INTO links (source_path, target_path) VALUES (?, ?)",
                    (rel_path, target),
                )

        vec_eligible = 1 if is_doc_vector_eligible(rel_path, str(meta["updated"]), max_episodic_days=60) else 0

        for heading, heading_path, start_line, end_line, chunk_body in split_markdown_chunks(text, title):
            sha = hashlib.sha256(chunk_body.encode("utf-8")).hexdigest()
            vec_blob: bytes | None = None
            if vec_eligible:
                cached_vec = temp_vec_cache.get((rel_path, sha)) if not getattr(args, "force", False) else None
                if cached_vec is not None:
                    vec_blob = unit_vector_blob(cached_vec)
                if not vec_blob and not no_embed:
                    embed_input = f"{title} — {heading_path}\nTags: {tags}\n{chunk_body}"
                    vec_blob = unit_vector_blob(get_embedding(embed_input))
                    if vec_blob:
                        embedded_count += 1
                elif vec_blob:
                    embedded_count += 1

            folded_body = fold_accents(f"{title} {heading_path} {tags} {chunk_body}")
            cur = conn.execute(
                """
                INSERT INTO chunks (rel_path, title, doc_type, tags, heading, heading_path, start_line, end_line, content, folded_content, is_vector_eligible, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rel_path,
                    title,
                    doc_type,
                    tags,
                    heading,
                    heading_path,
                    start_line,
                    end_line,
                    chunk_body,
                    folded_body,
                    vec_eligible,
                    vec_blob,
                ),
            )
            rowid = cur.lastrowid
            conn.execute(
                """
                INSERT INTO chunks_fts (rowid, rel_path, title, doc_type, tags, heading_path, content, folded_content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rowid, rel_path, title, doc_type, tags, heading_path, chunk_body, folded_body),
            )
        updated_count += 1

    removed_paths = set(existing_docs.keys()) - seen_paths
    for old_path in removed_paths:
        old_ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE rel_path = ?", (old_path,))]
        for cid in old_ids:
            conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
        conn.execute("DELETE FROM chunks WHERE rel_path = ?", (old_path,))
        conn.execute("DELETE FROM links WHERE source_path = ?", (old_path,))
        conn.execute("DELETE FROM documents WHERE rel_path = ?", (old_path,))

    if updated_count > 0 or removed_paths:
        conn.execute("DELETE FROM links")
        for doc_row in conn.execute("SELECT rel_path, content FROM documents"):
            src_rel = doc_row["rel_path"]
            for target in extract_wikilinks(doc_row["content"] or "", stem_lookup, src_rel):
                if target != src_rel:
                    conn.execute(
                        "INSERT OR IGNORE INTO links (source_path, target_path) VALUES (?, ?)",
                        (src_rel, target),
                    )

    conn.commit()
    total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    total_links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    hips_marker = hips_idx_dir / ".hips-sync-ok"
    db_file = vault_dir / ".kb" / "index.db"
    hips_up_to_date = False
    if updated_count == 0 and not removed_paths and not getattr(args, "force", False) and hips_marker.is_file():
        with contextlib.suppress(OSError):
            hips_up_to_date = (
                hips_marker.read_text(encoding="utf-8", errors="ignore").strip() == str(total_chunks)
                and db_file.stat().st_mtime_ns <= hips_marker.stat().st_mtime_ns
            )
    if not hips_up_to_date:
        with _CACHE_LOCK:
            _VAULT_CORPUS_CACHE.clear()
        hips_ok, hips_chunks = sync_hips_index(conn, vault_dir)
    else:
        hips_ok = bool(resolve_hips_bin()) and hips_idx_dir.is_dir()
        hips_chunks = total_chunks if hips_ok else 0
    conn.close()
    if not getattr(args, "quiet", False):
        hips_msg = f", {hips_chunks} chunks in hips Block-Max WAND index" if hips_ok else ""
        vec_msg = (
            "FTS5 lexical index"
            if (no_embed and embedded_count == 0)
            else f"{embedded_count} chunks with {EMBED_MODEL} vectors"
        )
        print(
            f"[sovereign-kb sync] Updated {updated_count} file(s) ({vec_msg}{hips_msg}). "
            f"Vault total: {total_docs} notes, {total_chunks} chunks, {total_links} graph links."
        )


def _is_index_stale(conn: sqlite3.Connection, vault_dir: Path, md_files: list[Path] | None = None) -> bool:
    """Return True if any vault Markdown file was added, deleted, or modified since last SQLite sync."""
    if md_files is None:
        md_files = list_vault_md_files(vault_dir)
    existing_mtimes = {row["rel_path"]: row["mtime"] for row in conn.execute("SELECT rel_path, mtime FROM documents")}
    return len(existing_mtimes) != len(md_files) or any(
        str(p.relative_to(vault_dir)) not in existing_mtimes
        or abs(existing_mtimes[str(p.relative_to(vault_dir))] - p.stat().st_mtime) >= 1e-4
        for p in md_files
    )


def sync_hips_index(conn: sqlite3.Connection, vault_dir: Path) -> tuple[bool, int]:
    """Export vault Markdown chunks to JSONL and atomically build the `hips` Block-Max WAND index."""
    hips_bin = resolve_hips_bin()
    if not hips_bin:
        return False, 0

    rows = conn.execute(
        """
        SELECT c.rel_path, c.heading_path, c.start_line, c.end_line, c.content, c.folded_content,
               COALESCE(d.title, c.rel_path) AS title, COALESCE(d.doc_type, 'note') AS doc_type, COALESCE(d.tags, '') AS tags
        FROM chunks c
        LEFT JOIN documents d ON d.rel_path = c.rel_path
        ORDER BY c.rel_path, c.start_line
        """
    ).fetchall()
    if not rows:
        return False, 0

    kb_dir = vault_dir / ".kb"
    kb_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = kb_dir / "hips-chunks.jsonl"
    hips_idx_dir = kb_dir / "hips-index"
    tmp_jsonl = kb_dir / f"hips-chunks.jsonl.tmp.{os.getpid()}"
    tmp_idx_dir = kb_dir / f"hips-index.tmp.{os.getpid()}"
    old_idx_dir = kb_dir / f"hips-index.old.{os.getpid()}"

    try:
        with tmp_jsonl.open("w", encoding="utf-8") as f:
            for r in rows:
                chunk_id = f"{r['rel_path']}:{r['start_line']}-{r['end_line']}"
                title_str = f"{r['title']} — {r['heading_path']} [{r['doc_type']}] ({r['tags']})"
                body_str = f"{r['content']}\n{r['folded_content']}"
                f.write(json.dumps({"id": chunk_id, "title": title_str, "body": body_str}, ensure_ascii=False) + "\n")
        os.replace(tmp_jsonl, jsonl_path)

        if tmp_idx_dir.exists():
            shutil.rmtree(tmp_idx_dir, ignore_errors=True)

        proc = subprocess.run(
            [
                str(hips_bin),
                "index",
                "--input",
                str(jsonl_path),
                "--out",
                str(tmp_idx_dir),
                "--code",
                "--title-weight",
                "3",
                "--reorder",
                "path",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not tmp_idx_dir.is_dir():
            shutil.rmtree(tmp_idx_dir, ignore_errors=True)
            (hips_idx_dir / ".hips-sync-ok").unlink(missing_ok=True)
            return False, 0

        (tmp_idx_dir / ".hips-sync-ok").write_text(str(len(rows)), encoding="utf-8")
        if old_idx_dir.exists():
            shutil.rmtree(old_idx_dir, ignore_errors=True)
        if hips_idx_dir.exists():
            os.replace(hips_idx_dir, old_idx_dir)
        os.replace(tmp_idx_dir, hips_idx_dir)
        shutil.rmtree(old_idx_dir, ignore_errors=True)
        with _CACHE_LOCK:
            _HIPS_QUERY_CACHE.clear()
            _VAULT_CORPUS_CACHE.clear()
        return hips_idx_dir.is_dir(), len(rows)
    finally:
        tmp_jsonl.unlink(missing_ok=True)
        if tmp_idx_dir.exists():
            shutil.rmtree(tmp_idx_dir, ignore_errors=True)
        if old_idx_dir.exists():
            shutil.rmtree(old_idx_dir, ignore_errors=True)


_HIPS_QUERY_CACHE: OrderedDict[
    tuple[str, str, int, float], tuple[MappingProxyType[Any, Any], MappingProxyType[Any, Any]]
] = OrderedDict()
_HIPS_QUERY_CACHE_MAX = 512


def hips_retrieve(
    vault_dir: Path,
    query: str,
    limit: int = 35,
) -> tuple[
    dict[tuple[str, int, int], int] | MappingProxyType[Any, Any],
    dict[tuple[str, int, int], float] | MappingProxyType[Any, Any],
]:
    """Run sub-millisecond Block-Max WAND search via `hips` over `.kb/hips-index`."""
    hips_bin = resolve_hips_bin()
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    if not hips_bin or not hips_idx_dir.is_dir():
        return {}, {}

    idx_mtime = hips_idx_dir.stat().st_mtime
    cache_key = (str(hips_idx_dir), query, limit, idx_mtime)
    with _CACHE_LOCK:
        cached_hips = _HIPS_QUERY_CACHE.get(cache_key)
        if cached_hips is not None:
            _HIPS_QUERY_CACHE.move_to_end(cache_key)
            return cached_hips

    folded_q = fold_accents(query)
    hips_query = query if folded_q == query.casefold() else f"{query} {folded_q}"
    proc = subprocess.run(
        [
            str(hips_bin),
            "search",
            "--index",
            str(hips_idx_dir),
            "--query",
            hips_query,
            "--top-k",
            str(limit),
            "--json",
            "--lexical",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {}, {}

    ranks: dict[tuple[str, int, int], int] = {}
    scores: dict[tuple[str, int, int], float] = {}
    rank_idx = 1
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            key = (str(obj.get("path", "")), int(obj.get("start", 0)), int(obj.get("end", 0)))
            if key not in ranks:
                ranks[key] = rank_idx
                scores[key] = float(obj.get("score", 0.0))
                rank_idx += 1
        except Exception:
            continue
    res = (MappingProxyType(ranks), MappingProxyType(scores))
    with _CACHE_LOCK:
        _HIPS_QUERY_CACHE[cache_key] = res
        _HIPS_QUERY_CACHE.move_to_end(cache_key)
        if len(_HIPS_QUERY_CACHE) > _HIPS_QUERY_CACHE_MAX:
            _HIPS_QUERY_CACHE.popitem(last=False)
    return res


_VAULT_CORPUS_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_VAULT_CORPUS_CACHE_MAX = 4
_VEC_RANK_CACHE_MAX = 256


def _db_state_token(conn: sqlite3.Connection, vault_dir: Path) -> tuple[Any, ...] | None:
    """Return a cache-invalidation key for clean committed file-backed DB connections, or None to bypass cache."""
    if conn.in_transaction or conn.total_changes > 0:
        return None
    main_db_file = ""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main":
            main_db_file = str(row[2] or "")
            break
    if not main_db_file:
        return None
    db_path = Path(main_db_file)
    wal_path = Path(f"{main_db_file}-wal")
    try:
        st = db_path.stat()
        db_sig = (st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    try:
        wst = wal_path.stat()
        wal_sig = (wst.st_mtime_ns, wst.st_size)
    except OSError:
        wal_sig = (0, 0)
    schema_ver = conn.execute("PRAGMA schema_version").fetchone()[0]
    data_ver = conn.execute("PRAGMA data_version").fetchone()[0]
    return (main_db_file, db_sig, wal_sig, schema_ver, data_ver)


def _load_corpus_vectors(
    conn: sqlite3.Connection,
) -> tuple[list[tuple[int, str, array.array[Any], float]], list[tuple[int, str]]]:
    vec_entries: list[tuple[int, str, array.array[Any], float]] = []
    zero_vec_entries: list[tuple[int, str]] = []
    for r in conn.execute("SELECT id, rel_path, embedding FROM chunks WHERE embedding IS NOT NULL"):
        raw_emb = r["embedding"]
        if not raw_emb:
            continue
        try:
            if isinstance(raw_emb, (bytes, bytearray)):
                if len(raw_emb) % 4 != 0:
                    continue
                c_vec = array.array("f", raw_emb)
            elif isinstance(raw_emb, str):
                parsed = json.loads(raw_emb)
                if not isinstance(parsed, list):
                    continue
                c_vec = array.array("f", parsed)
            else:
                continue
            c_norm = math.sqrt(sum(x * x for x in c_vec))
        except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
            continue
        if not math.isfinite(c_norm):
            continue
        if c_norm > 0.0:
            vec_entries.append((r["id"], r["rel_path"], c_vec, c_norm))
        else:
            zero_vec_entries.append((r["id"], r["rel_path"]))
    return vec_entries, zero_vec_entries


def _build_vault_corpus(conn: sqlite3.Connection) -> dict[str, Any]:
    doc_meta = {
        r["rel_path"]: dict(r)
        for r in conn.execute("SELECT rel_path, title, doc_type, tier, updated, tags FROM documents")
    }

    chunks: dict[int, dict[str, Any]] = {}
    doc_to_cids: dict[str, list[int]] = {}
    hkey_to_cids: dict[tuple[str, int, int], list[int]] = {}
    for r in conn.execute("SELECT id, rel_path, heading, heading_path, start_line, end_line, content FROM chunks"):
        c = dict(r)
        cid = c["id"]
        rp = c["rel_path"]
        hkey = (rp, int(c["start_line"]), int(c["end_line"]))
        c["_hkey"] = hkey
        c["_lower_hay"] = f"{rp} {c['heading_path']} {c['content']}".lower()
        c["_folded_hay"] = None
        chunks[cid] = c
        doc_to_cids.setdefault(rp, []).append(cid)
        hkey_to_cids.setdefault(hkey, []).append(cid)

    adj_raw: dict[str, set[str]] = {}
    for r in conn.execute("SELECT source_path, target_path FROM links"):
        src, dst = r["source_path"], r["target_path"]
        adj_raw.setdefault(src, set()).add(dst)
        adj_raw.setdefault(dst, set()).add(src)
    adj_links = {k: sorted(v) for k, v in adj_raw.items()}

    return {
        "doc_meta": doc_meta,
        "chunks": chunks,
        "doc_to_cids": doc_to_cids,
        "hkey_to_cids": hkey_to_cids,
        "vec_loaded": False,
        "vec_entries": [],
        "zero_vec_entries": [],
        "adj_links": adj_links,
        "fts_cache": {},
        "exact_id_cache": {},
        "vec_rank_cache": OrderedDict(),
    }


def _get_vault_corpus(conn: sqlite3.Connection, vault_dir: Path) -> dict[str, Any]:
    """Load and cache immutable per-DB-state vault corpus structures for sub-second hybrid retrieval."""
    token = _db_state_token(conn, vault_dir)
    if token is None:
        return _build_vault_corpus(conn)

    with _CACHE_LOCK:
        cached = _VAULT_CORPUS_CACHE.get(token)
        if cached is not None:
            _VAULT_CORPUS_CACHE.move_to_end(token)
            return cached

    corpus = _build_vault_corpus(conn)
    main_db_file = token[0]
    with _CACHE_LOCK:
        existing = _VAULT_CORPUS_CACHE.get(token)
        if existing is not None:
            _VAULT_CORPUS_CACHE.move_to_end(token)
            return existing
        for old_tok in [k for k in _VAULT_CORPUS_CACHE if k[0] == main_db_file]:
            _VAULT_CORPUS_CACHE.pop(old_tok, None)
        _VAULT_CORPUS_CACHE[token] = corpus
        while len(_VAULT_CORPUS_CACHE) > _VAULT_CORPUS_CACHE_MAX:
            _VAULT_CORPUS_CACHE.popitem(last=False)
    return corpus


def _ensure_corpus_vectors(conn: sqlite3.Connection, corpus: dict[str, Any]) -> None:
    if corpus["vec_loaded"]:
        return
    vec_entries, zero_vec_entries = _load_corpus_vectors(conn)
    with _CACHE_LOCK:
        if not corpus["vec_loaded"]:
            corpus["vec_entries"] = vec_entries
            corpus["zero_vec_entries"] = zero_vec_entries
            corpus["vec_loaded"] = True
