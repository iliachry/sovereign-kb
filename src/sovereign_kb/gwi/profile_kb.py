"""Goldwood Knowledge Base (`kb`) profile and Spectral Laplacian Fiedler solver."""

from __future__ import annotations

import collections
import datetime
import math
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import (
    ARCHETYPE_WEIGHTS,
    DEFAULT_K,
    DEFAULT_THETA,
    aggregate_tier_pair,
    compute_goldwood_index,
    compute_kolmogorov_elegance,
    compute_snrc,
)

from .utils import _SECRET_LEAK_RE, validate_skill_frontmatter


def compute_graph_fiedler_value(n_nodes: int, adj: list[set[int]]) -> float:
    """
    Compute the normalized Fiedler value (algebraic connectivity lambda_2 in [0, 2])
    of the symmetric normalized graph Laplacian L_sym = I - D^{-1/2} A D^{-1/2}.
    Returns 0.0 immediately if the graph is disconnected or contains isolated vertices.
    Uses peripheral-BFS-seeded Lanczos tridiagonalization with full reorthogonalization
    orthogonal to v_0 = D^{1/2} 1 / ||D^{1/2} 1||_2 followed by 56-step Sturm sequence bisection.
    """
    import collections

    if n_nodes <= 1:
        return 1.0
    if any(len(a) == 0 for a in adj):
        return 0.0

    # 1. Exact BFS connectivity check & peripheral node discovery
    visited = {0}
    dq: collections.deque[int] = collections.deque([0])
    last_node = 0
    while dq:
        cur = dq.popleft()
        last_node = cur
        for nxt in adj[cur]:
            if nxt not in visited:
                visited.add(nxt)
                dq.append(nxt)
    if len(visited) < n_nodes:
        return 0.0

    # 2. Peripheral BFS hop distances from `last_node` for topological Fiedler seeding
    dist = [-1] * n_nodes
    dist[last_node] = 0
    dq.append(last_node)
    while dq:
        cur = dq.popleft()
        for nxt in adj[cur]:
            if dist[nxt] < 0:
                dist[nxt] = dist[cur] + 1
                dq.append(nxt)
    max_d = max(dist) or 1

    deg = [float(len(a)) for a in adj]
    inv_sqrt_d = [1.0 / math.sqrt(d) for d in deg]
    v0 = [math.sqrt(d) for d in deg]
    norm0 = math.sqrt(sum(x * x for x in v0)) or 1.0
    v0 = [x / norm0 for x in v0]

    def _matvec(x: list[float]) -> list[float]:
        y = [0.0] * n_nodes
        for i in range(n_nodes):
            s = sum(inv_sqrt_d[j] * x[j] for j in adj[i])
            y[i] = x[i] - inv_sqrt_d[i] * s
        return y

    seeds = [
        [math.cos(math.pi * dist[i] / max_d) * v0[i] for i in range(n_nodes)],
        [math.sin((i + 1) * 1.37) for i in range(n_nodes)],
    ]
    best_lambda2 = 2.0
    m_steps = min(n_nodes - 1, 160)
    for seed in seeds:
        dot0 = sum(seed[i] * v0[i] for i in range(n_nodes))
        q = [seed[i] - dot0 * v0[i] for i in range(n_nodes)]
        nq = math.sqrt(sum(v * v for v in q))
        if nq < 1e-12:
            continue
        q = [v / nq for v in q]
        q_basis: list[list[float]] = [q]
        alphas: list[float] = []
        betas: list[float] = []
        for step_k in range(m_steps):
            z = _matvec(q_basis[step_k])
            alpha = sum(q_basis[step_k][i] * z[i] for i in range(n_nodes))
            alphas.append(alpha)
            # Two Gram-Schmidt passes ("twice is enough"): a single pass lets the null vector v_0 leak back
            # into Q_k on irregular graphs (|Q.v_0| ~ 1e-1 by k=30), pulling Ritz values spuriously toward 0.
            for _pass in range(2):
                dot0 = sum(z[i] * v0[i] for i in range(n_nodes))
                for i in range(n_nodes):
                    z[i] -= dot0 * v0[i]
                for prev_q in q_basis:
                    dp = sum(z[i] * prev_q[i] for i in range(n_nodes))
                    if abs(dp) > 1e-17:
                        for i in range(n_nodes):
                            z[i] -= dp * prev_q[i]
            beta = math.sqrt(sum(v * v for v in z))
            if beta < 1e-11 or step_k == m_steps - 1:
                break
            betas.append(beta)
            q_basis.append([v / beta for v in z])

        m_dim = len(alphas)
        lo, hi = 0.0, 2.0
        for _ in range(56):
            mid = 0.5 * (lo + hi)
            cnt = 0
            d = alphas[0] - mid
            if d < 0.0:
                cnt += 1
            elif abs(d) < 1e-18:
                d = -1e-18
            for idx in range(1, m_dim):
                d = (alphas[idx] - mid) - (betas[idx - 1] * betas[idx - 1]) / d
                if d < 0.0:
                    cnt += 1
                elif abs(d) < 1e-18:
                    d = -1e-18
            if cnt >= 1:
                hi = mid
            else:
                lo = mid
        best_lambda2 = min(best_lambda2, 0.5 * (lo + hi))

    return max(1e-12, min(2.0, best_lambda2))


def evaluate_kb_profile(
    target_dir: Path,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
) -> dict[str, Any]:
    """
    Evaluate a Sovereign Knowledge Base, Second Brain, or Semantic Graph Vault (`kb` profile)
    across 8 Epistemic & Structural Dimensions and the 4 Ontological Tiers (`S_sub`, `S_perc`, `S_act`, `S_exist`).
    Unifies the 6-Gate Vault Doctor (`kb verify`) with continuous spectral graph theory (Fiedler algebraic
    connectivity lambda_2, LCC & 2-hop hub reachability), hybrid retrieval coverage, and RSI provenance.
    """
    import collections

    from sovereign_kb.cli import (
        _find_frontmatter_end,
        _is_vault_no_embed,
        build_stem_lookup,
        extract_wikilinks,
        fold_accents,
        list_vault_md_files,
        parse_frontmatter,
    )
    from sovereign_kb.rsi import verify_ledger_chain

    root = (target_dir if target_dir.is_dir() else target_dir.parent).resolve()
    blockers: list[dict[str, str]] = []
    notes: list[str] = []

    md_files = list_vault_md_files(root)
    if not md_files:
        calc = compute_goldwood_index(
            [4.0, 4.0, 4.0, 4.0],
            weights=ARCHETYPE_WEIGHTS["kb"],
            theta=theta,
            k=k,
            conscience_clean=False,
        )
        return {
            "profile": "kb",
            "target": str(root),
            "project_name": root.name,
            "dimensions": [],
            "tiers": {"S_sub": 4.0, "S_perc": 4.0, "S_act": 4.0, "S_exist": 4.0},
            "calculation": calc,
            "blockers": [
                {
                    "id": "GWI-KB-00",
                    "severity": "P0",
                    "dimension": "Knowledge Base Vault",
                    "message": f"No Markdown notes found in vault {root}.",
                }
            ],
            "notes": [],
        }

    stem_lookup = build_stem_lookup(root, md_files)
    rel_paths = [str(p.relative_to(root)) for p in md_files]
    node_idx = {rel: i for i, rel in enumerate(rel_paths)}
    n_notes = len(md_files)

    # 1. Scan notes for Frontmatter Schema (F_schema), Prose/Math Hygiene (V_prose),
    #    Section Structure (A_struct), Conflict Markers (S_index), Secrets (R_prov) & Wikilinks (U_links)
    yaml_error_files: list[str] = []
    missing_fm_files: list[str] = []
    bad_date_files: list[str] = []
    schema_governed_count = 0
    valid_fm_count = 0
    rich_meta_count = 0

    unclosed_fence_files: list[str] = []
    unclosed_math_files: list[str] = []
    conflict_marker_files: list[str] = []
    stub_note_files: list[str] = []
    secret_leak_files: list[str] = []
    total_headings = 0

    inbound_counts: dict[str, int] = {rel: 0 for rel in rel_paths}
    adj_undirected: list[set[int]] = [set() for _ in range(n_notes)]
    broken_links: list[str] = []
    valid_links_count = 0
    disk_texts: dict[str, str] = {}

    for i, p in enumerate(md_files):
        rel = rel_paths[i]
        text = p.read_text(encoding="utf-8", errors="replace")
        disk_texts[rel] = text
        meta = parse_frontmatter(text)
        fm_end = _find_frontmatter_end(text)
        raw_fm = text[:fm_end] if fm_end > 0 else ""
        body = text[fm_end:] if fm_end > 0 else text

        if rel not in ("AGENTS.md", "README.md"):
            schema_governed_count += 1
            if meta.get("yaml_error"):
                yaml_error_files.append(f"{rel} ({meta['yaml_error']})")
            missing_keys: list[str] = []
            for req in ("title", "type", "updated", "tags"):
                m_kv = re.search(rf"(?m)^{req}\s*:(.*)$", raw_fm)
                if not m_kv:
                    missing_keys.append(req)
                elif req == "tags":
                    if not meta.get("tags_list"):
                        missing_keys.append(req)
                else:
                    raw_val = m_kv.group(1).split("#", 1)[0].strip().strip("\"'").strip()
                    if not raw_val or not str(meta.get(req) or "").strip():
                        missing_keys.append(req)
            upd_str = str(meta.get("updated") or "").strip()
            valid_iso_date = False
            if upd_str and re.match(r"^\d{4}-\d{2}-\d{2}$", upd_str):
                try:
                    datetime.date.fromisoformat(upd_str)
                    valid_iso_date = True
                except ValueError:
                    valid_iso_date = False
            if upd_str and not valid_iso_date:
                bad_date_files.append(f"{rel} (updated='{upd_str}')")
                if "updated" not in missing_keys:
                    missing_keys.append("updated(invalid-date)")
            if missing_keys:
                missing_fm_files.append(f"{rel} (missing/invalid {','.join(missing_keys)})")
            if not meta.get("yaml_error") and not missing_keys:
                valid_fm_count += 1
            raw_tags = meta.get("tags_list")
            tags_list: list[Any] = raw_tags if isinstance(raw_tags, list) else []
            raw_rel = meta.get("related")
            rel_list: list[Any] = raw_rel if isinstance(raw_rel, list) else []
            if len(tags_list) >= 2 or len(rel_list) >= 1 or rel == "INDEX.md":
                rich_meta_count += 1

        # Prose, CommonMark code fences, display math & git conflict markers
        fence_char: str | None = None
        fence_len = 0
        in_display_math = False
        note_headings = 0
        prose_lines_outside_fences: list[str] = []
        note_conflict_open_close: list[int] = []
        note_conflict_eq: list[int] = []

        for line_no, raw_line in enumerate(text.splitlines(), 1):
            if fence_char is None:
                m_open = re.match(r"^\s*(`{3,}|~{3,})(.*)$", raw_line)
                if m_open and not (m_open.group(1)[0] == "`" and "`" in m_open.group(2)):
                    fence_char = m_open.group(1)[0]
                    fence_len = len(m_open.group(1))
                    continue
            else:
                m_close = re.match(r"^\s*(`{3,}|~{3,})\s*$", raw_line)
                if m_close and m_close.group(1)[0] == fence_char and len(m_close.group(1)) >= fence_len:
                    fence_char = None
                    fence_len = 0
                continue

            if re.match(r"^(?:<{7} |>{7} )", raw_line):
                note_conflict_open_close.append(line_no)
            elif re.match(r"^={7}$", raw_line):
                note_conflict_eq.append(line_no)

            if re.match(r"^#{1,6}\s+\S", raw_line):
                note_headings += 1
            no_inline_code = re.sub(r"`[^`]+`", "", raw_line)
            dd_count = len(re.findall(r"(?<!\\)\$\$", no_inline_code))
            if dd_count % 2 == 1:
                in_display_math = not in_display_math

        if note_conflict_open_close:
            for c_ln in sorted(note_conflict_open_close + note_conflict_eq):
                conflict_marker_files.append(f"{rel}:{c_ln}")

        # Count true body prose words (excluding YAML frontmatter and fenced code blocks)
        b_fence_char: str | None = None
        b_fence_len = 0
        for b_line in body.splitlines():
            if b_fence_char is None:
                m_bopen = re.match(r"^\s*(`{3,}|~{3,})(.*)$", b_line)
                if m_bopen and not (m_bopen.group(1)[0] == "`" and "`" in m_bopen.group(2)):
                    b_fence_char = m_bopen.group(1)[0]
                    b_fence_len = len(m_bopen.group(1))
                    continue
                prose_lines_outside_fences.append(b_line)
            else:
                m_bclose = re.match(r"^\s*(`{3,}|~{3,})\s*$", b_line)
                if m_bclose and m_bclose.group(1)[0] == b_fence_char and len(m_bclose.group(1)) >= b_fence_len:
                    b_fence_char = None
                    b_fence_len = 0
        body_words = re.findall(r"\b\w+\b", "\n".join(prose_lines_outside_fences))
        min_words = 10 if rel in ("INDEX.md", "AGENTS.md", "README.md") else 20

        total_headings += note_headings
        if fence_char is not None:
            unclosed_fence_files.append(rel)
        if in_display_math:
            unclosed_math_files.append(rel)
        if len(body.strip()) == 0 or len(body_words) < min_words:
            stub_note_files.append(rel)
        if _SECRET_LEAK_RE.search(text):
            secret_leak_files.append(rel)

        # Wikilinks & graph adjacency
        for target_rel in extract_wikilinks(text, stem_lookup, rel):
            if target_rel not in inbound_counts:
                broken_links.append(f"{rel} -> [[{target_rel}]]")
            else:
                valid_links_count += 1
                if target_rel != rel:
                    inbound_counts[target_rel] += 1
                    j = node_idx[target_rel]
                    adj_undirected[i].add(j)
                    adj_undirected[j].add(i)

    # Dimension 1: F_schema (YAML Frontmatter & Ontological Schema Compliance — Gate 1)
    r_fm = valid_fm_count / max(1, schema_governed_count)
    r_rich = rich_meta_count / max(1, schema_governed_count)
    s_schema = (
        5.0
        + 4.4 * (r_fm * r_fm)
        + 0.4 * r_rich
        - 2.5 * len(yaml_error_files)
        - 1.2 * len(missing_fm_files)
        - 0.4 * len(bad_date_files)
    )
    if yaml_error_files or missing_fm_files:
        s_schema = min(s_schema, 6.5)
        issues_preview = "; ".join((yaml_error_files + missing_fm_files)[:4])
        blockers.append(
            {
                "id": "GWI-KB-S01",
                "severity": "P0",
                "dimension": "F_schema (Frontmatter Schema — Gate 1)",
                "message": f"Frontmatter schema violation in {len(yaml_error_files) + len(missing_fm_files)} note(s): {issues_preview}",
            }
        )
    else:
        notes.append(
            f"Gate 1 (Frontmatter Schema) verified across {schema_governed_count}/{schema_governed_count} governed note(s)."
        )

    # Dimension 2: S_index (SQLite FTS5, HIPS WAND, Vector Coverage & Git Binary Hygiene — Gates 4 & 5)
    bloat_files: list[str] = []
    if (root / ".git").exists():
        git_ls = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        for tracked in git_ls:
            if (
                tracked.endswith((".mp4", ".mov", ".pyc", ".DS_Store"))
                or "__pycache__" in tracked
                or tracked.startswith(".kb/index.db")
            ):
                bloat_files.append(tracked)

    db_path = root / ".kb" / "index.db"
    hips_dir = root / ".kb" / "hips-index"
    hips_ready = hips_dir.is_dir() and any(hips_dir.iterdir())
    allow_lexical = _is_vault_no_embed(root, False)
    db_doc_count = 0
    db_chunk_count = 0
    db_eligible_vecs = 0
    db_vec_count = 0
    indexed_rel_paths: set[str] = set()
    fresh_doc_paths: set[str] = set()
    fts_ok = False

    if db_path.is_file():
        try:
            ro_uri = f"{db_path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(ro_uri, uri=True)
            try:
                doc_rows = conn.execute("SELECT rel_path, content, mtime FROM documents").fetchall()
                for rp_raw, d_content, _d_mtime in doc_rows:
                    rp = str(rp_raw)
                    indexed_rel_paths.add(rp)
                    if (
                        rp in disk_texts
                        and isinstance(d_content, str)
                        and d_content != ""
                        and d_content == disk_texts[rp]
                    ):
                        fresh_doc_paths.add(rp)
                for c_rp_raw, c_body in conn.execute("SELECT rel_path, content FROM chunks").fetchall():
                    c_rp = str(c_rp_raw)
                    if c_rp in fresh_doc_paths and (not isinstance(c_body, str) or c_body not in disk_texts[c_rp]):
                        fresh_doc_paths.discard(c_rp)
                db_doc_count = len(indexed_rel_paths)
                db_chunk_count = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
                joined_chunks = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM chunks c JOIN documents d ON c.rel_path = d.rel_path"
                    ).fetchone()[0]
                )
                try:
                    db_eligible_vecs = int(
                        conn.execute("SELECT COUNT(*) FROM chunks WHERE is_vector_eligible = 1").fetchone()[0]
                    )
                    db_vec_count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM chunks WHERE is_vector_eligible = 1 AND embedding IS NOT NULL"
                        ).fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    db_eligible_vecs = db_chunk_count
                    db_vec_count = int(
                        conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
                    )
                fts_docsize = int(conn.execute("SELECT COUNT(*) FROM chunks_fts_docsize").fetchone()[0])
                fts_data_rows = int(conn.execute("SELECT COUNT(*) FROM chunks_fts_data").fetchone()[0])
                fts_probe_ok = (
                    db_chunk_count > 0
                    and joined_chunks == db_chunk_count
                    and fts_docsize == db_chunk_count
                    and fts_data_rows >= 2
                )
                if fts_probe_ok:
                    for cid, ctext in conn.execute("SELECT id, content FROM chunks").fetchall():
                        folded_probe = fold_accents(str(ctext or ""))
                        m_tok = re.search(
                            r"(?<![\w])(?!(?:AND|OR|NOT|NEAR)(?![\w]))[A-Za-z0-9]{3,}(?![\w])", folded_probe
                        )
                        if m_tok:
                            hit = conn.execute(
                                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid = ?",
                                (f'"{m_tok.group(0)}"', int(cid)),
                            ).fetchone()
                            if hit is None:
                                fts_probe_ok = False
                                break
                fts_ok = fts_probe_ok
            finally:
                conn.close()
        except Exception:
            fts_ok = False

    expected_rel_set = set(rel_paths)
    exact_path_sync = (indexed_rel_paths == expected_rel_set) and (fresh_doc_paths == expected_rel_set)
    overlap_count = len(fresh_doc_paths & expected_rel_set)
    r_doc_sync = (
        overlap_count / max(1, len(expected_rel_set | indexed_rel_paths))
        if (db_path.is_file() and db_chunk_count > 0 and fts_ok)
        else 0.0
    )
    r_vec = 1.0 if allow_lexical else (db_vec_count / max(1, db_eligible_vecs) if db_eligible_vecs > 0 else 0.0)
    s_index = (
        5.5
        + 2.2 * r_doc_sync
        + 1.4 * r_vec
        + (0.4 if hips_ready else 0.0)
        + (0.3 if fts_ok else 0.0)
        - 3.5 * len(conflict_marker_files)
        - 3.0 * len(bloat_files)
    )
    if conflict_marker_files:
        s_index = min(s_index, 6.2)
        blockers.append(
            {
                "id": "GWI-KB-S02",
                "severity": "P0",
                "dimension": "S_index (Git Conflict Hygiene — Gate 5)",
                "message": f"Unresolved Git conflict marker(s) in: {', '.join(conflict_marker_files[:4])}",
            }
        )
    if bloat_files:
        s_index = min(s_index, 6.4)
        blockers.append(
            {
                "id": "GWI-KB-S03",
                "severity": "P0",
                "dimension": "S_index (Git Binary Hygiene — Gate 5)",
                "message": f"Tracked binary/database artifact(s) in Git index: {', '.join(bloat_files[:4])}",
            }
        )
    if not db_path.is_file() or not exact_path_sync or db_chunk_count == 0 or not fts_ok:
        s_index = min(s_index, 6.5)
        blockers.append(
            {
                "id": "GWI-KB-S04",
                "severity": "P0",
                "dimension": "S_index (Hybrid Index Sync — Gate 4)",
                "message": (
                    f"Vault index `.kb/index.db` out of sync or corrupt "
                    f"(indexed_docs={db_doc_count}/{n_notes}, exact_path_sync={exact_path_sync}, "
                    f"chunks={db_chunk_count}, fts_ok={fts_ok}); run `kb sync`."
                ),
            }
        )
    elif not allow_lexical and db_vec_count < db_eligible_vecs:
        vec_sev = "P0" if r_vec < 0.85 else "P1"
        if vec_sev == "P0":
            s_index = min(s_index, 6.5)
        else:
            s_index -= 2.0 * (1.0 - r_vec)
        blockers.append(
            {
                "id": "GWI-KB-S05",
                "severity": vec_sev,
                "dimension": "S_index (Vector Embedding Coverage — Gate 4)",
                "message": f"Incomplete vector embeddings ({db_vec_count}/{db_eligible_vecs} eligible chunks, r_vec={r_vec:.3f}); run `kb sync`.",
            }
        )
    else:
        notes.append(
            f"Gate 4 (Hybrid Index) verified: {db_doc_count} docs, {db_chunk_count} chunks, "
            f"{db_vec_count}/{db_eligible_vecs} vectors, HIPS WAND={'OK' if hips_ready else 'N/A'}."
        )

    s_schema = round(max(0.0, min(9.8, s_schema)), 2)
    s_index = round(max(0.0, min(9.8, s_index)), 2)

    # Dimension 3: V_prose (Epistemic Prose, KaTeX Math Delimiters & Code-Fence Parity)
    clean_notes_count = n_notes - len(set(unclosed_fence_files) | set(unclosed_math_files))
    r_clean_prose = clean_notes_count / max(1, n_notes)
    s_prose = (
        5.0 + 4.7 * (r_clean_prose * r_clean_prose) - 3.0 * len(unclosed_fence_files) - 2.0 * len(unclosed_math_files)
    )
    if unclosed_fence_files:
        s_prose = min(s_prose, 6.4)
        blockers.append(
            {
                "id": "GWI-KB-P01",
                "severity": "P0",
                "dimension": "V_prose (Code-Fence Parity)",
                "message": f"Unclosed Markdown code fence (` ``` `) in: {', '.join(unclosed_fence_files[:4])}",
            }
        )
    if unclosed_math_files:
        s_prose = min(s_prose, 6.5)
        blockers.append(
            {
                "id": "GWI-KB-P02",
                "severity": "P0",
                "dimension": "V_prose (Display Math Parity)",
                "message": f"Unclosed display math block (`$$`) in: {', '.join(unclosed_math_files[:4])}",
            }
        )

    # Dimension 4: A_struct (Note Architecture, Section Granularity & Stub Freedom)
    r_non_stub = (n_notes - len(stub_note_files)) / max(1, n_notes)
    avg_headings = total_headings / max(1, n_notes)
    q_gran = min(1.0, avg_headings / 2.5)
    s_struct = 5.5 + 3.4 * (r_non_stub * r_non_stub) + 0.8 * q_gran - 1.2 * len(stub_note_files)
    if stub_note_files:
        sev = "P0" if len(stub_note_files) / max(1, n_notes) > 0.20 else "P1"
        if sev == "P0":
            s_struct = min(s_struct, 6.5)
        blockers.append(
            {
                "id": "GWI-KB-P03",
                "severity": sev,
                "dimension": "A_struct (Note Granularity & Stub Freedom)",
                "message": f"{len(stub_note_files)} stub note(s) (< 20 prose words in body): {', '.join(stub_note_files[:4])}",
            }
        )

    s_prose = round(max(0.0, min(9.7, s_prose)), 2)
    s_struct = round(max(0.0, min(9.7, s_struct)), 2)

    # Dimension 5: U_links (Wikilink Closure, Citation Density & Zero Broken Edges — Gate 2)
    avg_degree = valid_links_count / max(1, n_notes)
    s_links = 7.2 + 2.6 * min(1.0, avg_degree / 3.5) - 3.2 * len(broken_links)
    if broken_links:
        s_links = min(s_links, 6.2)
        blockers.append(
            {
                "id": "GWI-KB-A01",
                "severity": "P0",
                "dimension": "U_links (Wikilink Integrity — Gate 2)",
                "message": f"{len(broken_links)} broken [[wikilink]] reference(s): {', '.join(broken_links[:4])}",
            }
        )
    else:
        notes.append(
            f"Gate 2 (Wikilink Integrity) verified: {valid_links_count} valid edge(s) across {n_notes} notes (avg={avg_degree:.2f} links/note, 0 broken)."
        )

    # Dimension 6: D_topo (Graph Topology, Algebraic Connectivity lambda_2 & Orphan Freedom — Gate 3)
    orphan_files = [
        rel for rel, cnt in inbound_counts.items() if rel not in ("INDEX.md", "AGENTS.md", "README.md") and cnt == 0
    ]
    hub_idx = node_idx.get("INDEX.md")
    if hub_idx is None:
        hub_idx = max(range(n_notes), key=lambda idx_k: len(adj_undirected[idx_k])) if n_notes > 0 else 0

    bfs_hops: dict[int, int] = {hub_idx: 0}
    bfs_q: collections.deque[int] = collections.deque([hub_idx])
    while bfs_q:
        cur = bfs_q.popleft()
        for nxt in adj_undirected[cur]:
            if nxt not in bfs_hops:
                bfs_hops[nxt] = bfs_hops[cur] + 1
                bfs_q.append(nxt)

    # Compute exact connected components across all vertices to measure true LCC and island count
    comp_visited: set[int] = set()
    comp_sizes: list[int] = []
    for start_v in range(n_notes):
        if start_v in comp_visited:
            continue
        comp_visited.add(start_v)
        c_q: collections.deque[int] = collections.deque([start_v])
        c_sz = 0
        while c_q:
            u = c_q.popleft()
            c_sz += 1
            for w in adj_undirected[u]:
                if w not in comp_visited:
                    comp_visited.add(w)
                    c_q.append(w)
        comp_sizes.append(c_sz)

    num_components = len(comp_sizes)
    max_comp_size = max(comp_sizes) if comp_sizes else 0
    lcc_ratio = max_comp_size / max(1, n_notes)
    hub_reach_ratio = len(bfs_hops) / max(1, n_notes)
    reach2_ratio = sum(1 for h in bfs_hops.values() if h <= 2) / max(1, n_notes)
    fiedler_lambda2 = compute_graph_fiedler_value(n_notes, adj_undirected)

    s_topo = (
        5.0
        + 2.0 * hub_reach_ratio
        + 1.4 * reach2_ratio
        + 1.4 * min(1.0, fiedler_lambda2 / 0.15)
        - 3.0 * len(orphan_files)
    )
    if orphan_files:
        s_topo = min(s_topo, 6.4)
        blockers.append(
            {
                "id": "GWI-KB-A02",
                "severity": "P0",
                "dimension": "D_topo (Graph Connectivity — Gate 3)",
                "message": f"{len(orphan_files)} orphan note(s) with 0 inbound wikilinks: {', '.join(orphan_files[:4])}",
            }
        )
    if n_notes > 1 and (num_components > 1 or len(bfs_hops) < n_notes):
        s_topo = min(s_topo, 6.5)
        disconnected = [rel_paths[i] for i in range(n_notes) if i not in bfs_hops]
        blockers.append(
            {
                "id": "GWI-KB-A03",
                "severity": "P0",
                "dimension": "D_topo (Spectral Connectivity & Island Detection)",
                "message": (
                    f"Disconnected knowledge island(s) detected: components={num_components}, "
                    f"LCC={max_comp_size}/{n_notes}, hub_reach={len(bfs_hops)}/{n_notes} "
                    f"(lambda_2={fiedler_lambda2:.6f}, unreachable={', '.join(disconnected[:4])})."
                ),
            }
        )
    if not orphan_files and (n_notes == 1 or num_components == 1):
        notes.append(
            f"Gate 3 (Graph Topology) verified: LCC={max_comp_size}/{n_notes} (100%), "
            f"2-hop hub reachability={reach2_ratio * 100:.1f}%, Fiedler lambda_2={fiedler_lambda2:.4f}."
        )

    s_links = round(max(0.0, min(9.8, s_links)), 2)
    s_topo = round(max(0.0, min(9.8, s_topo)), 2)

    # Dimension 7: B_tier0 (Tier-0 Sovereign Taxonomy, Episodic Freshness & RSI Ledger Hash Chain)
    canonical_pillars = (
        (root / "INDEX.md").is_file(),
        (root / "00-identity").is_dir(),
        (root / "01-now").is_dir(),
        (root / "02-operating-system").is_dir(),
        (root / "03-life-domains").is_dir(),
        (root / "04-episodic").is_dir(),
    )
    r_pillars = sum(1 for ok in canonical_pillars if ok) / len(canonical_pillars)
    ep_dir = root / "04-episodic"
    ep_logs = sorted(ep_dir.glob("*-log.md")) if ep_dir.is_dir() else []
    ep_valid = bool(ep_logs and all(re.match(r"^\d{4}-\d{2}-\d{2}-log\.md$", p.name) for p in ep_logs))

    rsi_chain_ok = True
    if (root / ".kb" / "rsi-ledger.jsonl").is_file():
        rsi_chain_ok, _ = verify_ledger_chain(root)

    s_tier0 = 6.0 + 3.2 * r_pillars + (0.5 if ep_valid else 0.0) - (4.0 if not rsi_chain_ok else 0.0)
    if not rsi_chain_ok:
        s_tier0 = min(s_tier0, 6.0)
        blockers.append(
            {
                "id": "GWI-KB-E01",
                "severity": "P0",
                "dimension": "B_tier0 (RSI Cryptographic Ledger Chain — Gate 4)",
                "message": "Tampered or broken SHA-256 hash chain in `.kb/rsi-ledger.jsonl`.",
            }
        )

    # Dimension 8: R_prov (Ground-Truth Provenance, Agent Skills Integrity & Conscience — Gate 6)
    skills_to_check: list[Path] = sorted(
        p for p in root.rglob("SKILL.md") if not any(part.startswith(".") for part in p.relative_to(root).parts)
    )
    if root.name == "me-kb":
        skills_root = Path.home() / ".gemini" / "config" / "skills"
        plugins_root = Path.home() / ".gemini" / "config" / "plugins"
        if skills_root.exists():
            for sdir in sorted(skills_root.iterdir()):
                smd = sdir / "SKILL.md"
                if sdir.is_dir() and smd.is_file():
                    skills_to_check.append(smd)
        if plugins_root.exists():
            for pdir in sorted(plugins_root.iterdir()):
                ps_dir = pdir / "skills"
                if ps_dir.exists():
                    for sk in sorted(ps_dir.iterdir()):
                        smd = sk / "SKILL.md"
                        if sk.is_dir() and smd.is_file():
                            skills_to_check.append(smd)

    bad_skills: list[str] = []
    for smd in skills_to_check:
        ok, err = validate_skill_frontmatter(smd)
        if not ok:
            bad_skills.append(f"{smd.parent.name}/SKILL.md ({err})")

    s_prov = 9.6 - 3.5 * len(secret_leak_files) - 2.5 * len(bad_skills)
    if secret_leak_files:
        s_prov = min(s_prov, 6.0)
        blockers.append(
            {
                "id": "GWI-KB-E02",
                "severity": "P0",
                "dimension": "R_prov (Secret Hygiene — Gate 5)",
                "message": f"Leaked credential pattern detected in vault note(s): {', '.join(secret_leak_files[:4])}",
            }
        )
    if bad_skills:
        s_prov = min(s_prov, 6.4)
        blockers.append(
            {
                "id": "GWI-KB-E03",
                "severity": "P0",
                "dimension": "R_prov (Agent Skills Frontmatter — Gate 6)",
                "message": f"{len(bad_skills)} corrupt SKILL.md manifest(s): {', '.join(bad_skills[:3])}",
            }
        )
    elif skills_to_check:
        notes.append(f"Gate 6 (Agent Skills & Plugins) verified across {len(skills_to_check)} SKILL.md manifest(s).")

    s_tier0 = round(max(0.0, min(9.7, s_tier0)), 2)
    s_prov = round(max(0.0, min(9.6, s_prov)), 2)

    s_sub = round(aggregate_tier_pair(s_schema, s_index, 0.15, 0.10, theta=theta), 2)
    s_perc = round(aggregate_tier_pair(s_prose, s_struct, 0.10, 0.10, theta=theta), 2)
    s_act = round(aggregate_tier_pair(s_links, s_topo, 0.15, 0.15, theta=theta), 2)
    s_exist = round(aggregate_tier_pair(s_tier0, s_prov, 0.15, 0.10, theta=theta), 2)

    dimensions: list[dict[str, Any]] = [
        {
            "id": "F_schema",
            "tier": "S_sub",
            "name": "Frontmatter Schema & Ontological Contract",
            "weight": 0.15,
            "score": s_schema,
        },
        {
            "id": "S_index",
            "tier": "S_sub",
            "name": "Hybrid Index Sync (FTS5/HIPS/Vecs) & Git",
            "weight": 0.10,
            "score": s_index,
        },
        {
            "id": "V_prose",
            "tier": "S_perc",
            "name": "Epistemic Prose, KaTeX Math & Fence Parity",
            "weight": 0.10,
            "score": s_prose,
        },
        {
            "id": "A_struct",
            "tier": "S_perc",
            "name": "Note Architecture, Granularity & Non-Stub",
            "weight": 0.10,
            "score": s_struct,
        },
        {
            "id": "U_links",
            "tier": "S_act",
            "name": "Wikilink Closure & Reference Density",
            "weight": 0.15,
            "score": s_links,
        },
        {
            "id": "D_topo",
            "tier": "S_act",
            "name": "Spectral Connectivity (lambda_2) & LCC",
            "weight": 0.15,
            "score": s_topo,
        },
        {
            "id": "B_tier0",
            "tier": "S_exist",
            "name": "Tier-0 Taxonomy, Episodic & RSI Hash Chain",
            "weight": 0.15,
            "score": s_tier0,
        },
        {
            "id": "R_prov",
            "tier": "S_exist",
            "name": "Provenance, Skill Manifests & Conscience",
            "weight": 0.10,
            "score": s_prov,
        },
    ]

    conscience_clean = (
        len(yaml_error_files) == 0
        and len(conflict_marker_files) == 0
        and len(bloat_files) == 0
        and len(broken_links) == 0
        and len(secret_leak_files) == 0
        and rsi_chain_ok
    )
    g_calc = compute_goldwood_index(
        [s_sub, s_perc, s_act, s_exist],
        weights=ARCHETYPE_WEIGHTS["kb"],
        theta=theta,
        k=k,
        conscience_clean=conscience_clean,
    )

    essential_notes = max(1, n_notes - len(orphan_files) - len(stub_note_files))
    probe_states = {d["id"]: ("PASS" if d["score"] >= 9.0 else "FAIL") for d in dimensions}

    return {
        "profile": "kb",
        "target": str(root),
        "project_name": root.name,
        "kolmogorov_elegance": compute_kolmogorov_elegance(n_notes, essential_notes),
        "snrc": compute_snrc(valid_links_count + len(notes), len(blockers)),
        "dimensions": dimensions,
        "tiers": {
            "S_sub": s_sub,
            "S_perc": s_perc,
            "S_act": s_act,
            "S_exist": s_exist,
        },
        "graph_telemetry": {
            "notes_count": n_notes,
            "valid_links": valid_links_count,
            "broken_links": len(broken_links),
            "avg_degree": round(avg_degree, 2),
            "orphan_count": len(orphan_files),
            "lcc_ratio": round(lcc_ratio, 4),
            "two_hop_reachability": round(reach2_ratio, 4),
            "fiedler_lambda2": fiedler_lambda2,
            "indexed_docs": db_doc_count,
            "indexed_chunks": db_chunk_count,
            "vector_coverage": round(r_vec, 4),
            "hips_ready": hips_ready,
            "rsi_chain_ok": rsi_chain_ok,
        },
        "calculation": g_calc,
        "probe_states": probe_states,
        "blockers": blockers,
        "notes": notes,
    }
