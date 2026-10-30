---
title: "Executable Project Archetypes & Scaffolding Engine (sovereign-kb scaffold)"
type: operating-system
updated: 2026-10-29
tags: [archetypes, scaffolding, autonomous-engineering, web-ts, python-uv, rust, spatial-xr, latex-paper, godot, bevy, unity, chameleon, goldwood]
related:
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
  - "[[02-operating-system/subtractive-design-laws]]"
  - "[[02-operating-system/lenses/README-lenses]]"
  - "[[02-operating-system/scientific-paper-framework]]"
  - "[[02-operating-system/rust-systems-reference]]"
  - "[[02-operating-system/templates/TEMPLATE-autonomous-project-AGENTS]]"
  - "[[02-operating-system/templates/TEMPLATE-scientific-paper-AGENTS]]"
  - "[[02-operating-system/codebase-governance-and-sizing]]"
  - "[[INDEX]]"
---

# Executable Project Archetypes & Scaffolding Engine (`sovereign-kb scaffold`)

To spin up high-grade, deterministic projects across seven technical domains without boilerplate friction, `sovereign-kb` (`skb`) includes a built-in project factory (`sovereign-kb scaffold` / `sovereign-kb new`).

## 1. Quick CLI Usage

```bash
# List all 7 supported engineering archetypes
sovereign-kb scaffold --list

# Initialize a new project with AGENTS.md, docs/FEATURE_CATALOG.md, scripts/quality_gate.sh, and .gitignore
sovereign-kb scaffold web-ts-3d-gis ~/Projects/my-new-twin --name "my-new-twin"
sovereign-kb scaffold python-uv-ai ~/Projects/my-ai-service
sovereign-kb scaffold rust-systems ~/Projects/my-rust-kernel
sovereign-kb scaffold spatial-xr-ble ~/Projects/my-xr-experience
sovereign-kb scaffold paper-ieee-arxiv ~/Projects/my-ieee-paper
sovereign-kb scaffold game-sim-3d ~/Projects/my-3d-sim
sovereign-kb scaffold metatopia-chameleon ~/Projects/my-chameleon-app
```

Every scaffolded repository comes pre-wired with:
1. **`AGENTS.md`**: Pre-configured with either the 6-Pillar [[02-operating-system/autonomous-engineering-framework|Deterministic Autonomous Engineering Framework]] (for software) or the 10-Pillar [[02-operating-system/scientific-paper-framework|Autonomous Scientific Paper Engineering Framework]] (for research manuscripts), 4-tier model routing, and centralized secret hygiene.
2. **`docs/FEATURE_CATALOG.md` / `PAPER_SPEC.md` & `AUDIT_LEDGER.md`**: 100% feature-to-test or claim-to-proof traceability registry.
3. **`scripts/quality_gate.sh`**: Executable (`chmod +x`) deterministic quality gate tailored to the archetype's runtime.
4. **`.gitignore`**: Stack-specific ignore rules preventing build/binary/secret leaks.

---

## 2. The Seven Canonical Stack Archetypes

### Archetype 1: `web-ts-3d-gis` — Full-Stack Web, 3D GIS & Digital Twins
- **Toolchain**: TypeScript, React 19 / Next.js 16 / Vite, CesiumJS / Three.js, `knip` (dead code), `eslint`, `vitest`, `playwright`.
- **4-Stage Gate (`scripts/quality_gate.sh`)**:
  1. Conflict marker & secret leak check.
  2. `npx knip && npm run lint && npm run typecheck`.
  3. `npm run test:run && npm run build`.
  4. Hermetic Playwright E2E + automated video walkthrough (`npm run record:walkthrough`) with zero-violation `.diagnostics.json`.

### Archetype 2: `python-uv-ai` — Python `uv`, FastAPI, Queueing Simulators & Sovereign AI
- **Toolchain**: Python 3.10+ managed via `uv`, FastAPI, Pydantic v2, `ruff` (lint + format), `pytest`, SimPy / PyTorch / OpenAI-compatible local inference.
- **4-Stage Gate (`scripts/quality_gate.sh`)**:
  1. Conflict marker & `.venv` exclusion hygiene.
  2. `uv run ruff check . && uv run ruff format --check .`.
  3. `PYTHONHASHSEED=42 uv run pytest -q` (seeded deterministic simulation & unit tests).
  4. Bytecode & contract smoke test (`uv run python -m compileall -q .`).

### Archetype 3: `rust-systems` — Rust Systems, Non-Euclidean Game Engine & Kernels
- **Canonical Reference**: [[02-operating-system/rust-systems-reference|Rust Systems, Ownership & Memory Safety Reference (RUST-CERT-01..20)]].
- **Toolchain**: Rust (`cargo`), `rustfmt`, `clippy -D warnings`, `criterion` benchmarks, WGSL / WebGPU / Tokio.
- **4-Stage Gate (`scripts/quality_gate.sh`)**:
  1. `cargo fmt --all -- --check`.
  2. `cargo clippy --all-targets --all-features -- -D warnings`.
  3. `cargo test --all-targets`.
  4. `cargo build --release`.

### Archetype 4: `spatial-xr-ble` — Spatial WebXR, Unity 6 & Hardware BLE Biometrics
- **Toolchain**: Three.js r128+ WebXR / Unity 6, Android BLE GATT Bridge (`0x180D`/`0x2A37`), FastAPI research telemetry, Headless CDP / Playwright video capture + contact sheets (`review:video`).
- **4-Stage Gate (`scripts/quality_gate.sh`)**:
  1. Merge marker & asset manifest verification.
  2. Static analysis & schema validation (`npm run lint && npm run typecheck`).
  3. Hermetic physics/ODE solver & mock BLE GATT unit suite (`npm run test:run`).
  4. Headless WebGL2 / WebXR walkthrough + contact-sheet UX review (`npm run record:walkthrough && npm run review:video`).

### Archetype 5: `paper-ieee-arxiv` — IEEE / ACM / arXiv LaTeX Manuscripts & Reproducible Sim Suites
- **Canonical Framework**: [[02-operating-system/scientific-paper-framework|The Autonomous Scientific Paper Engineering Framework]] & [[02-operating-system/templates/TEMPLATE-scientific-paper-AGENTS|Template: Scientific Paper AGENTS.md]].
- **Scaffolded Files**: `AGENTS.md`, `PAPER_SPEC.md`, `AUDIT_LEDGER.md`, `paper.tex` (`IEEEtran`), `references.bib`, `sim/run_sim.py` ($\to$ `results/metrics.json`), `scripts/plot_figures.py` (Type-42 vector PDFs + Okabe-Ito palette), `scripts/verify_bib.py`, `scripts/package_arxiv.py`, and `scripts/quality_gate.sh`.
- **6-Stage Gate (`scripts/quality_gate.sh`)**:
  1. Merge marker & tracked LaTeX build artifact hygiene.
  2. Deterministic simulation & vector PDF figure generation (`PYTHONHASHSEED=42 uv run python sim/run_sim.py && uv run python scripts/plot_figures.py`).
  3. Zero-hallucination bibliography & citation resolution (`uv run python scripts/verify_bib.py`).
  4. Clean PDF compilation (`latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex`).
  5. Zero undefined citations/references, zero `Overfull \hbox > 3.0pt`, and page-budget verification (`pdfinfo`).
  6. IEEE PDF eXpress font-embedding verification (`pdffonts` zero Type-3 / unembedded fonts) + Tri-Council `AUDIT_LEDGER.md` verification.

### Archetype 6: `game-sim-3d` — 3D Game Engines & Embodied AI Simulation (Godot 4 / Bevy ECS / Unity 6 Headless)
- **Toolchain**: Godot 4 (`godot --headless`), Bevy ECS (`cargo` / Vulkan / Wayland / X11 / Metal / DX12), Unity 6 (`-batchmode -nographics` / ML-Agents), Python `uv` simulation telemetry verifier.
- **Scaffolded Files**: `AGENTS.md`, `docs/FEATURE_CATALOG.md`, `project.godot`, `sim/verify_sim.py` ($\to$ `results/sim_telemetry.json`), `scripts/quality_gate.sh`, and `.gitignore`.
- **4-Stage Gate (`scripts/quality_gate.sh`)**:
  1. Merge marker & engine build-cache exclusion hygiene (`.godot/`, `target/`, `Library/`).
  2. Headless Godot 4 project & script verification (`godot --headless --quit`).
  3. Bevy / Rust ECS static analysis & test suite (`cargo clippy --all-targets -- -D warnings && cargo test`).
  4. Deterministic simulation trajectory & telemetry verification (`PYTHONHASHSEED=42 uv run python sim/verify_sim.py`).

### Archetype 7: `metatopia-chameleon` — Metatopia Chameleon 2D App & 3D Spatial Experience Suite (`goldwood` / `obsidian` / `cobalt` / `folio`)
- **Canonical Standard**: [[02-operating-system/goldwood-index-gwi-standard|The Unified Goldwood Index (G >= 9.0) & Global Watchability Index (GWI) Standard]].
- **Toolchain**: React 19, TypeScript 5, Vite 6, Three.js (WebGL2 & WebXR), 3-Tier Design Token Bridge (`tokens/metatopia-tokens.json`), `<dialog closedby="any">`, View Transitions, Vitest, Playwright 1080p Walkthrough Video, and GitLab/Cloudflare staging deployment.
- **Scaffolded Files**: `AGENTS.md`, `docs/FEATURE_CATALOG.md`, `tokens/metatopia-tokens.json`, `scripts/quality_gate.sh`, and `.gitignore`.
- **4-Stage Gate (`scripts/quality_gate.sh`)**:
  1. Merge conflict & `Math.random` ban hygiene across `src/`, `tests/`, `e2e/`, `tokens/`, `schemas/`.
  2. Strict TypeScript & unit test suite (`npm run typecheck && npm run test:run`).
  3. Production build & Goldwood ($\mathcal{G} \ge 9.0$) staging bundle (`npm run build:staging`).
  4. Hermetic Playwright E2E & 1080p video walkthrough (`node scripts/record_walkthrough.mjs`).

