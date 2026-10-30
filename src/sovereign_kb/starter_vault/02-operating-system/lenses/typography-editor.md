---
title: "Lens L4: Scientific & Literary Typography Editor"
type: operating-system
updated: 2026-10-29
tags: [lens, typography, latex, ieee, arxiv, book-design, print24, editorial, verga]
lens_id: typography-editor
pole: verga
applies_to: ["**/*.tex", "**/*.bib", "**/*.cls", "**/*.sty", "**/figures/*.pdf", "**/book/**", "**/paper*/**"]
applies_when_deps: []
related:
  - "[[02-operating-system/lenses/README-lenses]]"
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
  - "[[02-operating-system/scientific-paper-framework]]"
---

# Lens L4: Scientific & Literary Typography Editor (`typography-editor`)

Evaluates IEEE/ACM/NeurIPS/arXiv research manuscripts and full-length technical books for **micro-typographic poise, vector figure legibility, float placement proximity, and zero markup leakage**.

---

## 1. Deterministic Telemetry Probes & Hard Vetoes

| Probe ID | Metric Inspected | Sovereign Target | Hard Veto Threshold |
| :--- | :--- | :--- | :--- |
| `ty.overfull_hbox` | LaTeX `.log` `Overfull \hbox` count & maximum badness (`pt`) | $0$ overfull boxes $> 3.0\text{ pt}$ | Any `Overfull \hbox` $> 10.0\text{ pt}$ (text protruding into margins/columns) or $> 5$ boxes $> 3.0\text{ pt}$ |
| `ty.undef_citations_refs` | Undefined references (`??`), citations, or duplicate labels in `.log`/`.blg` | $0$ undefined refs/citations; $100\%$ `.bib` parity | Any `LaTeX Warning: Reference ... undefined` or `Citation ... undefined` |
| `ty.markdown_leakage` | Raw unescaped Markdown syntax (`**bold**`, `` `code` ``, `###`) in `.tex` | $0$ leaked Markdown tokens | Any unescaped `**...**` or backtick code fence rendered literally in PDF |
| `ty.pdffonts_type3` | Poppler `pdffonts` & PDF stream font embedding audit | $0$ Type-3 bitmap fonts; $100\%$ embedded Type-1 / TrueType / Type-42 CID vector fonts | Any `Type 3` bitmapped font (`type3_fonts > 0`) or unembedded font in compiled PDF |
| `ty.pdfimages_dpi` | Poppler `pdfimages -list` effective raster print resolution (`x-ppi`, `y-ppi`) | $100\%$ vector `.pdf` figures or raster images $\ge 200\text{ PPI}$ | Any raster figure with effective print resolution $< 120\text{ PPI}$ |
| `ty.widows_float_drift` | Club/widow penalty enforcement & figure distance from first `\ref` | `\widowpenalty=10000`, `\clubpenalty=10000`; float drift $\le 1$ page | Uncontrolled widows/orphans or figures drifting $> 2$ pages after their textual discussion |

---

## 2. Builder Pre-Flight Constraints (Injected into `fable` / `sonnet`)

When a builder agent is dispatched on a repository where `typography-editor` is active:
1. **Microtype & Penalty Guardrails**: Enable `\usepackage{microtype}`, `\widowpenalty=10000`, `\clubpenalty=10000`, and `\emergencystretch=1.5em` so paragraphs set cleanly without margin protrusions.
2. **Booktabs & Vector Figures**: Use `\usepackage{booktabs}` (`\toprule`, `\midrule`, `\bottomrule` — never vertical rules `|`) and export all Matplotlib/TikZ figures as vector PDFs with `pdf.fonttype = 42` and minimum axis/legend font size $\ge 8\text{ pt}$ at final column width.
3. **Equation Punctuation & Notation Parity**: Punctuate display equations as part of the surrounding sentence and ensure every symbol matches the manuscript's Notation Table.

---

## 3. Astra Stage-5 Contact Sheet Rubric (`12-Page Thumbnail Grid`)

Astra inspects the **12-Page-per-Sheet PDF Thumbnail Grid** and must cite `(page_number, column_or_figure_id)` for every finding:
- **Grey-Value Page Balance**: Are figures and tables distributed evenly at the tops of pages (`[t!]`) rather than clumping into awkward half-empty pages?
- **Figure Label Readability**: Are all subfigure labels `(a)`, `(b)`, legends, and annotations crisp and non-overlapping at $100\%$ print zoom?
- **Section & Heading Rhythm**: Are section headings always followed by at least 3 lines of body text on the same column/page?
