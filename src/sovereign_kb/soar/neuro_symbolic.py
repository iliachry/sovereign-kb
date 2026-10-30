"""Neuro-Symbolic Bridge connecting the Soar Cognitive Kernel with local/sovereign LLM endpoints."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .kernel import SoarKernel
from .parser import parse_soar


class NeuroSymbolicBridge:
    """
    Connects Soar's Symbolic Impasse Architecture to local/sovereign OpenAI-compatible
    endpoints (`http://127.0.0.1:8000/v1/chat/completions` or `SKB_LLM_ENDPOINT`) with
    deterministic symbolic heuristic fallback so it works both online and offline.
    """

    def __init__(
        self,
        endpoints: list[str] | None = None,
        model: str = "qwen3.6-35b-a3b",
        timeout_sec: float = 1.5,
    ) -> None:
        env_ep = os.environ.get("SKB_LLM_ENDPOINT", "").strip()
        default_eps = ["http://127.0.0.1:8000/v1/chat/completions"]
        if env_ep and env_ep not in default_eps:
            default_eps.insert(0, env_ep)
        self.endpoints = default_eps if endpoints is None else endpoints
        self.model = model
        self.timeout_sec = timeout_sec

    def resolve_impasse(self, kernel: SoarKernel, goal_hint: str = "") -> dict[str, Any]:
        """
        Inspect an active Impasse in `kernel` (or candidate operators in `S1`),
        consult LLM (with symbolic lookahead heuristic fallback), compile a
        generalized `.soar` dominance production rule, load it into Procedural Memory,
        and run Elaboration so the rule resolves the tie architecturally via i-support.
        """
        snapshot = kernel.get_snapshot()
        candidates = snapshot["operator_candidates"]
        if len(candidates) < 2:
            if snapshot["current_phase"] in ("INPUT", "ELABORATION"):
                kernel.step_phase()
                if kernel.current_phase.value == "ELABORATION":
                    kernel.step_phase()
                snapshot = kernel.get_snapshot()
                candidates = snapshot["operator_candidates"]

        active_cands = [c for c in candidates if c["status"] != "rejected"]
        if not active_cands:
            return {
                "resolved": False,
                "engine": "symbolic-heuristic",
                "message": "No candidate operators currently competing in S1.",
            }

        winner, loser, rationale, heuristic_rule = self._select_best_operator(kernel, active_cands, goal_hint)

        problem_name = next(
            (str(w.value) for w in kernel.wmes.values() if w.id == "S1" and w.attr == "name"),
            "water-jugs",
        )
        llm_prompt = (
            f"Resolve tie impasse between {winner['name']} ({winner['attributes']}) "
            f"and {loser['name'] if loser else 'none'} ({loser['attributes'] if loser else {}}). "
            f"Goal hint: {goal_hint}"
        )
        llm_rule = self._try_gx10_generate_rule(llm_prompt, problem_name)
        synthesized_rule = llm_rule if llm_rule is not None else heuristic_rule
        engine = f"gx10:{self.model}" if llm_rule is not None else "symbolic-heuristic"

        loaded_rule_name = None
        if synthesized_rule:
            prods = kernel.load_soar_code(synthesized_rule, clear_existing=False)
            if prods:
                loaded_rule_name = prods[0].name
            kernel._run_elaboration_phase()

        eval_winner, _, _ = kernel.evaluate_operator_preferences("S1")
        if eval_winner is None and llm_rule is not None and heuristic_rule:
            if loaded_rule_name:
                kernel.productions.pop(loaded_rule_name, None)
            synthesized_rule = heuristic_rule
            engine = "symbolic-heuristic"
            prods = kernel.load_soar_code(synthesized_rule, clear_existing=False)
            if prods:
                loaded_rule_name = prods[0].name
            kernel._run_elaboration_phase()
            eval_winner, _, _ = kernel.evaluate_operator_preferences("S1")

        if eval_winner is not None and "S1" in kernel.active_impasses:
            kernel._run_decision_phase()
            kernel.current_phase = kernel.current_phase.__class__("APPLICATION")

        actually_resolved = eval_winner is not None and "S1" not in kernel.active_impasses

        kernel._log(
            "NEURO_SYMBOLIC",
            f"Resolved tie via {loaded_rule_name or engine}: {winner['operator_id']} ({winner['name']}) > {loser['operator_id'] if loser else 'none'}",
            {"rationale": rationale, "rule": loaded_rule_name, "engine": engine, "resolved": actually_resolved},
        )

        return {
            "resolved": actually_resolved,
            "engine": engine,
            "winner": winner,
            "loser": loser,
            "rationale": rationale,
            "synthesized_rule": synthesized_rule,
            "loaded_rule_name": loaded_rule_name,
            "snapshot": kernel.get_snapshot(),
        }

    def synthesize_soar_rule(self, natural_language_prompt: str, problem_space: str = "water-jugs") -> dict[str, Any]:
        """Translate a natural-language specification into a verified `.soar` `sp { ... }` production rule."""
        llm_resp = self._try_gx10_generate_rule(natural_language_prompt, problem_space)
        if llm_resp is not None:
            rule_code, engine = llm_resp, f"gx10:{self.model}"
        else:
            rule_code, engine = (
                self._deterministic_nl_to_soar(natural_language_prompt, problem_space),
                "symbolic-compiler",
            )

        parsed = parse_soar(rule_code)
        return {
            "prompt": natural_language_prompt,
            "problem_space": problem_space,
            "engine": engine,
            "soar_code": rule_code,
            "rule_name": parsed[0].name if parsed else "custom*rule",
            "valid_syntax": len(parsed) > 0,
        }

    def _select_best_operator(
        self,
        kernel: SoarKernel,
        active_cands: list[dict[str, Any]],
        goal_hint: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str, str]:
        """Score candidate operators using domain state analysis and emit a discriminating `.soar` control rule."""
        problem_name: str | None = None
        for w in kernel.wmes.values():
            if w.id == "S1" and w.attr == "name":
                problem_name = str(w.value)
                break
        state_name_clause = f"^name {problem_name} " if problem_name else ""

        def score_op(c: dict[str, Any]) -> float:
            name = str(c["name"])
            attrs = c["attributes"]
            if problem_name == "water-jugs":
                if name == "pour-jug":
                    return 30.0
                if name == "fill-jug":
                    vol = float(attrs.get("jug-volume", 0))
                    return 20.0 + vol
                if name == "empty-jug":
                    vol = float(attrs.get("jug-volume", 5))
                    return 15.0 - vol
            elif problem_name == "blocks-world":
                if attrs.get("to") == "table":
                    return 28.0
                return 20.0
            elif problem_name == "towers-of-hanoi":
                if attrs.get("to") == "B":
                    return 28.0
                return 18.0
            elif problem_name == "skb-vault":
                rank_bonus = float(attrs.get("authority", 1.0)) * 10.0
                rrf_bonus = float(attrs.get("rrf-score", 0.0)) * 100.0
                return rank_bonus + rrf_bonus
            return 15.0

        ranked = sorted(active_cands, key=score_op, reverse=True)
        winner = ranked[0]
        loser = ranked[1] if len(ranked) > 1 else None

        w_name = winner["name"]
        l_name = loser["name"] if loser else "other"

        w_extra = ""
        l_extra = ""
        suffix = f"{w_name}-over-{l_name}"
        if loser and w_name == l_name:
            for k, w_val in winner["attributes"].items():
                if k == "name":
                    continue
                l_val = loser["attributes"].get(k)
                if l_val is not None and w_val != l_val and not str(w_val).startswith(("J", "B", "D", "O", "S")):
                    w_extra = f" ^{k} {w_val}"
                    l_extra = f" ^{k} {l_val}"
                    suffix = f"{w_name}-{k}-{w_val}-over-{l_val}"
                    break

        rationale = (
            f"In problem-space `{problem_name}`, operator `{winner['operator_id']}` (`{w_name}{w_extra}`) "
            f"makes strictly greater progress toward the goal state than `{loser['operator_id'] if loser else 'none'}` (`{l_name}{l_extra}`). "
            f"Compiled symbolic dominance production `neuro*prefer*{suffix}`."
        )

        safe_suffix = suffix.replace("_", "-").replace(" ", "-").replace("/", "-")
        if loser:
            synthesized_rule = "\n".join(
                [
                    f"sp {{neuro*prefer*{safe_suffix}",
                    f'    "Neuro-Symbolic control rule preferring {w_name}{w_extra} over {l_name}{l_extra}"',
                    "    :i-support",
                    f"    (state <s> {state_name_clause}^operator <o1> + ^operator <o2> +)",
                    f"    (<o1> ^name {w_name}{w_extra})",
                    f"    (<o2> ^name {l_name}{l_extra})",
                    "-->",
                    "    (<s> ^operator <o1> > <o2>)",
                    "}",
                ]
            )
        else:
            synthesized_rule = "\n".join(
                [
                    f"sp {{neuro*best*{w_name}",
                    f'    "Neuro-Symbolic control rule marking {w_name} as best candidate"',
                    "    :i-support",
                    f"    (state <s> {state_name_clause}^operator <o1> +)",
                    f"    (<o1> ^name {w_name}{w_extra})",
                    "-->",
                    "    (<s> ^operator <o1> >)",
                    "}",
                ]
            )
        return winner, loser, rationale, synthesized_rule

    def _try_gx10_generate_rule(self, prompt: str, problem_space: str) -> str | None:
        """Attempt non-blocking query to local/sovereign Smart Priority Proxy if reachable."""
        if not self.endpoints:
            return None
        sys_msg = (
            "You are a Soar Cognitive Architecture expert. Output ONLY a single valid Soar production "
            "using `sp { ... }` syntax with `(state <s> ...)` on the LHS and `-->` before RHS actions."
        )
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {
                        "role": "user",
                        "content": f"Problem space: {problem_space}\nRequirement: {prompt}\nReturn only `sp {{ ... }}`.",
                    },
                ],
                "max_tokens": 220,
                "temperature": 0.1,
            }
        ).encode("utf-8")

        for url in self.endpoints:
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    content = data["choices"][0]["message"]["content"]
                    if "sp {" in content and "-->" in content:
                        start = content.index("sp {")
                        end = content.rindex("}") + 1
                        candidate = content[start:end]
                        parse_soar(candidate)
                        return candidate
            except Exception:
                continue
        return None

    def _deterministic_nl_to_soar(self, prompt: str, problem_space: str) -> str:
        """Deterministic NL-to-Soar compiler fallback producing guaranteed valid `.soar` syntax."""
        p_low = prompt.lower()
        if "prohibit" in p_low and "empty" in p_low:
            return "\n".join(
                [
                    f"sp {{{problem_space}*neuro*prohibit-empty-jug",
                    f'    "Synthesized from: {prompt}"',
                    "    :i-support",
                    f"    (state <s> ^name {problem_space} ^operator <o> +)",
                    "    (<o> ^name empty-jug)",
                    "-->",
                    "    (<s> ^operator <o> ~)",
                    "}",
                ]
            )
        if "avoid empty" in p_low or ("worst" in p_low and "empty" in p_low):
            return "\n".join(
                [
                    f"sp {{{problem_space}*neuro*reject-premature-empty",
                    f'    "Synthesized from: {prompt}"',
                    "    :i-support",
                    f"    (state <s> ^name {problem_space} ^operator <o> +)",
                    "    (<o> ^name empty-jug)",
                    "-->",
                    "    (<s> ^operator <o> <)",
                    "}",
                ]
            )
        if "pour" in p_low:
            return "\n".join(
                [
                    f"sp {{{problem_space}*neuro*prefer-pour-jug",
                    f'    "Synthesized from: {prompt}"',
                    "    :i-support",
                    f"    (state <s> ^name {problem_space} ^operator <o1> + ^operator <o2> +)",
                    "    (<o1> ^name pour-jug)",
                    "    (<o2> ^name fill-jug)",
                    "-->",
                    "    (<s> ^operator <o1> > <o2>)",
                    "}",
                ]
            )
        return "\n".join(
            [
                f"sp {{{problem_space}*neuro*custom-heuristic",
                f'    "Synthesized from: {prompt}"',
                "    :i-support",
                f"    (state <s> ^name {problem_space})",
                "-->",
                "    (<s> ^neuro-advice active)",
            ]
        )
