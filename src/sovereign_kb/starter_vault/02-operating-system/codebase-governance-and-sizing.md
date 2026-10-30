---
title: Codebase Governance, IP Sizing & Duplication Auditing
type: operating-system
updated: 2026-10-22
tags: [governance, ip-audit, sizing, scc, jscpd, knip, code-quality]
related:
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[03-life-domains/ventures-and-engineering]]"
---

# Codebase Governance, IP Sizing & Duplication Auditing

This document defines how we measure, govern, and audit codebase health, proprietary IP volume, and structural duplication across multi-repository engineering portfolios.

---

## 1. Static Analysis & IP Sizing Methodology

Every production codebase is evaluated using three deterministic static analysis engines:
1. **`scc` (Sloc, Cloc and Code)**: High-speed AST/line counter measuring Gross Files, Gross Lines, Blank Lines, Comments, and **Gross Logical Lines of Code (LOC)**.
2. **`jscpd` (Copy/Paste Detector)**: Token-based structural clone detector measuring duplicated lines, duplicated tokens, and **Structural Duplication Percentage**.
3. **`knip` (Dead Code & Unused Export Detector)**: Enforced in TypeScript/JavaScript repositories to guarantee **0 unused files, 0 unused dependencies, and 0 dead exports**.

---

## 2. Proprietary IP Accounting Formula

To distinguish true first-party engineering from vendored assets, generated code, or third-party templates, every audit strictly excludes non-authored directories (`node_modules`, `dist`, `build`, `.next`, `vendor`, `Library`, `Packages`, lockfiles, and auto-generated migrations/OpenAPI stubs) and applies the **Net Authored Proprietary IP Formula**:

$$\text{Net Authored IP LOC} = \text{Gross Logical LOC} - \text{Non-Authored Deductions (Vendored / Generated / Boilerplate)}$$

### Quality Benchmarks & Ceilings
- **Structural Duplication Ceiling**: Must remain **$< 8.0\%$** weighted average across the portfolio (with systems/Rust kernels targeting $< 4.5\%$).
- **Non-Authored Deduction Ratio**: Keep vendored/generated boilerplate below **$8.0\%$** of gross logical lines.
- **Conflict & Secret Hygiene**: Zero merge conflict markers (`<<<<<<<`) and zero hardcoded secret tokens in `HEAD`.
