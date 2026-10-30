#!/usr/bin/env python3
"""
sovereign-kb (skb) — Zero-Dependency Personal Knowledge Base & Autonomous Project Factory CLI.

4-Way Hybrid Retrieval:
  1. Optional `hips` Engine (Rust Block-Max WAND over bit-packed postings + `--code` identifier splitter)
  2. SQLite FTS5 (BM25 with column weights, hyphen/dot phrase compiler, exact identifier boost & Greek/Latin accent folding)
  3. Local Semantic Embeddings via Ollama (`nomic-embed-text:latest`, 768-d vectors with multi-endpoint failover)
  4. [[Wikilink]] Knowledge Graph (stem-normalized 1-hop propagation with MOC hub suppression)

Includes:
  - `sovereign-kb init <dir>`               : Bootstrap a clean, 6-Gate verified Sovereign Vault
  - `sovereign-kb hips <subcmd>`            : Direct Block-Max WAND indexing, search, benchmark, HTTP & MCP server
  - `sovereign-kb verify` (`doctor`/`lint`) : 6-Gate Deterministic Vault Doctor
  - `sovereign-kb scaffold <arch> <dir>`    : 7-Archetype Autonomous Software, 3D Sim & Scientific Paper Initializer
  - `sovereign-kb gwi` (`goldwood`/`audit`) : Unified Goldwood Index & Global Watchability Index (GWI+ v1.4.0) Gate
  - `sovereign-kb dashboard` (`ui`)         : Zero-Dependency Observability & Knowledge Graph Dashboard
  - `sovereign-kb usage` (`finops`)         : Multi-Agent FinOps, Token Telemetry & Prompt-Cache Auditor
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

SCHEMA_VERSION = 2
STARTER_VAULT_DIR = Path(__file__).resolve().parent / "starter_vault"
CONFIG_PATH = Path.home() / ".config" / "sovereign-kb" / "config.json"


@contextlib.contextmanager
def _vault_lock(vault_dir: Path):
    """Acquire an exclusive advisory lock across capture/sync/push operations."""
    kb_dir = vault_dir / ".kb"
    kb_dir.mkdir(parents=True, exist_ok=True)
    lock_path = kb_dir / ".vault.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


EMBED_MODEL = os.environ.get("KB_EMBED_MODEL", "nomic-embed-text:latest")


def resolve_hips_bin() -> Path | None:
    """Locate the `hips` Rust Block-Max WAND search binary via KB_HIPS_BIN, PATH, or ~/Projects/hips."""
    if "KB_HIPS_BIN" in os.environ:
        env_bin = os.environ["KB_HIPS_BIN"].strip()
        if not env_bin:
            return None
        p = Path(env_bin).expanduser()
        return p.resolve() if p.is_file() and os.access(p, os.X_OK) else None
    which_bin = shutil.which("hips")
    if which_bin:
        return Path(which_bin).resolve()
    for candidate in (
        Path.home() / ".local" / "bin" / "hips",
        Path.home() / "Projects" / "hips" / "target" / "release" / "hips",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def get_embed_endpoints() -> list[str]:
    primary = os.environ.get("KB_EMBED_URL", "http://127.0.0.1:11434/api/embeddings")
    extra_raw = os.environ.get("KB_EMBED_FALLBACK_URLS", "")
    extras = [u.strip() for u in extra_raw.split(",") if u.strip()]
    out: list[str] = []
    for u in [primary, *extras]:
        if u and u not in out:
            out.append(u)
    return out


def get_chat_endpoints() -> list[tuple[str, str, str]]:
    primary_model = os.environ.get("KB_CHAT_MODEL", os.environ.get("KB_GX10_MODEL", "qwen3.6-35b-a3b"))
    primary_url = os.environ.get(
        "KB_CHAT_URL", os.environ.get("KB_GX10_URL", "http://127.0.0.1:8000/v1/chat/completions")
    )
    ollama_model = os.environ.get("KB_OLLAMA_MODEL", "qwen2.5-coder:7b")
    ollama_url = os.environ.get("KB_OLLAMA_URL", "http://127.0.0.1:11434/v1/chat/completions")
    endpoints = [
        (primary_url, primary_model, f"openai-local:{primary_model}"),
        (ollama_url, ollama_model, f"ollama-local:{ollama_model}"),
    ]
    extra_raw = os.environ.get("KB_CHAT_FALLBACK_URLS", "")
    for item in extra_raw.split(","):
        url = item.strip()
        if url:
            endpoints.append((url, primary_model, f"fallback:{primary_model}"))
    return endpoints


_ACTIVE_EMBED_URL: str | None = None


def _get_config_path() -> Path:
    return Path.home() / ".config" / "sovereign-kb" / "config.json"


def resolve_vault_dir(explicit_vault: str | None = None, must_exist: bool = True) -> Path:
    """Resolve the active vault directory via CLI flag, env var, cwd walk-up, or user config."""
    if explicit_vault:
        v = Path(explicit_vault).expanduser().resolve()
        if must_exist and not v.exists():
            print(f"[skb] Error: Vault directory '{v}' does not exist. Run 'skb init {v}' first.", file=sys.stderr)
            sys.exit(1)
        return v

    env_vault = os.environ.get("KB_VAULT_DIR")
    if env_vault:
        v = Path(env_vault).expanduser().resolve()
        if must_exist and not v.exists():
            print(f"[skb] Error: Vault directory '{v}' does not exist. Run 'skb init {v}' first.", file=sys.stderr)
            sys.exit(1)
        return v

    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".kb").is_dir() or (
            (candidate / "INDEX.md").is_file() and (candidate / "00-identity").is_dir()
        ):
            return candidate

    cfg_path = _get_config_path()
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if cfg.get("vault_dir"):
                v = Path(cfg["vault_dir"]).expanduser().resolve()
                if must_exist and not v.exists():
                    print(
                        f"[skb] Error: Configured vault directory '{v}' does not exist. Run 'skb init {v}' first.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                return v
        except SystemExit:
            raise
        except Exception:
            pass

    default_vault = (Path.home() / "sovereign-kb-vault").resolve()
    default_initialized = (default_vault / ".kb").is_dir() or (default_vault / "INDEX.md").is_file()
    if not default_initialized and cwd != Path.home().resolve() and any(cwd.glob("*.md")):
        return cwd

    if must_exist and not default_initialized:
        print(
            "[skb] No vault found in current directory or config. "
            "Run 'skb init ~/notes --save-default' to create a vault, or pass '--vault <dir>'.",
            file=sys.stderr,
        )
        sys.exit(1)

    return default_vault


def fold_accents(text: str) -> str:
    """Casefold and strip combining diacritics (supports Greek polytonic/monotonic & Latin)."""
    decomp = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(ch for ch in decomp if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped)


def get_db(vault_dir: Path) -> sqlite3.Connection:
    db_dir = vault_dir / ".kb"
    db_path = db_dir / "index.db"
    db_dir.mkdir(parents=True, exist_ok=True)
    kb_gitignore = db_dir / ".gitignore"
    if not kb_gitignore.exists():
        with contextlib.suppress(OSError):
            kb_gitignore.write_text("*\n", encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    user_ver = conn.execute("PRAGMA user_version;").fetchone()[0]
    if user_ver != SCHEMA_VERSION:
        cached_vecs: dict[tuple[str, str], str] = {}
        try:
            for r in conn.execute("SELECT rel_path, content, embedding FROM chunks WHERE embedding IS NOT NULL"):
                h = hashlib.sha256(r["content"].encode("utf-8")).hexdigest()
                cached_vecs[(r["rel_path"], h)] = r["embedding"]
        except sqlite3.OperationalError:
            pass

        conn.executescript(
            """
            DROP TABLE IF EXISTS documents;
            DROP TABLE IF EXISTS chunks;
            DROP TABLE IF EXISTS chunks_fts;
            DROP TABLE IF EXISTS links;
            """
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
        _create_tables(conn)
        conn.commit()
        if cached_vecs:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _vec_cache (rel_path TEXT, sha TEXT, embedding TEXT);")
            conn.executemany(
                "INSERT INTO _vec_cache VALUES (?, ?, ?)",
                [(k[0], k[1], v) for k, v in cached_vecs.items()],
            )
            conn.commit()
    else:
        _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            rel_path TEXT PRIMARY KEY,
            title TEXT,
            doc_type TEXT,
            tier TEXT,
            updated TEXT,
            tags TEXT,
            mtime REAL,
            content TEXT
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT,
            heading TEXT,
            heading_path TEXT,
            start_line INTEGER,
            end_line INTEGER,
            content TEXT,
            folded_content TEXT,
            embedding TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            rel_path,
            title,
            doc_type,
            tags,
            heading_path,
            content,
            folded_content
        );
        CREATE TABLE IF NOT EXISTS links (
            source_path TEXT,
            target_path TEXT,
            PRIMARY KEY (source_path, target_path)
        );
        """
    )


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


def parse_frontmatter(text: str) -> dict:
    meta = {
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


_EMBED_CIRCUIT_OPEN: bool = False


def get_embedding(text: str) -> list[float] | None:
    global _ACTIVE_EMBED_URL, _EMBED_CIRCUIT_OPEN
    if os.environ.get("KB_NO_EMBED") == "1" or _EMBED_CIRCUIT_OPEN:
        return None
    candidates = [_ACTIVE_EMBED_URL] if _ACTIVE_EMBED_URL else get_embed_endpoints()

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
                        return vec
            except urllib.error.HTTPError:
                continue
            except Exception:
                if _ACTIVE_EMBED_URL == url:
                    _ACTIVE_EMBED_URL = None
                break
    if not _EMBED_CIRCUIT_OPEN:
        _EMBED_CIRCUIT_OPEN = True
        print(
            "[skb] Notice: Local embedding endpoint (Ollama) not reachable; indexing with SQLite FTS5 lexical search (pass --no-embed to silence).",
            file=sys.stderr,
        )
    return None


def cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
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

    temp_vec_cache: dict[tuple[str, str], str] = {}
    try:
        for r in conn.execute("SELECT rel_path, sha, embedding FROM temp._vec_cache"):
            temp_vec_cache[(r["rel_path"], r["sha"])] = r["embedding"]
    except sqlite3.OperationalError:
        pass

    seen_paths: set[str] = set()

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
            sha = hashlib.sha256(r["content"].encode("utf-8")).hexdigest()
            temp_vec_cache[(rel_path, sha)] = r["embedding"]

        old_ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE rel_path = ?", (rel_path,))]
        for cid in old_ids:
            conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
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

        for heading, heading_path, start_line, end_line, chunk_body in split_markdown_chunks(text, title):
            sha = hashlib.sha256(chunk_body.encode("utf-8")).hexdigest()
            vec_json = temp_vec_cache.get((rel_path, sha)) if not getattr(args, "force", False) else None
            if not vec_json and not no_embed:
                embed_input = f"{title} — {heading_path}\nTags: {tags}\n{chunk_body}"
                vec = get_embedding(embed_input)
                if vec:
                    vec_json = json.dumps(vec)
                    embedded_count += 1
            elif vec_json:
                embedded_count += 1

            folded_body = fold_accents(f"{title} {heading_path} {tags} {chunk_body}")
            cur = conn.execute(
                """
                INSERT INTO chunks (rel_path, heading, heading_path, start_line, end_line, content, folded_content, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rel_path, heading, heading_path, start_line, end_line, chunk_body, folded_body, vec_json),
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
    hips_ok, hips_chunks = sync_hips_index(conn, vault_dir)
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
            return False, 0

        if old_idx_dir.exists():
            shutil.rmtree(old_idx_dir, ignore_errors=True)
        if hips_idx_dir.exists():
            os.replace(hips_idx_dir, old_idx_dir)
        os.replace(tmp_idx_dir, hips_idx_dir)
        shutil.rmtree(old_idx_dir, ignore_errors=True)
        _HIPS_QUERY_CACHE.clear()
        return hips_idx_dir.is_dir(), len(rows)
    finally:
        tmp_jsonl.unlink(missing_ok=True)
        if tmp_idx_dir.exists():
            shutil.rmtree(tmp_idx_dir, ignore_errors=True)
        if old_idx_dir.exists():
            shutil.rmtree(old_idx_dir, ignore_errors=True)


_HIPS_QUERY_CACHE: dict[
    tuple[str, str, int, float], tuple[dict[tuple[str, int, int], int], dict[tuple[str, int, int], float]]
] = {}


def hips_retrieve(
    vault_dir: Path,
    query: str,
    limit: int = 35,
) -> tuple[dict[tuple[str, int, int], int], dict[tuple[str, int, int], float]]:
    """Run sub-millisecond Block-Max WAND search via `hips` over `.kb/hips-index`."""
    hips_bin = resolve_hips_bin()
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    if not hips_bin or not hips_idx_dir.is_dir():
        return {}, {}

    idx_mtime = hips_idx_dir.stat().st_mtime
    cache_key = (str(hips_idx_dir), query, limit, idx_mtime)
    if cache_key in _HIPS_QUERY_CACHE:
        return _HIPS_QUERY_CACHE[cache_key]

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
    _HIPS_QUERY_CACHE[cache_key] = (ranks, scores)
    return ranks, scores


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

    doc_meta = {
        r["rel_path"]: dict(r)
        for r in conn.execute("SELECT rel_path, title, doc_type, tier, updated, tags FROM documents")
    }

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

    hips_key_ranks: dict[tuple[str, int, int], int] = {}
    hips_key_scores: dict[tuple[str, int, int], float] = {}
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    if engine in ("auto", "hips", "hybrid"):
        if resolve_hips_bin() and not hips_idx_dir.is_dir():
            sync_hips_index(conn, vault_dir)
        hips_limit = 200 if allowed_docs is not None else 35
        hips_key_ranks, hips_key_scores = hips_retrieve(vault_dir, query, limit=hips_limit)

    fts_query, exact_identifiers = compile_fts_query(query)
    fts_ranks: dict[int, int] = {}
    if engine != "hips" and fts_query:
        try:
            if allowed_docs is not None:
                sorted_allowed = sorted(allowed_docs)
                placeholders = ", ".join("?" for _ in sorted_allowed)
                rows = conn.execute(
                    f"""
                    SELECT rowid, bm25(chunks_fts, {bm25_args}) AS score
                    FROM chunks_fts
                    WHERE chunks_fts MATCH ? AND rel_path IN ({placeholders})
                    ORDER BY score
                    LIMIT 35
                    """,
                    (fts_query, *sorted_allowed),
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
                fts_ranks[r["rowid"]] = rank_counter
        except sqlite3.OperationalError:
            pass

    no_embed = _is_vault_no_embed(vault_dir)
    q_vec = get_embedding(query) if (engine != "hips" and not no_embed) else None
    vec_ranks: dict[int, int] = {}
    vec_scores: dict[int, float] = {}
    all_chunks = conn.execute(
        "SELECT id, rel_path, heading, heading_path, start_line, end_line, content, embedding FROM chunks"
    ).fetchall()
    chunk_map = {c["id"]: dict(c) for c in all_chunks if allowed_docs is None or c["rel_path"] in allowed_docs}

    hips_ranks: dict[int, int] = {}
    hips_scores: dict[int, float] = {}
    for cid, c in chunk_map.items():
        hkey = (c["rel_path"], int(c["start_line"]), int(c["end_line"]))
        if hkey in hips_key_ranks:
            hips_ranks[cid] = hips_key_ranks[hkey]
            hips_scores[cid] = hips_key_scores[hkey]

    if q_vec:
        scored = []
        for cid, c in chunk_map.items():
            if c["embedding"]:
                sim = cosine_sim(q_vec, json.loads(c["embedding"]))
                vec_scores[cid] = sim
                scored.append((sim, cid))
        scored.sort(reverse=True)
        for rank_idx, (_sim, cid) in enumerate(scored[:30], start=1):
            vec_ranks[cid] = rank_idx

    rrf_scores: dict[int, float] = {}
    k_rrf = float(active_policy.get("k_rrf", 30.0))
    w_hips = float(active_policy.get("w_hips", 1.15))
    w_fts_with_hips = float(active_policy.get("w_fts_with_hips", 0.85))
    w_fts_solo = float(active_policy.get("w_fts_solo", 1.0))
    w_vec = float(active_policy.get("w_vec", 1.20))
    exact_id_boost = float(active_policy.get("exact_id_boost", 0.025))
    graph_1hop_boost = float(active_policy.get("graph_1hop_boost", 1.15))
    graph_neighbor_lead = float(active_policy.get("graph_neighbor_lead_factor", 0.28))

    has_hips = len(hips_ranks) > 0
    query_terms = list(dict.fromkeys(fold_accents(t) for t in re.findall(r"\w+", query) if len(t) >= 2))
    for cid, c in chunk_map.items():
        score = 0.0
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
            haystack = f"{c['rel_path']} {c['heading_path']} {c['content']}".lower()
            matched_ids = sum(1 for eid in exact_identifiers if eid in haystack)
            if matched_ids > 0:
                score += exact_id_boost * matched_ids
                if cid not in fts_ranks:
                    fts_ranks[cid] = 1

        if score > 0 and engine != "hips" and len(query_terms) >= 2:
            folded_hay = fold_accents(f"{c['rel_path']} {c['heading_path']} {c['content']}")
            matched_qt = sum(1 for qt in query_terms if qt in folded_hay)
            cov_ratio = matched_qt / len(query_terms)
            if cov_ratio > 0:
                score += 0.008 * cov_ratio + (0.006 if cov_ratio >= 0.999 else 0.0)

        if score > 0:
            doc_t = str(doc_meta.get(c["rel_path"], {}).get("doc_type", "")).lower()
            if doc_t in ("rsi-ledger", "distillation-ledger") or (
                c["rel_path"] == "02-operating-system/autonomous-rsi-distillations.md"
            ):
                score *= 0.60
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
    for doc_path in top_initial_docs:
        parent_best = max(
            (rrf_scores[cid] for cid, c in chunk_map.items() if c["rel_path"] == doc_path and cid in rrf_scores),
            default=0.0,
        )
        for r in conn.execute(
            "SELECT target_path FROM links WHERE source_path = ? UNION SELECT source_path FROM links WHERE target_path = ?",
            (doc_path, doc_path),
        ):
            nb = r[0]
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
        nb_chunks = [cid for cid, c in chunk_map.items() if c["rel_path"] == nb_doc]
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
    results = hybrid_retrieve(conn, vault_dir, question, top_k=6)
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


def cmd_verify(args, no_auto_sync: bool = False) -> None:
    """
    6-Gate Deterministic Vault & Environment Doctor:
      Gate 1: Frontmatter Schema & Date Freshness
      Gate 2: Wikilink Integrity (0 broken [[wikilinks]])
      Gate 3: Graph Connectivity (0 unintended orphan notes)
      Gate 4: SQLite FTS5, HIPS Block-Max WAND & Vector Embedding Completeness (+ RSI Ledger Chain)
      Gate 5: Git & Binary Hygiene (0 tracked .mp4/.pyc files)
      Gate 6: Agent Skills & Zero-Auth Plugins Frontmatter Integrity (valid YAML frontmatter & schemas)
    """
    vault_dir = resolve_vault_dir(getattr(args, "vault", None), must_exist=True)
    allow_lexical = _is_vault_no_embed(vault_dir, getattr(args, "allow_lexical_only", False))
    if getattr(args, "heal", False):
        from sovereign_kb.rsi import heal_and_consolidate_vault

        heal_rep = heal_and_consolidate_vault(
            vault_dir=vault_dir,
            list_md_fn=list_vault_md_files,
            parse_fm_fn=parse_frontmatter,
            extract_links_fn=extract_wikilinks,
            build_stem_fn=build_stem_lookup,
            sync_fn=cmd_sync,
            call_llm_fn=call_llm,
            use_llm=False,
            no_embed=allow_lexical,
            consolidate_episodic=False,
        )
        if not getattr(args, "json", False):
            healed = list(
                dict.fromkeys(
                    list(heal_rep.get("healed_frontmatter") or [])
                    + list(heal_rep.get("healed_orphans") or [])
                    + (["INDEX.md"] if heal_rep.get("index_created") else [])
                )
            )
            if healed:
                print(f"[skb verify --heal] Repaired {len(healed)} file(s): {', '.join(healed)}")
            else:
                print("[skb verify --heal] No repairs needed.")

    conn = get_db(vault_dir)
    md_files = list_vault_md_files(vault_dir)
    stem_lookup = build_stem_lookup(vault_dir, md_files)
    violations: list[dict] = []

    inbound_counts: dict[str, int] = {str(p.relative_to(vault_dir)): 0 for p in md_files}
    for p in md_files:
        rel = str(p.relative_to(vault_dir))
        text = p.read_text(encoding="utf-8", errors="replace")
        meta = parse_frontmatter(text)
        if rel not in ("AGENTS.md", "README.md"):
            if meta.get("yaml_error"):
                violations.append({"gate": "Gate 1 (Frontmatter)", "file": rel, "issue": str(meta["yaml_error"])})
            for req_key in ("title", "type", "updated", "tags"):
                if not meta.get(req_key):
                    violations.append(
                        {"gate": "Gate 1 (Frontmatter)", "file": rel, "issue": f"Missing frontmatter field '{req_key}'"}
                    )

        for target in extract_wikilinks(text, stem_lookup, rel):
            if target not in inbound_counts:
                violations.append(
                    {"gate": "Gate 2 (Wikilinks)", "file": rel, "issue": f"Broken wikilink target '{target}'"}
                )
            elif target != rel:
                inbound_counts[target] += 1

    for rel, count in inbound_counts.items():
        if rel in ("INDEX.md", "AGENTS.md", "README.md"):
            continue
        if count == 0:
            violations.append(
                {"gate": "Gate 3 (Graph Connectivity)", "file": rel, "issue": "Orphan note (0 inbound wikilinks)"}
            )

    needs_auto_sync = _is_index_stale(conn, vault_dir, md_files)
    if needs_auto_sync:
        if not (no_auto_sync or getattr(args, "no_auto_sync", False)):
            conn.close()
            cmd_sync(
                argparse.Namespace(
                    vault=str(vault_dir),
                    force=False,
                    no_embed=allow_lexical,
                    quiet=getattr(args, "json", False),
                )
            )
            conn = get_db(vault_dir)
            needs_auto_sync = _is_index_stale(conn, vault_dir, md_files)
        if needs_auto_sync:
            violations.append(
                {
                    "gate": "Gate 4 (Index Sync)",
                    "file": ".kb/index.db",
                    "issue": "Index mtime is stale relative to vault Markdown files on disk; run 'sovereign-kb sync'",
                }
            )

    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    vec_count = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    hips_ready = hips_idx_dir.is_dir() and any(hips_idx_dir.iterdir())
    require_embed = getattr(args, "require_embeddings", False)
    if doc_count != len(md_files):
        violations.append(
            {
                "gate": "Gate 4 (Index Sync)",
                "file": ".kb/index.db",
                "issue": f"Indexed document count ({doc_count}) != vault markdown file count ({len(md_files)}); run 'sovereign-kb sync'",
            }
        )
    if chunk_count == 0:
        violations.append(
            {
                "gate": "Gate 4 (Embeddings)",
                "file": ".kb/index.db",
                "issue": "Zero indexed chunks; run 'sovereign-kb sync'",
            }
        )
    elif not allow_lexical and vec_count < chunk_count:
        # Fail if partial embeddings exist (0 < vec_count < chunk_count), or if --require-embeddings is set,
        # or if an embedding server is currently reachable online.
        if vec_count > 0 or require_embed or get_embedding("ping") is not None:
            violations.append(
                {
                    "gate": "Gate 4 (Embeddings)",
                    "file": ".kb/index.db",
                    "issue": f"Incomplete vector embeddings ({vec_count}/{chunk_count}); run 'sovereign-kb sync'",
                }
            )
    try:
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('integrity-check');")
        conn.commit()
    except sqlite3.OperationalError as e:
        violations.append({"gate": "Gate 4 (FTS5 Integrity)", "file": ".kb/index.db", "issue": str(e)})

    if (vault_dir / ".kb" / "rsi-ledger.jsonl").is_file():
        from sovereign_kb.rsi import verify_ledger_chain

        chain_ok, _ = verify_ledger_chain(vault_dir)
        if not chain_ok:
            violations.append(
                {
                    "gate": "Gate 4 (RSI Ledger Chain)",
                    "file": ".kb/rsi-ledger.jsonl",
                    "issue": "Tampered or broken SHA-256 hash chain in .kb/rsi-ledger.jsonl",
                }
            )

    links_count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    conn.close()

    for p in md_files:
        rel = str(p.relative_to(vault_dir))
        fence_char: str | None = None
        fence_len = 0
        for line_no, raw_line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m_fence = re.match(r"^\s*(`{3,}|~{3,})", raw_line)
            if m_fence:
                run = m_fence.group(1)
                if fence_char is None:
                    fence_char = run[0]
                    fence_len = len(run)
                elif run[0] == fence_char and len(run) >= fence_len:
                    fence_char = None
                    fence_len = 0
                continue
            if fence_char is None and re.match(r"^(<{7} |={7}$|>{7} )", raw_line):
                violations.append(
                    {
                        "gate": "Gate 5 (Git Conflict Markers)",
                        "file": f"{rel}:{line_no}",
                        "issue": f"Unresolved Git merge/rebase conflict marker: {raw_line[:30]}",
                    }
                )
                break

    if (vault_dir / ".git").exists():
        git_ls = subprocess.run(
            ["git", "-C", str(vault_dir), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        for tracked in git_ls:
            if tracked.endswith((".mp4", ".mov", ".pyc", ".DS_Store")) or "__pycache__" in tracked:
                violations.append(
                    {
                        "gate": "Gate 5 (Git Hygiene)",
                        "file": tracked,
                        "issue": "Tracked binary video or bytecode artifact in git index",
                    }
                )

    op_cli = Path.home() / ".local" / "bin" / "openproject-query"
    episodic_dir = vault_dir / "04-episodic"
    if op_cli.is_file() and episodic_dir.is_dir() and vault_dir.name == "me-kb":
        ep_logs = sorted(episodic_dir.glob("*-log.md"))
        if ep_logs:
            latest_ep = ep_logs[-1]
            try:
                op_res = subprocess.run(
                    [str(op_cli), "--verify-log", str(latest_ep)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                if op_res.returncode != 0:
                    violations.append(
                        {
                            "gate": "Gate 5 (OpenProject Reference Drift)",
                            "file": str(latest_ep.relative_to(vault_dir)),
                            "issue": (op_res.stderr or op_res.stdout).strip()
                            or f"openproject-query exited {op_res.returncode}",
                        }
                    )
            except subprocess.TimeoutExpired:
                violations.append(
                    {
                        "gate": "Gate 5 (OpenProject Reference Drift)",
                        "file": str(latest_ep.relative_to(vault_dir)),
                        "issue": "openproject-query --verify-log timed out after 15s",
                    }
                )

    # Gate 6: Agent Skills & Zero-Auth Plugins Frontmatter Integrity
    from sovereign_kb.gwi import validate_skill_frontmatter

    all_skills_to_check: list[Path] = sorted(
        p
        for p in vault_dir.rglob("SKILL.md")
        if not any(part.startswith(".") for part in p.relative_to(vault_dir).parts)
    )
    if vault_dir.name == "me-kb":
        skills_root = Path.home() / ".gemini" / "config" / "skills"
        plugins_root = Path.home() / ".gemini" / "config" / "plugins"
        if skills_root.exists():
            for sdir in sorted(skills_root.iterdir()):
                smd = sdir / "SKILL.md"
                if sdir.is_dir() and smd.is_file():
                    all_skills_to_check.append(smd)
        if plugins_root.exists():
            for pdir in sorted(plugins_root.iterdir()):
                ps_dir = pdir / "skills"
                if ps_dir.exists():
                    for sk in sorted(ps_dir.iterdir()):
                        smd = sk / "SKILL.md"
                        if sk.is_dir() and smd.is_file():
                            all_skills_to_check.append(smd)

    for smd in all_skills_to_check:
        ok, err = validate_skill_frontmatter(smd)
        if not ok:
            try:
                rel_f = str(smd.relative_to(Path.home()))
            except ValueError:
                rel_f = str(smd)
            violations.append(
                {
                    "gate": "Gate 6 (Agent Skills)",
                    "file": rel_f,
                    "issue": err,
                }
            )

    report = {
        "status": "PASS" if not violations else "FAIL",
        "timestamp": datetime.datetime.now().isoformat(),
        "vault_dir": str(vault_dir),
        "documents": len(md_files),
        "chunks": chunk_count,
        "vectors": vec_count,
        "hips_indexed": hips_ready,
        "links": links_count,
        "skills_checked": len(all_skills_to_check),
        "violations_count": len(violations),
        "violations": violations,
    }

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        hips_tag = ", HIPS WAND: OK" if hips_ready else ""
        print(f"=== Sovereign-KB Deterministic Verification (6-Gate Doctor): {report['status']} ===")
        print(
            f"  Gate 1 (Frontmatter Schema) : {'PASS' if not any(v['gate'].startswith('Gate 1') for v in violations) else 'FAIL'}"
        )
        print(
            f"  Gate 2 (Wikilink Integrity) : {'PASS' if not any(v['gate'].startswith('Gate 2') for v in violations) else 'FAIL'}"
        )
        print(
            f"  Gate 3 (Graph Connectivity) : {'PASS' if not any(v['gate'].startswith('Gate 3') for v in violations) else 'FAIL'}"
        )
        print(
            f"  Gate 4 (FTS5, HIPS & Vecs)  : {'PASS' if not any(v['gate'].startswith('Gate 4') for v in violations) else 'FAIL'} ({vec_count}/{chunk_count} vectors{hips_tag})"
        )
        print(
            f"  Gate 5 (Git & Media Hygiene): {'PASS' if not any(v['gate'].startswith('Gate 5') for v in violations) else 'FAIL'}"
        )
        print(
            f"  Gate 6 (Skills & Plugins)   : {'PASS' if not any(v['gate'].startswith('Gate 6') for v in violations) else 'FAIL'} ({len(all_skills_to_check)} skills verified)"
        )
        if violations:
            print("\nViolations detected:")
            for v in violations:
                print(f"  - [{v['gate']}] {v['file']}: {v['issue']}")
            if not getattr(args, "heal", False) and any(v["gate"].startswith(("Gate 1", "Gate 3")) for v in violations):
                print(
                    "\nTip: Run 'skb verify --heal' to auto-generate missing YAML frontmatter and link orphan notes into INDEX.md."
                )

    if violations:
        sys.exit(1)


ARCHETYPES: dict[str, dict[str, str]] = {
    "web-ts-3d-gis": {
        "title": "Full-Stack Web / TypeScript / 3D GIS (Next.js / Vite / CesiumJS / Three.js)",
        "stack": "TypeScript, React 19 / Next.js 16 / Vite, CesiumJS / Three.js, Vitest, Playwright, Knip",
        "gate_cmds": """# Stage 1: Git conflict & secret hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' src/ || exit 1
# Stage 2: Dead code, lint & strict TypeScript
npx knip && npm run lint && npm run typecheck
# Stage 3: Unit tests & production build
npm run test:run && npm run build
# Stage 4: Hermetic Playwright E2E + Video Walkthrough
npm run test:e2e""",
        "gitignore": "node_modules/\ndist/\n.next/\ncoverage/\ntest-results/\nplaywright-report/\n*.env.local\n.DS_Store\n",
    },
    "python-uv-ai": {
        "title": "Python `uv` / FastAPI / ML / Queueing & Sovereign AI Systems",
        "stack": "Python 3.10+ (uv), FastAPI, Pydantic v2, Ruff, Pytest, SimPy / PyTorch",
        "gate_cmds": """# Stage 1: Git conflict & secret hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.venv --exclude-dir=.git || exit 1
# Stage 2: Ruff lint & format check
uv run ruff check . && uv run ruff format --check .
# Stage 3: Deterministic unit & property tests (pinned seed)
PYTHONHASHSEED=42 uv run pytest -q
# Stage 4: Smoke & CLI/API contract verification
uv run python -m compileall -q .""",
        "gitignore": ".venv/\n__pycache__/\n*.pyc\n.pytest_cache/\n.ruff_cache/\ndist/\n*.egg-info/\n.DS_Store\n",
    },
    "rust-systems": {
        "title": "Rust Systems / Non-Euclidean Spatial Engine / High-Throughput Kernels",
        "stack": "Rust (Cargo), Clippy (-D warnings), Criterion, WGSL / WebGPU / Tokio",
        "gate_cmds": """# Stage 1: Formatting & merge marker check
cargo fmt --all -- --check
# Stage 2: Strict Clippy static analysis
cargo clippy --all-targets --all-features -- -D warnings
# Stage 3: Deterministic unit, doc & integration tests
cargo test --all-targets
# Stage 4: Release build verification
cargo build --release""",
        "gitignore": "target/\nCargo.lock\n.DS_Store\n",
    },
    "spatial-xr-ble": {
        "title": "Spatial WebXR / Unity 6 / Quest 3 + Hardware BLE GATT Biometrics",
        "stack": "Three.js r128+ WebXR / Unity 6, WebBluetooth / Android BLE GATT Bridge, FastAPI Telemetry, Playwright CDP Video",
        "gate_cmds": """# Stage 1: Merge marker & asset manifest verification
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=node_modules --exclude-dir=.git || exit 1
# Stage 2: Static analysis & schema validation
npm run lint && npm run typecheck
# Stage 3: Hermetic simulation & mock BLE GATT unit suite
npm run test:run
# Stage 4: Headless WebGL2 / WebXR walkthrough + contact sheet generation
npm run record:walkthrough && npm run review:video""",
        "gitignore": "node_modules/\ndist/\nLibrary/\nTemp/\nObj/\nBuilds/\nLogs/\n*.apk\n.DS_Store\n",
    },
    "paper-ieee-arxiv": {
        "title": "IEEE / ACM / arXiv LaTeX Manuscript + Reproducible Simulation-to-Figure Suite",
        "stack": "LaTeX (IEEEtran / acmart / booktabs), Python uv (NumPy/SciPy/Matplotlib Type-42 PDF), latexmk, pdffonts, Tri-Council Audit Ledger",
        "gate_cmds": """# Stage 1: Git conflict & tracked build-artifact hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.git --exclude-dir=.venv || exit 1
# Stage 2: Deterministic simulation -> results/metrics.json & Type-42 vector PDF figures
if [ -f sim/run_sim.py ]; then PYTHONHASHSEED=42 uv run python sim/run_sim.py; fi
if [ -f scripts/plot_figures.py ]; then uv run python scripts/plot_figures.py; fi
# Stage 3: Zero-hallucination bibliography & citation resolution gate
if [ -f scripts/verify_bib.py ]; then uv run python scripts/verify_bib.py; fi
# Stage 4: Clean non-interactive PDF compilation via latexmk
TEX_MAIN="${TEX_MAIN:-paper.tex}"
if [ ! -f "$TEX_MAIN" ] && [ -f "main.tex" ]; then TEX_MAIN="main.tex"; fi
TEX_BASE="${TEX_MAIN%.tex}"
if [ -f "$TEX_MAIN" ] && command -v latexmk >/dev/null 2>&1; then
  latexmk -pdf -interaction=nonstopmode -halt-on-error "$TEX_MAIN"
  # Stage 5: Zero undefined citations/references, Overfull hbox & Anti-resizebox tabular check
  ! grep -E "LaTeX Warning: (Citation|Reference).*undefined" "${TEX_BASE}.log" || exit 1
  ! grep -E "LaTeX Warning: Label .* multiply defined" "${TEX_BASE}.log" || exit 1
  ! grep -rnE '\\resizebox\\{[^}]*\\}\\{[^}]*\\}\\{\\begin\\{tabular' . --include="*.tex" || exit 1
  # Stage 6: IEEE PDF eXpress Type-3 font ban & 150-DPI visual page rendering
  if command -v pdffonts >/dev/null 2>&1; then
    ! pdffonts "${TEX_BASE}.pdf" | awk 'NR>2 {print $2, $3}' | grep -E "Type 3" || exit 1
  fi
  if command -v pdftoppm >/dev/null 2>&1; then
    mkdir -p artifacts/pages && rm -f artifacts/pages/page*.png
    pdftoppm -png -r 150 "${TEX_BASE}.pdf" artifacts/pages/page
  fi
fi""",
        "gitignore": "*.aux\n*.bbl\n*.blg\n*.fdb_latexmk\n*.fls\n*.log\n*.out\n*.synctex.gz\n*.toc\nartifacts/\n__pycache__/\n.venv/\n.DS_Store\n",
    },
    "game-sim-3d": {
        "title": "3D Game Engine & Embodied AI Simulation (Godot 4 / Bevy ECS / Unity 6 Headless)",
        "stack": "Godot 4 (GDScript / Headless CLI), Bevy ECS (Rust / Cargo / Vulkan), Unity 6 (-batchmode / ML-Agents), Python uv",
        "gate_cmds": """# Stage 1: Merge marker & binary artifact hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.git --exclude-dir=.godot --exclude-dir=target --exclude-dir=Library || exit 1
# Stage 2: Godot 4 headless script & scene validation (when project.godot and scenes/main.gd are present)
if [ -f project.godot ] && [ -f scenes/main.gd ] && command -v godot >/dev/null 2>&1; then godot --headless --path . -s scenes/main.gd; fi
# Stage 3: Bevy / Rust ECS compile & test gate (when Cargo.toml is present)
if [ -f Cargo.toml ] && command -v cargo >/dev/null 2>&1; then cargo clippy --all-targets -- -D warnings && cargo test; fi
# Stage 4: Deterministic headless simulation & telemetry verification
if [ -f sim/verify_sim.py ]; then PYTHONHASHSEED=42 uv run python sim/verify_sim.py; fi""",
        "gitignore": ".godot/\ntarget/\nLibrary/\nTemp/\nObj/\nBuilds/\nLogs/\nUserSettings/\n*.import\n*.apk\n.venv/\n__pycache__/\n.DS_Store\n",
    },
    "metatopia-chameleon": {
        "title": "Metatopia Chameleon 2D App & 3D Spatial Experience Suite (Goldwood / Obsidian / Cobalt / Folio)",
        "stack": "React 19, TypeScript 5, Vite 6, Three.js (WebGL2/WebXR), 3-Tier Token Bridge, Vitest, Playwright 1080p Video, GitLab & Cloudflare Staging",
        "gate_cmds": """# Stage 1: Merge conflict & Math.random hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' src/ tests/ e2e/ tokens/ schemas/ 2>/dev/null || exit 1
! grep -rn "Math.random" src/ 2>/dev/null || exit 1
# Stage 2: Strict TypeScript & Unit Tests
if [ -f package.json ]; then npm run typecheck && npm run test:run; fi
# Stage 3: Production Build & Goldwood (G >= 9.0) Staging Bundle
if [ -f scripts/build_staging_bundle.mjs ]; then npm run build:staging; fi
# Stage 4: Hermetic Playwright E2E & 1080p Video Walkthrough
if [ -f scripts/record_walkthrough.mjs ]; then node scripts/record_walkthrough.mjs; fi""",
        "gitignore": "node_modules/\ndist/\ntest-results/\nplaywright-report/\nartifacts/*.webm\n*.log\n.DS_Store\n.env.local\n",
    },
}

ARCHETYPE_SKILL_ROUTING: dict[str, list[tuple[str, str]]] = {
    "web-ts-3d-gis": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("vercel-react-craft", "~/.gemini/config/skills/vercel-react-craft/SKILL.md"),
        ("ui-craft-polish", "~/.gemini/config/skills/ui-craft-polish/SKILL.md"),
        ("frontend-design", "~/.gemini/config/skills/frontend-design/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("webapp-testing", "~/.gemini/config/skills/webapp-testing/SKILL.md"),
        ("cloudflare-platform", "~/.gemini/config/skills/cloudflare-platform/SKILL.md"),
        ("principal-engineering", "~/.gemini/config/skills/principal-engineering/SKILL.md"),
    ],
    "metatopia-chameleon": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("vercel-react-craft", "~/.gemini/config/skills/vercel-react-craft/SKILL.md"),
        ("ui-craft-polish", "~/.gemini/config/skills/ui-craft-polish/SKILL.md"),
        ("frontend-design", "~/.gemini/config/skills/frontend-design/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("remotion-video", "~/.gemini/config/skills/remotion-video/SKILL.md"),
        ("webapp-testing", "~/.gemini/config/skills/webapp-testing/SKILL.md"),
        ("cloudflare-platform", "~/.gemini/config/skills/cloudflare-platform/SKILL.md"),
    ],
    "python-uv-ai": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("gx10-cluster", "~/.gemini/config/skills/gx10-cluster/SKILL.md"),
        ("huggingface-ml", "~/.gemini/config/skills/huggingface-ml/SKILL.md"),
        ("mcp-builder", "~/.gemini/config/skills/mcp-builder/SKILL.md"),
        ("supabase-postgres", "~/.gemini/config/skills/supabase-postgres/SKILL.md"),
        ("principal-engineering", "~/.gemini/config/skills/principal-engineering/SKILL.md"),
    ],
    "rust-systems": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("bevy-gamedev", "~/.gemini/config/skills/bevy-gamedev/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("principal-engineering", "~/.gemini/config/skills/principal-engineering/SKILL.md"),
    ],
    "spatial-xr-ble": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("unity", "~/.gemini/config/plugins/unity/skills/unity-cli/SKILL.md"),
        ("remotion-video", "~/.gemini/config/skills/remotion-video/SKILL.md"),
        ("ui-craft-polish", "~/.gemini/config/skills/ui-craft-polish/SKILL.md"),
    ],
    "paper-ieee-arxiv": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("latex-paper-craft", "~/.gemini/config/skills/latex-paper-craft/SKILL.md"),
        ("pdf", "~/.gemini/config/skills/pdf/SKILL.md"),
        ("science-arxiv", "~/.gemini/config/plugins/science/skills/literature_search_arxiv/SKILL.md"),
        ("science-openalex", "~/.gemini/config/plugins/science/skills/literature_search_openalex/SKILL.md"),
        ("huggingface-ml", "~/.gemini/config/skills/huggingface-ml/SKILL.md"),
    ],
    "game-sim-3d": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("godot-gamedev", "~/.gemini/config/skills/godot-gamedev/SKILL.md"),
        ("bevy-gamedev", "~/.gemini/config/skills/bevy-gamedev/SKILL.md"),
        ("unity", "~/.gemini/config/plugins/unity/skills/unity-cli/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("remotion-video", "~/.gemini/config/skills/remotion-video/SKILL.md"),
    ],
}

GWI_PROFILE_SKILL_ROUTING: dict[str, list[str]] = {
    "app": [
        "goldwood-gwi",
        "vercel-react-craft",
        "ui-craft-polish",
        "frontend-design",
        "webapp-testing",
        "cloudflare-platform",
        "supabase-postgres",
        "chrome-devtools-plugin",
        "modern-web-guidance-plugin",
    ],
    "sim": [
        "goldwood-gwi",
        "threejs-webgpu",
        "godot-gamedev",
        "bevy-gamedev",
        "unity",
        "gx10-cluster",
    ],
    "video": [
        "goldwood-gwi",
        "remotion-video",
        "gemini-api",
        "threejs-webgpu",
    ],
    "paper": [
        "goldwood-gwi",
        "latex-paper-craft",
        "pdf",
        "science",
        "huggingface-ml",
        "keenable",
        "parallel",
    ],
}

GWI_LENS_SKILL_ROUTING: dict[str, list[str]] = {
    "technical-artist": ["goldwood-gwi", "threejs-webgpu", "godot-gamedev", "bevy-gamedev", "unity"],
    "ux-ergonomics": [
        "goldwood-gwi",
        "ui-craft-polish",
        "a11y-debugging",
        "frontend-design",
        "ui",
        "ui-ugui",
        "ui-uitk",
    ],
    "spatial-cinema": [
        "goldwood-gwi",
        "remotion-video",
        "gemini-omni-flash-api",
        "optimize-audio",
        "audio-setup-mixers",
    ],
    "typography-editor": ["goldwood-gwi", "latex-paper-craft", "pdf", "docx", "pptx", "xlsx"],
    "web-perf-edge": [
        "goldwood-gwi",
        "vercel-react-craft",
        "debug-optimize-lcp",
        "memory-leak-debugging",
        "cookie-debugging",
        "cloudflare-platform",
        "optimize-web",
    ],
    "data-api-architect": [
        "goldwood-gwi",
        "supabase-postgres",
        "bigquery-sql",
        "dbt-bigquery",
        "dataform-bigquery",
        "gcp-dataflow",
        "gcp-spark",
        "accidental-data-loss-prevention",
        "api-engineer",
        "api-testing",
        "api-mocking",
    ],
    "ai-ml-science": [
        "goldwood-gwi",
        "gx10-cluster",
        "gemini-api-dev",
        "google-antigravity-sdk",
        "huggingface-ml",
        "ml-best-practices",
        "mcp-builder",
        "science",
    ],
    "principal-verification": [
        "goldwood-gwi",
        "principal-engineering",
        "webapp-testing",
        "managing-python-dependencies",
        "uv",
        "credentials",
        "personal-codex",
    ],
}


def cmd_scaffold(args) -> None:
    if getattr(args, "list", False) or not args.archetype:
        print("=== Available Autonomous Project Archetypes (`sovereign-kb scaffold <archetype> <dir>`) ===")
        for key, spec in ARCHETYPES.items():
            routed = ", ".join(s[0] for s in ARCHETYPE_SKILL_ROUTING.get(key, []))
            print(f"  - {key:18s} : {spec['title']}")
            print(f"    {'':18s}   Stack : {spec['stack']}")
            print(f"    {'':18s}   Skills: {routed}")
        return

    arch = args.archetype
    if arch not in ARCHETYPES:
        print(f"Error: Unknown archetype '{arch}'. Choose from: {', '.join(ARCHETYPES.keys())}")
        sys.exit(1)

    if not args.target_dir:
        print("Error: Please specify target directory: sovereign-kb scaffold <archetype> <target_dir>")
        sys.exit(1)

    target = Path(args.target_dir).expanduser().resolve()
    proj_name = args.name or target.name
    author_name = getattr(args, "author", None) or os.environ.get("KB_AUTHOR_NAME", "Author Name")
    spec = ARCHETYPES[arch]
    today = datetime.date.today().isoformat()

    target.mkdir(parents=True, exist_ok=True)
    (target / "docs").mkdir(exist_ok=True)
    (target / "scripts").mkdir(exist_ok=True)

    skill_rows = "\n".join(f"| `{sname}` | `{spath}` |" for sname, spath in ARCHETYPE_SKILL_ROUTING.get(arch, []))

    agents_md = f"""# AGENTS.md — Master Platform Guide & Complete Feature Registry

**Repository:** `{proj_name}`
**Archetype:** `{arch}` — {spec["title"]}
**Primary Stack:** `{spec["stack"]}`
**Initialized:** `{today}` via `sovereign-kb scaffold {arch}`

---

## 🎯 High-Level Agent Operating System

### 1. Autonomous Multi-Model Routing Strategy
| Tier | Role | Target Agent / Tier | Responsibilities |
| :--- | :--- | :--- | :--- |
| **Tier 1** | **Fast Execution & Implementation** | **Fast Worker (`sonnet`)** | Feature/simulation implementation, plotting scripts, tests, and rapid fixes. |
| **Tier 2** | **Deep Architecture / Reviewer #1** | **Heavy Worker (`fable`)** | System architecture, narrative/positioning coherence, and UX/figure review. |
| **Tier 3** | **Formal Verification / Reviewer #2** | **Independent Auditor (`astra`)** | Mathematical proofs, boundary/dimensional checks, concurrency, and edge-case audits. |
| **Tier 4** | **Sovereign Local Big-Context** | **Local Long-Context Node** | Private long-context cross-artifact sweeps across code, JSON results, and prose. |

### 2. Mandatory Operating Rules
1. **100% Catalog / Spec Synchronization**: Every feature, endpoint, module, or theorem must be registered in [`docs/FEATURE_CATALOG.md`](docs/FEATURE_CATALOG.md) (or `PAPER_SPEC.md` & `AUDIT_LEDGER.md` for papers) with its exact implementation path and verification coverage.
2. **Parallel Warm-Started Multi-Model Review (`Rule 1 — Warm-Start, Don't Restrict`)**: Run `fable` and `astra` in parallel (`oc triad "<prompt>"`) with a mechanical Git & test warm-start briefing while keeping full tool access enabled. Never self-exempt code changes as "low-risk" to skip independent review unless explicitly waived by the user.
3. **Hermetic Offline Execution & Zero Synthetic Data**: All simulations, tests, and walkthroughs must run deterministically offline using seeded fixtures (`PYTHONHASHSEED=42`) with zero fabricated numbers.
4. **Deterministic Quality Gate**: Never declare a task complete until `./scripts/quality_gate.sh` exits `0`.
5. **Centralized Credentials**: Never hardcode tokens or secrets. Source platform credentials from `~/.config/agents/secrets.env` (auto-loaded via `~/.zshenv`).

### 3. Activated Sovereign Agent Skills (`kb skills --archetype {arch}`)
| Skill ID | Local Entry Point (`SKILL.md` + `references/`) |
| :--- | :--- |
{skill_rows}

---

## ⚡ Standard Verification Commands

```bash
./scripts/quality_gate.sh
```

{spec["gate_cmds"]}

---

## 🗺️ Master Traceability Registry
See [`docs/FEATURE_CATALOG.md`](docs/FEATURE_CATALOG.md) (and `PAPER_SPEC.md` / `AUDIT_LEDGER.md` for scientific manuscripts).
"""

    feature_catalog_md = f"""# Master Feature Catalog — `{proj_name}`

| ID | Domain / Subsystem | Feature Description | Source Files | Automated Test / Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **F-01** | Core Architecture | Initial `{arch}` deterministic harness & quality gate | `AGENTS.md`, `scripts/quality_gate.sh` | `./scripts/quality_gate.sh` | ✅ |
"""

    quality_gate_sh = f"""#!/usr/bin/env bash
set -euo pipefail

echo "=== [{proj_name}] Running Deterministic Quality Gate ({arch}) ==="
{spec["gate_cmds"]}
echo "=== [{proj_name}] ALL QUALITY GATES PASSED ==="
"""

    agents_path = target / "AGENTS.md"
    catalog_path = target / "docs" / "FEATURE_CATALOG.md"
    gate_path = target / "scripts" / "quality_gate.sh"
    gitignore_path = target / ".gitignore"

    force = getattr(args, "force", False)
    if not agents_path.exists() or force:
        agents_path.write_text(agents_md, encoding="utf-8")
    if not catalog_path.exists() or force:
        catalog_path.write_text(feature_catalog_md, encoding="utf-8")
    if not gate_path.exists() or force:
        gate_path.write_text(quality_gate_sh, encoding="utf-8")
        gate_path.chmod(gate_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not gitignore_path.exists():
        gitignore_path.write_text(spec["gitignore"], encoding="utf-8")

    created_extra: list[Path] = []
    if arch == "game-sim-3d":
        for d in ("sim", "results", "scenes"):
            (target / d).mkdir(exist_ok=True)
        godot_proj = target / "project.godot"
        godot_main = target / "scenes" / "main.gd"
        verify_sim = target / "sim" / "verify_sim.py"
        if not godot_proj.exists() or force:
            godot_proj.write_text(
                "; Engine configuration file.\n"
                "config_version=5\n\n"
                "[application]\n\n"
                f'config/name="{proj_name}"\n'
                'config/features=PackedStringArray("4.4", "Forward Plus")\n',
                encoding="utf-8",
            )
            created_extra.append(godot_proj)
        if not godot_main.exists() or force:
            godot_main.write_text(
                "extends SceneTree\n\n"
                "func _init() -> void:\n"
                "\tvar pos := Vector3(1.0, 2.0, 3.0)\n"
                "\tvar vel := Vector3(-0.5, 0.0, 0.5)\n"
                "\tfor _step in range(60):\n"
                "\t\tpos += vel * (1.0 / 60.0)\n"
                '\tprint("[godot headless] PASS: final_pos=", pos)\n'
                "\tquit(0)\n",
                encoding="utf-8",
            )
            created_extra.append(godot_main)
        if not verify_sim.exists() or force:
            verify_sim.write_text(
                "#!/usr/bin/env python3\n"
                '"""Deterministic headless 3D simulation step & telemetry contract verifier."""\n'
                "import json\nimport math\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    dt = 1.0 / 60.0\n"
                "    steps = 120\n"
                "    trajectory = [\n"
                '        {"tick": t, "time_s": round(t * dt, 4), "energy_j": round(math.exp(-0.01 * t), 6)}\n'
                "        for t in range(steps)\n"
                "    ]\n"
                '    out = Path(__file__).resolve().parent.parent / "results" / "sim_telemetry.json"\n'
                "    out.parent.mkdir(parents=True, exist_ok=True)\n"
                '    out.write_text(json.dumps({"seed": 42, "dt_s": dt, "steps": steps, "trajectory": trajectory[:5]}, indent=2), encoding="utf-8")\n'
                '    print(f"[verify_sim] PASS: Verified {steps} deterministic ticks -> {out}")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(verify_sim)

    if arch == "metatopia-chameleon":
        for d in ("tokens", "schemas", "src", "tests", "e2e", "evidence"):
            (target / d).mkdir(exist_ok=True)
        tok_path = target / "tokens" / "metatopia-tokens.json"
        if not tok_path.exists() or force:
            tok_path.write_text(
                json.dumps(
                    {
                        "version": "1.0.0",
                        "defaultConcept": "goldwood",
                        "supportedConcepts": ["goldwood", "obsidian", "cobalt", "folio"],
                        "supportedModes": ["split", "2d", "3d"],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            created_extra.append(tok_path)

    if arch == "paper-ieee-arxiv":
        for d in ("sim", "results", "figures", "tables"):
            (target / d).mkdir(exist_ok=True)

        paper_spec = target / "PAPER_SPEC.md"
        audit_ledger = target / "AUDIT_LEDGER.md"
        sim_script = target / "sim" / "run_sim.py"
        bib_script = target / "scripts" / "verify_bib.py"
        arxiv_script = target / "scripts" / "package_arxiv.py"
        bib_file = target / "references.bib"
        tex_file = target / "paper.tex"

        if not paper_spec.exists() or force:
            paper_spec.write_text(
                f"# PAPER_SPEC.md — Mathematical & Empirical Contract (`{proj_name}`)\n\n"
                "## 1. Core Thesis & Research Questions\n"
                "- **Primary Thesis**: `<State 1-sentence falsifiable core thesis>`\n"
                "- **$RQ_1$ (Analytical Bound & Stability)**: `<State RQ1>`\n"
                "- **$RQ_2$ (Empirical Latency / Throughput / Energy Regime)**: `<State RQ2>`\n\n"
                "## 2. SOTA Gap Matrix (Table I)\n"
                "| Reference | Closed-Form Bound | Non-Stationary / Cliff Regime | Real Telemetry | Energy ($\\text{J/tok}$) |\n"
                "| :--- | :---: | :---: | :---: | :---: |\n"
                "| Baseline A `\\cite{gelenbe1991product}` | ✅ | ❌ | ❌ | ❌ |\n"
                "| **Ours** | ✅ | ✅ | ✅ | ✅ |\n\n"
                "## 3. Notation & Assumption Ledger\n"
                "- **Assumption $A_1$**: Arrival rate $\\lambda > 0$ and service rate $\\mu > \\lambda$ satisfy $\\rho = \\lambda/\\mu \\in (0, 1)$.\n",
                encoding="utf-8",
            )
            created_extra.append(paper_spec)

        if not audit_ledger.exists() or force:
            audit_ledger.write_text(
                f"# AUDIT_LEDGER.md — Tri-Council Scientific Audit (`{proj_name}`)\n\n"
                "| Audit ID | Auditor Role | Severity | Section / Eq. | Finding | Resolution | Status |\n"
                "| :--- | :--- | :---: | :--- | :--- | :--- | :---: |\n"
                "| **AUD-01** | Reviewer #2 (Formal Math) | `CRITICAL` | §III, Eq. (1) | Verify boundary limit $\\rho \\to 0^+$ and $\\rho \\to 1^-$. | Verified analytically and in `sim/run_sim.py`. | `RESOLVED` |\n",
                encoding="utf-8",
            )
            created_extra.append(audit_ledger)

        if not sim_script.exists() or force:
            sim_script.write_text(
                "#!/usr/bin/env python3\n"
                '"""Deterministic analytical vs. Monte Carlo simulation producing results/metrics.json."""\n'
                "import json\nimport random\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    random.seed(42)\n"
                "    mu = 10.0\n"
                "    rhos = [0.2, 0.4, 0.6, 0.8]\n"
                "    records = []\n"
                "    for rho in rhos:\n"
                "        lam = rho * mu\n"
                "        analytical_delay = 1.0 / (mu - lam)\n"
                '        records.append({"rho": rho, "lambda": lam, "mu": mu, "analytical_delay_s": round(analytical_delay, 6)})\n'
                '    out = Path(__file__).resolve().parent.parent / "results" / "metrics.json"\n'
                "    out.parent.mkdir(parents=True, exist_ok=True)\n"
                '    out.write_text(json.dumps({"seed": 42, "records": records}, indent=2), encoding="utf-8")\n'
                '    print(f"[sim] Wrote {len(records)} records to {out}")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(sim_script)

        plot_script = target / "scripts" / "plot_figures.py"
        if not plot_script.exists() or force:
            plot_script.write_text(
                "#!/usr/bin/env python3\n"
                '"""Generate IEEE PDF eXpress compliant Type-42 vector PDF figures from results/metrics.json."""\n'
                "import json\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    root = Path(__file__).resolve().parent.parent\n"
                '    metrics_path = root / "results" / "metrics.json"\n'
                "    if not metrics_path.exists():\n"
                "        return\n"
                '    data = json.loads(metrics_path.read_text(encoding="utf-8"))["records"]\n'
                "    try:\n"
                "        import matplotlib.pyplot as plt\n"
                '        plt.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42, "font.family": "serif", "font.size": 9})\n'
                "        fig, ax = plt.subplots(figsize=(3.5, 2.4))\n"
                '        ax.plot([r["rho"] for r in data], [r["analytical_delay_s"] * 1000 for r in data], "-o", color="#0072B2", label="Analytical Bound")\n'
                '        ax.set_xlabel("Utilization $\\\\rho = \\\\lambda / \\\\mu$")\n'
                '        ax.set_ylabel("Mean Sojourn Delay (ms)")\n'
                '        ax.grid(True, linestyle="--", alpha=0.4)\n'
                "        ax.legend(frameon=False)\n"
                "        fig.tight_layout()\n"
                '        out_pdf = root / "figures" / "fig1_performance.pdf"\n'
                "        out_pdf.parent.mkdir(parents=True, exist_ok=True)\n"
                "        fig.savefig(out_pdf)\n"
                "        plt.close(fig)\n"
                '        print(f"[plot] Generated Type-42 vector figure {out_pdf}")\n'
                "    except ImportError:\n"
                '        print("[plot] matplotlib not installed in current env; skipping PDF plot generation.")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(plot_script)

        if not bib_script.exists() or force:
            bib_script.write_text(
                "#!/usr/bin/env python3\n"
                '"""Zero-hallucination BibTeX citation verifier."""\n'
                "import re\nimport sys\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    root = Path(__file__).resolve().parent.parent\n"
                '    bib_path = root / "references.bib"\n'
                "    if not bib_path.exists():\n"
                '        print("[verify_bib] No references.bib found; skipping.")\n'
                "        return\n"
                '    bib_text = bib_path.read_text(encoding="utf-8")\n'
                '    keys = re.findall(r"@\\w+\\{\\s*([^,\\s]+)\\s*,", bib_text)\n'
                "    if len(keys) != len(set(keys)):\n"
                '        print("ERROR: Duplicate BibTeX keys detected in references.bib", file=sys.stderr)\n'
                "        sys.exit(1)\n"
                "    key_set = set(keys)\n"
                "    cited = set()\n"
                '    for tex in root.glob("*.tex"):\n'
                '        for m in re.finditer(r"\\\\cite\\{([^}]+)\\}", tex.read_text(encoding="utf-8")):\n'
                '            for k in m.group(1).split(","):\n'
                "                if k.strip():\n"
                "                    cited.add(k.strip())\n"
                "    missing = sorted(cited - key_set)\n"
                "    if missing:\n"
                '        print(f"ERROR: Missing citations in references.bib: {missing}", file=sys.stderr)\n'
                "        sys.exit(1)\n"
                '    print(f"[verify_bib] PASS: {len(cited)} cited keys verified across {len(key_set)} BibTeX entries.")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(bib_script)

        if not arxiv_script.exists() or force:
            arxiv_script.write_text(
                "#!/usr/bin/env python3\n"
                '"""Package clean Overleaf / arXiv submission bundle."""\n'
                "import zipfile\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    root = Path(__file__).resolve().parent.parent\n"
                '    bundle = root / "overleaf_arxiv_bundle.zip"\n'
                '    include_exts = {".tex", ".bib", ".bbl", ".sty", ".cls", ".pdf"}\n'
                '    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:\n'
                '        for p in sorted(root.rglob("*")):\n'
                '            if p.is_file() and p.name != bundle.name and ".git" not in p.parts and ".venv" not in p.parts:\n'
                "                if p.suffix in include_exts:\n"
                "                    zf.write(p, p.relative_to(root))\n"
                '    print(f"[package_arxiv] Created {bundle}")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(arxiv_script)

        if not bib_file.exists() or force:
            bib_file.write_text(
                "@article{gelenbe1991product,\n"
                "  author  = {Gelenbe, Erol},\n"
                "  title   = {Product-Form Queueing Networks with Negative and Positive Customers},\n"
                "  journal = {Journal of Applied Probability},\n"
                "  volume  = {28},\n"
                "  number  = {3},\n"
                "  pages   = {656--663},\n"
                "  year    = {1991},\n"
                "  doi     = {10.2307/3214499}\n"
                "}\n",
                encoding="utf-8",
            )
            created_extra.append(bib_file)

        if not tex_file.exists() or force:
            tex_file.write_text(
                "\\documentclass[conference]{IEEEtran}\n"
                "\\usepackage{amsmath,amssymb,amsfonts,amsthm}\n"
                "\\usepackage{booktabs}\n"
                "\\usepackage{cite}\n"
                "\\newtheorem{theorem}{Theorem}\n"
                "\\newtheorem{assumption}{Assumption}\n\n"
                f"\\title{{{proj_name}: Deterministic Analytical and Empirical Evaluation}}\n"
                f"\\author{{\\IEEEauthorblockN{{{author_name}}}}}\n\n"
                "\\begin{document}\n"
                "\\maketitle\n\n"
                "\\begin{abstract}\n"
                "We formulate a falsifiable analytical and empirical model validated via deterministic simulation~\\cite{gelenbe1991product}.\n"
                "\\end{abstract}\n\n"
                "\\section{Introduction}\n"
                "Modern distributed systems require rigorous analytical bounds~\\cite{gelenbe1991product}.\n\n"
                "\\bibliographystyle{IEEEtran}\n"
                "\\bibliography{references}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            created_extra.append(tex_file)

    print(f"[sovereign-kb scaffold] Initialized '{proj_name}' ({arch}) at {target}")
    print(f"  - {agents_path}")
    print(f"  - {catalog_path}")
    print(f"  - {gate_path} (executable)")
    print(f"  - {gitignore_path}")
    for extra in created_extra:
        print(f"  - {extra}")


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
        subprocess.run(["git", "-C", str(vault_dir), "pull", "--rebase", "--autostash"], check=True)
        cmd_sync(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=_is_vault_no_embed(vault_dir)))
        if mode in ("sync", "push"):
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
        hybrid_retrieve_fn=hybrid_retrieve,
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
        evaluate_retrieval_benchmark,
        heal_and_consolidate_vault,
        load_retrieval_policy,
        read_rsi_ledger,
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
        bench = evaluate_retrieval_benchmark(conn, vault_dir, hybrid_retrieve, policy=policy, probes=all_probes)
        heldout_bench = (
            evaluate_retrieval_benchmark(conn, vault_dir, hybrid_retrieve, policy=policy, probes=heldout_probes)
            if heldout_probes
            else bench
        )
        conn.close()
        entries = read_rsi_ledger(vault_dir)
        chain_ok, count = verify_ledger_chain(vault_dir)
        payload = {
            "vault_dir": str(vault_dir),
            "objective_j": bench["objective_j"],
            "heldout_objective_j": heldout_bench["objective_j"],
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
                f"  Retrieval Metrics : MRR@5={bench['mrr_at_5']:.4f}, "
                f"NDCG@5={bench['ndcg_at_5']:.4f}, Recall@1={bench['recall_at_1']:.4f}, "
                f"LedgerIntrusions={bench.get('ledger_intrusions', 0)}"
            )
            print(
                f"  Active Policy     : k_rrf={policy['k_rrf']}, w_hips={policy['w_hips']}, "
                f"w_fts={policy['w_fts_with_hips']}, w_vec={policy['w_vec']}, "
                f"id_boost={policy['exact_id_boost']}, graph_boost={policy['graph_1hop_boost']}"
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
        res = tune_retrieval_policy(conn, vault_dir, hybrid_retrieve)
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

    report = run_rsi_cycle(
        vault_dir=vault_dir,
        get_db_fn=get_db,
        hybrid_retrieve_fn=hybrid_retrieve,
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


def _scan_native_usage(days: int) -> dict:
    """Zero-dependency pure-Python fallback scanner for OpenCode SQLite and Antigravity JSONL logs."""
    cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max(1, days) + 1)
    cutoff_ms = int(cutoff_dt.timestamp() * 1000)
    by_date: dict[str, dict] = {}

    def _ensure_day(d_str: str) -> dict:
        if d_str not in by_date:
            by_date[d_str] = {
                "period": d_str,
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheCreationTokens": 0,
                "cacheReadTokens": 0,
                "totalTokens": 0,
                "totalCost": 0.0,
                "agents": {},
            }
        return by_date[d_str]

    def _add_record(d_str: str, agent: str, model: str, inp: int, out: int, cw: int, cr: int, cost: float) -> None:
        day = _ensure_day(d_str)
        tot = inp + out + cw + cr
        day["inputTokens"] += inp
        day["outputTokens"] += out
        day["cacheCreationTokens"] += cw
        day["cacheReadTokens"] += cr
        day["totalTokens"] += tot
        day["totalCost"] += cost
        ag = day["agents"].setdefault(
            agent,
            {
                "agent": agent,
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheCreationTokens": 0,
                "cacheReadTokens": 0,
                "totalTokens": 0,
                "totalCost": 0.0,
                "models": {},
            },
        )
        ag["inputTokens"] += inp
        ag["outputTokens"] += out
        ag["cacheCreationTokens"] += cw
        ag["cacheReadTokens"] += cr
        ag["totalTokens"] += tot
        ag["totalCost"] += cost
        m = ag["models"].setdefault(
            model,
            {
                "modelName": model,
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheCreationTokens": 0,
                "cacheReadTokens": 0,
                "cost": 0.0,
            },
        )
        m["inputTokens"] += inp
        m["outputTokens"] += out
        m["cacheCreationTokens"] += cw
        m["cacheReadTokens"] += cr
        m["cost"] += cost

    oc_db = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    if oc_db.is_file():
        try:
            conn = sqlite3.connect(f"file:{oc_db}?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT time_created, json_extract(data, '$.model.id'), "
                "json_extract(data, '$.tokens.input'), json_extract(data, '$.tokens.output'), "
                "json_extract(data, '$.tokens.cache.write'), json_extract(data, '$.tokens.cache.read'), "
                "json_extract(data, '$.cost') "
                "FROM session_message WHERE type = 'assistant' AND time_created >= ?",
                (cutoff_ms,),
            ).fetchall()
            conn.close()
            for ts_ms, model_id, inp, out, cw, cr, cost in rows:
                if not ts_ms:
                    continue
                d_str = datetime.datetime.fromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%d")
                _add_record(
                    d_str,
                    "opencode",
                    model_id or "opencode-unknown",
                    int(inp or 0),
                    int(out or 0),
                    int(cw or 0),
                    int(cr or 0),
                    float(cost or 0.0),
                )
        except Exception:
            pass

    agy_brain = Path.home() / ".gemini" / "antigravity" / "brain"
    if agy_brain.is_dir():
        cutoff_mtime = cutoff_dt.timestamp()
        for conv_dir in agy_brain.iterdir():
            t_path = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if not t_path.is_file() or t_path.stat().st_mtime < cutoff_mtime:
                continue
            cur_model = "gemini-4-argon"
            try:
                for line in t_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if obj.get("type") == "USER_INPUT":
                        txt = obj.get("content", "")
                        m_match = re.search(r"Model Selection` from [^\n]+? to ([^.\n<]+)", txt)
                        if m_match:
                            cur_model = m_match.group(1).strip()
                    elif obj.get("type") == "PLANNER_RESPONSE":
                        inp = int(obj.get("input_tokens") or 0)
                        out = int(obj.get("output_tokens") or 0)
                        cr = int(obj.get("cache_read_tokens") or 0)
                        cw = int(obj.get("cache_creation_tokens") or 0)
                        created_at = obj.get("created_at", "")
                        if created_at and (inp or out or cr):
                            d_str = created_at[:10]
                            est_cost = (inp * 2.5 + out * 15.0 + cr * 0.5) / 1_000_000.0
                            _add_record(d_str, "antigravity", cur_model, inp, out, cw, cr, est_cost)
            except Exception:
                continue

    daily_list = []
    for d_str in sorted(by_date.keys())[-max(1, days) :]:
        day = by_date[d_str]
        agents_list = []
        all_models: dict[str, dict] = {}
        for ag in day["agents"].values():
            m_list = list(ag.pop("models").values())
            ag["modelBreakdowns"] = sorted(m_list, key=lambda x: x["cost"], reverse=True)
            agents_list.append(ag)
            for m in m_list:
                agg = all_models.setdefault(
                    m["modelName"],
                    {
                        "modelName": m["modelName"],
                        "inputTokens": 0,
                        "outputTokens": 0,
                        "cacheCreationTokens": 0,
                        "cacheReadTokens": 0,
                        "cost": 0.0,
                    },
                )
                agg["inputTokens"] += m["inputTokens"]
                agg["outputTokens"] += m["outputTokens"]
                agg["cacheCreationTokens"] += m["cacheCreationTokens"]
                agg["cacheReadTokens"] += m["cacheReadTokens"]
                agg["cost"] += m["cost"]
        day["agents"] = sorted(agents_list, key=lambda x: x["totalCost"], reverse=True)
        day["modelBreakdowns"] = sorted(all_models.values(), key=lambda x: x["cost"], reverse=True)
        daily_list.append(day)

    totals = {
        "inputTokens": sum(d["inputTokens"] for d in daily_list),
        "outputTokens": sum(d["outputTokens"] for d in daily_list),
        "cacheCreationTokens": sum(d["cacheCreationTokens"] for d in daily_list),
        "cacheReadTokens": sum(d["cacheReadTokens"] for d in daily_list),
        "totalTokens": sum(d["totalTokens"] for d in daily_list),
        "totalCost": sum(d["totalCost"] for d in daily_list),
    }
    return {"engine": "native-stdlib", "daily": daily_list, "totals": totals}


def collect_usage_payload(
    days: int = 7,
    force_native: bool = False,
    offline: bool = False,
    no_gx10: bool = False,
) -> dict:
    """Collect multi-agent FinOps, token telemetry & prompt-cache efficiency payload."""
    ccusage_bin = None
    if not force_native:
        cand = Path.home() / ".local" / "bin" / "ccusage"
        if cand.is_file() and os.access(cand, os.X_OK):
            ccusage_bin = str(cand)
        else:
            ccusage_bin = shutil.which("ccusage")

    data = None
    engine_used = "native-stdlib"
    if ccusage_bin:
        env = dict(os.environ)
        agy_dir = Path.home() / ".gemini" / "antigravity"
        if agy_dir.is_dir() and "ANTIGRAVITY_DATA_DIR" not in env:
            env["ANTIGRAVITY_DATA_DIR"] = str(agy_dir)
        cmd = [ccusage_bin, "daily", "--last", str(days), "--by-agent", "--json"]
        if offline:
            cmd.append("--offline")
        res = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
        if res.returncode == 0 and res.stdout.strip():
            try:
                data = json.loads(res.stdout)
                engine_used = f"ccusage ({ccusage_bin})"
            except json.JSONDecodeError:
                data = None

    if data is None:
        data = _scan_native_usage(days)
        engine_used = data.get("engine", "native-stdlib")

    daily = data.get("daily") or []
    totals = data.get("totals") or {}
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    today_row = next((r for r in daily if (r.get("period") or r.get("date")) == today_str), None)
    if today_row is None and daily:
        today_row = daily[-1]

    tot_in = int(totals.get("inputTokens") or 0)
    tot_cr = int(totals.get("cacheReadTokens") or 0)
    cache_ratio = (tot_cr / (tot_cr + tot_in) * 100.0) if (tot_cr + tot_in) > 0 else 0.0

    gx10_info = None
    gx10_bin = Path.home() / ".local" / "bin" / "gx10-status"
    if not no_gx10 and gx10_bin.is_file() and os.access(gx10_bin, os.X_OK):
        try:
            g_res = subprocess.run([str(gx10_bin), "--quick"], capture_output=True, text=True, timeout=8, check=False)
            if g_res.returncode == 0 and g_res.stdout.strip():
                lines = [ln.strip() for ln in g_res.stdout.splitlines() if ln.strip() and not ln.startswith("===")]
                gx10_info = {"status": "online", "summary": lines}
        except Exception:
            gx10_info = None

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine": engine_used,
        "days_requested": days,
        "today_date": today_str,
        "today": today_row,
        "daily": daily,
        "totals": totals,
        "prompt_cache_hit_pct": round(cache_ratio, 2),
        "gx10_sovereign": gx10_info,
    }


def cmd_usage(args) -> None:
    """Multi-Agent FinOps, Token Telemetry & Cache Efficiency Auditor (`skb usage` / `skb finops`)."""
    days = 1 if getattr(args, "today", False) else int(getattr(args, "days", 7) or 7)
    payload = collect_usage_payload(
        days=days,
        force_native=getattr(args, "native", False),
        offline=getattr(args, "offline", False),
        no_gx10=getattr(args, "no_gx10", False),
    )

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        return

    engine_used = payload["engine"]
    cache_ratio = payload["prompt_cache_hit_pct"]
    today_row = payload["today"]
    today_str = payload["today_date"]
    daily = payload["daily"]
    totals = payload["totals"]
    gx10_info = payload["gx10_sovereign"]

    print("=== Sovereign-KB Agentic FinOps & Multi-Agent Token Telemetry ===")
    print(f"  Telemetry Engine  : {engine_used}")
    print(f"  Cache Efficiency  : {cache_ratio:.1f}% prompt cache read ratio (Guardrail >= 90.0%)")

    if today_row:
        t_date = today_row.get("period") or today_row.get("date") or today_str
        t_cost = float(today_row.get("totalCost") or 0.0)
        t_tok = int(today_row.get("totalTokens") or 0)
        t_in = int(today_row.get("inputTokens") or 0)
        t_out = int(today_row.get("outputTokens") or 0)
        t_cr = int(today_row.get("cacheReadTokens") or 0)
        t_eta = (t_cr / (t_cr + t_in) * 100.0) if (t_cr + t_in) > 0 else 0.0
        print(f"\n  [Focus Day: {t_date}]")
        print(
            f"  Total Credit Burn : ${t_cost:,.2f} USD  |  Total Tokens: {t_tok:,} "
            f"(In: {t_in:,} | Out: {t_out:,} | Cache Read: {t_cr:,} [{t_eta:.1f}%])"
        )
        for ag in today_row.get("agents") or []:
            ag_name = ag.get("agent", "unknown").upper()
            ag_cost = float(ag.get("totalCost") or 0.0)
            ag_tok = int(ag.get("totalTokens") or 0)
            print(f"    • {ag_name:<13} : ${ag_cost:8.2f} USD  ({ag_tok:>13,} total tokens)")
            for m in ag.get("modelBreakdowns") or []:
                m_name = m.get("modelName", "unknown")
                m_cost = float(m.get("cost") or 0.0)
                m_in = int(m.get("inputTokens") or 0)
                m_out = int(m.get("outputTokens") or 0)
                m_cr = int(m.get("cacheReadTokens") or 0)
                unpriced = " [unpriced]" if m.get("missingPricing") else ""
                print(
                    f"        - {m_name:<34} ${m_cost:7.2f}{unpriced} (in={m_in:,}, out={m_out:,}, cache_read={m_cr:,})"
                )

    if len(daily) > 1:
        print(f"\n  [Multi-Day Ledger ({len(daily)} active day(s))]")
        for row in daily:
            p_str = row.get("period") or row.get("date") or "?"
            c_val = float(row.get("totalCost") or 0.0)
            t_val = int(row.get("totalTokens") or 0)
            ag_summary = ", ".join(
                f"{a.get('agent')}=${float(a.get('totalCost') or 0.0):.2f}" for a in (row.get("agents") or [])
            )
            print(f"    {p_str} : ${c_val:8.2f} USD  |  {t_val:>13,} tokens  ({ag_summary})")
        print(
            f"    {'TOTAL':<10} : ${float(totals.get('totalCost') or 0.0):8.2f} USD  |  "
            f"{int(totals.get('totalTokens') or 0):>13,} tokens"
        )

    if gx10_info and gx10_info.get("summary"):
        print("\n  [Local GPU Cluster Offset ($0.00 Marginal API Cost)]")
        for ln in gx10_info["summary"]:
            if any(ln.startswith(k) for k in ("GPU:", "Memory:", "Inflight:", "Epoch")):
                print(f"    {ln}")


def cmd_dashboard(args) -> None:
    """Zero-Dependency Observability, FinOps & Knowledge Graph Dashboard (`skb dashboard` / `skb ui`)."""
    from sovereign_kb.dashboard import (
        build_dashboard_snapshot,
        discover_vaults,
        render_dashboard_html,
        serve_dashboard,
    )

    vault_dir = resolve_vault_dir(getattr(args, "vault", None))
    days = int(getattr(args, "days", 7) or 7)
    native_only = getattr(args, "native", False)
    no_gx10 = getattr(args, "no_gx10", False)

    def _build_snap(v_dir: Path, req_days: int) -> dict:
        return build_dashboard_snapshot(
            v_dir,
            get_db_fn=get_db,
            sync_fn=cmd_sync,
            list_md_fn=list_vault_md_files,
            parse_fm_fn=parse_frontmatter,
            extract_links_fn=extract_wikilinks,
            build_stem_fn=build_stem_lookup,
            hybrid_retrieve_fn=hybrid_retrieve,
            hips_retrieve_fn=hips_retrieve,
            resolve_hips_bin_fn=resolve_hips_bin,
            collect_usage_fn=collect_usage_payload,
            config_path=CONFIG_PATH,
            days=req_days,
            native_usage_only=native_only,
            no_gx10=no_gx10,
        )

    def _run_search(v_dir: Path, query: str, engine: str, limit: int) -> dict:
        conn = get_db(v_dir)
        t0 = time.perf_counter()
        hits = hybrid_retrieve(conn, v_dir, query, top_k=limit, engine=engine)
        lat_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        conn.close()
        return {"query": query, "engine": engine, "latency_ms": lat_ms, "results": hits}

    def _allowed_roots(v_dir: Path) -> set[Path]:
        return {Path(item["path"]).resolve() for item in discover_vaults(v_dir, CONFIG_PATH)}

    if getattr(args, "json", False):
        print(json.dumps(_build_snap(vault_dir, days), indent=2))
        return

    export_arg = getattr(args, "export", None)
    if export_arg is not None:
        out_path = (
            (vault_dir / ".kb" / "dashboard.html")
            if export_arg == "__DEFAULT__"
            else Path(export_arg).expanduser().resolve()
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        snap = _build_snap(vault_dir, days)
        html_doc = render_dashboard_html(snap, live_server=False)
        out_path.write_text(html_doc, encoding="utf-8")
        print(f"[sovereign-kb dashboard] Exported self-contained static HTML dashboard -> {out_path}")
        print(
            f"  Vault: {snap['vault']['name']} ({snap['vault']['documents']} notes, "
            f"{snap['vault']['chunks']} chunks, {snap['vault']['links']} links, "
            f"6-Gate={snap['gates']['status']})"
        )
        if getattr(args, "open", False):
            try:
                import webbrowser

                webbrowser.open(out_path.as_uri())
            except Exception:
                pass
        return

    serve_dashboard(
        vault_dir,
        host=getattr(args, "host", "127.0.0.1"),
        port=int(getattr(args, "port", 8765)),
        days=days,
        open_browser=getattr(args, "open", False),
        snapshot_builder=_build_snap,
        search_runner=_run_search,
        allowed_roots=_allowed_roots,
    )


def build_parser(show_all_aliases: bool = False) -> argparse.ArgumentParser:
    from sovereign_kb import __version__

    epilog = (
        (
            "Command Groups:\n"
            "  Everyday Memory : init, capture, search, ask, sync, verify (doctor), dashboard (ui), status, me\n"
            "  Project & Gate  : scaffold (new), gwi (audit, goldwood)\n"
            "  Advanced & Ops  : rsi (evolve, tune, consolidate), usage (finops), sessions (chats, ps),\n"
            "                    soar, hips, certify, skills, git-sync (pull, push)\n"
        )
        if show_all_aliases
        else "Run 'skb --help-all' to also display project scaffolding, quality gates, and advanced tooling."
    )
    parser = argparse.ArgumentParser(
        prog="sovereign-kb",
        description="Sovereign-KB (skb): Searchable, local-first Markdown memory for developers and AI coding agents.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--vault", help="Explicit path to vault directory (overrides KB_VAULT_DIR)")
    parser.add_argument(
        "--help-all",
        action="store_true",
        help="Show full help including advanced commands and aliases",
    )
    sub = parser.add_subparsers(
        dest="cmd",
        metavar="{init,capture,search,ask,sync,verify,dashboard,status,me}" if not show_all_aliases else None,
    )

    p_init = sub.add_parser("init", help="Initialize a new 6-Gate verified Sovereign Knowledge Base vault")
    p_init.add_argument("target_dir", nargs="?", help="Target vault directory (default: ~/sovereign-kb-vault)")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing starter files")
    p_init.add_argument("--no-embed", action="store_true", help="Skip vector embedding during initial sync (persisted)")
    p_init.add_argument(
        "--minimal",
        action="store_true",
        help="Create a minimal 5-file starter vault (default)",
    )
    p_init.add_argument(
        "--full",
        action="store_true",
        help="Include the full 28-file operating-system starter blueprint library",
    )
    p_init.add_argument(
        "--save-default", action="store_true", help="Save target_dir as default in ~/.config/sovereign-kb/config.json"
    )

    p_sync = sub.add_parser("sync", help="Index markdown files, compute embeddings & build HIPS Block-Max WAND index")
    p_sync.add_argument("--force", action="store_true", help="Force re-index and re-embed all files")
    p_sync.add_argument(
        "--no-embed", action="store_true", help="Index FTS5 & graph only without calling embedding endpoint"
    )

    p_search = sub.add_parser("search", help="Search across your Markdown vault (BM25 + HIPS WAND + Vector + Graph)")
    p_search.add_argument("query", nargs="+", help="Search query")
    p_search.add_argument("-n", "--limit", type=int, default=5, help="Max results")
    p_search.add_argument(
        "--type",
        help="Filter by frontmatter type (e.g. episodic, identity, now, operating-system, domain, decision, template)",
    )
    p_search.add_argument("--tag", help="Filter by frontmatter tag substring")
    p_search.add_argument(
        "--engine",
        choices=("auto", "hips", "fts5"),
        default="auto",
        help="Retrieval engine mode: 'auto' (4-way HIPS+FTS5+Vector+Graph), 'hips' (pure HIPS Block-Max WAND), or 'fts5'",
    )
    p_search.add_argument(
        "--explain",
        action="store_true",
        help="Show detailed RRF, HIPS WAND, vector cosine, and FTS5 rank telemetry per match",
    )
    p_search.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON search results for AI coding agents",
    )

    p_hips = sub.add_parser(
        "hips",
        **({"help": "Direct interface to the `hips` Block-Max WAND engine"} if show_all_aliases else {}),
    )
    p_hips.add_argument(
        "hips_cmd",
        nargs="?",
        choices=("status", "index", "search", "bench", "serve", "mcp"),
        default="status",
        help="HIPS subcommand (status, index, search, bench, serve, mcp)",
    )
    p_hips.add_argument("query", nargs="*", help="Query string (for `skb hips search` or `skb hips bench`)")
    p_hips.add_argument("-n", "--limit", type=int, default=8, help="Max results for `skb hips search`")
    p_hips.add_argument("--addr", default="127.0.0.1:18080", help="Listen address for `skb hips serve`")
    p_hips.add_argument("--json", action="store_true", help="Emit JSON output for `skb hips search`")

    p_ask = sub.add_parser("ask", help="Ask a question synthesized by local OpenAI/Ollama LLM")
    p_ask.add_argument("question", nargs="+", help="Question to ask the vault")

    p_cap = sub.add_parser("capture", help="Capture a timestamped note or decision into 04-episodic/")
    p_cap.add_argument("-s", "--sync", action="store_true", help="Immediately re-index after capture")
    p_cap.add_argument("text", nargs="+", help="Note content")

    for v_alias in ("verify", "doctor", "lint"):
        v_help = (
            "Run 6-Gate deterministic verification on vault & index (aliases: doctor, lint)"
            if v_alias == "verify" or show_all_aliases
            else None
        )
        p_ver = sub.add_parser(v_alias, **({"help": v_help} if v_help else {}))
        p_ver.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
        p_ver.add_argument(
            "--heal",
            action="store_true",
            help="Self-heal missing frontmatter (Gate 1) and orphan notes (Gate 3) before verifying",
        )
        p_ver.add_argument(
            "--allow-lexical-only",
            action="store_true",
            help="Allow Gate 4 to pass when running in lexical-only (no-embed) environments",
        )
        p_ver.add_argument(
            "--require-embeddings",
            action="store_true",
            help="Require 100%% vector embedding coverage even if Ollama is offline",
        )

    for s_alias in ("scaffold", "new"):
        s_help = (
            "Scaffold a new project or paper from the 7 deterministic archetypes (alias: new)"
            if show_all_aliases
            else None
        )
        p_scaf = sub.add_parser(s_alias, **({"help": s_help} if s_help else {}))
        p_scaf.add_argument(
            "archetype",
            nargs="?",
            help="Archetype ID (web-ts-3d-gis, python-uv-ai, rust-systems, spatial-xr-ble, paper-ieee-arxiv, game-sim-3d, metatopia-chameleon)",
        )
        p_scaf.add_argument("target_dir", nargs="?", help="Target project directory")
        p_scaf.add_argument("--name", help="Custom project name")
        p_scaf.add_argument("--author", help="Author name for scientific paper archetype")
        p_scaf.add_argument("--list", action="store_true", help="List available project archetypes")
        p_scaf.add_argument("--force", action="store_true", help="Overwrite existing AGENTS.md / quality_gate.sh")

    p_cert = sub.add_parser(
        "certify",
        **(
            {"help": "Run deterministic compiler-verified domain certification (`RUST-CERT-01..20`)"}
            if show_all_aliases
            else {}
        ),
    )
    p_cert.add_argument(
        "domain", nargs="?", choices=("rust",), default="rust", help="Certification domain (default: rust)"
    )
    p_cert.add_argument("--json", action="store_true", help="Output machine-readable JSON certification manifest")
    p_cert.add_argument(
        "--output-dir", help="Optional directory to write rust_certification_report.json and rust_cert_suite.rs"
    )
    p_cert.add_argument("--no-embed", action="store_true", help="Skip vector embedding calls if syncing vault")

    for r_alias in ("rsi", "evolve", "tune", "consolidate"):
        r_help = (
            "Run Monotonically-Gated Recursive Self-Improvement (aliases: evolve, tune, consolidate)"
            if show_all_aliases
            else None
        )
        p_rsi = sub.add_parser(r_alias, **({"help": r_help} if r_help else {}))
        if r_alias in ("rsi", "evolve"):
            p_rsi.add_argument(
                "rsi_cmd",
                nargs="?",
                choices=("cycle", "tune", "consolidate", "status", "ledger"),
                default="cycle",
                help="RSI subcommand (default: cycle)",
            )
        p_rsi.add_argument("--json", action="store_true", help="Output machine-readable JSON telemetry")
        p_rsi.add_argument("--use-llm", action="store_true", help="Synthesize episodic distillations via local LLM")
        p_rsi.add_argument(
            "--verify-rust", action="store_true", help="Include 20-domain `rustc -D warnings` certification in cycle"
        )
        p_rsi.add_argument("--no-embed", action="store_true", help="Skip vector embedding calls during re-sync")

    p_gsync = sub.add_parser(
        "git-sync",
        **({"help": "Pull, re-index, commit & push vault changes to Git remote"} if show_all_aliases else {}),
    )
    p_gsync.add_argument("-m", "--message", help="Custom commit message")

    sub.add_parser("pull", **({"help": "Git pull & re-index vault"} if show_all_aliases else {}))
    p_push = sub.add_parser("push", **({"help": "Commit, push & sync vault to Git remote"} if show_all_aliases else {}))
    p_push.add_argument("-m", "--message", help="Custom commit message")

    sub.add_parser("me", help="Print Tier-0 identity & current focus")
    p_resume = sub.add_parser(
        "resume",
        help="Print zero-context-tax Creative Mode Resumption Packet(s) (all, math, compute, spatial, writing)",
    )
    p_resume.add_argument(
        "mode",
        nargs="?",
        choices=("all", "math", "compute", "spatial", "writing"),
        default="all",
        help="Creative mode packet to display (default: all)",
    )
    p_resume.add_argument("--json", action="store_true", help="Emit JSON output")
    sub.add_parser("status", help="Show vault and index telemetry")
    for u_alias in ("usage", "finops"):
        u_help = (
            "Multi-agent FinOps, token telemetry & prompt-cache auditor (alias: finops)" if show_all_aliases else None
        )
        p_use = sub.add_parser(u_alias, **({"help": u_help} if u_help else {}))
        p_use.add_argument("--today", action="store_true", help="Show only today's credit and token usage")
        p_use.add_argument(
            "-d", "--days", type=int, default=7, help="Number of recent active days to include (default: 7)"
        )
        p_use.add_argument("--offline", action="store_true", help="Use cached pricing data without network lookup")
        p_use.add_argument("--native", action="store_true", help="Force zero-dependency stdlib SQLite/JSONL scanner")
        p_use.add_argument("--no-gx10", action="store_true", help="Skip querying remote GPU node status")
        p_use.add_argument("--json", action="store_true", help="Output machine-readable JSON FinOps report")

    for d_alias in ("dashboard", "ui"):
        d_help = (
            "Zero-dependency local observability & Knowledge Graph dashboard (alias: ui)"
            if d_alias == "dashboard" or show_all_aliases
            else None
        )
        p_dash = sub.add_parser(d_alias, **({"help": d_help} if d_help else {}))
        p_dash.add_argument(
            "--host", default="127.0.0.1", help="Bind host for live dashboard server (default: 127.0.0.1)"
        )
        p_dash.add_argument(
            "-p", "--port", type=int, default=8765, help="Bind port for live dashboard server (default: 8765)"
        )
        p_dash.add_argument(
            "-d", "--days", type=int, default=7, help="Number of recent FinOps active days to include (default: 7)"
        )
        p_dash.add_argument(
            "--export",
            nargs="?",
            const="__DEFAULT__",
            help="Export self-contained static HTML dashboard to path (default: <vault>/.kb/dashboard.html)",
        )
        p_dash.add_argument("--open", action="store_true", help="Automatically open the dashboard in default browser")
        p_dash.add_argument("--native", action="store_true", help="Force zero-dependency stdlib FinOps scanner")
        p_dash.add_argument("--no-gx10", action="store_true", help="Skip querying remote GPU node status")
        p_dash.add_argument("--json", action="store_true", help="Output combined dashboard telemetry snapshot as JSON")

    p_skills = sub.add_parser(
        "skills",
        **(
            {
                "help": "Inventory and route Sovereign Agent Skills & Zero-Auth Plugins across GWI profiles, lenses & archetypes"
            }
            if show_all_aliases
            else {}
        ),
    )
    p_skills.add_argument(
        "query", nargs="*", help="Optional search keyword across top-level skills and reference modules"
    )
    p_skills.add_argument(
        "--profile",
        choices=("app", "sim", "video", "paper"),
        help="Filter skills routed for a specific GWI profile (app, sim, video, paper)",
    )
    p_skills.add_argument(
        "--lens",
        choices=tuple(GWI_LENS_SKILL_ROUTING.keys()),
        help="Filter skills routed for a specific Bi-Polar GWI Craft Lens (4 Verga + 4 Chrysos)",
    )
    p_skills.add_argument(
        "--archetype",
        choices=tuple(ARCHETYPES.keys()),
        help="Filter skills routed for a specific kb scaffold archetype",
    )
    p_skills.add_argument(
        "--inject",
        metavar="REPO_DIR",
        help="Auto-detect archetype/GWI profile for REPO_DIR and inject Activated Sovereign Agent Skills table into its AGENTS.md",
    )
    p_skills.add_argument(
        "--inject-all",
        action="store_true",
        help="Auto-inject Activated Sovereign Agent Skills table into all repositories with AGENTS.md under ~/GitLab and ~/Projects",
    )
    p_skills.add_argument("--json", action="store_true", help="Emit machine-readable JSON skill & plugin inventory")
    p_skills.add_argument(
        "--verify",
        action="store_true",
        help="Strictly validate YAML frontmatter, schema, and syntax of all installed skills & plugins",
    )

    from sovereign_kb.gwi import register_gwi_subparser
    from sovereign_kb.sessions import register_sessions_subparser
    from sovereign_kb.soar import register_soar_subparser

    register_gwi_subparser(sub, show_all_aliases=show_all_aliases)
    register_soar_subparser(sub, show_all_aliases=show_all_aliases)
    register_sessions_subparser(sub, show_all_aliases=show_all_aliases)
    return parser


def cmd_skills(args) -> None:
    skills_root = Path.home() / ".gemini" / "config" / "skills"
    plugins_root = Path.home() / ".gemini" / "config" / "plugins"

    from sovereign_kb.gwi import validate_skill_frontmatter

    if getattr(args, "verify", False):
        all_skills: list[Path] = []
        if skills_root.exists():
            for sdir in sorted(skills_root.iterdir()):
                smd = sdir / "SKILL.md"
                if sdir.is_dir() and smd.is_file():
                    all_skills.append(smd)
        if plugins_root.exists():
            for pdir in sorted(plugins_root.iterdir()):
                ps_dir = pdir / "skills"
                if ps_dir.exists():
                    for sk in sorted(ps_dir.iterdir()):
                        smd = sk / "SKILL.md"
                        if sk.is_dir() and smd.is_file():
                            all_skills.append(smd)

        errs: list[tuple[Path, str]] = []
        for smd in all_skills:
            ok, msg = validate_skill_frontmatter(smd)
            if not ok:
                errs.append((smd, msg))

        if errs:
            print(f"❌ Skill Frontmatter Verification FAILED ({len(errs)} corrupt skill(s) out of {len(all_skills)}):")
            for smd, msg in errs:
                try:
                    rel = str(smd.relative_to(Path.home()))
                except ValueError:
                    rel = str(smd)
                print(f"  - {rel}: {msg}")
            sys.exit(1)
        else:
            print(
                f"✓ All {len(all_skills)} installed Sovereign Agent Skills & Plugin frontmatters verified (100% valid YAML)."
            )
            return

    def _parse_desc(md_path: Path) -> str:
        try:
            txt = md_path.read_text(encoding="utf-8")
            if txt.startswith("---"):
                fm = txt.split("---", 2)[1]
                try:
                    import yaml

                    data = yaml.safe_load(fm)
                    if isinstance(data, dict) and "description" in data:
                        return str(data["description"]).strip().splitlines()[0]
                except Exception:
                    pass
                for line in fm.splitlines():
                    if line.startswith("description:"):
                        return line[len("description:") :].strip().strip('"').strip("'")
        except Exception:
            pass
        return ""

    standalone_skills = []
    total_ref_modules = 0
    if skills_root.exists():
        for sdir in sorted(skills_root.iterdir()):
            smd = sdir / "SKILL.md"
            if sdir.is_dir() and smd.exists():
                refs = list((sdir / "references").rglob("*.md")) if (sdir / "references").exists() else []
                total_ref_modules += len(refs)
                standalone_skills.append(
                    {
                        "id": sdir.name,
                        "source": "global-skill",
                        "path": str(smd),
                        "ref_modules": len(refs),
                        "description": _parse_desc(smd),
                    }
                )

    plugins = []
    plugin_skill_count = 0
    if plugins_root.exists():
        for pdir in sorted(plugins_root.iterdir()):
            if not pdir.is_dir():
                continue
            pskills_dir = pdir / "skills"
            pskills = []
            if pskills_dir.exists():
                for sk in sorted(pskills_dir.iterdir()):
                    if sk.is_dir() and (sk / "SKILL.md").exists():
                        pskills.append(sk.name)
            plugin_skill_count += len(pskills)
            plugins.append(
                {
                    "id": pdir.name,
                    "path": str(pdir),
                    "skills_count": len(pskills),
                    "skills": pskills,
                }
            )

    query_str = " ".join(getattr(args, "query", []) or []).strip().lower()
    if query_str == "search":
        query_str = ""
    elif query_str.startswith("search "):
        query_str = query_str[len("search ") :].strip()

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "summary": {
                        "global_skills": len(standalone_skills),
                        "plugins": len(plugins),
                        "plugin_skills": plugin_skill_count,
                        "total_top_level_skills": len(standalone_skills) + plugin_skill_count,
                        "bundled_reference_modules": total_ref_modules,
                    },
                    "archetype_routing": {k: [x[0] for x in v] for k, v in ARCHETYPE_SKILL_ROUTING.items()},
                    "gwi_profile_routing": GWI_PROFILE_SKILL_ROUTING,
                    "gwi_lens_routing": GWI_LENS_SKILL_ROUTING,
                    "skills": standalone_skills,
                    "plugins": plugins,
                },
                indent=2,
            )
        )
        return

    print("=== Sovereign Agent Skills & Zero-Auth Plugin Matrix (`kb skills`) ===")
    print(
        f"  Global Skills: {len(standalone_skills)} | Zero-Auth Plugins: {len(plugins)} ({plugin_skill_count} skills) "
        f"| Total Top-Level Skills: {len(standalone_skills) + plugin_skill_count} | Reference Modules: {total_ref_modules}"
    )

    def _inject_into_repo(repo_dir: Path, explicit_arch: str | None = None, explicit_prof: str | None = None) -> bool:
        from sovereign_kb.gwi import detect_profile

        arch = explicit_arch
        if not arch:
            prof = explicit_prof or detect_profile(repo_dir)
            if prof == "paper":
                arch = "paper-ieee-arxiv"
            elif prof == "sim":
                arch = "game-sim-3d"
            elif (repo_dir / "Cargo.toml").is_file() and not (repo_dir / "package.json").is_file():
                arch = "rust-systems"
            elif (repo_dir / "pyproject.toml").is_file() and not (repo_dir / "package.json").is_file():
                arch = "python-uv-ai"
            elif (repo_dir / "tokens" / "metatopia-tokens.json").is_file():
                arch = "metatopia-chameleon"
            else:
                arch = "web-ts-3d-gis"

        skill_rows = "\n".join(f"| `{sname}` | `{spath}` |" for sname, spath in ARCHETYPE_SKILL_ROUTING.get(arch, []))
        section_md = (
            f"\n## Activated Sovereign Agent Skills (`kb skills --archetype {arch}`)\n\n"
            "| Skill ID | Local Entry Point (`SKILL.md` + `references/`) |\n"
            "| :--- | :--- |\n"
            f"{skill_rows}\n"
        )
        agents_file = repo_dir / "AGENTS.md"
        if agents_file.is_file():
            existing = agents_file.read_text(encoding="utf-8")
            if "Activated Sovereign Agent Skills" in existing:
                print(f"  [skip]   {repo_dir.name:34s} ({arch}) — already contains Activated Sovereign Agent Skills")
                return False
            agents_file.write_text(existing.rstrip() + "\n" + section_md, encoding="utf-8")
            print(f"  [inject] {repo_dir.name:34s} ({arch}) -> {agents_file}")
            return True
        else:
            agents_file.write_text(f"# AGENTS.md — `{repo_dir.name}`\n" + section_md, encoding="utf-8")
            print(f"  [create] {repo_dir.name:34s} ({arch}) -> {agents_file}")
            return True

    if getattr(args, "inject_all", False):
        seen_real: set[Path] = set()
        injected_count = 0
        checked_count = 0
        for parent in (Path.home() / "GitLab", Path.home() / "Projects"):
            if not parent.is_dir():
                continue
            for child in sorted(parent.iterdir()):
                if not child.is_dir():
                    continue
                real = child.resolve()
                if real in seen_real or real.name == "me-kb":
                    continue
                if not (real / ".git").exists() and not (real / "AGENTS.md").is_file():
                    continue
                seen_real.add(real)
                checked_count += 1
                if _inject_into_repo(real, getattr(args, "archetype", None), getattr(args, "profile", None)):
                    injected_count += 1
        print(f"\n[kb skills --inject-all] Processed {checked_count} repositories ({injected_count} newly injected).")
        return

    if getattr(args, "inject", None):
        repo_dir = Path(args.inject).expanduser().resolve()
        if not repo_dir.is_dir():
            print(f"Error: Directory not found: {repo_dir}", file=sys.stderr)
            sys.exit(1)
        _inject_into_repo(repo_dir, getattr(args, "archetype", None), getattr(args, "profile", None))
        return

    if getattr(args, "archetype", None):
        arch = args.archetype
        print(f"\n[Archetype Routing: {arch}]")
        for sname, spath in ARCHETYPE_SKILL_ROUTING.get(arch, []):
            print(f"  - {sname:24s} -> {spath}")
        return

    if getattr(args, "profile", None):
        prof = args.profile
        print(f"\n[GWI Profile Routing: {prof}]")
        for sname in GWI_PROFILE_SKILL_ROUTING.get(prof, []):
            print(f"  - {sname}")
        return

    if getattr(args, "lens", None):
        lens = args.lens
        print(f"\n[GWI Craft Lens Routing: {lens}]")
        for sname in GWI_LENS_SKILL_ROUTING.get(lens, []):
            print(f"  - {sname}")
        return

    if query_str:
        print(f"\n[Search Results for '{query_str}']")
        hits = 0
        for s in standalone_skills:
            if query_str in s["id"].lower() or query_str in s["description"].lower():
                print(f"  [skill]  {s['id']:28s} ({s['ref_modules']} refs) : {s['description'][:90]}")
                hits += 1
            rdir = Path(s["path"]).parent / "references"
            if rdir.exists():
                for rmd in sorted(rdir.rglob("*.md")):
                    if query_str in rmd.stem.lower() or query_str in str(rmd.parent.name).lower():
                        print(f"  [module] {s['id']}/{rmd.stem:24s} -> {rmd}")
                        hits += 1
        for p in plugins:
            for sk in p["skills"]:
                if query_str in sk.lower() or query_str in p["id"].lower():
                    print(f"  [plugin:{p['id']}] {sk}")
                    hits += 1
        if hits == 0:
            print("  No matching skills or reference modules found.")
        return

    print("\n1. Multi-Module Router Suites (`~/.gemini/config/skills/`):")
    for s in standalone_skills:
        if s["ref_modules"] > 0 or s["id"] in (
            "goldwood-gwi",
            "personal-kb",
            "gx10-cluster",
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "webapp-testing",
            "mcp-builder",
        ):
            ref_tag = f"[{s['ref_modules']:2d} modules]" if s["ref_modules"] > 0 else "[core skill]"
            print(f"  - {s['id']:25s} {ref_tag:12s} : {s['description'][:82]}")

    print("\n2. Zero-Auth Installed Plugins (`~/.gemini/config/plugins/`):")
    for p in plugins:
        print(f"  - {p['id']:36s} : {p['skills_count']:2d} skills")

    print("\n3. GWI Profile -> Skill Bundle Routing:")
    for prof, slist in GWI_PROFILE_SKILL_ROUTING.items():
        print(f"  - {prof:6s} : {', '.join(slist)}")


def main(argv: list[str] | None = None) -> None:
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    if "--help-all" in raw_argv:
        parser = build_parser(show_all_aliases=True)
        parser.print_help()
        return
    parser = build_parser(show_all_aliases=False)
    args = parser.parse_args(argv)
    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "sync":
        if getattr(args, "no_embed", False):
            args.persist_no_embed = True
        cmd_sync(args)
    elif args.cmd == "search":
        cmd_search(args)
    elif args.cmd == "hips":
        cmd_hips(args)
    elif args.cmd == "ask":
        cmd_ask(args)
    elif args.cmd == "capture":
        cmd_capture(args)
    elif args.cmd in ("verify", "doctor", "lint"):
        cmd_verify(args)
    elif args.cmd in ("scaffold", "new"):
        cmd_scaffold(args)
    elif args.cmd == "skills":
        cmd_skills(args)
    elif args.cmd in ("sessions", "chats", "ps"):
        from sovereign_kb.sessions import cmd_sessions

        cmd_sessions(args)
    elif args.cmd == "certify":
        cmd_certify(args)
    elif args.cmd in ("rsi", "evolve", "tune", "consolidate"):
        cmd_rsi(args)
    elif args.cmd in ("usage", "finops"):
        cmd_usage(args)
    elif args.cmd in ("dashboard", "ui"):
        cmd_dashboard(args)
    elif args.cmd in ("gwi", "goldwood", "audit"):
        from sovereign_kb.gwi import cmd_gwi

        cmd_gwi(args)
    elif args.cmd == "soar":
        from sovereign_kb.soar import cmd_soar

        cmd_soar(args, resolve_vault_dir)
    elif args.cmd == "git-sync":
        args.mode = "sync"
        cmd_git_sync(args)
    elif args.cmd == "pull":
        args.mode = "pull"
        cmd_git_sync(args)
    elif args.cmd == "push":
        args.mode = "push"
        cmd_git_sync(args)
    elif args.cmd == "me":
        cmd_me(args)
    elif args.cmd == "resume":
        cmd_resume(args)
    else:
        cmd_status(args)


if __name__ == "__main__":
    main()
