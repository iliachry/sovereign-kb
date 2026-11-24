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

## 🪪 Identity, Palette & GWI Lens Scope

- **Names**: product and PyPI package `sovereign-kb` (v1.4.0, MIT, author Ilias Chrysovergis); CLI entrypoints `sovereign-kb` / `skb` (full CLI), `gwi` (quality gate), `organon` (4-pillar front door), `evr` (`.evr` format). The dashboard title is "Sovereign-KB Telemetry".
- **Palette (dashboard)**: light/dark via `light-dark()` tokens in `src/sovereign_kb/dashboard.py`; data colours use the Okabe-Ito colour-blind-safe set (`--oi-*`); status text uses the darkened `--ok-fg` / `--warn-fg` tokens (>= 4.5:1). Light is the default polarity; no sibling product to inherit from (Law L0 not applicable).
- **Active GWI lenses** (`gwi lenses .`): `ux-ergonomics` (the `skb dashboard` HTML UI), `principal-verification`, `science-integrity`. Not applicable: `technical-artist` (no 3D assets), `spatial-cinema` (no shipped video/audio), `typography-editor` (no LaTeX/PDF), `web-perf-edge` (no JS bundle or npm deps; inline dashboard JS only), `data-api-architect` (local SQLite only, no RLS/HTTP API contract), `ai-ml-science` (no model training; local embeddings are optional).
- **Browser evidence**: `uv run --with playwright==1.58.0 python scripts/audit_dashboard.py` writes `evidence/playwright/report.json` and `evidence/dashboard_3viewport_sheet.png` (gitignored). Re-run it after any `dashboard.py` change; `scripts/quality_gate.sh` Stage 6 refuses stale evidence.

---

## ⚡ Standard Verification Commands

```bash
./scripts/quality_gate.sh
QG_BROWSER=1 ./scripts/quality_gate.sh   # also regenerate dashboard browser evidence
```

---

## 🗺️ Master Traceability Registry (`F-01`–`F-22`)
- `F-01`: Core Architecture & Packaging (`sovereign-kb`, `skb`, `gwi`, `organon`, `evr`)
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
- `F-13`: Unified Goldwood Index & Global Watchability Index (`skb gwi` / `gwi`, `--execute-gate`, `GWI-APP-U05`)
- `F-14`: Soar 9.6 Symbolic & Neuro-Symbolic Cognitive Kernel (`skb soar`)
- `F-15`: Creative Mode Resumption Packet (`skb resume`)
- `F-16`: The Sovereign Organon CLI (`organon`)
- `F-17`: Pistis On-Chain Covenant, Merkle Provenance, Tokenomics & Aristocracy (`organon chain` / `skb chain`)
- `F-18`: The `.evr` ("Everything" v1.0) Universal Zero-Copy Polyglot File Format (`evr` / `skb evr` / `organon evr`)
- `F-19`: Frontier Humanity-to-Organon Capabilities (`skb code-graph`, `skb falsify`, `skb splat`, `skb rsi compile-prompts`, `skb rsi sleep-consolidate`)
- `F-20`: Elenchus Pre-Seal Cross-Lineage Challenge & Human Adjudication Gate (`skb elenchus`, `organon elenchus`, `evr seal-gwi --require-elenchus`)
- `F-21`: Installed-Copy Freshness (`skb verify` Gate 0, one stderr line per command while stale, `skb --version`)
- `F-22`: README Drift Gate (`readme_check.py`: pre-push hook and `organon finish`)

See [`docs/FEATURE_CATALOG.md`](docs/FEATURE_CATALOG.md) for full file and test mappings.
