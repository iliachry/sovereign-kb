"""Closed-Loop Lyapunov-Gated Graph Curation (`skb jepa heal`)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from sovereign_kb.audit_registry import is_bundled_starter_vault_path
from sovereign_kb.cli.config_db import get_db
from sovereign_kb.cli.markdown_embed import parse_frontmatter
from sovereign_kb.cli.retrieval_notes import hybrid_retrieve
from sovereign_kb.cli.sync_hips import cmd_sync
from sovereign_kb.cli.verify import cmd_verify
from sovereign_kb.rsi import evaluate_retrieval_benchmark

from .ops import discover_gaps

_RELATED_RE = re.compile(r"^related:\s*$", re.MULTILINE)
_RELATED_EMPTY_LIST_RE = re.compile(r"^related:\s*\[\s*\]\s*$", re.MULTILINE)
_RELATED_INLINE_RE = re.compile(r"^related:\s*\S", re.MULTILINE)


def _inject_related_wikilink(md_text: str, target_no_ext: str) -> str | None:
    """Insert ``[[target_no_ext]]`` into YAML frontmatter ``related:`` list without breaking YAML."""
    if not md_text.startswith("---"):
        return None
    parts = md_text.split("\n---\n", 1)
    if len(parts) != 2:
        return None
    fm_block, body = parts
    link_line = f'  - "[[{target_no_ext}]]"'
    if f"[[{target_no_ext}]]" in fm_block or f"[[{target_no_ext}]]" in body:
        return None

    if _RELATED_RE.search(fm_block):
        new_fm = _RELATED_RE.sub(f"related:\n{link_line}", fm_block, count=1)
    elif _RELATED_EMPTY_LIST_RE.search(fm_block):
        new_fm = _RELATED_EMPTY_LIST_RE.sub(f"related:\n{link_line}", fm_block, count=1)
    elif _RELATED_INLINE_RE.search(fm_block):
        return None
    else:
        new_fm = fm_block.rstrip() + f"\nrelated:\n{link_line}"
    return new_fm + "\n---\n" + body


def heal_vault_links_with_jepa(
    vault_dir: Path,
    max_edges: int = 3,
    apply: bool = False,
) -> dict[str, Any]:
    """
    Propose (or Lyapunov-gated apply) high-confidence missing ``[[wikilink]]`` edges
    discovered by ``VaultJEPA``. Rolls back automatically if 6-Gate verify fails or
    if ``J_after < J_before``.
    """
    v_root = Path(vault_dir).expanduser().resolve()
    if is_bundled_starter_vault_path(v_root):
        raise ValueError("Refusing to mutate immutable bundled starter_vault.")

    gap_res = discover_gaps(v_root, top_k=max(max_edges * 4, 12), action="WIKILINK")
    proposed: list[dict[str, Any]] = []
    seen_sources: set[str] = set()

    for g in gap_res["gaps"]:
        src_rel = str(g["source_path"])
        tgt_rel = str(g["target_path"])
        if src_rel in seen_sources:
            continue
        src_p = v_root / src_rel
        if not src_p.is_file():
            continue
        raw = src_p.read_text(encoding="utf-8")
        fm = parse_frontmatter(raw)
        if not fm:
            continue
        tgt_no_ext = tgt_rel[:-3] if tgt_rel.endswith(".md") else tgt_rel
        if _inject_related_wikilink(raw, tgt_no_ext) is None:
            continue
        proposed.append(g)
        seen_sources.add(src_rel)
        if len(proposed) >= max(1, max_edges):
            break

    if not apply or not proposed:
        return {
            "vault_dir": str(v_root),
            "applied": False,
            "dry_run": not apply,
            "rolled_back": False,
            "proposed_edges": proposed,
        }

    # Measure baseline Lyapunov retrieval objective J_before
    conn = get_db(v_root)
    try:
        base_eval = evaluate_retrieval_benchmark(conn, v_root, hybrid_retrieve)
        j_before = float(base_eval["objective_j"])
    finally:
        conn.close()

    snapshots: dict[Path, str] = {}
    added_edges: list[dict[str, Any]] = []
    for g in proposed:
        src_p = v_root / str(g["source_path"])
        tgt_rel = str(g["target_path"])
        tgt_no_ext = tgt_rel[:-3] if tgt_rel.endswith(".md") else tgt_rel
        orig = src_p.read_text(encoding="utf-8")
        updated = _inject_related_wikilink(orig, tgt_no_ext)
        if updated is not None:
            snapshots[src_p] = orig
            src_p.write_text(updated, encoding="utf-8")
            added_edges.append(g)

    if not added_edges:
        return {
            "vault_dir": str(v_root),
            "applied": False,
            "dry_run": False,
            "rolled_back": False,
            "proposed_edges": [],
        }

    def _sync_quiet() -> None:
        cmd_sync(argparse.Namespace(vault=str(v_root), force=False, no_embed=True, quiet=True))

    _sync_quiet()
    import contextlib
    import io

    gates_ok = True
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cmd_verify(
                argparse.Namespace(
                    vault=str(v_root),
                    json=True,
                    heal=False,
                    allow_lexical_only=True,
                    require_embeddings=False,
                ),
                no_auto_sync=True,
            )
    except SystemExit as exc:
        gates_ok = exc.code in (0, None)

    conn2 = get_db(v_root)
    try:
        after_eval = evaluate_retrieval_benchmark(conn2, v_root, hybrid_retrieve)
        j_after = float(after_eval["objective_j"])
    finally:
        conn2.close()

    if not gates_ok or j_after < j_before - 1e-6:
        for p, orig_txt in snapshots.items():
            p.write_text(orig_txt, encoding="utf-8")
        _sync_quiet()
        return {
            "vault_dir": str(v_root),
            "applied": False,
            "dry_run": False,
            "rolled_back": True,
            "gates_ok": gates_ok,
            "j_before": round(j_before, 6),
            "j_after": round(j_after, 6),
            "delta_j": round(j_after - j_before, 6),
            "proposed_edges": added_edges,
        }

    return {
        "vault_dir": str(v_root),
        "applied": True,
        "dry_run": False,
        "rolled_back": False,
        "gates_ok": gates_ok,
        "j_before": round(j_before, 6),
        "j_after": round(j_after, 6),
        "delta_j": round(j_after - j_before, 6),
        "added_edges": added_edges,
    }
