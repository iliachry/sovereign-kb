# AGENTS.md — Master Platform Guide & Complete Feature Registry

**Repository:** `sovereign-kb`
**Archetype:** `python-uv-ai` — Python `uv` / SQLite FTS5 / Ollama / Autonomous Project Factory
**Primary Stack:** `Python 3.10+ (Pure Stdlib Runtime), SQLite FTS5, Ollama, Ruff, Pytest`
**Initialized:** `2026-10-22`

---

## 🧠 Persistent Project Memory (`skb`)
- Read foundational context at session start: `skb me`
- Search past architectural decisions before proposing changes: `skb search "<topic>" --json`
- Capture key technical decisions or root causes: `skb capture "<summary>"`

---

## 🎯 High-Level Agent Operating System

### 1. Mandatory Operating Rules
1. **100% Catalog / Spec Synchronization**: Every feature, command, or module must be registered in [`docs/FEATURE_CATALOG.md`](docs/FEATURE_CATALOG.md) with its exact implementation path and verification coverage.
2. **Parallel Multi-Model Code Review**: Run independent multi-model verification with a mechanical Git & test warm-start briefing before merging significant architectural changes.
3. **Zero Private IP or Secret Leaks**: As an open-source repository, `sovereign-kb` must contain zero hardcoded private IPs, local user paths, internal hostnames, or tokens (enforced by `test_zero_private_ip_or_secret_leaks`).
4. **Deterministic Quality Gate**: Never declare a task complete until `./scripts/quality_gate.sh` exits `0`.

---

## ⚡ Standard Verification Commands

```bash
./scripts/quality_gate.sh
```

---

## 🗺️ Master Traceability Registry (`F-01`–`F-14`)
- `F-01`: Core Architecture & Packaging (`sovereign-kb`, `skb`, `gwi`)
- `F-02`: OPSEC & Public Sanitization (`test_zero_private_ip_or_secret_leaks`)
- `F-03`: Unicode & FTS5 Compiler (`fold_accents`, `compile_fts_query`)
- `F-04`: Markdown Parser & Fence-Aware Chunker (`split_markdown_chunks`)
- `F-05`: Vault Initializer & 6-Gate Doctor (`skb init`, `skb sync`, `skb verify`)
- `F-06`: 4-Way Hybrid Retrieval (`hips` + FTS5 + Vector + 1-Hop Wikilink Graph)
- `F-07`: 7-Archetype Project Factory (`skb scaffold`)
- `F-08`: HIPS Block-Max WAND Engine (`skb hips`)
- `F-09`: Autonomous Rust Benchmark Suite (`skb certify rust`)
- `F-10`: Monotonically-Gated Recursive Self-Improvement (`skb rsi` / `evolve`)
- `F-11`: Agentic FinOps & Multi-Agent Token Telemetry (`skb usage` / `finops`)
- `F-12`: Zero-Dependency Local Observability Dashboard (`skb dashboard` / `ui`)
- `F-13`: Unified Goldwood Index & Global Watchability Index (`skb gwi` / `gwi`)
- `F-14`: Soar 9.6 Symbolic & Neuro-Symbolic Cognitive Kernel (`skb soar`)

See [`docs/FEATURE_CATALOG.md`](docs/FEATURE_CATALOG.md) for full file and test mappings.
