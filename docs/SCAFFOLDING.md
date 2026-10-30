# Project & Scientific Paper Scaffolding (`skb scaffold`)

`sovereign-kb` includes a deterministic **7-Archetype Project & Paper Factory** (`skb scaffold` / `skb new`) that bootstraps new software repositories or LaTeX research manuscripts pre-wired with `AGENTS.md`, `docs/FEATURE_CATALOG.md`, and `./scripts/quality_gate.sh`.

```bash
# List all 7 available project and research archetypes
skb scaffold --list
skb new --list
```

---

## The 7 Canonical Archetypes

| Archetype ID | Target Stack & Use Case | Command |
| :--- | :--- | :--- |
| **`web-ts-3d-gis`** | Full-Stack Web, TypeScript, React 19 / Next.js / Vite, CesiumJS / Three.js, Vitest, Playwright, Knip | `skb scaffold web-ts-3d-gis ~/Projects/my-web-app` |
| **`python-uv-ai`** | Python 3.10+ (`uv`), FastAPI, Pydantic v2, Ruff, Pytest, SimPy / PyTorch / AI services | `skb scaffold python-uv-ai ~/Projects/my-ai-service` |
| **`rust-systems`** | Rust (`cargo`), strict Clippy (`-D warnings`), Criterion benchmarks, WGSL / WebGPU / Tokio | `skb scaffold rust-systems ~/Projects/my-rust-engine` |
| **`spatial-xr-ble`** | Three.js WebXR / Unity 6 / Meta Quest 3 + WebBluetooth / BLE GATT telemetry & Playwright video | `skb scaffold spatial-xr-ble ~/Projects/my-webxr-app` |
| **`paper-ieee-arxiv`** | IEEE / ACM / arXiv LaTeX manuscript + reproducible `sim/run_sim.py` $\to$ Type-42 PDF figures & BibTeX verifier | `skb scaffold paper-ieee-arxiv ~/Projects/my-paper --author "Your Name"` |
| **`game-sim-3d`** | Godot 4 (`--headless`), Bevy ECS (Rust), or Unity 6 (`-batchmode`) + deterministic simulation telemetry verifier | `skb scaffold game-sim-3d ~/Projects/my-3d-sim` |
| **`metatopia-chameleon`** | 2D App & 3D Spatial Experience Suite with 3-tier semantic design tokens (`tokens/metatopia-tokens.json`) | `skb scaffold metatopia-chameleon ~/Projects/my-studio-app` |

---

## What Gets Generated

1. **Software Archetypes (`web-ts-3d-gis`, `python-uv-ai`, `rust-systems`, `spatial-xr-ble`, `game-sim-3d`, `metatopia-chameleon`)**:
   - `AGENTS.md` — Multi-model worker/auditor operating guide and feature traceability matrix (`FEAT-01..05`).
   - `docs/FEATURE_CATALOG.md` — Structured feature-to-test mapping checked by `skb gwi audit`.
   - `scripts/quality_gate.sh` — Executable 4-stage deterministic verification gate (conflict/secret check, linter/typecheck, unit suite, and build/smoke verification).
2. **Scientific Paper Archetype (`paper-ieee-arxiv`)**:
   - `PAPER_SPEC.md` — SOTA Gap Matrix and 3-Check Formal Proof Gate (Dimensional Homogeneity, Asymptotic Limits, Lyapunov/Spectral Stability).
   - `sim/run_sim.py` & `scripts/plot_figures.py` — Pinned-seed (`seed=42`) simulation generating `results/metrics.json` and Type-42 vector PDF figures (`figures/*.pdf`).
   - `scripts/verify_bib.py` & `scripts/package_arxiv.py` — Zero-hallucination BibTeX citation checker and clean Overleaf/arXiv `.zip` bundler.
   - `AUDIT_LEDGER.md` & `paper.tex` — Ready-to-compile LaTeX manuscript.
