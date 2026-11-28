"""S1 -> S2 deliberation loop: resolve tie impasses inside the substate, then chunk them away.

When Soar hits a tie on ``S1`` the kernel builds substate ``S2`` with one ``^item`` per
tied operator. Hand-written benchmarks ship ``*substate*`` evaluation rules that fire
in ``S2``; this module removes the need for them. A *resolver* picks the winner while the
agent sits in ``S2``, the choice is compiled into a general ``S2`` evaluation production,
the kernel fires it, and Explanation-Based Chunking turns the result into a ``chunk*`` rule.
Replaying with the chunks then solves the task with zero impasses and zero resolver calls.

Resolvers:

* :class:`HeuristicResolver` - domain scoring, no search (what the old bridge did on ``S1``).
* :class:`LookaheadResolver` - classic Soar selection-space evaluation: simulate each
  candidate in a cloned kernel and prefer the one that reaches ``(halt)`` in fewest cycles.
* :class:`EnergyResolver` - lowest energy from an external model (sovereign-kb plugs in VaultJEPA).
* :class:`LLMResolver` - asks an OpenAI-compatible endpoint (gx10 ``qwen3.6-35b-a3b``)
  to choose. Its answer is validated against working memory; the rule is always written
  by the symbolic compiler, never by the model, so a bad answer cannot inject code.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .ast_nodes import Production
from .kernel import ImpasseInfo, SoarKernel, _is_identifier
from .parser import format_production, parse_soar

DEFAULT_LLM_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"
DEFAULT_LLM_MODEL = "qwen3.6-35b-a3b"


# ---------------------------------------------------------------------------
# Source preparation
# ---------------------------------------------------------------------------
def is_substate_evaluation_rule(prod: Production) -> bool:
    """True for productions that test ``^impasse`` on a state (hand-written S2 control knowledge)."""
    return any(cond.is_state and any(at.attr == "impasse" for at in cond.attr_tests) for cond in prod.conditions)


def strip_substate_control(source: str) -> tuple[str, list[str]]:
    """Remove hand-written S2 evaluation rules so every tie must be deliberated. Returns ``(source, removed)``."""
    kept: list[str] = []
    removed: list[str] = []
    for prod in parse_soar(source):
        if is_substate_evaluation_rule(prod):
            removed.append(prod.name)
        else:
            kept.append(format_production(prod))
    return "\n\n".join(kept) + "\n", removed


# ---------------------------------------------------------------------------
# Context & decisions
# ---------------------------------------------------------------------------
def problem_name(kernel: SoarKernel, state_id: str = "S1") -> str:
    return next((str(w.value) for w in kernel.wmes.values() if w.id == state_id and w.attr == "name"), "")


def literal_attributes(kernel: SoarKernel, op_id: str) -> dict[str, Any]:
    """Operator attributes whose values are constants (identifier links are not generalisable)."""
    attrs = kernel.get_operator_details(op_id)["attributes"]
    return {k: v for k, v in sorted(attrs.items()) if not (isinstance(v, str) and _is_identifier(v))}


def state_triples(kernel: SoarKernel, root: str = "S1", depth: int = 2) -> list[tuple[str, str, Any]]:
    """Working-memory triples reachable from ``root`` (skips architecture links and operator objects)."""
    skip_attrs = {"io", "superstate", "type", "operator"}
    out: list[tuple[str, str, Any]] = []
    frontier = [root]
    seen = {root}
    for _ in range(depth):
        nxt: list[str] = []
        for wme in sorted(kernel.wmes.values(), key=lambda w: w.timetag):
            if wme.id in frontier and wme.attr not in skip_attrs:
                out.append((wme.id, wme.attr, wme.value))
                if isinstance(wme.value, str) and _is_identifier(wme.value) and wme.value not in seen:
                    seen.add(wme.value)
                    nxt.append(wme.value)
        frontier = nxt
    return out


@dataclass
class Decision:
    winner: str
    rationale: str
    engine: str
    latency_ms: float = 0.0
    llm_calls: int = 0
    llm_valid: bool | None = None
    scores: dict[str, float] = field(default_factory=dict)


class Resolver(Protocol):
    name: str

    def choose(self, kernel: SoarKernel, impasse: ImpasseInfo) -> Decision: ...


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------
def domain_score(problem: str, name: str, attrs: dict[str, Any]) -> float:
    """Hand-tuned progress heuristic per benchmark problem space (higher is better)."""
    if problem == "water-jugs":
        if name == "pour-jug":
            return 30.0
        if name == "fill-jug":
            return 20.0 + float(attrs.get("jug-volume", 0))
        if name == "empty-jug":
            return 15.0 - float(attrs.get("jug-volume", 5))
    elif problem == "blocks-world":
        return 28.0 if attrs.get("to") == "table" else 20.0
    elif problem == "towers-of-hanoi":
        return 28.0 if attrs.get("to") == "B" else 18.0
    elif problem == "skb-vault":
        return float(attrs.get("authority", 1.0)) * 10.0 + float(attrs.get("rrf-score", 0.0)) * 100.0
    return 15.0


class HeuristicResolver:
    name = "heuristic"

    def choose(self, kernel: SoarKernel, impasse: ImpasseInfo) -> Decision:
        t0 = time.perf_counter()
        prob = problem_name(kernel, impasse.state_id)
        scores = {
            op: domain_score(prob, kernel.get_operator_name(op), kernel.get_operator_details(op)["attributes"])
            for op in impasse.candidates
        }
        winner = max(sorted(impasse.candidates), key=lambda op: scores[op])
        return Decision(
            winner=winner,
            rationale=f"domain heuristic score {scores[winner]:.1f} is the highest in `{prob}`",
            engine=self.name,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            scores=scores,
        )


class LookaheadResolver:
    """Selection-space evaluation: try each candidate in a cloned kernel and count cycles to ``(halt)``."""

    name = "lookahead"

    def __init__(self, horizon: int = 40, max_depth: int = 3) -> None:
        self.horizon = horizon
        self.max_depth = max_depth

    def _simulate(self, kernel: SoarKernel, impasse: ImpasseInfo, op: str, depth: int) -> float:
        clone = copy.deepcopy(kernel)
        clone.enable_chunking = False
        clone.trace_log = []
        install_s2_rule(clone, op, [c for c in impasse.candidates if c != op], tag="lookahead")
        inner: Callable[[SoarKernel, ImpasseInfo], Decision]
        if depth < self.max_depth:

            def nested(k: SoarKernel, imp: ImpasseInfo) -> Decision:
                return self.choose(k, imp, depth + 1)

            inner = nested
        else:
            inner = HeuristicResolver().choose
        report = _drive(clone, inner, max_cycles=self.horizon, compile_rules=True)
        return float(clone.cycle_count) if report["solved"] else float("inf")

    def choose(self, kernel: SoarKernel, impasse: ImpasseInfo, depth: int = 0) -> Decision:
        t0 = time.perf_counter()
        scores = {op: self._simulate(kernel, impasse, op, depth) for op in sorted(impasse.candidates)}
        winner = min(sorted(impasse.candidates), key=lambda op: scores[op])
        if scores[winner] == float("inf"):
            fallback = HeuristicResolver().choose(kernel, impasse)
            winner, why = fallback.winner, "no candidate reached (halt) within the horizon; heuristic tiebreak"
        else:
            why = f"reaches (halt) at cycle {int(scores[winner])} in simulation"
        return Decision(
            winner=winner,
            rationale=why,
            engine=self.name,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            scores={k: (v if v != float("inf") else -1.0) for k, v in scores.items()},
        )


class EnergyResolver:
    """Pick the candidate an external model scores lowest (energy-based model, e.g. sovereign-kb's VaultJEPA).

    ``energy_of`` receives an operator's constant attributes and returns its energy, or ``None``
    when the model has no opinion. If no candidate gets a finite energy, or the lowest is shared,
    the ``fallback`` resolver decides.
    """

    def __init__(
        self,
        energy_of: Callable[[dict[str, Any]], float | None],
        label: str = "energy",
        fallback: Resolver | None = None,
    ) -> None:
        self.energy_of = energy_of
        self.name = label
        self.fallback = fallback or HeuristicResolver()

    def choose(self, kernel: SoarKernel, impasse: ImpasseInfo) -> Decision:
        t0 = time.perf_counter()
        energies: dict[str, float] = {}
        for op in sorted(impasse.candidates):
            e = self.energy_of(literal_attributes(kernel, op))
            if e is not None and e == e and abs(e) != float("inf"):
                energies[op] = float(e)
        ranked = sorted(energies.items(), key=lambda kv: kv[1])
        if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
            # Only the candidates tied at the minimum (or all, if nothing was scored) go to the fallback.
            tied = [op for op, e in ranked if e == ranked[0][1]] if ranked else list(impasse.candidates)
            fb = self.fallback.choose(kernel, dataclasses.replace(impasse, candidates=tied))
            fb.engine = f"{fb.engine} ({self.name} fallback)"
            fb.rationale = f"{self.name} gave no strict minimum; {fb.rationale}"
            fb.scores = energies
            fb.latency_ms = (time.perf_counter() - t0) * 1000.0
            return fb
        winner, energy = ranked[0]
        return Decision(
            winner=winner,
            rationale=f"lowest {self.name} energy {energy:.4f} among {len(energies)} scored candidates",
            engine=self.name,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            scores=energies,
        )


class LLMResolver:
    """Ask an OpenAI-compatible LLM which tied operator to pick; fall back to the heuristic on any failure."""

    name = "llm"

    def __init__(
        self,
        endpoint: str | None = None,
        model: str = DEFAULT_LLM_MODEL,
        timeout_sec: float = 30.0,
        fallback: Resolver | None = None,
        api_key: str | None = None,
    ) -> None:
        # The gx10 proxy on :8001 needs a bearer key; the local :8000 endpoint does not.
        self.api_key = api_key or os.environ.get("SKB_LLM_API_KEY") or os.environ.get("GX10_API_KEY") or None
        self.endpoint = endpoint or os.environ.get("SKB_LLM_ENDPOINT", "").strip() or DEFAULT_LLM_ENDPOINT
        self.model = model
        self.timeout_sec = timeout_sec
        self.fallback = fallback or HeuristicResolver()
        self.transcript: list[dict[str, Any]] = []
        self._reached = False

    def build_prompt(self, kernel: SoarKernel, impasse: ImpasseInfo) -> str:
        prob = problem_name(kernel, impasse.state_id)
        goal_rules = [
            p.source_text or format_production(p)
            for p in kernel.productions.values()
            if any(getattr(getattr(a, "call", None), "func_name", "") == "halt" for a in p.actions) and not p.is_chunk
        ]
        lines = [
            f"Soar agent in problem space `{prob}` hit a TIE impasse in {impasse.state_id}; you are reasoning in substate {impasse.substate_id}.",
            "Current working memory (id ^attr value):",
            *[f"  ({i} ^{a} {v})" for i, a, v in state_triples(kernel, impasse.state_id)],
            "Tied candidate operators:",
            *[
                f"  {op}: {kernel.get_operator_name(op)} {json.dumps(kernel.get_operator_details(op)['attributes'], sort_keys=True)}"
                for op in impasse.candidates
            ],
            "The goal is reached when this rule fires:",
            *goal_rules[:2],
            "Pick the operator that leads to the goal in the fewest steps without dead ends.",
            'Answer with JSON only: {"winner": "<operator id>", "rationale": "<one sentence>"}',
        ]
        return "\n".join(lines)

    def _call(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are an expert in the Soar cognitive architecture and planning."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 200,
                "temperature": 0.0,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:  # noqa: S310 - operator-configured endpoint
            self._reached = True
            data = json.loads(resp.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"])

    def choose(self, kernel: SoarKernel, impasse: ImpasseInfo) -> Decision:
        t0 = time.perf_counter()
        prompt = self.build_prompt(kernel, impasse)
        raw = ""
        error = ""
        self._reached = False
        try:
            raw = self._call(prompt)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            answer = json.loads(match.group(0)) if match else {}
            winner = str(answer.get("winner", "")).strip()
            if winner in impasse.candidates:
                latency = (time.perf_counter() - t0) * 1000.0
                self.transcript.append({"prompt": prompt, "response": raw, "valid": True})
                return Decision(
                    winner=winner,
                    rationale=str(answer.get("rationale", ""))[:240],
                    engine=f"llm:{self.model}",
                    latency_ms=latency,
                    llm_calls=1,
                    llm_valid=True,
                )
            error = f"winner {winner!r} is not one of {impasse.candidates}"
        except urllib.error.HTTPError as exc:  # the endpoint answered, with an error status
            self._reached = True
            error = f"HTTPError: {exc}"
        except Exception as exc:  # unreachable, timeout, malformed response
            error = f"{type(exc).__name__}: {exc}"
        self.transcript.append({"prompt": prompt, "response": raw, "valid": False, "error": error})
        fb = self.fallback.choose(kernel, impasse)
        fb.engine = f"{fb.engine} (llm fallback)"
        fb.rationale = f"LLM answer rejected ({error}); {fb.rationale}"
        fb.latency_ms = (time.perf_counter() - t0) * 1000.0
        fb.llm_calls = 1 if self._reached else 0
        fb.llm_valid = False
        return fb


class VerifiedLLMResolver:
    """System 1 proposes, System 2 checks: take the LLM's pick unless lookahead proves it a dead end."""

    name = "llm+verify"

    def __init__(self, llm: LLMResolver | None = None, verifier: LookaheadResolver | None = None) -> None:
        self.llm = llm or LLMResolver()
        self.verifier = verifier or LookaheadResolver()

    def choose(self, kernel: SoarKernel, impasse: ImpasseInfo) -> Decision:
        t0 = time.perf_counter()
        proposal = self.llm.choose(kernel, impasse)
        if self.verifier._simulate(kernel, impasse, proposal.winner, 0) != float("inf"):
            proposal.engine = f"{proposal.engine} (verified)"
            proposal.latency_ms = (time.perf_counter() - t0) * 1000.0
            return proposal
        checked = self.verifier.choose(kernel, impasse)
        checked.engine = f"{proposal.engine} -> overruled by lookahead"
        checked.rationale = (
            f"LLM chose {proposal.winner} ({kernel.get_operator_name(proposal.winner)}) but simulation hits a dead end; "
            f"{checked.rationale}"
        )
        checked.llm_calls = proposal.llm_calls
        checked.llm_valid = False
        checked.latency_ms = (time.perf_counter() - t0) * 1000.0
        return checked


# ---------------------------------------------------------------------------
# Rule compilation & the loop
# ---------------------------------------------------------------------------
def _sym(raw: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9-]+", "-", str(raw)).strip("-")
    return text or "x"


class IndistinguishableTie(ValueError):
    """The winner cannot be told apart from a loser by any testable attribute, so no general rule exists."""


_BARE_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9_*-]*")


def soar_value(value: Any) -> str:
    """Render a constant for Soar source: numbers bare, simple symbols bare, everything else as ``|...|``."""
    if isinstance(value, bool):
        value = str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if _BARE_SYMBOL.fullmatch(text):
        return text
    return "|" + text.replace("|", "") + "|"


def _describe(kernel: SoarKernel, op: str, var: str, deep: bool) -> tuple[list[str], list[str], tuple]:
    """Tests for one operator: ``(main tests, extra conditions, comparable signature)``.

    Literal attributes always; with ``deep`` also identifier links, described by the linked
    object's literal attributes (``^target <o1-target>`` + ``(<o1-target> ^kind box)``).
    """
    attrs = kernel.get_operator_details(op)["attributes"]
    tests: list[str] = []
    extra: list[str] = []
    sig: list[tuple] = []
    for k, v in sorted(attrs.items()):
        if isinstance(v, str) and _is_identifier(v):
            if not deep:
                continue
            link_var = f"<{var}-{_sym(k)}>"
            linked = literal_attributes(kernel, v)
            tests.append(f"^{k} {link_var}")
            if linked:
                extra.append(f"    ({link_var} " + " ".join(f"^{a} {soar_value(b)}" for a, b in linked.items()) + ")")
            sig.append((k, tuple(sorted((a, str(b)) for a, b in linked.items()))))
        else:
            tests.append(f"^{k} {soar_value(v)}")
            sig.append((k, str(v)))
    return tests, extra, tuple(sig)


def compile_s2_rule(kernel: SoarKernel, winner: str, losers: list[str], tag: str = "neuro") -> str:
    """Write a general S2 evaluation production: in a tie between these operator *kinds*, prefer the winner.

    Operators are described by their constant attributes; if that cannot tell the winner from a
    loser, identifier links are described one level deep. Raises :class:`IndistinguishableTie`
    when even that is identical, because any rule would also prefer the loser over the winner.
    """
    prob = problem_name(kernel)
    deep = False
    while True:
        w_tests, w_extra, w_sig = _describe(kernel, winner, "o1", deep)
        described = [_describe(kernel, loser, f"o{i}", deep) for i, loser in enumerate(losers, start=2)]
        if all(sig != w_sig for _, _, sig in described):
            break
        if deep:
            raise IndistinguishableTie(f"{winner} has the same testable attributes as a competing operator")
        deep = True

    items = "^item <o1> " + " ".join(f"^item {{ <o{i}> <> <o1> }}" for i in range(2, len(losers) + 2))
    conds = [f"    (state <s2> ^superstate <ss> ^impasse tie {items.strip()})"]
    if prob:
        conds.append(f"    (<ss> ^name {soar_value(prob)})")
    conds.append("    (<o1> " + " ".join(w_tests) + ")")
    conds.extend(w_extra)
    actions = []
    for idx, (l_tests, l_extra, _) in enumerate(described, start=2):
        conds.append(f"    (<o{idx}> " + " ".join(l_tests) + ")")
        conds.extend(l_extra)
        actions.append(f"    (<ss> ^operator <o1> > <o{idx}>)")

    def label(op: str) -> str:
        attrs = literal_attributes(kernel, op)
        rest = "-".join(_sym(v) for k, v in attrs.items() if k != "name") or _sym(op)
        return f"{_sym(attrs.get('name', op))}-{rest}"

    # Name both sides so distinct decisions never share a rule (or chunk / ChunkStore) name.
    over = "-".join(label(loser) for loser in losers)
    name = f"{tag}*s2*{_sym(prob or 'task')}*prefer-{label(winner)}-over-{over}"
    return "\n".join([f"sp {{{name}", *conds, "-->", *actions, "}"])


def install_s2_rule(kernel: SoarKernel, winner: str, losers: list[str], tag: str = "neuro") -> str | None:
    rule = compile_s2_rule(kernel, winner, losers, tag=tag)
    prods = kernel.load_soar_code(rule, clear_existing=False)
    return prods[0].name if prods else None


def _drive(kernel: SoarKernel, choose: Any, max_cycles: int, compile_rules: bool) -> dict[str, Any]:
    """Core loop shared by :func:`solve` and lookahead simulation."""
    deliberations: list[dict[str, Any]] = []
    seen_ties: set[tuple[str, ...]] = set()
    dead_end = False
    start = kernel.cycle_count
    while not kernel.halted and kernel.cycle_count - start < max_cycles:
        kernel.step_cycle()
        imp = kernel.active_impasses.get("S1")
        if imp is None:
            continue
        if imp.impasse_type != "tie":
            dead_end = True
            break
        key = tuple(sorted(imp.candidates))
        if key in seen_ties:
            continue
        seen_ties.add(key)
        decision: Decision = choose(kernel, imp)
        losers = [c for c in imp.candidates if c != decision.winner]
        rule_name = None
        unresolvable = ""
        if compile_rules:
            try:
                rule_name = install_s2_rule(kernel, decision.winner, losers)
            except IndistinguishableTie as exc:
                unresolvable = str(exc)
        deliberations.append(
            {
                "cycle": kernel.cycle_count,
                "substate": imp.substate_id,
                "candidates": [
                    {"id": c, "name": kernel.get_operator_name(c), "attributes": literal_attributes(kernel, c)}
                    for c in imp.candidates
                ],
                "winner": decision.winner,
                "winner_name": kernel.get_operator_name(decision.winner),
                "rationale": decision.rationale,
                "engine": decision.engine,
                "latency_ms": round(decision.latency_ms, 2),
                "llm_calls": decision.llm_calls,
                "llm_valid": decision.llm_valid,
                "scores": decision.scores,
                "s2_rule": rule_name,
                "unresolvable": unresolvable or None,
            }
        )
        if unresolvable:
            dead_end = True
            break
    return {"solved": kernel.halted, "dead_end": dead_end, "deliberations": deliberations}


def solve(kernel: SoarKernel, resolver: Resolver, max_cycles: int = 80) -> dict[str, Any]:
    """Run run-1 with S2 deliberation, then replay with chunks only. Returns a JSON-safe report."""
    t0 = time.perf_counter()
    run1 = _drive(kernel, resolver.choose, max_cycles=max_cycles, compile_rules=True)
    run1_ms = (time.perf_counter() - t0) * 1000.0
    run1_cycles = kernel.cycle_count
    run1_impasses = kernel.total_impasses_count
    chunks = [
        {"name": n, "source_text": kernel.productions[n].source_text}
        for n in kernel.learned_chunks
        if n in kernel.productions
    ]

    for d in run1["deliberations"]:
        d["chunk"] = next((n for n in kernel.learned_chunks if d["s2_rule"] and f"*{d['s2_rule']}*" in n), None)

    # Replay: drop the deliberation-time S2 rules too, so only chunks can prevent impasses.
    for d in run1["deliberations"]:
        if d["s2_rule"]:
            kernel.productions.pop(d["s2_rule"], None)
    t1 = time.perf_counter()
    kernel.reset_working_memory(keep_chunks=True)
    replay = _drive(kernel, _refuse, max_cycles=max_cycles, compile_rules=False)
    replay_ms = (time.perf_counter() - t1) * 1000.0

    return {
        "resolver": resolver.name,
        "solved": run1["solved"],
        "dead_end": run1["dead_end"],
        "run1_cycles": run1_cycles,
        "run1_impasses": run1_impasses,
        "run1_ms": round(run1_ms, 2),
        "llm_calls": sum(d["llm_calls"] for d in run1["deliberations"]),
        "llm_valid": sum(1 for d in run1["deliberations"] if d["llm_valid"]),
        "deliberations": run1["deliberations"],
        "chunks": chunks,
        "replay_solved": replay["solved"],
        "replay_cycles": kernel.cycle_count,
        "replay_impasses": kernel.total_impasses_count,
        "replay_resolver_calls": len(replay["deliberations"]),
        "replay_ms": round(replay_ms, 2),
    }


def _refuse(kernel: SoarKernel, impasse: ImpasseInfo) -> Decision:
    """Replay-time resolver: never consulted when chunks cover every tie; if it is, pick deterministically."""
    return Decision(winner=sorted(impasse.candidates)[0], rationale="replay: no chunk covered this tie", engine="none")
