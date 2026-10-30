---
title: The Autonomous Scientific Paper Engineering Framework
type: operating-system
updated: 2026-10-22
tags: [scientific-papers, latex, ieeetran, arxiv, research, simulation, reproducibility, tri-council, reviewer-2, bibliography, booktabs]
related:
  - "[[INDEX]]"
  - "[[00-identity/cognitive-and-working-style]]"
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[02-operating-system/archetypes/README-archetypes]]"
  - "[[02-operating-system/templates/TEMPLATE-scientific-paper-AGENTS]]"
  - "[[03-life-domains/research-and-publications]]"
---

# The Autonomous Scientific Paper Engineering Framework

Synthesized from empirical systems benchmarking, stochastic queueing networks, and formal axiomatic treatises, this framework defines the **10-Pillar Standard for Autonomous Q1 Scientific Paper Engineering** (IEEE Transactions / Letters / INFOCOM / ICC, ACM SIGCOMM / MobiCom, NeurIPS / ICML, Nature/Elsevier Q1, and arXiv).

---

## Core Philosophy: Code-First, Falsifiable, Zero-Hallucination Science

> *"A scientific manuscript is a compiled view over executable mathematical proofs, deterministic simulations, and verifiable empirical telemetry. No number may appear in prose or tables unless traced to `results/*.json`; no citation may enter `references.bib` without API/DOI verification; and no theorem is complete until audited by an independent adversarial model and cross-validated against discrete-event or numerical simulation."*

---

## The 10 Pillars of Autonomous Scientific Paper Engineering

### Pillar 1 — Problem Formulation, Gap Matrix & Falsifiable Contributions Contract (`PAPER_SPEC.md`)
Before writing a single paragraph of `.tex`, the agent must lock the paper's contract in `PAPER_SPEC.md`:
1. **SOTA Gap Matrix (Table I or §II)**:
   - Compare the proposed work against $5\text{--}8$ state-of-the-art references across $5\text{--}6$ concrete technical dimensions (e.g., *Closed-form transient delay*, *Heterogeneous hardware telemetry*, *Memory-cliff analytical bound*, *Energy per token $[\text{J/tok}]$*, *Sub-millisecond complexity*).
   - The final row (**Ours**) must demonstrably resolve the exact structural intersection left open by prior work.
2. **Falsifiable Contributions Contract (§I)**:
   - The final subsection of §I (`\subsection{Key Contributions}`) must contain $3\text{--}4$ bullet points.
   - **Ban Weak Verbs**: Never write *"we discuss"*, *"we explore"*, or *"we study"*.
   - **Mandatory Triple Structure**:
     $$\text{[Action Verb: Derive / Prove / Architect / Benchmark]} \longrightarrow \text{[Exact Mathematical or System Artifact]} \longrightarrow \text{[Quantified Empirical Gain with Units]}$$
3. **Claim-to-Evidence Traceability Map**:
   - Every quantitative metric stated in the Abstract and §I must map 1-to-1 to a specific `Theorem/Proposition`, `Algorithm`, `Table`, or `Figure` in §IV–§V.

---

### Pillar 2 — Mathematical Rigor, Notation Ledger & Dimensional Analysis
1. **Single Source of Notation (`notation.tex` / Table of Notation)**:
   - Every symbol is defined once before first use and registered in a centralized Notation Table:
     - **Scalars**: italic ($x, \lambda, \mu, T, L_{\text{in}}$)
     - **Vectors**: bold lowercase ($\mathbf{x}, \boldsymbol{\theta}, \mathbf{q}$)
     - **Matrices / Tensors**: bold uppercase ($\mathbf{W}, \mathbf{J}, \mathbf{\Sigma}$)
     - **Sets / Spaces / Graphs**: calligraphic or blackboard bold ($\mathcal{S}, \mathcal{G}=(\mathcal{V},\mathcal{E}), \mathbb{R}^d$)
     - **Random Variables vs. Realizations**: uppercase $X$ vs. lowercase $x$, with explicit expectation/probability operators ($\mathbb{E}[\cdot], \mathbb{P}(\cdot), \text{Var}(\cdot)$).
2. **Numbered Assumption Ledger ($A_1, \dots, A_k$)**:
   - Every modeling assumption (e.g., *Poisson arrival process*, *ergodicity*, *Lipschitz gradient continuity*, *non-preemptive service*) is declared inside an explicit `\begin{assumption}...\end{assumption}` environment and referenced explicitly by each `Theorem`/`Lemma` that invokes it.
3. **3-Check Formal Proof Gate**:
   - **Check A — Dimensional Homogeneity**: Every equation is verified for unit balance across LHS and RHS (e.g., $[\text{J/tok}] = [\text{W}] \cdot [\text{s/tok}]$, $[\text{GB/s}]$, dimensionless utilization $\rho = \lambda / (c\mu) \in [0, 1)$).
   - **Check B — Asymptotic & Boundary Limits**: Every closed-form expression is evaluated at extreme regimes ($\lambda \to 0^+$, $\rho \to 1^-$, $N \to \infty$, $\text{SNR} \to 0$, $B \to 1$, $M \le M_{\text{VRAM}}$ vs. $M > M_{\text{VRAM}}$) to confirm reduction to known classical laws (Little's Law, Pollaczek–Khinchine, M/M/1, Roofline ceiling).
   - **Check C — Existence, Uniqueness & Stability**: For queueing networks, dynamical systems, or fixed-point equations, prove positivity, uniqueness, and stability (Lyapunov function or Jacobian spectral radius $\rho(\mathbf{J}) < 1$).

---

### Pillar 3 — Code-First Reproducible Simulation & Empirical Data Pipeline
**Zero-Synthetic-Data Rule**: Agents are strictly forbidden from typing fabricated numbers into LaTeX tables or hardcoding synthetic arrays inside `plot_*.py` scripts.
1. **Dual-Validation Pattern (Analytical Theory $\leftrightarrow$ Simulation / Telemetry)**:
   - Every analytical equation or bound must be cross-validated against either:
     - an independent **Discrete-Event / Monte Carlo / ODE Simulator** (`sim/run_sim.py` using `SimPy`, Gillespie SSA, or RK4), or
     - **Real Hardware Telemetry** across physical compute nodes.
   - Plots must overlay **Analytical Curves** (solid lines) against **Empirical / Simulated Markers** (markers with error bars) and report the Mean Absolute Percentage Error (MAPE).
2. **Statistical Rigor**:
   - Pin all seeds (`PYTHONHASHSEED=42`, `np.random.default_rng(42)`).
   - Execute $R \ge 5\text{--}30$ independent runs per configuration.
   - Report **Mean $\pm$ SD** or **95% Confidence Intervals** (and **P50 / P95 / P99** for tail latency metrics such as $\text{TTFT}$ and $\text{TPOT}$).
3. **Immutable Artifact Pipeline (`sim/` $\to$ `results/*.json` $\to$ `figures/*.pdf` & `tables/*.tex`)**:
   - Simulation and benchmark scripts write structured JSON artifacts (`results/metrics.json`) containing environment metadata, parameters, and raw/aggregated metrics.
   - Plotting and table-generation scripts read exclusively from `results/*.json`.

---

### Pillar 4 — Publication-Grade Visuals, Typography & Booktabs Engineering
1. **100% Vector Figures (`figures/*.pdf`)**:
   - All plots, rooflines, queueing diagrams, and system architectures must be exported as vector `.pdf` (or native `TikZ`/`PGFPlots`). Raster `.png`/`.jpg` files are prohibited except for hardware photographs or UI screenshots at $\ge 300\text{ DPI}$.
2. **IEEE PDF eXpress Type-42 Font Embedding Rule**:
   - Every `matplotlib` script **must** enforce TrueType (Type 42) font embedding to prevent Type 3 bitmap font rejections on IEEE PDF eXpress / PaperPlaza and arXiv:
     ```python
     import matplotlib.pyplot as plt

     plt.rcParams.update(
         {
             "pdf.fonttype": 42,  # TrueType font embedding (REQUIRED by IEEE)
             "ps.fonttype": 42,
             "font.family": "serif",
             "font.size": 9,
             "axes.labelsize": 9,
             "legend.fontsize": 8,
             "xtick.labelsize": 8,
             "ytick.labelsize": 8,
         }
     )
     ```
3. **Colorblind-Safe & Grayscale-Distinct Styling**:
   - Use the **Okabe-Ito** palette (`#0072B2`, `#D55E00`, `#009E73`, `#CC79A7`, `#F0E442`, `#56B4E9`) paired with distinct line styles (`-`, `--`, `-.`, `:`) and markers (`o`, `s`, `^`, `D`) so every curve remains distinguishable when printed in black-and-white.
4. **Booktabs Table Standard**:
   - Never use vertical rules (`|`) or double `\hline\hline`.
   - Always use `\toprule`, `\midrule`, `\bottomrule` (`\usepackage{booktabs}`), explicit measurement units in column headers (e.g., `Latency (ms)`, `Energy (J/tok)`), **bold** for best performance, and <u>underline</u> for second-best.
5. **Algorithmic Pseudocode Standard**:
   - Use `algorithm` + `algpseudocode` (`algorithmicx`), with explicit `\Require` (Inputs), `\Ensure` (Outputs), line numbers, and inline time/space complexity annotations $\mathcal{O}(\cdot)$.

---

### Pillar 5 — Zero-Hallucination Bibliographic Verification Gate (`references.bib`)
1. **Programmatic Citation Verification (`scripts/verify_bib.py`)**:
   - Every entry in `references.bib` must correspond to a real, verifiable publication (checked via Crossref API, DOI.org, arXiv API, Semantic Scholar, or DBLP).
   - **Zero Undefined or Unused Keys**: Every `\cite{key}` in `.tex` must exist in `references.bib`, with zero `?` markers in the compiled PDF.
   - **Acronym Case Protection**: Protect technical acronyms in BibTeX `title` fields using double braces (`{{GPU}}`, `{{LLM}}`, `{{3GPP}}`, `{{M/M/1}}`, `{{RNN}}`, `{{IoT}}`, `{{QoS}}`) so `IEEEtran.bst` does not lowercase them.
   - **Canonical Venue Preference**: When an arXiv preprint has been published in a peer-reviewed journal or conference (e.g., IEEE ToN, INFOCOM, NeurIPS), cite the canonical published venue with `volume`, `number`, `pages`, `year`, and `doi`.

---

### Pillar 6 — Section-by-Section Structural Blueprint (IMRaD + Formal System Architecture)
1. **Title & Abstract (150–250 words, 5-sentence contract)**:
   - *(S1) Domain & Context* $\to$ *(S2) Concrete Theoretical/Systems Bottleneck* $\to$ *(S3) Core Mechanism/Analytical Contribution ("In this paper, we derive/architect...")* $\to$ *(S4) Exact Quantitative Gains* $\to$ *(S5) Regime Insight / Takeaway*.
2. **§I Introduction**:
   - High-stakes motivation $\to$ Why SOTA fails (fundamental trade-off or capacity cliff) $\to$ Core insight $\to$ Bulleted **Key Contributions** $\to$ SOTA Gap Matrix (Table I) or System Architecture (Fig. 1).
3. **§II Related Work**:
   - Grouped into $2\text{--}4$ thematic taxonomy streams (never a chronological laundry list). Every subsection ends with a contrastive paragraph (*"Unlike [11]–[15], which assume..."*).
4. **§III System Model & Problem Formulation**:
   - Formal mathematical state space, queueing/hardware/network model, Notation Table, Assumptions $A_1\text{--}A_k$, and optimization problem $(\mathcal{P}_1)$ with explicit constraints.
5. **§IV Theoretical Analysis / Proposed Architecture**:
   - Lemmas, Theorems, Proofs (or Proof Sketches in the main text with complete step-by-step derivations in the Appendix), Algorithms, and complexity/convergence proofs.
6. **§V Experimental / Numerical Evaluation**:
   - Opens with explicit **Research Questions ($RQ_1\text{--}RQ_4$)**; provides a complete **Hardware/Simulation Parameter Table**; evaluates against SOTA baselines and ablations; explains the physical/mathematical mechanism behind every inflection point, crossover, or capacity cliff.
7. **§VI Limitations, Threats to Validity & Conclusion**:
   - Proactively states regime boundaries and modeling assumptions (neutralizing hostile Reviewer #2 critiques), followed by a crisp quantitative synthesis.

---

### Pillar 7 — Multi-Model Tri-Council Scientific Workflow & Adversarial "Reviewer #2" Gate
Never allow the model that drafted a section or proof to self-approve it. Apply the **4-Tier Scientific Council**:

| Tier | Scientific Role | Primary Responsibilities |
| :--- | :--- | :--- |
| **Tier 1** | **Simulation, Plotting & LaTeX Worker** | Writes `sim/run_sim.py`, `scripts/plot_figures.py` (Type-42 PDF), `booktabs` tables, `verify_bib.py`, and arXiv packaging. |
| **Tier 2** | **Reviewer #1 — Narrative, Novelty & Positioning** | Audits Abstract-to-Conclusion alignment, SOTA Gap Matrix sharpness, rhetoric flow, and conceptual originality. |
| **Tier 3** | **Reviewer #2 — Formal Proofs, Math & Baselines** | Line-by-line proof verification, hidden assumption hunting, dimensional/limit checks, baseline fairness, and statistical rigor. |
| **Tier 4** | **Cross-Artifact Consistency Auditor (262k ctx)** | Ingests full `.tex` + `results/*.json` + `sim/*.py` simultaneously to verify zero numerical or symbolic drift between code and prose. |

All findings from Tiers 2–4 are logged in `AUDIT_LEDGER.md` (see [[02-operating-system/templates/TEMPLATE-scientific-paper-AGENTS]]) with unique IDs (`AUD-01..N`), severity (`CRITICAL`, `MAJOR`, `MINOR`), exact mathematical/text resolution, and verification status (`RESOLVED`).

---

### Pillar 8 — 6-Stage Deterministic LaTeX Quality Gate (`scripts/quality_gate.sh`)
No manuscript commit or release is complete until `./scripts/quality_gate.sh` exits `0` across all 6 stages:
1. **Stage 1 — Git & Artifact Hygiene**: Zero merge conflict markers (`<<<<<<<`) and zero tracked LaTeX build artifacts (`.aux`, `.bbl`, `.blg`, `.fdb_latexmk`, `.fls`, `.log`, `.synctex.gz`).
2. **Stage 2 — Reproducible Simulation & Figure Pipeline**: Executes `PYTHONHASHSEED=42 uv run python sim/run_sim.py` and `uv run python scripts/plot_figures.py`, verifying `results/*.json` and non-empty vector `figures/*.pdf`.
3. **Stage 3 — Bibliographic & Claim Integrity**: Runs `uv run python scripts/verify_bib.py` to confirm 100% citation resolution, acronym brace protection, and zero duplicate BibTeX keys.
4. **Stage 4 — Non-Interactive PDF Compilation**: Runs `latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex`.
5. **Stage 5 — Zero-Warning & Page-Budget Gate**:
   - Asserts zero `LaTeX Warning: (Citation|Reference).*undefined` and zero `multiply-defined labels` in `paper.log`.
   - Asserts zero `Overfull \hbox` exceeding `3.0pt`.
   - Checks page count via `pdfinfo paper.pdf` against the target venue budget.
6. **Stage 6 — IEEE Font Embedding & Visual Contact-Sheet Gate**:
   - Runs `pdffonts paper.pdf` to verify **zero `Type 3` bitmap fonts** and **100% font embedding (`emb = yes`)**.

---

### Pillar 9 — Dual-Track Manuscript Architecture & Automated Submission Packaging
1. **Dual-Track Compilation**:
   - **Track A (`paper_ieee.tex`)**: Tight two-column `IEEEtran` manuscript adhering strictly to conference/letter page limits, with proof sketches in main text.
   - **Track B (`paper.tex` / `paper_full.tex`)**: Extended journal / arXiv preprint containing complete step-by-step appendix derivations, extended parameter sweeps, and reproducibility documentation.
2. **One-Command Submission Bundle (`scripts/package_arxiv.py`)**:
   - Generates a clean, flat `overleaf_arxiv_bundle.zip` / `arxiv_submission.tar.gz` containing `.tex`, pre-compiled `.bbl`, custom `.sty`/`.cls`, and only referenced `figures/*.pdf`.

---

### Pillar 10 — Peer-Review Rebuttal & Camera-Ready Protocol (`RESPONSE_TO_REVIEWERS.md`)
During journal/conference revision rounds:
1. Every reviewer comment is decomposed and assigned an immutable ID (`R1.C1`, `R1.C2`, `R2.C1`, `AE.1`).
2. Each response follows a mandatory **4-Part Structure**:
   - **1. Executive Disposition**: Polite, direct first sentence stating the exact action taken.
   - **2. Technical & Mathematical Answer**: Self-contained proof, derivation, or new empirical table directly inside the response letter.
   - **3. Verbatim Manuscript Diff Quote**: Boxed/colored quote showing the exact added/revised text with Section, Page, Column, and Line references.
   - **4. Traceability Link**: Pointer to the exact simulation script (`sim/`) and `AUDIT_LEDGER.md` entry verifying the revision.
