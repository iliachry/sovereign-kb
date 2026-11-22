"""Configuration, advisory file locking, SQLite schema, and vault discovery."""

from __future__ import annotations

import array
import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

SCHEMA_VERSION = 3
STARTER_VAULT_DIR = Path(__file__).resolve().parent.parent / "starter_vault"
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


def _get_config_path() -> Path:
    return Path.home() / ".config" / "sovereign-kb" / "config.json"


def _reject_if_bundled_starter_vault(v: Path) -> None:
    from ..audit_registry import is_bundled_starter_vault_path

    if is_bundled_starter_vault_path(v):
        print(
            "[skb] Error: Bundled 'starter_vault' is a read-only package template and cannot be used as a live vault. "
            "Run 'skb init ~/notes --save-default' instead.",
            file=sys.stderr,
        )
        sys.exit(1)


def resolve_vault_dir(explicit_vault: str | None = None, must_exist: bool = True) -> Path:
    """Resolve the active vault directory via CLI flag, env var, cwd walk-up, or user config."""
    from ..audit_registry import is_bundled_starter_vault_path

    if explicit_vault:
        v = Path(explicit_vault).expanduser().resolve()
        _reject_if_bundled_starter_vault(v)
        if must_exist and not v.exists():
            print(f"[skb] Error: Vault directory '{v}' does not exist. Run 'skb init {v}' first.", file=sys.stderr)
            sys.exit(1)
        return v

    env_vault = os.environ.get("KB_VAULT_DIR")
    if env_vault:
        v = Path(env_vault).expanduser().resolve()
        _reject_if_bundled_starter_vault(v)
        if must_exist and not v.exists():
            print(f"[skb] Error: Vault directory '{v}' does not exist. Run 'skb init {v}' first.", file=sys.stderr)
            sys.exit(1)
        return v

    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if is_bundled_starter_vault_path(candidate) or (candidate / "src" / "sovereign_kb" / "__init__.py").is_file():
            continue
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
                _reject_if_bundled_starter_vault(v)
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
    if is_bundled_starter_vault_path(default_vault):
        _reject_if_bundled_starter_vault(default_vault)
    default_initialized = (default_vault / ".kb").is_dir() or (default_vault / "INDEX.md").is_file()
    is_pkg_or_starter_cwd = (
        is_bundled_starter_vault_path(cwd) or (cwd / "src" / "sovereign_kb" / "__init__.py").is_file()
    )
    if not default_initialized and not is_pkg_or_starter_cwd and cwd != Path.home().resolve() and any(cwd.glob("*.md")):
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
    if text.isascii():
        return text.lower()
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
        cached_vecs: dict[tuple[str, str], bytes] = {}
        try:
            for r in conn.execute("SELECT rel_path, content, embedding FROM chunks WHERE embedding IS NOT NULL"):
                raw_emb = r["embedding"]
                if raw_emb is None:
                    continue
                blob_emb: bytes | None = None
                if isinstance(raw_emb, (bytes, bytearray)):
                    blob_emb = bytes(raw_emb)
                elif isinstance(raw_emb, str):
                    try:
                        floats = json.loads(raw_emb)
                        if isinstance(floats, list) and len(floats) > 0:
                            blob_emb = array.array("f", floats).tobytes()
                    except Exception:
                        pass
                if blob_emb:
                    h = hashlib.sha256(r["content"].encode("utf-8")).hexdigest()
                    cached_vecs[(r["rel_path"], h)] = blob_emb
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
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _vec_cache (rel_path TEXT, sha TEXT, embedding BLOB);")
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
            title TEXT,
            doc_type TEXT,
            tags TEXT,
            heading TEXT,
            heading_path TEXT,
            start_line INTEGER,
            end_line INTEGER,
            content TEXT,
            folded_content TEXT,
            is_vector_eligible INTEGER NOT NULL DEFAULT 1,
            embedding BLOB
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            rel_path,
            title,
            doc_type,
            tags,
            heading_path,
            content,
            folded_content,
            content='chunks',
            content_rowid='id'
        );
        CREATE TABLE IF NOT EXISTS links (
            source_path TEXT,
            target_path TEXT,
            PRIMARY KEY (source_path, target_path)
        );
        """
    )


def is_doc_vector_eligible(rel_path: str, updated_date: str = "", max_episodic_days: int = 60) -> bool:
    """
    Tier-aware vector admission guardrail:
    Canonical semantic notes (00-identity, 01-now, 02-operating-system, 03-life-domains, INDEX, AGENTS)
    are always embedded.
    Episodic logs older than max_episodic_days (default 60) remain full-text (FTS5 BM25) and
    Block-Max WAND (hips) indexed but skip dense vector embedding.
    """
    if not rel_path.startswith("04-episodic/"):
        return True

    date_str = ""
    stem = Path(rel_path).stem
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", stem)
    if m:
        date_str = m.group(1)
    elif updated_date and re.match(r"^\d{4}-\d{2}-\d{2}$", updated_date):
        date_str = updated_date

    if not date_str:
        return True

    try:
        doc_d = datetime.date.fromisoformat(date_str)
        today = datetime.date.today()
        return (today - doc_d).days <= max_episodic_days
    except Exception:
        return True
