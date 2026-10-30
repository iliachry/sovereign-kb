---
title: "AGENTS.md — Sovereign Knowledge Base & Autonomous Project Factory"
type: operating-system
updated: 2026-10-29
tags: [agents, codex, rules, verification, scaffolding, gwi, subtractive-design]
related:
  - "[[INDEX]]"
  - "[[00-identity/README-me]]"
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
  - "[[02-operating-system/subtractive-design-laws]]"
  - "[[02-operating-system/lenses/README-lenses]]"
  - "[[02-operating-system/scientific-paper-framework]]"
  - "[[02-operating-system/archetypes/README-archetypes]]"
---

# AGENTS.md — Sovereign Knowledge Base Vault

This directory is a **Sovereign Personal Knowledge Base & Autonomous Project Factory** managed by `sovereign-kb` (`skb`).

## Core Directives for AI Agents
1. **Read Tier-0 Context First**: Consult [[00-identity/README-me]], [[00-identity/cognitive-and-working-style]], and [[01-now/now]] before making architectural recommendations.
2. **Keep the Index & Graph Synchronized**:
   - Whenever adding or editing Markdown notes in this vault, maintain YAML frontmatter (`title`, `type`, `updated`, `tags`, `related`) and valid `[[wikilink]]` edges.
   - Run `sovereign-kb sync` and `sovereign-kb verify` (6-Gate Vault Doctor) after modifying any `.md` file.
3. **Bootstrap New Projects & Scientific Manuscripts Deterministically**:
   - Use `sovereign-kb scaffold <archetype> <target_dir>` (`web-ts-3d-gis`, `python-uv-ai`, `rust-systems`, `spatial-xr-ble`, `paper-ieee-arxiv`, `game-sim-3d`, `metatopia-chameleon`) to initialize new repositories with the 6-Pillar [[02-operating-system/autonomous-engineering-framework|Deterministic Autonomous Engineering Framework]], the [[02-operating-system/goldwood-index-gwi-standard|Unified Goldwood Index (GWI+ v1.4.0) Standard]], the [[02-operating-system/subtractive-design-laws|10 Subtractive Design Laws (L0–L9)]], or the 10-Pillar [[02-operating-system/scientific-paper-framework|Autonomous Scientific Paper Engineering Framework]].
4. **Never Commit SQLite Index or Media Binaries**: `.kb/index.db*`, `__pycache__/`, and `*.mp4` are ignored in `.gitignore`.
