"""
Zero-Dependency Local-First Observability & Knowledge Graph Dashboard for Sovereign-KB (`skb dashboard` / `kb dashboard`).

Replaces heavy Dockerized Grafana/Prometheus stacks with a pure Python stdlib (`http.server` + `sqlite3` + `json`)
interactive telemetry server and single-file static HTML exporter:
  1. Interactive 2D Force-Directed [[Wikilink]] Knowledge Graph + Note/Backlink Inspector
  2. Multi-Agent FinOps, Daily Token/USD Burn & Prompt-Cache Hit Ratio (eta_cache >= 90%) Telemetry
  3. 4-Way Hybrid Retrieval Workbench (HIPS Block-Max WAND + SQLite FTS5 + Ollama Vectors + 1-Hop Graph)
  4. Recursive Self-Improvement (RSI) Lyapunov Trajectory J(theta_t) & SHA-256 Hash-Chained Audit Ledger
  5. 5-Gate Deterministic Vault Doctor & Multi-Vault Switcher (`me-kb` <-> `sovereign-kb-vault`)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


def discover_vaults(active_vault: Path, config_path: Path) -> list[dict[str, Any]]:
    """Discover local Sovereign-KB vaults on disk without hardcoding private host identifiers."""
    candidates: list[Path] = [active_vault.resolve()]
    env_v = os.environ.get("KB_VAULT_DIR")
    if env_v:
        candidates.append(Path(env_v).expanduser().resolve())
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if cfg.get("vault_dir"):
                candidates.append(Path(cfg["vault_dir"]).expanduser().resolve())
        except Exception:
            pass

    home = Path.home()
    candidates.extend(
        [
            home / "sovereign-kb-vault",
            home / "notes",
        ]
    )

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except Exception:
            continue
        key = str(resolved)
        if key in seen or not resolved.is_dir():
            continue
        if (resolved / ".kb").is_dir() or (resolved / "INDEX.md").is_file():
            seen.add(key)
            out.append(
                {
                    "name": resolved.name,
                    "path": key,
                    "active": resolved == active_vault.resolve(),
                }
            )
    return out


def classify_tier_folder(rel_path: str) -> str:
    if "/" not in rel_path:
        return "root"
    top = rel_path.split("/", 1)[0]
    if top in ("00-identity", "01-now", "02-operating-system", "03-life-domains", "04-episodic"):
        return top
    return "other"


def build_dashboard_snapshot(
    vault_dir: Path,
    *,
    get_db_fn: Callable[[Path], Any],
    sync_fn: Callable[[Any], None],
    list_md_fn: Callable[[Path], list[Path]],
    parse_fm_fn: Callable[[str], dict],
    extract_links_fn: Callable[[str, dict[str, str] | None], list[str]],
    build_stem_fn: Callable[[Path, list[Path] | None], dict[str, str]],
    hybrid_retrieve_fn: Callable[..., list[dict]],
    hips_retrieve_fn: Callable[..., tuple[dict, dict]],
    resolve_hips_bin_fn: Callable[[], Path | None],
    collect_usage_fn: Callable[..., dict[str, Any]],
    config_path: Path,
    days: int = 7,
    native_usage_only: bool = False,
    no_gx10: bool = False,
) -> dict[str, Any]:
    """Assemble the complete observability, knowledge graph, RSI, and FinOps snapshot for a vault."""
    from sovereign_kb.rsi import load_retrieval_policy, read_rsi_ledger, verify_ledger_chain

    vault_dir = vault_dir.expanduser().resolve()
    conn = get_db_fn(vault_dir)
    md_files = list_md_fn(vault_dir)
    existing_mtimes = {row["rel_path"]: row["mtime"] for row in conn.execute("SELECT rel_path, mtime FROM documents")}
    needs_sync = len(existing_mtimes) != len(md_files) or any(
        str(p.relative_to(vault_dir)) not in existing_mtimes
        or abs(existing_mtimes[str(p.relative_to(vault_dir))] - p.stat().st_mtime) >= 1e-4
        for p in md_files
    )
    if needs_sync and md_files:
        conn.close()
        sync_fn(argparse.Namespace(vault=str(vault_dir), force=False, no_embed=True, quiet=True))
        conn = get_db_fn(vault_dir)

    stem_lookup = build_stem_fn(vault_dir, md_files)

    # 1. Evaluate 5-Gate Vault Doctor
    violations: list[dict[str, str]] = []
    inbound_counts: dict[str, int] = {str(p.relative_to(vault_dir)): 0 for p in md_files}
    outbound_counts: dict[str, int] = {str(p.relative_to(vault_dir)): 0 for p in md_files}

    for p in md_files:
        rel = str(p.relative_to(vault_dir))
        text = p.read_text(encoding="utf-8", errors="replace")
        meta = parse_fm_fn(text)
        if rel not in ("AGENTS.md", "README.md"):
            for req_key in ("title", "type", "updated", "tags"):
                if not meta.get(req_key):
                    violations.append({"gate": "Gate 1 (Frontmatter)", "file": rel, "issue": f"Missing '{req_key}'"})
        try:
            targets = extract_links_fn(text, stem_lookup, rel)
        except TypeError:
            targets = extract_links_fn(text, stem_lookup)
        for target in targets:
            if target not in inbound_counts:
                violations.append({"gate": "Gate 2 (Wikilinks)", "file": rel, "issue": f"Broken wikilink '{target}'"})
            elif target != rel:
                inbound_counts[target] += 1
                outbound_counts[rel] = outbound_counts.get(rel, 0) + 1

        fence_char: str | None = None
        fence_len = 0
        for line_no, raw_line in enumerate(text.splitlines(), 1):
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
                        "issue": f"Unresolved Git conflict marker: {raw_line[:30]}",
                    }
                )
                break

    for rel, count in inbound_counts.items():
        if rel not in ("INDEX.md", "AGENTS.md", "README.md") and count == 0:
            violations.append(
                {"gate": "Gate 3 (Graph Connectivity)", "file": rel, "issue": "Orphan note (0 inbound links)"}
            )

    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    vec_count = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
    links_count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]

    if doc_count != len(md_files):
        violations.append(
            {
                "gate": "Gate 4 (Index Sync)",
                "file": ".kb/index.db",
                "issue": f"Indexed docs ({doc_count}) != markdown files ({len(md_files)})",
            }
        )

    chain_ok, ledger_cycles = verify_ledger_chain(vault_dir)
    if not chain_ok:
        violations.append(
            {
                "gate": "Gate 4 (RSI Ledger)",
                "file": ".kb/rsi-ledger.jsonl",
                "issue": "Broken SHA-256 hash chain",
            }
        )

    if (vault_dir / ".git").exists():
        git_ls = subprocess.run(
            ["git", "-C", str(vault_dir), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        for tracked in git_ls:
            if tracked.endswith((".mp4", ".mov", ".pyc", ".DS_Store")) or "__pycache__" in tracked:
                violations.append({"gate": "Gate 5 (Git Hygiene)", "file": tracked, "issue": "Tracked binary artifact"})

    from sovereign_kb.gwi import validate_skill_frontmatter

    for smd in sorted(
        p
        for p in vault_dir.rglob("SKILL.md")
        if not any(part.startswith(".") for part in p.relative_to(vault_dir).parts)
    ):
        ok, err = validate_skill_frontmatter(smd)
        if not ok:
            violations.append(
                {
                    "gate": "Gate 6 (Agent Skills)",
                    "file": str(smd.relative_to(vault_dir)),
                    "issue": err,
                }
            )

    gate_states = []
    for idx, (g_prefix, g_label) in enumerate(
        [
            ("Gate 1", "Frontmatter Schema"),
            ("Gate 2", "Wikilink Integrity"),
            ("Gate 3", "Graph Connectivity"),
            ("Gate 4", "FTS5, HIPS & Ledger"),
            ("Gate 5", "Git & Binary Hygiene"),
            ("Gate 6", "Skills & Plugins"),
        ],
        start=1,
    ):
        g_viol = [v for v in violations if v["gate"].startswith(g_prefix)]
        gate_states.append(
            {
                "id": f"G{idx}",
                "name": f"{g_prefix}: {g_label}",
                "status": "PASS" if not g_viol else "FAIL",
                "violations": len(g_viol),
            }
        )

    # 2. Knowledge Graph Nodes & Edges
    chunk_counts_by_doc = {
        r["rel_path"]: int(r["cnt"])
        for r in conn.execute("SELECT rel_path, COUNT(*) AS cnt FROM chunks GROUP BY rel_path")
    }
    nodes: list[dict[str, Any]] = []
    tier_counts: dict[str, int] = {}
    for r in conn.execute(
        "SELECT rel_path, title, doc_type, tier, updated, tags, LENGTH(content) AS byte_len FROM documents ORDER BY rel_path"
    ):
        rp = r["rel_path"]
        tf = classify_tier_folder(rp)
        tier_counts[tf] = tier_counts.get(tf, 0) + 1
        in_deg = inbound_counts.get(rp, 0)
        out_deg = outbound_counts.get(rp, 0)
        nodes.append(
            {
                "id": rp,
                "title": r["title"] or Path(rp).stem,
                "doc_type": r["doc_type"] or "note",
                "tier_folder": tf,
                "updated": r["updated"] or "",
                "tags": r["tags"] or "",
                "chunks": chunk_counts_by_doc.get(rp, 0),
                "bytes": int(r["byte_len"] or 0),
                "inbound_degree": in_deg,
                "outbound_degree": out_deg,
                "total_degree": in_deg + out_deg,
            }
        )

    node_ids = {n["id"] for n in nodes}
    edges: list[dict[str, str]] = []
    for r in conn.execute("SELECT source_path, target_path FROM links ORDER BY source_path, target_path"):
        if r["source_path"] in node_ids and r["target_path"] in node_ids:
            edges.append({"source": r["source_path"], "target": r["target_path"]})

    # 3. HIPS Block-Max WAND & SQLite Metrics
    db_path = vault_dir / ".kb" / "index.db"
    sqlite_bytes = db_path.stat().st_size if db_path.is_file() else 0
    hips_bin = resolve_hips_bin_fn()
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    hips_ready = hips_idx_dir.is_dir() and any(hips_idx_dir.iterdir())
    hips_bytes = sum(f.stat().st_size for f in hips_idx_dir.rglob("*") if f.is_file()) if hips_ready else 0
    hips_warm_ms: float | None = None
    if hips_bin and hips_ready:
        t0 = time.perf_counter()
        hips_retrieve_fn(vault_dir, "Block-Max WAND Lyapunov 6-Pillar", limit=5)
        hips_warm_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    # 4. Warm 4-Way Hybrid Search Probe
    default_query = "6-Pillar Deterministic Autonomous Engineering"
    t_search0 = time.perf_counter()
    initial_hits = hybrid_retrieve_fn(conn, vault_dir, default_query, top_k=6, engine="auto")
    if not initial_hits:
        default_query = "Architecture Decisions Runbook"
        initial_hits = hybrid_retrieve_fn(conn, vault_dir, default_query, top_k=6, engine="auto")
    initial_search_ms = round((time.perf_counter() - t_search0) * 1000.0, 2)
    conn.close()

    # 5. RSI Policy & SHA-256 Hash-Chained Ledger
    policy = load_retrieval_policy(vault_dir)
    policy_sha = hashlib.sha256(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    ledger_entries = read_rsi_ledger(vault_dir)
    latest_j = ledger_entries[-1].get("j_after") if ledger_entries else None
    latest_mrr = (
        ledger_entries[-1].get("after", {}).get("mrr_at_5")
        if ledger_entries and isinstance(ledger_entries[-1].get("after"), dict)
        else None
    )

    # 6. Multi-Agent FinOps & Prompt Cache Telemetry
    finops = collect_usage_fn(
        days=days,
        force_native=native_usage_only,
        offline=True,
        no_gx10=no_gx10,
    )

    vec_pct = round((vec_count / chunk_count * 100.0), 1) if chunk_count > 0 else 0.0

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "vault": {
            "name": vault_dir.name,
            "path": str(vault_dir),
            "documents": doc_count,
            "chunks": chunk_count,
            "vectors": vec_count,
            "vector_coverage_pct": vec_pct,
            "links": links_count,
            "sqlite_bytes": sqlite_bytes,
            "hips_ready": hips_ready,
            "hips_bytes": hips_bytes,
            "hips_binary": str(hips_bin) if hips_bin else None,
            "hips_warm_ms": hips_warm_ms,
        },
        "gates": {
            "status": "PASS" if not violations else "FAIL",
            "passed_count": sum(1 for g in gate_states if g["status"] == "PASS"),
            "total_gates": 6,
            "items": gate_states,
            "violations": violations,
        },
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "tier_counts": tier_counts,
        },
        "rsi": {
            "policy": policy,
            "policy_sha256": policy_sha,
            "chain_verified": chain_ok,
            "total_cycles": ledger_cycles,
            "latest_j": latest_j,
            "latest_mrr_at_5": latest_mrr,
            "ledger": ledger_entries[-25:],
        },
        "finops": finops,
        "initial_search": {
            "query": default_query,
            "engine": "auto",
            "latency_ms": initial_search_ms,
            "results": initial_hits,
        },
        "vaults": discover_vaults(vault_dir, config_path),
    }


def render_dashboard_html(snapshot: dict[str, Any], live_server: bool = False) -> str:
    """Render a zero-dependency, modern HTML5/CSS/JS observability dashboard."""
    vault_name = html.escape(snapshot["vault"]["name"])
    embedded_json = json.dumps(snapshot, ensure_ascii=False).replace("</", "<\\/")
    live_flag = "true" if live_server else "false"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <script>
  try {{
    document.querySelector('meta[name="color-scheme"]').content = localStorage.getItem("color-scheme") ?? "light dark";
  }} catch (_) {{}}
  </script>
  <title>Sovereign-KB Telemetry &amp; Knowledge Graph — {vault_name}</title>
  <style>
    :root {{
      color-scheme: light dark;

      &:has(> head > meta[name="color-scheme"][content="dark"]) {{
        color-scheme: dark;
      }}
      &:has(> head > meta[name="color-scheme"][content="light"]) {{
        color-scheme: light;
      }}

      --bg-canvas: light-dark(#f6f8fb, #0b0f17);
      --bg-surface: light-dark(#ffffff, #111826);
      --bg-elevated: light-dark(#eef2f8, #182235);
      --bg-glass: light-dark(rgba(255, 255, 255, 0.88), rgba(17, 24, 38, 0.88));
      --fg-primary: light-dark(#0f172a, #f1f5f9);
      --fg-secondary: light-dark(#475569, #94a3b8);
      --fg-muted: light-dark(#64748b, #64748b);
      --border-subtle: light-dark(#dbe3ef, #1e2d44);
      --border-strong: light-dark(#cbd5e1, #2e4365);

      /* Okabe-Ito Colorblind-Safe Palette */
      --oi-blue: light-dark(#0072B2, #38bdf8);
      --oi-orange: light-dark(#D55E00, #fb923c);
      --oi-green: light-dark(#009E73, #34d399);
      --oi-amber: light-dark(#E69F00, #fbbf24);
      --oi-purple: light-dark(#CC79A7, #f472b6);
      --oi-sky: light-dark(#56B4E9, #60a5fa);

      --scrollbar-track: light-dark(#e2e8f0, #0f172a);
      --scrollbar-thumb: light-dark(#94a3b8, #334155);
      scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
      scrollbar-width: thin;
      accent-color: var(--oi-blue);
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg-canvas);
      color: var(--fg-primary);
      line-height: 1.5;
      min-block-size: 100dvh;
    }}

    code, pre, .mono {{
      font-family: "JetBrains Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
    }}

    /* Sticky Top Telemetry Header */
    .top-bar {{
      position: sticky;
      inset-block-start: 0;
      z-index: 40;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem 1.25rem;
      padding: 0.75rem 1.5rem;
      background: var(--bg-glass);
      backdrop-filter: blur(12px);
      border-block-end: 1px solid var(--border-subtle);
    }}

    .brand-group {{
      display: flex;
      align-items: center;
      gap: 0.85rem;
      flex-wrap: wrap;
    }}

    .brand-logo {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      font-weight: 700;
      font-size: 1.05rem;
      letter-spacing: -0.02em;
    }}

    .brand-badge {{
      display: inline-flex;
      align-items: center;
      padding: 0.15rem 0.55rem;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      background: var(--bg-elevated);
      border: 1px solid var(--border-strong);
      color: var(--oi-blue);
    }}

    .header-controls {{
      display: flex;
      align-items: center;
      gap: 0.65rem;
      flex-wrap: wrap;
    }}

    select, input[type="text"], input[type="search"], button {{
      font: inherit;
      font-size: 0.85rem;
      color: var(--fg-primary);
      background: var(--bg-surface);
      border: 1px solid var(--border-strong);
      border-radius: 0.5rem;
      padding: 0.4rem 0.7rem;
    }}

    button {{
      cursor: pointer;
      font-weight: 600;
      transition: background-color 120ms ease, border-color 120ms ease;
    }}

    button:hover {{
      background: var(--bg-elevated);
    }}

    button.primary {{
      background: var(--oi-blue);
      color: #ffffff;
      border-color: transparent;
    }}

    .status-pill {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.28rem 0.68rem;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 700;
      border: 1px solid var(--border-subtle);
      background: var(--bg-surface);
    }}

    .status-pill.pass {{
      color: var(--oi-green);
      border-color: var(--oi-green);
    }}

    .status-pill.warn {{
      color: var(--oi-orange);
      border-color: var(--oi-orange);
    }}

    .main-shell {{
      max-inline-size: 1680px;
      margin-inline: auto;
      padding: 1.25rem 1.5rem 3rem;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }}

    /* KPI Strip */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 0.9rem;
    }}

    .kpi-card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 0.75rem;
      padding: 0.95rem 1.1rem;
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }}

    .kpi-label {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--fg-secondary);
      font-weight: 600;
    }}

    .kpi-value {{
      font-size: 1.55rem;
      font-weight: 800;
      letter-spacing: -0.02em;
    }}

    .kpi-sub {{
      font-size: 0.75rem;
      color: var(--fg-secondary);
    }}

    /* Dashboard Sections with Content Visibility Containment */
    .dashboard-section {{
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 0.75rem;
      padding: 1.15rem 1.25rem;
      content-visibility: auto;
      contain-intrinsic-size: auto 320px auto 520px;
    }}

    @supports not (content-visibility: auto) {{
      .dashboard-section {{
        contain: layout style paint;
      }}
    }}

    .section-header {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin-block-end: 0.95rem;
      padding-block-end: 0.65rem;
      border-block-end: 1px solid var(--border-subtle);
    }}

    .section-title {{
      font-size: 1rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}

    .section-subtitle {{
      font-size: 0.8rem;
      color: var(--fg-secondary);
    }}

    /* Split Grid for Graph + Note Inspector */
    .graph-workbench {{
      display: grid;
      grid-template-columns: minmax(0, 1.65fr) minmax(320px, 1fr);
      gap: 1rem;
      align-items: stretch;
    }}

    @media (max-width: 1080px) {{
      .graph-workbench {{
        grid-template-columns: 1fr;
      }}
    }}

    .canvas-container {{
      position: relative;
      background: var(--bg-canvas);
      border: 1px solid var(--border-subtle);
      border-radius: 0.75rem;
      block-size: 500px;
      overflow: hidden;
    }}

    #graph-canvas {{
      inline-size: 100%;
      block-size: 100%;
      display: block;
      cursor: grab;
    }}

    #graph-canvas:active {{
      cursor: grabbing;
    }}

    .graph-legend {{
      position: absolute;
      inset-block-end: 0.65rem;
      inset-inline-start: 0.65rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      background: var(--bg-glass);
      padding: 0.4rem 0.6rem;
      border-radius: 0.5rem;
      border: 1px solid var(--border-subtle);
      font-size: 0.75rem;
    }}

    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      cursor: pointer;
      padding: 0.1rem 0.35rem;
      border-radius: 0.35rem;
    }}

    .legend-dot {{
      inline-size: 9px;
      block-size: 9px;
      border-radius: 999px;
      display: inline-block;
    }}

    .inspector-pane {{
      background: var(--bg-canvas);
      border: 1px solid var(--border-subtle);
      border-radius: 0.75rem;
      padding: 0.95rem;
      block-size: 500px;
      overflow-y: auto;
      scrollbar-gutter: stable;
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
    }}

    .note-preview {{
      font-size: 0.8rem;
      white-space: pre-wrap;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
      padding: 0.7rem;
      overflow-x: auto;
      max-block-size: 270px;
    }}

    .chip-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
    }}

    .chip {{
      font-size: 0.75rem;
      padding: 0.15rem 0.48rem;
      border-radius: 0.35rem;
      background: var(--bg-elevated);
      border: 1px solid var(--border-subtle);
      cursor: pointer;
    }}

    .chip:hover {{
      border-color: var(--oi-blue);
      color: var(--oi-blue);
    }}

    /* Two-Column Row for FinOps + RSI */
    .dual-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(460px, 1fr));
      gap: 1.25rem;
    }}

    @media (max-width: 640px) {{
      .dual-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    .chart-box {{
      background: var(--bg-canvas);
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
      padding: 0.75rem;
      margin-block-end: 0.85rem;
    }}

    /* Responsive Sticky Table (Modern Web Guidance `responsive-table`) */
    .table-wrapper {{
      overflow: auto;
      max-inline-size: 100%;
      max-block-size: min(380px, 70vh);
      container-type: inline-size;
      scrollbar-gutter: stable;
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
    }}

    table {{
      inline-size: 100%;
      border-collapse: separate;
      border-spacing: 0;
      font-size: 0.8rem;
    }}

    caption {{
      text-align: start;
      padding: 0.5rem 0.75rem;
      font-weight: 700;
      font-size: 0.8rem;
      color: var(--fg-secondary);
      background: var(--bg-elevated);
      border-block-end: 1px solid var(--border-subtle);
    }}

    th, td {{
      padding: 0.52rem 0.75rem;
      text-align: start;
      border-block-end: 1px solid var(--border-subtle);
    }}

    thead {{
      position: sticky;
      inset-block-start: 0;
      z-index: 2;
      background: var(--bg-elevated);
    }}

    thead th {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--fg-secondary);
      background: var(--bg-elevated);
    }}

    tbody th {{
      position: sticky;
      inset-inline-start: 0;
      z-index: 1;
      background: var(--bg-surface);
      font-weight: 600;
    }}

    tbody tr:hover td, tbody tr:hover th {{
      background: var(--bg-elevated);
    }}

    @container (width < 600px) {{
      table, thead, tbody, tr, th, td {{
        display: block;
      }}
      thead {{
        display: none;
      }}
      tr {{
        margin-block-end: 0.85rem;
        border: 1px solid var(--border-strong);
        border-radius: 0.5rem;
      }}
      tbody th {{
        position: sticky;
        inset-block-start: 0;
        z-index: 2;
        background: var(--bg-elevated);
      }}
      td:nth-child(2)::before {{ content: var(--label-2, "Col 2") ": "; font-weight: 600; }}
      td:nth-child(3)::before {{ content: var(--label-3, "Col 3") ": "; font-weight: 600; }}
      td:nth-child(4)::before {{ content: var(--label-4, "Col 4") ": "; font-weight: 600; }}
      td:nth-child(5)::before {{ content: var(--label-5, "Col 5") ": "; font-weight: 600; }}
    }}

    /* 4-Way Retrieval Cards */
    .search-bar-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      margin-block-end: 0.9rem;
    }}

    .search-bar-row input[type="search"] {{
      flex: 1 1 280px;
    }}

    .hit-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 0.8rem;
    }}

    .hit-card {{
      background: var(--bg-canvas);
      border: 1px solid var(--border-subtle);
      border-radius: 0.5rem;
      padding: 0.8rem 0.95rem;
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
    }}

    .hit-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 0.5rem;
    }}

    .hit-path {{
      font-weight: 700;
      font-size: 0.8rem;
      color: var(--oi-blue);
      cursor: pointer;
    }}

    .score-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      font-size: 0.75rem;
    }}

    .score-badge {{
      padding: 0.1rem 0.42rem;
      border-radius: 0.35rem;
      background: var(--bg-elevated);
      border: 1px solid var(--border-subtle);
    }}

    .gate-strip {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }}
  </style>
</head>
<body>
  <header class="top-bar">
    <div class="brand-group">
      <div class="brand-logo">
        <span>⚡ Sovereign-KB Telemetry</span>
        <span class="brand-badge mono">v1.4.0 Zero-Dep</span>
      </div>
      <div id="gate-strip" class="gate-strip"></div>
    </div>
    <div class="header-controls">
      <label for="vault-select" class="kpi-label" style="margin-inline-end:0.2rem;">Vault:</label>
      <select id="vault-select" aria-label="Select Sovereign Vault"></select>
      <button id="refresh-btn" type="button" title="Refresh live telemetry">↻ Refresh</button>
      <button id="theme-toggle" type="button" aria-label="Toggle color scheme">◐ Theme</button>
    </div>
  </header>

  <main class="main-shell">
    <!-- Top KPI Strip -->
    <section class="kpi-grid" aria-label="Key Vault and FinOps Metrics">
      <div class="kpi-card">
        <span class="kpi-label">Vault Graph &amp; Chunks</span>
        <span class="kpi-value mono" id="kpi-docs-chunks">—</span>
        <span class="kpi-sub" id="kpi-links-vecs">—</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">HIPS Block-Max WAND</span>
        <span class="kpi-value mono" id="kpi-hips-lat">—</span>
        <span class="kpi-sub" id="kpi-hips-sub">—</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">RSI Lyapunov Objective J(θ)</span>
        <span class="kpi-value mono" id="kpi-rsi-j">—</span>
        <span class="kpi-sub" id="kpi-rsi-sub">—</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Prompt Cache Efficiency (η)</span>
        <span class="kpi-value mono" id="kpi-cache-pct">—</span>
        <span class="kpi-sub" id="kpi-cache-sub">Guardrail ≥ 90.0%</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Multi-Agent Credit Burn (7d)</span>
        <span class="kpi-value mono" id="kpi-cost-total">—</span>
        <span class="kpi-sub" id="kpi-cost-today">—</span>
      </div>
    </section>

    <!-- Panel 1: Interactive 2D Force-Directed Wikilink Knowledge Graph -->
    <section class="dashboard-section" aria-labelledby="sec-graph-title">
      <div class="section-header">
        <div>
          <h2 class="section-title" id="sec-graph-title">🕸️ Interactive [[Wikilink]] Knowledge Graph &amp; Note Inspector</h2>
          <p class="section-subtitle">Force-directed topology colored by tier folder (Okabe-Ito palette). Click any node to inspect frontmatter, backlinks, and markdown.</p>
        </div>
        <div style="display:flex; gap:0.5rem; align-items:center;">
          <input type="search" id="graph-filter" placeholder="Filter graph nodes (e.g. rust, paper, now)..." aria-label="Filter graph nodes">
          <button type="button" id="graph-reset-btn">Reset View</button>
        </div>
      </div>

      <div class="graph-workbench">
        <div class="canvas-container">
          <canvas id="graph-canvas"></canvas>
          <div class="graph-legend" id="graph-legend"></div>
        </div>
        <aside class="inspector-pane" id="note-inspector" aria-live="polite">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <strong id="insp-title" style="font-size:0.95rem;">Select a node in the graph</strong>
            <span id="insp-type" class="brand-badge mono">note</span>
          </div>
          <div id="insp-path" class="mono" style="font-size:0.75rem; color:var(--fg-secondary);"></div>
          <div id="insp-meta" style="font-size:0.75rem; color:var(--fg-secondary);"></div>
          <div>
            <div class="kpi-label" style="margin-block-end:0.25rem;">Inbound Backlinks</div>
            <div id="insp-inbound" class="chip-list"></div>
          </div>
          <div>
            <div class="kpi-label" style="margin-block-end:0.25rem;">Outbound [[Wikilinks]]</div>
            <div id="insp-outbound" class="chip-list"></div>
          </div>
          <div class="kpi-label">Markdown Content Preview</div>
          <pre class="note-preview mono" id="insp-content">Click any node in the Knowledge Graph or any search hit below to inspect its contents.</pre>
        </aside>
      </div>
    </section>

    <!-- Panel 2: 4-Way Hybrid Retrieval Workbench -->
    <section class="dashboard-section" aria-labelledby="sec-search-title">
      <div class="section-header">
        <div>
          <h2 class="section-title" id="sec-search-title">🔍 4-Way Hybrid Retrieval Inspector (HIPS WAND + FTS5 + Vectors + 1-Hop Graph)</h2>
          <p class="section-subtitle">Inspect Reciprocal Rank Fusion (RRF) score decomposition across lexical Block-Max WAND, BM25, semantic vectors, and graph neighbors.</p>
        </div>
        <span id="search-latency-badge" class="status-pill pass mono">0.0 ms</span>
      </div>

      <form id="search-form" class="search-bar-row">
        <input type="search" id="search-input" placeholder="Query vault (e.g. Type-42 font embedding, RUST-CERT-14, Block-Max WAND)..." required>
        <select id="search-engine" aria-label="Retrieval Engine">
          <option value="auto">Engine: auto (4-Way Hybrid RRF)</option>
          <option value="hips">Engine: hips (Pure Rust Block-Max WAND)</option>
          <option value="fts5">Engine: fts5 (SQLite BM25)</option>
        </select>
        <button type="submit" class="primary">Run Probe</button>
      </form>

      <div id="search-hits" class="hit-grid"></div>
    </section>

    <!-- Panel 3 & 4: Multi-Agent FinOps + RSI Lyapunov Ledger -->
    <div class="dual-grid">
      <section class="dashboard-section" aria-labelledby="sec-finops-title">
        <div class="section-header">
          <div>
            <h2 class="section-title" id="sec-finops-title">💰 Multi-Agent FinOps &amp; Prompt Cache Telemetry</h2>
            <p class="section-subtitle">Daily API-equivalent token cost &amp; prompt-cache hit telemetry across local and cloud AI coding agents.</p>
          </div>
          <span id="finops-engine-badge" class="brand-badge mono">ccusage</span>
        </div>

        <div class="chart-box" id="finops-chart-box"></div>

        <div tabindex="0" role="region" aria-labelledby="finops-table-caption" class="table-wrapper">
          <table style="--label-2:'Agent'; --label-3:'Input / Cache Read'; --label-4:'Output'; --label-5:'API Equiv USD';">
            <caption id="finops-table-caption">Model &amp; Agent Token Telemetry Breakdown (Focus Window)</caption>
            <thead>
              <tr>
                <th>Model</th>
                <th>Agent</th>
                <th>Input / Cache Read</th>
                <th>Output</th>
                <th>API Equiv USD</th>
              </tr>
            </thead>
            <tbody id="finops-tbody"></tbody>
          </table>
        </div>
      </section>

      <section class="dashboard-section" aria-labelledby="sec-rsi-title">
        <div class="section-header">
          <div>
            <h2 class="section-title" id="sec-rsi-title">♾️ RSI Lyapunov Trajectory &amp; SHA-256 Audit Ledger</h2>
            <p class="section-subtitle">Monotonically-gated optimization J(θ_t+1, K) ≥ J(θ_t, K) &amp; retrieval policy weights.</p>
          </div>
          <span id="rsi-chain-badge" class="status-pill pass mono">SHA-256: PASS</span>
        </div>

        <div class="chart-box" id="rsi-chart-box"></div>
        <div id="rsi-policy-chips" class="chip-list" style="margin-block-end:0.85rem;"></div>

        <div tabindex="0" role="region" aria-labelledby="rsi-table-caption" class="table-wrapper">
          <table style="--label-2:'J(θ) Before → After'; --label-3:'MRR@5 / Recall@1'; --label-4:'ΔJ'; --label-5:'SHA-256 Hash';">
            <caption id="rsi-table-caption">Append-Only Cryptographic RSI Ledger (.kb/rsi-ledger.jsonl)</caption>
            <thead>
              <tr>
                <th>Cycle</th>
                <th>J(θ) Before → After</th>
                <th>MRR@5 / Recall@1</th>
                <th>ΔJ</th>
                <th>SHA-256 Chain</th>
              </tr>
            </thead>
            <tbody id="rsi-tbody"></tbody>
          </table>
        </div>
      </section>
    </div>
  </main>

  <script>
    const LIVE_SERVER = {live_flag};
    let SNAPSHOT = {embedded_json};

    const TIER_COLORS = {{
      "00-identity": "#0072B2",
      "01-now": "#D55E00",
      "02-operating-system": "#009E73",
      "03-life-domains": "#CC79A7",
      "04-episodic": "#E69F00",
      "root": "#56B4E9",
      "other": "#94a3b8"
    }};

    function fmtNum(n) {{
      return Number(n || 0).toLocaleString();
    }}

    function fmtBytes(b) {{
      const num = Number(b || 0);
      if (num >= 1048576) return (num / 1048576).toFixed(2) + " MiB";
      if (num >= 1024) return (num / 1024).toFixed(1) + " KiB";
      return num + " B";
    }}

    function esc(s) {{
      const d = document.createElement("div");
      d.textContent = String(s ?? "");
      return d.innerHTML;
    }}

    /* Modern Web Guidance Two-State Dark Mode Override (`dark-mode-toggle`) */
    document.getElementById("theme-toggle").addEventListener("click", () => {{
      const meta = document.querySelector('meta[name="color-scheme"]');
      const sysDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const sysScheme = sysDark ? "dark" : "light";
      let stored = null;
      try {{ stored = localStorage.getItem("color-scheme"); }} catch (_) {{}}
      const currentRendered = (stored === "dark" || stored === "light") ? stored : sysScheme;
      const target = currentRendered === "dark" ? "light" : "dark";
      if (target !== sysScheme) {{
        try {{ localStorage.setItem("color-scheme", target); }} catch (_) {{}}
        meta.content = target;
      }} else {{
        try {{ localStorage.removeItem("color-scheme"); }} catch (_) {{}}
        meta.content = "light dark";
      }}
    }});

    window.addEventListener("storage", (e) => {{
      if (e.key === "color-scheme") {{
        document.querySelector('meta[name="color-scheme"]').content = e.newValue ?? "light dark";
      }}
    }});

    /* Render Top KPIs & 5-Gate Strip */
    function renderTopTelemetry() {{
      const v = SNAPSHOT.vault;
      const g = SNAPSHOT.gates;
      const r = SNAPSHOT.rsi;
      const f = SNAPSHOT.finops;

      document.getElementById("kpi-docs-chunks").textContent = `${{v.documents}} notes / ${{v.chunks}} chunks`;
      document.getElementById("kpi-links-vecs").textContent = `${{v.links}} [[wikilinks]] • ${{v.vector_coverage_pct}}% vectors (${{fmtBytes(v.sqlite_bytes)}})`;

      if (v.hips_ready) {{
        document.getElementById("kpi-hips-lat").textContent = v.hips_warm_ms !== null ? `${{v.hips_warm_ms}} ms` : "READY";
        document.getElementById("kpi-hips-sub").textContent = `Index: ${{fmtBytes(v.hips_bytes)}} (Block-Max WAND)`;
      }} else {{
        document.getElementById("kpi-hips-lat").textContent = "FTS5 Mode";
        document.getElementById("kpi-hips-sub").textContent = "Zero-dep SQLite BM25 active";
      }}

      document.getElementById("kpi-rsi-j").textContent = r.latest_j !== null && r.latest_j !== undefined
        ? Number(r.latest_j).toFixed(4)
        : "Not evaluated";
      document.getElementById("kpi-rsi-sub").textContent = `${{r.total_cycles}} SHA-256 cycles • policy #${{r.policy_sha256.slice(0, 8)}}`;

      const totTok = Number((f.totals && f.totals.totalTokens) || 0);
      const eta = Number(f.prompt_cache_hit_pct || 0);
      const cacheEl = document.getElementById("kpi-cache-pct");
      if (totTok === 0) {{
        cacheEl.textContent = "—";
        cacheEl.style.color = "var(--fg-primary)";
        document.getElementById("kpi-cache-sub").textContent = "No agent logs in window";
      }} else {{
        cacheEl.textContent = `${{eta.toFixed(1)}}%`;
        cacheEl.style.color = eta >= 90.0 ? "var(--oi-green)" : "var(--oi-orange)";
        document.getElementById("kpi-cache-sub").textContent = eta >= 90.0
          ? "PASS (≥ 90.0% cache read ratio)"
          : "BELOW 90.0% cache guardrail";
      }}

      const totCost = Number((f.totals && f.totals.totalCost) || 0);
      const todayCost = Number((f.today && f.today.totalCost) || 0);
      document.getElementById("kpi-cost-total").textContent = `$${{totCost.toFixed(2)}}`;
      document.getElementById("kpi-cost-today").textContent = `Focus day: $${{todayCost.toFixed(2)}} USD (${{fmtNum((f.totals && f.totals.totalTokens) || 0)}} tok)`;

      const strip = document.getElementById("gate-strip");
      strip.innerHTML = g.items.map(item => {{
        const cls = item.status === "PASS" ? "pass" : "warn";
        return `<span class="status-pill ${{cls}} mono" title="${{esc(item.name)}}">${{esc(item.id)}}: ${{esc(item.status)}}</span>`;
      }}).join("");

      const sel = document.getElementById("vault-select");
      sel.innerHTML = (SNAPSHOT.vaults || []).map(vl => {{
        const selected = vl.path === v.path ? "selected" : "";
        return `<option value="${{esc(vl.path)}}" ${{selected}}>${{esc(vl.name)}}</option>`;
      }}).join("");
    }}

    /* Render Search Hits */
    function renderSearchHits(searchObj) {{
      if (!searchObj) return;
      document.getElementById("search-input").value = searchObj.query || "";
      document.getElementById("search-latency-badge").textContent = `${{searchObj.latency_ms ?? 0}} ms (${{searchObj.results.length}} hits)`;
      const container = document.getElementById("search-hits");
      if (!searchObj.results || searchObj.results.length === 0) {{
        container.innerHTML = `<div class="hit-card">No chunks matched query.</div>`;
        return;
      }}
      container.innerHTML = searchObj.results.map((h, idx) => {{
        const hipsBadge = h.hips_score !== null && h.hips_score !== undefined
          ? `<span class="score-badge mono">HIPS: ${{h.hips_score}} (#${{h.hips_rank}})</span>`
          : `<span class="score-badge mono">HIPS: —</span>`;
        const ftsBadge = h.fts_rank ? `<span class="score-badge mono">FTS5: #${{h.fts_rank}}</span>` : "";
        const vecBadge = h.vec_sim !== null && h.vec_sim !== undefined
          ? `<span class="score-badge mono">Cos: ${{h.vec_sim}}</span>`
          : "";
        const snippet = String(h.content || "").slice(0, 260);
        return `
          <article class="hit-card">
            <div class="hit-header">
              <span class="hit-path mono" data-note="${{esc(h.rel_path)}}">[${{idx + 1}}] ${{esc(h.rel_path)}}#L${{h.start_line}}-L${{h.end_line}}</span>
              <span class="status-pill pass mono">RRF ${{h.rrf_score}}</span>
            </div>
            <div style="font-size:0.75rem; font-weight:600; color:var(--fg-secondary);">${{esc(h.heading_path)}}</div>
            <div class="score-badges">${{hipsBadge}}${{ftsBadge}}${{vecBadge}}</div>
            <pre class="note-preview mono" style="max-block-size:110px;">${{esc(snippet)}}</pre>
          </article>
        `;
      }}).join("");

      container.querySelectorAll("[data-note]").forEach(el => {{
        el.addEventListener("click", () => selectNode(el.getAttribute("data-note")));
      }});
    }}

    /* Render FinOps SVG Chart & Model Breakdown Table */
    function renderFinOps() {{
      const f = SNAPSHOT.finops || {{}};
      document.getElementById("finops-engine-badge").textContent = f.engine || "native-stdlib";
      const daily = f.daily || [];
      const chartBox = document.getElementById("finops-chart-box");

      if (daily.length === 0) {{
        chartBox.innerHTML = `<div style="font-size:0.8rem; color:var(--fg-secondary);">No daily token records found in window.</div>`;
      }} else {{
        const maxCost = Math.max(1.0, ...daily.map(d => Number(d.totalCost || 0)));
        const w = 520, h = 155, padL = 48, padR = 16, padT = 18, padB = 32;
        const plotW = w - padL - padR;
        const plotH = h - padT - padB;
        const barW = Math.min(44, Math.max(14, (plotW / daily.length) * 0.62));

        let barsSvg = "";
        daily.forEach((d, i) => {{
          const c = Number(d.totalCost || 0);
          const bh = Math.max(3, (c / maxCost) * plotH);
          const x = padL + (i + 0.5) * (plotW / daily.length) - barW / 2;
          const y = padT + plotH - bh;
          const dateLabel = String(d.period || d.date || "").slice(5);
          barsSvg += `
            <rect x="${{x.toFixed(1)}}" y="${{y.toFixed(1)}}" width="${{barW.toFixed(1)}}" height="${{bh.toFixed(1)}}" rx="4" fill="var(--oi-blue)">
              <title>${{esc(d.period || d.date)}}: $${{c.toFixed(2)}} USD (${{fmtNum(d.totalTokens)}} tokens)</title>
            </rect>
            <text x="${{(x + barW / 2).toFixed(1)}}" y="${{(y - 4).toFixed(1)}}" text-anchor="middle" font-size="9.5" fill="var(--fg-secondary)" class="mono">$${{c.toFixed(0)}}</text>
            <text x="${{(x + barW / 2).toFixed(1)}}" y="${{h - 10}}" text-anchor="middle" font-size="10" fill="var(--fg-secondary)" class="mono">${{esc(dateLabel)}}</text>
          `;
        }});

        chartBox.innerHTML = `
          <svg viewBox="0 0 ${{w}} ${{h}}" width="100%" height="155" role="img" aria-label="Daily API-Equivalent USD Burn Chart">
            <line x1="${{padL}}" y1="${{padT + plotH}}" x2="${{w - padR}}" y2="${{padT + plotH}}" stroke="var(--border-strong)" stroke-width="1"/>
            <text x="6" y="${{padT + 8}}" font-size="9.5" fill="var(--fg-secondary)" class="mono">$${{maxCost.toFixed(0)}}</text>
            <text x="6" y="${{padT + plotH}}" font-size="9.5" fill="var(--fg-secondary)" class="mono">$0</text>
            ${{barsSvg}}
          </svg>
        `;
      }}

      const rows = [];
      const focusDay = f.today || (daily.length ? daily[daily.length - 1] : null);
      if (focusDay && focusDay.agents) {{
        for (const ag of focusDay.agents) {{
          for (const m of (ag.modelBreakdowns || [])) {{
            rows.push({{
              model: m.modelName || "unknown",
              agent: (ag.agent || "agent").toUpperCase(),
              inp: Number(m.inputTokens || 0),
              cr: Number(m.cacheReadTokens || 0),
              out: Number(m.outputTokens || 0),
              cost: Number(m.cost || 0)
            }});
          }}
        }}
      }}
      const tbody = document.getElementById("finops-tbody");
      if (rows.length === 0) {{
        tbody.innerHTML = `<tr><th>—</th><td colspan="4">No model breakdowns recorded for focus day.</td></tr>`;
      }} else {{
        tbody.innerHTML = rows.map(r => `
          <tr>
            <th class="mono">${{esc(r.model)}}</th>
            <td class="mono">${{esc(r.agent)}}</td>
            <td class="mono">${{fmtNum(r.inp)}} / ${{fmtNum(r.cr)}}</td>
            <td class="mono">${{fmtNum(r.out)}}</td>
            <td class="mono" style="font-weight:700;">$${{r.cost.toFixed(2)}}</td>
          </tr>
        `).join("");
      }}
    }}

    /* Render RSI Lyapunov Trajectory & Ledger */
    function renderRSI() {{
      const r = SNAPSHOT.rsi || {{}};
      const badge = document.getElementById("rsi-chain-badge");
      badge.textContent = r.chain_verified ? `SHA-256: PASS (${{r.total_cycles}} cycles)` : "SHA-256: BROKEN";
      badge.className = `status-pill ${{r.chain_verified ? "pass" : "warn"}} mono`;

      const p = r.policy || {{}};
      document.getElementById("rsi-policy-chips").innerHTML = [
        `k_rrf=${{p.k_rrf}}`,
        `w_hips=${{p.w_hips}}`,
        `w_fts=${{p.w_fts_with_hips}}`,
        `w_vec=${{p.w_vec}}`,
        `id_boost=${{p.exact_id_boost}}`,
        `graph_1hop=${{p.graph_1hop_boost}}`
      ].map(t => `<span class="chip mono">${{esc(t)}}</span>`).join("");

      const ledger = r.ledger || [];
      const chartBox = document.getElementById("rsi-chart-box");
      if (ledger.length === 0) {{
        chartBox.innerHTML = `<div style="font-size:0.8rem; color:var(--fg-secondary);">No RSI cycles recorded yet in .kb/rsi-ledger.jsonl (run <code>skb evolve</code>).</div>`;
      }} else {{
        const w = 520, h = 140, padL = 48, padR = 20, padT = 18, padB = 28;
        const plotW = w - padL - padR;
        const plotH = h - padT - padB;
        const jVals = ledger.map(e => Number(e.j_after || 0));
        const minJ = Math.max(0, Math.min(...jVals) - 0.02);
        const maxJ = Math.min(1.05, Math.max(...jVals) + 0.02);
        const spanJ = Math.max(0.01, maxJ - minJ);

        const pts = ledger.map((e, i) => {{
          const x = padL + (ledger.length === 1 ? plotW / 2 : (i / (ledger.length - 1)) * plotW);
          const y = padT + plotH - ((Number(e.j_after || 0) - minJ) / spanJ) * plotH;
          return {{ x, y, e }};
        }});
        const poly = pts.map(p => `${{p.x.toFixed(1)}},${{p.y.toFixed(1)}}`).join(" ");
        const circles = pts.map(p => `
          <circle cx="${{p.x.toFixed(1)}}" cy="${{p.y.toFixed(1)}}" r="4.5" fill="var(--oi-green)">
            <title>${{esc(p.e.cycle_id)}}: J=${{Number(p.e.j_after).toFixed(6)}}</title>
          </circle>
          <text x="${{p.x.toFixed(1)}}" y="${{h - 8}}" text-anchor="middle" font-size="9.5" fill="var(--fg-secondary)" class="mono">${{esc(p.e.cycle_id)}}</text>
        `).join("");

        chartBox.innerHTML = `
          <svg viewBox="0 0 ${{w}} ${{h}}" width="100%" height="140" role="img" aria-label="Lyapunov Objective Trajectory">
            <line x1="${{padL}}" y1="${{padT + plotH}}" x2="${{w - padR}}" y2="${{padT + plotH}}" stroke="var(--border-strong)" stroke-width="1"/>
            <text x="4" y="${{padT + 8}}" font-size="9.5" fill="var(--fg-secondary)" class="mono">${{maxJ.toFixed(3)}}</text>
            <text x="4" y="${{padT + plotH}}" font-size="9.5" fill="var(--fg-secondary)" class="mono">${{minJ.toFixed(3)}}</text>
            <polyline fill="none" stroke="var(--oi-green)" stroke-width="2.5" points="${{poly}}"/>
            ${{circles}}
          </svg>
        `;
      }}

      const tbody = document.getElementById("rsi-tbody");
      if (ledger.length === 0) {{
        tbody.innerHTML = `<tr><th>—</th><td colspan="4">Run <code>skb evolve</code> to record SHA-256 chained RSI cycles.</td></tr>`;
      }} else {{
        tbody.innerHTML = ledger.slice().reverse().map(e => {{
          const jb = Number(e.j_before || 0).toFixed(4);
          const ja = Number(e.j_after || 0).toFixed(4);
          const dj = Number(e.delta_j || 0);
          const mrr = e.after && e.after.mrr_at_5 !== undefined ? Number(e.after.mrr_at_5).toFixed(3) : "—";
          const rec = e.after && e.after.recall_at_1 !== undefined ? Number(e.after.recall_at_1).toFixed(3) : "—";
          const hashShort = String(e.entry_hash || "").slice(0, 14) + "…";
          return `
            <tr>
              <th class="mono">${{esc(e.cycle_id)}}</th>
              <td class="mono">${{jb}} → ${{ja}}</td>
              <td class="mono">${{mrr}} / ${{rec}}</td>
              <td class="mono" style="color:var(--oi-green); font-weight:700;">${{dj >= 0 ? "+" : ""}}${{dj.toFixed(5)}}</td>
              <td class="mono" title="${{esc(e.entry_hash)}}">${{esc(hashShort)}}</td>
            </tr>
          `;
        }}).join("");
      }}
    }}

    /* Interactive 2D Force-Directed Knowledge Graph */
    let graphNodes = [];
    let graphEdges = [];
    let selectedNodeId = null;
    let hoveredNodeId = null;
    let activeTierFilter = null;
    let searchFilterText = "";
    let camX = 0, camY = 0, camScale = 1.0;
    let draggingNode = null;
    let isPanning = false;
    let panStartX = 0, panStartY = 0;

    function initGraph() {{
      const rawNodes = (SNAPSHOT.graph && SNAPSHOT.graph.nodes) || [];
      const rawEdges = (SNAPSHOT.graph && SNAPSHOT.graph.edges) || [];
      const N = rawNodes.length;
      graphNodes = rawNodes.map((n, i) => {{
        const angle = (2 * Math.PI * i) / Math.max(1, N);
        const radius = n.id === "INDEX.md" ? 0 : 120 + (i % 3) * 45;
        return {{
          ...n,
          x: Math.cos(angle) * radius,
          y: Math.sin(angle) * radius,
          vx: 0,
          vy: 0,
          r: Math.min(18, Math.max(6, 5.5 + Math.sqrt(n.total_degree || 1) * 1.9))
        }};
      }});
      const nodeMap = new Map(graphNodes.map(n => [n.id, n]));
      graphEdges = rawEdges
        .map(e => ({{ source: nodeMap.get(e.source), target: nodeMap.get(e.target) }}))
        .filter(e => e.source && e.target);

      const legendEl = document.getElementById("graph-legend");
      const tc = (SNAPSHOT.graph && SNAPSHOT.graph.tier_counts) || {{}};
      legendEl.innerHTML = Object.entries(TIER_COLORS)
        .filter(([k]) => tc[k] > 0)
        .map(([k, col]) => `
          <span class="legend-item mono" data-tier="${{esc(k)}}">
            <span class="legend-dot" style="background:${{col}};"></span>
            ${{esc(k)}} (${{tc[k]}})
          </span>
        `).join("");

      legendEl.querySelectorAll("[data-tier]").forEach(el => {{
        el.addEventListener("click", () => {{
          const t = el.getAttribute("data-tier");
          activeTierFilter = activeTierFilter === t ? null : t;
          drawGraph();
        }});
      }});

      for (let step = 0; step < 160; step++) stepPhysics();
      drawGraph();
      if (graphNodes.length > 0) {{
        const defaultNode = graphNodes.find(n => n.id.includes("autonomous-engineering")) || graphNodes[0];
        selectNode(defaultNode.id);
      }}
    }}

    function stepPhysics() {{
      const N = graphNodes.length;
      for (let i = 0; i < N; i++) {{
        const a = graphNodes[i];
        a.vx *= 0.82;
        a.vy *= 0.82;
        a.vx += -a.x * 0.012;
        a.vy += -a.y * 0.012;
        for (let j = i + 1; j < N; j++) {{
          const b = graphNodes[j];
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          let d2 = dx * dx + dy * dy + 16;
          let f = 1400 / d2;
          let dist = Math.sqrt(d2);
          let fx = (dx / dist) * f;
          let fy = (dy / dist) * f;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }}
      }}
      for (const e of graphEdges) {{
        const a = e.source, b = e.target;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const targetLen = (a.doc_type === "moc" || b.doc_type === "moc") ? 150 : 95;
        const f = (dist - targetLen) * 0.025;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }}
      for (const n of graphNodes) {{
        if (n !== draggingNode) {{
          n.x += Math.max(-12, Math.min(12, n.vx));
          n.y += Math.max(-12, Math.min(12, n.vy));
        }}
      }}
    }}

    function isNodeVisible(n) {{
      if (activeTierFilter && n.tier_folder !== activeTierFilter) return false;
      if (searchFilterText) {{
        const q = searchFilterText.toLowerCase();
        const hay = `${{n.id}} ${{n.title}} ${{n.tags}}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }}
      return true;
    }}

    function drawGraph() {{
      const canvas = document.getElementById("graph-canvas");
      const rect = canvas.parentElement.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.floor(rect.width * dpr) || canvas.height !== Math.floor(rect.height * dpr)) {{
        canvas.width = Math.floor(rect.width * dpr);
        canvas.height = Math.floor(rect.height * dpr);
      }}
      const ctx = canvas.getContext("2d");
      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, rect.width, rect.height);

      ctx.translate(rect.width / 2 + camX, rect.height / 2 + camY);
      ctx.scale(camScale, camScale);

      const focusId = hoveredNodeId || selectedNodeId;
      const connected = new Set();
      if (focusId) {{
        connected.add(focusId);
        for (const e of graphEdges) {{
          if (e.source.id === focusId) connected.add(e.target.id);
          if (e.target.id === focusId) connected.add(e.source.id);
        }}
      }}

      for (const e of graphEdges) {{
        const vis = isNodeVisible(e.source) && isNodeVisible(e.target);
        const hi = focusId && (e.source.id === focusId || e.target.id === focusId);
        ctx.beginPath();
        ctx.moveTo(e.source.x, e.source.y);
        ctx.lineTo(e.target.x, e.target.y);
        ctx.strokeStyle = hi ? "rgba(56, 189, 248, 0.85)" : "rgba(148, 163, 184, 0.25)";
        ctx.globalAlpha = vis ? 1.0 : 0.1;
        ctx.lineWidth = hi ? 2.0 : 1.0;
        ctx.stroke();
      }}

      for (const n of graphNodes) {{
        const vis = isNodeVisible(n);
        const dim = focusId && !connected.has(n.id);
        ctx.globalAlpha = (!vis || dim) ? 0.2 : 1.0;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = TIER_COLORS[n.tier_folder] || TIER_COLORS.other;
        ctx.fill();
        if (n.id === selectedNodeId || n.id === hoveredNodeId) {{
          ctx.lineWidth = 2.5;
          ctx.strokeStyle = "#ffffff";
          ctx.stroke();
        }}
        if (camScale >= 0.75 || n.id === selectedNodeId || n.id === hoveredNodeId) {{
          ctx.font = "600 10.5px -apple-system, BlinkMacSystemFont, sans-serif";
          ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--fg-primary") || "#cbd5e1";
          const shortLabel = n.id.split("/").pop().replace(".md", "");
          ctx.fillText(shortLabel, n.x + n.r + 4, n.y + 3.5);
        }}
      }}
      ctx.restore();
    }}

    function canvasHitTest(clientX, clientY) {{
      const canvas = document.getElementById("graph-canvas");
      const rect = canvas.getBoundingClientRect();
      const wx = (clientX - rect.left - rect.width / 2 - camX) / camScale;
      const wy = (clientY - rect.top - rect.height / 2 - camY) / camScale;
      for (let i = graphNodes.length - 1; i >= 0; i--) {{
        const n = graphNodes[i];
        const dx = wx - n.x, dy = wy - n.y;
        if (dx * dx + dy * dy <= (n.r + 4) * (n.r + 4)) return n;
      }}
      return null;
    }}

    const canvasEl = document.getElementById("graph-canvas");
    canvasEl.addEventListener("mousedown", (e) => {{
      const hit = canvasHitTest(e.clientX, e.clientY);
      if (hit) {{
        draggingNode = hit;
        selectNode(hit.id);
      }} else {{
        isPanning = true;
        panStartX = e.clientX - camX;
        panStartY = e.clientY - camY;
      }}
    }});

    window.addEventListener("mousemove", (e) => {{
      if (draggingNode) {{
        const rect = canvasEl.getBoundingClientRect();
        draggingNode.x = (e.clientX - rect.left - rect.width / 2 - camX) / camScale;
        draggingNode.y = (e.clientY - rect.top - rect.height / 2 - camY) / camScale;
        stepPhysics();
        drawGraph();
      }} else if (isPanning) {{
        camX = e.clientX - panStartX;
        camY = e.clientY - panStartY;
        drawGraph();
      }} else if (e.target === canvasEl) {{
        const hit = canvasHitTest(e.clientX, e.clientY);
        const nextHover = hit ? hit.id : null;
        if (nextHover !== hoveredNodeId) {{
          hoveredNodeId = nextHover;
          drawGraph();
        }}
      }}
    }});

    window.addEventListener("mouseup", () => {{
      draggingNode = null;
      isPanning = false;
    }});

    canvasEl.addEventListener("wheel", (e) => {{
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 0.9;
      camScale = Math.max(0.35, Math.min(3.0, camScale * factor));
      drawGraph();
    }}, {{ passive: false }});

    document.getElementById("graph-filter").addEventListener("input", (e) => {{
      searchFilterText = e.target.value.trim();
      drawGraph();
    }});

    document.getElementById("graph-reset-btn").addEventListener("click", () => {{
      camX = 0; camY = 0; camScale = 1.0; activeTierFilter = null; searchFilterText = "";
      document.getElementById("graph-filter").value = "";
      drawGraph();
    }});

    window.addEventListener("resize", () => drawGraph());

    async function selectNode(relPath) {{
      selectedNodeId = relPath;
      drawGraph();
      const node = graphNodes.find(n => n.id === relPath);
      if (!node) return;
      document.getElementById("insp-title").textContent = node.title;
      document.getElementById("insp-type").textContent = node.doc_type;
      document.getElementById("insp-path").textContent = node.id;
      document.getElementById("insp-meta").textContent = `Updated: ${{node.updated || "—"}} • Chunks: ${{node.chunks}} • Size: ${{fmtBytes(node.bytes)}} • Tags: ${{node.tags || "none"}}`;

      const inLinks = graphEdges.filter(e => e.target.id === relPath).map(e => e.source.id);
      const outLinks = graphEdges.filter(e => e.source.id === relPath).map(e => e.target.id);

      const inEl = document.getElementById("insp-inbound");
      inEl.innerHTML = inLinks.length
        ? inLinks.map(p => `<span class="chip mono" data-jump="${{esc(p)}}">← ${{esc(p)}}</span>`).join("")
        : `<span style="font-size:0.75rem; color:var(--fg-muted);">0 inbound links</span>`;

      const outEl = document.getElementById("insp-outbound");
      outEl.innerHTML = outLinks.length
        ? outLinks.map(p => `<span class="chip mono" data-jump="${{esc(p)}}">→ ${{esc(p)}}</span>`).join("")
        : `<span style="font-size:0.75rem; color:var(--fg-muted);">0 outbound links</span>`;

      document.querySelectorAll("[data-jump]").forEach(el => {{
        el.addEventListener("click", () => selectNode(el.getAttribute("data-jump")));
      }});

      if (LIVE_SERVER) {{
        try {{
          const vPath = encodeURIComponent(SNAPSHOT.vault.path);
          const nPath = encodeURIComponent(relPath);
          const res = await fetch(`/api/note?vault=${{vPath}}&path=${{nPath}}`);
          if (res.ok) {{
            const data = await res.json();
            document.getElementById("insp-content").textContent = data.content || "";
            return;
          }}
        }} catch (_) {{}}
      }}
      const hitMatch = (SNAPSHOT.initial_search && SNAPSHOT.initial_search.results || []).find(r => r.rel_path === relPath);
      document.getElementById("insp-content").textContent = hitMatch
        ? hitMatch.content
        : `Note: ${{node.title}} (${{node.id}})\\nTags: ${{node.tags}}\\nRun 'skb dashboard' live server to stream full Markdown bodies on click.`;
    }}

    /* Live Search Form Handler */
    document.getElementById("search-form").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const q = document.getElementById("search-input").value.trim();
      const eng = document.getElementById("search-engine").value;
      if (!q) return;
      if (LIVE_SERVER) {{
        const vPath = encodeURIComponent(SNAPSHOT.vault.path);
        const res = await fetch(`/api/search?vault=${{vPath}}&q=${{encodeURIComponent(q)}}&engine=${{encodeURIComponent(eng)}}&limit=6`);
        if (res.ok) {{
          const data = await res.json();
          renderSearchHits(data);
          return;
        }}
      }}
      searchFilterText = q;
      document.getElementById("graph-filter").value = q;
      drawGraph();
    }});

    /* Vault Switcher & Refresh */
    async function reloadVault(vaultPath) {{
      if (!LIVE_SERVER) return;
      const res = await fetch(`/api/snapshot?vault=${{encodeURIComponent(vaultPath)}}`);
      if (res.ok) {{
        SNAPSHOT = await res.json();
        renderAll();
      }}
    }}

    document.getElementById("vault-select").addEventListener("change", (e) => reloadVault(e.target.value));
    document.getElementById("refresh-btn").addEventListener("click", () => reloadVault(SNAPSHOT.vault.path));

    function renderAll() {{
      renderTopTelemetry();
      renderSearchHits(SNAPSHOT.initial_search);
      renderFinOps();
      renderRSI();
      initGraph();
    }}

    renderAll();
  </script>
</body>
</html>
"""


def serve_dashboard(
    initial_vault: Path,
    *,
    host: str,
    port: int,
    days: int,
    open_browser: bool,
    snapshot_builder: Callable[[Path, int], dict[str, Any]],
    search_runner: Callable[[Path, str, str, int], dict[str, Any]],
    allowed_roots: Callable[[Path], set[Path]],
) -> None:
    """Start a zero-dependency ThreadingHTTPServer serving the interactive Sovereign-KB dashboard."""

    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _resolve_requested_vault(self, query_params: dict[str, list[str]]) -> Path:
            raw = (query_params.get("vault") or [str(initial_vault)])[0]
            cand = Path(raw).expanduser().resolve()
            valid = allowed_roots(initial_vault)
            if cand in valid or (cand / ".kb").is_dir() or (cand / "INDEX.md").is_file():
                return cand
            return initial_vault

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            vault_dir = self._resolve_requested_vault(qs)

            if parsed.path in ("/", "/index.html"):
                snap = snapshot_builder(vault_dir, days)
                html_doc = render_dashboard_html(snap, live_server=True).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_doc)))
                self.end_headers()
                self.wfile.write(html_doc)
                return

            if parsed.path == "/api/snapshot":
                req_days = int((qs.get("days") or [str(days)])[0])
                snap = snapshot_builder(vault_dir, req_days)
                self._send_json(snap)
                return

            if parsed.path == "/api/search":
                q = (qs.get("q") or ["6-Pillar Deterministic"])[0]
                eng = (qs.get("engine") or ["auto"])[0]
                limit = int((qs.get("limit") or ["6"])[0])
                res = search_runner(vault_dir, q, eng, limit)
                self._send_json(res)
                return

            if parsed.path == "/api/note":
                rel = (qs.get("path") or ["INDEX.md"])[0]
                target = (vault_dir / rel).resolve()
                if not str(target).startswith(str(vault_dir.resolve())) or not target.is_file():
                    self._send_json({"error": "Note not found"}, status=404)
                    return
                self._send_json(
                    {
                        "rel_path": rel,
                        "content": target.read_text(encoding="utf-8", errors="replace"),
                    }
                )
                return

            self._send_json({"error": "Not found"}, status=404)

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}"
    print("=== Sovereign-KB Zero-Dependency Observability Dashboard ===")
    print(f"  Active Vault : {initial_vault}")
    print(f"  Live Web UI  : {url}")
    print(f"  JSON API     : {url}/api/snapshot")
    print("  Press Ctrl+C to stop the server.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[skb dashboard] Stopped.")
    finally:
        server.server_close()
