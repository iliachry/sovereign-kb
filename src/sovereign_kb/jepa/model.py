"""Unified Vault JEPA model: Pivoted-QR subspace, EMA target encoder, FiLM predictor, 2-step rollout."""

from __future__ import annotations

import array
import base64
import hashlib
import importlib.util
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .features import ACTIONS, JEPATransition, dot, l2_normalize

ACTION_TO_ID: dict[str, int] = {name: i for i, name in enumerate(ACTIONS)}


@dataclass
class JEPAConfig:
    """Hyperparameters for ``VaultJEPA``."""

    input_dim: int = 128
    proj_dim: int = 128
    latent_dim: int = 48
    hidden_dim: int = 64
    action_dim: int = 8
    epochs: int = 25
    lr: float = 0.03
    momentum: float = 0.9
    ema_tau: float = 0.96
    var_weight: float = 0.25
    cov_weight: float = 0.04
    nce_weight: float = 1.0
    nce_temp: float = 0.15
    rollout_weight: float = 0.25
    weight_decay: float = 1e-4
    batch_size: int = 32
    seed: int = 42
    backend: str = "stdlib"  # "stdlib" | "torch" | "auto"

    def effective_in_dim(self) -> int:
        if self.proj_dim > 0 and self.input_dim > self.proj_dim:
            return self.proj_dim
        return self.input_dim


def _init_matrix(rows: int, cols: int, std: float, rng: random.Random) -> list[list[float]]:
    return [[rng.gauss(0.0, std) for _ in range(cols)] for _ in range(rows)]


def _matvec(mat: list[list[float]], vec: list[float], bias: list[float] | None = None) -> list[float]:
    out = [0.0] * len(mat)
    for i, row in enumerate(mat):
        s = bias[i] if bias is not None else 0.0
        for w, x in zip(row, vec, strict=True):
            s += w * x
        out[i] = s
    return out


def _norm_with_stats(z: list[float]) -> tuple[list[float], float]:
    norm_sq = sum(v * v for v in z)
    norm = math.sqrt(norm_sq + 1e-12)
    inv = 1.0 / norm
    return [v * inv for v in z], inv


def _norm_grad_pullback(s: list[float], inv_norm: float, grad_s: list[float]) -> list[float]:
    """Exact Jacobian-vector product of ``s = z / ||z||``."""
    dot_gs = sum(g * sv for g, sv in zip(grad_s, s, strict=True))
    return [(g - sv * dot_gs) * inv_norm for g, sv in zip(grad_s, s, strict=True)]


def _build_projection_matrix(input_dim: int, proj_dim: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed + 7919)
    scale = 1.0 / math.sqrt(float(proj_dim))
    return [[rng.gauss(0.0, scale) for _ in range(input_dim)] for _ in range(proj_dim)]


def compute_collapse_metrics(
    latents: list[list[float]],
    pos_energies: list[float],
    neg_energies: list[float],
    latent_dim: int,
) -> dict[str, Any]:
    """Compute effective rank, per-dimension std, off-diagonal covariance, and energy margin."""
    n = len(latents)
    if n < 2:
        mean_pos = sum(pos_energies) / len(pos_energies) if pos_energies else 1.0
        mean_neg = sum(neg_energies) / len(neg_energies) if neg_energies else 1.0
        return {
            "effective_rank": float(latent_dim),
            "min_dim_std": 0.0,
            "mean_dim_std": 0.0,
            "mean_abs_offdiag_cov": 0.0,
            "mean_pos_energy": round(mean_pos, 6),
            "mean_neg_energy": round(mean_neg, 6),
            "energy_margin": round(mean_neg - mean_pos, 6),
            "collapsed": False,
        }

    means = [sum(latents[b][j] for b in range(n)) / n for j in range(latent_dim)]
    centered = [[latents[b][j] - means[j] for j in range(latent_dim)] for b in range(n)]
    variances = [sum(centered[b][j] ** 2 for b in range(n)) / n for j in range(latent_dim)]
    stds = [math.sqrt(v + 1e-12) for v in variances]

    total_var = sum(variances)
    if total_var <= 1e-12:
        eff_rank = 1.0
    else:
        entropy = 0.0
        for v in variances:
            p = v / total_var
            if p > 1e-12:
                entropy -= p * math.log(p)
        eff_rank = math.exp(entropy)

    offdiag_sum = 0.0
    offdiag_count = 0
    for i in range(latent_dim):
        for j in range(i + 1, latent_dim):
            cov_ij = sum(centered[b][i] * centered[b][j] for b in range(n)) / n
            offdiag_sum += abs(cov_ij)
            offdiag_count += 1
    mean_abs_offdiag = offdiag_sum / max(1, offdiag_count)

    mean_pos = sum(pos_energies) / len(pos_energies) if pos_energies else 1.0
    mean_neg = sum(neg_energies) / len(neg_energies) if neg_energies else 1.0
    margin = mean_neg - mean_pos
    collapsed = (eff_rank < max(2.0, 0.25 * latent_dim)) or (margin <= 0.0)

    return {
        "effective_rank": round(eff_rank, 4),
        "min_dim_std": round(min(stds), 6),
        "mean_dim_std": round(sum(stds) / latent_dim, 6),
        "mean_abs_offdiag_cov": round(mean_abs_offdiag, 6),
        "mean_pos_energy": round(mean_pos, 6),
        "mean_neg_energy": round(mean_neg, 6),
        "energy_margin": round(margin, 6),
        "collapsed": collapsed,
    }


def _encode_proj_b64(mat: list[list[float]]) -> tuple[str, list[int]]:
    rows = len(mat)
    cols = len(mat[0]) if rows else 0
    arr = array.array("f", (float(v) for row in mat for v in row))
    if sys.byteorder != "little":
        arr.byteswap()
    return base64.b64encode(arr.tobytes()).decode("ascii"), [rows, cols]


def _decode_proj_b64(b64: str, shape: list[int]) -> list[list[float]]:
    rows, cols = int(shape[0]), int(shape[1])
    raw = base64.b64decode(b64.encode("ascii"))
    arr = array.array("f")
    arr.frombytes(raw)
    if sys.byteorder != "little":
        arr.byteswap()
    return [list(arr[r * cols : (r + 1) * cols]) for r in range(rows)]


class VaultJEPA:
    """Action-conditioned Joint-Embedding Predictive Architecture for Markdown vaults."""

    def __init__(self, config: JEPAConfig) -> None:
        self.config = config
        in_d = config.effective_in_dim()
        d = config.latent_dim
        h = config.hidden_dim
        a_d = config.action_dim
        rng = random.Random(config.seed)

        self._proj_mat: list[list[float]] | None = (
            _build_projection_matrix(config.input_dim, in_d, config.seed)
            if (config.proj_dim > 0 and config.input_dim > config.proj_dim)
            else None
        )
        self._proj_fitted: bool = False

        # Online context encoder
        self.W_ctx = _init_matrix(d, in_d, math.sqrt(2.0 / (in_d + d)), rng)
        self.b_ctx = [0.0] * d

        # EMA target encoder (stop-gradient, initialized identical to context encoder)
        self.W_tgt = [list(row) for row in self.W_ctx]
        self.b_tgt = list(self.b_ctx)

        # Action embeddings
        self.action_emb = _init_matrix(len(ACTIONS), a_d, 0.25, rng)

        # FiLM-modulated residual MLP predictor:
        # u = W1s s_x + b1, m = 1 + tanh(W_gamma e_a), h = ReLU(m * u + W1a e_a), s_hat = norm(s_x + W2 h + b2)
        self.W1s = _init_matrix(h, d, math.sqrt(2.0 / (d + h)), rng)
        self.W1a = _init_matrix(h, a_d, math.sqrt(2.0 / (a_d + h)), rng)
        self.W_gamma = _init_matrix(h, a_d, 0.15, rng)
        self.b1 = [0.0] * h
        self.W2 = _init_matrix(d, h, 0.05, rng)
        self.b2 = [0.0] * d

    def fit_input_subspace(self, train_vectors: list[list[float]]) -> None:
        """Fit a data-adaptive orthonormal subspace via Pivoted Modified Gram-Schmidt."""
        if self.config.proj_dim <= 0 or self.config.input_dim <= self.config.proj_dim:
            return
        target_dim = self.config.proj_dim
        in_dim = self.config.input_dim

        # Deduplicate up to 256 unique vectors
        seen: set[tuple[float, ...]] = set()
        residuals: list[list[float]] = []
        for vec in train_vectors:
            if len(vec) != in_dim:
                continue
            sig = tuple(round(v, 5) for v in vec[:12])
            if sig in seen:
                continue
            seen.add(sig)
            residuals.append(list(vec))
            if len(residuals) >= 256:
                break

        basis: list[list[float]] = []
        for k in range(target_dim):
            best_idx = -1
            best_norm_sq = 0.0
            for idx, rvec in enumerate(residuals):
                nsq = sum(x * x for x in rvec)
                if nsq > best_norm_sq:
                    best_norm_sq = nsq
                    best_idx = idx

            if best_idx < 0 or best_norm_sq <= 1e-8:
                rng = random.Random(self.config.seed + 10007 + k)
                cand = [rng.gauss(0.0, 1.0) for _ in range(in_dim)]
            else:
                cand = list(residuals[best_idx])

            # Modified Gram-Schmidt (2 passes for numerical orthogonality)
            for _ in range(2):
                for q in basis:
                    proj = dot(cand, q)
                    for j in range(in_dim):
                        cand[j] -= proj * q[j]
            q_new = l2_normalize(cand)
            if sum(x * x for x in q_new) <= 1e-12:
                rng = random.Random(self.config.seed + 20011 + k)
                cand = [rng.gauss(0.0, 1.0) for _ in range(in_dim)]
                for q in basis:
                    proj = dot(cand, q)
                    for j in range(in_dim):
                        cand[j] -= proj * q[j]
                q_new = l2_normalize(cand)
            basis.append(q_new)

            # Deflate residuals
            for rvec in residuals:
                proj = dot(rvec, q_new)
                if abs(proj) > 1e-12:
                    for j in range(in_dim):
                        rvec[j] -= proj * q_new[j]

        self._proj_mat = basis
        self._proj_fitted = True

    def project_input(self, x: list[float]) -> list[float]:
        if self._proj_mat is None:
            return x
        return l2_normalize(_matvec(self._proj_mat, x))

    def encode_context(self, x: list[float]) -> list[float]:
        xp = self.project_input(x)
        s, _ = _norm_with_stats(_matvec(self.W_ctx, xp, self.b_ctx))
        return s

    def encode_target(self, y: list[float]) -> list[float]:
        yp = self.project_input(y)
        s, _ = _norm_with_stats(_matvec(self.W_tgt, yp, self.b_tgt))
        return s

    def _pred_fwd(self, s_x: list[float], a_idx: int) -> dict[str, Any]:
        """Forward pass of the FiLM-modulated residual predictor."""
        d = self.config.latent_dim
        h_d = self.config.hidden_dim
        a_d = self.config.action_dim
        ea = self.action_emb[a_idx]

        u_vec = [0.0] * h_d
        gamma_t = [0.0] * h_d
        m_vec = [0.0] * h_d
        h_vec = [0.0] * h_d
        for k in range(h_d):
            u_k = self.b1[k]
            w1s_k = self.W1s[k]
            for j in range(d):
                u_k += w1s_k[j] * s_x[j]
            u_vec[k] = u_k

            wg_k = self.W_gamma[k]
            w1a_k = self.W1a[k]
            raw_g = 0.0
            beta_k = 0.0
            for j in range(a_d):
                raw_g += wg_k[j] * ea[j]
                beta_k += w1a_k[j] * ea[j]
            gt_k = math.tanh(raw_g)
            m_k = 1.0 + gt_k
            gamma_t[k] = gt_k
            m_vec[k] = m_k
            z_h = m_k * u_k + beta_k
            h_vec[k] = z_h if z_h > 0.0 else 0.0

        z_hat = [self.b2[i] + s_x[i] for i in range(d)]
        for i in range(d):
            w2_i = self.W2[i]
            for k in range(h_d):
                z_hat[i] += w2_i[k] * h_vec[k]
        shat, inv_hat = _norm_with_stats(z_hat)
        return {
            "shat": shat,
            "inv_hat": inv_hat,
            "u": u_vec,
            "gamma_t": gamma_t,
            "m": m_vec,
            "h_vec": h_vec,
        }

    def _pred_bwd(
        self,
        s_x: list[float],
        a_idx: int,
        cache: dict[str, Any],
        g_shat: list[float],
        accs: dict[str, Any],
    ) -> list[float]:
        """Backward pass of the FiLM-modulated residual predictor; returns ``g_sx``."""
        d = self.config.latent_dim
        h_d = self.config.hidden_dim
        a_d = self.config.action_dim
        ea = self.action_emb[a_idx]
        shat = cache["shat"]
        inv_hat = cache["inv_hat"]
        u_vec = cache["u"]
        gamma_t = cache["gamma_t"]
        m_vec = cache["m"]
        h_vec = cache["h_vec"]

        g_zhat = _norm_grad_pullback(shat, inv_hat, g_shat)
        g_b2 = accs["g_b2"]
        g_W2 = accs["g_W2"]
        g_W1s = accs["g_W1s"]
        g_W1a = accs["g_W1a"]
        g_W_gamma = accs["g_W_gamma"]
        g_b1 = accs["g_b1"]
        ga_row = accs["g_action"][a_idx]

        for i in range(d):
            gz_i = g_zhat[i]
            g_b2[i] += gz_i
            gw2_i = g_W2[i]
            for k in range(h_d):
                gw2_i[k] += gz_i * h_vec[k]

        g_sx = list(g_zhat)
        for k in range(h_d):
            if h_vec[k] <= 0.0:
                continue
            gh_k = 0.0
            for i in range(d):
                gh_k += self.W2[i][k] * g_zhat[i]
            if gh_k == 0.0:
                continue

            m_k = m_vec[k]
            g_u_k = gh_k * m_k
            g_b1[k] += g_u_k
            w1s_k = self.W1s[k]
            gw1s_k = g_W1s[k]
            for j in range(d):
                gw1s_k[j] += g_u_k * s_x[j]
                g_sx[j] += g_u_k * w1s_k[j]

            gt_k = gamma_t[k]
            g_raw_g = gh_k * u_vec[k] * (1.0 - gt_k * gt_k)
            w1a_k = self.W1a[k]
            gw1a_k = g_W1a[k]
            wg_k = self.W_gamma[k]
            gwg_k = g_W_gamma[k]
            for j in range(a_d):
                gw1a_k[j] += gh_k * ea[j]
                gwg_k[j] += g_raw_g * ea[j]
                ga_row[j] += gh_k * w1a_k[j] + g_raw_g * wg_k[j]

        return g_sx

    def predict(self, s_x: list[float], action: str) -> list[float]:
        a_idx = ACTION_TO_ID.get(action, 0)
        return list(self._pred_fwd(s_x, a_idx)["shat"])

    @staticmethod
    def energy(s_hat: list[float], s_y: list[float]) -> float:
        """Cosine energy ``E(s_hat, s_y) = 1 - <s_hat, s_y>`` in ``[0, 2]``."""
        return 1.0 - dot(s_hat, s_y)

    def rollout(self, x0: list[float], action: str, steps: int = 1) -> list[list[float]]:
        """Encode ``x0`` and roll out ``steps`` latent transitions under ``action``."""
        s = self.encode_context(x0)
        states: list[list[float]] = []
        for _ in range(max(1, steps)):
            s = self.predict(s, action)
            states.append(s)
        return states

    def _build_two_hop_chains(
        self,
        transitions: list[JEPATransition],
        max_chains: int = 384,
    ) -> list[tuple[list[float], int, int, list[float], str]]:
        by_src: dict[str, list[JEPATransition]] = {}
        for tr in transitions:
            by_src.setdefault(tr.src_key, []).append(tr)

        chains: list[tuple[list[float], int, int, list[float], str]] = []
        for tr1 in transitions:
            for tr2 in by_src.get(tr1.tgt_key, ()):
                if tr1.src_path == tr2.tgt_path:
                    continue
                chains.append(
                    (
                        self.project_input(tr1.src_vec),
                        ACTION_TO_ID.get(tr1.action, 0),
                        ACTION_TO_ID.get(tr2.action, 0),
                        self.project_input(tr2.tgt_vec),
                        tr2.tgt_path,
                    )
                )
        if len(chains) > max_chains:
            rng = random.Random(self.config.seed + 313)
            rng.shuffle(chains)
            chains = chains[:max_chains]
        return chains

    def train(self, transitions: list[JEPATransition]) -> dict[str, Any]:
        """Train ``VaultJEPA`` on ``transitions`` using the configured backend."""
        if not transitions:
            raise ValueError("Cannot train VaultJEPA on empty transitions list")

        self.fit_input_subspace([tr.src_vec for tr in transitions] + [tr.tgt_vec for tr in transitions])

        backend = self.config.backend
        if backend == "auto":
            backend = "torch" if importlib.util.find_spec("torch") is not None else "stdlib"

        if backend == "torch":
            return self._train_torch(transitions)
        return self._train_stdlib(transitions)

    def _update_ema(self) -> None:
        tau = self.config.ema_tau
        one_minus = 1.0 - tau
        for i in range(self.config.latent_dim):
            self.b_tgt[i] = tau * self.b_tgt[i] + one_minus * self.b_ctx[i]
            row_t = self.W_tgt[i]
            row_c = self.W_ctx[i]
            for j in range(len(row_c)):
                row_t[j] = tau * row_t[j] + one_minus * row_c[j]

    def _zero_grad_accs(self, in_d: int, d: int, h_d: int, a_d: int) -> dict[str, Any]:
        return {
            "g_W_ctx": [[0.0] * in_d for _ in range(d)],
            "g_b_ctx": [0.0] * d,
            "g_action": [[0.0] * a_d for _ in range(len(ACTIONS))],
            "g_W1s": [[0.0] * d for _ in range(h_d)],
            "g_W1a": [[0.0] * a_d for _ in range(h_d)],
            "g_W_gamma": [[0.0] * a_d for _ in range(h_d)],
            "g_b1": [0.0] * h_d,
            "g_W2": [[0.0] * h_d for _ in range(d)],
            "g_b2": [0.0] * d,
        }

    def _train_stdlib(self, transitions: list[JEPATransition]) -> dict[str, Any]:
        cfg = self.config
        in_d = cfg.effective_in_dim()
        d = cfg.latent_dim
        h_d = cfg.hidden_dim
        a_d = cfg.action_dim
        rng = random.Random(cfg.seed)

        proj_pairs = [
            (
                self.project_input(tr.src_vec),
                ACTION_TO_ID.get(tr.action, 0),
                self.project_input(tr.tgt_vec),
                tr.tgt_path,
            )
            for tr in transitions
        ]
        chains = self._build_two_hop_chains(transitions) if cfg.rollout_weight > 0.0 else []

        vel = self._zero_grad_accs(in_d, d, h_d, a_d)
        gamma = 0.7 / math.sqrt(float(d))
        epoch_losses: list[float] = []

        for epoch in range(cfg.epochs):
            lr_t = cfg.lr * (0.15 + 0.85 * 0.5 * (1.0 + math.cos(math.pi * epoch / max(1, cfg.epochs - 1))))
            rng.shuffle(proj_pairs)
            total_loss = 0.0
            total_count = 0

            for start in range(0, len(proj_pairs), cfg.batch_size):
                batch = proj_pairs[start : start + cfg.batch_size]
                bsz = len(batch)
                if bsz == 0:
                    continue

                sx_batch: list[list[float]] = []
                inv_x_batch: list[float] = []
                sy_batch: list[list[float]] = []
                pred_caches: list[dict[str, Any]] = []
                shat_batch: list[list[float]] = []

                pred_loss_sum = 0.0
                for xp, a_idx, yp, _ in batch:
                    sx, inv_x = _norm_with_stats(_matvec(self.W_ctx, xp, self.b_ctx))
                    sy, _ = _norm_with_stats(_matvec(self.W_tgt, yp, self.b_tgt))
                    pc = self._pred_fwd(sx, a_idx)
                    shat = pc["shat"]

                    pred_loss_sum += 2.0 * (1.0 - dot(shat, sy))
                    sx_batch.append(sx)
                    inv_x_batch.append(inv_x)
                    sy_batch.append(sy)
                    pred_caches.append(pc)
                    shat_batch.append(shat)

                # In-batch InfoNCE contrastive energy
                nce_loss_sum = 0.0
                grad_shat_nce = [[0.0] * d for _ in range(bsz)]
                if cfg.nce_weight > 0.0 and bsz >= 2:
                    inv_temp = 1.0 / max(1e-4, cfg.nce_temp)
                    inv_bsz = 1.0 / bsz
                    tgt_keys = [item[3] for item in batch]
                    for b in range(bsz):
                        shat_b = shat_batch[b]
                        tb = tgt_keys[b]
                        valid_j = [j for j in range(bsz) if j == b or tgt_keys[j] != tb]
                        if len(valid_j) < 2:
                            continue
                        logits = [dot(shat_b, sy_batch[j]) * inv_temp for j in valid_j]
                        max_l = max(logits)
                        exps = [math.exp(lg - max_l) for lg in logits]
                        denom = sum(exps)
                        probs = [e / denom for e in exps]
                        pos_pos = valid_j.index(b)
                        nce_loss_sum += -cfg.nce_weight * math.log(max(1e-12, probs[pos_pos]))

                        scale = cfg.nce_weight * inv_bsz * inv_temp
                        g_b = grad_shat_nce[b]
                        sy_b = sy_batch[b]
                        for idx_j, j in enumerate(valid_j):
                            pj = probs[idx_j]
                            sy_j = sy_batch[j]
                            for m in range(d):
                                g_b[m] += pj * sy_j[m]
                        for m in range(d):
                            g_b[m] = scale * (g_b[m] - sy_b[m])

                # Sphere-scaled VICReg variance & covariance regularizers on sx_batch
                grad_sx_reg = [[0.0] * d for _ in range(bsz)]
                reg_loss = 0.0
                if bsz >= 2:
                    means = [sum(sx_batch[b][j] for b in range(bsz)) / bsz for j in range(d)]
                    centered = [[sx_batch[b][j] - means[j] for j in range(d)] for b in range(bsz)]
                    variances = [sum(centered[b][j] ** 2 for b in range(bsz)) / bsz for j in range(d)]
                    stds = [math.sqrt(v + 1e-6) for v in variances]

                    var_loss = 0.0
                    for j in range(d):
                        diff = gamma - stds[j]
                        if diff > 0.0:
                            var_loss += diff
                            coeff = -cfg.var_weight / (d * bsz * stds[j])
                            for b in range(bsz):
                                grad_sx_reg[b][j] += coeff * centered[b][j]
                    var_loss = cfg.var_weight * (var_loss / d)

                    cov_loss = 0.0
                    for i in range(d):
                        for j in range(i + 1, d):
                            cov_ij = sum(centered[b][i] * centered[b][j] for b in range(bsz)) / bsz
                            cov_loss += 2.0 * cov_ij * cov_ij
                            scale = (4.0 * cfg.cov_weight * cov_ij) / (d * bsz)
                            for b in range(bsz):
                                grad_sx_reg[b][i] += scale * centered[b][j]
                                grad_sx_reg[b][j] += scale * centered[b][i]
                    cov_loss = cfg.cov_weight * (cov_loss / d)
                    reg_loss = var_loss + cov_loss

                total_loss += pred_loss_sum + nce_loss_sum + reg_loss * bsz
                total_count += bsz

                accs = self._zero_grad_accs(in_d, d, h_d, a_d)
                inv_bsz = 1.0 / bsz
                for b in range(bsz):
                    xp, a_idx, _, _ = batch[b]
                    sx = sx_batch[b]
                    sy = sy_batch[b]
                    shat = shat_batch[b]
                    g_shat = [2.0 * inv_bsz * (shat[i] - sy[i]) + grad_shat_nce[b][i] for i in range(d)]
                    g_sx = self._pred_bwd(sx, a_idx, pred_caches[b], g_shat, accs)
                    for j in range(d):
                        g_sx[j] += grad_sx_reg[b][j]

                    g_zx = _norm_grad_pullback(sx, inv_x_batch[b], g_sx)
                    for i in range(d):
                        gz_i = g_zx[i]
                        accs["g_b_ctx"][i] += gz_i
                        gw_i = accs["g_W_ctx"][i]
                        for j in range(in_d):
                            gw_i[j] += gz_i * xp[j]

                self._apply_sgd_step(accs, vel, lr_t)
                self._update_ema()

            # 2-hop multi-step rollout consistency pass
            if chains:
                rng.shuffle(chains)
                for start in range(0, len(chains), cfg.batch_size):
                    c_batch = chains[start : start + cfg.batch_size]
                    cb = len(c_batch)
                    if cb == 0:
                        continue
                    accs = self._zero_grad_accs(in_d, d, h_d, a_d)
                    inv_cb = cfg.rollout_weight / cb
                    for xp0, a1_idx, a2_idx, xp2, _ in c_batch:
                        s0, inv_0 = _norm_with_stats(_matvec(self.W_ctx, xp0, self.b_ctx))
                        s2, _ = _norm_with_stats(_matvec(self.W_tgt, xp2, self.b_tgt))
                        c1 = self._pred_fwd(s0, a1_idx)
                        s1 = c1["shat"]
                        c2 = self._pred_fwd(s1, a2_idx)
                        s_hat2 = c2["shat"]
                        total_loss += cfg.rollout_weight * 2.0 * (1.0 - dot(s_hat2, s2))
                        total_count += 1

                        g_shat2 = [2.0 * inv_cb * (s_hat2[i] - s2[i]) for i in range(d)]
                        g_s1 = self._pred_bwd(s1, a2_idx, c2, g_shat2, accs)
                        g_s0 = self._pred_bwd(s0, a1_idx, c1, g_s1, accs)
                        g_z0 = _norm_grad_pullback(s0, inv_0, g_s0)
                        for i in range(d):
                            gz_i = g_z0[i]
                            accs["g_b_ctx"][i] += gz_i
                            gw_i = accs["g_W_ctx"][i]
                            for j in range(in_d):
                                gw_i[j] += gz_i * xp0[j]

                    self._apply_sgd_step(accs, vel, lr_t)
                    self._update_ema()

            epoch_losses.append(round(total_loss / max(1, total_count), 6))

        return {
            "backend": "stdlib",
            "epochs": cfg.epochs,
            "initial_loss": epoch_losses[0],
            "final_loss": epoch_losses[-1],
            "epoch_losses": epoch_losses,
            "two_hop_chains": len(chains),
            "subspace_fitted": self._proj_fitted,
        }

    def _apply_sgd_step(self, accs: dict[str, Any], vel: dict[str, Any], lr: float) -> None:
        sq = (
            sum(v * v for row in accs["g_W_ctx"] for v in row)
            + sum(v * v for v in accs["g_b_ctx"])
            + sum(v * v for row in accs["g_action"] for v in row)
            + sum(v * v for row in accs["g_W1s"] for v in row)
            + sum(v * v for row in accs["g_W1a"] for v in row)
            + sum(v * v for row in accs["g_W_gamma"] for v in row)
            + sum(v * v for v in accs["g_b1"])
            + sum(v * v for row in accs["g_W2"] for v in row)
            + sum(v * v for v in accs["g_b2"])
        )
        clip = 1.0
        if sq > 25.0:
            clip = 5.0 / math.sqrt(sq)

        mom = self.config.momentum
        wd = self.config.weight_decay

        for mat, gkey, vkey in (
            (self.W_ctx, "g_W_ctx", "g_W_ctx"),
            (self.action_emb, "g_action", "g_action"),
            (self.W1s, "g_W1s", "g_W1s"),
            (self.W1a, "g_W1a", "g_W1a"),
            (self.W_gamma, "g_W_gamma", "g_W_gamma"),
            (self.W2, "g_W2", "g_W2"),
        ):
            gmat = accs[gkey]
            vmat = vel[vkey]
            for i, row in enumerate(mat):
                grow = gmat[i]
                vrow = vmat[i]
                for j in range(len(row)):
                    vrow[j] = mom * vrow[j] + grow[j] * clip
                    row[j] -= lr * (vrow[j] + wd * row[j])

        for vec, gkey, vkey in (
            (self.b_ctx, "g_b_ctx", "g_b_ctx"),
            (self.b1, "g_b1", "g_b1"),
            (self.b2, "g_b2", "g_b2"),
        ):
            gvec = accs[gkey]
            vvec = vel[vkey]
            for i in range(len(vec)):
                vvec[i] = mom * vvec[i] + gvec[i] * clip
                vec[i] -= lr * vvec[i]

    def _train_torch(self, transitions: list[JEPATransition]) -> dict[str, Any]:
        import torch  # type: ignore[import-not-found]
        import torch.nn.functional as F  # type: ignore[import-not-found]

        cfg = self.config
        torch.manual_seed(cfg.seed)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        d = cfg.latent_dim
        gamma = 0.7 / math.sqrt(float(d))

        xs = torch.tensor([self.project_input(tr.src_vec) for tr in transitions], dtype=torch.float32, device=device)
        ys = torch.tensor([self.project_input(tr.tgt_vec) for tr in transitions], dtype=torch.float32, device=device)
        acts = torch.tensor([ACTION_TO_ID.get(tr.action, 0) for tr in transitions], dtype=torch.long, device=device)
        tgt_vocab = {p: idx for idx, p in enumerate(sorted({tr.tgt_path for tr in transitions}))}
        tgt_ids = torch.tensor([tgt_vocab[tr.tgt_path] for tr in transitions], dtype=torch.long, device=device)

        W_ctx = torch.tensor(self.W_ctx, dtype=torch.float32, device=device, requires_grad=True)
        b_ctx = torch.tensor(self.b_ctx, dtype=torch.float32, device=device, requires_grad=True)
        W_tgt = torch.tensor(self.W_tgt, dtype=torch.float32, device=device)
        b_tgt = torch.tensor(self.b_tgt, dtype=torch.float32, device=device)
        act_emb = torch.tensor(self.action_emb, dtype=torch.float32, device=device, requires_grad=True)
        W1s = torch.tensor(self.W1s, dtype=torch.float32, device=device, requires_grad=True)
        W1a = torch.tensor(self.W1a, dtype=torch.float32, device=device, requires_grad=True)
        W_gamma = torch.tensor(self.W_gamma, dtype=torch.float32, device=device, requires_grad=True)
        b1 = torch.tensor(self.b1, dtype=torch.float32, device=device, requires_grad=True)
        W2 = torch.tensor(self.W2, dtype=torch.float32, device=device, requires_grad=True)
        b2 = torch.tensor(self.b2, dtype=torch.float32, device=device, requires_grad=True)

        params = [W_ctx, b_ctx, act_emb, W1s, W1a, W_gamma, b1, W2, b2]
        opt = torch.optim.AdamW(params, lr=cfg.lr * 0.5, weight_decay=cfg.weight_decay)

        def _pred_torch(sx_t: Any, a_t: Any) -> Any:
            ea = act_emb[a_t]
            m = 1.0 + torch.tanh(ea @ W_gamma.t())
            u = sx_t @ W1s.t() + b1
            h_act = F.relu(m * u + ea @ W1a.t())
            return F.normalize(sx_t + h_act @ W2.t() + b2, dim=-1)

        chains = self._build_two_hop_chains(transitions) if cfg.rollout_weight > 0.0 else []
        c_x0 = torch.tensor([c[0] for c in chains], dtype=torch.float32, device=device) if chains else None
        c_a1 = torch.tensor([c[1] for c in chains], dtype=torch.long, device=device) if chains else None
        c_a2 = torch.tensor([c[2] for c in chains], dtype=torch.long, device=device) if chains else None
        c_x2 = torch.tensor([c[3] for c in chains], dtype=torch.float32, device=device) if chains else None

        epoch_losses: list[float] = []
        n = xs.shape[0]
        for epoch in range(cfg.epochs):
            lr_mult = 0.15 + 0.85 * 0.5 * (1.0 + math.cos(math.pi * epoch / max(1, cfg.epochs - 1)))
            for pg in opt.param_groups:
                pg["lr"] = cfg.lr * 0.5 * lr_mult

            perm = torch.randperm(n, device=device)
            ep_loss = 0.0
            ep_cnt = 0
            for start in range(0, n, cfg.batch_size):
                idx = perm[start : start + cfg.batch_size]
                xb, yb, ab, tb = xs[idx], ys[idx], acts[idx], tgt_ids[idx]
                sx = F.normalize(xb @ W_ctx.t() + b_ctx, dim=-1)
                with torch.no_grad():
                    sy = F.normalize(yb @ W_tgt.t() + b_tgt, dim=-1)
                shat = _pred_torch(sx, ab)
                pred_loss = ((shat - sy) ** 2).sum(dim=-1).mean()

                nce_loss = torch.tensor(0.0, device=device)
                if cfg.nce_weight > 0.0 and xb.shape[0] >= 2:
                    logits = (shat @ sy.t()) / max(1e-4, cfg.nce_temp)
                    bsz_t = xb.shape[0]
                    same_tgt = tb.unsqueeze(1) == tb.unsqueeze(0)
                    diag_mask = torch.eye(bsz_t, dtype=torch.bool, device=device)
                    logits = logits.masked_fill(same_tgt & (~diag_mask), -1e9)
                    labels = torch.arange(bsz_t, dtype=torch.long, device=device)
                    nce_loss = cfg.nce_weight * F.cross_entropy(logits, labels)

                reg = torch.tensor(0.0, device=device)
                if xb.shape[0] >= 2:
                    xc = sx - sx.mean(dim=0, keepdim=True)
                    std = torch.sqrt(xc.var(dim=0, unbiased=False) + 1e-6)
                    var_loss = cfg.var_weight * F.relu(gamma - std).mean()
                    cov = (xc.t() @ xc) / xb.shape[0]
                    cov.fill_diagonal_(0.0)
                    cov_loss = cfg.cov_weight * (cov**2).sum() / d
                    reg = var_loss + cov_loss

                loss = pred_loss + nce_loss + reg
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(params, 5.0)
                opt.step()

                with torch.no_grad():
                    W_tgt.mul_(cfg.ema_tau).add_(W_ctx, alpha=1.0 - cfg.ema_tau)
                    b_tgt.mul_(cfg.ema_tau).add_(b_ctx, alpha=1.0 - cfg.ema_tau)

                ep_loss += float(loss.item()) * xb.shape[0]
                ep_cnt += int(xb.shape[0])

            if c_x0 is not None and c_a1 is not None and c_a2 is not None and c_x2 is not None:
                cn = c_x0.shape[0]
                c_perm = torch.randperm(cn, device=device)
                for start in range(0, cn, cfg.batch_size):
                    c_idx = c_perm[start : start + cfg.batch_size]
                    s0 = F.normalize(c_x0[c_idx] @ W_ctx.t() + b_ctx, dim=-1)
                    with torch.no_grad():
                        s2 = F.normalize(c_x2[c_idx] @ W_tgt.t() + b_tgt, dim=-1)
                    s1 = _pred_torch(s0, c_a1[c_idx])
                    s_hat2 = _pred_torch(s1, c_a2[c_idx])
                    r_loss = cfg.rollout_weight * ((s_hat2 - s2) ** 2).sum(dim=-1).mean()
                    opt.zero_grad()
                    r_loss.backward()
                    torch.nn.utils.clip_grad_norm_(params, 5.0)
                    opt.step()
                    with torch.no_grad():
                        W_tgt.mul_(cfg.ema_tau).add_(W_ctx, alpha=1.0 - cfg.ema_tau)
                        b_tgt.mul_(cfg.ema_tau).add_(b_ctx, alpha=1.0 - cfg.ema_tau)
                    ep_loss += float(r_loss.item()) * int(c_idx.shape[0])
                    ep_cnt += int(c_idx.shape[0])

            epoch_losses.append(round(ep_loss / max(1, ep_cnt), 6))

        self.W_ctx = W_ctx.detach().cpu().tolist()
        self.b_ctx = b_ctx.detach().cpu().tolist()
        self.W_tgt = W_tgt.detach().cpu().tolist()
        self.b_tgt = b_tgt.detach().cpu().tolist()
        self.action_emb = act_emb.detach().cpu().tolist()
        self.W1s = W1s.detach().cpu().tolist()
        self.W1a = W1a.detach().cpu().tolist()
        self.W_gamma = W_gamma.detach().cpu().tolist()
        self.b1 = b1.detach().cpu().tolist()
        self.W2 = W2.detach().cpu().tolist()
        self.b2 = b2.detach().cpu().tolist()

        return {
            "backend": f"torch:{device.type}",
            "device": str(device),
            "epochs": cfg.epochs,
            "initial_loss": epoch_losses[0],
            "final_loss": epoch_losses[-1],
            "epoch_losses": epoch_losses,
            "two_hop_chains": len(chains),
            "subspace_fitted": self._proj_fitted,
        }

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format": "sovereign-kb-jepa-v1",
            "config": asdict(self.config),
            "actions": list(ACTIONS),
            "weights": {
                "W_ctx": self.W_ctx,
                "b_ctx": self.b_ctx,
                "W_tgt": self.W_tgt,
                "b_tgt": self.b_tgt,
                "action_emb": self.action_emb,
                "W1s": self.W1s,
                "W1a": self.W1a,
                "W_gamma": self.W_gamma,
                "b1": self.b1,
                "W2": self.W2,
                "b2": self.b2,
            },
        }
        if self._proj_fitted and self._proj_mat is not None:
            b64, shape = _encode_proj_b64(self._proj_mat)
            payload["proj_b64"] = b64
            payload["proj_shape"] = shape
        canonical = json.dumps(payload, sort_keys=True).encode("utf-8")
        payload["model_sha256"] = hashlib.sha256(canonical).hexdigest()[:16]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VaultJEPA:
        valid_fields = set(JEPAConfig.__dataclass_fields__.keys())
        raw_cfg = {k: v for k, v in data["config"].items() if k in valid_fields}
        cfg = JEPAConfig(**raw_cfg)
        model = cls(cfg)
        w = data["weights"]
        model.W_ctx = w["W_ctx"]
        model.b_ctx = w["b_ctx"]
        model.W_tgt = w["W_tgt"]
        model.b_tgt = w["b_tgt"]
        model.action_emb = w["action_emb"]
        model.W1s = w["W1s"]
        model.W1a = w["W1a"]
        if "W_gamma" in w:
            model.W_gamma = w["W_gamma"]
        else:
            model.W_gamma = [[0.0] * cfg.action_dim for _ in range(cfg.hidden_dim)]
        model.b1 = w["b1"]
        model.W2 = w["W2"]
        model.b2 = w["b2"]
        if "proj_b64" in data and "proj_shape" in data:
            model._proj_mat = _decode_proj_b64(data["proj_b64"], data["proj_shape"])
            model._proj_fitted = True
        return model

    def save(self, path: Path, metadata: dict[str, Any] | None = None) -> str:
        payload = self.to_dict()
        if metadata:
            payload["metadata"] = metadata
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(payload["model_sha256"])

    @classmethod
    def load(cls, path: Path) -> tuple[VaultJEPA, dict[str, Any]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data), data

    def export_evr(self, evr_path: Path, metadata: dict[str, Any] | None = None) -> Path:
        """Seal JEPA weight tensors and metadata into a zero-copy ``.evr`` capsule."""
        from sovereign_kb.evr.builder import EvrBuilder

        builder = EvrBuilder(
            title="Sovereign-KB Vault JEPA Checkpoint",
            author="Sovereign Organon (JEPA)",
            description="Action-conditioned Joint-Embedding Predictive Architecture checkpoint.",
            polyglot=False,
        )
        for name, mat in (
            ("W_ctx", self.W_ctx),
            ("W_tgt", self.W_tgt),
            ("action_emb", self.action_emb),
            ("W1s", self.W1s),
            ("W1a", self.W1a),
            ("W_gamma", self.W_gamma),
            ("W2", self.W2),
        ):
            flat = [float(v) for row in mat for v in row]
            builder.add_tensor(
                f"tensors/{name}.tensor",
                values=flat,
                shape=[len(mat), len(mat[0]) if mat else 0],
                dtype="f32",
            )
        state = {"config": asdict(self.config), "actions": list(ACTIONS)}
        if metadata:
            state["metadata"] = metadata
        builder.add_state("state/jepa_manifest.json", state)
        return builder.write(evr_path)
