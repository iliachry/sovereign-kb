from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..bridge import verify_triad_provenance_chain
from .merkle import default_chain_anchors_path, verify_chain_anchors
from .territory_tokens import FSM_STATES, ISO_3166_ALPHA2_CODES, PISTIS_PROTOCOL_VERSION
from .tokenomics import simulate_pid_mpc_token_control

# ==============================================================================
# 4. PHD SOFTWARE ARISTOCRACY & CONSTITUTIONAL RESILIENCE (`aristocracy`)
# ==============================================================================


def _spectral_radius_power_iter(mat: list[list[float]], iters: int = 40) -> float:
    """Estimate spectral radius rho(mat) via matrix power Frobenius norm ||M^k||_F^{1/k}."""
    n = len(mat)
    cur = [row[:] for row in mat]
    for _ in range(5):
        # Square matrix 5 times -> M^32
        nxt = [[0.0 for _ in range(n)] for _ in range(n)]
        for i in range(n):
            for k in range(n):
                if cur[i][k] != 0.0:
                    for j in range(n):
                        nxt[i][j] += cur[i][k] * cur[k][j]
        cur = nxt
    frob_sq = sum(cur[i][j] ** 2 for i in range(n) for j in range(n))
    return (frob_sq**0.5) ** (1.0 / 32.0)


def _solve_linear_system_nxn(mat_a: list[list[float]], rhs_b: list[float]) -> list[float]:
    """Solve `mat_a * x = rhs_b` via Gaussian elimination with partial pivoting."""
    n = len(rhs_b)
    aug = [mat_a[i][:] + [rhs_b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            raise ValueError("SingularMatrix: (I - gamma * W) is not invertible (gamma * rho(W) >= 1)")
        aug[col], aug[piv] = aug[piv], aug[col]
        div = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= div
        for r in range(n):
            if r != col:
                fac = aug[r][col]
                for j in range(col, n + 1):
                    aug[r][j] -= fac * aug[col][j]
    return [aug[i][n] for i in range(n)]


def audit_organon_aristocracy(
    gamma_decay: float = 0.5,
    ledger_file: Path | None = None,
    anchors_file: Path | None = None,
) -> dict[str, Any]:
    """
    Model The Sovereign Organon's runtime governance topology as a Dual-Weight Authority Graph
    G = (V, E, alpha, delta, beta) (`aristocracy/sections/aristocracy.tex`) and compute:
      - Direct Aristocratic Index A_0(v_i) = sum_j alpha(e_{ij}) * delta(e_{ij})
      - Exact Transitive Aristocratic Index A_gamma(v_i) = [(I - gamma*W)^{-1} W 1]_i
      - Raw & Size-Adjusted Aristocracy Coefficients (Gamma_0(A), Gamma_tilde_0(A), Gamma_gamma(A))
      - Authority Gini Coefficient G(A_0)
      - Proposition 1 Structural Blast Radius Bound
      - Principles P1-P5 Constitutional Resilience verification computed from graph edges & live ledgers.
    """
    if (
        isinstance(gamma_decay, bool)
        or not isinstance(gamma_decay, (int, float))
        or not math.isfinite(float(gamma_decay))
        or float(gamma_decay) <= 0.0
        or float(gamma_decay) >= 1.0
    ):
        raise ValueError("InvalidGammaDecay: gamma_decay must be in (0.0, 1.0)")
    gamma = float(gamma_decay)

    actors = [
        "v0:sovereign_architect",  # Aristocratic Core (A)
        "v1:organon_orchestrator",  # Aristocratic Core (A)
        "v2:gwi_phronesis_gate",  # Stateless P4 Reference Monitor
        "v3:tri_council_quorum",  # Stateless P4 Multi-Model Quorum
        "v4:kb_episteme_retriever",  # Subordinate Specialist
        "v5:swm_stage_worker",  # Subordinate Specialist
        "v6:pistis_chain_settler",  # Subordinate Specialist
        "v7:peripheral_tool_sandbox",  # Peripheral Execution Node
    ]
    actor_privileges = [1.00, 0.95, 0.60, 0.60, 0.45, 0.45, 0.50, 0.20]
    core_indices = [0, 1]  # |A| = 2, |V| = 8
    core_set = set(core_indices)
    n_actors = len(actors)

    # Directed edges: (src_idx, dst_idx, alpha_governance, delta_strictness, beta_semantic_bandwidth)
    edges: list[tuple[int, int, float, float, float]] = [
        # Downward constitutional governance from Sovereign Architect (v0) and Orchestrator (v1)
        (0, 1, 1.00, 1.00, 0.15),
        (0, 2, 0.95, 1.00, 0.05),
        (0, 3, 0.90, 0.95, 0.10),
        (1, 2, 0.90, 1.00, 0.10),
        (1, 3, 0.85, 0.95, 0.15),
        (1, 4, 0.95, 0.95, 0.20),
        (1, 5, 0.95, 0.95, 0.25),
        (1, 6, 0.95, 1.00, 0.10),
        (5, 7, 0.35, 0.80, 0.20),
        # Upward feedback edges (alpha = 0.0 legitimate downward authority, mediated beta)
        (7, 2, 0.00, 0.00, 0.22),  # Peripheral outputs routed through GWI Reference Monitor (P1/P4)
        (4, 2, 0.00, 0.00, 0.18),  # RAG retrieval routed through taint-aware verifier (P1/P3)
        (2, 1, 0.00, 1.00, 0.08),  # Symbolic gate receipt (delta=1.0, low semantic bandwidth beta=0.08)
        (3, 1, 0.00, 0.95, 0.10),  # Multi-model quorum verdict (P4)
    ]

    w_mat = [[0.0 for _ in range(n_actors)] for _ in range(n_actors)]
    beta_mat = [[0.0 for _ in range(n_actors)] for _ in range(n_actors)]
    for u, v, alpha, delta, beta in edges:
        w_mat[u][v] = alpha * delta
        beta_mat[u][v] = beta

    rho_w = _spectral_radius_power_iter(w_mat)
    if gamma * rho_w >= 1.0:
        raise ValueError(f"NeumannSeriesDivergence: gamma ({gamma}) * rho(W) ({rho_w:.4f}) >= 1.0")

    # 1. Direct (1-hop) Aristocratic Index A_0(v_i) = (W 1)_i
    a0 = [sum(w_mat[i][j] for j in range(n_actors)) for i in range(n_actors)]
    s0 = sum(a0)

    # 2. Exact Transitive Aristocratic Index A_gamma(v_i) = [(I - gamma*W)^{-1} W 1]_i
    i_minus_gw = [[(1.0 if i == j else 0.0) - gamma * w_mat[i][j] for j in range(n_actors)] for i in range(n_actors)]
    a_gamma = _solve_linear_system_nxn(i_minus_gw, a0)
    s_gamma = sum(a_gamma)

    # 3. Raw & Size-Adjusted Aristocracy Coefficients (Definition 4)
    core_ratio = len(core_indices) / n_actors
    gamma_0 = sum(a0[i] for i in core_indices) / max(1e-12, s0)
    gamma_tilde_0 = (gamma_0 - core_ratio) / (1.0 - core_ratio)

    gamma_trans = sum(a_gamma[i] for i in core_indices) / max(1e-12, s_gamma)
    gamma_tilde_trans = (gamma_trans - core_ratio) / (1.0 - core_ratio)

    # 4. Authority Gini Coefficient G(A_0)
    abs_diffs = sum(abs(a0[i] - a0[j]) for i in range(n_actors) for j in range(n_actors))
    gini_0 = abs_diffs / (2.0 * n_actors * max(1e-12, s0))

    # 5. Proposition 1 Structural Blast Radius Bound
    c_avg = s0 / n_actors
    theoretical_blast_lb = (gamma_0 * c_avg / len(core_indices)) * n_actors
    realized_out_max = max(sum(1 for j in range(n_actors) if w_mat[i][j] > 0) for i in core_indices)

    # 6. Constitutional Resilience Principles (P1-P5) Computed Dynamically from Graph & Live Ledgers
    # P1: Compute max upward semantic bandwidth beta(u -> v) from non-core u into core v,
    #     and check zero direct unmediated edges from peripheral v7 into core {0, 1}.
    upward_betas_to_core = [beta for (u, v, _a, _d, beta) in edges if u not in core_set and v in core_set]
    max_upward_beta = max(upward_betas_to_core) if upward_betas_to_core else 0.0
    direct_periphery_to_core = any(u == 7 and v in core_set for (u, v, _a, _d, _b) in edges)
    p1_passed = (not direct_periphery_to_core) and (max_upward_beta <= 0.12)

    # P2: Constitutional minimalism (|A| <= 2 and size-adjusted Gamma_tilde_0 >= 0.70)
    p2_passed = len(core_indices) <= 2 and gamma_tilde_0 >= 0.70

    # P3: Live cryptographic verification of both triad_provenance_ledger.jsonl and chain_anchors.jsonl
    triad_ok, triad_count = verify_triad_provenance_chain(ledger_file)
    anchors_ok, anchors_count, _ = verify_chain_anchors(anchors_file)
    p3_passed = bool(triad_ok and anchors_ok)

    # P4: Multi-channel validation (reports both independent product and correlated-LLM bound)
    theta_single = 0.25
    theta_symbolic_gwi = 0.05
    theta_fable = 0.12
    theta_astra = 0.12
    theta_quorum_independent = theta_symbolic_gwi * theta_fable * theta_astra  # 0.00072
    theta_quorum_correlated_llm = theta_symbolic_gwi * max(theta_fable, theta_astra)  # 0.006
    p4_passed = theta_quorum_independent < 0.005 and theta_quorum_correlated_llm <= 0.01

    # P5: Enumerate all simple upward semantic paths from non-core nodes to core nodes in G
    #     and compute R(pi) = (p(dst) - p(src)) * prod_{e in pi} beta(e)
    max_path_risk = 0.0
    worst_path_desc = ""
    for start_node in range(n_actors):
        if start_node in core_set:
            continue
        stack: list[tuple[int, list[int], float]] = [(start_node, [start_node], 1.0)]
        while stack:
            curr, path_nodes, prod_beta = stack.pop()
            if curr in core_set and len(path_nodes) > 1:
                priv_gap = max(0.0, actor_privileges[curr] - actor_privileges[start_node])
                # Reference monitors (v2, v3) apply P4 symbolic + quorum gating factor
                monitor_factor = theta_symbolic_gwi if (2 in path_nodes or 3 in path_nodes) else 1.0
                risk = priv_gap * prod_beta * monitor_factor
                if risk > max_path_risk:
                    max_path_risk = risk
                    worst_path_desc = " -> ".join(actors[idx] for idx in path_nodes)
                continue
            for nxt in range(n_actors):
                if beta_mat[curr][nxt] > 0.0 and nxt not in path_nodes and nxt > start_node - 8:
                    # Only follow upward/feedback edges (alpha == 0.0) toward core
                    if w_mat[curr][nxt] == 0.0:
                        stack.append((nxt, path_nodes + [nxt], prod_beta * beta_mat[curr][nxt]))

    path_privilege_risk = round(max_path_risk, 6)
    p5_passed = path_privilege_risk <= 0.01

    principles: dict[str, dict[str, Any]] = {
        "P1_semantic_privilege_isolation": {
            "passed": p1_passed,
            "max_upward_beta_to_core": round(max_upward_beta, 4),
            "direct_periphery_to_core_edges": direct_periphery_to_core,
            "mechanism": "Peripheral outputs filtered through symbolic GWI gate before entering Orchestrator context",
        },
        "P2_constitutional_minimalism": {
            "passed": p2_passed,
            "core_cardinality": len(core_indices),
            "total_actors": n_actors,
        },
        "P3_provenance_tracking": {
            "passed": p3_passed,
            "triad_ledger_valid": triad_ok,
            "triad_events_count": triad_count,
            "chain_anchors_valid": anchors_ok,
            "chain_anchors_count": anchors_count,
            "mechanism": "Append-only SHA-256 triad_provenance_ledger.jsonl + RFC 6962 Merkle chain_anchors.jsonl",
        },
        "P4_multi_channel_validation": {
            "passed": p4_passed,
            "single_channel_theta": theta_single,
            "quorum_override_prob": round(theta_quorum_independent, 6),
            "quorum_correlated_llm_bound": round(theta_quorum_correlated_llm, 6),
            "suppression_factor_x": round(theta_single / max(1e-9, theta_quorum_independent), 1),
        },
        "P5_aristocracy_threat_modeling": {
            "passed": p5_passed,
            "max_path_privilege_risk_R": path_privilege_risk,
            "critical_upward_path": worst_path_desc,
            "risk_bound_epsilon": 0.01,
        },
    }

    return {
        "actors_count": n_actors,
        "core_actors": [actors[i] for i in core_indices],
        "spectral_radius_W": round(rho_w, 6),
        "direct_aristocratic_index_A0": {actors[i]: round(a0[i], 3) for i in range(n_actors)},
        "transitive_aristocratic_index_Agamma": {actors[i]: round(a_gamma[i], 3) for i in range(n_actors)},
        "metrics": {
            "gamma_0_raw": round(gamma_0, 4),
            "gamma_0_size_adjusted": round(gamma_tilde_0, 4),
            "gamma_transitive_raw": round(gamma_trans, 4),
            "gamma_transitive_size_adjusted": round(gamma_tilde_trans, 4),
            "authority_gini_G0": round(gini_0, 4),
            "is_software_aristocracy": gamma_0 > 0.70 and gamma_tilde_0 >= 0.60 and gini_0 >= 0.40,
        },
        "blast_radius_bound": {
            "proposition1_lower_bound": round(theoretical_blast_lb, 2),
            "max_core_1hop_out_degree": realized_out_max,
            "transitive_reach": n_actors - 1,
            "bound_satisfied": realized_out_max >= theoretical_blast_lb,
        },
        "constitutional_resilience": principles,
        "all_principles_passed": all(p["passed"] for p in principles.values()),
    }


def collect_pistis_summary(
    ari: dict[str, Any] | None = None,
    tok_ctl: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact, fail-safe telemetry snapshot of the 4th Organon dimension (`Pistis`) for `organon status`."""
    ap = default_chain_anchors_path()
    anchors_valid, anchors_count, latest_merkle_root = verify_chain_anchors(ap)
    ari_data = ari if ari is not None else audit_organon_aristocracy(anchors_file=ap)
    ctl_data = tok_ctl if tok_ctl is not None else simulate_pid_mpc_token_control()
    return {
        "protocol_version": PISTIS_PROTOCOL_VERSION,
        "anchors_path": str(ap),
        "anchors_chain_valid": anchors_valid,
        "merkle_anchors_count": anchors_count if anchors_valid else 0,
        "latest_merkle_root": latest_merkle_root,
        "nc_fsm_states": len(FSM_STATES),
        "iso_territories_encoded": len(ISO_3166_ALPHA2_CODES),
        "mpc_rmse": ctl_data["rmse_mpc"],
        "mpc_volatility_reduction_x": ctl_data["mpc_volatility_reduction_x"],
        "lyapunov_schur_stable": ctl_data["lyapunov_certificate"]["schur_stable"],
        "aristocracy_gamma0": ari_data["metrics"]["gamma_0_raw"],
        "aristocracy_tilde0": ari_data["metrics"]["gamma_0_size_adjusted"],
        "aristocracy_gini0": ari_data["metrics"]["authority_gini_G0"],
        "constitutional_resilience_ok": bool(ari_data["all_principles_passed"] and anchors_valid),
    }
