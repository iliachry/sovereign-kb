---
title: The Deterministic Autonomous Engineering Framework
type: operating-system
updated: 2026-10-26
tags: [autonomous-engineering, quality-gates, video-walkthrough, youtube-publishing, playwright, webxr, opencode, testing, hermetic, finops, ccusage, telemetry]
related:
  - "[[00-identity/cognitive-and-working-style]]"
  - "[[02-operating-system/heuristics-and-mental-models]]"
  - "[[02-operating-system/codebase-governance-and-sizing]]"
  - "[[02-operating-system/templates/TEMPLATE-autonomous-project-AGENTS]]"
  - "[[03-life-domains/ventures-and-engineering]]"
  - "[[03-life-domains/infrastructure-and-compute]]"
---

# The Deterministic Autonomous Engineering Framework

Developed and battle-tested across production 3D Geospatial Digital Twins, Next.js/CMS platforms, and Meta Quest 3 WebXR applications, this framework solves the core problem of agentic software engineering: **how to make autonomous AI agents deliver zero-regression, production-grade software deterministically without human babysitting**.

---

## 1. Core Philosophy: Asynchronous Video-Backed Autonomy

> *"A passing unit test suite or a green compiler is necessary, but never sufficient. An agent has not finished a task until the full application is exercised headlessly in motion, zero console/network telemetry violations are mathematically asserted, and a human-verifiable video walkthrough + contact sheet is produced and inspected by an independent auditor model."*

1. **Human Role (Architect & Asynchronous Reviewer)**:
   - Specifies architectural intent, domain requirements, or issue specifications.
   - Reviews work asynchronously via **recorded 1080p video walkthroughs** (`.mp4` / `.webp`) and **chronological contact sheets**—never wasting flow state manually clicking through broken dev builds.
2. **Multi-Model Separation of Powers**:
   - Never allow the same model context that wrote the code to rubber-stamp its own delivery, and never let the authoring agent self-exempt its own code changes as "low-risk" to skip independent review.
   - **Rule 1 (`Warm-Start, Don't Restrict`)**: Always invoke `fable` and `astra` **in parallel** (`oc triad "<prompt>"`) with a mechanical Git warm-start header (`Repo Root`, `git log -n 3`, `git status -sb`, `git diff HEAD --stat`, and test results) so reviewers skip blind discovery turns while keeping 100% of their tool access enabled for independent verification.
   - **Tier 1 — Fast Worker (`sonnet`)**: High-speed execution for routine implementation, boilerplate, file edits, unit test expansion, lint fixes, and documentation sync.
   - **Tier 2 — Heavy Worker (`fable`)**: Complex multi-file feature implementation, state machine refactoring, 3D/WebXR shader & spatial logic, and walkthrough pipeline authoring.
   - **Tier 3 — Independent Auditor (`astra`)**: Adversarial verification of 100% Master Feature Catalog parity, security/secret hygiene, console/network diagnostics, and frame-by-frame visual/UX inspection (`DISPOSITION: APPROVED | NOT APPROVED`).
   - **Tier 4 — Sovereign Local Big-Context Node**: Private, zero-cost long-context reasoning (`262k` context) and cross-repository analysis.

---

## 2. The Six Pillars of Deterministic Engineering

### Pillar 1: The Exhaustive Master Feature Catalog (`AGENTS.md`)
Every repository must maintain a root `AGENTS.md` containing a **Master Feature Catalog** mapping every domain pillar, route, state store, modal, and API service:
- **Zero-Regression Contract**: Before writing code, the agent must read `AGENTS.md`.
- **Perpetual Catalog & Walkthrough Expansion**: Whenever a feature, route, or component is added or modified, the agent **must** update both the Master Feature Catalog in `AGENTS.md` and the automated E2E walkthrough spec so coverage remains strictly at **100% of all platform domains**.

### Pillar 2: Hermetic Offline Development & Deterministic Fixtures
Autonomous verification must never flake due to external network outages, third-party rate limits, or shared database mutation:
- **Frontend Mock Interceptors**: Provide a deterministic offline flag (`VITE_USE_MOCK_API=true` or `e2e/support/hermetic-routes.ts`) backed by realistic factories (`testFactories.ts`).
- **Non-Destructive CMS & Database Seeding (`seed-policy.ts`)**:
  - Walkthroughs against stateful backends must snapshot public API state before and after execution and assert byte-for-byte identity.
  - UI reorder or mutation tests exercise drag-and-drop and dirty-form states in the browser, then restore the original order without persisting destructive side effects.
- **Fast Component Isolation Sandbox (`/dev/showcase`)**:
  - Heavy 3D applications (CesiumJS globes, Three.js WebXR scenes) expose an isolated `/dev/showcase` route to test UI widgets without initializing full 3D engines.

### Pillar 3: Multi-Stage Deterministic Quality Gates (`npm run verify` / `quality_gate.sh`)
Before any walkthrough is recorded or any task is declared complete, a single deterministic command must pass **100% green** with a **Self-Healing Mandate**:
1. **Gate 1 — Git & Conflict Hygiene**: Ensures zero leftover `<<<<<<<` markers and zero tracked secrets.
2. **Gate 2 — Dead Code & Linkage Verification**: `knip` (unused files, exports, dependencies), ESLint (`0 errors`), and module/asset linkage verifiers.
3. **Gate 3 — Strict Typecheck & Unit Suite**: `tsc --noEmit` + Vitest / Pytest unit & integration suites.
4. **Gate 4 — Production Bundle Compilation**: Full `vite build` / `next build` verifying clean chunk splitting and zero leaked env tokens.

### Pillar 4: Automated Video Walkthrough Pipeline (`record:walkthrough` & `record:cms`)
Every repository implements a one-command headless recording pipeline (`npm run record:walkthrough` or `./scripts/pipeline/auto_walkthrough.sh`):
- **Cinematic & Real-Input Execution**:
  - Uses Playwright `--project=walkthrough-video` or headless Chromium + CDP frame streaming (`1920x1080`, `24fps`) with visual cursor/highlight helpers.
  - Supports both **Director Mode** (deterministic camera flights + scripted solver) and **Play Mode** (real keyboard/mouse/pointer events with zero auto-solve cheats).
- **Synchronized Output Artifacts**:
  - `artifacts/full_platform_walkthrough.mp4` (1080p H.264 for human/PM review)
  - `artifacts/full_platform_walkthrough.webp` (animated WebP for inline Markdown embedding)
  - `artifacts/full_platform_walkthrough.diagnostics.json` & `.meta.json`

### Pillar 5: Zero-Violation Runtime Telemetry (`diagnostics.ts`)
A video walkthrough is only valid if its companion `.diagnostics.json` asserts **strictly zero runtime violations** across the entire multi-minute run:
- `pageErrors: []` (`0` uncaught exceptions)
- `consoleErrors: []` (`0` `console.error` or React fatal warnings)
- `httpErrors: []` (`0` responses with status $\ge 400$)
- `failedRequests: []` (`0` aborted/failed network assets or 3D tiles)
- Any unavoidable third-party/dev-server noise must be matched by an explicit, documented regex allowance and recorded in `tolerated[]`—never silently dropped.

### Pillar 6: Multi-Modal Contact-Sheet & Frame-by-Frame UX Review
1. **Automated Packet Generation**:
   - Decomposes the recorded MP4 + `capture-manifest.json` (which logs SHA-256 video hash, exact frame counts per act, camera coordinates, HUD bounding boxes, and fired events) into:
     - `frames/`: Full-resolution PNGs sampled every 2.0s (or 0.25s for micro-interaction windows) plus exact before/at/after event frames.
     - `sheets/`: Chronological two-column contact sheets mapping every panel to an exact seek timestamp and frame ID.
     - `clips/`: Per-act MP4 clips.
2. **Mandatory Agent Visual Inspection Protocol**:
   - The agent **must** open and inspect the generated video/WebP and contact sheets using vision tools.
   - Verify text legibility, HUD/modal non-overlap, smooth 3D camera transitions, and prompt-to-target consistency.
   - Output a formal `assessment.json` / `WALKTHROUGH_REMEDIATION.md` mapping every visual finding or audit blocker (`B1..Bn`) to its exact frame ID, code fix, and automated proof.

---

## 3. Agentic FinOps, Token Telemetry & Cache Economics (`skb usage` / `ai-usage`)

Operating multi-agent autonomous engineering loops across **Antigravity (`gemini-4-argon` / `gemini-3.8-flash`)**, **OpenCode (`claude-fable-5-1`, `claude-sonnet-5-5`, `gpt-6-astra`)**, and **Sovereign Local Inference (`qwen3.6-35b-a3b`)** requires deterministic visibility into token consumption, prompt-cache efficiency, and API-equivalent credit burn.

### 3.1 The 4-Tier FinOps Routing & Cache Efficiency Law
1. **Prompt Cache Hit Ratio Guardrail ($\eta_{\text{cache}} \ge 90\%$)**:
   $$\eta_{\text{cache}} = \frac{T_{\text{cache\_read}}}{T_{\text{cache\_read}} + T_{\text{input\_uncached}}} \ge 0.90$$
   Long-horizon autonomous sessions must preserve system-prompt and `AGENTS.md` prefix stability so cache reads exceed 90% of input volume.
2. **Asymmetric Worker / Auditor Economics (`fable` vs. `astra`)**:
   - Heavy multi-file implementation (`fable`) typically accounts for >90% of OpenCode spend, whereas independent adversarial auditing (`astra`) costs ~6%. Never skip independent `astra` verification to save tokens.
3. **Sovereign Big-Context Offload (`$0.00` Marginal Cost)**:
   - Route exploratory `262k`-context repository sweeps and second-opinion code reviews to local sovereign GPU endpoints before invoking frontier cloud APIs.

### 3.2 Standard FinOps Telemetry Commands
```bash
skb usage                         # Unified multi-agent daily token, cache-hit & USD credit report
skb usage --days 7 --json         # Machine-readable 7-day FinOps breakdown across all local agents
ccusage daily --last 7 --offline  # Fast native multi-agent daily table via cached LiteLLM pricing
opencode stats                    # Instant OpenCode activity heatmap, subagent count & tool success rate
```

---

## 4. Autonomous YouTube Video Production & Publishing Pipeline (`youtube-upload`)

Whenever a 3D simulation (`game-sim-3d` in Godot 4 / Bevy / Unity), platform walkthrough, or scientific visual showcase is prepared for public release on YouTube, agents must follow a deterministic production, packaging, and autonomous publishing pipeline.

### 4.1 Video, Audio & Thumbnail Master Standards
1. **Broadcast Video & Audio Encoding**:
   - **Video Stream**: `1920×1080` (`1080p30` or `1080p60`), `H.264` (`libx264 -crf 16 -pix_fmt yuv420p -movflags +faststart`).
   - **Mandatory Stereo Audio Track**: Never upload a silent `.mp4` to YouTube. Always mux a `48 kHz` Stereo AAC (`192 kbps`) ambient telemetry/synth soundscape or voiceover narration so viewers never assume their speakers are broken.
   - **Multi-Act Narrative Structure**: Structure technical showcases into `4–5` distinct acts (`8–12s` each) with deterministic camera trajectories and lower-third chapter titles.
2. **High-CTR Branded Thumbnail Engineering (`< 2.0 MB` Hard Limit)**:
   - Resolution: `1920×1080` `.jpg` (quality `95`, typically `400–600 KB`) or optimized `.png` strictly below YouTube Studio's `2.0 MB` upload limit.
   - **100% Opaque Overlay Law (`alpha = 255`)**: When compositing channel badges or bottom high-CTR title banners over a rendered 3D frame, always use `fill=(5, 10, 22, 255)` (`100%` opacity) and position top badges cleanly below or completely over existing HUD bars so underlying HUD text never bleeds through.
   - **Mandatory Visual Inspection**: Always inspect the generated thumbnail image via vision tools before attaching it.
3. **Complete Publishing Kit Directory**:
   - Every release generates a self-contained kit folder containing:
     1. Master `.mp4` (`1080p`, H.264 + Stereo AAC)
     2. Branded High-CTR Thumbnail (`.jpg` `< 2.0 MB`) + `3–4` clean 1080p Act Stills (for YouTube Studio's 3-way *Thumbnail Test & Compare*)
     3. Synchronized English Subtitles (`.srt`) matching the act timestamps
     4. Copy-Paste Metadata (`YOUTUBE_STUDIO_COPY_PASTE.md`): Title ($\le 100$ chars), Description with `00:00` Automatic YouTube Chapters, comma-separated Tags ($\le 500$ chars), and a Pinned First Comment template.

### 4.2 Autonomous YouTube Studio Upload & Verification Protocol
When automating YouTube Studio uploads via Playwright (`~/.local/bin/youtube-upload`):
1. **Google Sign-In Anti-Detection Flags**:
   - Launch Google Chrome via `launch_persistent_context` (`~/.config/agents/youtube-chrome-profile`) with `ignore_default_args=["--enable-automation"]` and `--disable-blink-features=AutomationControlled`. Leaving Playwright's default `--enable-automation` flag active causes Google Sign-In to block the browser with *"This browser or app may not be secure"*.
2. **Direct Upload Modal Navigation (`?d=ud`)**:
   - Navigate directly to `https://studio.youtube.com/channel/<CHANNEL_ID>/videos/upload?d=ud` to open the file upload modal immediately, avoiding brittle button selectors across localized YouTube Studio headers.
   - Populate `#textbox` `0` (Title) and `#textbox` `1` (Description), attach the custom thumbnail to `input#file-loader[type='file']`, select `tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']`, expand `ytcp-button#toggle-button`, and populate `#tags-container input#text-input`.
3. **Draft vs. Public & Background HD-Check Confirmation Handling**:
   - If `#done-button` (*Publish*) is clicked while YouTube is still running background HD/copyright checks, YouTube Studio opens a secondary confirmation dialog (`ytcp-button#secondary-action-button` / *"Publish anyway"*).
   - Closing the browser before clicking `#secondary-action-button` leaves the video in **Draft** state. Always click `#secondary-action-button` (or click `ytcp-button.edit-draft-button` on the top `ytcp-video-row` in Channel Content, select `PUBLIC`, and click `#done-button`).
4. **Video ID Extraction Rule (`l` vs. `I`)**:
   - Never transcribe a YouTube video ID via visual OCR from a screenshot (where lowercase `l` and uppercase `I` are identical in sans-serif UI fonts). Always extract the exact `href` attribute directly from the DOM (`a.ytcp-video-info` or `ytcp-video-row`).
5. **Mandatory Public `oEmbed` `HTTP 200` Verification Gate**:
   - Never declare a YouTube upload complete until YouTube's public oEmbed endpoint returns **`HTTP 200`**:
     ```bash
     curl -s -i "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=<VIDEO_ID>&format=json" | head -n 15
     ```
   - `HTTP 200` + JSON payload proves the video is live and public; `HTTP 403` indicates it is still Private/Draft; `HTTP 404` indicates an invalid Video ID.
