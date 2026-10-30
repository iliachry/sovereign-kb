"""SQLite-Backed Procedural Memory Store (`ChunkStore`) for Soar 9.6 `chunk*` Productions."""

from __future__ import annotations

import datetime
import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Callable


class ChunkStore:
    """
    Persists and indexes learned Soar `chunk*` productions in SQLite alongside
    Sovereign-KB's declarative FTS5/Vector index (`.kb/index.db`).
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @classmethod
    def for_vault(cls, vault_dir: Path) -> ChunkStore:
        return cls(Path(vault_dir) / ".kb" / "index.db")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS soar_chunks (
                    name TEXT PRIMARY KEY,
                    source_goal TEXT NOT NULL DEFAULT '',
                    conditions_hash TEXT NOT NULL UNIQUE,
                    soar_code TEXT NOT NULL,
                    provenance_run_id TEXT NOT NULL DEFAULT '',
                    wp_id INTEGER NOT NULL DEFAULT 0,
                    commit_sha TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL DEFAULT 'general',
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def compute_conditions_hash(prod: Any, soar_code: str = "") -> str:
        cond_repr = repr(getattr(prod, "conditions", None)) + "::" + repr(getattr(prod, "actions", None))
        if cond_repr == "None::None" and soar_code:
            cond_repr = soar_code.strip()
        return hashlib.sha256(cond_repr.encode("utf-8")).hexdigest()[:24]

    def upsert_chunk(
        self,
        prod: Any,
        soar_code: str,
        source_goal: str = "",
        provenance_run_id: str = "",
        wp_id: int = 0,
        commit_sha: str = "",
        domain: str = "general",
    ) -> dict[str, Any]:
        cond_hash = self.compute_conditions_hash(prod, soar_code)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        name = str(getattr(prod, "name", None) or f"chunk*auto*{cond_hash[:8]}")
        goal = source_goal or str(getattr(prod, "source_goal", "") or getattr(prod, "documentation", "") or "")

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT name, hit_count, created_at FROM soar_chunks WHERE conditions_hash = ? OR name = ?",
                (cond_hash, name),
            ).fetchone()
            if existing:
                kept_name = str(existing["name"])
                conn.execute(
                    """
                    UPDATE soar_chunks
                    SET soar_code = ?,
                        source_goal = CASE WHEN ? != '' THEN ? ELSE source_goal END,
                        provenance_run_id = CASE WHEN ? != '' THEN ? ELSE provenance_run_id END,
                        wp_id = CASE WHEN ? > 0 THEN ? ELSE wp_id END,
                        commit_sha = CASE WHEN ? != '' THEN ? ELSE commit_sha END,
                        domain = CASE WHEN ? != 'general' THEN ? ELSE domain END,
                        updated_at = ?
                    WHERE name = ?
                    """,
                    (
                        soar_code,
                        goal,
                        goal,
                        provenance_run_id,
                        provenance_run_id,
                        int(wp_id),
                        int(wp_id),
                        commit_sha,
                        commit_sha,
                        domain,
                        domain,
                        now,
                        kept_name,
                    ),
                )
                conn.commit()
                return {
                    "name": kept_name,
                    "conditions_hash": cond_hash,
                    "created": False,
                    "hit_count": int(existing["hit_count"]),
                }

            conn.execute(
                """
                INSERT INTO soar_chunks (
                    name, source_goal, conditions_hash, soar_code,
                    provenance_run_id, wp_id, commit_sha, domain,
                    hit_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    name,
                    goal,
                    cond_hash,
                    soar_code,
                    provenance_run_id,
                    int(wp_id),
                    commit_sha,
                    domain,
                    now,
                    now,
                ),
            )
            conn.commit()
        return {"name": name, "conditions_hash": cond_hash, "created": True, "hit_count": 0}

    def upsert_chunks_from_kernel(
        self,
        kernel: Any,
        format_production_fn: Callable[[Any], str],
        is_valid_fn: Callable[[Any], bool] | None = None,
        source_goal: str = "",
        provenance_run_id: str = "",
        wp_id: int = 0,
        commit_sha: str = "",
        domain: str = "general",
    ) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        for p in kernel.productions.values():
            if not getattr(p, "is_chunk", False):
                continue
            if is_valid_fn is not None and not is_valid_fn(p):
                continue
            code = format_production_fn(p)
            stored.append(
                self.upsert_chunk(
                    prod=p,
                    soar_code=code,
                    source_goal=source_goal,
                    provenance_run_id=provenance_run_id,
                    wp_id=wp_id,
                    commit_sha=commit_sha,
                    domain=domain,
                )
            )
        return stored

    def load_chunks_into_kernel(
        self,
        kernel: Any,
        parse_soar_fn: Callable[[str], list[Any]],
        is_valid_fn: Callable[[Any], bool] | None = None,
        domain: str | None = None,
        record_hit: bool = True,
    ) -> int:
        loaded = 0
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._connect() as conn:
            if domain:
                rows = conn.execute(
                    "SELECT name, soar_code FROM soar_chunks WHERE domain IN (?, 'general') ORDER BY hit_count DESC, updated_at DESC",
                    (domain,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT name, soar_code FROM soar_chunks ORDER BY hit_count DESC, updated_at DESC"
                ).fetchall()

            loaded_names: list[str] = []
            for r in rows:
                try:
                    parsed = parse_soar_fn(str(r["soar_code"]))
                    for p in parsed:
                        if is_valid_fn is not None and not is_valid_fn(p):
                            continue
                        p.is_chunk = True
                        kernel.productions[p.name] = p
                        if p.name not in kernel.learned_chunks:
                            kernel.learned_chunks.append(p.name)
                        loaded += 1
                        loaded_names.append(str(r["name"]))
                except Exception:
                    continue

            if record_hit and loaded_names:
                for nm in loaded_names:
                    conn.execute(
                        "UPDATE soar_chunks SET hit_count = hit_count + 1, updated_at = ? WHERE name = ?",
                        (now, nm),
                    )
                conn.commit()
        return loaded

    def search_chunks(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        tokens = [t.lower() for t in query.replace("-", " ").replace("*", " ").split() if len(t) >= 2]
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM soar_chunks ORDER BY hit_count DESC, updated_at DESC").fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            haystack = f"{d['name']} {d['source_goal']} {d['domain']} {d['soar_code']}".lower()
            score = sum(1.0 for tok in tokens if tok in haystack) + 0.1 * float(d.get("hit_count", 0))
            if not tokens or score > 0:
                d["match_score"] = round(score, 3)
                results.append(d)
        results.sort(key=lambda x: (x["match_score"], x["hit_count"]), reverse=True)
        return results[:limit]

    def list_chunks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM soar_chunks ORDER BY hit_count DESC, updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
