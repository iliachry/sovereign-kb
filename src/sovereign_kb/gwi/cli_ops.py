"""CLI subparser registration and `gwi` command dispatcher (`register_gwi_subparser`, `cmd_gwi`, `main_cli`)."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import (
    ARCHETYPE_WEIGHTS,
    DEFAULT_K,
    DEFAULT_THETA,
    DEFAULT_THRESHOLD_G,
    GWI_STANDARD_VERSION,
    compute_goldwood_index,
)

from .lenses_catalog import CRAFT_LENSES
from .lenses_laws import audit_directory_regularity
from .lenses_probes import detect_active_lenses
from .reporting import (
    build_multi_model_prompt,
    discover_default_portfolio_dirs,
    evaluate_target,
    format_audit_report,
    triad_review_state,
    write_goldwood_evidence,
)


def register_gwi_subparser(subparsers: argparse._SubParsersAction, show_all_aliases: bool = False) -> None:
    """Register `skb gwi` (and aliases `goldwood`, `audit`) onto `sovereign-kb`'s CLI parser."""
    for alias in ("gwi", "goldwood", "audit"):
        g_help = (
            "Unified Goldwood Index (G >= 9.0) & GWI+ v1.4.0 quality gate (aliases: audit, goldwood)"
            if show_all_aliases
            else None
        )
        p = subparsers.add_parser(alias, help=g_help) if g_help else subparsers.add_parser(alias)
        _configure_gwi_parser(p)


def _configure_gwi_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "gwi_subcmd",
        nargs="?",
        default="audit",
        help="Subcommand (`audit`, `status`, `subprojects`, `record-triad`, `calc`, `lenses`, `portfolio`, `prompt`) or path to target",
    )
    parser.add_argument(
        "gwi_args",
        nargs="*",
        help="Target path(s) or 4 tier scores for `calc`",
    )
    parser.add_argument(
        "--profile",
        choices=("auto", "app", "sim", "video", "paper", "kb"),
        default="auto",
        help="Evaluation profile (default: auto)",
    )
    parser.add_argument(
        "--weights",
        help="Comma-separated 4-tier weights alpha_1,alpha_2,alpha_3,alpha_4 or archetype name (balanced, app, sim, video, paper, kb, craftsperson, scholar, leader)",
    )
    parser.add_argument(
        "--theta", type=float, default=DEFAULT_THETA, help="Barrier survival threshold theta (default: 7.0)"
    )
    parser.add_argument("--k", type=float, default=DEFAULT_K, help="Barrier steepness k (default: 2.5)")
    parser.add_argument(
        "--write-evidence", action="store_true", help="Write `evidence/goldwood.json` in target directory"
    )
    parser.add_argument(
        "--seal",
        nargs="?",
        const="__DEFAULT__",
        default="",
        help="Seal the audit report + visual/media evidence into a tamper-evident `.evr` capsule (`evidence/goldwood.evr`) and anchor into Pistis Chain",
    )
    parser.add_argument(
        "--require-elenchus",
        action="store_true",
        help="Require a fresh, cross-lineage adjudicated Elenchus challenge receipt before sealing `.evr`",
    )
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 if G(S) < 9.0 or any blocker is P0")
    parser.add_argument(
        "--strict-lenses",
        action="store_true",
        help="Cap Verga at <= 7.80 when required Craft Lens contact sheets/artifacts are unmeasured and exit 1 if coupled G < 9.0",
    )
    parser.add_argument("--probe-audio", action="store_true", help="Run live ffmpeg EBU R128 loudness scan on video")
    parser.add_argument(
        "--execute-gate",
        action="store_true",
        help="Execute scripts/quality_gate.sh (or language test runner) and bind evidence/gate_receipt.json to the current tree SHA",
    )
    parser.add_argument(
        "--subprojects",
        action="store_true",
        help="Auto-discover and audit all subprojects (root, papers, web/apps, videos) inside target repository",
    )
    parser.add_argument(
        "--incremental",
        "--fast",
        dest="incremental",
        action="store_true",
        help="Skip subprojects whose scoped Git tree SHA and `gwi_version` are already FRESH and PASS",
    )
    parser.add_argument(
        "--disposition",
        default="APPROVED",
        help="Tri-Council disposition (`APPROVED` or `NOT_APPROVED`) when running `record-triad`",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Optional summary notes when recording a Tri-Council (`record-triad`) audit",
    )
    parser.add_argument(
        "--agent", choices=("astra", "fable", "gx10", "triad"), default="astra", help="Target auditor for `gwi prompt`"
    )
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")


def _require_existing_target(target: Path) -> None:
    """Refuse to audit a phantom target: a missing path would otherwise score as an empty tree (G=0.32 FAIL)."""
    if target.exists():
        return
    print(
        f"error: GWI target does not exist: {target}\n"
        "       (hint: pass a directory, `.tex`, or `.mp4` path; `gwi audit <dir>` — the `gwi` alias is not a target)",
        file=sys.stderr,
    )
    sys.exit(2)


def cmd_gwi(args: argparse.Namespace) -> None:
    """Execute `skb gwi` / standalone `gwi` commands."""
    from ..audit_registry import (
        AUDIT_REGISTRY_PATH,
        discover_subprojects,
        evaluate_subproject_freshness,
        find_git_root,
        get_sovereign_versions,
        load_audit_registry,
        record_gwi_audit,
        record_triad_audit,
    )

    subcmd = getattr(args, "gwi_subcmd", "audit") or "audit"
    rest = list(getattr(args, "gwi_args", []) or [])
    # Tolerate redundant alias tokens (`gwi gwi audit <dir>`, `python -m sovereign_kb.gwi gwi audit <dir>`)
    # so the alias is never mistaken for a target path.
    while subcmd in ("gwi", "goldwood") and rest:
        subcmd = rest.pop(0)
    theta = float(getattr(args, "theta", DEFAULT_THETA))
    k = float(getattr(args, "k", DEFAULT_K))

    valid_subcmds = (
        "audit",
        "calc",
        "portfolio",
        "prompt",
        "lenses",
        "regularity",
        "kanon",
        "l7",
        "status",
        "registry",
        "history",
        "subprojects",
        "record-triad",
    )
    if subcmd not in valid_subcmds:
        rest = [subcmd, *rest]
        subcmd = "audit"

    if subcmd in ("audit", "status", "history", "subprojects", "record-triad") and rest:
        _require_existing_target(Path(rest[0]).expanduser())

    if subcmd == "record-triad":
        target_p = Path(rest[0]).expanduser().resolve() if rest else Path.cwd().resolve()
        subpath = rest[1] if len(rest) > 1 else "."
        disp = str(getattr(args, "disposition", "APPROVED")).upper()
        notes = str(getattr(args, "notes", ""))
        triad_rec = record_triad_audit(
            target_p,
            subpath=subpath,
            disposition=disp,
            notes=notes,
        )
        if getattr(args, "json", False):
            print(json.dumps(triad_rec, indent=2))
        else:
            print(
                f"=== Recorded Sovereign Tri-Council Audit (`Fable 5.1` x `GPT-6 Astra`) ===\n"
                f"  Target      : {find_git_root(target_p).name}:{subpath}\n"
                f"  Commit/Tree : {triad_rec['commit_sha']} (tree={triad_rec['tree_sha']})\n"
                f"  Disposition : {triad_rec['disposition']}\n"
                f"  Models      : {triad_rec['fable_model']} + {triad_rec['astra_model']}\n"
                f"  Versions    : kb={triad_rec['versions']['kb_version']} "
                f"gwi={triad_rec['versions']['gwi_version']} "
                f"swm={triad_rec['versions']['swm_version']}\n"
                f"  Registry    : {AUDIT_REGISTRY_PATH}"
            )
        return

    if subcmd == "subprojects":
        target_p = Path(rest[0]).expanduser().resolve() if rest else Path.cwd().resolve()
        subs = discover_subprojects(target_p)
        rows = [evaluate_subproject_freshness(target_p, d["subpath"]) for d in subs]
        if getattr(args, "json", False):
            print(json.dumps({"repo_root": str(find_git_root(target_p)), "subprojects": rows}, indent=2))
            return
        print(f"=== Auto-Discovered Subprojects for `{find_git_root(target_p).name}` ===")
        print(
            f"  {'Subproject':<34} {'Profile':<7} {'Commit':<9} {'TreeSHA':<17} {'Freshness':<14} {'G(S)':>6}  {'Triad (Astra x Fable)'}"
        )
        print("  " + "-" * 112)
        for r in rows:
            g_str = f"{r['goldwood_g']:6.2f}" if isinstance(r.get("goldwood_g"), (int, float)) else "   N/A"
            triad_str = (
                f"{r['triad_disposition']} @ {r['triad_commit_sha']} ({(r['triad_audited_at'] or '')[:16]})"
                if r["triad_ever_audited"]
                else "NEVER AUDITED"
            )
            print(
                f"  {r['target_label']:<34} {r['profile'].upper():<7} {r['current_commit']:<9} "
                f"{r['current_tree']:<17} {r['freshness']:<14} {g_str}  {triad_str}"
            )
        return

    if subcmd in ("status", "registry", "history"):
        cur_ver = get_sovereign_versions()
        reg = load_audit_registry()
        if rest:
            roots = [find_git_root(Path(p)) for p in rest if Path(p).expanduser().exists()]
        else:
            seen_roots: dict[str, Path] = {}
            for v in reg.get("projects", {}).values():
                if isinstance(v, dict) and v.get("repo_root"):
                    rp = Path(v["repo_root"])
                    if rp.is_dir():
                        gr = find_git_root(rp)
                        seen_roots[str(gr)] = gr
            for d in discover_default_portfolio_dirs():
                if d.is_dir():
                    gr = find_git_root(d)
                    seen_roots.setdefault(str(gr), gr)
            for extra in (Path.home() / "GitHub" / "aristocracy", Path.home() / "GitHub" / "Flood3D_Unity"):
                if extra.is_dir():
                    gr = find_git_root(extra)
                    seen_roots.setdefault(str(gr), gr)
            roots = sorted(seen_roots.values(), key=lambda x: x.name.lower())

        all_rows: list[dict[str, Any]] = []
        for r_root in roots:
            subs = discover_subprojects(r_root)
            for sub_info in subs:
                all_rows.append(evaluate_subproject_freshness(r_root, sub_info["subpath"]))

        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "registry_path": str(AUDIT_REGISTRY_PATH),
                        "current_versions": cur_ver,
                        "targets": all_rows,
                    },
                    indent=2,
                )
            )
            return

        print(
            f"=== Sovereign Audit Provenance & Subproject Cache Registry ===\n"
            f"  Registry File    : {AUDIT_REGISTRY_PATH}\n"
            f"  Current Versions : kb={cur_ver['kb_version']} ({cur_ver['kb_sha']}) | "
            f"gwi={cur_ver['gwi_version']} | swm={cur_ver['swm_version']} ({cur_ver['swm_sha']}) | "
            f"me-kb={cur_ver['me_kb_sha']}\n"
        )
        print(
            f"  {'Project / Subproject':<34} {'Prof':<6} {'Last Audit':<17} {'Commit':<8} {'State':<13} "
            f"{'G(S)':>6}  {'Triad (Fable x Astra)':<24} {'Versions (kb/gwi/swm)'}"
        )
        print("  " + "-" * 136)
        for row in all_rows:
            aud_ts = (row.get("audited_at") or "never")[:16].replace("T", " ")
            g_str = f"{row['goldwood_g']:6.2f}" if isinstance(row.get("goldwood_g"), (int, float)) else "   N/A"
            if row["triad_ever_audited"]:
                triad_s = f"{row['triad_disposition']}@{row.get('triad_commit_sha') or '?'}"
            else:
                triad_s = "NEVER AUDITED"
            v = row.get("versions") or {}
            v_str = (
                f"{v.get('kb_version', '-')}/{v.get('gwi_version', '-')}/{v.get('swm_version', '-')}" if v else "-/-/-"
            )
            print(
                f"  {row['target_label']:<34} {row['profile'].upper():<6} {aud_ts:<17} "
                f"{row['current_commit']:<8} {row['freshness']:<13} {g_str}  {triad_s:<24} {v_str}"
            )
        fresh_c = sum(1 for r in all_rows if r["freshness"] == "FRESH")
        triad_c = sum(1 for r in all_rows if r["triad_ever_audited"])
        print(
            f"\n  Summary: {len(all_rows)} auditable target(s) across {len(roots)} repo(s) | "
            f"{fresh_c}/{len(all_rows)} FRESH | {triad_c}/{len(all_rows)} Tri-Council Audited (`Fable 5.1` x `GPT-6 Astra`)."
        )
        return

    if subcmd in ("regularity", "kanon", "l7"):
        targets = (
            [Path(p).expanduser().resolve() for p in rest]
            if rest
            else [p for p in discover_default_portfolio_dirs() if p.name != "sovereign-kb"]
        )
        results = [audit_directory_regularity(t) for t in targets if t.exists()]
        passed_all = all(r["passed"] for r in results)
        payload = {
            "standard": f"GWI Law L7 — Simplicity Favors Regularity (v{GWI_STANDARD_VERSION})",
            "total_projects": len(results),
            "passed_kanon": sum(1 for r in results if r["passed"]),
            "all_passed": passed_all,
            "passed": passed_all,
            "projects": results,
            "repositories": results,
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(
                f"=== GWI Law L7 — Simplicity Favors Regularity (Η Απλότητα Ευνοεί την Κανονικότητα, v{GWI_STANDARD_VERSION}) ==="
            )
            print(
                f"  {'Repository':<26} {'Files':>5} {'FontSizes':>11} {'Radii':>9} {'Grids':>9} {'R_kanon':>9}  {'Status'}"
            )
            print("  " + "-" * 84)
            for r in results:
                fs_s = f"{r['distinct_font_sizes']}/8"
                br_s = f"{r['distinct_border_radii']}/4"
                gt_s = f"{r['distinct_grid_templates']}/5"
                print(
                    f"  {r['project_name']:<26} {r['files_scanned']:5d} {fs_s:>11} {br_s:>9} {gt_s:>9} {r['r_kanon']:9.4f}  {r['status']}"
                )
            print(
                f"\n  Law L7 Summary: {payload['passed_kanon']} / {payload['total_projects']} repository(s) achieve R_kanon = 1.0000."
            )
        if getattr(args, "strict", False) and not passed_all:
            sys.exit(1)
        return

    if subcmd == "lenses":
        if rest:
            target_p = Path(rest[0]).expanduser().resolve()
            active = detect_active_lenses(target_p, profile=getattr(args, "profile", "auto"))
            if getattr(args, "json", False):
                print(json.dumps({"target": str(target_p), "active_lenses": active}, indent=2))
                return
            print(f"=== Active Specialist Craft Lenses for `{target_p.name}` ({target_p}) ===")
            for spec in active:
                print(f"\n  • [{spec['id']}] {spec['title']}  ({', '.join(spec['activation_reasons'])})")
                print(f"    Contact Sheet : {spec['contact_sheet']}")
                for pr in spec["probes"]:
                    print(f"    - Probe `{pr['id']}`: {pr['metric']} | Target: {pr['target']} | Veto: {pr['veto']}")
            return

        if getattr(args, "json", False):
            print(json.dumps(CRAFT_LENSES, indent=2))
            return
        print(f"=== Sovereign Multi-Disciplinary Craft Lenses (Chrysos x Verga Standard v{GWI_STANDARD_VERSION}) ===")
        for lid, spec in CRAFT_LENSES.items():
            print(f"\n  • [{lid}] {spec['title']}")
            print(f"    Vault Spec    : {spec['vault_note']}")
            print(f"    Profiles      : {', '.join(spec['applies_to_profiles'])}")
            print(f"    Contact Sheet : {spec['contact_sheet']}")
            for pr in spec["probes"]:
                print(f"    - Probe `{pr['id']:<24}` : Target {pr['target']:<26} | Veto: {pr['veto']}")
        return

    if subcmd == "calc":
        if len(rest) < 4:
            raise SystemExit("Usage: gwi calc <S_sub> <S_perc> <S_act> <S_exist> [--weights w1,w2,w3,w4]")
        scores = [float(x) for x in rest[:4]]
        w_arg = getattr(args, "weights", None)
        weights: list[float] | tuple[float, ...] | None = None
        if w_arg:
            if w_arg in ARCHETYPE_WEIGHTS:
                weights = ARCHETYPE_WEIGHTS[w_arg]
            else:
                weights = [float(x.strip()) for x in w_arg.split(",") if x.strip()]
        res = compute_goldwood_index(scores, weights=weights, theta=theta, k=k)
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2))
        else:
            print("=== Universal Goldwood Index Calculator G(S) ===")
            print(f"  Tier Scores S     : {res['tier_scores']}")
            print(f"  Weights alpha     : {res['weights']}")
            print(f"  Barrier Psi(S_i)  : {res['psi_values']}  (theta={res['theta']}, k={res['k']})")
            print(f"  Barrier Product   : {res['barrier_product']:.4f}")
            print(f"  Linear Average    : {res['linear_score']:.2f} / 10.00")
            print(f"  Goldwood Index G  : {res['goldwood_g']:.2f} / 10.00  -->  {res['verdict']}")
        if getattr(args, "strict", False) and res["status"] != "PASS":
            sys.exit(1)
        return

    if subcmd == "portfolio":
        strict_lenses = bool(getattr(args, "strict_lenses", False))
        targets = [Path(p).expanduser().resolve() for p in rest] if rest else discover_default_portfolio_dirs()
        reports = []
        for t in targets:
            if not t.exists():
                continue
            rep = getattr(sys.modules.get("sovereign_kb.gwi"), "evaluate_target", evaluate_target)(
                t,
                profile=getattr(args, "profile", "auto"),
                probe_audio=getattr(args, "probe_audio", False),
                theta=theta,
                k=k,
                strict_lenses=strict_lenses,
            )
            record_gwi_audit(t, rep)
            reports.append(rep)
        summary: dict[str, Any] = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "standard_version": GWI_STANDARD_VERSION,
            "total_projects": len(reports),
            "passed_9plus": sum(1 for r in reports if r["calculation"]["status"] == "PASS"),
            "projects": reports,
        }
        if getattr(args, "json", False):
            print(json.dumps(summary, indent=2))
        else:
            print(f"=== Sovereign Portfolio — Goldwood Index (G >= 9.0, v{GWI_STANDARD_VERSION}) Leaderboard ===")
            print(
                f"  {'Project':<26} {'Profile':<8} {'S_sub':>6} {'S_perc':>6} {'S_act':>6} {'S_exist':>7} {'Prod Psi':>9} {'G(S)':>7}  {'Status'}"
            )
            print("  " + "-" * 96)
            for r in reports:
                c = r["calculation"]
                t = r["tiers"]
                print(
                    f"  {r['project_name']:<26} {r['profile'].upper():<8} "
                    f"{t['S_sub']:6.2f} {t['S_perc']:6.2f} {t['S_act']:6.2f} {t['S_exist']:7.2f} "
                    f"{c['barrier_product']:9.4f} {c['goldwood_g']:7.2f}  {c['verdict']}"
                )
            print(
                f"\n  Portfolio Summary: {summary['passed_9plus']} / {summary['total_projects']} project(s) meet the 9+ Goldwood Standard."
            )
        any_veto = any((r.get("craft_lenses") or {}).get("veto_triggered") for r in reports)
        any_lens_fail = any(
            (r.get("craft_lenses") or {}).get("coupled_goldwood_g", 0.0) < DEFAULT_THRESHOLD_G for r in reports
        )
        if getattr(args, "strict", False) and (summary["passed_9plus"] < summary["total_projects"] or any_veto):
            sys.exit(1)
        if strict_lenses and (any_lens_fail or any_veto):
            sys.exit(1)
        return

    target_path = Path(rest[0]).expanduser().resolve() if rest else Path.cwd().resolve()
    strict_lenses = bool(getattr(args, "strict_lenses", False))
    execute_gate = bool(getattr(args, "execute_gate", False))
    probe_audio = bool(getattr(args, "probe_audio", False))
    req_profile = str(getattr(args, "profile", "auto"))
    incremental = bool(getattr(args, "incremental", False))
    require_el = bool(getattr(args, "require_elenchus", False))
    seal_arg = getattr(args, "seal", "") or ""
    in_gate_exec = os.environ.get("GWI_IN_GATE_EXECUTION") == str(target_path.resolve())

    def _enforce_gwi_elenchus_on_target(target_p: Path, gwi_rep: dict[str, Any] | None) -> dict[str, Any]:
        from ..elenchus import evaluate_elenchus_gate

        return evaluate_elenchus_gate(target_p, gwi_report=gwi_rep, require_independent=True)

    if getattr(args, "subprojects", False) and subcmd == "audit":
        subs = discover_subprojects(target_path)
        sub_results: list[dict[str, Any]] = []
        any_fail = False
        any_lens_fail = False
        any_elenchus_fail = False
        for sp in subs:
            sp_path = Path(sp["abs_path"])
            fresh_info = evaluate_subproject_freshness(
                target_path,
                sp["subpath"],
                requested_profile=sp["profile"],
                require_strict_lenses=strict_lenses,
                require_probe_audio=probe_audio if sp["profile"] == "video" else False,
                require_execute_gate=execute_gate if sp["profile"] in ("app", "sim") else False,
                require_theta=theta,
                require_k=k,
            )
            if incremental and fresh_info["is_fresh_pass"]:
                sp_entry: dict[str, Any] = {
                    "subpath": sp["subpath"],
                    "name": sp["name"],
                    "profile": sp["profile"],
                    "cache_hit": True,
                    "freshness": fresh_info,
                }
                if require_el and not in_gate_exec:
                    el_gate = _enforce_gwi_elenchus_on_target(sp_path, None)
                    sp_entry["elenchus_gate"] = el_gate
                    if not el_gate["passed"]:
                        any_elenchus_fail = True
                sub_results.append(sp_entry)
                if not getattr(args, "json", False):
                    v = fresh_info.get("versions") or {}
                    print(
                        f"⚡ [CACHE HIT] {sp['name']} ({sp['profile'].upper()}) — FRESH @ {fresh_info['current_commit']} "
                        f"(tree={fresh_info['current_tree']}, G={fresh_info['goldwood_g']:.2f} PASS, "
                        f"kb={v.get('kb_version')}/gwi={v.get('gwi_version')}/swm={v.get('swm_version')})"
                    )
                    if require_el and not in_gate_exec and not sp_entry["elenchus_gate"]["passed"]:
                        eg = sp_entry["elenchus_gate"]
                        print(f"  ! [gwi --require-elenchus] BLOCKED ({eg['reason_code']}): {eg['message']}")
                continue

            sp_rep = getattr(sys.modules.get("sovereign_kb.gwi"), "evaluate_target", evaluate_target)(
                sp_path,
                profile=sp["profile"],
                probe_audio=probe_audio,
                theta=theta,
                k=k,
                strict_lenses=strict_lenses,
                execute_gate=execute_gate,
            )
            aud_rec = record_gwi_audit(sp_path, sp_rep)
            if sp_rep["calculation"]["status"] != "PASS":
                any_fail = True
            sp_cl = sp_rep.get("craft_lenses") or {}
            if sp_cl.get("coupled_goldwood_g", 0.0) < DEFAULT_THRESHOLD_G or sp_cl.get("veto_triggered"):
                any_lens_fail = True
            sp_entry = {
                "subpath": sp["subpath"],
                "name": sp["name"],
                "profile": sp["profile"],
                "cache_hit": False,
                "audit_record": aud_rec,
                "report": sp_rep,
            }
            if require_el and not in_gate_exec:
                el_gate = _enforce_gwi_elenchus_on_target(sp_path, sp_rep)
                sp_entry["elenchus_gate"] = el_gate
                if not el_gate["passed"]:
                    any_elenchus_fail = True
            sub_results.append(sp_entry)
            if not getattr(args, "json", False):
                c = sp_rep["calculation"]
                print(
                    f"🔍 [AUDITED]   {sp['name']} ({sp['profile'].upper()}) @ {aud_rec['commit_sha']} "
                    f"(tree={aud_rec['tree_sha']}) -> G={c['goldwood_g']:.2f} / 10.00 ({c['status']}, "
                    f"blockers={len(sp_rep.get('blockers') or [])})"
                )
                if require_el and not in_gate_exec and not sp_entry["elenchus_gate"]["passed"]:
                    eg = sp_entry["elenchus_gate"]
                    print(f"  ! [gwi --require-elenchus] BLOCKED ({eg['reason_code']}): {eg['message']}")
        if getattr(args, "json", False):
            print(json.dumps({"repo_root": str(find_git_root(target_path)), "subprojects": sub_results}, indent=2))
        if (getattr(args, "strict", False) and any_fail) or any_elenchus_fail:
            sys.exit(1)
        # Cache hits already require a passing stored lens score under --strict-lenses (is_fresh_pass).
        if strict_lenses and any_lens_fail:
            sys.exit(1)
        return

    if incremental and subcmd == "audit" and not (seal_arg or getattr(args, "write_evidence", False)):
        root_dir = find_git_root(target_path)
        try:
            rel_sp = str(target_path.relative_to(root_dir)) if target_path != root_dir else "."
        except ValueError:
            rel_sp = "."
        fresh_info = evaluate_subproject_freshness(
            root_dir,
            rel_sp,
            requested_profile=req_profile,
            require_strict_lenses=strict_lenses,
            require_probe_audio=probe_audio,
            require_execute_gate=execute_gate,
            require_theta=theta,
            require_k=k,
        )
        if fresh_info["is_fresh_pass"]:
            el_gate_inc: dict[str, Any] | None = None
            if require_el and not in_gate_exec:
                el_gate_inc = _enforce_gwi_elenchus_on_target(target_path, None)
                if not el_gate_inc["passed"]:
                    if getattr(args, "json", False):
                        print(
                            json.dumps(
                                {
                                    "error": el_gate_inc["message"],
                                    "elenchus_gate": el_gate_inc,
                                    "cache_hit": True,
                                    "freshness": fresh_info,
                                },
                                indent=2,
                            )
                        )
                    else:
                        print(f"[gwi --require-elenchus] Error: {el_gate_inc['message']}", file=sys.stderr)
                    sys.exit(1)
            if getattr(args, "json", False):
                payload_inc: dict[str, Any] = {"cache_hit": True, "freshness": fresh_info}
                if el_gate_inc is not None:
                    payload_inc["elenchus_gate"] = el_gate_inc
                print(json.dumps(payload_inc, indent=2))
            else:
                v = fresh_info.get("versions") or {}
                print(
                    f"⚡ [CACHE HIT — SKIPPED RE-AUDIT] {fresh_info['target_label']} ({fresh_info['profile'].upper()})\n"
                    f"  Last Audit : {fresh_info['audited_at']}\n"
                    f"  Commit/Tree: {fresh_info['current_commit']} (tree={fresh_info['current_tree']}, dirty=False)\n"
                    f"  Score      : G = {fresh_info['goldwood_g']:.2f} / 10.00 ({fresh_info['status']})\n"
                    f"  Triad      : {fresh_info['triad_disposition']} "
                    f"({'audited @ ' + str(fresh_info['triad_commit_sha']) if fresh_info['triad_ever_audited'] else 'never audited by Astra x Fable'})\n"
                    f"  Versions   : kb={v.get('kb_version')} | gwi={v.get('gwi_version')} | swm={v.get('swm_version')}"
                )
            return

    report = getattr(sys.modules.get("sovereign_kb.gwi"), "evaluate_target", evaluate_target)(
        target_path,
        profile=req_profile,
        probe_audio=probe_audio,
        theta=theta,
        k=k,
        strict_lenses=strict_lenses,
        execute_gate=execute_gate,
    )
    audit_rec = record_gwi_audit(target_path, report)
    report["audit_provenance"] = audit_rec
    report["triad_state"] = triad_review_state(
        audit_rec.get("triad") or {}, audit_rec.get("full_commit_sha") or audit_rec.get("commit_sha"), target_path
    )

    if getattr(args, "write_evidence", False) and not in_gate_exec:
        ev_file = write_goldwood_evidence(target_path, report)
        report["evidence_file"] = str(ev_file)

    if seal_arg and not in_gate_exec:
        from ..evr import seal_gwi_audit_capsule

        seal_out = None if seal_arg == "__DEFAULT__" else seal_arg
        try:
            evr_seal = seal_gwi_audit_capsule(
                target_path,
                output_path=seal_out,
                report=report,
                require_elenchus=require_el,
            )
        except ValueError as exc:
            if getattr(args, "json", False):
                print(json.dumps({"error": str(exc), "report": report}, indent=2))
            else:
                print(f"[gwi --seal] Error: {exc}", file=sys.stderr)
            sys.exit(1)
        report["evr_capsule"] = {
            "path": evr_seal["path"],
            "file_size_bytes": evr_seal["file_size_bytes"],
            "merkle_root": evr_seal["merkle_root"],
            "pistis_chain_event": evr_seal.get("pistis_chain_event"),
        }
    elif require_el and not in_gate_exec:
        el_gate = _enforce_gwi_elenchus_on_target(target_path, report)
        report["elenchus_gate"] = el_gate
        if not el_gate["passed"]:
            if getattr(args, "json", False):
                print(json.dumps({"error": el_gate["message"], "elenchus_gate": el_gate, "report": report}, indent=2))
            else:
                print(f"[gwi --require-elenchus] Error: {el_gate['message']}", file=sys.stderr)
            sys.exit(1)

    if subcmd == "prompt":
        prompt_text = build_multi_model_prompt(report, agent=getattr(args, "agent", "astra"))
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"agent": getattr(args, "agent", "astra"), "prompt": prompt_text, "report": report}, indent=2
                )
            )
        else:
            print(prompt_text)
        return

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        print(format_audit_report(report))
        v = audit_rec.get("versions") or {}
        triad_label = report["triad_state"]["label"]
        print(
            f"\n  [7. Sovereign Audit Provenance & Cache State]\n"
            f"    • Recorded At       : {audit_rec['audited_at']} -> {AUDIT_REGISTRY_PATH}\n"
            f"    • Target & Scope    : {audit_rec['name']} (commit={audit_rec['commit_sha']}, tree={audit_rec['tree_sha']})\n"
            f"    • Models Executed   : {', '.join(audit_rec.get('models') or [])}\n"
            f"    • Tri-Council State : {triad_label}\n"
            f"    • Stack Versions    : kb={v.get('kb_version')} ({v.get('kb_sha')}) | "
            f"gwi={v.get('gwi_version')} | swm={v.get('swm_version')} ({v.get('swm_sha')}) | me-kb={v.get('me_kb_sha')}"
        )
        if report.get("evidence_file"):
            print(f"    • Evidence Manifest : {report['evidence_file']}")
        if report.get("evr_capsule"):
            ec = report["evr_capsule"]
            print(f"    • Sealed .evr Audit : {ec['path']} (merkle={ec['merkle_root'][:16]}...)")

    cl = report.get("craft_lenses") or {}
    if getattr(args, "strict", False) and (report["calculation"]["status"] != "PASS" or cl.get("veto_triggered")):
        sys.exit(1)
    if strict_lenses and (cl.get("coupled_goldwood_g", 0.0) < DEFAULT_THRESHOLD_G or cl.get("veto_triggered")):
        sys.exit(1)


def main_cli(argv: list[str] | None = None) -> None:
    """Standalone `gwi` CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="gwi",
        description="Unified Goldwood Index (G >= 9.0) & Global Watchability Index (GWI+ v1.4.0) Quality Gate CLI",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {GWI_STANDARD_VERSION}")
    _configure_gwi_parser(parser)
    args = parser.parse_args(argv)
    cmd_gwi(args)
