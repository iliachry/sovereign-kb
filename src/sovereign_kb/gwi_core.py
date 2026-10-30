"""
Pure Mathematical Kernel for the Universal Goldwood Index (G) & Global Watchability Index (`sovereign_kb.gwi_core`).

Zero-I/O formulation of:
  - Normalized Sigmoid Catastrophic Barrier Psi_norm(x) = min(1.0, sigma(x) / sigma(9.0))
  - Two-Sided Subtractive Cognitive Load Functional P_subtractive(m) (Laws L0-L6)
  - Non-Compensatory Intra-Tier Aggregator aggregate_tier_pair(a, b)
  - Universal Goldwood Index G(S) = Lambda_conscience * (sum alpha_i S_i) * prod Psi_norm(S_i)
  - Unified Chrysos x Verga Coupled Goldwood Index G_coupled <= G(S)
  - Closed-form Jacobian Sensitivity nabla_S G(S) & exact plateau-aware headroom gain
  - Kolmogorov Existential Parsimony E(L)
  - Signal-to-Noise Ratio of Consciousness (SNRC)
"""

from __future__ import annotations

import math
from typing import Any

GWI_STANDARD_VERSION = "1.4.0"
DEFAULT_THETA = 7.0
DEFAULT_K = 2.5
DEFAULT_CEILING = 9.0
DEFAULT_THRESHOLD_G = 9.0
VERGA_UNMEASURED_CAP = 7.80

ARCHETYPE_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
    "balanced": (0.25, 0.25, 0.25, 0.25),
    "app": (0.25, 0.30, 0.25, 0.20),
    "sim": (0.25, 0.25, 0.30, 0.20),
    "video": (0.25, 0.25, 0.25, 0.25),
    "paper": (0.20, 0.30, 0.20, 0.30),
    "craftsperson": (0.25, 0.20, 0.35, 0.20),
    "scholar": (0.20, 0.30, 0.20, 0.30),
    "leader": (0.30, 0.15, 0.30, 0.25),
}

# (lower_bound L_k, upper_bound U_k, penalty_stiffness lambda_k)
# Includes Law L7 (Simplicity Favors Regularity) & Law L8 (First-Paint Simultaneous Cognitive Load Budget)
DEFAULT_SUBTRACTIVE_BUDGETS: dict[str, tuple[float | None, float | None, float]] = {
    "headerRows": (None, 1.0, 1.5),
    "headerItems": (None, 7.0, 1.0),
    "maxAnswerBullets": (None, 2.0, 1.2),
    "expandedTablesVisible": (None, 1.0, 1.5),
    "hudOcclusionRatio": (None, 0.25, 2.0),
    "horizontalOverflowPx": (None, 0.0, 2.0),
    "minTapTargetPx": (44.0, None, 1.5),
    "edgeDensity": (0.015, 0.36, 1.5),
    "primaryCtaCount": (None, 1.0, 1.2),
    "stepsToValue": (None, 4.0, 1.0),
    "distinctFontSizes": (None, 8.0, 1.5),
    "distinctBorderRadii": (None, 4.0, 1.2),
    "distinctGridTemplates": (None, 5.0, 1.2),
    "lexicalIrregularities": (None, 0.0, 2.0),
    "visibleInteractiveControls": (None, 24.0, 1.8),
    "visibleChoiceGroups": (None, 4.0, 1.5),
    "activeMacroRegions": (None, 3.0, 1.8),
    "chromeToContentRatio": (None, 0.65, 1.8),
    "metricMultiplicity": (None, 2.0, 1.5),
}


def raw_sigmoid(x: float, theta: float = DEFAULT_THETA, k: float = DEFAULT_K) -> float:
    val = float(x)
    if not math.isfinite(val) or not math.isfinite(float(theta)) or not math.isfinite(float(k)):
        raise ValueError("Sigmoid inputs must be finite real numbers")
    exponent = -float(k) * (val - float(theta))
    if exponent > 60.0:
        return 0.0
    if exponent < -60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(exponent))


def psi_barrier(
    x: float,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
    ceiling: float = DEFAULT_CEILING,
) -> float:
    """
    Continuous catastrophic barrier gating function Psi_norm(x).
    Normalized relative to `ceiling` (9.0) so that Psi_norm(x) == 1.0 for x >= 9.0
    while collapsing toward 0 when x < theta (7.0).
    """
    raw_x = raw_sigmoid(x, theta=theta, k=k)
    if ceiling <= theta:
        return raw_x
    raw_ceil = raw_sigmoid(ceiling, theta=theta, k=k)
    if raw_ceil <= 0.0:
        return raw_x
    return min(1.0, raw_x / raw_ceil)


def aggregate_tier_pair(
    a: float,
    b: float,
    w_a: float = 0.5,
    w_b: float = 0.5,
    theta: float = DEFAULT_THETA,
) -> float:
    """
    Non-compensatory intra-tier aggregation:
      - If either constituent dimension falls below the catastrophic survival threshold `theta` (7.0),
        returns `min(a, b)` directly so a P0 failure cannot be averaged above `theta` to escape barrier collapse.
      - Otherwise returns the weight-aware mean capped at `min(a, b) + 0.4`.
    """
    fa, fb = float(a), float(b)
    low = min(fa, fb)
    if low < float(theta):
        return low
    denom = max(1e-9, float(w_a) + float(w_b))
    w_mean = (float(w_a) * fa + float(w_b) * fb) / denom
    return min(w_mean, low + 0.4)


def compute_subtractive_penalty(
    metrics: dict[str, Any] | None = None,
    budgets: dict[str, tuple[float | None, float | None, float]] | None = None,
) -> dict[str, Any]:
    """
    Compute the Two-Sided Subtractive Cognitive Load Functional P_subtractive(m) in (0, 1] (Laws L0-L6):
      P_subtractive(m) = prod_{k in B} exp(-lambda_k * max(0, (m_k - U_k)/scale_U, (L_k - m_k)/scale_L)^2)
    """
    if not metrics:
        return {"p_subtractive": 1.0, "factors": {}, "violations": []}
    active_budgets = budgets or DEFAULT_SUBTRACTIVE_BUDGETS
    p_total = 1.0
    factors: dict[str, float] = {}
    violations: list[str] = []

    for key, (low_b, up_b, lam) in active_budgets.items():
        raw_v = metrics.get(key)
        if raw_v is None or isinstance(raw_v, bool):
            continue
        try:
            val = float(raw_v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue

        excess = 0.0
        if up_b is not None and val > up_b:
            scale_u = abs(up_b) if abs(up_b) > 1e-6 else 16.0
            excess = max(excess, (val - up_b) / scale_u)
            violations.append(f"{key}={val} > {up_b}")
        if low_b is not None and val < low_b:
            scale_l = abs(low_b) if abs(low_b) > 1e-6 else 1.0
            excess = max(excess, (low_b - val) / scale_l)
            violations.append(f"{key}={val} < {low_b}")

        factor = math.exp(-float(lam) * (excess * excess)) if excess > 0.0 else 1.0
        factors[key] = round(factor, 4)
        p_total *= factor

    return {
        "p_subtractive": round(max(0.0, min(1.0, p_total)), 4),
        "factors": factors,
        "violations": violations,
    }


def compute_kolmogorov_elegance(actual_complexity: float, essential_complexity: float) -> float:
    """Kolmogorov Existential Parsimony E(L) = exp(-max(0, (|L| - K(T)) / K(T))) in (0, 1]."""
    if not math.isfinite(float(actual_complexity)) or not math.isfinite(float(essential_complexity)):
        raise ValueError("Complexity inputs must be finite")
    k_t = max(1.0, float(essential_complexity))
    l_val = max(k_t, float(actual_complexity))
    return round(math.exp(-(l_val - k_t) / k_t), 4)


def compute_regularity_index(
    distinct_font_sizes: int = 0,
    distinct_border_radii: int = 0,
    distinct_grid_templates: int = 0,
    lexical_irregularities: int = 0,
) -> float:
    """
    Law L7 — Simplicity Favors Regularity (Η Απλότητα Ευνοεί την Κανονικότητα):
      R_kanon = prod_{m} exp(-gamma_m * max(0, (r_m - C_m) / max(C_m, 1))^2) in (0, 1].
    Penalizes ad-hoc micro-variance across typography scales, border radii, grid templates,
    and leaked internal tokens/markdown in user-facing surfaces.
    """
    sub = compute_subtractive_penalty(
        {
            "distinctFontSizes": distinct_font_sizes,
            "distinctBorderRadii": distinct_border_radii,
            "distinctGridTemplates": distinct_grid_templates,
            "lexicalIrregularities": lexical_irregularities,
        }
    )
    return float(sub["p_subtractive"])


def compute_simultaneous_load_index(
    visible_interactive_controls: int = 0,
    visible_choice_groups: int = 0,
    active_macro_regions: int = 0,
    chrome_to_content_ratio: float = 0.0,
    metric_multiplicity: int = 0,
) -> dict[str, Any]:
    """
    Law L8 — First-Paint Simultaneous Cognitive Load Budget (Ο Νόμος του Ταυτόχρονου Γνωστικού Φορτίου):
      H_hick = log2(N_ctrl + 1) bits (ceiling <= 4.64 bits / 24 controls, veto > 36 controls)
      P_load = prod_{k in L8} exp(-lambda_k * max(0, (m_k - U_k) / U_k)^2) in (0, 1]
    Prevents 'Regularized Maximalism' where individually regular widgets overload the first-paint viewport.
    """
    n_ctrl = max(0, int(visible_interactive_controls))
    h_hick = round(math.log2(n_ctrl + 1.0), 2)
    t_hick_ms = round(200.0 + 150.0 * h_hick, 1)
    sub = compute_subtractive_penalty(
        {
            "visibleInteractiveControls": n_ctrl,
            "visibleChoiceGroups": max(0, int(visible_choice_groups)),
            "activeMacroRegions": max(0, int(active_macro_regions)),
            "chromeToContentRatio": max(0.0, float(chrome_to_content_ratio)),
            "metricMultiplicity": max(0, int(metric_multiplicity)),
        }
    )
    p_load = float(sub["p_subtractive"])
    s_l8 = round(max(0.0, min(10.0, 10.0 * p_load)), 2)
    return {
        "visible_interactive_controls": n_ctrl,
        "hick_entropy_bits": h_hick,
        "hick_decision_ms": t_hick_ms,
        "visible_choice_groups": max(0, int(visible_choice_groups)),
        "active_macro_regions": max(0, int(active_macro_regions)),
        "chrome_to_content_ratio": round(max(0.0, float(chrome_to_content_ratio)), 3),
        "metric_multiplicity": max(0, int(metric_multiplicity)),
        "p_load": p_load,
        "s_l8": s_l8,
        "passed": p_load >= 1.0 and len(sub["violations"]) == 0,
        "violations": sub["violations"],
    }


def compute_snrc(meaningful_signals: float, noise_violations: float, epsilon: float = 1.0) -> float:
    """Signal-to-Noise Ratio of Consciousness (SNRC) = I_meaning / (H_noise + epsilon)."""
    if not math.isfinite(float(meaningful_signals)) or not math.isfinite(float(noise_violations)):
        raise ValueError("SNRC inputs must be finite")
    i_val = max(0.0, float(meaningful_signals))
    h_val = max(0.0, float(noise_violations))
    return round(i_val / (h_val + max(1e-6, float(epsilon))), 2)


def compute_goldwood_index(
    tier_scores: list[float] | tuple[float, ...],
    weights: list[float] | tuple[float, ...] | None = None,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
    conscience_clean: bool = True,
    subtractive_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute the Universal Goldwood Index G(S) = Lambda_conscience * (sum alpha_i S_i) * prod Psi_norm(S_i).
    Enforces the exact unrounded 3-clause Sovereign Predicate:
      PASS <=> raw_G(S) >= 9.0 and min_i(S_i) >= theta and Lambda_conscience == 1.
    """
    if not math.isfinite(float(theta)) or not math.isfinite(float(k)):
        raise ValueError("theta and k must be finite numbers")
    raw_inputs = [float(s) for s in tier_scores]
    if any(not math.isfinite(s) for s in raw_inputs):
        raise ValueError("tier_scores must contain only finite numbers")

    scores = [max(0.0, min(10.0, s)) for s in raw_inputs]
    n = len(scores)
    if n == 0:
        raise ValueError("tier_scores must contain at least one score")

    sub_res = compute_subtractive_penalty(subtractive_metrics)
    p_sub = float(sub_res["p_subtractive"])
    if n >= 2 and p_sub < 1.0:
        scores[1] = max(0.0, min(10.0, scores[1] * p_sub))

    if weights is None:
        w = [1.0 / n] * n
    else:
        if len(weights) != n:
            raise ValueError(f"Expected {n} weights, got {len(weights)}")
        w_floats = [float(x) for x in weights]
        if any(not math.isfinite(x) for x in w_floats):
            raise ValueError("weights must contain only finite numbers")
        if any(x < 0.0 for x in w_floats):
            raise ValueError("weights must be non-negative")
        w_sum = sum(w_floats)
        if w_sum <= 0.0:
            raise ValueError("sum of weights must be strictly positive")
        w = [x / w_sum for x in w_floats]

    linear_score = sum(w[i] * scores[i] for i in range(n))
    psi_values = [psi_barrier(s, theta=theta, k=k) for s in scores]
    raw_psi_values = [raw_sigmoid(s, theta=theta, k=k) for s in scores]
    barrier_product = 1.0
    for p in psi_values:
        barrier_product *= p

    conscience_lambda = 1 if conscience_clean else 0
    unmasked_g = linear_score * barrier_product
    raw_g = float(conscience_lambda) * unmasked_g
    min_score = min(scores)
    barrier_collapsed = min_score + 1e-9 < theta

    tier_names = ("S_sub", "S_perc", "S_act", "S_exist")
    jacobian_dg_ds: list[float] = []
    headroom_gains: list[float] = []
    for i in range(n):
        barrier_term = (k * (1.0 - raw_psi_values[i]) * linear_score) if scores[i] < DEFAULT_CEILING else 0.0
        dg_dsi = float(conscience_lambda) * barrier_product * (w[i] + barrier_term)
        jacobian_dg_ds.append(round(dg_dsi, 4))
        headroom_gains.append(round(max(0.0, 10.0 - scores[i]) * dg_dsi, 4))

    best_idx = max(range(n), key=lambda idx: (headroom_gains[idx], jacobian_dg_ds[idx]))
    highest_leverage_tier = tier_names[best_idx] if best_idx < len(tier_names) else f"S_{best_idx + 1}"

    if not conscience_clean:
        verdict = "FAIL (CONSCIENCE / CONSOLE HYGIENE VIOLATION: Lambda = 0)"
        status = "FAIL"
    elif barrier_collapsed:
        verdict = f"FAIL (CATASTROPHIC BARRIER COLLAPSE: min S_i = {min_score:.2f} < theta={theta:.1f})"
        status = "FAIL"
    elif raw_g + 1e-9 >= DEFAULT_THRESHOLD_G:
        verdict = "PASS (9+ GOLDWOOD SOVEREIGN STANDARD)"
        status = "PASS"
    elif raw_g + 1e-9 >= 8.0:
        verdict = "REVISE (8/10 BOURGEOIS COMPROMISE — BELOW 9.0 GATE)"
        status = "REVISE"
    else:
        verdict = "FAIL (BELOW 9.0 GOLDWOOD STANDARD)"
        status = "FAIL"

    return {
        "standard_version": GWI_STANDARD_VERSION,
        "psi_variant": "normalized_ceiling_9.0",
        "tier_scores": [round(s, 2) for s in scores],
        "weights": [round(x, 4) for x in w],
        "psi_values": [round(p, 4) for p in psi_values],
        "raw_psi_values": [round(p, 4) for p in raw_psi_values],
        "subtractive_penalty_p": p_sub,
        "subtractive_violations": sub_res["violations"],
        "jacobian_dG_dS": jacobian_dg_ds,
        "headroom_gain_dG": headroom_gains,
        "highest_leverage_tier": highest_leverage_tier,
        "barrier_product": round(barrier_product, 4),
        "linear_score": round(linear_score, 2),
        "unmasked_goldwood_g": round(unmasked_g, 2),
        "raw_goldwood_g": round(raw_g, 6),
        "goldwood_g": round(raw_g, 2),
        "min_tier_score": round(min_score, 2),
        "theta": theta,
        "k": k,
        "conscience_lambda": conscience_lambda,
        "barrier_collapsed": barrier_collapsed,
        "status": status,
        "verdict": verdict,
    }


def compute_coupled_goldwood_g(
    g_calc: dict[str, Any],
    chrysos_score: float,
    verga_score: float,
    veto_triggered: bool = False,
) -> float:
    """
    Compute the unified Bi-Polar Chrysos x Verga Coupled Goldwood Index G_coupled:
      P = min(prod Psi_norm(S_i), Psi_norm(Verga), Psi_norm(Chrysos))
      G_coupled = Lambda_conscience * min(G(S), sqrt(Chrysos * Verga) * P)
    Guarantees symmetric barrier gating across both poles (4 Verga + 4 Chrysos lenses)
    and never contradicts or exceeds a barrier-collapsed or conscience-failed G(S).
    """
    conscience_l = int(g_calc.get("conscience_lambda", 1))
    if conscience_l == 0:
        return 0.0
    theta = float(g_calc.get("theta", DEFAULT_THETA))
    k = float(g_calc.get("k", DEFAULT_K))
    barrier_prod = float(g_calc.get("barrier_product", 1.0))
    verga_psi = psi_barrier(verga_score, theta=theta, k=k)
    chrysos_psi = psi_barrier(chrysos_score, theta=theta, k=k)
    geom_mean = math.sqrt(max(0.0, float(chrysos_score)) * max(0.0, float(verga_score)))
    barrier_coupled = geom_mean * min(barrier_prod, verga_psi, chrysos_psi)
    raw_g = float(g_calc.get("raw_goldwood_g", barrier_coupled))
    coupled = min(raw_g, barrier_coupled)
    if veto_triggered:
        coupled = min(coupled, 6.80)
    return round(max(0.0, coupled), 2)


CANONICAL_GITLAB_GROUPS: frozenset[str] = frozenset(
    {
        "core",
        "platform",
        "apps",
        "spatial-xr",
        "services",
        "research-ai",
        "personal",
        "archive",
        "infrastructure",
    }
)

CANONICAL_WEDGE_ROUTES: tuple[str, ...] = ("/snap", "/moment", "?room=204", "/card")


def compute_canonical_anchor_l9_index(
    active_gitlab_groups: int = 9,
    unanchored_wedge_repos: int = 0,
    unanchored_subdomains: int = 0,
    has_canonical_passport: bool = True,
) -> dict[str, Any]:
    """
    Law L9 — Canonical Monorepo Anchor Over New-Repo & Subdomain Sprawl:
      - Canonical GitLab Pillar Groups <= 9
      - 0 standalone micro-repositories for single-route wedges (/snap, /moment, ?room=204, /card)
      - 0 unanchored standalone subdomains outside canonical product anchors
      - Verified .swm/passport.yaml or AGENTS.md canonical anchor metadata
    """
    exc_groups = max(0, int(active_gitlab_groups) - 9)
    exc_wedges = max(0, int(unanchored_wedge_repos))
    exc_subdomains = max(0, int(unanchored_subdomains))
    passport_pen = 0.0 if has_canonical_passport else 0.15

    r_l9 = round(
        math.exp(-(0.18 * exc_groups + 0.35 * exc_wedges + 0.25 * exc_subdomains + passport_pen)),
        4,
    )
    passed = exc_groups == 0 and exc_wedges == 0 and exc_subdomains == 0 and has_canonical_passport
    return {
        "active_gitlab_groups": int(active_gitlab_groups),
        "unanchored_wedge_repos": int(unanchored_wedge_repos),
        "unanchored_subdomains": int(unanchored_subdomains),
        "has_canonical_passport": bool(has_canonical_passport),
        "r_l9": r_l9,
        "passed": passed,
    }
