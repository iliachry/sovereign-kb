from __future__ import annotations

import math
import random
from typing import Any

# ==============================================================================
# 3. PHD VIRTUAL TOKEN ECONOMIES (`ai_tokenomics`) & ANTIFRAGILITY (`antifragility`)
# ==============================================================================


def single_agent_reward(price: float) -> float:
    """
    Single-agent pricing model (`ai_tokenomics/main.tex` Sec VII-A):
      D(p) = max(0, 20 - p),  C(D) = 0.5 * D^2.
    Over the discrete action space P = {1, ..., 10}, the maximum is at p* = 10 (R = 50.0);
    over the unrestricted continuous domain R_+, the global maximum is at p* = 40/3 ≈ 13.3333 (R = 66.6667).
    """
    d = max(0.0, 20.0 - float(price))
    return float(price) * d - 0.5 * (d**2)


def duopoly_payoffs(a1: float, a2: float) -> tuple[float, float]:
    """
    Two-agent competitive pricing game (`ai_tokenomics/main.tex` Sec VII-B):
      D_i(a_i, a_{-i}) = max(0, 10 - a_i + 0.5*a_{-i}),  C_i(D_i) = 0.2*D_i^2.
    Unique pure-strategy Nash Equilibrium over {1..10} is (8, 8) -> payoff 40.8;
    Collusive Pareto optimum over {1..10} is (10, 10) -> payoff 45.0.
    """
    d1 = max(0.0, 10.0 - float(a1) + 0.5 * float(a2))
    d2 = max(0.0, 10.0 - float(a2) + 0.5 * float(a1))
    r1 = float(a1) * d1 - 0.2 * (d1**2)
    r2 = float(a2) * d2 - 0.2 * (d2**2)
    return r1, r2


def simulate_egt_replicator_dynamics(
    steps: int = 1000,
    dt: float = 0.01,
) -> dict[str, Any]:
    """
    Simulate Evolutionary Game Theory (EGT) replicator dynamics (`ai_tokenomics` Theorem 3):
      dx_k/dt = x_k * (f_k(x) - f_bar(x))
    over discrete price strategies A = {1, ..., 10}.
    Verifies step-size stability `dt * max|f_k - f_bar| < 1` and convergence to the pure
    Evolutionarily Stable Strategy (ESS) at p* = 8 (payoff 40.8).
    """
    if steps < 1 or dt <= 0.0 or not math.isfinite(dt):
        raise ValueError("steps >= 1 and finite dt > 0 required")
    actions = list(range(1, 11))
    k_num = len(actions)
    payoff_matrix = [[duopoly_payoffs(a_i, a_j)[0] for a_j in actions] for a_i in actions]
    max_payoff_spread = max(max(r) for r in payoff_matrix) - min(min(r) for r in payoff_matrix)
    if dt * max_payoff_spread >= 1.0:
        raise ValueError(f"StepSizeTooLarge: dt={dt} * spread={max_payoff_spread} >= 1.0 violates positivity")

    x = [1.0 / k_num for _ in range(k_num)]
    for _ in range(steps):
        fitness = [sum(payoff_matrix[i][j] * x[j] for j in range(k_num)) for i in range(k_num)]
        avg_fitness = sum(x[i] * fitness[i] for i in range(k_num))
        x_next = [x[i] * (1.0 + dt * (fitness[i] - avg_fitness)) for i in range(k_num)]
        s_tot = sum(x_next)
        x = [v / s_tot for v in x_next]

    best_idx = max(range(k_num), key=lambda idx: x[idx])
    ess_price = actions[best_idx]
    ess_prob = x[best_idx]
    kl_lyapunov = -math.log(max(1e-12, ess_prob))
    return {
        "ess_price": ess_price,
        "ess_probability": round(ess_prob, 4),
        "ess_payoff": round(duopoly_payoffs(ess_price, ess_price)[0], 2),
        "kl_lyapunov_v": round(kl_lyapunov, 6),
        "converged_to_ne_8": ess_price == 8 and ess_prob > 0.90,
    }


def compute_folk_theorem_collusion_index(
    observed_price: float,
    ne_price: float = 8.0,
    collusive_price: float = 10.0,
) -> dict[str, Any]:
    """
    Quantify emergent Folk-Theorem tacit collusion (`ai_tokenomics` Sec V-A & VII-B):
      I_collusion = (p_obs - p_NE) / (p_collusive - p_NE)
    In `ai_tokenomics`, Nash Q-Learning converges to p = 8.7 +/- 1.1 -> I_collusion = 0.35.
    """
    for val, name in ((observed_price, "observed_price"), (ne_price, "ne_price"), (collusive_price, "collusive_price")):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
            raise ValueError(f"InvalidPrice: {name} must be a finite number")
    if float(collusive_price) <= float(ne_price):
        raise ValueError("collusive_price must be strictly greater than ne_price")

    denom = float(collusive_price) - float(ne_price)
    raw_idx = (float(observed_price) - float(ne_price)) / denom
    clipped_idx = max(0.0, min(1.0, raw_idx))
    regime = "COMPETITIVE_NE"
    if clipped_idx >= 0.75:
        regime = "CARTEL_COLLUSION"
    elif clipped_idx >= 0.20:
        regime = "SUPRA_COMPETITIVE_TACIT_COLLUSION"
    return {
        "observed_price": round(float(observed_price), 3),
        "ne_price": float(ne_price),
        "collusive_price": float(collusive_price),
        "collusion_index": round(clipped_idx, 4),
        "regime": regime,
        "model_source": "ai_tokenomics_table_2_nash_q_benchmark",
    }


def solve_discrete_lyapunov_2x2(
    a_cl: list[list[float]],
    q_mat: list[list[float]] | None = None,
    w_max: float = 0.05,
) -> dict[str, Any]:
    """
    Solve the discrete-time Lyapunov equation (`ai_tokenomics` Theorem 4):
      A_cl^T P A_cl - P = -Q
    exactly via the 3x3 symmetric linear system for `(p00, p01, p11)`, verify Schur stability
    (`rho(A_cl) < 1`), matrix symmetry/positive-definiteness of `Q` and `P`, exact Frobenius
    residual `||A_cl^T P A_cl - P + Q||_F < 1e-7`, and compute the invariant sublevel-set
    Euclidean ultimate boundedness radius for `||w_t|| <= w_max`.
    """
    q = q_mat or [[1.0, 0.0], [0.0, 1.0]]
    if len(a_cl) != 2 or len(q) != 2 or any(len(r) != 2 for r in a_cl + q):
        raise ValueError("InvalidMatrix: a_cl and q_mat must be 2x2 matrices")
    for r in a_cl + q:
        for v in r:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise ValueError("InvalidMatrix: matrix entries must be finite numbers")
    if isinstance(w_max, bool) or not isinstance(w_max, (int, float)) or not math.isfinite(float(w_max)) or w_max < 0:
        raise ValueError("InvalidDisturbanceBound: w_max must be a non-negative finite number")

    q00, q01 = float(q[0][0]), float(q[0][1])
    q10, q11 = float(q[1][0]), float(q[1][1])
    if abs(q01 - q10) > 1e-9:
        raise ValueError("InvalidMatrix: Q must be symmetric")
    det_q = q00 * q11 - q01 * q10
    if q00 <= 0.0 or det_q <= 0.0:
        raise ValueError("InvalidMatrix: Q must be positive definite")

    tr_q = q00 + q11
    disc_q = max(0.0, tr_q * tr_q - 4.0 * det_q)
    lambda_min_q = 0.5 * (tr_q - math.sqrt(disc_q))

    a00, a01 = float(a_cl[0][0]), float(a_cl[0][1])
    a10, a11 = float(a_cl[1][0]), float(a_cl[1][1])
    tr = a00 + a11
    det = a00 * a11 - a01 * a10
    disc = tr * tr - 4.0 * det
    if disc >= 0.0:
        sqrt_d = math.sqrt(disc)
        rho = max(abs(0.5 * (tr + sqrt_d)), abs(0.5 * (tr - sqrt_d)))
    else:
        rho = math.sqrt(max(0.0, det))

    if rho >= 1.0:
        return {
            "schur_stable": False,
            "spectral_radius": round(rho, 6),
            "positive_definite_p": False,
            "residual_frobenius": None,
            "ultimate_bound_sq": None,
        }

    # Exact 3x3 linear system M * [p00, p01, p11]^T = [-q00, -q01, -q11]^T
    m_aug = [
        [a00 * a00 - 1.0, 2.0 * a00 * a10, a10 * a10, -q00],
        [a00 * a01, a00 * a11 + a01 * a10 - 1.0, a10 * a11, -q01],
        [a01 * a01, 2.0 * a01 * a11, a11 * a11 - 1.0, -q11],
    ]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r_idx: abs(m_aug[r_idx][col]))
        if abs(m_aug[piv][col]) < 1e-14:
            return {
                "schur_stable": False,
                "spectral_radius": round(rho, 6),
                "positive_definite_p": False,
                "residual_frobenius": None,
                "ultimate_bound_sq": None,
            }
        m_aug[col], m_aug[piv] = m_aug[piv], m_aug[col]
        div = m_aug[col][col]
        for j in range(col, 4):
            m_aug[col][j] /= div
        for r_idx in range(3):
            if r_idx != col:
                fac = m_aug[r_idx][col]
                for j in range(col, 4):
                    m_aug[r_idx][j] -= fac * m_aug[col][j]

    p00, p01, p11 = m_aug[0][3], m_aug[1][3], m_aug[2][3]
    p_mat = [[p00, p01], [p01, p11]]

    def _matmul(m1: list[list[float]], m2: list[list[float]]) -> list[list[float]]:
        return [
            [
                m1[0][0] * m2[0][0] + m1[0][1] * m2[1][0],
                m1[0][0] * m2[0][1] + m1[0][1] * m2[1][1],
            ],
            [
                m1[1][0] * m2[0][0] + m1[1][1] * m2[1][0],
                m1[1][0] * m2[0][1] + m1[1][1] * m2[1][1],
            ],
        ]

    a_mat = [[a00, a01], [a10, a11]]
    at_mat = [[a00, a10], [a01, a11]]
    at_p = _matmul(at_mat, p_mat)
    at_p_a = _matmul(at_p, a_mat)
    q_clean = [[q00, q01], [q10, q11]]
    residual = math.sqrt(sum((at_p_a[i][j] - p_mat[i][j] + q_clean[i][j]) ** 2 for i in range(2) for j in range(2)))

    det_p = p00 * p11 - p01 * p01
    tr_p = p00 + p11
    disc_p = max(0.0, tr_p * tr_p - 4.0 * det_p)
    lambda_max_p = 0.5 * (tr_p + math.sqrt(disc_p))
    lambda_min_p = 0.5 * (tr_p - math.sqrt(disc_p))
    pd_ok = p00 > 0.0 and det_p > 0.0 and lambda_min_p > 0.0 and residual < 1e-7

    # Spectral norm ||A^T P||_2^2 = lambda_max((A^T P)^T (A^T P))
    gram = _matmul([[at_p[0][0], at_p[1][0]], [at_p[0][1], at_p[1][1]]], at_p)
    tr_g = gram[0][0] + gram[1][1]
    det_g = gram[0][0] * gram[1][1] - gram[0][1] * gram[1][0]
    norm_at_p_sq = 0.5 * (tr_g + math.sqrt(max(0.0, tr_g * tr_g - 4.0 * det_g)))

    # Rigorous invariant sublevel-set Euclidean ultimate bound:
    # Delta V <= -(lambda_min(Q)/2) ||x||^2 + ((2 ||A^T P||_2^2 / lambda_min(Q)) + lambda_max(P)) ||w||^2
    # => V_inv = (lambda_max(P) / (0.5 * lambda_min(Q))) * beta_w, and ||x||^2 <= V_inv / lambda_min(P)
    beta_w = ((2.0 * norm_at_p_sq / lambda_min_q) + lambda_max_p) * (float(w_max) ** 2)
    alpha_lyap = (0.5 * lambda_min_q) / max(1e-12, lambda_max_p)
    v_inv = beta_w / max(1e-12, alpha_lyap)
    ult_bound_sq = v_inv / max(1e-12, lambda_min_p)

    return {
        "schur_stable": True,
        "spectral_radius": round(rho, 6),
        "positive_definite_p": pd_ok,
        "residual_frobenius": round(residual, 12),
        "lambda_min_q": round(lambda_min_q, 6),
        "p_matrix": [[round(v, 5) for v in row] for row in p_mat],
        "ultimate_bound_sq": round(ult_bound_sq, 6),
    }


def simulate_pid_mpc_token_control(
    steps: int = 200,
    target_price: float = 1.0,
    noise_sigma: float = 0.05,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Simulate Uncontrolled vs. Causal PID vs. Causal Horizon-H Riccati LQR/MPC token supply
    governance on a SINGLE common 2D discrete-time plant (A, B) with identical shocks w_t:
      x_{t+1} = A x_t + B u_t + w_t,   x_t = [e_t, v_t]^T
    Derives the closed-loop matrix `A_cl = A - B * K_mpc` directly from `(A, B, K_mpc)` and
    certifies it via `solve_discrete_lyapunov_2x2`.
    """
    a_plant = [[0.88, 0.20], [0.05, 0.82]]
    b_plant = [1.0, 0.4]
    q_mpc = [[4.0, 0.0], [0.0, 1.0]]
    r_mpc = 0.08
    horizon_h = 15

    # Solve finite-horizon H=15 backward Riccati recursion on (a_plant, b_plant, q_mpc, r_mpc)
    p_ric = [row[:] for row in q_mpc]
    k_mpc = [0.0, 0.0]
    a_cl = [row[:] for row in a_plant]
    for _ in range(horizon_h):
        pb0 = p_ric[0][0] * b_plant[0] + p_ric[0][1] * b_plant[1]
        pb1 = p_ric[1][0] * b_plant[0] + p_ric[1][1] * b_plant[1]
        bt_p_b = b_plant[0] * pb0 + b_plant[1] * pb1

        pa00 = p_ric[0][0] * a_plant[0][0] + p_ric[0][1] * a_plant[1][0]
        pa01 = p_ric[0][0] * a_plant[0][1] + p_ric[0][1] * a_plant[1][1]
        pa10 = p_ric[1][0] * a_plant[0][0] + p_ric[1][1] * a_plant[1][0]
        pa11 = p_ric[1][0] * a_plant[0][1] + p_ric[1][1] * a_plant[1][1]
        bt_p_a = [b_plant[0] * pa00 + b_plant[1] * pa10, b_plant[0] * pa01 + b_plant[1] * pa11]

        k_mpc = [bt_p_a[0] / (r_mpc + bt_p_b), bt_p_a[1] / (r_mpc + bt_p_b)]
        a_cl = [
            [a_plant[0][0] - b_plant[0] * k_mpc[0], a_plant[0][1] - b_plant[0] * k_mpc[1]],
            [a_plant[1][0] - b_plant[1] * k_mpc[0], a_plant[1][1] - b_plant[1] * k_mpc[1]],
        ]
        p_next = [[0.0, 0.0], [0.0, 0.0]]
        for i in range(2):
            for j in range(2):
                s = q_mpc[i][j] + k_mpc[i] * r_mpc * k_mpc[j]
                for r_idx in range(2):
                    for c_idx in range(2):
                        s += a_cl[r_idx][i] * p_ric[r_idx][c_idx] * a_cl[c_idx][j]
                p_next[i][j] = s
        p_ric = p_next

    rng = random.Random(seed)
    amp_scale = float(noise_sigma) / 0.05 if noise_sigma > 0.0 else 0.0
    shocks = [
        [
            0.18 * amp_scale * math.sin(2.0 * math.pi * t / 30.0) + rng.gauss(0.0, noise_sigma),
            0.12 * amp_scale * math.cos(2.0 * math.pi * t / 25.0) + rng.gauss(0.0, 0.6 * noise_sigma),
        ]
        for t in range(steps)
    ]
    w_sup = max((math.hypot(w[0], w[1]) for w in shocks), default=0.0)

    # 1. Uncontrolled trajectory (u_t = 0) on (a_plant, b_plant)
    x_unc = [0.5, 0.3]
    sq_err_unc = 0.0
    for t in range(steps):
        sq_err_unc += x_unc[0] ** 2
        w = shocks[t]
        x_unc = [
            a_plant[0][0] * x_unc[0] + a_plant[0][1] * x_unc[1] + w[0],
            a_plant[1][0] * x_unc[0] + a_plant[1][1] * x_unc[1] + w[1],
        ]
    rmse_unc = math.sqrt(sq_err_unc / steps)

    # 2. Causal PID trajectory on exact same (a_plant, b_plant), observing only price error e_t = x[0]
    x_pid = [0.5, 0.3]
    integ = 0.0
    prev_err = 0.5
    kp, ki, kd = 0.45, 0.08, 0.05
    sq_err_pid = 0.0
    for t in range(steps):
        err = x_pid[0]
        sq_err_pid += err**2
        integ = 0.70 * integ + err
        deriv = err - prev_err
        prev_err = err
        u_pid = -(kp * err + ki * integ + kd * deriv)
        w = shocks[t]
        x_pid = [
            a_plant[0][0] * x_pid[0] + a_plant[0][1] * x_pid[1] + b_plant[0] * u_pid + w[0],
            a_plant[1][0] * x_pid[0] + a_plant[1][1] * x_pid[1] + b_plant[1] * u_pid + w[1],
        ]
    rmse_pid = math.sqrt(sq_err_pid / steps)

    # 3. Causal Riccati LQR/MPC trajectory on exact same (a_plant, b_plant): u_t = -K_mpc * x_t
    x_mpc = [0.5, 0.3]
    sq_err_mpc = 0.0
    for t in range(steps):
        sq_err_mpc += x_mpc[0] ** 2
        u_mpc = -(k_mpc[0] * x_mpc[0] + k_mpc[1] * x_mpc[1])
        w = shocks[t]
        x_mpc = [
            a_plant[0][0] * x_mpc[0] + a_plant[0][1] * x_mpc[1] + b_plant[0] * u_mpc + w[0],
            a_plant[1][0] * x_mpc[0] + a_plant[1][1] * x_mpc[1] + b_plant[1] * u_mpc + w[1],
        ]
    rmse_mpc = math.sqrt(sq_err_mpc / steps)

    # Theorem 4 Closed-Loop Lyapunov Certificate for the exact derived A_cl = A - B * K_mpc
    lyapunov_cert = solve_discrete_lyapunov_2x2(
        a_cl=a_cl,
        w_max=w_sup,
    )

    return {
        "steps": steps,
        "target_price": target_price,
        "plant_a": a_plant,
        "plant_b": b_plant,
        "mpc_gain_k": [round(k_mpc[0], 5), round(k_mpc[1], 5)],
        "derived_a_cl": [[round(v, 5) for v in row] for row in a_cl],
        "rmse_uncontrolled": round(rmse_unc, 4),
        "rmse_pid": round(rmse_pid, 4),
        "rmse_mpc": round(rmse_mpc, 4),
        "mpc_volatility_reduction_x": round(rmse_unc / max(1e-9, rmse_mpc), 2),
        "lyapunov_certificate": lyapunov_cert,
    }


def evaluate_antifragile_curving_and_ami(
    p_tx: float = 1.0,
    noise_n: float = 0.1,
    bandwidth_b: float = 1.0,
    k_p: float = 5.0,
    k_b: float = 1.2,
    stressor_j: float = 2.0,
    vag: float = 0.84,
    alc: float = 0.79,
    srr: float = 0.88,
) -> dict[str, Any]:
    """
    Evaluate PhD Antifragility Theorems 1 & 2 (`antifragility/main.tex`) and the
    Antifragile Market Index (`AMI(t) = alpha*VAG + beta*ALC + gamma*SRR`):
      - Power Adaptation: C_p''(J) > 0 for all J >= 0 iff 0 <= k_p < P/N = gamma_0.
      - Bandwidth Adaptation: tripartite thresholds f_min = 2B/(P+2N), f_max = B(P+2N)/(2N(P+N)).
        At equality `k_b == f_min`, curvature remains strictly positive for all finite J >= 0
        (`UNIVERSAL_ANTIFRAGILE`, critical_stressor_jc = "infinity").
    """
    if p_tx <= 0 or noise_n <= 0 or bandwidth_b <= 0 or stressor_j < 0:
        raise ValueError("p_tx > 0, noise_n > 0, bandwidth_b > 0, stressor_j >= 0 required")
    gamma_0 = p_tx / noise_n
    j = float(stressor_j)
    denom_p = math.log(2.0) * ((noise_n + j) ** 2) * ((noise_n + j + p_tx + k_p * j) ** 2)
    bracket_p = 2.0 * (noise_n + j + p_tx + k_p * j) - (p_tx - k_p * noise_n)
    c_p_second_deriv = (bandwidth_b * (p_tx - k_p * noise_n) * bracket_p) / denom_p
    power_universally_antifragile = (0.0 <= k_p < gamma_0) and (c_p_second_deriv > 0.0)

    f_min = (2.0 * bandwidth_b) / (p_tx + 2.0 * noise_n)
    f_max = (bandwidth_b * (p_tx + 2.0 * noise_n)) / (2.0 * noise_n * (p_tx + noise_n))
    den_jc = k_b * (p_tx + 2.0 * noise_n) - 2.0 * bandwidth_b
    if 0.0 <= k_b <= f_min or den_jc <= 1e-12:
        bw_regime = "UNIVERSAL_ANTIFRAGILE"
        j_critical: float | str = "infinity"
    elif f_min < k_b < f_max:
        bw_regime = "CONDITIONAL_ANTIFRAGILE"
        num_jc = bandwidth_b * (p_tx + 2.0 * noise_n) - k_b * (2.0 * p_tx * noise_n + 2.0 * (noise_n**2))
        j_critical = round(num_jc / den_jc, 4)
    else:
        bw_regime = "FRAGILE"
        j_critical = 0.0

    # Antifragile Market Index AMI(t) = 0.4*VAG + 0.35*ALC + 0.25*SRR
    ami_score = 0.40 * float(vag) + 0.35 * float(alc) + 0.25 * float(srr)
    return {
        "initial_snr_gamma0": round(gamma_0, 4),
        "power_adaptation": {
            "k_p": k_p,
            "threshold_gamma0": round(gamma_0, 4),
            "second_derivative_c_pp": round(c_p_second_deriv, 6),
            "universally_antifragile": power_universally_antifragile,
        },
        "bandwidth_adaptation": {
            "k_b": k_b,
            "f_min": round(f_min, 4),
            "f_max": round(f_max, 4),
            "regime": bw_regime,
            "critical_stressor_jc": j_critical,
        },
        "antifragile_market_index": {
            "vag": round(vag, 4),
            "alc": round(alc, 4),
            "srr": round(srr, 4),
            "ami_score": round(ami_score, 4),
            "antifragile_verified": ami_score >= 0.75 and power_universally_antifragile,
        },
    }


def run_tokenomics_suite() -> dict[str, Any]:
    """Run the complete PhD `ai_tokenomics` + `antifragility` verification suite."""
    action_grid = list(range(1, 11))
    best_discrete_p = max(action_grid, key=single_agent_reward)
    continuous_opt_p = 40.0 / 3.0

    # Verify unique pure-strategy Nash equilibrium over action_grid x action_grid
    ne_candidates: list[int] = []
    for p_star in action_grid:
        best_r = max(duopoly_payoffs(a_i, p_star)[0] for a_i in action_grid)
        if abs(duopoly_payoffs(p_star, p_star)[0] - best_r) < 1e-9:
            ne_candidates.append(p_star)
    ne_p = ne_candidates[0] if ne_candidates else 8

    egt = simulate_egt_replicator_dynamics()
    collusion_nash_q = compute_folk_theorem_collusion_index(observed_price=8.7)
    collusion_ess = compute_folk_theorem_collusion_index(observed_price=float(egt["ess_price"]))
    control = simulate_pid_mpc_token_control()
    antifragile = evaluate_antifragile_curving_and_ami()
    return {
        "single_agent_monopoly_optimum": {
            "action_space": "P = {1..10}",
            "optimal_price": best_discrete_p,
            "max_reward": round(single_agent_reward(best_discrete_p), 4),
            "unrestricted_continuous_price": round(continuous_opt_p, 4),
            "unrestricted_continuous_reward": round(single_agent_reward(continuous_opt_p), 4),
        },
        "duopoly_nash_equilibrium": {
            "ne_price": ne_p,
            "ne_payoff": round(duopoly_payoffs(ne_p, ne_p)[0], 4),
            "verified_unique_on_grid": ne_candidates == [8],
        },
        "egt_replicator": egt,
        "nash_q_collusion_audit": collusion_nash_q,
        "egt_ess_collusion_audit": collusion_ess,
        "pid_mpc_control": control,
        "antifragility": antifragile,
    }
