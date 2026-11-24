"""CLI subparser and `skb elenchus` / `organon elenchus` command handler."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .adjudication import adjudicate_elenchus_finding, evaluate_elenchus_gate
from .constants import _safe_float
from .council import run_elenchus_challenge
from .git_diff import _resolve_target_and_scope, build_elenchus_challenge_packet


def _print_elenchus_report(receipt: dict[str, Any]) -> None:
    raw_panel = receipt.get("panel")
    panel: dict[str, Any] = raw_panel if isinstance(raw_panel, dict) else {}
    raw_metrics = receipt.get("metrics")
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    raw_lins = panel.get("distinct_llm_lineages")
    lins_list = [str(x) for x in raw_lins] if isinstance(raw_lins, list) and raw_lins else ["deterministic"]
    print(f"=== Elenchus Pre-Seal Adversarial Challenge ({receipt.get('project_name')}) ===")
    print(
        f"  Seal Status   : {receipt.get('seal_status')} | Mode={panel.get('panel_mode')} | "
        f"N_eff={_safe_float(panel.get('n_eff'), 1.0):.2f} (lineages={', '.join(lins_list)})"
    )
    if panel.get("warning"):
        print(f"  Warning       : {panel['warning']}")
    print(
        f"  Disagreements : clusters={metrics.get('total_clusters', 0)} | "
        f"convergence={metrics.get('independent_convergence_count', 0)} | "
        f"echo={metrics.get('prompt_anchored_echo_count', 0)} | "
        f"split={metrics.get('lineage_split_count', 0)} | "
        f"disagreement_index={_safe_float(metrics.get('disagreement_index'), 0.0):.2f}"
    )
    raw_bindings = receipt.get("bindings")
    bindings: dict[str, Any] = raw_bindings if isinstance(raw_bindings, dict) else {}
    print(
        f"  Bindings      : commit={bindings.get('commit_sha')} | tree={bindings.get('tree_sha')} | "
        f"worktree={bindings.get('worktree_sha')} | score_blind={receipt.get('score_blind')}"
    )
    raw_clusters = receipt.get("clusters")
    for c in raw_clusters if isinstance(raw_clusters, list) else []:
        if not isinstance(c, dict):
            continue
        raw_adj = c.get("adjudication")
        adj: dict[str, Any] = raw_adj if isinstance(raw_adj, dict) else {}
        loc = f"{c['file_path']}:{c['line']}" if c.get("file_path") else "(repo-wide)"
        adj_lbl = adj.get("decision") or adj.get("status") or "PENDING"
        print(
            f"  • [{c.get('severity', 'P1')}] {c.get('cluster_id', '')} ({c.get('signal_class', '')}, "
            f"alpha={_safe_float(c.get('anchoring_ratio'), 0.0):.2f}) @ {loc} [{adj_lbl}]: {c.get('claim', '')}"
        )


def register_elenchus_subparser(subparsers: Any, show_all_aliases: bool = False) -> None:
    """Register `elenchus` CLI subcommand onto `skb` / `organon` parser."""
    e_help = (
        "Pre-seal cross-lineage adversarial challenge, independence calibration & human adjudication gate"
        if show_all_aliases
        else None
    )
    p = subparsers.add_parser("elenchus", **({"help": e_help} if e_help else {}))
    p.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("status", "run", "adjudicate", "prompt"),
        help="Elenchus action (`status`, `run`, `adjudicate`, `prompt`)",
    )
    p.add_argument("target", nargs="?", default=".", help="Target repository or project path (default: .)")
    p.add_argument("--intent", default="", help="Stated task intent to challenge against implementation")
    p.add_argument("--reviews", default="", help="Path to JSON review array or parallel `oc triad` transcript")
    p.add_argument(
        "--runner",
        choices=("auto", "oc", "http", "offline"),
        default="auto",
        help="Reviewer execution backend (`auto`, `oc`, `http`, `offline`)",
    )
    p.add_argument("--offline", action="store_true", help="Shortcut for `--runner offline`")
    p.add_argument(
        "--http-endpoint",
        action="append",
        default=[],
        help="OpenAI-compatible endpoint in `model_id=http://.../v1/chat/completions` format (repeatable)",
    )
    p.add_argument(
        "--include-scores",
        action="store_true",
        help="Include numeric GWI scores in challenge prompt (disables default score-blind mode)",
    )
    p.add_argument("--finding", default="", help="Disputed cluster ID (e.g. `ELC-001`) or `ALL` for `adjudicate`")
    p.add_argument("--decision", default="", help="Human decision (`accept`, `reject`, `escalate`) for `adjudicate`")
    p.add_argument("--reason", default="", help="Explicit human rationale (>= 12 chars required for `reject`)")
    p.add_argument("--by", default="human-operator", help="Human adjudicator identifier")
    p.add_argument(
        "--override",
        action="store_true",
        help="Allow `adjudicate` to change a cluster already adjudicated accept/escalate (recorded in history)",
    )
    p.add_argument("--strict", action="store_true", help="Exit with code 1 if gate does not pass")
    p.add_argument("--json", action="store_true", help="Output machine-readable JSON")


def cmd_elenchus(args: argparse.Namespace) -> None:
    action = getattr(args, "action", "status") or "status"
    target = getattr(args, "target", ".") or "."
    as_json = bool(getattr(args, "json", False))
    target_dir, _, _ = _resolve_target_and_scope(target)

    if action == "prompt":
        packet = build_elenchus_challenge_packet(
            target_dir,
            intent=str(getattr(args, "intent", "") or ""),
            blind_scores=not bool(getattr(args, "include_scores", False)),
        )
        if as_json:
            print(json.dumps(packet, indent=2))
        else:
            print(packet["prompt"])
        return

    if action == "status":
        r_path = target_dir / "evidence" / "elenchus_challenge.json"
        loaded: dict[str, Any] | None = None
        if r_path.is_file():
            try:
                raw_loaded = json.loads(r_path.read_text(encoding="utf-8"))
                if isinstance(raw_loaded, dict):
                    loaded = raw_loaded
            except Exception:
                loaded = None
        gate = evaluate_elenchus_gate(target_dir, receipt=loaded, require_independent=True)
        payload = {"receipt": loaded, "gate": gate}
        if as_json:
            print(json.dumps(payload, indent=2))
        else:
            if isinstance(loaded, dict):
                _print_elenchus_report(loaded)
                print(f"  Gate Check    : {'PASS' if gate['passed'] else 'BLOCKED'} ({gate['reason_code']})")
            else:
                print(f"=== Elenchus Status ({target_dir.name}): {gate['seal_status']} ===")
                print(f"  {gate['message']}")
        if getattr(args, "strict", False) and not gate["passed"]:
            sys.exit(1)
        return

    if action == "run":
        runner = "offline" if getattr(args, "offline", False) else str(getattr(args, "runner", "auto") or "auto")
        reviews_arg = str(getattr(args, "reviews", "") or "").strip() or None
        try:
            receipt = run_elenchus_challenge(
                target_dir,
                intent=str(getattr(args, "intent", "") or ""),
                reviews_file=reviews_arg,
                runner=runner,
                http_endpoints=list(getattr(args, "http_endpoint", []) or []),
                blind_scores=not bool(getattr(args, "include_scores", False)),
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            if as_json:
                print(json.dumps({"error": str(exc)}, indent=2))
            else:
                print(f"[elenchus run] Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if as_json:
            print(json.dumps(receipt, indent=2))
        else:
            _print_elenchus_report(receipt)
        if getattr(args, "strict", False) and receipt.get("seal_status") not in (
            "READY_UNANIMOUS",
            "READY_ADJUDICATED",
        ):
            sys.exit(1)
        return

    if action == "adjudicate":
        try:
            receipt = adjudicate_elenchus_finding(
                target_dir,
                finding_id=str(getattr(args, "finding", "") or ""),
                decision=str(getattr(args, "decision", "") or ""),
                reason=str(getattr(args, "reason", "") or ""),
                adjudicator=str(getattr(args, "by", "human-operator") or "human-operator"),
                override=bool(getattr(args, "override", False)),
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            if as_json:
                print(json.dumps({"error": str(exc)}, indent=2))
            else:
                print(f"[elenchus adjudicate] Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if as_json:
            print(json.dumps(receipt, indent=2))
        else:
            _print_elenchus_report(receipt)
        if getattr(args, "strict", False) and receipt.get("seal_status") not in (
            "READY_UNANIMOUS",
            "READY_ADJUDICATED",
        ):
            sys.exit(1)
        return
