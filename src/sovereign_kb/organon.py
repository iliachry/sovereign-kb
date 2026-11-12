"""
The Sovereign Organon (`organon`) — Unified CLI for Episteme (`kb`), Techne (`swm`), and Phronesis (`gwi`).

Provides a single deterministic Python entrypoint for:
  - `organon status [--json] [--all-nodes]` : Unified 3-pillar telemetry across `kb`, `swm`, `gwi`, and cluster nodes
  - `organon showcase [--json]`             : Live interactive showcase & commandbook for interacting with the Organon
  - `organon verify`                        : 6-Gate KB verification + GWI provenance registry + SWM control-plane check
  - `organon audit [dir]`                   : Run-bound GWI 9+ gate (`--strict --strict-lenses --execute-gate`)
  - `organon task <OP#id> [--execute]`      : Assemble KB context pack, verify OpenProject ground truth, and run SWM
  - `organon finish [dir] [-m <msg>]`       : Pre-completion gate (`readme_check` + `gwi --execute-gate` + `kb verify` + sync)
  - `organon sync-state`                    : Non-destructively merge `~/.swm/audit_registry.json` with configured remote peer
  - `organon kb | swm | gwi ...`            : Direct pillar pass-throughs
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .audit_registry import (
    AUDIT_REGISTRY_PATH,
    evaluate_subproject_freshness,
    get_sovereign_versions,
    is_bundled_starter_vault_path,
    load_audit_registry,
    resolve_organon_vault_state,
    resolve_swm_bin,
)
from .bridge import (
    BRIDGE_CONTRACT_VERSION,
    retrieve_vault_precedents,
    verify_triad_provenance_chain,
)
from .chain import (
    PISTIS_PROTOCOL_VERSION,
    anchor_organon_attestation,
    cmd_chain,
    collect_pistis_summary,
    register_chain_subparser,
)
from .evr import cmd_evr, register_evr_subparser
from .gwi import GWI_STANDARD_VERSION
from .sessions import _get_cluster_env

_VAULT_FREE_SKB_CMDS = {
    "-h",
    "--help",
    "--help-all",
    "--version",
    "-V",
    "init",
    "scaffold",
    "new",
    "skills",
    "sessions",
    "chats",
    "ps",
    "usage",
    "finops",
    "gwi",
    "goldwood",
    "audit",
    "video",
    "chain",
    "pistis",
    "blockchain",
    "evr",
    "everything",
}


def _is_initialized_vault(vault_path: Path) -> bool:
    if is_bundled_starter_vault_path(vault_path):
        return False
    return vault_path.is_dir() and ((vault_path / ".kb").is_dir() or (vault_path / "INDEX.md").is_file())


def _vault_error_message(prefix: str, vault_path: Path, vault_source: str) -> str:
    if vault_source == "forbidden_starter" or is_bundled_starter_vault_path(vault_path):
        return (
            f"[{prefix}] Error: Bundled 'starter_vault' ({vault_path}) is a read-only package template and "
            "cannot be used as a live vault. Run 'skb init ~/notes --save-default' instead."
        )
    return (
        f"[{prefix}] Error: Configured vault '{vault_path}' ({vault_source}) does not exist or is not initialized. "
        f"Run 'skb init {vault_path}' first."
    )


def _resolve_openproject_bin() -> Path | None:
    """Locate optional `openproject-query` helper, remaining hermetic under pytest unless explicitly overridden."""
    if "OPENPROJECT_QUERY_BIN" in os.environ:
        raw = os.environ["OPENPROJECT_QUERY_BIN"].strip()
        if not raw:
            return None
        cand = Path(raw).expanduser()
        return cand.resolve() if cand.is_file() and os.access(cand, os.X_OK) else None
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    which_op = shutil.which("openproject-query")
    if which_op:
        return Path(which_op).resolve()
    local_op = Path.home() / ".local" / "bin" / "openproject-query"
    if local_op.is_file() and os.access(local_op, os.X_OK):
        return local_op.resolve()
    return None


def _print_swm_open_core_notice(subargs: list[str] | None = None, as_json: bool = False) -> int:
    """Emit a clean Open-Core diagnostic when `swm` (`software-machine`) is not installed."""
    args_in = list(subargs or [])
    if "-h" in args_in or "--help" in args_in:
        print(
            "usage: organon swm [subcommand]\n\n"
            "Techne (`swm` — Autonomous 6-Stage Software Machine) is Goldwood Technologies'\n"
            "proprietary studio orchestrator and is not installed in this Open-Core environment.\n\n"
            "Open-source Organon pillars available locally right now:\n"
            "  • Episteme  (`organon kb ...` / `skb`)       — 4-Way Hybrid Markdown Memory, Soar 9.6 & RSI\n"
            "  • Phronesis (`organon audit .` / `gwi`)      — Goldwood Index (G >= 9.0) & Laws L0-L9 Gate\n"
            "  • Pistis    (`organon chain ...`)            — RFC 6962 Merkle Proofs, NC FSM & Tokenomics\n"
            "  • Polyglot  (`organon evr ...` / `evr`)      — Zero-Copy `.evr` Verified Artifact Capsules\n\n"
            "Learn more: https://goldwood.tech/organon"
        )
        return 0

    payload = {
        "installed": False,
        "edition": "open-core",
        "pillar": "techne_swm",
        "message": (
            "Techne (`swm` — Goldwood Studio Engine) is not installed in this Open-Core environment. "
            "Use `organon kb`, `organon audit`, `organon chain`, or `organon evr` locally."
        ),
        "url": "https://goldwood.tech/organon",
    }
    if as_json or "--json" in args_in:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "[organon] Techne (`swm` — Goldwood Studio Engine) is not installed in this Open-Core environment.\n"
            "          Open-source pillars active locally: `organon kb`, `organon audit`, `organon chain`, `organon evr`.\n"
            "          Architecture & live explorer: https://goldwood.tech/organon",
            file=sys.stderr,
        )
    return 2


def _resolve_local_node_name() -> str:
    env_node = _get_cluster_env("KB_LOCAL_NODE")
    if env_node:
        return env_node
    host = socket.gethostname().split(".")[0].strip()
    return host or "local-workstation"


def _resolve_cluster_peers() -> list[str]:
    peers: list[str] = []
    for var in ("KB_REMOTE_PEER", "KB_GPU_PEER"):
        val = _get_cluster_env(var)
        if val and val not in peers:
            peers.append(val)
    return peers


def _parse_iso_epoch(ts: Any) -> float:
    """Parse an ISO-8601 timestamp string into UTC epoch seconds (-1.0 if missing/unparseable)."""
    raw = str(ts or "").strip()
    if not raw:
        return -1.0
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).timestamp()
    except Exception:
        return -1.0


def _should_replace_record(
    l_rec: dict[str, Any] | None, r_rec: dict[str, Any] | None, status_key: str = "status"
) -> bool:
    """Return True if `r_rec` is strictly newer in UTC epoch time, or ties timestamp and carries a FAIL/NOT_APPROVED status."""
    if not isinstance(r_rec, dict):
        return False
    if not isinstance(l_rec, dict):
        return True
    r_ep = _parse_iso_epoch(r_rec.get("audited_at"))
    l_ep = _parse_iso_epoch(l_rec.get("audited_at"))
    if r_ep >= 0.0 and l_ep >= 0.0:
        if r_ep > l_ep:
            return True
        if r_ep < l_ep:
            return False
        # Equal UTC timestamp: fail closed (FAIL / REVISE / NOT_APPROVED wins over PASS / APPROVED)
        r_st = str(r_rec.get(status_key) or "").upper()
        l_st = str(l_rec.get(status_key) or "").upper()
        bad_states = {"FAIL", "FAILED", "REVISE", "NOT_APPROVED", "REQUEST_CHANGES", "VETO"}
        if r_st in bad_states and l_st not in bad_states:
            return True
        return False
    return str(r_rec.get("audited_at") or "") > str(l_rec.get("audited_at") or "")


def merge_audit_registries(local_reg: dict[str, Any], remote_reg: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministically merge two `audit_registry.json` structures by comparing per-record `audited_at`
    UTC epoch timestamps on `last_audit`, `triad`, and each `subprojects[subpath]` entry.
    Never discards a newer remote FAIL or an additional remote subproject.
    """
    merged = copy.deepcopy(local_reg if isinstance(local_reg, dict) else {})
    l_projects = merged.setdefault("projects", {})
    r_projects = (remote_reg.get("projects") or {}) if isinstance(remote_reg, dict) else {}

    for proj_key, r_proj in r_projects.items():
        if not isinstance(r_proj, dict):
            continue
        if proj_key not in l_projects or not isinstance(l_projects.get(proj_key), dict):
            l_projects[proj_key] = copy.deepcopy(r_proj)
            continue

        l_proj = l_projects[proj_key]
        # Merge top-level last_audit by UTC epoch audited_at
        r_last = r_proj.get("last_audit")
        l_last = l_proj.get("last_audit")
        if isinstance(r_last, dict) and _should_replace_record(l_last, r_last, status_key="status"):
            l_proj["last_audit"] = copy.deepcopy(r_last)

        # Merge top-level triad by UTC epoch audited_at
        r_triad = r_proj.get("triad")
        l_triad = l_proj.get("triad")
        if isinstance(r_triad, dict) and r_triad.get("ever_audited"):
            if _should_replace_record(l_triad, r_triad, status_key="disposition"):
                l_proj["triad"] = copy.deepcopy(r_triad)

        # Merge subprojects dictionary entry-by-entry
        r_subs = r_proj.get("subprojects")
        if isinstance(r_subs, dict):
            l_subs = l_proj.setdefault("subprojects", {})
            for sp_key, r_sp in r_subs.items():
                if not isinstance(r_sp, dict):
                    continue
                l_sp = l_subs.get(sp_key)
                if not isinstance(l_sp, dict):
                    l_subs[sp_key] = copy.deepcopy(r_sp)
                    continue
                if _should_replace_record(l_sp, r_sp, status_key="status"):
                    merged_sp = copy.deepcopy(r_sp)
                    # Preserve whichever subproject triad record is newer
                    r_sp_tr = r_sp.get("triad") if isinstance(r_sp.get("triad"), dict) else {}
                    l_sp_tr = l_sp.get("triad") if isinstance(l_sp.get("triad"), dict) else {}
                    if l_sp_tr.get("ever_audited") and _should_replace_record(
                        r_sp_tr, l_sp_tr, status_key="disposition"
                    ):
                        merged_sp["triad"] = copy.deepcopy(l_sp_tr)
                    l_subs[sp_key] = merged_sp
                else:
                    r_sp_tr = r_sp.get("triad") if isinstance(r_sp.get("triad"), dict) else {}
                    l_sp_tr = l_sp.get("triad") if isinstance(l_sp.get("triad"), dict) else {}
                    if r_sp_tr.get("ever_audited") and _should_replace_record(
                        l_sp_tr, r_sp_tr, status_key="disposition"
                    ):
                        l_sp["triad"] = copy.deepcopy(r_sp_tr)

    merged["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return merged


def collect_organon_status(all_nodes: bool = False) -> dict[str, Any]:
    """Collect unified telemetry across Episteme (`kb`), Techne (`swm`), and Phronesis (`gwi`)."""
    versions = get_sovereign_versions()
    me_kb, vault_source = resolve_organon_vault_state()
    vault_configured = vault_source not in ("unconfigured", "forbidden_starter") and _is_initialized_vault(me_kb)
    kb_db = me_kb / ".kb" / "index.db"

    # 1. Episteme (`kb`)
    episteme: dict[str, Any] = {
        "vault_dir": str(me_kb),
        "configured": vault_configured,
        "vault_source": vault_source,
        "version": versions.get("kb_version", __version__),
        "git_sha": versions.get("me_kb_sha", "unknown"),
        "engine_sha": versions.get("kb_sha", "unknown"),
        "bridge_contract_version": BRIDGE_CONTRACT_VERSION,
        "documents": 0,
        "chunks": 0,
        "vectors": 0,
        "links": 0,
        "hips_ready": vault_configured
        and (me_kb / ".kb" / "hips-index").is_dir()
        and any((me_kb / ".kb" / "hips-index").iterdir()),
        "soar_chunks": 0,
        "provenance_events": 0,
        "provenance_chain_valid": True,
        "outcome_probes": 0,
    }
    if vault_configured and kb_db.is_file():
        try:
            conn = sqlite3.connect(kb_db)
            episteme["documents"] = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            episteme["chunks"] = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            episteme["vectors"] = int(
                conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
            )
            episteme["links"] = int(conn.execute("SELECT COUNT(*) FROM links").fetchone()[0])
            conn.close()
        except Exception:
            pass

    soar_total = 0
    for db_candidate in (kb_db, Path.home() / ".swm" / "soar_chunks.db"):
        if db_candidate.is_file():
            try:
                c_soar = sqlite3.connect(db_candidate)
                soar_total += int(c_soar.execute("SELECT COUNT(*) FROM soar_chunks").fetchone()[0])
                c_soar.close()
            except Exception:
                pass
    if soar_total == 0:
        for s_file in (me_kb / ".kb" / "learned_chunks.soar", Path.home() / ".swm" / "soar_learned_chunks.soar"):
            if s_file.is_file():
                try:
                    soar_total += sum(
                        1
                        for ln in s_file.read_text(encoding="utf-8", errors="replace").splitlines()
                        if ln.strip().startswith("sp {")
                    )
                except Exception:
                    pass
    episteme["soar_chunks"] = soar_total

    probes_file = me_kb / ".kb" / "gwi_outcome_probes.json"
    if probes_file.is_file():
        try:
            pdata = json.loads(probes_file.read_text(encoding="utf-8"))
            if isinstance(pdata, list):
                episteme["outcome_probes"] = len(pdata)
        except Exception:
            pass

    chain_ok, chain_events = verify_triad_provenance_chain()
    episteme["provenance_chain_valid"] = bool(chain_ok)
    episteme["provenance_events"] = int(chain_events)

    # 2. Techne (`swm`) — reads ~/.swm/daemon.json and ~/.swm/runs/*.json when installed
    swm_bin = resolve_swm_bin()
    swm_installed = swm_bin is not None
    swm_dir = Path.home() / ".swm"
    daemon_path = swm_dir / "daemon.json"
    runs_dir = swm_dir / "runs"
    techne: dict[str, Any] = {
        "installed": swm_installed,
        "edition": "studio" if swm_installed else "open-core",
        "version": versions.get("swm_version", "open-core" if not swm_installed else "unknown"),
        "git_sha": versions.get("swm_sha", "not-installed" if not swm_installed else "unknown"),
        "state_path": str(swm_dir),
        "total_runs": 0,
        "completed_runs": 0,
        "failed_runs": 0,
        "in_progress_runs": 0,
        "daemon_running": False,
        "dry_run_mode": True,
        "last_poll_at": None,
        "last_poll_age_s": None,
    }
    if daemon_path.is_file():
        try:
            d_st = json.loads(daemon_path.read_text(encoding="utf-8"))
            if isinstance(d_st, dict):
                techne["daemon_running"] = bool(d_st.get("enabled", False))
                techne["dry_run_mode"] = bool(d_st.get("dry_run_mode", True))
                techne["last_poll_at"] = d_st.get("last_poll_at")
                if d_st.get("last_poll_at"):
                    try:
                        dt = datetime.datetime.fromisoformat(str(d_st["last_poll_at"]).replace("Z", "+00:00"))
                        techne["last_poll_age_s"] = int(
                            max(0.0, (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds())
                        )
                    except Exception:
                        pass
        except Exception:
            pass

    if runs_dir.is_dir():
        run_files = sorted(runs_dir.glob("*.json"))
        techne["total_runs"] = len(run_files)
        for rf in run_files:
            try:
                robj = json.loads(rf.read_text(encoding="utf-8"))
                st_val = str(robj.get("stage") or "").upper()
                if st_val in ("COMPLETED", "APPROVED", "MERGED"):
                    techne["completed_runs"] += 1
                elif st_val in ("FAILED", "REJECTED", "ABORTED"):
                    techne["failed_runs"] += 1
                else:
                    techne["in_progress_runs"] += 1
            except Exception:
                pass

    # 3. Phronesis (`gwi`) — distinguishes recorded_pass_targets vs live fresh_pass_targets
    reg = load_audit_registry()
    projects_map = reg.get("projects") if isinstance(reg.get("projects"), dict) else {}
    total_targets = 0
    recorded_pass = 0
    fresh_pass = 0
    triad_count = 0
    g_scores: list[float] = []
    check_live_freshness = not bool(os.environ.get("PYTEST_CURRENT_TEST"))
    for proj_entry in projects_map.values():
        if not isinstance(proj_entry, dict):
            continue
        repo_root_str = str(proj_entry.get("repo_root") or "")
        repo_root_path = Path(repo_root_str) if repo_root_str else None
        repo_exists = bool(repo_root_path and repo_root_path.is_dir())
        subs = proj_entry.get("subprojects")
        if isinstance(subs, dict) and subs:
            for sp_key, sp_entry in subs.items():
                if not isinstance(sp_entry, dict):
                    continue
                total_targets += 1
                if sp_entry.get("status") == "PASS":
                    recorded_pass += 1
                    sp_ver = sp_entry.get("versions") or {}
                    ver_match = bool(
                        sp_ver.get("kb_sha")
                        and sp_ver.get("kb_sha") == versions.get("kb_sha")
                        and sp_ver.get("gwi_version") == versions.get("gwi_version")
                    )
                    if check_live_freshness and repo_exists and repo_root_path is not None and ver_match:
                        try:
                            f_info = evaluate_subproject_freshness(repo_root_path, str(sp_key))
                            if f_info.get("is_fresh_pass"):
                                fresh_pass += 1
                        except Exception:
                            pass
                    elif not check_live_freshness:
                        fresh_pass += 1
                tr = sp_entry.get("triad") or proj_entry.get("triad") or {}
                if tr.get("ever_audited"):
                    triad_count += 1
                g_val = sp_entry.get("coupled_g") or sp_entry.get("goldwood_g")
                if isinstance(g_val, (int, float)):
                    g_scores.append(float(g_val))
        else:
            total_targets += 1
            last_a = proj_entry.get("last_audit") or {}
            if last_a.get("status") == "PASS":
                recorded_pass += 1
                la_ver = last_a.get("versions") or {}
                ver_match = bool(
                    la_ver.get("kb_sha")
                    and la_ver.get("kb_sha") == versions.get("kb_sha")
                    and la_ver.get("gwi_version") == versions.get("gwi_version")
                )
                if check_live_freshness and repo_exists and repo_root_path is not None and ver_match:
                    try:
                        f_info = evaluate_subproject_freshness(repo_root_path, ".")
                        if f_info.get("is_fresh_pass"):
                            fresh_pass += 1
                    except Exception:
                        pass
                elif not check_live_freshness:
                    fresh_pass += 1
            tr = proj_entry.get("triad") or {}
            if tr.get("ever_audited"):
                triad_count += 1
            g_val = last_a.get("coupled_g") or last_a.get("goldwood_g")
            if isinstance(g_val, (int, float)):
                g_scores.append(float(g_val))

    phronesis: dict[str, Any] = {
        "standard_version": GWI_STANDARD_VERSION,
        "registry_path": str(AUDIT_REGISTRY_PATH),
        "total_targets": total_targets,
        "recorded_pass_targets": recorded_pass,
        "fresh_pass_targets": fresh_pass,
        "triad_audited_targets": triad_count,
        "mean_goldwood_g": round(sum(g_scores) / len(g_scores), 2) if g_scores else None,
        "min_goldwood_g": round(min(g_scores), 2) if g_scores else None,
    }

    # 4. Cluster Nodes
    local_node = _resolve_local_node_name()
    cluster: dict[str, Any] = {"local_node": local_node, "peers": {}}
    if all_nodes:
        for peer in _resolve_cluster_peers():
            if peer == local_node:
                continue
            t0 = time.perf_counter()
            try:
                proc = subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", peer, "hostname && uptime"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                lat_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                cluster["peers"][peer] = {
                    "reachable": proc.returncode == 0,
                    "latency_ms": lat_ms,
                    "summary": " | ".join(line.strip() for line in (proc.stdout or "").splitlines() if line.strip())[
                        :120
                    ],
                }
            except Exception as exc:
                cluster["peers"][peer] = {"reachable": False, "error": str(exc)}

    try:
        pistis = collect_pistis_summary()
    except Exception as exc:
        pistis = {
            "protocol_version": PISTIS_PROTOCOL_VERSION,
            "anchors_path": "",
            "anchors_chain_valid": False,
            "merkle_anchors_count": 0,
            "latest_merkle_root": None,
            "nc_fsm_states": 7,
            "iso_territories_encoded": 249,
            "mpc_rmse": 0.0,
            "mpc_volatility_reduction_x": 0.0,
            "lyapunov_schur_stable": False,
            "aristocracy_gamma0": 0.0,
            "aristocracy_tilde0": 0.0,
            "aristocracy_gini0": 0.0,
            "constitutional_resilience_ok": False,
            "error": str(exc),
        }

    return {
        "organon_version": __version__,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "episteme_kb": episteme,
        "techne_swm": techne,
        "phronesis_gwi": phronesis,
        "pistis_chain": pistis,
        "cluster": cluster,
    }


def print_organon_status(status: dict[str, Any]) -> None:
    kb = status["episteme_kb"]
    swm = status["techne_swm"]
    gwi = status["phronesis_gwi"]
    pis = status.get("pistis_chain") or collect_pistis_summary()
    cl = status["cluster"]

    mode_badge = "DRY-RUN (Safe Preview)" if swm["dry_run_mode"] else "ARMED (Autonomous Execution)"
    poll_str = f"{swm['last_poll_age_s']}s ago" if swm.get("last_poll_age_s") is not None else "never"
    hips_str = "READY" if kb["hips_ready"] else "MISSING"
    chain_str = "VALID" if kb["provenance_chain_valid"] else "BROKEN"
    const_str = "PASS" if pis.get("constitutional_resilience_ok") else "FAIL"

    print("╔══════════════════════════════════════════════════════════════════════════════════════════╗")
    print("║  THE SOVEREIGN ORGANON (Ὄργανον) — Episteme · Techne · Phronesis · Pistis (On-Chain)     ║")
    print("╚══════════════════════════════════════════════════════════════════════════════════════════╝")
    print(f"  [1] EPISTEME (`kb` v{kb['version']} @ {kb['engine_sha']}, vault={kb['git_sha']})")
    print(
        f"      Corpus     : {kb['documents']} docs | {kb['chunks']} chunks | {kb['vectors']} vectors | {kb['links']} wikilinks"
    )
    print(
        f"      Retrieval  : HIPS Block-Max WAND={hips_str} | Soar Chunks={kb['soar_chunks']} | Outcome Probes={kb['outcome_probes']}"
    )
    print(
        f"      Provenance : {kb['provenance_events']} ledger events (SHA-256 chain={chain_str}, contract=v{kb['bridge_contract_version']})"
    )
    print()
    if swm.get("installed", True):
        print(f"  [2] TECHNE (`swm` v{swm['version']} @ {swm['git_sha']})")
        print(f"      Control    : Mode={mode_badge} | Last Poll={poll_str}")
        print(
            f"      Factory    : {swm['total_runs']} total runs ({swm['completed_runs']} completed, "
            f"{swm['in_progress_runs']} active, {swm['failed_runs']} failed)"
        )
    else:
        print("  [2] TECHNE (`swm` — Open-Core Edition: Goldwood Studio Engine Not Installed)")
        print("      Control    : Standalone Open-Core Mode (`kb` + `gwi` + `chain` + `evr` active locally)")
        print("      Explorer   : https://goldwood.tech/organon")
    print()
    print(f"  [3] PHRONESIS (`gwi` v{gwi['standard_version']})")
    print(
        f"      Portfolio  : {gwi['fresh_pass_targets']}/{gwi['total_targets']} targets FRESH PASS "
        f"({gwi.get('recorded_pass_targets', gwi['fresh_pass_targets'])} recorded PASS) | "
        f"{gwi['triad_audited_targets']}/{gwi['total_targets']} Tri-Council Audited (`Fable 5.1` x `GPT-6 Astra`)"
    )
    if gwi["mean_goldwood_g"] is not None:
        print(
            f"      Scores     : Mean G(S) = {gwi['mean_goldwood_g']:.2f} / 10.00 | Min G(S) = {gwi['min_goldwood_g']:.2f}"
        )
    print()
    print(f"  [4] PISTIS (`organon chain` v{pis.get('protocol_version', PISTIS_PROTOCOL_VERSION)})")
    print(
        f"      Licensing  : Poetics 7-State NC FSM ({pis['iso_territories_encoded']} ISO territories) + PLT + Dual-Escrow RoyaltySplitter"
    )
    print(
        f"      Tokenomics : PhD Lyapunov-MPC RMSE={pis['mpc_rmse']:.4f} ({pis['mpc_volatility_reduction_x']}x damping) "
        f"| Aristocracy Gamma_0={pis['aristocracy_gamma0']:.2f} (P1-P5={const_str})"
    )
    print(
        f"      Anchors    : {pis['merkle_anchors_count']} RFC 6962 Merkle block(s) "
        f"(root={pis.get('latest_merkle_root') or 'unanchored'})"
    )

    if cl.get("peers"):
        print()
        print(f"  [5] CLUSTER TOPOLOGY (Local Host: `{cl['local_node']}`)")
        for peer, pinfo in cl["peers"].items():
            if pinfo.get("reachable"):
                print(f"      • {peer:<15}: ONLINE ({pinfo['latency_ms']} ms) — {pinfo['summary']}")
            else:
                print(f"      • {peer:<15}: OFFLINE ({pinfo.get('error', 'unreachable')})")


def build_showcase_payload() -> dict[str, Any]:
    """Return structured interactive workflow guide for The Sovereign Organon."""
    status = collect_organon_status(all_nodes=False)
    workflows = [
        {
            "id": "WF-01",
            "pillar": "Organon (Unified Telemetry)",
            "title": "1. Situational Awareness & Cluster Pulse",
            "when_to_use": "Start of any work session or before restarting Antigravity / deploying.",
            "commands": [
                "organon status --all-nodes",
                "kb sessions --check-remote",
                "organon verify",
            ],
            "what_happens": (
                "Checks the 6-Gate KB invariant on `me-kb`, inspects `~/.swm/` factory runs and daemon arming, "
                "verifies subproject tree-SHA freshness across the portfolio, and checks local + remote peer sessions."
            ),
        },
        {
            "id": "WF-02",
            "pillar": "Episteme (`kb`)",
            "title": "2. Hybrid Retrieval, Soar Chunking & Self-Evolution (RSI)",
            "when_to_use": "Looking up architecture laws, project context, or distilling episodic logs into permanent rules.",
            "commands": [
                'organon kb search "Subtractive Design Law L7"',
                'organon kb hips search "Goldwood barrier function"',
                "organon kb soar list",
                "organon kb evolve",
            ],
            "what_happens": (
                "Queries SQLite FTS5 + 768-d vectors + Rust `hips` Block-Max WAND (<2ms), retrieves verified procedural "
                "Soar chunks (`kb_soar_match`), and runs counterfactual holdout gating (`kb rsi cycle`) including GWI outcome probes."
            ),
        },
        {
            "id": "WF-03",
            "pillar": "Techne (`swm`)",
            "title": "3. Autonomous Software Factory & OpenProject Dispatch",
            "when_to_use": "Turning an OpenProject work package (`OP#<id>`) into an autonomous 6-stage engineering run.",
            "commands": [
                "organon swm queue",
                "organon task 1762",
                "organon task 1762 --execute",
                "organon swm daemon --arm",
            ],
            "what_happens": (
                "Exports a cognitive context pack from `me-kb` (with verified contract v1.4.0), validates the exact "
                "OpenProject subject via `openproject-query`, runs the 6-stage SWM pipeline (Architect -> Implement -> "
                "Verify -> GWI 9+ -> Tri-Council), and records provenance events."
            ),
        },
        {
            "id": "WF-04",
            "pillar": "Phronesis (`gwi` + `oc triad`)",
            "title": "4. Run-Bound Quality Gate (`G >= 9.0`) & Parallel Tri-Council Review",
            "when_to_use": "Verifying any repository, simulation, paper, or video deliverable before shipping.",
            "commands": [
                "organon audit .",
                'oc triad "Audit the current repository diff for P0/P1 defects"',
                "organon gwi status",
            ],
            "what_happens": (
                "`organon audit` runs `gwi audit --strict --strict-lenses --execute-gate --write-evidence`, binding "
                "`evidence/gate_receipt.json` to the exact Git tree SHA and feeding any recurring blockers into RSI holdout probes. "
                "`oc triad` runs Claude Fable 5.1 + GPT-6 Astra in parallel and auto-records the verdict in `~/.swm/audit_registry.json`."
            ),
        },
        {
            "id": "WF-05",
            "pillar": "Pistis (`organon chain`)",
            "title": "5. On-Chain Bilateral Licensing, Merkle Anchoring & PhD Tokenomics",
            "when_to_use": "Executing Poetics/kall.fyi bilateral contracts, anchoring GWI provenance, or auditing tokenomics & aristocracy.",
            "commands": [
                "organon chain negotiate",
                "organon chain anchor --target .",
                "organon chain tokenomics",
                "organon chain aristocracy",
            ],
            "what_happens": (
                "Runs the Poetics 7-State Negotiation Contract (`NC`) FSM + `PLT` mint + Dual Push/Pull-Escrow `RoyaltySplitter`, "
                "anchors an RFC 6962 Merkle root with P3 provenance trace into `~/.swm/chain_anchors.jsonl`, verifies PhD Lyapunov-MPC "
                "stability + EGT ESS + Folk-Theorem collusion index + Antifragility (`AMI`), and audits Software Aristocracy (`Gamma_0`, P1-P5)."
            ),
        },
        {
            "id": "WF-06",
            "pillar": "Closure & Cross-Workstation Sync",
            "title": "6. Atomic End-of-Task Closure & Cross-Workstation Registry Merge",
            "when_to_use": "Completing an engineering task and synchronizing state across workstations.",
            "commands": [
                "organon sync-state",
                'organon finish . -m "docs(episodic): complete feature and GWI 9.5+ audit"',
            ],
            "what_happens": (
                "Runs `readme_check.py`, executes the run-bound GWI gate, runs the 6-Gate `kb verify` doctor, "
                "anchors the RFC 6962 Merkle attestation block, non-destructively merges `~/.swm/audit_registry.json` "
                "with the remote peer, and performs an atomic `kb push`."
            ),
        },
    ]
    return {"status": status, "workflows": workflows}


def print_organon_showcase(payload: dict[str, Any]) -> None:
    print_organon_status(payload["status"])
    print("\n╔══════════════════════════════════════════════════════════════════════════════════════════╗")
    print("║  HOW YOU INTERACT WITH THE ORGANON — 6 CANONICAL WORKFLOWS                              ║")
    print("╚══════════════════════════════════════════════════════════════════════════════════════════╝")
    for wf in payload["workflows"]:
        print(f"\n  ■ {wf['title']}  [{wf['pillar']}]")
        print(f"    When : {wf['when_to_use']}")
        print("    Run  :")
        for c in wf["commands"]:
            print(f"      $ {c}")
        print(f"    Flow : {wf['what_happens']}")


def sync_organon_state_remote(peer: str | None = None, quiet: bool = False) -> dict[str, Any]:
    """
    Non-destructively merge `~/.swm/audit_registry.json` with the configured remote workstation peer.
    Preserves node-local `daemon.json`, `soar_chunks.db`, and divergent provenance ledgers, and fails closed
    (`synced=False`) if any SSH read/write transfer fails.
    """
    resolved_peer = (peer or _get_cluster_env("KB_REMOTE_PEER") or "").strip()
    local_node = _resolve_local_node_name()
    local_reg_path = AUDIT_REGISTRY_PATH
    result: dict[str, Any] = {
        "local_node": local_node,
        "peer": resolved_peer or "unconfigured",
        "synced": False,
        "merged_projects": 0,
    }

    if not resolved_peer:
        result["error"] = (
            "No remote peer configured (set KB_REMOTE_PEER in ~/.config/agents/secrets.env or pass --peer)"
        )
        if not quiet:
            print(f"[organon sync-state] Error: {result['error']}", file=sys.stderr)
        return result

    if resolved_peer in (local_node, "local", "localhost"):
        local_reg = load_audit_registry()
        result["synced"] = True
        result["self_peer"] = True
        result["merged_projects"] = len(local_reg.get("projects") or {})
        return result

    try:
        probe = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", resolved_peer, "mkdir -p ~/.swm"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if probe.returncode != 0:
            result["error"] = (probe.stderr or "peer unreachable").strip()
            if not quiet:
                print(f"[organon sync-state] Error: {result['error']}", file=sys.stderr)
            return result

        # Fetch remote audit_registry.json if present (exit 0 even if file doesn't exist yet, via test -f)
        r_reg_proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=3",
                resolved_peer,
                "if [ -f ~/.swm/audit_registry.json ]; then cat ~/.swm/audit_registry.json; else echo '{}'; fi",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
        if r_reg_proc.returncode != 0:
            result["error"] = (r_reg_proc.stderr or "failed to read remote audit_registry.json").strip()
            if not quiet:
                print(f"[organon sync-state] Error: {result['error']}", file=sys.stderr)
            return result

        local_reg = load_audit_registry()
        remote_raw = (r_reg_proc.stdout or "").strip()
        try:
            remote_reg = json.loads(remote_raw)
        except Exception as exc:
            result["error"] = f"invalid JSON from remote audit_registry.json: {exc}"
            if not quiet:
                print(f"[organon sync-state] Error: {result['error']}", file=sys.stderr)
            return result
        if not isinstance(remote_reg, dict) or (
            "projects" in remote_reg and not isinstance(remote_reg.get("projects"), dict)
        ):
            result["error"] = "remote audit_registry.json payload has invalid top-level structure"
            if not quiet:
                print(f"[organon sync-state] Error: {result['error']}", file=sys.stderr)
            return result
        for p_k, p_v in (remote_reg.get("projects") or {}).items():
            if not isinstance(p_v, dict) or (
                "subprojects" in p_v
                and (
                    not isinstance(p_v.get("subprojects"), dict)
                    or any(not isinstance(sp_v, dict) for sp_v in p_v["subprojects"].values())
                )
            ):
                result["error"] = f"remote audit_registry.json project '{p_k}' has malformed subprojects structure"
                if not quiet:
                    print(f"[organon sync-state] Error: {result['error']}", file=sys.stderr)
                return result
        merged_reg = merge_audit_registries(local_reg, remote_reg)

        local_reg_path.parent.mkdir(parents=True, exist_ok=True)
        merged_json = json.dumps(merged_reg, indent=2) + "\n"
        local_reg_path.write_text(merged_json, encoding="utf-8")
        result["merged_projects"] = len(merged_reg.get("projects") or {})

        # Push merged audit_registry.json back to peer atomically
        write_proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=4",
                resolved_peer,
                "cat > ~/.swm/audit_registry.json.tmp && mv ~/.swm/audit_registry.json.tmp ~/.swm/audit_registry.json",
            ],
            input=merged_json,
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
        if write_proc.returncode != 0:
            result["error"] = (write_proc.stderr or "failed to write remote audit_registry.json").strip()
            if not quiet:
                print(f"[organon sync-state] Error: {result['error']}", file=sys.stderr)
            return result

        result["synced"] = True
        if not quiet:
            print(
                f"=== Organon Cross-Node Registry Sync (`{local_node}` <-> `{resolved_peer}`) ===\n"
                f"  Audit Registry : {result['merged_projects']} project(s) merged & synchronized ({local_reg_path})"
            )
    except Exception as exc:
        result["error"] = str(exc)
        if not quiet:
            print(f"[organon sync-state] Error: could not sync with {resolved_peer}: {exc}", file=sys.stderr)
    return result


def main_cli(argv: list[str] | None = None) -> None:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        status = collect_organon_status(all_nodes=False)
        print_organon_status(status)
        return

    cmd = args_list[0]
    rest = args_list[1:]

    # Direct pillar pass-throughs
    if cmd in ("kb", "skb", "episteme", "memory"):
        active_vault, vault_source = resolve_organon_vault_state()
        has_explicit_vault_flag = any(arg == "--vault" or arg.startswith("--vault=") for arg in rest)
        has_help_flag = any(arg in ("-h", "--help", "--help-all") for arg in rest)
        default_mekb = (Path.home() / "GitLab" / "me-kb").resolve()
        kb_bin = shutil.which("kb") or str(Path.home() / ".local" / "bin" / "kb")
        if (
            argv is None
            and not has_explicit_vault_flag
            and vault_source in ("config", "studio_default")
            and active_vault == default_mekb
            and default_mekb.is_dir()
            and Path(kb_bin).is_file()
            and os.access(kb_bin, os.X_OK)
        ):
            os.execv(kb_bin, [kb_bin, *rest])
        from .cli import main as skb_main

        forwarded = list(rest)
        needs_vault = (
            not has_explicit_vault_flag
            and not has_help_flag
            and (not forwarded or forwarded[0] not in _VAULT_FREE_SKB_CMDS)
        )
        if needs_vault:
            if vault_source == "forbidden_starter":
                print(_vault_error_message("organon", active_vault, vault_source), file=sys.stderr)
                sys.exit(1)
            if vault_source == "unconfigured":
                print(
                    "[organon] Error: No initialized Sovereign-KB vault found. "
                    "Run 'skb init ~/notes --save-default' or pass '--vault <dir>'.",
                    file=sys.stderr,
                )
                sys.exit(1)
            forwarded = ["--vault", str(active_vault), *forwarded]
        skb_main(forwarded)
        return
    if cmd in ("swm", "techne", "machine"):
        swm_bin = resolve_swm_bin()
        if swm_bin is None:
            rc = _print_swm_open_core_notice(rest)
            if rc != 0:
                sys.exit(rc)
            return
        if argv is None:
            os.execv(str(swm_bin), [str(swm_bin), *rest])
        rc_swm = subprocess.run([str(swm_bin), *rest], check=False).returncode
        if rc_swm != 0:
            sys.exit(rc_swm)
        return
    if cmd in ("gwi", "phronesis", "measure"):
        from .gwi import main_cli as gwi_main

        gwi_main(rest)
        return

    parser = argparse.ArgumentParser(
        prog="organon",
        description="The Sovereign Organon (Ὄργανον) — Unified CLI for Episteme (kb), Techne (swm), and Phronesis (gwi)",
    )
    sub = parser.add_subparsers(dest="subcmd")

    p_status = sub.add_parser("status", help="Show unified 3-pillar status across kb, swm, gwi, and cluster nodes")
    p_status.add_argument("--all-nodes", action="store_true", help="Probe configured remote cluster peers")
    p_status.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    p_show = sub.add_parser("showcase", help="Display interactive showcase and 5 canonical Organon workflows")
    p_show.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    p_ver = sub.add_parser("verify", help="Run 6-Gate KB verification + GWI registry status + SWM status")
    p_ver.add_argument("--heal", action="store_true", help="Heal KB index and sessions before verifying")

    p_aud = sub.add_parser(
        "audit",
        help="Run GWI 9+ gate with run-bound behavioral execution (--strict --strict-lenses --execute-gate)",
    )
    p_aud.add_argument("target", nargs="?", default=".", help="Target repository or artifact path (default: .)")
    p_aud.add_argument("--profile", default="auto", help="Evaluation profile (auto, app, sim, video, paper, kb)")
    p_aud.add_argument("--no-execute-gate", action="store_true", help="Skip live quality_gate.sh execution")
    p_aud.add_argument(
        "--triad", action="store_true", help="Also run parallel `oc triad` review (`Fable 5.1` x `GPT-6 Astra`)"
    )
    p_aud.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    p_task = sub.add_parser("task", help="Assemble KB context pack, verify OpenProject ground truth, and run SWM")
    p_task.add_argument("wp_id", type=int, help="OpenProject Work Package ID (e.g. 1762)")
    p_task.add_argument("--execute", action="store_true", help="Execute live SWM pipeline (default: preview/dry-run)")

    p_sync = sub.add_parser(
        "sync-state", help="Non-destructively merge ~/.swm/audit_registry.json with remote workstation"
    )
    p_sync.add_argument("--peer", default="", help="Remote workstation SSH host (defaults to KB_REMOTE_PEER)")
    p_sync.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    p_fin = sub.add_parser(
        "finish",
        help="Run end-of-task verification (`readme_check` + `gwi --execute-gate` + `kb verify` + Merkle anchor + cross-node sync + optional `kb push`)",
    )
    p_fin.add_argument("target", nargs="?", default=".", help="Target repository path (default: .)")
    p_fin.add_argument("-m", "--message", help="Episodic commit message for atomic `kb push -m ...`")
    p_fin.add_argument("--skip-push", action="store_true", help="Do not run `kb push` even if -m is provided")

    register_chain_subparser(sub, show_all_aliases=True)
    register_evr_subparser(sub, show_all_aliases=True)

    parsed = parser.parse_args(args_list)

    if parsed.subcmd in ("chain", "pistis", "blockchain"):
        cmd_chain(parsed)
        return

    if parsed.subcmd in ("evr", "everything"):
        cmd_evr(parsed)
        return

    if parsed.subcmd == "status":
        st = collect_organon_status(all_nodes=bool(parsed.all_nodes))
        if parsed.json:
            print(json.dumps(st, indent=2))
        else:
            print_organon_status(st)
        return

    if parsed.subcmd == "showcase":
        payload = build_showcase_payload()
        if parsed.json:
            print(json.dumps(payload, indent=2))
        else:
            print_organon_showcase(payload)
        return

    if parsed.subcmd == "verify":
        me_kb, vault_source = resolve_organon_vault_state()
        rc1 = 0
        if vault_source == "unconfigured":
            print(
                "  [skip] No initialized Sovereign-KB vault configured (run `skb init ~/notes --save-default` to enable)."
            )
        elif not _is_initialized_vault(me_kb):
            print(_vault_error_message("organon verify", me_kb, vault_source), file=sys.stderr)
            sys.exit(1)
        else:
            v_cmd = [sys.executable, "-m", "sovereign_kb.cli", "--vault", str(me_kb), "verify"]
            if parsed.heal:
                v_cmd.append("--heal")
            rc1 = subprocess.run(v_cmd, check=False).returncode
        rc2 = subprocess.run([sys.executable, "-m", "sovereign_kb.cli", "gwi", "status"], check=False).returncode
        swm_bin = resolve_swm_bin()
        rc3 = subprocess.run([str(swm_bin), "status"], check=False).returncode if swm_bin is not None else 0
        if rc1 != 0 or rc2 != 0 or rc3 != 0:
            sys.exit(1)
        return

    if parsed.subcmd == "audit":
        from .gwi import main_cli as gwi_main

        gwi_args = ["audit", parsed.target, "--strict", "--strict-lenses", "--write-evidence"]
        if parsed.profile and parsed.profile != "auto":
            gwi_args.extend(["--profile", parsed.profile])
        if not parsed.no_execute_gate:
            gwi_args.append("--execute-gate")
        if parsed.json:
            gwi_args.append("--json")
        gwi_main(gwi_args)
        if parsed.triad:
            oc_bin = str(Path.home() / ".local" / "bin" / "oc")
            subprocess.run(
                [
                    oc_bin,
                    "triad",
                    "Audit this repository against GWI v1.4.0 Laws L0-L9 and report APPROVED or NOT_APPROVED.",
                ],
                cwd=str(Path(parsed.target).expanduser().resolve()),
                check=False,
            )
        return

    if parsed.subcmd == "task":
        me_kb, vault_source = resolve_organon_vault_state()
        if vault_source != "unconfigured" and not _is_initialized_vault(me_kb):
            print(_vault_error_message("organon task", me_kb, vault_source), file=sys.stderr)
            sys.exit(1)
        op_bin = _resolve_openproject_bin()
        subject = f"OP#{parsed.wp_id}"
        if op_bin is not None:
            op_res = subprocess.run([str(op_bin), str(parsed.wp_id)], capture_output=True, text=True, check=False)
            if op_res.returncode == 0 and op_res.stdout.strip():
                first_line = op_res.stdout.strip().splitlines()[0]
                subject = first_line
                print(f"=== OpenProject Ground Truth (`openproject-query {parsed.wp_id}`) ===")
                for ln in op_res.stdout.strip().splitlines()[:6]:
                    print(f"  {ln}")
        if vault_source == "unconfigured":
            pack: dict[str, Any] = {
                "contract_version": BRIDGE_CONTRACT_VERSION,
                "declarative_chunks": [],
                "declarative_hits": [],
                "procedural_chunks": [],
            }
        else:
            pack = retrieve_vault_precedents(me_kb, query=subject, top_k=5)
        decl_hits = pack.get("declarative_chunks") or pack.get("declarative_hits") or []
        print(
            f"\n=== Episteme Context Pack (contract=v{pack.get('contract_version', BRIDGE_CONTRACT_VERSION)}, "
            f"hits={len(decl_hits)}, soar_chunks={len(pack.get('procedural_chunks') or [])}) ==="
        )
        if vault_source == "unconfigured":
            print(
                "  [note] No initialized vault configured; run `skb init ~/notes --save-default` to index precedents."
            )
        for idx, ch in enumerate(decl_hits[:3], 1):
            print(f"  {idx}. [{ch.get('rel_path')}] {ch.get('heading', '')}")
        swm_bin = resolve_swm_bin()
        if swm_bin is None:
            print("\n=== Techne Dispatch (`swm` — Open-Core Mode) ===")
            if parsed.execute:
                sys.exit(_print_swm_open_core_notice(["run", str(parsed.wp_id)]))
            print(
                "  Context pack assembled locally. Live 6-stage autonomous `swm` execution requires "
                "the Goldwood Studio Engine (`https://goldwood.tech/organon`)."
            )
            return
        # `swm run <wp_id>` executes live by default; pass `--dry-run` unless `--execute` is requested
        swm_cmd = [str(swm_bin), "run", str(parsed.wp_id)]
        if not parsed.execute:
            swm_cmd.append("--dry-run")
        print(f"\n=== Dispatching Techne (`{' '.join(swm_cmd)}`) ===")
        sys.exit(subprocess.run(swm_cmd, check=False).returncode)

    if parsed.subcmd == "sync-state":
        res = sync_organon_state_remote(peer=parsed.peer or None, quiet=bool(parsed.json))
        if parsed.json:
            print(json.dumps(res, indent=2))
        if not res.get("synced", False):
            sys.exit(1)
        return

    if parsed.subcmd == "finish":
        active_vault, vault_source = resolve_organon_vault_state()
        if vault_source != "unconfigured" and not _is_initialized_vault(active_vault):
            print(_vault_error_message("organon finish", active_vault, vault_source), file=sys.stderr)
            sys.exit(1)
        if parsed.message and not parsed.skip_push and vault_source == "unconfigured":
            print(
                "[organon finish] Error: Cannot push vault commit (-m) because no initialized vault is configured.",
                file=sys.stderr,
            )
            sys.exit(1)
        target_dir = Path(parsed.target).expanduser().resolve()
        readme_chk = Path(__file__).resolve().parent / "readme_check.py"
        if readme_chk.is_file() and (target_dir / "README.md").is_file():
            print(f"=== [1/5] Running README Zero-Hallucination Gate on `{target_dir.name}` ===")
            rc_rm = subprocess.run([sys.executable, str(readme_chk), str(target_dir)], check=False).returncode
            if rc_rm != 0:
                sys.exit(rc_rm)

        print(f"\n=== [2/5] Running Run-Bound GWI 9+ Gate on `{target_dir.name}` ===")
        from .gwi import main_cli as gwi_main

        gwi_main(["audit", str(target_dir), "--strict", "--strict-lenses", "--execute-gate", "--write-evidence"])

        print("\n=== [3/5] Running 6-Gate Sovereign KB Doctor (`skb verify`) ===")
        active_vault, vault_source = resolve_organon_vault_state()
        if vault_source == "unconfigured":
            print("  [skip] No initialized user vault configured (run `skb init ~/notes --save-default` to enable).")
        elif not _is_initialized_vault(active_vault):
            print(_vault_error_message("organon finish", active_vault, vault_source), file=sys.stderr)
            sys.exit(1)
        else:
            rc_kb = subprocess.run(
                [sys.executable, "-m", "sovereign_kb.cli", "--vault", str(active_vault), "verify"],
                check=False,
            ).returncode
            if rc_kb != 0:
                sys.exit(rc_kb)

        print("\n=== [4/5] Anchoring RFC 6962 Merkle Attestation (`organon chain anchor`) ===")
        try:
            anchor_rec = anchor_organon_attestation(target_dir=target_dir)
        except (ValueError, OSError) as exc:
            print(f"[organon finish] ERROR anchoring RFC 6962 attestation: {exc}", file=sys.stderr)
            sys.exit(1)
        print(
            f"  Anchored {anchor_rec['anchor_id']}: root={anchor_rec['rfc6962_merkle_root'][:22]}... "
            f"(leaves={anchor_rec['merkle_leaves_count']}, proof_verified={anchor_rec['inclusion_proof_verified']})"
        )

        print("\n=== [5/5] Synchronizing Organon Registry with Remote Peer ===")
        configured_peer = (_get_cluster_env("KB_REMOTE_PEER") or "").strip()
        if configured_peer:
            sync_res = sync_organon_state_remote(peer=configured_peer, quiet=False)
            if not sync_res.get("synced", False):
                sys.exit(1)
        else:
            print("  Single-node mode (no KB_REMOTE_PEER configured) — local audit registry updated.")

        if parsed.message and not parsed.skip_push:
            if vault_source == "unconfigured":
                print(
                    "[organon finish] Error: Cannot push vault commit (-m) because no initialized vault is configured.",
                    file=sys.stderr,
                )
                sys.exit(1)
            kb_bin = shutil.which("kb") or str(Path.home() / ".local" / "bin" / "kb")
            if active_vault == (Path.home() / "GitLab" / "me-kb").resolve() and Path(kb_bin).is_file():
                sys.exit(subprocess.run([kb_bin, "push", "-m", parsed.message], check=False).returncode)
            sys.exit(
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "sovereign_kb.cli",
                        "--vault",
                        str(active_vault),
                        "push",
                        "-m",
                        parsed.message,
                    ],
                    check=False,
                ).returncode
            )
        return


if __name__ == "__main__":
    main_cli()
