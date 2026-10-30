---
title: "Lens L2: Principal UX, Spatial Ergonomics & Conversion Designer"
type: operating-system
updated: 2026-10-29
tags: [lens, ux, ergonomics, interaction-design, conversion, wcag, hud, chameleon, verga]
lens_id: ux-ergonomics
pole: verga
applies_to: ["**/routes/**", "**/pages/**", "**/components/**", "**/*.tsx", "**/*.jsx", "**/*.vue", "**/*.css"]
applies_when_deps: ["react", "react-dom", "next", "tailwindcss", "lucide-react"]
related:
  - "[[02-operating-system/lenses/README-lenses]]"
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
  - "[[02-operating-system/subtractive-design-laws]]"
  - "[[03-life-domains/ventures-and-engineering]]"
---

# Lens L2: Principal UX, Spatial Ergonomics & Conversion Designer (`ux-ergonomics`)

Evaluates 2D/3D web applications across **spatial HUD-over-canvas ergonomics, perceptual hierarchy, WCAG accessibility, Subtractive Design Laws (`L0`–`L9`), and frictionless conversion flow**.

---

## 1. Deterministic Telemetry Probes & Hard Vetoes

| Probe ID | Metric Inspected | Sovereign Target | Hard Veto Threshold |
| :--- | :--- | :--- | :--- |
| `ux.hud_occlusion_pct` | 2D overlay bounding-box union $\cap$ 3D `<canvas>` viewport area (`1920x1080`) | $\frac{|\text{HUD} \cap \text{Canvas}|}{|\text{Canvas}|} \le 18\%$ in default state | $> 25\%$ of 3D viewport occluded by permanent 2D panels |
| `ux.wcag_contrast_tap` | Linearized WCAG 2.1 contrast $\frac{L_1 + 0.05}{L_2 + 0.05} \ge 4.5:1$ & Fitts's Law tap target size | $0$ token contrast violations; targets $\ge 44\times 44\text{ px}$ | Any primary text-on-surface token pair $< 4.5:1$ or interactive controls $< 32\text{ px}$ |
| `ux.viewport_overflow` | 3-Viewport responsive contact sheet (`390` / `768` / `1440px`) & horizontal scroll overflow | `scrollWidth <= innerWidth` (`0px` overflow) across all 3 viewports | Horizontal overflow $> 0\text{ px}$ at `390px` mobile or missing 3-Viewport Sheet under `--strict-lenses` |
| `ux.cta_hierarchy` | Competing primary Call-To-Action buttons above the first fold | Exactly $1$ primary CTA + $\le 1$ secondary CTA | $\ge 3$ competing high-emphasis CTAs above the fold |
| `ux.steps_to_value` | Interaction clicks/steps from landing route to core product value or checkout | $\le 4$ steps | $> 5$ steps, dead-end links, or $> 6$ required form fields on initial onboarding |
| `ux.state_completeness` | Designed `loading`, `empty`, and actionable `error` states across views | $100\%$ coverage; `0` generic `"Something went wrong"` | Blank white/black screen on empty data or raw stack traces exposed to user |
| `ux.motion_tokens` | Semantic CSS design tokens & `@media (prefers-reduced-motion: reduce)` | Tokenized palette + reduced-motion fallback | Unbounded layout shift (`CLS > 0.1`) or unthrottled animations without reduced-motion guard |
| `ux.subtractive_budget` | [[02-operating-system/subtractive-design-laws|Subtractive Design Laws (`L0`–`L9`)]]: `headerRows`, `maxAnswerBullets`, `expandedTablesVisible`, `edge_density` | `headerRows == 1`, `maxAnswerBullets <= 2`, `expandedTablesVisible <= 1`, `0.015 <= edge_density <= 0.36` | Multi-row header clutter (`headerRows > 1`), stacked tables in chat history (`> 1`), or visual clutter (`edge_density > 0.36`) |
| `ux.sibling_inheritance` | Law `L0`: Sibling design-system inheritance | Exact Light/Dark default polarity, brand primary hex, typography & institutional footer parity | Unrequested dark neon-cyberpunk theme when sibling platform is Light-First institutional |

---

## 2. Builder Pre-Flight Constraints (Injected into `fable` / `sonnet`)

When a builder agent is dispatched on a repository where `ux-ergonomics` is active:
1. **Protect the 3D Viewport**: In hybrid 2D/3D apps, keep floating 2D sidebars collapsible or translucent (`backdrop-blur-md bg-slate-950/75`) and never exceed $25\%$ total viewport occlusion.
2. **One Primary Action Per Screen**: Every route or modal must have a single unambiguous primary action, clear visual hierarchy (title $\to$ metric/visual $\to$ action), and keyboard `Escape` / focus-trap hygiene.
3. **Zero Raw Hex Sprawl**: Bind colors, spacing, and typography to semantic CSS custom properties (`--bg-surface`, `--text-primary`, `--accent-primary`).
4. **Frictionless B2B Conversion**: Keep pricing, live demo walkthroughs, and onboarding reachable in $\le 1$ click from the landing view.

---

## 3. Astra Stage-5 Contact Sheet Rubric (`3-Viewport Responsive Sheet`)

Astra inspects the **3-Viewport Contact Sheet** (`390x844` Mobile, `768x1024` Tablet/Quest 3 Browser, `1440x900` Desktop) and must cite `(viewport, element_selector_or_region)` for every finding:
- **5-Second Hierarchy Test**: Is the core value proposition and primary interactive affordance immediately unmistakable within 5 seconds?
- **Information Density & Whitespace**: Are data tables, KPI cards, and GIS layer toggles grouped cleanly without horizontal scrollbars or text truncation at `390px`?
- **2D $\leftrightarrow$ 3D Visual Cohesion**: Do HUD panels harmonize with the underlying 3D scene rather than looking pasted on top?
