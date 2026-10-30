# Recursive Self-Improvement (`skb rsi`), Soar 9.6 Kernel (`skb soar`), Rust Certification & Agent FinOps

`sovereign-kb` includes optional autonomous maintenance, cognitive reasoning, compiler verification, and multi-agent FinOps subsystems for advanced workflows.

---

## 1. Monotonically-Gated Recursive Self-Improvement (`skb rsi` / `skb evolve`)

`sovereign-kb` provides a closed-loop **3-Loop Recursive Self-Improvement (RSI) Engine** (`src/sovereign_kb/rsi.py`) governed by a strict **Lyapunov non-regression invariant** ($J(\theta_{t+1}, K_{t+1}) \ge J(\theta_t, K_t)$) and an append-only **SHA-256 hash-chained audit ledger** (`.kb/rsi-ledger.jsonl`):

```bash
# Execute a full 3-loop RSI cycle (Self-Heal + Episodic Consolidation + Policy Tuning + Ledger Proof)
skb rsi cycle --verify-rust
# Or use the shorthand alias:
skb evolve

# Inspect active retrieval policy (.kb/retrieval_policy.json), Lyapunov objective J, and SHA-256 ledger
skb rsi status
skb rsi ledger

# Run Loop 1 only: Coordinate-search meta-optimization of RRF & BM25 weights
skb tune

# Run Loop 2 only: Self-heal frontmatter/orphans & consolidate 04-episodic/*.md -> 02-operating-system/
skb consolidate
```

1. **Loop 1 — Retrieval Policy Meta-Optimization (`.kb/retrieval_policy.json`)**:
   - Evaluates a composite objective $J(\theta, K) = 0.45\,\text{MRR@5} + 0.30\,\text{NDCG@5} + 0.15\,\text{Recall@1} + 0.10\,\text{MeanMargin}$ across canonical multi-domain probes and held-out structural probes from the vault.
   - Optimizes `k_rrf`, `w_hips`, `w_fts`, `w_vec`, `exact_id_boost`, `graph_1hop_boost`, and column `bm25_weights`, persisting updates only when $J(\theta', K) \ge J(\theta_t, K)$ on both train and held-out splits.
2. **Loop 2 — Self-Healing & Episodic-to-Semantic Consolidation (`skb consolidate` / `skb verify --heal`)**:
   - Automatically repairs missing YAML frontmatter (Gate 1) and wires unlinked orphan notes into `INDEX.md` (Gate 3).
   - Distills timestamped entries from `04-episodic/*.md` into `02-operating-system/autonomous-rsi-distillations.md` (with atomic rollback if any of the 6 Vault Gates fail).
3. **Loop 3 — Compiler-Grounded Verification & Tamper-Evident SHA-256 Ledger (`.kb/rsi-ledger.jsonl`)**:
   - Couples retrieval verification with `rustc --edition 2021 --test -D warnings` (`--verify-rust`) and records every cycle in a cryptographically chained ledger verified by Gate 4 of `skb verify`.

---

## 2. Soar 9.6 Symbolic & Neuro-Symbolic Cognitive Kernel (`skb soar`)

`src/sovereign_kb/soar/` implements a pure-stdlib **Soar 9.6 Cognitive Kernel** with Rete production matching (`sp { ... }`), 5-Phase Decision Cycle, `i-support`/`o-support` Truth Maintenance System, 8-preference calculus, impasse subgoaling (`S1 -> S2`), Explanation-Based Chunking (`chunk*`), SQLite `ChunkStore`, and the `soarc` Python transpiler:

```bash
# Run the 4 canonical Soar benchmarks (Water Jugs, Blocks World, Towers of Hanoi, Vault Governance)
skb soar bench

# Extract and verify all ```soar production blocks inside your Markdown vault
skb soar verify

# Resolve an operator-tie impasse and persist learned procedural chunks (.kb/learned_chunks.soar)
skb soar resolve --benchmark water-jugs
```

---

## 3. 20-Domain Autonomous Rust Compiler Certification (`skb certify rust`)

`src/sovereign_kb/rust_cert.py` verifies 20 core Rust systems domains (`RUST-CERT-01..20`: ownership, borrowing, lifetimes, concurrency, atomics, pinning, typestate, and C-ABI) against `rustc --edition 2021 --test -D warnings`:

```bash
# Run the 20-domain Rust certification harness
skb certify rust --no-embed
```

---

## 4. Multi-Agent FinOps (`skb usage`) & Live Session Auditing (`skb sessions`)

```bash
# Audit recent 7-day multi-agent token usage, prompt-cache hit ratio, and API-equivalent USD spend
skb usage
skb finops --today --json
skb usage --native --no-gx10

# Inspect live agent sessions, background tasks, and deadlock states
skb sessions
skb chats
skb ps
skb sessions --clean --dry-run

# Git vault synchronization helpers
skb git-sync -m "docs: sync vault notes"
skb pull
skb push -m "docs: update notes"
```
