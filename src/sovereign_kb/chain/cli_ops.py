from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .aristocracy import audit_organon_aristocracy, collect_pistis_summary
from .merkle import anchor_organon_attestation
from .negotiation import run_negotiation_simulation
from .territory_tokens import PISTIS_PROTOCOL_VERSION
from .tokenomics import run_tokenomics_suite

# ==============================================================================
# 5. CLI SUBPARSER & COMMAND DISPATCH (`organon chain` / `skb chain`)
# ==============================================================================


def register_chain_subparser(subparsers: Any, show_all_aliases: bool = False) -> None:
    aliases = ("chain", "pistis", "blockchain")
    for idx, name in enumerate(aliases):
        help_txt = (
            "Pistis (Πίστις) — Poetics Bilateral Licensing FSM, RFC 6962 Merkle Anchor, PhD Tokenomics & Aristocracy"
            if idx == 0 or show_all_aliases
            else None
        )
        p = subparsers.add_parser(name, **({"help": help_txt} if help_txt else {}))
        p.add_argument(
            "action",
            nargs="?",
            choices=("status", "negotiate", "anchor", "tokenomics", "aristocracy", "full-suite"),
            default="status",
            help="Pistis blockchain action (default: status)",
        )
        p.add_argument("--target", default=".", help="Target repository directory for `anchor` (default: .)")
        p.add_argument("--json", action="store_true", help="Output machine-readable JSON")


def _emit_cli_error(msg: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": msg, "valid": False}, indent=2))
    else:
        print(f"[organon chain] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def cmd_chain(args: argparse.Namespace) -> None:
    action = getattr(args, "action", "status") or "status"
    as_json = bool(getattr(args, "json", False))
    raw_target = getattr(args, "target", ".") or "."
    target_dir = Path(raw_target).expanduser().resolve()

    try:
        if action == "negotiate":
            res = run_negotiation_simulation()
            ok = bool(
                res["s1_signatures_verified"]
                and res["s2_self_acceptance_blocked"]
                and res["s2_frontrunning_blocked"]
                and res["s3_atomic_plt_minted"]
                and res["system_wei_conserved"]
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                plt = res["plt"]
                dist = res["royalty_distribution"]
                s1_status = "PASS" if res["s1_signatures_verified"] else "FAIL"
                print("=== [Pistis 1/4] Poetics On-Chain Bilateral Negotiation (`NC` + `PLT` + `RoyaltySplitter`) ===")
                print(
                    f"  FSM Lifecycle     : IDLE -> OFFERED -> COUNTERED -> ACCEPTED -> {res['fsm_final_state']} "
                    f"({res['rounds_taken']} rounds, {res['s1_non_repudiation_events']} HMAC-signed events)"
                )
                print(
                    f"  Safety Guards     : S1 Signatures={s1_status} | S2 Self-Accept Blocked={res['s2_self_acceptance_blocked']} "
                    f"| S2 Front-Run Blocked={res['s2_frontrunning_blocked']} | S3 Atomic PLT={res['s3_atomic_plt_minted']}"
                )
                print(
                    f"  Minted PLT #{plt['plt_id']}     : royalty={plt['agreed_terms']['royalty_bps']} bps | "
                    f"territories={','.join(plt['agreed_terms']['territories_decoded'])} | "
                    f"cap={plt['agreed_terms']['max_issuances']} | termsHash={plt['terms_hash'][:18]}..."
                )
                print(
                    f"  RoyaltySplitter   : Gross={dist['gross_amount_wei']} wei -> Creators={dist['creator_total_wei']} wei "
                    f"+ Platform={dist['platform_amount_wei']} wei (Wei Conserved={dist['wei_conserved']}, "
                    f"Pull-Escrow Withdrawn={res['pull_escrow_withdrawn_wei']} wei)"
                )
            if not ok:
                sys.exit(1)
            return

        if action == "anchor":
            res = anchor_organon_attestation(target_dir=target_dir)
            if as_json:
                print(json.dumps(res, indent=2))
                return
            print("=== [Pistis 2/4] RFC 6962 Merkle Provenance & On-Chain Attestation Anchor ===")
            print(
                f"  Anchor Block ID   : {res['anchor_id']} (repo={res['target_repo']}, git={res['git_head_sha'][:12]})"
            )
            print(f"  RFC 6962 Root     : {res['rfc6962_merkle_root']} ({res['merkle_leaves_count']} leaves)")
            print(
                f"  Inclusion Proof   : {res['inclusion_proof_len']} sibling hop(s) | "
                f"Verified={res['inclusion_proof_verified']}"
            )
            p3 = res["p3_provenance"]
            print(
                f"  P3 Trace (T_prov) : {p3['stages_count']} stages (privilege {p3['origin_privilege']:.2f} -> "
                f"{p3['final_privilege']:.2f}, declassified={p3['declassified']}, valid={p3['valid']})"
            )
            print(f"  Anchor SHA-256    : {res['anchor_hash']}")
            return

        if action == "tokenomics":
            res = run_tokenomics_suite()
            ctl = res["pid_mpc_control"]
            lya = ctl["lyapunov_certificate"]
            ok = bool(res["egt_replicator"]["converged_to_ne_8"] and lya["schur_stable"] and lya["positive_definite_p"])
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                egt = res["egt_replicator"]
                col = res["nash_q_collusion_audit"]
                af = res["antifragility"]
                print("=== [Pistis 3/4] PhD Virtual Token Economies (`ai_tokenomics`) & Antifragility (`AMI`) ===")
                print(
                    f"  Game Equilibria   : Grid Monopoly p*=10 (R=50.0, Continuous p*=13.33) | "
                    f"Competitive NE p*=8 (R=40.8) | EGT ESS p*={egt['ess_price']} (prob={egt['ess_probability']:.4f})"
                )
                print(
                    f"  Folk-Theorem Audit: Nash-Q Benchmark p={col['observed_price']} -> "
                    f"Collusion Index={col['collusion_index']:.2f} [{col['regime']}]"
                )
                print(
                    f"  Shared-Plant Ctrl : RMSE Uncontrolled={ctl['rmse_uncontrolled']:.4f} | "
                    f"Causal PID={ctl['rmse_pid']:.4f} | Riccati MPC={ctl['rmse_mpc']:.4f} "
                    f"({ctl['mpc_volatility_reduction_x']}x volatility reduction)"
                )
                print(
                    f"  Lyapunov Theorem 4: Schur Stable={lya['schur_stable']} (rho={lya['spectral_radius']:.4f} < 1, "
                    f"P > 0={lya['positive_definite_p']}, residual={lya['residual_frobenius']}, "
                    f"bound={lya['ultimate_bound_sq']:.6f})"
                )
                print(
                    f"  Antifragility     : Power Universal={af['power_adaptation']['universally_antifragile']} "
                    f"(k_p={af['power_adaptation']['k_p']} < gamma_0={af['initial_snr_gamma0']}) | "
                    f"BW Regime={af['bandwidth_adaptation']['regime']} | AMI={af['antifragile_market_index']['ami_score']:.4f}"
                )
            if not ok:
                sys.exit(1)
            return

        if action == "aristocracy":
            res = audit_organon_aristocracy()
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                m = res["metrics"]
                br = res["blast_radius_bound"]
                p4 = res["constitutional_resilience"]["P4_multi_channel_validation"]
                print("=== [Pistis 4/4] PhD Software Aristocracy & Constitutional Resilience (`aristocracy`) ===")
                print(
                    f"  Concentration     : Gamma_0(A)={m['gamma_0_raw']:.4f} | Size-Adjusted Gamma_tilde_0(A)="
                    f"{m['gamma_0_size_adjusted']:.4f} | Gini G(A_0)={m['authority_gini_G0']:.4f}"
                )
                print(
                    f"  Blast Radius (P1) : Theoretical Lower Bound >= {br['proposition1_lower_bound']:.2f} | "
                    f"Core 1-Hop Out={br['max_core_1hop_out_degree']} | Transitive Reach={br['transitive_reach']}/7"
                )
                print(
                    f"  Quorum Gate (P4)  : Single-Channel theta={p4['single_channel_theta']} -> "
                    f"3-Channel Quorum={p4['quorum_override_prob']:.6f} (Correlated Bound={p4['quorum_correlated_llm_bound']:.4f})"
                )
                print(f"  Constitutional P1-P5: {'ALL PASSED' if res['all_principles_passed'] else 'FAILED'}")
            if not res["all_principles_passed"]:
                sys.exit(1)
            return

        # `status` or `full-suite`
        neg = run_negotiation_simulation()
        tok = run_tokenomics_suite()
        ari = audit_organon_aristocracy()
        anc = (
            anchor_organon_attestation(target_dir=target_dir)
            if action == "full-suite"
            else collect_pistis_summary(ari=ari, tok_ctl=tok["pid_mpc_control"])
        )
        summary = collect_pistis_summary(ari=ari, tok_ctl=tok["pid_mpc_control"]) if action == "full-suite" else anc
        combined: dict[str, Any] = {
            "protocol_version": PISTIS_PROTOCOL_VERSION,
            "summary": summary,
            "poetics_negotiation": neg,
            "anchor": anc,
            "phd_tokenomics": tok,
            "phd_aristocracy": ari,
        }
        if as_json:
            print(json.dumps(combined, indent=2))
            return

        s: dict[str, Any] = combined["summary"]
        s1_s4_ok = (
            neg["s1_signatures_verified"]
            and neg["s2_self_acceptance_blocked"]
            and neg["s2_frontrunning_blocked"]
            and neg["s3_atomic_plt_minted"]
            and neg["system_wei_conserved"]
        )
        lya_ok = tok["pid_mpc_control"]["lyapunov_certificate"]["schur_stable"]
        print("╔══════════════════════════════════════════════════════════════════════════════════════════╗")
        print("║  PISTIS (Πίστις) — ON-CHAIN COVENANT, BILATERAL LICENSING & PHD TOKENOMICS ENGINE        ║")
        print("╚══════════════════════════════════════════════════════════════════════════════════════════╝")
        print(
            f"  [1] Poetics Licensing (`negotiate`) : 7-State NC FSM -> PLT #{neg['plt']['plt_id']} | "
            f"S1-S4={'PASS' if s1_s4_ok else 'FAIL'} | 249 ISO Territories + Dual-Escrow RoyaltySplitter"
        )
        print(
            f"  [2] Merkle Provenance (`anchor`)    : {s['merkle_anchors_count']} anchored block(s) | "
            f"Latest Root={s['latest_merkle_root'] or 'unanchored (run `organon chain anchor`)'}"
        )
        print(
            f"  [3] PhD Tokenomics (`tokenomics`)   : EGT ESS p*={tok['egt_replicator']['ess_price']} | "
            f"Riccati MPC={tok['pid_mpc_control']['rmse_mpc']:.4f} ({tok['pid_mpc_control']['mpc_volatility_reduction_x']}x) | "
            f"Lyapunov Schur={'PASS' if lya_ok else 'FAIL'} | AMI={tok['antifragility']['antifragile_market_index']['ami_score']:.4f}"
        )
        print(
            f"  [4] PhD Aristocracy (`aristocracy`) : Gamma_0={s['aristocracy_gamma0']:.4f} (Size-Adj={s['aristocracy_tilde0']:.4f}, "
            f"Gini={s['aristocracy_gini0']:.4f}) | Constitutional P1-P5={'PASS' if s['constitutional_resilience_ok'] else 'FAIL'}"
        )
    except (ValueError, PermissionError, TimeoutError, KeyError, OSError) as exc:
        _emit_cli_error(str(exc), as_json=as_json)
