---
title: "Template: Scientific Paper AGENTS.md, PAPER_SPEC & Audit Harness"
type: template
updated: 2026-10-22
tags: [template, scientific-papers, latex, ieeetran, arxiv, reviewer-2, audit-ledger, bootstrap]
related:
  - "[[INDEX]]"
  - "[[02-operating-system/scientific-paper-framework]]"
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[02-operating-system/archetypes/README-archetypes]]"
  - "[[03-life-domains/research-and-publications]]"
---

# Template: Scientific Paper `AGENTS.md`, `PAPER_SPEC.md` & Audit Harness

Use `sovereign-kb scaffold paper-ieee-arxiv <target_dir>` (see [[02-operating-system/archetypes/README-archetypes]] and [[02-operating-system/scientific-paper-framework]]) to automatically generate this complete scientific paper harness, or copy the templates below into any research repository.

---

## 1. Root `AGENTS.md` Template for Scientific Papers

````markdown
# AGENTS.md — Autonomous Scientific Paper Engineering Guide

**Repository:** `<PAPER_SLUG>`
**Target Venue & Track:** `<e.g., IEEE Transactions on Networking (ToN) / IEEE INFOCOM / arXiv cs.DC>`
**Page Budget:** `<e.g., 6 pages IEEE Letter / 8 pages Conference / 14 pages Extended arXiv>`
**Framework Standard:** `02-operating-system/scientific-paper-framework.md`

---

## 🎯 High-Level Scientific Operating System

### 1. Multi-Model Tri-Council Scientific Workflow
| Tier | Role | Responsibilities |
| :--- | :--- | :--- |
| **Tier 1** | **Simulation, Plotting & LaTeX Worker** | Implements `sim/run_sim.py`, `scripts/plot_figures.py` (Type-42 PDF), `booktabs` tables, `verify_bib.py`, and arXiv bundle packaging. |
| **Tier 2** | **Reviewer #1 — Narrative, Novelty & Positioning** | Audits Abstract-to-Conclusion alignment, SOTA Gap Matrix sharpness, related work taxonomy, and conceptual clarity. |
| **Tier 3** | **Reviewer #2 — Formal Proofs, Math & Baselines** | Line-by-line proof verification, dimensional homogeneity, asymptotic boundary limits, baseline fairness, and statistical rigor. |
| **Tier 4** | **Cross-Artifact Consistency Auditor (262k ctx)** | Full-manuscript + `results/*.json` + `sim/*.py` sweep to verify zero numerical or symbolic drift between code and prose. |

### 2. Non-Negotiable Scientific Integrity Rules
1. **Zero Synthetic Numbers**: Never hardcode fabricated numbers into `.tex` tables or `plot_*.py` scripts. Every empirical claim must trace to `results/*.json` produced by `sim/run_sim.py` or hardware benchmark scripts.
2. **Type-42 Vector Figures Only**: All figures must be vector `.pdf` files generated with `plt.rcParams['pdf.fonttype'] = 42` and `plt.rcParams['ps.fonttype'] = 42` (zero Type 3 bitmap fonts) using the colorblind-safe Okabe-Ito palette.
3. **Zero-Hallucination Bibliography**: Every citation in `references.bib` must be verified via `scripts/verify_bib.py`, with technical acronyms protected in double braces (`{{GPU}}`, `{{LLM}}`, `{{3GPP}}`).
4. **6-Stage Deterministic Quality Gate**: Never declare a draft or revision complete until `./scripts/quality_gate.sh` exits `0` and all entries in `AUDIT_LEDGER.md` are marked `RESOLVED`.

---

## ⚡ Standard Verification & Packaging Commands

```bash
./scripts/quality_gate.sh                  # Full 6-Stage LaTeX + Sim + Bib + Font + Page-Budget Gate
uv run python sim/run_sim.py               # Run deterministic simulations -> results/metrics.json
uv run python scripts/plot_figures.py      # Regenerate Type-42 vector PDFs -> figures/*.pdf
uv run python scripts/verify_bib.py        # Audit references.bib against .tex citations
uv run python scripts/package_arxiv.py     # Build overleaf_arxiv_bundle.zip & ARXIV_METADATA.txt
```
````

---

## 2. `PAPER_SPEC.md` Template (Problem, Gap Matrix, Notation & Claims)

````markdown
# PAPER_SPEC.md — Mathematical & Empirical Contract

## 1. Core Thesis & Research Questions
- **Primary Thesis**: `<1-sentence falsifiable core thesis>`
- **$RQ_1$ (Analytical / Scaling Regime)**: `<Question 1>`
- **$RQ_2$ (Latency / Throughput / Convergence)**: `<Question 2>`
- **$RQ_3$ (Energy / Memory / Resource Cliff)**: `<Question 3>`

## 2. SOTA Gap Matrix (Table I Blueprint)
| Reference | Analytical Bound | Non-Stationary / Cliff Regime | Real Hardware Telemetry | Energy Efficiency ($\text{J/tok}$) | Complexity |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Prior Baseline A | ✅ | ❌ | ❌ | ❌ | $\mathcal{O}(N^2)$ |
| Prior Baseline B | ❌ | ✅ | ✅ | ❌ | $\mathcal{O}(N \log N)$ |
| **Ours** | ✅ | ✅ | ✅ | ✅ | $\mathcal{O}(N)$ |

## 3. Falsifiable Contributions Contract (§I)
1. **Contribution 1 (Formal Model / Theorem)**: `[Derive/Prove] -> [Artifact] -> [Closed-form bound / stability guarantee]`
2. **Contribution 2 (Algorithm / Architecture)**: `[Architect/Design] -> [Mechanism] -> [Complexity & operational guarantee]`
3. **Contribution 3 (Empirical / Simulation Validation)**: `[Benchmark/Validate] -> [Dataset / Hardware Cluster] -> [Quantified speedup/reduction from results/metrics.json]`
````

---

## 3. `AUDIT_LEDGER.md` Template (Tri-Council Adversarial Review)

````markdown
# AUDIT_LEDGER.md — Tri-Council Adversarial Review & Verification

| Audit ID | Auditor Role | Severity | Section / Equation | Finding Description | Resolution Applied | Status |
| :--- | :--- | :---: | :--- | :--- | :--- | :---: |
| **AUD-01** | Reviewer #2 (Formal Math) | `CRITICAL` | §III, Thm. 1 | Verify boundary limit as $\rho \to 1^-$ and explicit stability condition $\lambda < c\mu$. | Added Assumption $A_1$ and boundary limit proof in Appendix A; verified in `sim/run_sim.py`. | `RESOLVED` |
| **AUD-02** | Reviewer #1 (Positioning) | `MAJOR` | §I & Abstract | Align Abstract numerical claims with Table II confidence intervals. | Synced Abstract metrics directly with `results/metrics.json`. | `RESOLVED` |
| **AUD-03** | Cross-Artifact Auditor | `MINOR` | `references.bib` | Protect acronyms `{{GPU}}` and `{{LLM}}` in BibTeX titles. | Updated `references.bib` and verified via `scripts/verify_bib.py`. | `RESOLVED` |
````
