---
title: "Template: Autonomous Project AGENTS.md & Quality Harness"
type: template
updated: 2026-10-22
tags: [template, agents-md, autonomous-engineering, quality-gates, walkthrough, bootstrap]
related:
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[02-operating-system/codebase-governance-and-sizing]]"
  - "[[02-operating-system/archetypes/README-archetypes]]"
---

# Template: Autonomous Project `AGENTS.md` & Quality Harness

Use `sovereign-kb scaffold <archetype> <target_dir>` (see [[02-operating-system/archetypes/README-archetypes]]) or copy this blueprint into the root `AGENTS.md` of any new repository to immediately initialize the **Deterministic Autonomous Engineering Framework**.

---

````markdown
# AGENTS.md — Master Platform Guide & Complete Feature Registry

**Repository:** `<PROJECT_NAME>`
**Primary Stack:** `<e.g., Next.js 16 / Vite + React / Three.js WebXR / FastAPI / Rust>`
**Audience:** Autonomous AI Agents and Human Engineers

---

## 🎯 High-Level Agent Operating System

1. **Mandatory Step 0 — Catalog & Rules Audit**:
   - Before writing code or modifying schemas, read this `AGENTS.md` in full.
   - Reference the **Master Feature Catalog** below to ensure **zero existing features are broken, omitted, or regressed**.
2. **Multi-Model Orchestration (`Rule 1 — Warm-Start, Don't Restrict`)**:
   - Run `fable` and `astra` in parallel (`oc triad "<prompt>"`) with a mechanical Git & test warm-start briefing while keeping 100% of their tool access enabled; never self-exempt code changes as "low-risk" to skip review.
   - **Fast Worker (`sonnet`)**: Routine implementation, component wiring, unit tests, lint/type fixes, documentation.
   - **Heavy Worker (`fable`)**: Complex architecture, multi-domain features, 3D/spatial logic, walkthrough orchestration.
   - **Independent Auditor (`astra`)**: Audits 100% catalog parity, verifies zero-error diagnostics telemetry, inspects contact sheets/video frames, and issues formal `APPROVED` / `NOT APPROVED` disposition.
   - **Sovereign Local Node**: Long-context private code review and cross-repo checks.
3. **Deterministic Quality Gate (`npm run verify` / `./scripts/quality_gate.sh`)**:
   - Runs conflict-marker check, dead-code elimination (`knip`), linter (`eslint`), strict typecheck (`tsc --noEmit`), unit tests (`vitest` / `pytest`), and production build.
   - **Self-Healing Mandate**: If any gate fails, fix the root cause and re-run until 100% green without asking the user for manual intervention.
4. **Perpetual Video Walkthrough & Telemetry Contract (`npm run record:walkthrough`)**:
   - Every user-facing or backend/CMS change must be covered by an Act in `e2e/walkthrough.spec.ts`.
   - The walkthrough must assert **0 page errors, 0 console errors, 0 HTTP ≥ 400 responses, and 0 failed network requests** in `artifacts/*.diagnostics.json`.
   - **Mandatory Visual Review**: After recording, inspect the generated `.mp4` / `.webp` and contact sheets frame-by-frame to verify visual layout, transitions, and data integrity before concluding.
5. **Hermetic Offline Development**:
   - Never rely on flaky external cloud APIs during automated tests or walkthroughs. Use deterministic fixtures (`VITE_USE_MOCK_API=true` / `e2e/support/hermetic-routes.ts`).
6. **Zero-Leak Security Protocol**:
   - Never commit raw API tokens or credentials. Load shared secrets from `~/.config/agents/secrets.env` or git-ignored `.env.local`.

---

## ⚡ Standard Verification Commands

```bash
# 1. Full deterministic quality gate (Conflict Check + Knip + Lint + Typecheck + Unit Tests + Build)
npm run verify

# 2. Automated hermetic video walkthroughs (1080p MP4 + WebP + .diagnostics.json)
npm run record:walkthrough

# 3. Contact-sheet & frame-level UX review packet generation
npm run review:video -- --video artifacts/full_platform_walkthrough.mp4 --out renders/ux-review
```

---

## 🗺️ Master Feature Catalog (100% Coverage Registry)

| Domain | Area | Key Routes & Components | Primary Methods & State | Walkthrough Act |
|:---:|---|---|---|:---:|
| **1** | **Auth & Onboarding** | `/login`, `/register` | `checkAuth`, `useAuthStore` | Act 1 |
| **2** | **Core Dashboard** | `/dashboard` | `fetchMetrics`, `DashboardGrid` | Act 2 |
| **3** | **Domain Feature A** | `/feature-a` | `useFeatureStore` | Act 3 |
````
