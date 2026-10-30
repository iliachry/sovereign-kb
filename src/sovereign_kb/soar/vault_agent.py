"""
Vault-Integrated Soar 9.6 Symbolic & Neuro-Symbolic Cognitive Engine for Sovereign-KB (`skb soar`).

Bridges Sovereign-KB's 4-Way Hybrid Retrieval & 3-Loop RSI Engine with Allen Newell's
Soar Cognitive Architecture:
  1. Fence-aware extraction of executable ```soar production blocks from vault Markdown notes.
  2. Vault-to-WME Graph Projection (`i-support` Truth Maintenance vs `o-support` state mutations).
  3. Impasse-driven RRF Tie-Breaking (`S1 -> S2`) & Explanation-Based Chunking (`chunk*` synthesis).
  4. Canonical AI Benchmark Suite (`water-jugs`, `blocks-world`, `towers-of-hanoi`, `vault-governance`)
     with 0-impasse chunked replay verification and `soarc` Python transpilation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from .ast_nodes import (
    ConjunctiveTest,
    DisjunctionTest,
    EqualityTest,
    Production,
    RHSAction,
    RHSFuncCall,
    RHSFunctionStandalone,
    Var,
)
from .kernel import SoarKernel
from .neuro_symbolic import NeuroSymbolicBridge
from .parser import format_production, parse_soar
from .transpiler import transpile_soar_to_python

WATER_JUGS_SOAR = """
sp {water-jugs*propose*initialize
    "Propose initialization when state has no problem-space name"
    (state <s> ^superstate nil -^name)
-->
    (<s> ^operator <o> + =)
    (<o> ^name init-water-jugs)
}

sp {water-jugs*apply*initialize
    "Initialize the 5-gallon and 3-gallon jugs at 0 gallons"
    :o-support
    (state <s> ^operator <o>)
    (<o> ^name init-water-jugs)
-->
    (<s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 0)
    (<j3> ^volume 3 ^contents 0)
}

sp {water-jugs*elaborate*free-capacity
    "Dynamically compute free capacity of each jug via i-support TMS"
    :i-support
    (state <s> ^name water-jugs ^jug <j>)
    (<j> ^volume <v> ^contents <c>)
-->
    (<j> ^free (- <v> <c>))
}

sp {water-jugs*propose*fill-5-at-start
    "Propose filling the 5-gallon jug when both jugs are empty"
    (state <s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 0)
    (<j3> ^volume 3 ^contents 0)
-->
    (<s> ^operator <o> +)
    (<o> ^name fill-jug ^fill-jug <j5> ^jug-volume 5)
}

sp {water-jugs*propose*fill-3-at-start
    "Competing proposal: fill the 3-gallon jug when both jugs are empty (creates Tie Impasse)"
    (state <s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 0)
    (<j3> ^volume 3 ^contents 0)
-->
    (<s> ^operator <o> +)
    (<o> ^name fill-jug ^fill-jug <j3> ^jug-volume 3)
}

sp {water-jugs*substate*prefer-larger-fill
    "In a tie substate, prefer filling the 5-gallon jug over the 3-gallon jug"
    (state <s2> ^superstate <ss> ^impasse tie ^item <o1> ^item <o2>)
    (<o1> ^name fill-jug ^jug-volume 5)
    (<o2> ^name fill-jug ^jug-volume 3)
-->
    (<ss> ^operator <o1> > <o2>)
}

sp {water-jugs*propose*pour-5-to-3-full
    "When 5G has 5 and 3G has 0, propose pouring 5G into 3G"
    (state <s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 5)
    (<j3> ^volume 3 ^contents 0)
-->
    (<s> ^operator <o> + =)
    (<o> ^name pour-jug ^source-jug <j5> ^dest-jug <j3> ^transfer 3)
}

sp {water-jugs*propose*empty-3-when-full
    "When 5G has 2 and 3G is full (3), propose emptying 3G"
    (state <s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 2)
    (<j3> ^volume 3 ^contents 3)
-->
    (<s> ^operator <o> +)
    (<o> ^name empty-jug ^empty-jug <j3> ^jug-volume 3)
}

sp {water-jugs*propose*empty-5-when-2
    "Competing proposal at (5:2, 3:3): empty 5G (creates Tie Impasse #2)"
    (state <s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 2)
    (<j3> ^volume 3 ^contents 3)
-->
    (<s> ^operator <o> +)
    (<o> ^name empty-jug ^empty-jug <j5> ^jug-volume 5)
}

sp {water-jugs*substate*prefer-empty-3-over-5
    "In a tie substate, prefer emptying the full 3G jug to preserve the 2G remainder in 5G"
    (state <s2> ^superstate <ss> ^impasse tie ^item <o1> ^item <o2>)
    (<o1> ^name empty-jug ^jug-volume 3)
    (<o2> ^name empty-jug ^jug-volume 5)
-->
    (<ss> ^operator <o1> > <o2>)
}

sp {water-jugs*propose*pour-2-from-5-to-3
    "Transfer the 2 gallons from 5G into the empty 3G jug"
    (state <s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 2)
    (<j3> ^volume 3 ^contents 0)
-->
    (<s> ^operator <o> + =)
    (<o> ^name pour-jug ^source-jug <j5> ^dest-jug <j3> ^transfer 2)
}

sp {water-jugs*propose*refill-5-when-3-has-2
    "Refill the empty 5G jug while 3G holds 2 gallons"
    (state <s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 0)
    (<j3> ^volume 3 ^contents 2)
-->
    (<s> ^operator <o> + =)
    (<o> ^name fill-jug ^fill-jug <j5> ^jug-volume 5)
}

sp {water-jugs*propose*pour-1-to-reach-4
    "Pour 1 gallon from 5G into 3G (free=1), leaving exactly 4 gallons in 5G"
    (state <s> ^name water-jugs ^jug <j5> ^jug <j3>)
    (<j5> ^volume 5 ^contents 5)
    (<j3> ^volume 3 ^contents 2 ^free 1)
-->
    (<s> ^operator <o> + =)
    (<o> ^name pour-jug ^source-jug <j5> ^dest-jug <j3> ^transfer 1)
}

sp {water-jugs*apply*fill-jug
    "Apply fill-jug: replace contents with jug volume"
    :o-support
    (state <s> ^name water-jugs ^operator <o>)
    (<o> ^name fill-jug ^fill-jug <j>)
    (<j> ^volume <v> ^contents <c>)
-->
    (<j> ^contents <c> -)
    (<j> ^contents <v>)
}

sp {water-jugs*apply*empty-jug
    "Apply empty-jug: reset contents to 0"
    :o-support
    (state <s> ^name water-jugs ^operator <o>)
    (<o> ^name empty-jug ^empty-jug <j>)
    (<j> ^volume <v> ^contents <c>)
-->
    (<j> ^contents <c> -)
    (<j> ^contents 0)
}

sp {water-jugs*apply*pour-jug
    "Apply pour-jug: subtract transfer from source and add to destination"
    :o-support
    (state <s> ^name water-jugs ^operator <o>)
    (<o> ^name pour-jug ^source-jug <src> ^dest-jug <dst> ^transfer <delta>)
    (<src> ^contents <sc>)
    (<dst> ^contents <dc>)
-->
    (<src> ^contents <sc> -)
    (<src> ^contents (- <sc> <delta>))
    (<dst> ^contents <dc> -)
    (<dst> ^contents (+ <dc> <delta>))
}

sp {water-jugs*detect*goal-achieved
    "Halt when the 5-gallon jug contains exactly 4 gallons"
    (state <s> ^name water-jugs ^jug <j5>)
    (<j5> ^volume 5 ^contents 4)
-->
    (<s> ^goal-achieved yes)
    (halt)
}
"""

BLOCKS_WORLD_SOAR = """
sp {blocks-world*propose*initialize
    "Initialize the Sussman Anomaly configuration"
    (state <s> ^superstate nil -^name)
-->
    (<s> ^operator <o> + =)
    (<o> ^name init-blocks-world)
}

sp {blocks-world*apply*initialize
    "Create blocks A, B, C with C on A, A on table, B on table"
    :o-support
    (state <s> ^operator <o>)
    (<o> ^name init-blocks-world)
-->
    (<s> ^name blocks-world ^ontop <t1> ^ontop <t2> ^ontop <t3>)
    (<t1> ^top-block C ^bottom-block A)
    (<t2> ^top-block A ^bottom-block table)
    (<t3> ^top-block B ^bottom-block table)
}

sp {blocks-world*elaborate*clear
    "Infer via i-support TMS that a block is ^clear if no other block rests on top of it"
    :i-support
    (state <s> ^name blocks-world ^ontop <rel>)
    (<rel> ^top-block <blk>)
   -(<other-rel> ^bottom-block <blk>)
-->
    (<s> ^clear <blk>)
}

sp {blocks-world*propose*unstack-c-to-table
    "Propose moving C from A to table when C is clear via i-support"
    (state <s> ^name blocks-world ^clear C ^ontop <t1>)
    (<t1> ^top-block C ^bottom-block A)
-->
    (<s> ^operator <o> +)
    (<o> ^name move-block ^moving C ^from A ^to table)
}

sp {blocks-world*propose*premature-b-on-c
    "Competing Sussman trap: propose putting B on C before C is on table (triggers Tie Impasse)"
    (state <s> ^name blocks-world ^clear B ^clear C ^ontop <t1> ^ontop <t3>)
    (<t1> ^top-block C ^bottom-block A)
    (<t3> ^top-block B ^bottom-block table)
-->
    (<s> ^operator <o> +)
    (<o> ^name move-block ^moving B ^from table ^to C)
}

sp {blocks-world*substate*avoid-sussman-trap
    "In tie substate S2, prefer unstacking C to table over prematurely placing B on C"
    (state <s2> ^superstate <ss> ^impasse tie ^item <o1> ^item <o2>)
    (<o1> ^name move-block ^moving C ^to table)
    (<o2> ^name move-block ^moving B ^to C)
-->
    (<ss> ^operator <o1> > <o2>)
}

sp {blocks-world*propose*stack-b-on-c
    "Once C is on the table and B and C are clear via i-support, stack B on C"
    (state <s> ^name blocks-world ^clear B ^clear C ^ontop <t1> ^ontop <t3>)
    (<t1> ^top-block C ^bottom-block table)
    (<t3> ^top-block B ^bottom-block table)
-->
    (<s> ^operator <o> + =)
    (<o> ^name move-block ^moving B ^from table ^to C)
}

sp {blocks-world*propose*stack-a-on-b
    "Once B is on C and A becomes clear via i-support TMS, stack A on B"
    (state <s> ^name blocks-world ^clear A ^clear B ^ontop <t1> ^ontop <t2> ^ontop <t3>)
    (<t1> ^top-block C ^bottom-block table)
    (<t3> ^top-block B ^bottom-block C)
    (<t2> ^top-block A ^bottom-block table)
-->
    (<s> ^operator <o> + =)
    (<o> ^name move-block ^moving A ^from table ^to B)
}

sp {blocks-world*apply*move-block
    "Apply move-block by updating the ^bottom-block relation"
    :o-support
    (state <s> ^name blocks-world ^operator <o> ^ontop <rel>)
    (<o> ^name move-block ^moving <blk> ^from <old> ^to <new>)
    (<rel> ^top-block <blk> ^bottom-block <old>)
-->
    (<rel> ^bottom-block <old> -)
    (<rel> ^bottom-block <new>)
}

sp {blocks-world*detect*tower-complete
    "Halt when A is on B, B is on C, and C is on table"
    (state <s> ^name blocks-world ^ontop <t1> ^ontop <t2> ^ontop <t3>)
    (<t1> ^top-block C ^bottom-block table)
    (<t3> ^top-block B ^bottom-block C)
    (<t2> ^top-block A ^bottom-block B)
-->
    (<s> ^goal-achieved yes)
    (halt)
}
"""

TOWERS_OF_HANOI_SOAR = """
sp {hanoi*propose*initialize
    "Initialize Towers of Hanoi with Small(1) and Large(2) disks on Peg-A"
    (state <s> ^superstate nil -^name)
-->
    (<s> ^operator <o> + =)
    (<o> ^name init-hanoi)
}

sp {hanoi*apply*initialize
    "Place Disk-1 (small) and Disk-2 (large) on Peg-A; goal is both on Peg-C"
    :o-support
    (state <s> ^operator <o>)
    (<o> ^name init-hanoi)
-->
    (<s> ^name towers-of-hanoi ^step 0 ^disk <d1> ^disk <d2>)
    (<d1> ^size 1 ^peg A)
    (<d2> ^size 2 ^peg A)
}

sp {hanoi*propose*move-d1-to-B
    "At step 0, propose moving Disk-1 to auxiliary Peg-B"
    (state <s> ^name towers-of-hanoi ^step 0 ^disk <d1> ^disk <d2>)
    (<d1> ^size 1 ^peg A)
    (<d2> ^size 2 ^peg A)
-->
    (<s> ^operator <o> +)
    (<o> ^name move-disk ^disk <d1> ^disk-size 1 ^from A ^to B ^next-step 1)
}

sp {hanoi*propose*move-d1-to-C-greedy
    "At step 0, competing greedy proposal: move Disk-1 directly to Peg-C (blocks Disk-2!)"
    (state <s> ^name towers-of-hanoi ^step 0 ^disk <d1> ^disk <d2>)
    (<d1> ^size 1 ^peg A)
    (<d2> ^size 2 ^peg A)
-->
    (<s> ^operator <o> +)
    (<o> ^name move-disk ^disk <d1> ^disk-size 1 ^from A ^to C ^next-step 1)
}

sp {hanoi*substate*prefer-auxiliary-peg-for-small-disk
    "In tie substate S2, prefer moving Disk-1 to auxiliary Peg-B so Peg-C stays open for Disk-2"
    (state <s2> ^superstate <ss> ^impasse tie ^item <o1> ^item <o2>)
    (<o1> ^name move-disk ^disk-size 1 ^to B)
    (<o2> ^name move-disk ^disk-size 1 ^to C)
-->
    (<ss> ^operator <o1> > <o2>)
}

sp {hanoi*propose*move-d2-to-C
    "At step 1, with Disk-1 on Peg-B, move Disk-2 from Peg-A to goal Peg-C"
    (state <s> ^name towers-of-hanoi ^step 1 ^disk <d1> ^disk <d2>)
    (<d1> ^size 1 ^peg B)
    (<d2> ^size 2 ^peg A)
-->
    (<s> ^operator <o> + =)
    (<o> ^name move-disk ^disk <d2> ^disk-size 2 ^from A ^to C ^next-step 2)
}

sp {hanoi*propose*move-d1-from-B-to-C
    "At step 2, move Disk-1 from Peg-B onto Disk-2 at Peg-C"
    (state <s> ^name towers-of-hanoi ^step 2 ^disk <d1> ^disk <d2>)
    (<d1> ^size 1 ^peg B)
    (<d2> ^size 2 ^peg C)
-->
    (<s> ^operator <o> + =)
    (<o> ^name move-disk ^disk <d1> ^disk-size 1 ^from B ^to C ^next-step 3)
}

sp {hanoi*apply*move-disk
    "Apply move-disk and increment step counter"
    :o-support
    (state <s> ^name towers-of-hanoi ^operator <o> ^step <st>)
    (<o> ^name move-disk ^disk <d> ^disk-size <sz> ^from <f> ^to <t> ^next-step <ns>)
    (<d> ^peg <f>)
-->
    (<d> ^peg <f> -)
    (<d> ^peg <t>)
    (<s> ^step <st> -)
    (<s> ^step <ns>)
}

sp {hanoi*detect*goal
    "Halt when both Disk-1 and Disk-2 are on Peg-C"
    (state <s> ^name towers-of-hanoi ^disk <d1> ^disk <d2>)
    (<d1> ^size 1 ^peg C)
    (<d2> ^size 2 ^peg C)
-->
    (<s> ^goal-achieved yes)
    (halt)
}
"""

VAULT_GOVERNANCE_SOAR = """
sp {vault-governance*propose*initialize
    "Initialize Sovereign-KB Cognitive Governance State"
    (state <s> ^superstate nil -^name)
-->
    (<s> ^operator <o> + =)
    (<o> ^name init-vault-governance)
}

sp {vault-governance*apply*initialize
    "Project competing retrieval & GWI remediation candidates into Working Memory"
    :o-support
    (state <s> ^operator <o>)
    (<o> ^name init-vault-governance)
-->
    (<s> ^name skb-vault ^phase retrieval-tie ^cand <c1> ^cand <c2>)
    (<c1> ^tier operating-system ^authority 3 ^doc scientific-paper-framework)
    (<c2> ^tier episodic ^authority 1 ^doc daily-log)
}

sp {vault-governance*elaborate*epistemic-priority
    "Infer i-supported canonical status for operating-system notes"
    :i-support
    (state <s> ^name skb-vault ^cand <c>)
    (<c> ^tier operating-system)
-->
    (<c> ^canonical yes)
}

sp {vault-governance*propose*select-os-candidate
    "Propose selecting the canonical operating-system note (requires i-supported ^canonical yes)"
    (state <s> ^name skb-vault ^phase retrieval-tie ^cand <c1>)
    (<c1> ^tier operating-system ^authority 3 ^canonical yes)
-->
    (<s> ^operator <o> +)
    (<o> ^name select-candidate ^cand <c1> ^tier operating-system ^authority 3)
}

sp {vault-governance*propose*select-episodic-candidate
    "Competing proposal: select raw episodic log (triggers Tie Impasse in S1)"
    (state <s> ^name skb-vault ^phase retrieval-tie ^cand <c2>)
    (<c2> ^tier episodic ^authority 1)
-->
    (<s> ^operator <o> +)
    (<o> ^name select-candidate ^cand <c2> ^tier episodic ^authority 1)
}

sp {vault-governance*substate*prefer-higher-authority
    "In tie substate S2, assert strict dominance for higher-authority canonical notes"
    (state <s2> ^superstate <ss> ^impasse tie ^item <o1> ^item <o2>)
    (<o1> ^name select-candidate ^authority 3)
    (<o2> ^name select-candidate ^authority 1)
-->
    (<ss> ^operator <o1> > <o2>)
}

sp {vault-governance*apply*select-candidate
    "Apply winning candidate and reach verified state"
    :o-support
    (state <s> ^name skb-vault ^phase retrieval-tie ^operator <o>)
    (<o> ^name select-candidate ^cand <c> ^tier <t>)
    (<c> ^doc <d>)
-->
    (<s> ^phase retrieval-tie -)
    (<s> ^phase verified ^selected-tier <t> ^selected-doc <d>)
}

sp {vault-governance*detect*goal
    "Halt once canonical operating-system authority is selected"
    (state <s> ^name skb-vault ^phase verified ^selected-tier operating-system)
-->
    (<s> ^goal-achieved yes)
    (halt)
}
"""

CANONICAL_SOAR_BENCHMARKS: dict[str, str] = {
    "water-jugs": WATER_JUGS_SOAR,
    "blocks-world": BLOCKS_WORLD_SOAR,
    "towers-of-hanoi": TOWERS_OF_HANOI_SOAR,
    "vault-governance": VAULT_GOVERNANCE_SOAR,
}


def learned_chunks_path(vault_dir: Path) -> Path:
    return vault_dir / ".kb" / "learned_chunks.soar"


def extract_soar_blocks(md_text: str) -> list[str]:
    """
    Fence-aware extractor for ```soar code blocks inside vault Markdown notes.
    Returns a list of raw Soar source blocks.
    """
    blocks: list[str] = []
    in_soar = False
    fence_marker = ""
    current_lines: list[str] = []

    for line in md_text.splitlines():
        stripped = line.strip()
        if not in_soar:
            if stripped.startswith("```soar") or stripped.startswith("~~~soar"):
                in_soar = True
                fence_marker = stripped[:3]
                current_lines = []
        else:
            if stripped.startswith(fence_marker):
                in_soar = False
                block_str = "\n".join(current_lines).strip()
                if block_str:
                    blocks.append(block_str)
                current_lines = []
            else:
                current_lines.append(line)
    return blocks


def _extract_authority_interval(vt: Any) -> tuple[float, float] | None:
    """Recursively extract a closed numeric interval `[low, high]` from a Soar LHS `Test`."""
    if isinstance(vt, EqualityTest):
        if isinstance(vt.target, (int, float)) and not isinstance(vt.target, bool):
            val = float(vt.target)
            return (val, val)
        return None
    if isinstance(vt, DisjunctionTest):
        if not vt.options:
            return None
        vals: list[float] = []
        for opt in vt.options:
            if isinstance(opt, (int, float)) and not isinstance(opt, bool):
                vals.append(float(opt))
            else:
                return None
        return (min(vals), max(vals))
    if isinstance(vt, ConjunctiveTest):
        if not vt.tests:
            return None
        low = -float("inf")
        high = float("inf")
        for sub in vt.tests:
            sub_iv = _extract_authority_interval(sub)
            if sub_iv is None:
                return None
            low = max(low, sub_iv[0])
            high = min(high, sub_iv[1])
        if low > high or not (low > -float("inf") and high < float("inf")):
            return None
        return (low, high)
    return None


def _is_valid_learned_chunk(prod: Production) -> bool:
    """
    Recursively validate that a production loaded from `.kb/learned_chunks.soar` is a safe,
    sound EBC operator-dominance chunk rule without nested `RHSFuncCall` (`(halt)`),
    prohibition/rejection preferences, or inverted/unprovable authority ordering.
    """
    if not prod.is_chunk or not prod.name.startswith("chunk*"):
        return False
    if not prod.actions or len(prod.conditions) < 3:
        return False
    if any(c.negated for c in prod.conditions):
        return False

    state_conds = [c for c in prod.conditions if c.is_state and not c.negated]
    if len(state_conds) != 1:
        return False
    state_var = state_conds[0].id_var
    acceptable_op_vars = {
        at.value_test.target.name
        for at in state_conds[0].attr_tests
        if at.attr == "operator"
        and at.acceptable_pref
        and not at.negated
        and isinstance(at.value_test, EqualityTest)
        and isinstance(at.value_test.target, Var)
    }
    if len(acceptable_op_vars) < 2:
        return False

    # Recursively extract and intersect numeric `^authority` intervals across all LHS conditions
    cond_authority: dict[str, tuple[float, float]] = {}
    op_cand_links: dict[str, str] = {}
    for cond in prod.conditions:
        if cond.is_state:
            continue
        for at in cond.attr_tests:
            if (
                at.attr == "cand"
                and not at.negated
                and isinstance(at.value_test, EqualityTest)
                and isinstance(at.value_test.target, Var)
            ):
                op_cand_links[cond.id_var.name] = at.value_test.target.name
            if at.attr == "authority":
                if at.negated:
                    return False
                iv = _extract_authority_interval(at.value_test)
                if iv is None:
                    return False
                prev = cond_authority.get(cond.id_var.name)
                if prev is not None:
                    merged = (max(prev[0], iv[0]), min(prev[1], iv[1]))
                    if merged[0] > merged[1]:
                        return False
                    cond_authority[cond.id_var.name] = merged
                else:
                    cond_authority[cond.id_var.name] = iv

    for op_var, cand_var in op_cand_links.items():
        if cand_var in cond_authority:
            c_iv = cond_authority[cand_var]
            prev = cond_authority.get(op_var)
            if prev is not None:
                merged = (max(prev[0], c_iv[0]), min(prev[1], c_iv[1]))
                if merged[0] > merged[1]:
                    return False
                cond_authority[op_var] = merged
            else:
                cond_authority[op_var] = c_iv

    for act in prod.actions:
        if isinstance(act, RHSFunctionStandalone) or not isinstance(act, RHSAction):
            return False
        if (
            isinstance(act.id_sym, RHSFuncCall)
            or isinstance(act.attr, RHSFuncCall)
            or isinstance(act.value, RHSFuncCall)
        ):
            return False
        if act.id_sym != state_var or act.attr != "operator" or not isinstance(act.value, Var):
            return False
        if act.value.name not in acceptable_op_vars:
            return False
        if len(act.preferences) != 1:
            return False
        pspec = act.preferences[0]
        if pspec.pref_type != ">" or not isinstance(pspec.referent, Var):
            return False
        if pspec.referent.name not in acceptable_op_vars or pspec.referent.name == act.value.name:
            return False
        # Require provable numeric dominance: winner's minimum authority > loser's maximum authority
        win_iv = cond_authority.get(act.value.name)
        lose_iv = cond_authority.get(pspec.referent.name)
        if win_iv is None or lose_iv is None or win_iv[0] <= lose_iv[1]:
            return False

    return True


def _has_goal_achieved_wme(kernel: SoarKernel) -> bool:
    """Verify that `(S1 ^goal-achieved yes)` exists in Working Memory (not merely early `(halt)`)."""
    return any(w.id == "S1" and w.attr == "goal-achieved" and str(w.value) == "yes" for w in kernel.wmes.values())


def load_vault_productions(vault_dir: Path, kernel: SoarKernel | None = None) -> dict[str, Any]:
    """
    Scan all Markdown notes in `vault_dir` plus `.kb/learned_chunks.soar`,
    parse and load all `sp { ... }` productions into `kernel`.
    """
    if kernel is None:
        kernel = SoarKernel(enable_chunking=True)

    loaded_rules: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    files_with_soar: list[str] = []

    if vault_dir.is_dir():
        for md_path in sorted(vault_dir.rglob("*.md")):
            if any(part.startswith(".") for part in md_path.relative_to(vault_dir).parts):
                continue
            try:
                text = md_path.read_text(encoding="utf-8")
            except Exception:
                continue
            blocks = extract_soar_blocks(text)
            if not blocks:
                continue
            rel = str(md_path.relative_to(vault_dir))
            files_with_soar.append(rel)
            for blk in blocks:
                try:
                    prods = kernel.load_soar_code(blk, clear_existing=False)
                    for p in prods:
                        loaded_rules.append(
                            {
                                "name": p.name,
                                "source": rel,
                                "is_chunk": p.is_chunk,
                                "o_support": kernel.classify_support(p) == "o",
                            }
                        )
                except Exception as exc:
                    errors.append({"source": rel, "error": str(exc)})

    cp = learned_chunks_path(vault_dir)
    if cp.is_file():
        try:
            parsed_chunks = parse_soar(cp.read_text(encoding="utf-8"))
            for p in parsed_chunks:
                if not _is_valid_learned_chunk(p):
                    errors.append(
                        {
                            "source": ".kb/learned_chunks.soar",
                            "error": f"Rejected non-chunk or unsafe halt production '{p.name}'",
                        }
                    )
                    continue
                kernel.productions[p.name] = p
                if p.name not in kernel.learned_chunks:
                    kernel.learned_chunks.append(p.name)
                loaded_rules.append(
                    {
                        "name": p.name,
                        "source": ".kb/learned_chunks.soar",
                        "is_chunk": True,
                        "o_support": kernel.classify_support(p) == "o",
                    }
                )
        except Exception as exc:
            errors.append({"source": ".kb/learned_chunks.soar", "error": str(exc)})

    return {
        "status": "PASS" if not errors else "FAIL",
        "vault_dir": str(vault_dir),
        "files_with_soar": files_with_soar,
        "rules_loaded": len(loaded_rules),
        "rules": loaded_rules,
        "errors": errors,
    }


def _sanitize_soar_sym(raw: str, fallback: str = "doc") -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(raw).strip()).strip("-")
    if not cleaned or cleaned[0].isdigit():
        return f"{fallback}-{cleaned}" if cleaned else fallback
    return cleaned


def _build_dynamic_tie_soar_rules(
    candidates: list[dict[str, Any]], tier_weights: dict[str, int]
) -> tuple[str, dict[str, dict[str, Any]]]:
    """
    Build a Soar 9.6 program that projects ALL competing `candidates` into `S1`,
    infers `i-support` canonical status, creates an operator tie between the top
    contenders, resolves the tie in `S2` by epistemic authority (and score rank),
    and commits `(S1 ^selected-doc <doc>)`.
    """
    if len(candidates) < 2:
        return "", {}

    scored_entries: list[tuple[int, float, int, str, str, dict[str, Any]]] = []
    used_docs: set[str] = set()
    for idx, c in enumerate(candidates):
        raw_tier = str(c.get("doc_type", c.get("type", "episodic")))
        tier_sym = _sanitize_soar_sym(raw_tier, "tier")
        auth = int(tier_weights.get(raw_tier, tier_weights.get(tier_sym, 1)))
        score = float(c.get("score", c.get("rrf_score", 0.0)))
        doc_sym = _sanitize_soar_sym(str(c.get("rel_path", c.get("id", c.get("doc", f"cand-{idx}")))), f"doc{idx}")
        if doc_sym in used_docs:
            doc_sym = f"{doc_sym}-{idx}"
        used_docs.add(doc_sym)
        scored_entries.append((auth, score, -idx, doc_sym, tier_sym, c))

    # Order all supplied candidates by epistemic authority and score so even a 3rd+ candidate is evaluated
    ranked_entries = sorted(scored_entries, key=lambda x: (x[0], x[1], x[2]), reverse=True)
    top_entry = ranked_entries[0]
    runner_entry = ranked_entries[1]

    hi_auth, _, _, hi_doc, hi_tier, _ = top_entry
    lo_auth, _, _, lo_doc, lo_tier, _ = runner_entry

    eff_hi_auth = hi_auth if hi_auth != lo_auth else hi_auth + 1
    eff_lo_auth = lo_auth

    doc_map = {entry[3]: entry[5] for entry in ranked_entries}
    cand_vars = " ".join(f"^cand <c{i + 1}>" for i in range(len(ranked_entries)))
    cand_wmes_lines = []
    for i, entry in enumerate(ranked_entries):
        e_auth, _, _, e_doc, e_tier, _ = entry
        eff_a = eff_hi_auth if i == 0 else (eff_lo_auth if i == 1 else min(e_auth, eff_lo_auth))
        cand_wmes_lines.append(f"    (<c{i + 1}> ^tier {e_tier} ^authority {eff_a} ^doc {e_doc})")
    cand_wmes_block = "\n".join(cand_wmes_lines)

    soar_src = f"""
sp {{vault-governance*propose*initialize
    "Initialize Sovereign-KB Cognitive Governance State"
    (state <s> ^superstate nil -^name)
-->
    (<s> ^operator <o> + =)
    (<o> ^name init-vault-governance)
}}

sp {{vault-governance*apply*initialize
    "Project all competing retrieval candidates into Working Memory S1"
    :o-support
    (state <s> ^operator <o>)
    (<o> ^name init-vault-governance)
-->
    (<s> ^name skb-vault ^phase retrieval-tie {cand_vars})
{cand_wmes_block}
}}

sp {{vault-governance*elaborate*epistemic-priority
    "Infer i-supported canonical status for highest-authority candidate"
    :i-support
    (state <s> ^name skb-vault ^cand <c>)
    (<c> ^authority {eff_hi_auth})
-->
    (<c> ^canonical yes)
}}

sp {{vault-governance*propose*select-os-candidate
    "Propose selecting the canonical higher-authority candidate (requires i-supported ^canonical yes)"
    (state <s> ^name skb-vault ^phase retrieval-tie ^cand <c1>)
    (<c1> ^tier {hi_tier} ^authority {eff_hi_auth} ^canonical yes)
-->
    (<s> ^operator <o> +)
    (<o> ^name select-candidate ^cand <c1> ^tier {hi_tier} ^authority {eff_hi_auth})
}}

sp {{vault-governance*propose*select-episodic-candidate
    "Competing proposal: select runner-up candidate (triggers Tie Impasse in S1)"
    (state <s> ^name skb-vault ^phase retrieval-tie ^cand <c2>)
    (<c2> ^tier {lo_tier} ^authority {eff_lo_auth})
-->
    (<s> ^operator <o> +)
    (<o> ^name select-candidate ^cand <c2> ^tier {lo_tier} ^authority {eff_lo_auth})
}}

sp {{vault-governance*substate*prefer-higher-authority
    "In tie substate S2, assert strict dominance for higher-authority candidate"
    (state <s2> ^superstate <ss> ^impasse tie ^item <o1> ^item <o2>)
    (<o1> ^name select-candidate ^authority {eff_hi_auth})
    (<o2> ^name select-candidate ^authority {eff_lo_auth})
-->
    (<ss> ^operator <o1> > <o2>)
}}

sp {{vault-governance*apply*select-candidate
    "Apply winning candidate and reach verified state"
    :o-support
    (state <s> ^name skb-vault ^phase retrieval-tie ^operator <o>)
    (<o> ^name select-candidate ^cand <c> ^tier <t>)
    (<c> ^doc <d>)
-->
    (<s> ^phase retrieval-tie -)
    (<s> ^phase verified ^selected-tier <t> ^selected-doc <d>)
}}

sp {{vault-governance*detect*goal
    "Halt once winning candidate is verified in S1"
    (state <s> ^name skb-vault ^phase verified ^selected-tier {hi_tier} ^selected-doc {hi_doc})
-->
    (<s> ^goal-achieved yes)
    (halt)
}}
"""
    return soar_src, doc_map


def resolve_retrieval_tie_with_soar(
    vault_dir: Path,
    query: str,
    candidates: list[dict[str, Any]],
    persist_chunks: bool = True,
) -> dict[str, Any]:
    """
    Project competing 4-way RRF retrieval candidates into Soar Working Memory (`S1`),
    detect and resolve a `tie` impasse via Substate `S2` (or `NeuroSymbolicBridge`),
    and compile an Explanation-Based `chunk*` rule for 0-impasse future replay.
    """
    if not candidates:
        return {
            "query": query,
            "preloaded_chunks": 0,
            "first_run_impasses": 0,
            "replay_impasses": 0,
            "zero_impasse_replay": False,
            "goal_achieved": False,
            "selected_doc": None,
            "selected_tier": None,
            "chunks_compiled": 0,
            "chunk_names": [],
            "reranked_candidates": [],
        }

    if len(candidates) == 1:
        only = candidates[0]
        only_tier = _sanitize_soar_sym(str(only.get("doc_type", only.get("type", "episodic"))), "tier")
        only_doc = _sanitize_soar_sym(str(only.get("rel_path", only.get("id", only.get("doc", "cand-0")))), "doc0")
        return {
            "query": query,
            "preloaded_chunks": 0,
            "first_run_impasses": 0,
            "replay_impasses": 0,
            "zero_impasse_replay": False,
            "goal_achieved": True,
            "selected_doc": only_doc,
            "selected_tier": only_tier,
            "chunks_compiled": 0,
            "chunk_names": [],
            "reranked_candidates": list(candidates),
        }

    tier_weights = {
        "identity": 4,
        "operating-system": 3,
        "decision": 3,
        "domain": 2,
        "life-domain": 2,
        "now": 2,
        "rsi-ledger": 1,
        "distillation-ledger": 1,
        "episodic": 1,
    }
    soar_program, doc_map = _build_dynamic_tie_soar_rules(candidates, tier_weights)

    kernel = SoarKernel(enable_chunking=True)
    kernel.load_soar_code(soar_program, clear_existing=True)

    cp = learned_chunks_path(vault_dir)
    preloaded_chunks = 0
    if cp.is_file():
        try:
            parsed_chunks = parse_soar(cp.read_text(encoding="utf-8"))
            for p in parsed_chunks:
                if _is_valid_learned_chunk(p):
                    kernel.productions[p.name] = p
                    if p.name not in kernel.learned_chunks:
                        kernel.learned_chunks.append(p.name)
                    preloaded_chunks += 1
        except Exception:
            pass
    elif vault_dir.is_dir():
        try:
            from .chunk_store import ChunkStore

            cstore = ChunkStore.for_vault(vault_dir)
            preloaded_chunks += cstore.load_chunks_into_kernel(
                kernel, parse_soar, is_valid_fn=_is_valid_learned_chunk, domain="skb-vault"
            )
        except Exception:
            pass

    first_run = kernel.run_to_completion(max_cycles=25)
    replay_run = kernel.replay_with_chunks(max_cycles=25)
    goal_ok = _has_goal_achieved_wme(kernel)

    selected_doc = (
        next(
            (str(w.value) for w in kernel.wmes.values() if w.id == "S1" and w.attr == "selected-doc"),
            None,
        )
        if goal_ok
        else None
    )
    selected_tier = (
        next(
            (str(w.value) for w in kernel.wmes.values() if w.id == "S1" and w.attr == "selected-tier"),
            None,
        )
        if goal_ok
        else None
    )

    compiled_chunks = [format_production(p) for p in kernel.productions.values() if _is_valid_learned_chunk(p)]
    if persist_chunks and compiled_chunks and goal_ok and vault_dir.is_dir():
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text("\n\n".join(compiled_chunks) + "\n", encoding="utf-8")
        try:
            from .chunk_store import ChunkStore

            cstore = ChunkStore.for_vault(vault_dir)
            cstore.upsert_chunks_from_kernel(
                kernel,
                format_production,
                is_valid_fn=_is_valid_learned_chunk,
                source_goal=query,
                domain="skb-vault",
            )
        except Exception:
            pass

    # Rank supplied retrieval candidates directly using the Soar kernel's verified `^selected-doc` WME decision!
    reranked = list(candidates)
    if goal_ok and len(reranked) >= 2 and selected_doc in doc_map:
        winning_cand = doc_map[selected_doc]
        remaining = [c for c in reranked if c is not winning_cand]
        remaining.sort(
            key=lambda c: (
                int(tier_weights.get(str(c.get("doc_type", c.get("type", "episodic"))), 1)),
                float(c.get("score", c.get("rrf_score", 0.0))),
            ),
            reverse=True,
        )
        reranked = [winning_cand, *remaining]

    return {
        "query": query,
        "preloaded_chunks": preloaded_chunks,
        "first_run_impasses": first_run["total_impasses_count"],
        "replay_impasses": replay_run["total_impasses_count"],
        "zero_impasse_replay": bool(goal_ok and replay_run["total_impasses_count"] == 0),
        "goal_achieved": goal_ok,
        "selected_doc": selected_doc,
        "selected_tier": selected_tier,
        "chunks_compiled": len(compiled_chunks) if goal_ok else 0,
        "chunk_names": [p.name for p in kernel.productions.values() if _is_valid_learned_chunk(p)] if goal_ok else [],
        "reranked_candidates": reranked,
    }


def run_soar_benchmark_suite(benchmark: str = "all") -> dict[str, Any]:
    """Execute one or all canonical Soar benchmarks and verify 0-impasse chunked replay."""
    targets = list(CANONICAL_SOAR_BENCHMARKS.keys()) if benchmark == "all" else [benchmark]
    results: dict[str, Any] = {}
    all_passed = True

    for name in targets:
        source = CANONICAL_SOAR_BENCHMARKS.get(name)
        if not source:
            raise ValueError(f"Unknown Soar benchmark '{name}'. Choices: {list(CANONICAL_SOAR_BENCHMARKS)}")
        kernel = SoarKernel(enable_chunking=True)
        kernel.load_soar_code(source, clear_existing=True)
        run1 = kernel.run_to_completion(max_cycles=35)
        goal1 = _has_goal_achieved_wme(kernel)
        run2 = kernel.replay_with_chunks(max_cycles=35)
        goal2 = _has_goal_achieved_wme(kernel)
        transpiled = transpile_soar_to_python(source, class_name=f"Soar_{name.replace('-', '_').title()}")
        passed = (
            bool(run1["halted"] and goal1)
            and bool(run2["halted"] and goal2)
            and int(run1["total_impasses_count"]) >= 1
            and int(run2["total_impasses_count"]) == 0
            and bool(transpiled["verified"])
        )
        if not passed:
            all_passed = False
        results[name] = {
            "passed": passed,
            "goal_achieved": bool(run1["halted"] and goal1 and goal2),
            "initial_cycles": run1["cycle"],
            "initial_impasses": run1["total_impasses_count"],
            "replay_cycles": run2["cycle"],
            "replay_impasses": run2["total_impasses_count"],
            "chunks_learned": [p.name for p in kernel.productions.values() if p.is_chunk],
            "transpiled_verified": transpiled["verified"],
            "transpiled_lines": transpiled["line_count"],
        }

    return {
        "status": "PASS" if all_passed else "FAIL",
        "benchmarks_count": len(results),
        "benchmarks": results,
    }


def register_soar_subparser(sub: argparse._SubParsersAction, show_all_aliases: bool = False) -> None:
    """Register `skb soar` subcommands."""
    soar_help = (
        "Soar 9.6 Symbolic & Neuro-Symbolic Cognitive Kernel, Chunking & Rete Transpiler (`soarc`)"
        if show_all_aliases
        else None
    )
    p_soar = sub.add_parser(
        "soar",
        **({"help": soar_help} if soar_help else {}),
    )
    p_soar.add_argument(
        "soar_cmd",
        nargs="?",
        choices=("status", "bench", "verify", "transpile", "resolve"),
        default="status",
        help="Soar subcommand (default: status)",
    )
    p_soar.add_argument(
        "-b",
        "--benchmark",
        default="all",
        choices=("all", "water-jugs", "blocks-world", "towers-of-hanoi", "vault-governance"),
        help="Benchmark problem space to execute or transpile",
    )
    p_soar.add_argument(
        "-q",
        "--query",
        default="scientific paper framework Pillar 5",
        help="Query or natural-language rule specification for `resolve`",
    )
    p_soar.add_argument("--json", action="store_true", help="Output machine-readable JSON")


def cmd_soar(args: argparse.Namespace, resolve_vault_fn: Callable[[str | None], Path]) -> None:
    """Dispatch `skb soar` CLI commands."""
    vault_dir = resolve_vault_fn(getattr(args, "vault", None))
    subcmd = getattr(args, "soar_cmd", "status") or "status"

    if subcmd == "bench":
        rep = run_soar_benchmark_suite(getattr(args, "benchmark", "all"))
        if getattr(args, "json", False):
            print(json.dumps(rep, indent=2))
            return
        print(f"=== Soar 9.6 Cognitive Benchmark Suite [{rep['status']}] ===")
        for bname, bdata in rep["benchmarks"].items():
            print(
                f"  - {bname:18s}: goal={bdata['goal_achieved']} | "
                f"impasses={bdata['initial_impasses']}->{bdata['replay_impasses']} (0-impasse replay) | "
                f"chunks={len(bdata['chunks_learned'])} | soarc={bdata['transpiled_lines']} LOC"
            )
        return

    if subcmd == "verify":
        vrep = load_vault_productions(vault_dir)
        brep = run_soar_benchmark_suite("vault-governance")
        combined = {
            "status": "PASS" if (vrep["status"] == "PASS" and brep["status"] == "PASS") else "FAIL",
            "vault_productions": vrep,
            "governance_verification": brep["benchmarks"]["vault-governance"],
        }
        if getattr(args, "json", False):
            print(json.dumps(combined, indent=2))
            return
        print(
            f"=== Soar Vault Production & Chunking Verification [{combined['status']}] ===\n"
            f"  Files with ```soar : {len(vrep['files_with_soar'])}\n"
            f"  Productions loaded : {vrep['rules_loaded']}\n"
            f"  0-Impasse Replay   : {brep['benchmarks']['vault-governance']['replay_impasses'] == 0}"
        )
        return

    if subcmd == "transpile":
        bname = getattr(args, "benchmark", "vault-governance")
        if bname == "all":
            bname = "vault-governance"
        src = CANONICAL_SOAR_BENCHMARKS[bname]
        trep = transpile_soar_to_python(src, class_name=f"Soar_{bname.replace('-', '_').title()}")
        if getattr(args, "json", False):
            print(json.dumps(trep, indent=2))
        else:
            print(trep["python_code"])
        return

    if subcmd == "resolve":
        bridge = NeuroSymbolicBridge()
        kernel = SoarKernel(enable_chunking=True)
        bname = getattr(args, "benchmark", "water-jugs")
        if bname == "all":
            bname = "water-jugs"
        kernel.load_soar_code(CANONICAL_SOAR_BENCHMARKS[bname], clear_existing=True)
        kernel.run_until_impasse(max_cycles=10)
        res = bridge.resolve_impasse(kernel, goal_hint=getattr(args, "query", ""))
        default_cands = [
            {
                "rel_path": "05-episodic/daily-log.md",
                "doc_type": "episodic",
                "score": 0.82,
            },
            {
                "rel_path": "02-operating-system/scientific-paper-framework.md",
                "doc_type": "operating-system",
                "score": 0.81,
            },
        ]
        tie_res = resolve_retrieval_tie_with_soar(vault_dir, getattr(args, "query", ""), default_cands)
        out = {
            "impasse_resolution": {
                "resolved": res["resolved"],
                "engine": res["engine"],
                "loaded_rule_name": res["loaded_rule_name"],
                "synthesized_rule": res["synthesized_rule"],
            },
            "vault_chunking": tie_res,
        }
        if getattr(args, "json", False):
            print(json.dumps(out, indent=2))
        else:
            print(
                f"Resolved impasse via {res['engine']} -> rule `{res['loaded_rule_name']}` "
                f"(0-impasse replay={tie_res['zero_impasse_replay']})"
            )
        return

    # Default: status
    vrep = load_vault_productions(vault_dir)
    cp = learned_chunks_path(vault_dir)
    status_payload = {
        "engine": "Soar 9.6 Symbolic & Neuro-Symbolic Kernel (Zero-Dependency Pure Stdlib)",
        "vault_dir": str(vault_dir),
        "learned_chunks_file": str(cp),
        "learned_chunks_exists": cp.is_file(),
        "files_with_soar": vrep["files_with_soar"],
        "rules_loaded": vrep["rules_loaded"],
        "benchmarks_available": list(CANONICAL_SOAR_BENCHMARKS.keys()),
    }
    if getattr(args, "json", False):
        print(json.dumps(status_payload, indent=2))
    else:
        print(
            f"=== Sovereign-KB Soar Cognitive Kernel ===\n"
            f"  Vault Dir         : {vault_dir}\n"
            f"  Vault Productions : {vrep['rules_loaded']} across {len(vrep['files_with_soar'])} file(s)\n"
            f"  Learned Chunks    : {cp} (exists={cp.is_file()})\n"
            f"  Benchmarks        : {', '.join(CANONICAL_SOAR_BENCHMARKS.keys())}"
        )
