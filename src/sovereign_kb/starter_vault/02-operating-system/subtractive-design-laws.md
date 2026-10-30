---
title: "The Subtractive Design & Cognitive Load Laws (GWI v1.4.0 Anti-Bloat Standard)"
type: operating-system
updated: 2026-10-29
tags: [subtractive-design, cognitive-load, anti-bloat, minimalism, gwi, ux-ergonomics, kolmogorov, sibling-inheritance, verga]
related:
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
  - "[[02-operating-system/lenses/ux-ergonomics]]"
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[00-identity/cognitive-and-working-style]]"
---

# The Subtractive Design & Cognitive Load Laws (`GWI v1.4.0` Anti-Bloat Standard)

> *"Perfection is achieved, not when there is nothing more to add, but when there is nothing left to take away."* — Antoine de Saint-Exupéry  
> *"Additive maximalism is how an uncalibrated LLM proves it worked. Subtractive restraint is how a Principal Designer respects human attention."*

Codified from multi-model post-mortem audits where an initial 9+ GWI build was stripped by **`-907 lines`** to match the institutional clarity of its parent Digital Twin portal, these **Subtractive Design Laws** govern every UI, 3D/XR HUD, scientific paper, slide deck, and video walkthrough across all projects.

---

## 1. Root-Cause Diagnosis: Why AI Agents Systematically Over-Produce ("The 7 Failure Mechanisms")

Left to default next-token priors and lower-bound quality gates, AI coding agents systematically exhibit **Kitchen-Sink / Additive Maximalism**:

| # | Failure Mechanism | Mathematical / Behavioral Cause | Concrete Symptom in Uncalibrated Builds (Before `-907` Line Cut) |
| :--- | :--- | :--- | :--- |
| **M1** | **Generation Without Retrieval (Template Prior Bias)** | Without a mandatory sibling-inspection pre-flight, the LLM samples its high-probability "AI dashboard" prior (`bg-slate-950` dark cyberpunk + neon cyan/emerald) instead of inspecting sibling repositories. | Built a dark-first neon theme (`#0b0f19`) when the sibling platform is a **Light-First (`#ffffff` / `#f8f9fa`) institutional portal** (`#0069d9` primary blue). |
| **M2** | **Checklist Monotonicity (One-Sided Lower Bounds)** | When every automated probe is a lower bound (`features >= 6`, `edge_density >= 0.015`, `cards >= 3`) with **no upper ceiling**, adding more badges, boxes, and paragraphs strictly increases the probability of passing. | Added nested `H2020` / `GA 826283` / `5 Pilots` sub-badges inside header buttons and a 4-box telemetry grid inside the left navigation sidebar. |
| **M3** | **Confusing Capability Availability with Default Visibility** | Agents prove they implemented 6 catalog features (`F-01..F-06`) by rendering all 6 simultaneously on the first viewport rather than making them **reachable via progressive disclosure**. | Rendered full session metadata, multi-line prompt descriptions, live sensor gauges, and NATALIE adaptation cards simultaneously on first paint. |
| **M4** | **Redundancy as Uncertainty Hedging** | Uncertain whether an evaluator prefers prose, KPI cards, a table, or citations, the LLM outputs **all four representations for the exact same fact** inside one turn. | Every assistant answer repeated the same metric in a 4-bullet paragraph, 4 KPI cards, a 6-column table, and 3 full-width citation cards. |
| **M5** | **Asymmetric Token vs. Saccade Cost** | Emitting 40 extra JSX lines costs the LLM near-zero marginal effort, whereas each extra visual group imposes a super-linear human visual-search and working-memory penalty (Hick–Hyman & Miller bounds). | High visual fragmentation (`edge_density > 0.36`) requiring rapid eye saccades across 3 dense columns. |
| **M6** | **Zero Temporal Decay of Attention State** | Agents treat historical turns identically to the active turn, leaving old analytical tables expanded indefinitely as the conversation grows. | After 3 prompts, 3 full-width analytical tables stacked vertically in the chat history (`expandedTablesVisible = 3`). |
| **M7** | **The Line-Count Progress Illusion** | Agents equate `+1,400 LOC` with thoroughness; `-907 LOC` of editorial subtraction never occurs unless enforced by a **Subtractive Cut Pass**. | Required explicit human intervention (`"mou fainetai too much information"`) to trigger the `-907` line reduction. |

---

## 2. Two-Sided Kolmogorov & Cognitive Load Formulation (`GWI v1.4.0`)

In `GWI v1.4.0`, the **Wooden Pole ($\beta\acute{\epsilon}\rho\gamma\alpha$ / *Verga* — Tier II Perceptual & Lens `ux-ergonomics`)** replaces one-sided lower bounds with a **Two-Sided Perceptual & Cognitive Window**:

$$\mathcal{P}_{\text{subtractive}}(\mathbf{m}) = \prod_{k \in \mathcal{B}} \exp\!\left( -\lambda_k \cdot \max\!\left(0,\; \frac{m_k - U_k}{\max(|U_k|, 16)},\; \frac{L_k - m_k}{\max(|L_k|, 1)}\right)^2 \right) \in (0, 1]$$

where $m_k$ is a measured surface density metric (e.g., `headerRows`, `headerItems`, `maxAnswerBullets`, `expandedTablesVisible`, `minTapTargetPx`, `edge_density`) with lower bound $L_k$ and **Hard Subtractive Ceiling** $U_k$. Any surface that violates $[L_k, U_k]$ suffers an immediate exponential penalty on $\mathcal{S}_{\text{perc}}$ and triggers a `ux-ergonomics` lens warning or veto.

---

## 3. The 7 Subtractive Design Laws (`L0`–`L6`)

### Law `L0` — Inherit Before You Invent (Mandatory Sibling Pre-Flight)
1. **Sibling Discovery First**: Before writing a single line of CSS, Tailwind, or theme configuration for any companion service (`chat.<domain>`, `docs.<domain>`, `admin.<domain>`, `cms.<domain>`, or same-venture repo), inspect the parent/sibling repository in your workspace.
2. **Exact Token Inheritance**: Extract and reuse the sibling's exact **default Light/Dark polarity**, background/surface hex values (`#ffffff`, `#f8f9fa`), brand primary (`#0069d9`), semantic status colors (`#2e7d32`, `#a65d00`, `#d32f2f`), typography family, and institutional footer/emblem (`eu-emblem.svg`, Horizon 2020 /Horizon Europe grant attribution).
3. **Anti-Cyberpunk Default**: Never default to dark-mode neon cyberpunk (`bg-slate-950` + glowing cyan/emerald) unless the parent product is explicitly dark-first or the user explicitly asks for it.

### Law `L1` — Persistent Chrome & Header Budget
1. **Single-Row Header (`headerRows == 1`)**: At desktop/tablet widths ($\ge 768\text{ px}$), the top application bar must fit on **1 horizontal row** (`height <= 56px`) with **`<= 7` top-level interactive items**.
2. **1-Word Navigation Labels**: Navigation tabs and pilot/workspace switchers must use short 1–2 word labels (`All Pilots`, `Venice`, `Athens`, `Hadrian`, `Demo`) with **zero nested sub-badges** inside buttons.
3. **Persistent Region Ceiling (`<= 3` Regions)**: At most 3 persistent structural regions on desktop (`Left Nav <= 2/12`, `Center Primary Canvas >= 7/12`, `Right Inspector <= 3/12` collapsible) and **1 region on mobile (`< 768px`)** with sidebars collapsed into drawers/sheets.

### Law `L2` — Default Reading & Copy Budget
1. **Executive Summary Ceiling (`<= 2` Bullets, `<= 80` Words)**: Default AI synthesis answers, modal summaries, and section introductions must contain **at most 2 concise bullet points** (`maxAnswerBullets <= 2`, `<= 80` words total). Deeper technical derivations belong behind an explicit *"Expand Details"* disclosure or follow-up prompt.
2. **1-Line Navigation & Tree Items**: Left-sidebar session rows, layer trees, and quick-prompt triggers must be **single-line items (`1 line`, `<= 6 words`)**—never multi-line paragraph previews or badge clusters inside navigation lists.
3. **Compact Inspector Cards (`<= 2` Lines per Item)**: Right-hand telemetry/inspector cards (`Sensors`, `Alerts`, `Adaptations`) must render as compact 2-line rows (Title + Primary Value/Status pill + 1-line subtitle, `<= 35 words` per card).

### Law `L3` — Representation Economy (One Fact, One Primary Representation)
1. **No Quadruple Encoding**: A single metric or insight may appear in **prose + at most 1 structured visual** (either KPI cards *or* a compact table), never repeated simultaneously across prose, KPI cards, multi-column tables, and large citation cards.
2. **Temporal Decay of History (`expandedTablesVisible <= 1`)**: In conversational or feed interfaces, **only the latest assistant message** may display expanded KPI cards and an analytical table (`<= 4` columns, `<= 5` rows). Older messages automatically collapse their structured tables into a single summary state.
3. **Inline Citation Pills**: Provenance and knowledge-base citations must render as **compact 1-line inline pills** (`[DOC-ID · Title]`), never as full-width stacked cards underneath every answer.

### Law `L4` — Breathing Room & Two-Sided Visual Density Window
1. **Minimum Open Canvas**: Maintain $\ge 30\%$ open background/whitespace on standard 2D web views (`max-w-3xl mx-auto` reading container), $\ge 40\%$ on presentation slides/video title cards, and $\ge 75\%$ unoccluded 3D viewport on spatial/XR HUDs (`ux.hud_occlusion_pct <= 22%`).
2. **Two-Sided Edge Density (`0.015 <= edge_density <= 0.36`)**: Rendered screenshots and contact sheets must neither be blank (`< 0.015`) nor visually over-fragmented (`> 0.36`).

### Law `L5` — Visual Vocabulary & Emphasis Budget
1. **1 Primary CTA per Viewport**: At most **1 high-emphasis solid primary button** (`#0069d9`) visible per screen region; all secondary actions use quiet outline/ghost styling.
2. **Badge & Pill Ceiling**: Never nest a badge inside another button/pill; limit status pills to `<= 1` per card row; use at most `2` font families (`sans` + `mono`) and `3` font weights (`400`, `600`, `700`).

### Law `L6` — Reachability Over Simultaneous Visibility & Mandatory Subtractive Cut Pass
1. **Progressive Disclosure**: Master Feature Catalog parity (`100%` of `F-01..F-06`) is satisfied when every feature is **reachable within $\le 1\text{–}2$ deliberate interactions** and verified across Walkthrough Acts—not by dumping every widget onto the initial screen.
2. **Mandatory Subtractive Cut Pass**: Before running the final `kb gwi audit <dir> --strict --strict-lenses` gate on any UI task, the builder agent must execute an explicit **Subtractive Cut Pass**:
   - Can any header row, subtitle, or decorative badge be deleted without losing task capability? **Delete it.**
   - Are any two components showing the exact same number at the same time? **Collapse or remove one.**
   - Does any chat/feed history repeat heavy tables across turns? **Gate heavy visuals to `isLatestAssistant` only.**

---

## 4. Cross-Media Cognitive Budgets (`App` / `3D Sim` / `Paper` / `Video`)

| Surface Profile | Max Persistent Regions | Max Copy per Unit | Max Table Size (Default) | Min Whitespace / Open Canvas | Hard Anti-Bloat Ceiling (`kb gwi` / `swm`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`app` (2D Web / Chat / SaaS)** | $\le 3$ Desktop, $1$ Mobile | $\le 2$ bullets ($\le 80$ words) / turn; $1$-line nav items | $\le 1$ expanded table ($\le 4\text{ cols}\times 5\text{ rows}$) | $\ge 30\%$ open canvas | `headerRows <= 1`, `maxAnswerBullets <= 2`, `expandedTablesVisible <= 1`, `0.015 <= edge_density <= 0.36` |
| **`sim` (3D GIS / WebXR / Godot / Bevy)** | $\le 2$ translucent HUD panels | $\le 30$ words total across active HUD | $0$ permanent tables over 3D canvas (use drawer) | $\ge 78\%$ clear 3D viewport | `hudOcclusionPct <= 22%`, `xrFovDeg <= 90` |
| **`paper` (IEEE / ACM / arXiv LaTeX)** | $1\text{–}2$ text columns | $\le 1$ core claim per paragraph; captions $\le 35$ words | $\le 6$ columns (`\toprule`/`\bottomrule`, `0` vertical rules) | Standard IEEE/ACM margins (`0pt` overfull) | `overfullHboxPt <= 10.0`, `0` markdown leakage |
| **`video` (YouTube 1080p/4K Walkthrough)** | $1$ focal action per Act | Lower-third / caption $\le 14$ words ($\le 2$ lines) | $1$ focal visual per chapter | $\ge 35\%$ clean frame around focal UI | `lufs` $\in [-16, -12]$, `0.015 <= edge_density <= 0.36` |
