# Unified Goldwood Index (`G >= 9.0`), 8 Bi-Polar Craft Lenses & 10 Subtractive Design Laws (`GWI+ v1.4.0`)

Derived from *The Goldwood Index* ($\chi\rho\upsilon\sigma\acute{o}\varsigma$ *Chrysos* / Golden Rigor $\times$ $\beta\acute{\epsilon}\rho\gamma\alpha$ *Verga* / Living Craft) and *The Aesthetics of Quality*, `skb gwi` (also exposed as standalone `gwi`, `skb goldwood`, and `skb audit`) enforces a **non-linear sigmoid barrier** across four Ontological Tiers $\mathbf{S} = (\mathcal{S}_{\text{sub}}, \mathcal{S}_{\text{perc}}, \mathcal{S}_{\text{act}}, \mathcal{S}_{\text{exist}}) \in [0, 10]^4$:

$$\mathcal{G}(\mathbf{S}) = \left( \sum_{i=1}^4 \alpha_i \mathcal{S}_i \right) \cdot \prod_{i=1}^4 \Psi(\mathcal{S}_i), \qquad \Psi(x) = \min\!\left(1.0,\; \frac{\sigma(x)}{\sigma(9.0)}\right), \qquad \sigma(x) = \frac{1}{1 + e^{-k(x - \theta)}} \quad (k = 2.5, \; \theta = 7.0)$$

- **Weakest-Link Barrier Collapse**: If any single tier $\mathcal{S}_i$ drops below $\theta = 7.0$, $\Psi(\mathcal{S}_i) \to 0$, collapsing $\mathcal{G}(\mathbf{S}) \to 0$ (a silent video stream, Type-3 bitmap font, or unhandled runtime error cannot be averaged out by feature velocity).
- **Coupled *Chrysos* $\times$ *Verga* Scoring & `7.80` Unmeasured Cap**:
  $$\mathcal{G}_{\text{Verga}}(R) = \min_{\ell \in \mathcal{L}_{\text{req}}(R)} \mathcal{S}_{\ell}(R), \qquad \mathcal{G}_{\text{total}}(R) = \sqrt{\mathcal{G}_{\text{Chrysos}}(R) \cdot \mathcal{G}_{\text{Verga}}(R)}$$
  Under `--strict-lenses`, any required Craft Lens with unmeasured runtime/contact-sheet evidence is capped at **`7.80 / 10` (`VERGA_UNMEASURED_CAP`)**, preventing premature `9.0+` certification.
- **Two-Sided Kolmogorov & Subtractive Cognitive Window (`v1.4.0`)**:
  $$\mathcal{E}(\mathcal{L}) = \exp\!\left( - \max\!\left(0, \frac{|\mathcal{L}| - K(\mathcal{T})}{K(\mathcal{T})}\right) \right), \qquad \mathcal{P}_{\text{subtractive}}(\mathbf{m}) = \prod_{k \in \mathcal{B}} \exp\!\left( -\lambda_k \cdot \max\!\left(0,\; \frac{m_k - U_k}{U_k}\right)^2 \right)$$

---

## 1. The 4 Domain Evaluation Profiles & 8 Bi-Polar Specialist Craft Lenses (4 *Verga* + 4 *Chrysos*)

| Lens ID | Pole & Profiles | Primary Deterministic Artifact Probes (`src/sovereign_kb/gwi.py`) | Hard Veto Threshold | Required Contact Sheet / Evidence |
| :--- | :--- | :--- | :--- | :--- |
| **`technical-artist`** | *Verga* (`app` 3D, `sim`) | `renderer.info` (`draws <= 180`), frame `p95 <= 16.6ms`, KTX2/Draco compression, camera $\notin$ mesh AABB, Rec.709 luminance ($\mu_Y \in [18, 215], \sigma_Y \ge 14$), Sobel edge & 6-tile MSE | `draws > 300`, `p95 > 33.3ms`, black/blown canvas, or unseeded `Math.random()` | 6-Pose Canonical Orbit Sheet (`1920x1080`) |
| **`ux-ergonomics`** | *Verga* (`app`, `sim` HUD) | 2D HUD $\cap$ 3D canvas occlusion ($\le 18\%$), linearized WCAG 2.1 contrast ($\ge 4.5:1$), `0px` horizontal overflow across `390/768/1440px`, and **Subtractive Laws (`L0`–`L8`)** | HUD occlusion $> 25\%$, contrast $< 4.5:1$, `headerRows > 1`, `expandedTablesVisible > 1`, `controls > 36` ($S_{L8}$), or `edge_density > 0.36` | 3-Viewport Responsive Sheet (`390` / `768` / `1440px`) |
| **`spatial-cinema`** | *Verga* (`video`, `app`/`sim` Audio) | `ffmpeg -af ebur128` (`I` $\in [-16, -13]\text{ LUFS}$, `TP` $\le -1.0\text{ dBTP}$, `LRA` $\ge 2.0\text{ LU}$), `.srt` reading speed ($\text{CPS} \le 20$), 9-Frame tile MSE $\ge 25.0$, companion `<20MB` MP4 (`sc.companion_size`) | Silent/clipped audio, flat sine drone (`LRA < 0.2`), `.srt` overlap / $\text{CPS} > 28$, frozen frames, or missing `<20MB` companion MP4 | 9-Frame Shot Sequence + Waveform Strip |
| **`typography-editor`** | *Verga* (`paper`) | LaTeX `.log` `Overfull \hbox <= 3.0pt`, `0` undefined refs/citations, `0` raw Markdown leakage (`**bold**`), Poppler `pdffonts` (`0` Type-3 bitmap fonts), `pdfimages >= 200 PPI` | `Overfull \hbox > 10pt`, undefined citation, leaked `**bold**`, any `Type 3` bitmap font, or `< 120 PPI` figure | 12-Page-per-Sheet Thumbnail Grid |
| **`web-perf-edge`** | *Chrysos* | `wp.barrel_waterfalls` (no wildcard icon-library imports), `wp.listener_memory_hygiene`, `wp.edge_bundle_budget` ($\le 2.5\text{ MB}$) | Barrel `import * as` from `lucide-react` / `@mui/icons-material` / `@heroicons/react` | — |
| **`data-api-architect`** | *Chrysos* | `da.destructive_sql_guard` (no unguarded `DROP`/`DELETE`), `da.rls_index_hygiene`, `da.api_contract_schema` | Unguarded `DELETE FROM x;` or `DISABLE ROW LEVEL SECURITY` | — |
| **`ai-ml-science`** | *Chrysos* | `ai.deterministic_seeds` (no legacy `google.generativeai`), `ai.gx10_cluster_guard`, `ai.reproducible_artifacts` | Legacy `google.generativeai` import or unconfirmed cluster service stop/restart | — |
| **`principal-verification`** | *Chrysos* (all profiles) | `pv.lockfile_reproducibility`, `pv.secret_hygiene`, `pv.skill_manifest_binding`, `pv.canonical_anchor_l9` (Law `L9`), `pv.readme_doc_freshness` | Leaked token, missing `SKILL.md`, unanchored sprawl repo, or broken `README.md` paths/commands | `AGENTS.md` skill matrix |

---

## 2. The 10 Subtractive Design, Regularity & Canonical Anchor Laws (`L0`–`L9`)

To eliminate AI kitchen-sink bloat (`M1`–`M7`), structural irregularity, cognitive overload, and repository sprawl, `skb gwi` enforces [The Subtractive Design & Cognitive Load Laws](../src/sovereign_kb/starter_vault/02-operating-system/subtractive-design-laws.md):

- **Law `L0` (Inherit Before You Invent)**: Inspect sibling repositories and inherit their exact Light/Dark default polarity, hex palette, typography, and institutional footer (`ux.sibling_inheritance`).
- **Law `L1` (Single-Row Chrome Budget)**: Top application bars must fit on `1` horizontal row (`headerRows == 1`, `<= 7` items, short 1-word labels, `0` nested sub-badges) with $\le 3$ persistent desktop regions ($1$ on mobile).
- **Law `L2` (Default Reading & Copy Budget)**: Default executive/AI answers must contain $\le 2$ concise bullets (`maxAnswerBullets <= 2`, $\le 80$ words); sidebar navigation items must be `1` single line; inspector cards $\le 2$ lines.
- **Law `L3` (Representation Economy)**: Never quadruple-encode the same metric across prose + KPIs + table + citation cards simultaneously. Gate expanded analytical tables to the latest turn only (`expandedTablesVisible <= 1`) and use compact inline citation pills.
- **Law `L4` (Two-Sided Visual Density Window)**: Maintain $\ge 30\%$ open canvas ($\ge 78\%$ clear 3D viewport) and keep perceptual edge density within `0.015 <= edge_density <= 0.36`.
- **Law `L5` (Visual Vocabulary & Emphasis Budget)**: At most `1` solid primary CTA per viewport region, `0` nested badges, $\le 2$ font families, and $\le 3$ font weights.
- **Law `L6` (Progressive Disclosure & Subtractive Cut Pass)**: Satisfy 100% Master Feature Catalog parity via reachability ($\le 1\text{–}2$ clicks) rather than simultaneous first-paint clutter, followed by a mandatory **Subtractive Cut Pass**.
- **Law `L7` (Simplicity Favors Regularity — *Η Απλότητα Ευνοεί την Κανονικότητα*)**: One canonical typographic/spatial scale: `distinctFontSizes <= 8`, `distinctBorderRadii <= 4`, `distinctGridTemplates <= 5` ($\mathcal{R}_{\text{kanon}} = 1.0$; veto above 12 / 6 / 7; probe `ux.regularity_kanonikotita`).
- **Law `L8` (First-Paint Simultaneous Cognitive Load Budget — *Ο Νόμος του Ταυτόχρονου Γνωστικού Φορτίου*)**: `<= 24` visible interactive controls (veto `> 36`), `<= 4` choice groups (veto `> 6`), `<= 3` macro-regions (veto `> 4`), chrome-to-content ratio `<= 0.65` (veto `> 1.10`), no default `split` view mode, no sub-44px inline targets (probe `ux.simultaneous_load_l8`).
- **Law `L9` (Canonical Anchor Over New-Repo & Subdomain Sprawl)**: Mount feature wedges (`/snap`, `/moment`, `?room=204`, `/card`) inside canonical product monorepos rather than spawning standalone repos (probe `pv.canonical_anchor_l9`).

---

## 3. CLI Usage (`skb gwi` / `gwi` & `skb skills`)

```bash
# Inspect all 8 Bi-Polar Craft Lenses (or active lenses for a specific repo)
skb gwi lenses
skb gwi lenses ~/Projects/my-app

# Run the 9+ Goldwood Gate with strict Principal-Grade Artifact Probes & write evidence/goldwood.json
skb gwi audit . --strict --strict-lenses --write-evidence
skb goldwood audit .
skb audit .

# Run the standalone Law L7 Structural Regularity (Kanonikotita) audit
skb gwi regularity .

# Audit an entire portfolio of repositories
gwi portfolio

# Compute exact barrier-penalized G(S) and closed-form Jacobian sensitivity dG/dS
skb gwi calc 9.6 9.5 9.8 9.7 --weights app

# Generate a pre-loaded adversarial audit prompt for an independent auditor model
skb gwi prompt . --agent astra

# Inspect, verify, or inject archetype skills into AGENTS.md
skb skills
skb skills --verify
skb skills --inject .
```
