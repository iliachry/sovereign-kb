"""The Soar Cognitive Architecture Kernel: WME Graph, Rete Matcher, 5-Phase Decision Cycle, TMS, Impasses & Chunking."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union

from .ast_nodes import (
    AttrValueTest,
    Condition,
    ConjunctiveTest,
    DisjunctionTest,
    EqualityTest,
    PreferenceSpec,
    Production,
    RelationalTest,
    RHSAction,
    RHSFuncCall,
    RHSFunctionStandalone,
    RHSValue,
    Symbol,
    Test,
    Var,
)
from .parser import format_production, parse_soar


class DecisionPhase(str, Enum):
    """The 5 canonical phases of the Soar Decision Cycle."""

    INPUT = "INPUT"
    ELABORATION = "ELABORATION"
    DECISION = "DECISION"
    APPLICATION = "APPLICATION"
    OUTPUT = "OUTPUT"


PHASE_ORDER = [
    DecisionPhase.INPUT,
    DecisionPhase.ELABORATION,
    DecisionPhase.DECISION,
    DecisionPhase.APPLICATION,
    DecisionPhase.OUTPUT,
]


@dataclass
class WME:
    """A Working Memory Element `(id ^attr value)` with timetag and truth-maintenance metadata."""

    timetag: int
    id: str
    attr: str
    value: Union[str, int, float]
    support: str = "arch"  # 'arch', 'i', 'o'
    source_production: str | None = None
    instantiation_id: int | None = None
    supporting_timetags: tuple[int, ...] = ()
    state_id: str = "S1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timetag": self.timetag,
            "id": self.id,
            "attr": self.attr,
            "value": self.value,
            "support": self.support,
            "source_production": self.source_production,
            "state_id": self.state_id,
        }

    def __repr__(self) -> str:
        return f"({self.timetag}: {self.id} ^{self.attr} {self.value} [{self.support}])"


@dataclass
class Preference:
    """An active preference on `(id ^attr value)` in Preference Memory."""

    pref_id: int
    id: str
    attr: str
    value: Union[str, int, float]
    pref_type: str  # '+', '-', '!', '~', '>', '<', '='
    referent: Union[str, int, float, None] = None
    support: str = "i"  # 'i' or 'o'
    source_production: str | None = None
    instantiation_id: int | None = None
    supporting_timetags: tuple[int, ...] = ()
    state_id: str = "S1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pref_id": self.pref_id,
            "id": self.id,
            "attr": self.attr,
            "value": self.value,
            "pref_type": self.pref_type,
            "referent": self.referent,
            "support": self.support,
            "source_production": self.source_production,
            "state_id": self.state_id,
        }


@dataclass
class Instantiation:
    """Tracks a fired rule instantiation for Truth Maintenance (TMS) and Chunking backtracing."""

    inst_id: int
    production_name: str
    bindings: dict[str, Union[str, int, float]]
    matched_timetags: tuple[int, ...]
    support: str  # 'i' or 'o'
    state_id: str
    lhs_bindings: dict[str, Union[str, int, float]] = field(default_factory=dict)
    created_wme_timetags: list[int] = field(default_factory=list)
    created_pref_ids: list[int] = field(default_factory=list)


@dataclass
class ImpasseInfo:
    """Describes an architectural impasse encountered during the Decision Phase."""

    state_id: str
    impasse_type: str  # 'tie', 'conflict', 'constraint-failure', 'no-change'
    attribute: str  # 'operator' or 'state'
    candidates: list[str] = field(default_factory=list)
    substate_id: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "impasse_type": self.impasse_type,
            "attribute": self.attribute,
            "candidates": self.candidates,
            "substate_id": self.substate_id,
            "reason": self.reason,
        }


def _is_identifier(val: Any) -> bool:
    """Return True if `val` is a Soar object/state/operator identifier (e.g. `S1`, `O3`, `J1`, `B2`)."""
    if not isinstance(val, str) or len(val) < 2:
        return False
    return val[0].isupper() and val[1:].isdigit()


class SoarKernel:
    """Full Soar Cognitive Architecture Kernel with 5-phase Decision Cycle, TMS, Substates & Chunking."""

    def __init__(self, enable_chunking: bool = True) -> None:
        self.enable_chunking = enable_chunking
        self.productions: dict[str, Production] = {}
        self.wmes: dict[int, WME] = {}
        self.preferences: dict[int, Preference] = {}
        self.instantiations: dict[int, Instantiation] = {}
        self.fired_Instantiation_signatures: set[tuple[str, tuple[tuple[str, Any], ...], tuple[int, ...]]] = set()

        self._next_timetag = 1
        self._next_pref_id = 1
        self._next_inst_id = 1
        self._id_counters: dict[str, int] = {}

        self.cycle_count = 0
        self.current_phase: DecisionPhase = DecisionPhase.INPUT
        self.halted = False
        self.state_stack: list[str] = []
        self.active_impasses: dict[str, ImpasseInfo] = {}
        self.total_impasses_count = 0
        self.learned_chunks: list[str] = []
        self.trace_log: list[dict[str, Any]] = []
        self.write_buffer: list[str] = []
        self.pending_inputs: list[tuple[str, str, Union[str, int, float]]] = []

        self._initialize_working_memory()

    def _alloc_id(self, prefix: str = "O") -> str:
        letter = prefix[0].upper() if prefix else "O"
        if not letter.isalpha():
            letter = "X"
        idx = self._id_counters.get(letter, 0) + 1
        self._id_counters[letter] = idx
        return f"{letter}{idx}"

    def _initialize_working_memory(self) -> None:
        """Initialize the root state `S1` and `^io` links."""
        self.wmes.clear()
        self.preferences.clear()
        self.instantiations.clear()
        self.fired_Instantiation_signatures.clear()
        self._next_timetag = 1
        self._next_pref_id = 1
        self._next_inst_id = 1
        self._id_counters = {"S": 1, "I": 3}

        self.cycle_count = 0
        self.current_phase = DecisionPhase.INPUT
        self.halted = False
        self.state_stack = ["S1"]
        self.active_impasses.clear()
        self.total_impasses_count = 0
        self.trace_log.clear()
        self.write_buffer.clear()

        self._add_wme("S1", "type", "state", support="arch", state_id="S1")
        self._add_wme("S1", "superstate", "nil", support="arch", state_id="S1")
        self._add_wme("S1", "io", "I1", support="arch", state_id="S1")
        self._add_wme("I1", "input-link", "I2", support="arch", state_id="S1")
        self._add_wme("I1", "output-link", "I3", support="arch", state_id="S1")

    def reset_working_memory(self, keep_chunks: bool = True) -> None:
        """Reset Working Memory to `S1` while optionally keeping learned `chunk*` productions."""
        if not keep_chunks:
            to_delete = [name for name, p in self.productions.items() if p.is_chunk]
            for name in to_delete:
                del self.productions[name]
            self.learned_chunks.clear()
        self._initialize_working_memory()
        self._log("SYSTEM", "Working memory reset (keep_chunks=" + str(keep_chunks) + ")")

    def load_soar_code(self, source: str, clear_existing: bool = False) -> list[Production]:
        """Parse and load `.soar` productions into Procedural Memory."""
        if clear_existing:
            self.productions.clear()
            self.learned_chunks.clear()
        parsed = parse_soar(source)
        for prod in parsed:
            self.productions[prod.name] = prod
            if prod.is_chunk and prod.name not in self.learned_chunks:
                self.learned_chunks.append(prod.name)
        self._log("LOAD", f"Loaded {len(parsed)} productions into Procedural Memory.")
        return parsed

    def add_input_wme(self, parent_id: str, attr: str, value: Union[str, int, float]) -> None:
        """Queue an external sensory input WME to be added during the next `INPUT` phase."""
        self.pending_inputs.append((parent_id, attr, value))

    def _log(self, category: str, message: str, details: dict[str, Any] | None = None) -> None:
        entry = {
            "cycle": self.cycle_count,
            "phase": self.current_phase.value,
            "category": category,
            "message": message,
            "details": details or {},
        }
        self.trace_log.append(entry)

    def _find_wme(self, id_str: str, attr: str, value: Union[str, int, float]) -> WME | None:
        for w in self.wmes.values():
            if w.id == id_str and w.attr == attr and w.value == value and type(w.value) is type(value):
                return w
        return None

    def _add_wme(
        self,
        id_str: str,
        attr: str,
        value: Union[str, int, float],
        support: str = "i",
        source_production: str | None = None,
        instantiation_id: int | None = None,
        supporting_timetags: tuple[int, ...] = (),
        state_id: str = "S1",
    ) -> WME:
        existing = self._find_wme(id_str, attr, value)
        if existing is not None:
            # Upgrade support to 'o' if an o-supported rule asserts an existing i-supported WME
            if support == "o" and existing.support == "i":
                existing.support = "o"
                existing.source_production = source_production
                existing.instantiation_id = instantiation_id
                existing.supporting_timetags = supporting_timetags
            return existing

        tt = self._next_timetag
        self._next_timetag += 1
        wme = WME(
            timetag=tt,
            id=id_str,
            attr=attr,
            value=value,
            support=support,
            source_production=source_production,
            instantiation_id=instantiation_id,
            supporting_timetags=supporting_timetags,
            state_id=state_id,
        )
        self.wmes[tt] = wme
        return wme

    def _remove_wme_by_tuple(self, id_str: str, attr: str, value: Union[str, int, float]) -> list[WME]:
        removed: list[WME] = []
        for tt, w in list(self.wmes.items()):
            if w.id == id_str and w.attr == attr and w.value == value:
                removed.append(w)
                del self.wmes[tt]
        return removed

    def _add_preference(
        self,
        id_str: str,
        attr: str,
        value: Union[str, int, float],
        pref_type: str,
        referent: Union[str, int, float, None] = None,
        support: str = "i",
        source_production: str | None = None,
        instantiation_id: int | None = None,
        supporting_timetags: tuple[int, ...] = (),
        state_id: str = "S1",
    ) -> Preference:
        for p in self.preferences.values():
            if (
                p.id == id_str
                and p.attr == attr
                and p.value == value
                and p.pref_type == pref_type
                and p.referent == referent
                and p.instantiation_id == instantiation_id
            ):
                return p
        pid = self._next_pref_id
        self._next_pref_id += 1
        pref = Preference(
            pref_id=pid,
            id=id_str,
            attr=attr,
            value=value,
            pref_type=pref_type,
            referent=referent,
            support=support,
            source_production=source_production,
            instantiation_id=instantiation_id,
            supporting_timetags=supporting_timetags,
            state_id=state_id,
        )
        self.preferences[pid] = pref
        return pref

    # -------------------------------------------------------------------------
    # Production Support Classification (`i-support` vs `o-support`)
    # -------------------------------------------------------------------------
    def classify_support(self, prod: Production) -> str:
        """Determine whether a production receives `i-support` or `o-support`."""
        if prod.support_flag == ":o-support":
            return "o"
        if prod.support_flag == ":i-support":
            return "i"

        # A rule that tests a selected operator `(<s> ^operator <o>)` on LHS
        # and modifies working memory (other than proposing operators) is an Operator Application rule -> o-support.
        state_vars: set[str] = set()
        for cond in prod.conditions:
            if cond.is_state:
                state_vars.add(cond.id_var.name)

        tested_operator_vars: set[str] = set()
        for cond in prod.conditions:
            if not cond.negated and cond.id_var.name in state_vars:
                for at in cond.attr_tests:
                    if not at.negated and at.attr == "operator" and not at.acceptable_pref:
                        if isinstance(at.value_test, EqualityTest) and isinstance(at.value_test.target, Var):
                            tested_operator_vars.add(at.value_test.target.name)

        if tested_operator_vars:
            # Check if RHS modifies state or state-linked structures (or removes WMEs)
            for act in prod.actions:
                if isinstance(act, RHSAction):
                    if act.is_removal:
                        return "o"
                    # If it modifies something other than elaborating the operator itself
                    if isinstance(act.id_sym, Var) and act.id_sym.name not in tested_operator_vars:
                        # Unless it's purely asserting an operator preference on the same state
                        if act.attr == "operator" and any(
                            p.pref_type in ("+", ">", "<", "=", "-", "!", "~") for p in act.preferences
                        ):
                            # Preferences asserted inside a substate onto a superstate get o-support
                            continue
                        return "o"
        return "i"

    # -------------------------------------------------------------------------
    # Pattern Matcher (Rete-style Unification + Relational & Negated Tests)
    # -------------------------------------------------------------------------
    def match_production(self, prod: Production) -> list[tuple[dict[str, Union[str, int, float]], tuple[int, ...]]]:
        """Find all valid variable bindings and supporting WME timetags for `prod`."""
        positive_conds = [c for c in prod.conditions if not c.negated]
        negated_conds = [c for c in prod.conditions if c.negated]

        results: list[tuple[dict[str, Union[str, int, float]], tuple[int, ...]]] = []

        def backtrack(
            idx: int,
            bindings: dict[str, Union[str, int, float]],
            timetags: list[int],
        ) -> None:
            if idx == len(positive_conds):
                # Verify all negated conditions fail under current bindings
                for ncond in negated_conds:
                    if self._condition_matches_any(ncond, bindings):
                        return
                results.append((dict(bindings), tuple(sorted(set(timetags)))))
                return

            cond = positive_conds[idx]
            for next_bindings, matched_tts in self._match_positive_condition(cond, bindings):
                backtrack(idx + 1, next_bindings, timetags + matched_tts)

        backtrack(0, {}, [])
        return results

    def _condition_matches_any(self, cond: Condition, bindings: dict[str, Union[str, int, float]]) -> bool:
        for _ in self._match_positive_condition(cond, bindings):
            return True
        return False

    def _match_positive_condition(
        self,
        cond: Condition,
        bindings: dict[str, Union[str, int, float]],
    ) -> list[tuple[dict[str, Union[str, int, float]], list[int]]]:
        """Match a single positive `Condition` (which may contain multiple `^attr` tests)."""
        # Determine candidate IDs for `cond.id_var`
        if cond.id_var.name in bindings:
            cand_ids = [str(bindings[cond.id_var.name])]
            if cond.is_state and not self._is_state_id(cand_ids[0]):
                return []
        else:
            if cond.is_state:
                cand_ids = list(self.state_stack)
            else:
                cand_ids = sorted({w.id for w in self.wmes.values()})

        matches: list[tuple[dict[str, Union[str, int, float]], list[int]]] = []
        pos_attr_tests = [at for at in cond.attr_tests if not at.negated]
        neg_attr_tests = [at for at in cond.attr_tests if at.negated]

        for cid in cand_ids:
            base_b = dict(bindings)
            base_b[cond.id_var.name] = cid

            def match_attrs(
                a_idx: int,
                cur_b: dict[str, Union[str, int, float]],
                cur_tts: list[int],
            ) -> None:
                if a_idx == len(pos_attr_tests):
                    # Check negated attribute tests `-^attr val`
                    for nat in neg_attr_tests:
                        if self._attr_test_matches(cid, nat, cur_b):
                            return
                    matches.append((cur_b, cur_tts))
                    return

                at = pos_attr_tests[a_idx]
                if at.attr == "__self__":
                    ok, next_b = self._eval_test(cid, at.value_test, cur_b)
                    if ok:
                        match_attrs(a_idx + 1, next_b, cur_tts)
                    return

                attr_name = self._resolve_symbol(at.attr, cur_b)
                if attr_name is None:
                    # Unbound attribute variable
                    for w in self.wmes.values():
                        if w.id != cid:
                            continue
                        b_with_attr = dict(cur_b)
                        assert isinstance(at.attr, Var)
                        b_with_attr[at.attr.name] = w.attr
                        ok, next_b = self._eval_test(w.value, at.value_test, b_with_attr)
                        if ok:
                            match_attrs(a_idx + 1, next_b, cur_tts + [w.timetag])
                else:
                    # Attribute name is known; search WMEs or acceptable operator preferences
                    if at.acceptable_pref:
                        for p in self.preferences.values():
                            if p.id == cid and p.attr == str(attr_name) and p.pref_type == "+":
                                ok, next_b = self._eval_test(p.value, at.value_test, cur_b)
                                if ok:
                                    # Record negative pref_id so TMS retracts dependents if the preference retracts
                                    match_attrs(
                                        a_idx + 1,
                                        next_b,
                                        cur_tts + [-p.pref_id] + list(p.supporting_timetags),
                                    )
                    else:
                        for w in self.wmes.values():
                            if w.id == cid and w.attr == str(attr_name):
                                ok, next_b = self._eval_test(w.value, at.value_test, cur_b)
                                if ok:
                                    match_attrs(a_idx + 1, next_b, cur_tts + [w.timetag])

            match_attrs(0, base_b, [])

        return matches

    def _attr_test_matches(
        self,
        cid: str,
        at: AttrValueTest,
        bindings: dict[str, Union[str, int, float]],
    ) -> bool:
        attr_name = self._resolve_symbol(at.attr, bindings)
        for w in self.wmes.values():
            if w.id != cid:
                continue
            if attr_name is not None and w.attr != str(attr_name):
                continue
            ok, _ = self._eval_test(w.value, at.value_test, bindings)
            if ok:
                return True
        return False

    def _is_state_id(self, id_str: str) -> bool:
        return any(w.id == id_str and w.attr == "type" and w.value == "state" for w in self.wmes.values())

    def _resolve_symbol(self, sym: Symbol, bindings: dict[str, Union[str, int, float]]) -> Union[str, int, float, None]:
        if isinstance(sym, Var):
            return bindings.get(sym.name)
        return sym

    def _eval_test(
        self,
        val: Union[str, int, float],
        test: Test,
        bindings: dict[str, Union[str, int, float]],
    ) -> tuple[bool, dict[str, Union[str, int, float]]]:
        if isinstance(test, EqualityTest):
            if isinstance(test.target, Var):
                vname = test.target.name
                if vname in bindings:
                    return (bindings[vname] == val and type(bindings[vname]) is type(val)), bindings
                nb = dict(bindings)
                nb[vname] = val
                return True, nb
            return (val == test.target and type(val) is type(test.target)), bindings

        if isinstance(test, DisjunctionTest):
            return (val in test.options), bindings

        if isinstance(test, RelationalTest):
            rhs = self._resolve_symbol(test.target, bindings)
            if rhs is None:
                return False, bindings
            op = test.op
            if op == "<>":
                return (val != rhs), bindings
            if op == "<=>":
                return (type(val) is type(rhs)), bindings
            try:
                f_lhs = float(val)
                f_rhs = float(rhs)
            except (ValueError, TypeError):
                return False, bindings
            if op == "<":
                return (f_lhs < f_rhs), bindings
            if op == ">":
                return (f_lhs > f_rhs), bindings
            if op == "<=":
                return (f_lhs <= f_rhs), bindings
            if op == ">=":
                return (f_lhs >= f_rhs), bindings
            return False, bindings

        if isinstance(test, ConjunctiveTest):
            cur_b = dict(bindings)
            for sub in test.tests:
                ok, cur_b = self._eval_test(val, sub, cur_b)
                if not ok:
                    return False, bindings
            return True, cur_b

        return False, bindings

    # -------------------------------------------------------------------------
    # RHS Execution & Expression Evaluation
    # -------------------------------------------------------------------------
    def _eval_rhs_value(
        self,
        rhs_val: Union[Symbol, RHSFuncCall],
        bindings: dict[str, Union[str, int, float]],
        rhs_new_vars: dict[str, str],
        hint_prefix: str = "O",
    ) -> Union[str, int, float]:
        if isinstance(rhs_val, Var):
            if rhs_val.name in bindings:
                return bindings[rhs_val.name]
            if rhs_val.name not in rhs_new_vars:
                rhs_new_vars[rhs_val.name] = self._alloc_id(hint_prefix or rhs_val.name[0].upper())
            return rhs_new_vars[rhs_val.name]

        if isinstance(rhs_val, RHSFuncCall):
            return self._exec_rhs_func(rhs_val, bindings, rhs_new_vars)

        return rhs_val

    def _exec_rhs_func(
        self,
        call: RHSFuncCall,
        bindings: dict[str, Union[str, int, float]],
        rhs_new_vars: dict[str, str],
    ) -> Union[str, int, float]:
        fn = call.func_name
        if fn == "crlf":
            return "\n"
        if fn == "halt":
            self.halted = True
            self._log("HALT", "Production executed (halt) action.")
            return "halt"
        if fn == "write":
            parts = [str(self._eval_rhs_value(a, bindings, rhs_new_vars)) for a in call.args]
            text = "".join(parts)
            self.write_buffer.append(text)
            self._log("WRITE", text.strip())
            return text

        eval_args = [self._eval_rhs_value(a, bindings, rhs_new_vars) for a in call.args]
        nums = [float(x) for x in eval_args]
        res: float = 0.0
        if fn == "+":
            res = sum(nums)
        elif fn == "-":
            res = nums[0] - sum(nums[1:]) if len(nums) > 1 else -nums[0]
        elif fn == "*":
            res = 1.0
            for x in nums:
                res *= x
        elif fn in ("/", "div"):
            res = nums[0] / nums[1] if nums[1] != 0 else 0.0
        elif fn == "min":
            res = min(nums)
        elif fn == "max":
            res = max(nums)
        else:
            return str(eval_args[0]) if eval_args else ""

        if res.is_integer():
            return int(res)
        return round(res, 6)

    def _fire_instantiation(
        self,
        prod: Production,
        bindings: dict[str, Union[str, int, float]],
        matched_timetags: tuple[int, ...],
        support: str,
    ) -> Instantiation:
        inst_id = self._next_inst_id
        self._next_inst_id += 1

        # Determine owning state for this instantiation
        state_id = "S1"
        for cond in prod.conditions:
            if cond.is_state and cond.id_var.name in bindings:
                state_id = str(bindings[cond.id_var.name])
                break

        inst = Instantiation(
            inst_id=inst_id,
            production_name=prod.name,
            bindings=dict(bindings),
            lhs_bindings=dict(bindings),
            matched_timetags=matched_timetags,
            support=support,
            state_id=state_id,
        )
        self.instantiations[inst_id] = inst

        rhs_new_vars: dict[str, str] = {}
        superstate_results: list[tuple[RHSAction, str, str, Any, list[PreferenceSpec]]] = []

        for act in prod.actions:
            if isinstance(act, RHSFunctionStandalone):
                self._exec_rhs_func(act.call, bindings, rhs_new_vars)
                continue

            id_val = str(self._eval_rhs_value(act.id_sym, bindings, rhs_new_vars, "O"))
            attr_val = str(self._eval_rhs_value(act.attr, bindings, rhs_new_vars, "A"))
            hint = "O" if attr_val == "operator" else (attr_val[0].upper() if attr_val else "X")
            val = self._eval_rhs_value(act.value, bindings, rhs_new_vars, hint)

            # Determine if this action targets a superstate from a substate!
            target_state = self._owner_state_of_id(id_val) or state_id
            is_cross_state_result = (
                state_id != "S1" and target_state != state_id and self._is_ancestor_state(target_state, state_id)
            )
            effective_support = "o" if is_cross_state_result else support

            if (
                act.is_removal
                and len(act.preferences) == 1
                and act.preferences[0].pref_type == "-"
                and attr_val != "operator"
            ):
                removed = self._remove_wme_by_tuple(id_val, attr_val, val)
                for rw in removed:
                    self._log(
                        "WME_REMOVE",
                        f"Retracted ({rw.id} ^{rw.attr} {rw.value}) via {prod.name}",
                    )
                continue

            # If this is an operator preference (`^operator`), store in Preference Memory
            if attr_val == "operator":
                for pspec in act.preferences:
                    ref_val = (
                        self._eval_rhs_value(pspec.referent, bindings, rhs_new_vars, "O")
                        if pspec.referent is not None
                        else None
                    )
                    pref = self._add_preference(
                        id_str=id_val,
                        attr=attr_val,
                        value=val,
                        pref_type=pspec.pref_type,
                        referent=ref_val,
                        support=effective_support,
                        source_production=prod.name,
                        instantiation_id=inst_id,
                        supporting_timetags=matched_timetags,
                        state_id=target_state,
                    )
                    inst.created_pref_ids.append(pref.pref_id)
                if is_cross_state_result:
                    superstate_results.append((act, id_val, attr_val, val, act.preferences))
            else:
                # Normal WME creation
                wme = self._add_wme(
                    id_str=id_val,
                    attr=attr_val,
                    value=val,
                    support=effective_support,
                    source_production=prod.name,
                    instantiation_id=inst_id,
                    supporting_timetags=matched_timetags,
                    state_id=target_state,
                )
                inst.created_wme_timetags.append(wme.timetag)
                if is_cross_state_result:
                    superstate_results.append((act, id_val, attr_val, val, act.preferences))

        merged_bindings = {**bindings, **rhs_new_vars}
        inst.bindings = merged_bindings

        self._log(
            "FIRE",
            f"Fired {prod.name} [{support}-support] in {state_id}",
            {"production": prod.name, "support": support, "state_id": state_id},
        )

        # If a substate rule produced a superstate preference/WME, compile a Chunk!
        if superstate_results and self.enable_chunking:
            self._compile_chunk_from_substate(prod, inst, superstate_results)

        return inst

    def _owner_state_of_id(self, obj_id: str) -> str | None:
        if obj_id in self.state_stack:
            return obj_id
        for w in self.wmes.values():
            if w.id == obj_id or w.value == obj_id:
                return w.state_id
        for p in self.preferences.values():
            if p.value == obj_id:
                return p.state_id
        return None

    def _is_ancestor_state(self, ancestor: str, descendant: str) -> bool:
        if ancestor not in self.state_stack or descendant not in self.state_stack:
            return False
        return self.state_stack.index(ancestor) < self.state_stack.index(descendant)

    # -------------------------------------------------------------------------
    # Truth Maintenance System (TMS) & Garbage Collection
    # -------------------------------------------------------------------------
    def _run_truth_maintenance(self) -> int:
        """Retract any `i-supported` instantiations whose conditions no longer hold."""
        retracted_count = 0
        changed = True
        while changed:
            changed = False
            for inst_id, inst in list(self.instantiations.items()):
                if inst.support != "i":
                    continue
                prod = self.productions.get(inst.production_name)
                if prod is None:
                    continue
                # Check if all matched positive WME timetags (tt > 0) and preference IDs (tt < 0) still exist
                still_valid = all(
                    (tt in self.wmes if tt > 0 else (-tt) in self.preferences) for tt in inst.matched_timetags
                )
                if still_valid:
                    for ncond in prod.conditions:
                        if ncond.negated and self._condition_matches_any(ncond, inst.lhs_bindings):
                            still_valid = False
                            break
                    if still_valid:
                        for pcond in prod.conditions:
                            if not pcond.negated:
                                cid = str(inst.lhs_bindings.get(pcond.id_var.name, ""))
                                for at in pcond.attr_tests:
                                    if at.negated and self._attr_test_matches(cid, at, inst.lhs_bindings):
                                        still_valid = False
                                        break

                if not still_valid:
                    self._retract_instantiation(inst)
                    retracted_count += 1
                    changed = True
                elif any(tt not in self.wmes for tt in inst.created_wme_timetags):
                    # If an o-supported rule removed an i-supported WME whose LHS still holds,
                    # clear its fired signature so the elaboration phase re-asserts it.
                    self._retract_instantiation(inst)
                    changed = True

        self._garbage_collect_disconnected_wmes()
        return retracted_count

    def _retract_instantiation(self, inst: Instantiation) -> None:
        self.instantiations.pop(inst.inst_id, None)
        for tt in inst.created_wme_timetags:
            if tt in self.wmes and self.wmes[tt].support == "i":
                still_supported = any(tt in other.created_wme_timetags for other in self.instantiations.values())
                if not still_supported:
                    del self.wmes[tt]
        for pid in inst.created_pref_ids:
            if pid in self.preferences and self.preferences[pid].support == "i":
                still_supported = any(pid in other.created_pref_ids for other in self.instantiations.values())
                if not still_supported:
                    del self.preferences[pid]
        sig_bindings = inst.lhs_bindings or inst.bindings
        sig = (
            inst.production_name,
            tuple(sorted((k, v) for k, v in sig_bindings.items())),
            inst.matched_timetags,
        )
        self.fired_Instantiation_signatures.discard(sig)

    def _garbage_collect_disconnected_wmes(self) -> None:
        """Remove WMEs whose identifiers are unreachable from active states or candidate operators."""
        reachable: set[str] = set(self.state_stack)
        # Candidate operators in active preferences are also roots while their preference lives
        for p in self.preferences.values():
            if p.id in reachable and isinstance(p.value, str) and _is_identifier(p.value):
                reachable.add(p.value)

        added = True
        while added:
            added = False
            for w in self.wmes.values():
                if w.id in reachable and isinstance(w.value, str) and _is_identifier(w.value):
                    if w.value not in reachable:
                        reachable.add(w.value)
                        added = True

        for tt, w in list(self.wmes.items()):
            if w.id not in reachable:
                del self.wmes[tt]

    # -------------------------------------------------------------------------
    # Chunking (Explanation-Based Learning via Dependency Backtracing)
    # -------------------------------------------------------------------------
    def _compile_chunk_from_substate(
        self,
        resolving_prod: Production,
        resolving_inst: Instantiation,
        superstate_results: list[tuple[RHSAction, str, str, Any, list[PreferenceSpec]]],
    ) -> Production | None:
        """Backtrace from a substate's superstate preference/action to pre-impasse superstate WMEs and compile a `chunk*` rule."""
        substate_id = resolving_inst.state_id
        # Find the superstate ID
        superstate_id = "S1"
        for w in self.wmes.values():
            if w.id == substate_id and w.attr == "superstate" and isinstance(w.value, str):
                superstate_id = w.value
                break

        # Identify any candidate operators referenced in `superstate_results`
        referenced_ids: set[str] = {superstate_id}
        for _, id_val, _, val, prefs in superstate_results:
            referenced_ids.add(id_val)
            if isinstance(val, str) and _is_identifier(val):
                referenced_ids.add(val)
            for p in prefs:
                if isinstance(p.referent, Var) and p.referent.name in resolving_inst.bindings:
                    ref_resolved = resolving_inst.bindings[p.referent.name]
                    if isinstance(ref_resolved, str) and _is_identifier(ref_resolved):
                        referenced_ids.add(ref_resolved)

        # Collect only WMEs causally tested by instantiations in `substate_id` leading up to `resolving_inst`
        visited_tt: set[int] = set()
        pre_impasse_wmes: list[WME] = []
        queue_tt = list(resolving_inst.matched_timetags)
        backtraced_insts: list[Instantiation] = [resolving_inst]
        seen_inst_ids: set[int] = {resolving_inst.inst_id}

        # Seed queue with supporting timetags of acceptable (+) preferences for referenced operators
        for op_id in referenced_ids - {superstate_id}:
            for p in self.preferences.values():
                if p.id == superstate_id and p.attr == "operator" and str(p.value) == op_id and p.pref_type == "+":
                    for k_tt in p.supporting_timetags:
                        if k_tt not in queue_tt:
                            queue_tt.append(k_tt)

        while queue_tt:
            tt = queue_tt.pop(0)
            if tt in visited_tt:
                continue
            visited_tt.add(tt)
            wme = self.wmes.get(tt)
            if wme is None:
                if tt < 0 and (-tt) in self.preferences:
                    pref = self.preferences[-tt]
                    if (
                        pref.id != superstate_id
                        and pref.instantiation_id is not None
                        and pref.instantiation_id in self.instantiations
                        and pref.instantiation_id not in seen_inst_ids
                    ):
                        seen_inst_ids.add(pref.instantiation_id)
                        backtraced_insts.append(self.instantiations[pref.instantiation_id])
                    for k_tt in pref.supporting_timetags:
                        if k_tt not in visited_tt:
                            queue_tt.append(k_tt)
                continue
            if wme.state_id == superstate_id:
                if wme not in pre_impasse_wmes:
                    pre_impasse_wmes.append(wme)
                if wme.support == "i" and wme.supporting_timetags:
                    for k_tt in wme.supporting_timetags:
                        if k_tt not in visited_tt:
                            queue_tt.append(k_tt)
            else:
                if (
                    wme.instantiation_id is not None
                    and wme.instantiation_id in self.instantiations
                    and wme.instantiation_id not in seen_inst_ids
                ):
                    seen_inst_ids.add(wme.instantiation_id)
                    backtraced_insts.append(self.instantiations[wme.instantiation_id])
                # Trace back through the substate rule that created this WME
                for k_tt in wme.supporting_timetags:
                    if k_tt not in visited_tt:
                        queue_tt.append(k_tt)

        # Ensure every referenced operator ID has at least its tested attributes (or defining attributes if none tested)
        for op_id in sorted(referenced_ids - {superstate_id}):
            if not any(w.id == op_id for w in pre_impasse_wmes):
                for w in self.wmes.values():
                    if w.state_id == superstate_id and w.id == op_id and w.attr not in ("io", "superstate", "type"):
                        pre_impasse_wmes.append(w)

        # Ensure any non-state, non-operator object ID in `pre_impasse_wmes` is transitively linked from `superstate_id`
        cand_op_set = {
            str(p.value)
            for p in self.preferences.values()
            if p.id == superstate_id and p.attr == "operator" and p.pref_type == "+"
        }
        linked_changed = True
        while linked_changed:
            linked_changed = False
            bound_values = {str(w.value) for w in pre_impasse_wmes if isinstance(w.value, str)}
            needed_ids = {
                w.id
                for w in pre_impasse_wmes
                if w.id != superstate_id and w.id not in cand_op_set and w.id not in bound_values
            }
            for nid in sorted(needed_ids):
                for w in self.wmes.values():
                    if w.state_id == superstate_id and w.value == nid and w not in pre_impasse_wmes:
                        pre_impasse_wmes.append(w)
                        linked_changed = True
                        break

        if not pre_impasse_wmes:
            return None

        # Variabilize concrete identifiers (`S1` -> `<s>`, `O1` -> `<o1>`, `J1` -> `<j1>`)
        id_to_var: dict[str, Var] = {superstate_id: Var("s")}
        counter_by_prefix: dict[str, int] = {}

        def var_for_id(concrete_id: str) -> Var:
            if concrete_id not in id_to_var:
                prefix = concrete_id[0].lower()
                idx = counter_by_prefix.get(prefix, 0) + 1
                counter_by_prefix[prefix] = idx
                id_to_var[concrete_id] = Var(f"{prefix}{idx}")
            return id_to_var[concrete_id]

        # Group pre-impasse WMEs by their `id`
        grouped: dict[str, list[WME]] = {}
        for w in sorted(pre_impasse_wmes, key=lambda x: x.timetag):
            if w.attr in ("io", "superstate", "type"):
                continue
            grouped.setdefault(w.id, []).append(w)

        chunk_conditions: list[Condition] = []
        # Ensure state condition comes first, including acceptable operator tests `^operator <o> +`
        state_attr_tests: list[AttrValueTest] = []
        for w in grouped.pop(superstate_id, []):
            val_sym: Symbol = var_for_id(w.value) if (isinstance(w.value, str) and _is_identifier(w.value)) else w.value
            state_attr_tests.append(AttrValueTest(attr=w.attr, value_test=EqualityTest(val_sym)))

        # Any referenced candidate operator in superstate preferences gets `^operator <o> +`
        cand_op_ids = {
            p.value
            for p in self.preferences.values()
            if p.id == superstate_id and p.attr == "operator" and p.pref_type == "+"
        }
        for ref_id in sorted(referenced_ids):
            if ref_id in cand_op_ids:
                state_attr_tests.append(
                    AttrValueTest(
                        attr="operator",
                        value_test=EqualityTest(var_for_id(ref_id)),
                        acceptable_pref=True,
                    )
                )

        # Preserve negated attribute tests (`-^attr val`) and negated conditions across all backtraced instantiations
        extra_negated_conds: list[Condition] = []
        extra_obj_neg_attrs: dict[str, list[AttrValueTest]] = {}

        def _variabilize_test_for(t: Test, lhs_b: dict[str, Union[str, int, float]]) -> Test:
            if isinstance(t, EqualityTest):
                if isinstance(t.target, Var) and t.target.name in lhs_b:
                    rv = lhs_b[t.target.name]
                    if isinstance(rv, str) and _is_identifier(rv):
                        return EqualityTest(var_for_id(rv))
                    return EqualityTest(rv)
                return t
            if isinstance(t, RelationalTest):
                if isinstance(t.target, Var) and t.target.name in lhs_b:
                    rv = lhs_b[t.target.name]
                    if isinstance(rv, str) and _is_identifier(rv):
                        return RelationalTest(op=t.op, target=var_for_id(rv))
                    return RelationalTest(op=t.op, target=rv)
                return t
            return t

        for inst_item in backtraced_insts:
            inst_prod = self.productions.get(inst_item.production_name)
            if inst_prod is None:
                continue
            for cond in inst_prod.conditions:
                bound_id = str(inst_item.lhs_bindings.get(cond.id_var.name, ""))
                if not bound_id or bound_id == substate_id:
                    continue
                if cond.negated and (bound_id == superstate_id or bound_id in id_to_var):
                    neg_cond = Condition(
                        id_var=var_for_id(bound_id),
                        attr_tests=[
                            AttrValueTest(
                                attr=at.attr,
                                value_test=_variabilize_test_for(at.value_test, inst_item.lhs_bindings),
                                negated=at.negated,
                                acceptable_pref=at.acceptable_pref,
                            )
                            for at in cond.attr_tests
                        ],
                        is_state=(bound_id == superstate_id),
                        negated=True,
                    )
                    if neg_cond not in extra_negated_conds:
                        extra_negated_conds.append(neg_cond)
                elif not cond.negated:
                    for at in cond.attr_tests:
                        if at.negated:
                            neg_at = AttrValueTest(
                                attr=at.attr,
                                value_test=_variabilize_test_for(at.value_test, inst_item.lhs_bindings),
                                negated=True,
                            )
                            if bound_id == superstate_id:
                                if neg_at not in state_attr_tests:
                                    state_attr_tests.append(neg_at)
                            elif bound_id in id_to_var or bound_id in grouped:
                                obj_list = extra_obj_neg_attrs.setdefault(bound_id, [])
                                if neg_at not in obj_list:
                                    obj_list.append(neg_at)

        chunk_conditions.append(Condition(id_var=var_for_id(superstate_id), attr_tests=state_attr_tests, is_state=True))
        chunk_conditions.extend(extra_negated_conds)

        for obj_id, wlist in grouped.items():
            attr_tests: list[AttrValueTest] = []
            seen_pairs: set[tuple[str, Any]] = set()
            for w in wlist:
                if (w.attr, w.value) in seen_pairs:
                    continue
                seen_pairs.add((w.attr, w.value))
                val_sym = var_for_id(w.value) if (isinstance(w.value, str) and _is_identifier(w.value)) else w.value
                attr_tests.append(AttrValueTest(attr=w.attr, value_test=EqualityTest(val_sym)))
            attr_tests.extend(extra_obj_neg_attrs.pop(obj_id, []))
            if attr_tests:
                chunk_conditions.append(Condition(id_var=var_for_id(obj_id), attr_tests=attr_tests, is_state=False))

        for obj_id, neg_ats in extra_obj_neg_attrs.items():
            if neg_ats:
                chunk_conditions.append(Condition(id_var=var_for_id(obj_id), attr_tests=neg_ats, is_state=False))

        # Build RHS actions for the chunk
        chunk_actions: list[Union[RHSAction, RHSFunctionStandalone]] = []
        for act, id_val, attr_val, val, prefs in superstate_results:
            rhs_id = var_for_id(id_val) if _is_identifier(id_val) else id_val
            rhs_v: Symbol = var_for_id(val) if (isinstance(val, str) and _is_identifier(val)) else val
            new_prefs: list[PreferenceSpec] = []
            for p in prefs:
                new_ref: RHSValue | None = None
                if p.referent is not None:
                    ref_concrete = self._eval_rhs_value(p.referent, resolving_inst.bindings, {})
                    new_ref = (
                        var_for_id(ref_concrete)
                        if (isinstance(ref_concrete, str) and _is_identifier(ref_concrete))
                        else ref_concrete
                    )
                new_prefs.append(PreferenceSpec(pref_type=p.pref_type, referent=new_ref))
            chunk_actions.append(RHSAction(id_sym=rhs_id, attr=attr_val, value=rhs_v, preferences=new_prefs))

        chunk_idx = len(self.learned_chunks) + 1
        chunk_name = f"chunk*{resolving_prod.name}*c{chunk_idx}"
        chunk_prod = Production(
            name=chunk_name,
            conditions=chunk_conditions,
            actions=chunk_actions,
            doc=f"Compiled by Soar Chunking from substate {substate_id} via {resolving_prod.name}",
            support_flag=":i-support",
            is_chunk=True,
        )
        chunk_prod.source_text = format_production(chunk_prod)

        # Avoid duplicate identical chunks (compare conditions & actions without name)
        for existing in self.productions.values():
            if (
                existing.is_chunk
                and existing.conditions == chunk_prod.conditions
                and existing.actions == chunk_prod.actions
            ):
                return existing

        self.productions[chunk_name] = chunk_prod
        self.learned_chunks.append(chunk_name)
        self._log(
            "CHUNK_LEARNED",
            f"Compiled permanent chunk rule `{chunk_name}` from {substate_id} backtrace!",
            {"chunk_name": chunk_name, "source_text": chunk_prod.source_text},
        )
        return chunk_prod

    # -------------------------------------------------------------------------
    # Preference Calculus & Impasse Detection
    # -------------------------------------------------------------------------
    def evaluate_operator_preferences(self, state_id: str) -> tuple[str | None, ImpasseInfo | None, dict[str, Any]]:
        """
        Execute Soar's Preference Semantics Calculus for `(state_id ^operator ...)`.
        Returns `(winning_operator_id, impasse_info, breakdown_dict)`.
        """
        prefs = [p for p in self.preferences.values() if p.id == state_id and p.attr == "operator"]

        acceptable = sorted({str(p.value) for p in prefs if p.pref_type == "+"})
        rejected = {str(p.value) for p in prefs if p.pref_type == "-"}
        prohibited = {str(p.value) for p in prefs if p.pref_type == "~"}
        required = sorted({str(p.value) for p in prefs if p.pref_type == "!"})

        breakdown: dict[str, Any] = {
            "state_id": state_id,
            "acceptable": acceptable,
            "rejected": sorted(rejected),
            "prohibited": sorted(prohibited),
            "required": required,
            "better_pairs": [],
            "indifferent": [],
            "numeric_rl": {},
        }

        # 1. Require (!) check (in Soar, require overrides reject `-`, conflicting only with prohibit `~` or multiple `!`)
        if required:
            if len(required) > 1 or any(r in prohibited for r in required):
                imp = ImpasseInfo(
                    state_id=state_id,
                    impasse_type="constraint-failure",
                    attribute="operator",
                    candidates=required,
                    reason="Conflicting require/prohibit preferences on operators.",
                )
                return None, imp, breakdown
            return required[0], None, breakdown

        # 2. Filter Acceptable (+) by Reject (-) and Prohibit (~)
        candidates = [c for c in acceptable if c not in rejected and c not in prohibited]
        if not candidates:
            imp = ImpasseInfo(
                state_id=state_id,
                impasse_type="no-change",
                attribute="state",
                candidates=[],
                reason=f"No acceptable unrejected operators proposed in {state_id}.",
            )
            return None, imp, breakdown

        # 3. Binary Better (>) and Worse (<) dominance
        better_edges: set[tuple[str, str]] = set()
        for p in prefs:
            if p.pref_type == ">" and p.referent is not None:
                better_edges.add((str(p.value), str(p.referent)))
            elif p.pref_type == "<" and p.referent is not None:
                better_edges.add((str(p.referent), str(p.value)))

        breakdown["better_pairs"] = [f"{a} > {b}" for a, b in sorted(better_edges)]

        # Check for mutual or cyclic dominance conflicts among active candidates
        cand_set = set(candidates)
        for a, b in better_edges:
            if a in cand_set and b in cand_set and (b, a) in better_edges and a != b:
                imp = ImpasseInfo(
                    state_id=state_id,
                    impasse_type="conflict",
                    attribute="operator",
                    candidates=sorted({a, b}),
                    reason=f"Conflicting dominance preferences: {a} > {b} and {b} > {a}.",
                )
                return None, imp, breakdown

        dominated = {b for (a, b) in better_edges if a in cand_set and b in cand_set and a != b}
        pre_dom_candidates = list(candidates)
        candidates = [c for c in candidates if c not in dominated]
        if not candidates:
            imp = ImpasseInfo(
                state_id=state_id,
                impasse_type="conflict",
                attribute="operator",
                candidates=pre_dom_candidates,
                reason=f"Cyclic dominance conflict among operators: {', '.join(pre_dom_candidates)}.",
            )
            return None, imp, breakdown

        # 4. Unary Best (>) and Worst (<)
        unary_best = {str(p.value) for p in prefs if p.pref_type == ">" and p.referent is None}
        unary_worst = {str(p.value) for p in prefs if p.pref_type == "<" and p.referent is None}

        best_cands = [c for c in candidates if c in unary_best]
        if best_cands:
            candidates = best_cands

        non_worst_cands = [c for c in candidates if c not in unary_worst]
        if non_worst_cands:
            candidates = non_worst_cands

        if len(candidates) == 1:
            return candidates[0], None, breakdown

        # 5. Indifferent (=) and Numeric RL (= 0.75) check (non-transitive binary indifference per Soar manual)
        unary_indifferent: set[str] = set()
        binary_indifferent_pairs: set[tuple[str, str]] = set()
        rl_scores: dict[str, float] = {}
        for p in prefs:
            if p.pref_type == "=":
                op_str = str(p.value)
                if p.referent is None:
                    unary_indifferent.add(op_str)
                elif isinstance(p.referent, (int, float)):
                    unary_indifferent.add(op_str)
                    rl_scores[op_str] = rl_scores.get(op_str, 0.0) + float(p.referent)
                elif isinstance(p.referent, str):
                    ref_str = str(p.referent)
                    binary_indifferent_pairs.add((op_str, ref_str))
                    binary_indifferent_pairs.add((ref_str, op_str))

        indifferent_ops = unary_indifferent | {a for (a, _) in binary_indifferent_pairs}
        breakdown["indifferent"] = sorted(indifferent_ops)
        breakdown["numeric_rl"] = rl_scores

        def _pair_is_indifferent(c1: str, c2: str) -> bool:
            if c1 == c2:
                return True
            if c1 in unary_indifferent and c2 in unary_indifferent:
                return True
            return (c1, c2) in binary_indifferent_pairs

        all_mutually_indifferent = all(
            _pair_is_indifferent(candidates[i], candidates[j])
            for i in range(len(candidates))
            for j in range(i + 1, len(candidates))
        )

        if all_mutually_indifferent:
            winner = max(candidates, key=lambda c: rl_scores.get(c, 0.0))
            return winner, None, breakdown

        # Otherwise: Tie Impasse!
        imp = ImpasseInfo(
            state_id=state_id,
            impasse_type="tie",
            attribute="operator",
            candidates=candidates,
            reason=f"Tie impasse between {len(candidates)} incomparable operators: {', '.join(candidates)}",
        )
        return None, imp, breakdown

    def _create_substate_for_impasse(self, imp: ImpasseInfo) -> str:
        """Create architectural substate `S2` (or `S3`, ...) for an unresolved impasse."""
        sub_id = self._alloc_id("S")
        imp.substate_id = sub_id
        self.state_stack.append(sub_id)
        self.active_impasses[imp.state_id] = imp
        self.total_impasses_count += 1

        self._add_wme(sub_id, "type", "state", support="arch", state_id=sub_id)
        self._add_wme(sub_id, "superstate", imp.state_id, support="arch", state_id=sub_id)
        self._add_wme(sub_id, "impasse", imp.impasse_type, support="arch", state_id=sub_id)
        self._add_wme(sub_id, "attribute", imp.attribute, support="arch", state_id=sub_id)
        self._add_wme(
            sub_id,
            "choices",
            "multiple" if len(imp.candidates) > 1 else "none",
            support="arch",
            state_id=sub_id,
        )
        self._add_wme(sub_id, "quiescence", "t", support="arch", state_id=sub_id)
        for cand_op in imp.candidates:
            # Link `^item` WME to the supporting timetags of the candidate's acceptable (+) preference
            cand_tts: list[int] = []
            for p in self.preferences.values():
                if p.id == imp.state_id and p.attr == "operator" and str(p.value) == cand_op and p.pref_type == "+":
                    cand_tts.extend(p.supporting_timetags)
            self._add_wme(
                sub_id,
                "item",
                cand_op,
                support="arch",
                supporting_timetags=tuple(sorted(set(cand_tts))),
                state_id=sub_id,
            )

        self._log(
            "IMPASSE",
            f"Impasse [{imp.impasse_type}] in {imp.state_id} -> Created substate {sub_id} with items {imp.candidates}",
            imp.to_dict(),
        )
        return sub_id

    def _pop_substates_from(self, state_id: str) -> None:
        """Retract all substates below `state_id` once its impasse is resolved."""
        if state_id not in self.state_stack:
            return
        idx = self.state_stack.index(state_id)
        removed_states = self.state_stack[idx + 1 :]
        if not removed_states:
            return

        self.state_stack = self.state_stack[: idx + 1]
        for rs in removed_states:
            self.active_impasses.pop(rs, None)
        self.active_impasses.pop(state_id, None)

        # Remove all WMEs, preferences, and instantiations owned by removed substates
        for tt, w in list(self.wmes.items()):
            if w.state_id in removed_states or w.id in removed_states:
                del self.wmes[tt]
        for pid, p in list(self.preferences.items()):
            if p.state_id in removed_states or p.id in removed_states:
                del self.preferences[pid]
        for iid, inst in list(self.instantiations.items()):
            if inst.state_id in removed_states:
                self._retract_instantiation(inst)

        self._garbage_collect_disconnected_wmes()
        self._log(
            "IMPASSE_RESOLVED",
            f"Resolved impasse in {state_id}; popped substate(s) {removed_states}.",
        )

    # -------------------------------------------------------------------------
    # 5-Phase Execution Engine
    # -------------------------------------------------------------------------
    def step_phase(self) -> dict[str, Any]:
        """Execute a single phase of the Soar 5-phase Decision Cycle and advance `current_phase`."""
        if self.halted:
            return {"halted": True, "phase": self.current_phase.value, "cycle": self.cycle_count}

        executed_phase = self.current_phase

        if executed_phase == DecisionPhase.INPUT:
            self.cycle_count += 1
            for pid, attr, val in self.pending_inputs:
                self._add_wme(pid, attr, val, support="arch", state_id="S1")
            if self.pending_inputs:
                self._log("INPUT", f"Processed {len(self.pending_inputs)} sensory input WMEs.")
            self.pending_inputs.clear()
            self.current_phase = DecisionPhase.ELABORATION

        elif executed_phase == DecisionPhase.ELABORATION:
            self._run_elaboration_phase()
            self.current_phase = DecisionPhase.DECISION

        elif executed_phase == DecisionPhase.DECISION:
            self._run_decision_phase()
            self.current_phase = DecisionPhase.APPLICATION

        elif executed_phase == DecisionPhase.APPLICATION:
            self._run_application_phase()
            self.current_phase = DecisionPhase.OUTPUT

        elif executed_phase == DecisionPhase.OUTPUT:
            self._run_output_phase()
            self.current_phase = DecisionPhase.INPUT

        return {
            "executed_phase": executed_phase.value,
            "next_phase": self.current_phase.value,
            "cycle": self.cycle_count,
            "halted": self.halted,
        }

    def step_cycle(self) -> dict[str, Any]:
        """Run one complete 5-phase Decision Cycle (finishing the current cycle back to `INPUT`)."""
        if self.halted:
            return self.get_snapshot()
        # Step at least once, and continue until current_phase returns to INPUT or halted
        self.step_phase()
        while self.current_phase != DecisionPhase.INPUT and not self.halted:
            self.step_phase()
        return self.get_snapshot()

    def run_until_impasse(self, max_cycles: int = 30) -> dict[str, Any]:
        """Run decision cycles until an Impasse is active (substate created) or agent halts."""
        initial_impasses = self.total_impasses_count
        steps = 0
        while not self.halted and steps < max_cycles:
            self.step_cycle()
            steps += 1
            if self.total_impasses_count > initial_impasses or len(self.state_stack) > 1:
                break
        return self.get_snapshot()

    def run_to_completion(self, max_cycles: int = 50) -> dict[str, Any]:
        """Run decision cycles until `(halt)` fires or `max_cycles` is reached."""
        steps = 0
        while not self.halted and steps < max_cycles:
            self.step_cycle()
            steps += 1
        return self.get_snapshot()

    def replay_with_chunks(self, max_cycles: int = 50) -> dict[str, Any]:
        """Reset Working Memory while retaining learned `chunk*` rules, then run to completion."""
        self.reset_working_memory(keep_chunks=True)
        return self.run_to_completion(max_cycles=max_cycles)

    def _run_elaboration_phase(self) -> None:
        """Fire all matching `i-supported` elaboration and operator proposal rules to quiescence."""
        self._run_truth_maintenance()
        for _wave in range(20):
            fired_in_wave = 0
            for prod in list(self.productions.values()):
                if self.classify_support(prod) != "i":
                    continue
                for bindings, tts in self.match_production(prod):
                    sig = (
                        prod.name,
                        tuple(sorted((k, v) for k, v in bindings.items())),
                        tts,
                    )
                    if sig in self.fired_Instantiation_signatures:
                        continue
                    self.fired_Instantiation_signatures.add(sig)
                    self._fire_instantiation(prod, bindings, tts, support="i")
                    fired_in_wave += 1
                    if self.halted:
                        return
            self._run_truth_maintenance()
            if fired_in_wave == 0:
                break

    def _run_decision_phase(self) -> None:
        """Evaluate operator preferences down the state stack; select operator or create Impasse substate."""
        # First check if any ancestor state with an active impasse can now resolve its impasse!
        for st_id in list(self.state_stack):
            if st_id not in self.state_stack:
                continue
            winner, imp, _ = self.evaluate_operator_preferences(st_id)
            if winner is not None:
                existing_imp = self.active_impasses.get(st_id)
                if (
                    existing_imp is not None
                    and existing_imp.impasse_type == "no-change"
                    and existing_imp.attribute == "operator"
                    and existing_imp.candidates == [winner]
                ):
                    # Keep the existing operator no-change substate alive while the same unapplied operator remains selected
                    self._install_selected_operator(st_id, winner)
                    continue
                # If this state previously had an impasse, pop all substates beneath it!
                if st_id in self.active_impasses or self.state_stack[-1] != st_id:
                    self._pop_substates_from(st_id)
                self._install_selected_operator(st_id, winner)
                return
            # If st_id is the deepest state and hit an impasse, create a new substate
            if st_id == self.state_stack[-1] and imp is not None:
                self._create_substate_for_impasse(imp)
                return

    def _install_selected_operator(self, state_id: str, operator_id: str) -> None:
        """Install `(state_id ^operator operator_id)` as the active operator for `state_id`."""
        # Remove any previous active `(state_id ^operator ...)` WME
        for tt, w in list(self.wmes.items()):
            if w.id == state_id and w.attr == "operator":
                if w.value == operator_id:
                    return
                del self.wmes[tt]

        self._add_wme(
            state_id,
            "operator",
            operator_id,
            support="arch",
            source_production="DECISION_PHASE",
            state_id=state_id,
        )
        op_name = self.get_operator_name(operator_id)
        self._log(
            "DECISION",
            f"Selected operator {operator_id} ({op_name}) in {state_id}",
            {"state_id": state_id, "operator_id": operator_id, "operator_name": op_name},
        )

    def _run_application_phase(self) -> None:
        """Fire `o-supported` operator application rules (and interleaved `i-supported` elaborations) in waves to quiescence."""
        total_fired = 0
        fired_prods_this_cycle: set[str] = set()

        for _wave in range(50):
            pending_firings: list[tuple[Production, dict[str, Union[str, int, float]], tuple[int, ...], str]] = []
            for prod in list(self.productions.values()):
                sup = self.classify_support(prod)
                if sup == "o":
                    if prod.name in fired_prods_this_cycle:
                        continue
                else:
                    if any(isinstance(a, RHSAction) and a.attr == "operator" for a in prod.actions):
                        continue
                for bindings, tts in self.match_production(prod):
                    sig = (
                        prod.name,
                        tuple(sorted((k, v) for k, v in bindings.items())),
                        tts,
                    )
                    if sig in self.fired_Instantiation_signatures:
                        continue
                    self.fired_Instantiation_signatures.add(sig)
                    pending_firings.append((prod, bindings, tts, sup))

            if not pending_firings:
                break

            for prod, bindings, tts, sup in pending_firings:
                if sup == "o":
                    total_fired += 1
                    if any(isinstance(a, RHSAction) and a.is_removal for a in prod.actions):
                        fired_prods_this_cycle.add(prod.name)
                self._fire_instantiation(prod, bindings, tts, support=sup)
                if self.halted:
                    break
            self._run_truth_maintenance()
            if self.halted:
                break

        # Check if an operator was selected on the deepest state but zero application rules fired -> operator no-change impasse
        active_op_wmes = [w for w in self.wmes.values() if w.attr == "operator" and w.support == "arch"]
        if active_op_wmes and total_fired == 0 and not self.halted:
            op_wme = active_op_wmes[-1]
            existing_imp = self.active_impasses.get(op_wme.id)
            if not (
                existing_imp is not None
                and existing_imp.impasse_type == "no-change"
                and existing_imp.attribute == "operator"
                and existing_imp.candidates == [str(op_wme.value)]
            ):
                imp = ImpasseInfo(
                    state_id=op_wme.id,
                    impasse_type="no-change",
                    attribute="operator",
                    candidates=[str(op_wme.value)],
                    reason=f"Selected operator {op_wme.value} ({self.get_operator_name(str(op_wme.value))}) had no applicable rules.",
                )
                self._create_substate_for_impasse(imp)

        # After application rules fire, remove the architectural `(<s> ^operator <o>)` WME
        # and retracted i-supported operator proposals so the next cycle starts cleanly.
        for tt, w in list(self.wmes.items()):
            if w.attr == "operator" and w.support == "arch":
                del self.wmes[tt]
        # Clean up consumed cross-state operator dominance preferences for operators that have finished applying
        if total_fired > 0:
            for pid, p in list(self.preferences.items()):
                if p.attr == "operator" and p.support == "o" and p.pref_type in (">", "<", "="):
                    del self.preferences[pid]
        self._run_truth_maintenance()

        # Run non-proposal i-supported state elaborations (such as derived attributes and goal detection)
        for prod in list(self.productions.values()):
            if self.classify_support(prod) != "i":
                continue
            if any(isinstance(a, RHSAction) and a.attr == "operator" for a in prod.actions):
                continue
            for bindings, tts in self.match_production(prod):
                sig = (
                    prod.name,
                    tuple(sorted((k, v) for k, v in bindings.items())),
                    tts,
                )
                if sig in self.fired_Instantiation_signatures:
                    continue
                self.fired_Instantiation_signatures.add(sig)
                self._fire_instantiation(prod, bindings, tts, support="i")
                if self.halted:
                    return

    def _run_output_phase(self) -> None:
        """Inspect `(I1 ^output-link I3)` for any motor output commands."""
        outputs = [w.to_dict() for w in self.wmes.values() if w.id == "I3"]
        if outputs:
            self._log("OUTPUT", f"Emitted {len(outputs)} output-link commands.", {"outputs": outputs})

    # -------------------------------------------------------------------------
    # Introspection & Snapshot Serialization for UI / Debugger
    # -------------------------------------------------------------------------
    def get_operator_name(self, op_id: str) -> str:
        for w in self.wmes.values():
            if w.id == op_id and w.attr == "name":
                return str(w.value)
        return op_id

    def get_operator_details(self, op_id: str) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        for w in self.wmes.values():
            if w.id == op_id:
                attrs[w.attr] = w.value
        return {"id": op_id, "name": attrs.get("name", op_id), "attributes": attrs}

    def get_snapshot(self) -> dict[str, Any]:
        """Return a complete, JSON-serializable snapshot of the Soar Kernel state."""
        deepest_state = self.state_stack[-1] if self.state_stack else "S1"
        winner, imp, calc_breakdown = self.evaluate_operator_preferences("S1")

        # Enrich candidate operators with human-readable descriptions
        op_candidates: list[dict[str, Any]] = []
        for op_id in calc_breakdown["acceptable"]:
            details = self.get_operator_details(op_id)
            prefs_for_op = [
                f"{p.pref_type}{(' ' + str(p.referent)) if p.referent is not None else ''}"
                for p in self.preferences.values()
                if p.id == "S1" and p.attr == "operator" and str(p.value) == op_id
            ]
            op_candidates.append(
                {
                    "operator_id": op_id,
                    "name": details["name"],
                    "attributes": details["attributes"],
                    "preferences": prefs_for_op,
                    "status": (
                        "rejected"
                        if op_id in calc_breakdown["rejected"] or op_id in calc_breakdown["prohibited"]
                        else ("selected" if winner == op_id else "tied/candidate")
                    ),
                }
            )

        return {
            "cycle": self.cycle_count,
            "current_phase": self.current_phase.value,
            "halted": self.halted,
            "state_stack": list(self.state_stack),
            "deepest_state": deepest_state,
            "active_impasses": {k: v.to_dict() for k, v in self.active_impasses.items()},
            "total_impasses_count": self.total_impasses_count,
            "wmes": [w.to_dict() for w in sorted(self.wmes.values(), key=lambda x: x.timetag)],
            "preferences": [p.to_dict() for p in sorted(self.preferences.values(), key=lambda x: x.pref_id)],
            "preference_calculus": calc_breakdown,
            "operator_candidates": op_candidates,
            "productions": [
                {
                    "name": p.name,
                    "doc": p.doc,
                    "support": self.classify_support(p),
                    "is_chunk": p.is_chunk,
                    "source_text": p.source_text,
                }
                for p in self.productions.values()
            ],
            "learned_chunks": [
                {
                    "name": cname,
                    "source_text": self.productions[cname].source_text,
                }
                for cname in self.learned_chunks
                if cname in self.productions
            ],
            "write_output": list(self.write_buffer),
            "trace_log": self.trace_log[-60:],
        }
