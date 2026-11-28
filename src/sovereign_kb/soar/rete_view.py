"""Rete-network view of the loaded productions, with live alpha-memory counts from working memory.

The kernel matches productions directly; this module compiles the same conditions into the
shape a Rete network would have so the studio can show it: one *alpha node* per distinct
constant test ``(kind ^attr value)`` shared across productions, a chain of *join nodes*
per production (one per positive condition), and a *terminal node* per production.
Counts come from the current working memory, preferences and instantiations.
"""

from __future__ import annotations

from typing import Any

from .ast_nodes import ConjunctiveTest, EqualityTest, Var
from .kernel import SoarKernel, _is_identifier


def _const(test: Any) -> Any:
    """The constant a value test pins down, or ``None`` for variables / relational tests."""
    if isinstance(test, EqualityTest) and not isinstance(test.target, Var):
        return test.target
    if isinstance(test, ConjunctiveTest):
        for sub in test.tests:
            c = _const(sub)
            if c is not None:
                return c
    return None


def _alpha_key(is_state: bool, attr: str, value: Any, acceptable: bool) -> str:
    kind = "state" if is_state else "id"
    val = "*" if value is None else str(value)
    return f"({kind} ^{attr} {val}{' +' if acceptable else ''})"


def _count_alpha(kernel: SoarKernel, is_state: bool, attr: str, value: Any, acceptable: bool) -> int:
    states = set(kernel.state_stack)
    if acceptable:
        return sum(
            1
            for p in kernel.preferences.values()
            if p.attr == attr and p.pref_type == "+" and (not is_state or p.id in states)
        )
    return sum(
        1
        for w in kernel.wmes.values()
        if w.attr == attr
        and (value is None or str(w.value) == str(value))
        and (not is_state or w.id in states or (w.attr == "superstate" and _is_identifier(w.id)))
    )


def build_rete(kernel: SoarKernel) -> dict[str, Any]:
    alpha: dict[str, dict[str, Any]] = {}
    productions: list[dict[str, Any]] = []
    live: dict[str, int] = {}
    for inst in kernel.instantiations.values():
        live[inst.production_name] = live.get(inst.production_name, 0) + 1

    for prod in kernel.productions.values():
        joins: list[dict[str, Any]] = []
        for cond in prod.conditions:
            keys: list[str] = []
            for at in cond.attr_tests:
                if at.negated or at.attr == "__self__":
                    continue
                attr = str(at.attr)
                value = _const(at.value_test)
                key = _alpha_key(cond.is_state, attr, value, at.acceptable_pref)
                node = alpha.setdefault(
                    key,
                    {
                        "key": key,
                        "attr": attr,
                        "is_state": cond.is_state,
                        "wme_count": _count_alpha(kernel, cond.is_state, attr, value, at.acceptable_pref),
                        "productions": [],
                    },
                )
                if prod.name not in node["productions"]:
                    node["productions"].append(prod.name)
                keys.append(key)
            joins.append({"negated": cond.negated, "var": cond.id_var.name, "alpha": keys})
        productions.append(
            {
                "name": prod.name,
                "is_chunk": prod.is_chunk,
                "support": kernel.classify_support(prod),
                "joins": joins,
                "live_instantiations": live.get(prod.name, 0),
            }
        )

    alpha_list = sorted(alpha.values(), key=lambda a: (-len(a["productions"]), a["key"]))
    for idx, node in enumerate(alpha_list):
        node["id"] = f"a{idx}"
    shared = sum(1 for a in alpha_list if len(a["productions"]) > 1)
    total_tests = sum(len(a["productions"]) for a in alpha_list)
    return {
        "alpha": alpha_list,
        "productions": productions,
        "stats": {
            "alpha_nodes": len(alpha_list),
            "shared_alpha_nodes": shared,
            "alpha_tests_without_sharing": total_tests,
            "join_nodes": sum(len(p["joins"]) for p in productions),
            "productions": len(productions),
            "chunks": sum(1 for p in productions if p["is_chunk"]),
            "live_instantiations": sum(p["live_instantiations"] for p in productions),
        },
    }
