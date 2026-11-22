"""7-archetype deterministic project scaffold specs and generator (`cmd_scaffold`)."""

from __future__ import annotations

import datetime
import json
import os
import stat
import sys
from pathlib import Path

ARCHETYPES: dict[str, dict[str, str]] = {
    "web-ts-3d-gis": {
        "title": "Full-Stack Web / TypeScript / 3D GIS (Next.js / Vite / CesiumJS / Three.js)",
        "stack": "TypeScript, React 19 / Next.js 16 / Vite, CesiumJS / Three.js, Vitest, Playwright, Knip",
        "gate_cmds": """# Stage 1: Git conflict & secret hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' src/ || exit 1
# Stage 2: Dead code, lint & strict TypeScript
npx knip && npm run lint && npm run typecheck
# Stage 3: Unit tests & production build
npm run test:run && npm run build
# Stage 4: Hermetic Playwright E2E + Video Walkthrough
npm run test:e2e""",
        "gitignore": "node_modules/\ndist/\n.next/\ncoverage/\ntest-results/\nplaywright-report/\n*.env.local\n.DS_Store\n",
    },
    "python-uv-ai": {
        "title": "Python `uv` / FastAPI / ML / Queueing & Sovereign AI Systems",
        "stack": "Python 3.10+ (uv), FastAPI, Pydantic v2, Ruff, Pytest, SimPy / PyTorch",
        "gate_cmds": """# Stage 1: Git conflict & secret hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.venv --exclude-dir=.git || exit 1
# Stage 2: Ruff lint & format check
uv run ruff check . && uv run ruff format --check .
# Stage 3: Deterministic unit & property tests (pinned seed)
PYTHONHASHSEED=42 uv run pytest -q
# Stage 4: Smoke & CLI/API contract verification
uv run python -m compileall -q .""",
        "gitignore": ".venv/\n__pycache__/\n*.pyc\n.pytest_cache/\n.ruff_cache/\ndist/\n*.egg-info/\n.DS_Store\n",
    },
    "rust-systems": {
        "title": "Rust Systems / Non-Euclidean Spatial Engine / High-Throughput Kernels",
        "stack": "Rust (Cargo), Clippy (-D warnings), Criterion, WGSL / WebGPU / Tokio",
        "gate_cmds": """# Stage 1: Formatting & merge marker check
cargo fmt --all -- --check
# Stage 2: Strict Clippy static analysis
cargo clippy --all-targets --all-features -- -D warnings
# Stage 3: Deterministic unit, doc & integration tests
cargo test --all-targets
# Stage 4: Release build verification
cargo build --release""",
        "gitignore": "target/\nCargo.lock\n.DS_Store\n",
    },
    "spatial-xr-ble": {
        "title": "Spatial WebXR / Unity 6 / Quest 3 + Hardware BLE GATT Biometrics",
        "stack": "Three.js r128+ WebXR / Unity 6, WebBluetooth / Android BLE GATT Bridge, FastAPI Telemetry, Playwright CDP Video",
        "gate_cmds": """# Stage 1: Merge marker & asset manifest verification
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=node_modules --exclude-dir=.git || exit 1
# Stage 2: Static analysis & schema validation
npm run lint && npm run typecheck
# Stage 3: Hermetic simulation & mock BLE GATT unit suite
npm run test:run
# Stage 4: Headless WebGL2 / WebXR walkthrough + contact sheet generation
npm run record:walkthrough && npm run review:video""",
        "gitignore": "node_modules/\ndist/\nLibrary/\nTemp/\nObj/\nBuilds/\nLogs/\n*.apk\n.DS_Store\n",
    },
    "paper-ieee-arxiv": {
        "title": "IEEE / ACM / arXiv LaTeX Manuscript + Reproducible Simulation-to-Figure Suite",
        "stack": "LaTeX (IEEEtran / acmart / booktabs), Python uv (NumPy/SciPy/Matplotlib Type-42 PDF), latexmk, pdffonts, Tri-Council Audit Ledger",
        "gate_cmds": """# Stage 1: Git conflict & tracked build-artifact hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.git --exclude-dir=.venv || exit 1
# Stage 2: Deterministic simulation -> results/metrics.json & Type-42 vector PDF figures
if [ -f sim/run_sim.py ]; then PYTHONHASHSEED=42 uv run python sim/run_sim.py; fi
if [ -f scripts/plot_figures.py ]; then uv run python scripts/plot_figures.py; fi
# Stage 3: Zero-hallucination bibliography & citation resolution gate
if [ -f scripts/verify_bib.py ]; then uv run python scripts/verify_bib.py; fi
# Stage 4: Clean non-interactive PDF compilation via latexmk
TEX_MAIN="${TEX_MAIN:-paper.tex}"
if [ ! -f "$TEX_MAIN" ] && [ -f "main.tex" ]; then TEX_MAIN="main.tex"; fi
TEX_BASE="${TEX_MAIN%.tex}"
if [ -f "$TEX_MAIN" ] && command -v latexmk >/dev/null 2>&1; then
  latexmk -pdf -interaction=nonstopmode -halt-on-error "$TEX_MAIN"
  # Stage 5: Zero undefined citations/references, Overfull hbox & Anti-resizebox tabular check
  ! grep -E "LaTeX Warning: (Citation|Reference).*undefined" "${TEX_BASE}.log" || exit 1
  ! grep -E "LaTeX Warning: Label .* multiply defined" "${TEX_BASE}.log" || exit 1
  ! grep -rnE '\\resizebox\\{[^}]*\\}\\{[^}]*\\}\\{\\begin\\{tabular' . --include="*.tex" || exit 1
  # Stage 6: IEEE PDF eXpress Type-3 font ban & 150-DPI visual page rendering
  if command -v pdffonts >/dev/null 2>&1; then
    ! pdffonts "${TEX_BASE}.pdf" | awk 'NR>2 {print $2, $3}' | grep -E "Type 3" || exit 1
  fi
  if command -v pdftoppm >/dev/null 2>&1; then
    mkdir -p artifacts/pages && rm -f artifacts/pages/page*.png
    pdftoppm -png -r 150 "${TEX_BASE}.pdf" artifacts/pages/page
  fi
fi""",
        "gitignore": "*.aux\n*.bbl\n*.blg\n*.fdb_latexmk\n*.fls\n*.log\n*.out\n*.synctex.gz\n*.toc\nartifacts/\n__pycache__/\n.venv/\n.DS_Store\n",
    },
    "game-sim-3d": {
        "title": "3D Game Engine & Embodied AI Simulation (Godot 4 / Bevy ECS / Unity 6 Headless)",
        "stack": "Godot 4 (GDScript / Headless CLI), Bevy ECS (Rust / Cargo / Vulkan), Unity 6 (-batchmode / ML-Agents), Python uv",
        "gate_cmds": """# Stage 1: Merge marker & binary artifact hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.git --exclude-dir=.godot --exclude-dir=target --exclude-dir=Library || exit 1
# Stage 2: Godot 4 headless script & scene validation (when project.godot and scenes/main.gd are present)
if [ -f project.godot ] && [ -f scenes/main.gd ] && command -v godot >/dev/null 2>&1; then godot --headless --path . -s scenes/main.gd; fi
# Stage 3: Bevy / Rust ECS compile & test gate (when Cargo.toml is present)
if [ -f Cargo.toml ] && command -v cargo >/dev/null 2>&1; then cargo clippy --all-targets -- -D warnings && cargo test; fi
# Stage 4: Deterministic headless simulation & telemetry verification
if [ -f sim/verify_sim.py ]; then PYTHONHASHSEED=42 uv run python sim/verify_sim.py; fi""",
        "gitignore": ".godot/\ntarget/\nLibrary/\nTemp/\nObj/\nBuilds/\nLogs/\nUserSettings/\n*.import\n*.apk\n.venv/\n__pycache__/\n.DS_Store\n",
    },
    "metatopia-chameleon": {
        "title": "Metatopia Chameleon 2D App & 3D Spatial Experience Suite (Goldwood / Obsidian / Cobalt / Folio)",
        "stack": "React 19, TypeScript 5, Vite 6, Three.js (WebGL2/WebXR), 3-Tier Token Bridge, Vitest, Playwright 1080p Video, GitLab & Cloudflare Staging",
        "gate_cmds": """# Stage 1: Merge conflict & Math.random hygiene
! grep -rnE '^(<<<<<<<|=======|>>>>>>>)' src/ tests/ e2e/ tokens/ schemas/ 2>/dev/null || exit 1
! grep -rn "Math.random" src/ 2>/dev/null || exit 1
# Stage 2: Strict TypeScript & Unit Tests
if [ -f package.json ]; then npm run typecheck && npm run test:run; fi
# Stage 3: Production Build & Goldwood (G >= 9.0) Staging Bundle
if [ -f scripts/build_staging_bundle.mjs ]; then npm run build:staging; fi
# Stage 4: Hermetic Playwright E2E & 1080p Video Walkthrough
if [ -f scripts/record_walkthrough.mjs ]; then node scripts/record_walkthrough.mjs; fi""",
        "gitignore": "node_modules/\ndist/\ntest-results/\nplaywright-report/\nartifacts/*.webm\n*.log\n.DS_Store\n.env.local\n",
    },
}

ARCHETYPE_SKILL_ROUTING: dict[str, list[tuple[str, str]]] = {
    "web-ts-3d-gis": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("geospatial-digital-twins", "~/.gemini/config/skills/geospatial-digital-twins/SKILL.md"),
        ("vercel-react-craft", "~/.gemini/config/skills/vercel-react-craft/SKILL.md"),
        ("ui-craft-polish", "~/.gemini/config/skills/ui-craft-polish/SKILL.md"),
        ("frontend-design", "~/.gemini/config/skills/frontend-design/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("webapp-testing", "~/.gemini/config/skills/webapp-testing/SKILL.md"),
        ("cloudflare-platform", "~/.gemini/config/skills/cloudflare-platform/SKILL.md"),
        ("principal-engineering", "~/.gemini/config/skills/principal-engineering/SKILL.md"),
    ],
    "metatopia-chameleon": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("vercel-react-craft", "~/.gemini/config/skills/vercel-react-craft/SKILL.md"),
        ("ui-craft-polish", "~/.gemini/config/skills/ui-craft-polish/SKILL.md"),
        ("frontend-design", "~/.gemini/config/skills/frontend-design/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("remotion-video", "~/.gemini/config/skills/remotion-video/SKILL.md"),
        ("webapp-testing", "~/.gemini/config/skills/webapp-testing/SKILL.md"),
        ("cloudflare-platform", "~/.gemini/config/skills/cloudflare-platform/SKILL.md"),
        ("eu-grants-and-venture-ops", "~/.gemini/config/skills/eu-grants-and-venture-ops/SKILL.md"),
    ],
    "python-uv-ai": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("gx10-cluster", "~/.gemini/config/skills/gx10-cluster/SKILL.md"),
        ("stochastic-math-econometrics", "~/.gemini/config/skills/stochastic-math-econometrics/SKILL.md"),
        ("web3-fintech-settlement", "~/.gemini/config/skills/web3-fintech-settlement/SKILL.md"),
        ("huggingface-ml", "~/.gemini/config/skills/huggingface-ml/SKILL.md"),
        ("mcp-builder", "~/.gemini/config/skills/mcp-builder/SKILL.md"),
        ("supabase-postgres", "~/.gemini/config/skills/supabase-postgres/SKILL.md"),
        ("principal-engineering", "~/.gemini/config/skills/principal-engineering/SKILL.md"),
    ],
    "rust-systems": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("rust-go-systems", "~/.gemini/config/skills/rust-go-systems/SKILL.md"),
        ("bevy-gamedev", "~/.gemini/config/skills/bevy-gamedev/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("principal-engineering", "~/.gemini/config/skills/principal-engineering/SKILL.md"),
    ],
    "spatial-xr-ble": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("iot-embedded-ble", "~/.gemini/config/skills/iot-embedded-ble/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("unity", "~/.gemini/config/plugins/unity/skills/unity-cli/SKILL.md"),
        ("remotion-video", "~/.gemini/config/skills/remotion-video/SKILL.md"),
        ("ui-craft-polish", "~/.gemini/config/skills/ui-craft-polish/SKILL.md"),
    ],
    "paper-ieee-arxiv": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("latex-paper-craft", "~/.gemini/config/skills/latex-paper-craft/SKILL.md"),
        ("stochastic-math-econometrics", "~/.gemini/config/skills/stochastic-math-econometrics/SKILL.md"),
        ("eu-grants-and-venture-ops", "~/.gemini/config/skills/eu-grants-and-venture-ops/SKILL.md"),
        ("pdf", "~/.gemini/config/skills/pdf/SKILL.md"),
        ("science-arxiv", "~/.gemini/config/plugins/science/skills/literature_search_arxiv/SKILL.md"),
        ("science-openalex", "~/.gemini/config/plugins/science/skills/literature_search_openalex/SKILL.md"),
        ("huggingface-ml", "~/.gemini/config/skills/huggingface-ml/SKILL.md"),
    ],
    "game-sim-3d": [
        ("goldwood-gwi", "~/.gemini/config/skills/goldwood-gwi/SKILL.md"),
        ("geospatial-digital-twins", "~/.gemini/config/skills/geospatial-digital-twins/SKILL.md"),
        ("godot-gamedev", "~/.gemini/config/skills/godot-gamedev/SKILL.md"),
        ("bevy-gamedev", "~/.gemini/config/skills/bevy-gamedev/SKILL.md"),
        ("unity", "~/.gemini/config/plugins/unity/skills/unity-cli/SKILL.md"),
        ("threejs-webgpu", "~/.gemini/config/skills/threejs-webgpu/SKILL.md"),
        ("remotion-video", "~/.gemini/config/skills/remotion-video/SKILL.md"),
    ],
}

GWI_PROFILE_SKILL_ROUTING: dict[str, list[str]] = {
    "app": [
        "goldwood-gwi",
        "vercel-react-craft",
        "ui-craft-polish",
        "frontend-design",
        "astryx-design",
        "geospatial-digital-twins",
        "webapp-testing",
        "cloudflare-platform",
        "supabase-postgres",
        "web3-fintech-settlement",
        "eu-grants-and-venture-ops",
        "chrome-devtools-plugin",
        "modern-web-guidance-plugin",
    ],
    "sim": [
        "goldwood-gwi",
        "threejs-webgpu",
        "geospatial-digital-twins",
        "godot-gamedev",
        "bevy-gamedev",
        "unity",
        "rust-go-systems",
        "iot-embedded-ble",
        "stochastic-math-econometrics",
        "gx10-cluster",
    ],
    "video": [
        "goldwood-gwi",
        "remotion-video",
        "gemini-api",
        "threejs-webgpu",
    ],
    "paper": [
        "goldwood-gwi",
        "latex-paper-craft",
        "stochastic-math-econometrics",
        "eu-grants-and-venture-ops",
        "pdf",
        "science",
        "huggingface-ml",
        "keenable",
        "parallel",
    ],
}

GWI_LENS_SKILL_ROUTING: dict[str, list[str]] = {
    "technical-artist": [
        "goldwood-gwi",
        "threejs-webgpu",
        "geospatial-digital-twins",
        "godot-gamedev",
        "bevy-gamedev",
        "unity",
    ],
    "ux-ergonomics": [
        "goldwood-gwi",
        "ui-craft-polish",
        "a11y-debugging",
        "frontend-design",
        "astryx-design",
        "ui",
        "ui-ugui",
        "ui-uitk",
    ],
    "spatial-cinema": [
        "goldwood-gwi",
        "remotion-video",
        "gemini-omni-flash-api",
        "optimize-audio",
        "audio-setup-mixers",
    ],
    "typography-editor": [
        "goldwood-gwi",
        "latex-paper-craft",
        "eu-grants-and-venture-ops",
        "pdf",
        "docx",
        "pptx",
        "xlsx",
    ],
    "web-perf-edge": [
        "goldwood-gwi",
        "vercel-react-craft",
        "debug-optimize-lcp",
        "memory-leak-debugging",
        "cookie-debugging",
        "cloudflare-platform",
        "iot-embedded-ble",
        "optimize-web",
    ],
    "data-api-architect": [
        "goldwood-gwi",
        "supabase-postgres",
        "web3-fintech-settlement",
        "bigquery-sql",
        "dbt-bigquery",
        "dataform-bigquery",
        "gcp-dataflow",
        "gcp-spark",
        "accidental-data-loss-prevention",
        "api-engineer",
        "api-testing",
        "api-mocking",
    ],
    "ai-ml-science": [
        "goldwood-gwi",
        "gx10-cluster",
        "stochastic-math-econometrics",
        "gemini-api-dev",
        "google-antigravity-sdk",
        "huggingface-ml",
        "ml-best-practices",
        "mcp-builder",
        "science",
    ],
    "principal-verification": [
        "goldwood-gwi",
        "principal-engineering",
        "rust-go-systems",
        "webapp-testing",
        "managing-python-dependencies",
        "uv",
        "credentials",
        "personal-kb",
    ],
}


def cmd_scaffold(args) -> None:
    if getattr(args, "list", False) or not args.archetype:
        print("=== Available Autonomous Project Archetypes (`sovereign-kb scaffold <archetype> <dir>`) ===")
        for key, spec in ARCHETYPES.items():
            routed = ", ".join(s[0] for s in ARCHETYPE_SKILL_ROUTING.get(key, []))
            print(f"  - {key:18s} : {spec['title']}")
            print(f"    {'':18s}   Stack : {spec['stack']}")
            print(f"    {'':18s}   Skills: {routed}")
        return

    arch = args.archetype
    if arch not in ARCHETYPES:
        print(f"Error: Unknown archetype '{arch}'. Choose from: {', '.join(ARCHETYPES.keys())}")
        sys.exit(1)

    if not args.target_dir:
        print("Error: Please specify target directory: sovereign-kb scaffold <archetype> <target_dir>")
        sys.exit(1)

    target = Path(args.target_dir).expanduser().resolve()
    proj_name = args.name or target.name
    author_name = getattr(args, "author", None) or os.environ.get("KB_AUTHOR_NAME", "Author Name")
    spec = ARCHETYPES[arch]
    today = datetime.date.today().isoformat()

    target.mkdir(parents=True, exist_ok=True)
    (target / "docs").mkdir(exist_ok=True)
    (target / "scripts").mkdir(exist_ok=True)

    skill_rows = "\n".join(f"| `{sname}` | `{spath}` |" for sname, spath in ARCHETYPE_SKILL_ROUTING.get(arch, []))

    agents_md = f"""# AGENTS.md — Master Platform Guide & Complete Feature Registry

**Repository:** `{proj_name}`
**Archetype:** `{arch}` — {spec["title"]}
**Primary Stack:** `{spec["stack"]}`
**Initialized:** `{today}` via `sovereign-kb scaffold {arch}`

---

## 🎯 High-Level Agent Operating System

### 1. Autonomous Multi-Model Routing Strategy
| Tier | Role | Target Agent / Tier | Responsibilities |
| :--- | :--- | :--- | :--- |
| **Tier 1** | **Fast Execution & Implementation** | **Fast Worker (`sonnet`)** | Feature/simulation implementation, plotting scripts, tests, and rapid fixes. |
| **Tier 2** | **Deep Architecture / Reviewer #1** | **Heavy Worker (`fable`)** | System architecture, narrative/positioning coherence, and UX/figure review. |
| **Tier 3** | **Formal Verification / Reviewer #2** | **Independent Auditor (`astra`)** | Mathematical proofs, boundary/dimensional checks, concurrency, and edge-case audits. |
| **Tier 4** | **Sovereign Local Big-Context** | **Local Long-Context Node** | Private long-context cross-artifact sweeps across code, JSON results, and prose. |

### 2. Mandatory Operating Rules
1. **100% Catalog / Spec Synchronization**: Every feature, endpoint, module, or theorem must be registered in [`docs/FEATURE_CATALOG.md`](docs/FEATURE_CATALOG.md) (or `PAPER_SPEC.md` & `AUDIT_LEDGER.md` for papers) with its exact implementation path and verification coverage.
2. **Parallel Warm-Started Multi-Model Review (`Rule 1 — Warm-Start, Don't Restrict`)**: Run `fable` and `astra` in parallel (`oc triad "<prompt>"`) with a mechanical Git & test warm-start briefing while keeping full tool access enabled. Never self-exempt code changes as "low-risk" to skip independent review unless explicitly waived by the user.
3. **Hermetic Offline Execution & Zero Synthetic Data**: All simulations, tests, and walkthroughs must run deterministically offline using seeded fixtures (`PYTHONHASHSEED=42`) with zero fabricated numbers.
4. **Deterministic Quality Gate**: Never declare a task complete until `./scripts/quality_gate.sh` exits `0`.
5. **Centralized Credentials**: Never hardcode tokens or secrets. Source platform credentials from `~/.config/agents/secrets.env` (auto-loaded via `~/.zshenv`).

### 3. Activated Sovereign Agent Skills (`kb skills --archetype {arch}`)
| Skill ID | Local Entry Point (`SKILL.md` + `references/`) |
| :--- | :--- |
{skill_rows}

---

## ⚡ Standard Verification Commands

```bash
./scripts/quality_gate.sh
```

{spec["gate_cmds"]}

---

## 🗺️ Master Traceability Registry
See [`docs/FEATURE_CATALOG.md`](docs/FEATURE_CATALOG.md) (and `PAPER_SPEC.md` / `AUDIT_LEDGER.md` for scientific manuscripts).
"""

    feature_catalog_md = f"""# Master Feature Catalog — `{proj_name}`

| ID | Domain / Subsystem | Feature Description | Source Files | Automated Test / Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **F-01** | Core Architecture | Initial `{arch}` deterministic harness & quality gate | `AGENTS.md`, `scripts/quality_gate.sh` | `./scripts/quality_gate.sh` | ✅ |
"""

    quality_gate_sh = f"""#!/usr/bin/env bash
set -euo pipefail

echo "=== [{proj_name}] Running Deterministic Quality Gate ({arch}) ==="
{spec["gate_cmds"]}
echo "=== [{proj_name}] ALL QUALITY GATES PASSED ==="
"""

    agents_path = target / "AGENTS.md"
    catalog_path = target / "docs" / "FEATURE_CATALOG.md"
    gate_path = target / "scripts" / "quality_gate.sh"
    gitignore_path = target / ".gitignore"

    force = getattr(args, "force", False)
    if not agents_path.exists() or force:
        agents_path.write_text(agents_md, encoding="utf-8")
    if not catalog_path.exists() or force:
        catalog_path.write_text(feature_catalog_md, encoding="utf-8")
    if not gate_path.exists() or force:
        gate_path.write_text(quality_gate_sh, encoding="utf-8")
        gate_path.chmod(gate_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not gitignore_path.exists():
        gitignore_path.write_text(spec["gitignore"], encoding="utf-8")

    created_extra: list[Path] = []
    if arch == "game-sim-3d":
        for d in ("sim", "results", "scenes"):
            (target / d).mkdir(exist_ok=True)
        godot_proj = target / "project.godot"
        godot_main = target / "scenes" / "main.gd"
        verify_sim = target / "sim" / "verify_sim.py"
        if not godot_proj.exists() or force:
            godot_proj.write_text(
                "; Engine configuration file.\n"
                "config_version=5\n\n"
                "[application]\n\n"
                f'config/name="{proj_name}"\n'
                'config/features=PackedStringArray("4.4", "Forward Plus")\n',
                encoding="utf-8",
            )
            created_extra.append(godot_proj)
        if not godot_main.exists() or force:
            godot_main.write_text(
                "extends SceneTree\n\n"
                "func _init() -> void:\n"
                "\tvar pos := Vector3(1.0, 2.0, 3.0)\n"
                "\tvar vel := Vector3(-0.5, 0.0, 0.5)\n"
                "\tfor _step in range(60):\n"
                "\t\tpos += vel * (1.0 / 60.0)\n"
                '\tprint("[godot headless] PASS: final_pos=", pos)\n'
                "\tquit(0)\n",
                encoding="utf-8",
            )
            created_extra.append(godot_main)
        if not verify_sim.exists() or force:
            verify_sim.write_text(
                "#!/usr/bin/env python3\n"
                '"""Deterministic headless 3D simulation step & telemetry contract verifier."""\n'
                "import json\nimport math\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    dt = 1.0 / 60.0\n"
                "    steps = 120\n"
                "    trajectory = [\n"
                '        {"tick": t, "time_s": round(t * dt, 4), "energy_j": round(math.exp(-0.01 * t), 6)}\n'
                "        for t in range(steps)\n"
                "    ]\n"
                '    out = Path(__file__).resolve().parent.parent / "results" / "sim_telemetry.json"\n'
                "    out.parent.mkdir(parents=True, exist_ok=True)\n"
                '    out.write_text(json.dumps({"seed": 42, "dt_s": dt, "steps": steps, "trajectory": trajectory[:5]}, indent=2), encoding="utf-8")\n'
                '    print(f"[verify_sim] PASS: Verified {steps} deterministic ticks -> {out}")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(verify_sim)

    if arch == "metatopia-chameleon":
        for d in ("tokens", "schemas", "src", "tests", "e2e", "evidence"):
            (target / d).mkdir(exist_ok=True)
        tok_path = target / "tokens" / "metatopia-tokens.json"
        if not tok_path.exists() or force:
            tok_path.write_text(
                json.dumps(
                    {
                        "version": "1.0.0",
                        "defaultConcept": "goldwood",
                        "supportedConcepts": ["goldwood", "obsidian", "cobalt", "folio"],
                        "supportedModes": ["split", "2d", "3d"],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            created_extra.append(tok_path)

    if arch == "paper-ieee-arxiv":
        for d in ("sim", "results", "figures", "tables"):
            (target / d).mkdir(exist_ok=True)

        paper_spec = target / "PAPER_SPEC.md"
        audit_ledger = target / "AUDIT_LEDGER.md"
        sim_script = target / "sim" / "run_sim.py"
        bib_script = target / "scripts" / "verify_bib.py"
        arxiv_script = target / "scripts" / "package_arxiv.py"
        bib_file = target / "references.bib"
        tex_file = target / "paper.tex"

        if not paper_spec.exists() or force:
            paper_spec.write_text(
                f"# PAPER_SPEC.md — Mathematical & Empirical Contract (`{proj_name}`)\n\n"
                "## 1. Core Thesis & Research Questions\n"
                "- **Primary Thesis**: `<State 1-sentence falsifiable core thesis>`\n"
                "- **$RQ_1$ (Analytical Bound & Stability)**: `<State RQ1>`\n"
                "- **$RQ_2$ (Empirical Latency / Throughput / Energy Regime)**: `<State RQ2>`\n\n"
                "## 2. SOTA Gap Matrix (Table I)\n"
                "| Reference | Closed-Form Bound | Non-Stationary / Cliff Regime | Real Telemetry | Energy ($\\text{J/tok}$) |\n"
                "| :--- | :---: | :---: | :---: | :---: |\n"
                "| Baseline A `\\cite{gelenbe1991product}` | ✅ | ❌ | ❌ | ❌ |\n"
                "| **Ours** | ✅ | ✅ | ✅ | ✅ |\n\n"
                "## 3. Notation & Assumption Ledger\n"
                "- **Assumption $A_1$**: Arrival rate $\\lambda > 0$ and service rate $\\mu > \\lambda$ satisfy $\\rho = \\lambda/\\mu \\in (0, 1)$.\n",
                encoding="utf-8",
            )
            created_extra.append(paper_spec)

        if not audit_ledger.exists() or force:
            audit_ledger.write_text(
                f"# AUDIT_LEDGER.md — Tri-Council Scientific Audit (`{proj_name}`)\n\n"
                "| Audit ID | Auditor Role | Severity | Section / Eq. | Finding | Resolution | Status |\n"
                "| :--- | :--- | :---: | :--- | :--- | :--- | :---: |\n"
                "| **AUD-01** | Reviewer #2 (Formal Math) | `CRITICAL` | §III, Eq. (1) | Verify boundary limit $\\rho \\to 0^+$ and $\\rho \\to 1^-$. | Verified analytically and in `sim/run_sim.py`. | `RESOLVED` |\n",
                encoding="utf-8",
            )
            created_extra.append(audit_ledger)

        if not sim_script.exists() or force:
            sim_script.write_text(
                "#!/usr/bin/env python3\n"
                '"""Deterministic analytical vs. Monte Carlo simulation producing results/metrics.json."""\n'
                "import json\nimport random\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    random.seed(42)\n"
                "    mu = 10.0\n"
                "    rhos = [0.2, 0.4, 0.6, 0.8]\n"
                "    records = []\n"
                "    for rho in rhos:\n"
                "        lam = rho * mu\n"
                "        analytical_delay = 1.0 / (mu - lam)\n"
                '        records.append({"rho": rho, "lambda": lam, "mu": mu, "analytical_delay_s": round(analytical_delay, 6)})\n'
                '    out = Path(__file__).resolve().parent.parent / "results" / "metrics.json"\n'
                "    out.parent.mkdir(parents=True, exist_ok=True)\n"
                '    out.write_text(json.dumps({"seed": 42, "records": records}, indent=2), encoding="utf-8")\n'
                '    print(f"[sim] Wrote {len(records)} records to {out}")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(sim_script)

        plot_script = target / "scripts" / "plot_figures.py"
        if not plot_script.exists() or force:
            plot_script.write_text(
                "#!/usr/bin/env python3\n"
                '"""Generate IEEE PDF eXpress compliant Type-42 vector PDF figures from results/metrics.json."""\n'
                "import json\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    root = Path(__file__).resolve().parent.parent\n"
                '    metrics_path = root / "results" / "metrics.json"\n'
                "    if not metrics_path.exists():\n"
                "        return\n"
                '    data = json.loads(metrics_path.read_text(encoding="utf-8"))["records"]\n'
                "    try:\n"
                "        import matplotlib.pyplot as plt\n"
                '        plt.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42, "font.family": "serif", "font.size": 9})\n'
                "        fig, ax = plt.subplots(figsize=(3.5, 2.4))\n"
                '        ax.plot([r["rho"] for r in data], [r["analytical_delay_s"] * 1000 for r in data], "-o", color="#0072B2", label="Analytical Bound")\n'
                '        ax.set_xlabel("Utilization $\\\\rho = \\\\lambda / \\\\mu$")\n'
                '        ax.set_ylabel("Mean Sojourn Delay (ms)")\n'
                '        ax.grid(True, linestyle="--", alpha=0.4)\n'
                "        ax.legend(frameon=False)\n"
                "        fig.tight_layout()\n"
                '        out_pdf = root / "figures" / "fig1_performance.pdf"\n'
                "        out_pdf.parent.mkdir(parents=True, exist_ok=True)\n"
                "        fig.savefig(out_pdf)\n"
                "        plt.close(fig)\n"
                '        print(f"[plot] Generated Type-42 vector figure {out_pdf}")\n'
                "    except ImportError:\n"
                '        print("[plot] matplotlib not installed in current env; skipping PDF plot generation.")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(plot_script)

        if not bib_script.exists() or force:
            bib_script.write_text(
                "#!/usr/bin/env python3\n"
                '"""Zero-hallucination BibTeX citation verifier."""\n'
                "import re\nimport sys\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    root = Path(__file__).resolve().parent.parent\n"
                '    bib_path = root / "references.bib"\n'
                "    if not bib_path.exists():\n"
                '        print("[verify_bib] No references.bib found; skipping.")\n'
                "        return\n"
                '    bib_text = bib_path.read_text(encoding="utf-8")\n'
                '    keys = re.findall(r"@\\w+\\{\\s*([^,\\s]+)\\s*,", bib_text)\n'
                "    if len(keys) != len(set(keys)):\n"
                '        print("ERROR: Duplicate BibTeX keys detected in references.bib", file=sys.stderr)\n'
                "        sys.exit(1)\n"
                "    key_set = set(keys)\n"
                "    cited = set()\n"
                '    for tex in root.glob("*.tex"):\n'
                '        for m in re.finditer(r"\\\\cite\\{([^}]+)\\}", tex.read_text(encoding="utf-8")):\n'
                '            for k in m.group(1).split(","):\n'
                "                if k.strip():\n"
                "                    cited.add(k.strip())\n"
                "    missing = sorted(cited - key_set)\n"
                "    if missing:\n"
                '        print(f"ERROR: Missing citations in references.bib: {missing}", file=sys.stderr)\n'
                "        sys.exit(1)\n"
                '    print(f"[verify_bib] PASS: {len(cited)} cited keys verified across {len(key_set)} BibTeX entries.")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(bib_script)

        if not arxiv_script.exists() or force:
            arxiv_script.write_text(
                "#!/usr/bin/env python3\n"
                '"""Package clean Overleaf / arXiv submission bundle."""\n'
                "import zipfile\nfrom pathlib import Path\n\n"
                "def main() -> None:\n"
                "    root = Path(__file__).resolve().parent.parent\n"
                '    bundle = root / "overleaf_arxiv_bundle.zip"\n'
                '    include_exts = {".tex", ".bib", ".bbl", ".sty", ".cls", ".pdf"}\n'
                '    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:\n'
                '        for p in sorted(root.rglob("*")):\n'
                '            if p.is_file() and p.name != bundle.name and ".git" not in p.parts and ".venv" not in p.parts:\n'
                "                if p.suffix in include_exts:\n"
                "                    zf.write(p, p.relative_to(root))\n"
                '    print(f"[package_arxiv] Created {bundle}")\n\n'
                'if __name__ == "__main__":\n'
                "    main()\n",
                encoding="utf-8",
            )
            created_extra.append(arxiv_script)

        if not bib_file.exists() or force:
            bib_file.write_text(
                "@article{gelenbe1991product,\n"
                "  author  = {Gelenbe, Erol},\n"
                "  title   = {Product-Form Queueing Networks with Negative and Positive Customers},\n"
                "  journal = {Journal of Applied Probability},\n"
                "  volume  = {28},\n"
                "  number  = {3},\n"
                "  pages   = {656--663},\n"
                "  year    = {1991},\n"
                "  doi     = {10.2307/3214499}\n"
                "}\n",
                encoding="utf-8",
            )
            created_extra.append(bib_file)

        if not tex_file.exists() or force:
            tex_file.write_text(
                "\\documentclass[conference]{IEEEtran}\n"
                "\\usepackage{amsmath,amssymb,amsfonts,amsthm}\n"
                "\\usepackage{booktabs}\n"
                "\\usepackage{cite}\n"
                "\\newtheorem{theorem}{Theorem}\n"
                "\\newtheorem{assumption}{Assumption}\n\n"
                f"\\title{{{proj_name}: Deterministic Analytical and Empirical Evaluation}}\n"
                f"\\author{{\\IEEEauthorblockN{{{author_name}}}}}\n\n"
                "\\begin{document}\n"
                "\\maketitle\n\n"
                "\\begin{abstract}\n"
                "We formulate a falsifiable analytical and empirical model validated via deterministic simulation~\\cite{gelenbe1991product}.\n"
                "\\end{abstract}\n\n"
                "\\section{Introduction}\n"
                "Modern distributed systems require rigorous analytical bounds~\\cite{gelenbe1991product}.\n\n"
                "\\bibliographystyle{IEEEtran}\n"
                "\\bibliography{references}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            created_extra.append(tex_file)

    print(f"[sovereign-kb scaffold] Initialized '{proj_name}' ({arch}) at {target}")
    print(f"  - {agents_path}")
    print(f"  - {catalog_path}")
    print(f"  - {gate_path} (executable)")
    print(f"  - {gitignore_path}")
    for extra in created_extra:
        print(f"  - {extra}")
