# Unified Vault JEPA (`skb jepa`)

`sovereign_kb.jepa` (`F-23`) implements an action-conditioned **Joint-Embedding Predictive Architecture (JEPA)** over Markdown knowledge vaults. Instead of predicting raw tokens, it learns to predict **abstract latent representations** of target notes and episodic transitions directly from context representations and transition actions.

---

## 1. Mathematical Formulation

Given a vault indexed in `.kb/index.db`, each document $x$ is represented either by the mean L2-normalized 768-d chunk embedding (when dense embeddings are present) or by a deterministic 128-d signed feature-hashed vector (offline zero-dependency fallback). When `input_dim > proj_dim`, **Pivoted Modified Gram-Schmidt** fits a data-adaptive orthonormal subspace $\Pi \in \mathbb{R}^{p \times D}$ ($\Pi \Pi^\top = I_p$) on the training split (`proj_b64`).

1. **Online Context Encoder** $f_\theta$:

   $$
   s_x = \frac{W_{\mathrm{ctx}} \tilde{x} + b_{\mathrm{ctx}}}{\|W_{\mathrm{ctx}} \tilde{x} + b_{\mathrm{ctx}}\|_2} \in \mathbb{S}^{d-1}
   $$

2. **EMA Target Encoder** $f_{\bar{\theta}}$ (stop-gradient, updated via $\bar{\theta} \leftarrow \tau \bar{\theta} + (1-\tau)\theta$):

   $$
   s_y = \mathrm{sg}\!\left(\frac{W_{\mathrm{tgt}} \tilde{y} + b_{\mathrm{tgt}}}{\|W_{\mathrm{tgt}} \tilde{y} + b_{\mathrm{tgt}}\|_2}\right) \in \mathbb{S}^{d-1}
   $$

3. **FiLM-Modulated Action-Conditioned Residual Predictor** $P_\phi(s_x, a)$ over $a \in \mathcal{A}$ (`WIKILINK`, `NEXT_SECTION`, `EPISODIC_NEXT`, `EPISODIC_TO_SEMANTIC`):

   $$
   \hat{s}_y = \mathrm{norm}\!\Big(s_x + W_2\,\mathrm{ReLU}\!\big((1 + \tanh(W_\gamma e_a)) \odot (W_{1s} s_x + b_1) + W_{1a} e_a\big) + b_2\Big)
   $$

4. **Energy, In-Batch InfoNCE, 2-Step Rollout & Sphere-Scaled VICReg Anti-Collapse Objective**:

   $$
   E(\hat{s}_y, s_y) = 1 - \langle \hat{s}_y, s_y \rangle \in [0, 2]
   $$

   $$
   \mathcal{L} = \frac{1}{B}\sum_{b=1}^B \|\hat{s}_{y,b} - s_{y,b}\|_2^2 + \lambda_{\mathrm{nce}}\,\mathcal{L}_{\mathrm{InfoNCE}}(\hat{s}_y, s_y; \tau_{\mathrm{nce}}) + \lambda_{\mathrm{roll}}\,\mathcal{L}_{2\mathrm{-hop}} + \frac{\lambda_{\mathrm{var}}}{d}\sum_{j=1}^d \max\!\left(0,\, \frac{0.7}{\sqrt{d}} - \sigma_j(s_x)\right) + \frac{\lambda_{\mathrm{cov}}}{d}\sum_{i \ne j} \mathrm{Cov}(s_x)_{ij}^2
   $$

   trained with Momentum SGD ($\beta=0.9$) and cosine-annealed learning rate in pure Python stdlib (or AdamW in the optional PyTorch backend).

---

## 2. CLI Reference (`skb jepa` & `skb search --jepa`)

```bash
# Train the Unified Vault JEPA and save .kb/jepa_model.json (optional .evr capsule)
skb jepa train
skb jepa train --epochs 25 --latent-dim 48 --evr jepa_checkpoint.evr

# Multi-step latent world-model rollout from a note or query
skb jepa predict "01-now/now.md" --action EPISODIC_NEXT --steps 2 -n 5

# Goal-conditioned latent beam search from a start note/query to a target note/query
skb jepa plan "04-episodic/2026-11-27-log.md" "02-operating-system/goldwood-index-gwi-standard.md" --steps 4 --beam 4

# 5th-channel latent search (or fuse directly into skb search)
skb jepa search "latent world model collapse" -n 5
skb search "latent world model collapse" --jepa --explain -n 5

# Discover unlinked (source -> target) knowledge-graph gaps with lowest predicted energy
skb jepa gaps -n 10

# Detect high-energy surprising / drifted transitions in the existing vault
skb jepa surprise -n 10

# Benchmark Raw Cosine vs Vault JEPA vs Hybrid RRF + 2-Hop Rollout on 20% held-out transitions
skb jepa bench

# Train Hierarchical Chunk <-> Document H-JEPA (.kb/hjepa_model.json)
skb jepa hjepa

# Neuro-Symbolic Soar <-> JEPA impasse resolution (System 1 energy + System 2 chunk* compilation)
skb jepa soar "Goldwood Index"

# Closed-loop Lyapunov-gated graph curation (dry-run by default; --apply verifies 6-Gate + J_after >= J_before)
skb jepa heal -n 3
skb jepa heal -n 3 --apply

# Polyglot AST Code-Graph JEPA (predicts symbol coupling & impacted symbols)
skb jepa code . VaultJEPA

# Inspect checkpoint SHA-256 and collapse diagnostics
skb jepa status
```

---

## 3. Architecture & Barbell Isolation

- **Zero Runtime Dependencies**: The default `--backend stdlib` runs purely on Python 3.10+ stdlib (`math`, `array`, `json`, `sqlite3`, `hashlib`, `random`) with analytical backpropagation. Passing `--backend torch` or `--backend auto` uses PyTorch when installed without adding any package dependency to `pyproject.toml`.
- **Barbell Isolation**: `jepa` is registered under `[tool.gwi.barbell].experimental` in `pyproject.toml` and is never imported by core GWI, retrieval, or RSI modules.
