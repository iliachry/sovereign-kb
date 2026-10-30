---
title: "Soar 9.6 Symbolic & Neuro-Symbolic Cognitive Architecture (skb soar)"
type: operating-system
tier: 1
updated: 2026-10-29
tags: [soar, cognitive-architecture, neuro-symbolic, chunking, rete, wme, impasse, rsi]
related:
  - "[[INDEX]]"
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
---

# Soar 9.6 Symbolic & Neuro-Symbolic Cognitive Architecture (`skb soar`)

Integrates Allen Newell's **Unified Theory of Cognition (`Soar 9.6`)** with **Sovereign-KB (`skb`)** and the **Autonomous Software Machine (`swm`)** to complete the **4-Memory Cognitive Architecture**:
1. **Semantic Memory**: Vault notes (`00-identity/`, `02-operating-system/`, `03-life-domains/`) indexed via 4-Way Hybrid Retrieval (`hips` Block-Max WAND + FTS5 + Vectors + `[[Wikilink]]` Graph) in [[02-operating-system/autonomous-engineering-framework]].
2. **Episodic Memory**: Chronological captures (`04-episodic/`) and the SHA-256 hash-chained Lyapunov ledger (`.kb/rsi-ledger.jsonl`).
3. **Working Memory (`WM`)**: Directed graph of Working Memory Elements (`WMEs`, `(id ^attr value)`) rooted at `S1`, governed by the **Truth Maintenance System (`TMS`)** separating monotonic **`i-support`** elaborations from persistent **`o-support`** operator mutations.
4. **Procedural Memory (`sp { ... }`)**: Executable Soar production rules embedded in vault Markdown fences (` ```soar `) and **Explanation-Based Chunking (`chunk*`)** rules compiled automatically in `.kb/learned_chunks.soar`.

---

## 1. The 5-Phase Decision Cycle & 8-Preference Calculus

Every cognitive cycle executes five deterministic phases:
1. **`INPUT`**: Hydrate `(S1 ^io.input-link ...)` with live vault diagnostics, retrieval candidates, and [[02-operating-system/goldwood-index-gwi-standard]] tier scores.
2. **`ELABORATION`**: Fire all matching `i-support` productions and operator proposals `(<s> ^operator <o> +)` until quiescence.
3. **`DECISION`**: Evaluate the 8-preference cascade (`!` Require $\to$ `~`/`-` Prohibit/Reject $\to$ `+` Acceptable $\to$ `>`/`<` Better/Worse $\to$ `>`/`<` Best/Worst $\to$ `=` Indifferent). If no unique winner emerges, create a child **Substate (`S2`)** with `^impasse tie|conflict|constraint-failure|no-change`.
4. **`APPLICATION`**: Fire `:o-support` operator application rules to mutate state WMEs.
5. **`OUTPUT`**: Emit verified actions and compile backtraced **`chunk*` rules** whenever a substate resolves a superstate impasse.

---

## 2. Canonical Vault Governance Production Rules

```soar
sp {skb*elaborate*tier-authority
    "Assign i-supported canonical authority to operating-system notes"
    :i-support
    (state <s> ^name skb-vault ^cand <c>)
    (<c> ^tier operating-system)
-->
    (<c> ^canonical yes)
}

sp {skb*substate*resolve-retrieval-tie
    "In tie substate S2, prefer higher epistemic authority (operating-system > episodic)"
    (state <s2> ^superstate <ss> ^impasse tie ^item <o1> ^item <o2>)
    (<o1> ^name select-candidate ^authority 3)
    (<o2> ^name select-candidate ^authority 1)
-->
    (<ss> ^operator <o1> > <o2>)
}
```
