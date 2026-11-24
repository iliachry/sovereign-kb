"""Multi-agent FinOps telemetry scanner and observability dashboard launcher."""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from .config_db import CONFIG_PATH, get_db, resolve_hips_bin, resolve_vault_dir
from .markdown_embed import build_stem_lookup, extract_wikilinks, list_vault_md_files, parse_frontmatter
from .retrieval_notes import hybrid_retrieve
from .sync_hips import cmd_sync, hips_retrieve


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
            hybrid_retrieve_fn=getattr(sys.modules.get("sovereign_kb.cli"), "hybrid_retrieve", hybrid_retrieve),
            hips_retrieve_fn=hips_retrieve,
            resolve_hips_bin_fn=resolve_hips_bin,
            collect_usage_fn=collect_usage_payload,
            config_path=getattr(sys.modules.get("sovereign_kb.cli"), "CONFIG_PATH", CONFIG_PATH),
            days=req_days,
            native_usage_only=native_only,
            no_gx10=no_gx10,
        )

    def _run_search(v_dir: Path, query: str, engine: str, limit: int) -> dict:
        conn = get_db(v_dir)
        t0 = time.perf_counter()
        hits = getattr(sys.modules.get("sovereign_kb.cli"), "hybrid_retrieve", hybrid_retrieve)(
            conn, v_dir, query, top_k=limit, engine=engine
        )
        lat_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        conn.close()
        return {"query": query, "engine": engine, "latency_ms": lat_ms, "results": hits}

    def _allowed_roots(v_dir: Path) -> set[Path]:
        return {
            Path(item["path"]).resolve()
            for item in discover_vaults(v_dir, getattr(sys.modules.get("sovereign_kb.cli"), "CONFIG_PATH", CONFIG_PATH))
        }

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
