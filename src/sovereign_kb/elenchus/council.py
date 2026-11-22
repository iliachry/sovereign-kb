"""Cross-lineage council execution (`oc triad`, OpenAI-compat HTTP, offline probes) for Elenchus."""

from __future__ import annotations

import base64
import datetime
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ..audit_registry import (
    is_bundled_starter_vault_path,
    record_triad_audit,
)
from .constants import (
    _UNSAFE_DIFF_PATTERNS,
    ELENCHUS_PROTOCOL_VERSION,
    _is_strict_int,
    classify_model_lineage,
)
from .git_diff import build_elenchus_challenge_packet
from .panel import synthesize_elenchus_verdict
from .prose_rules import _normalize_finding_dict, _trim_blank_outer_lines
from .redaction import _extract_url_secrets, _redact_sensitive_text, _redact_url, _scrub_review_dict
from .review_parser import parse_oc_triad_stdout, parse_reviewer_output


def _run_offline_structural_probe(target_dir: Path, packet: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Deterministic non-independent fallback challenge when no live cross-lineage LLM endpoint is used.
    Always classifies as `lineage="deterministic"` so the panel honestly reports `DEGRADED_SINGLE_LINEAGE`
    (`gate_closable = False`).
    """
    findings: list[dict[str, Any]] = []
    san = packet.get("sanitized_evidence") or {}
    gate_exec = san.get("gate_execution") or {}
    if (
        not gate_exec
        or gate_exec.get("passed") is not True
        or gate_exec.get("executed") is not True
        or not _is_strict_int(gate_exec.get("exit_code"))
        or gate_exec.get("exit_code") != 0
    ):
        findings.append(
            _normalize_finding_dict(
                {
                    "severity": "P1",
                    "category": "test_gap",
                    "file_path": "evidence/gate_receipt.json",
                    "line": 0,
                    "symbol": "",
                    "claim": "Missing or non-passing run-bound behavioral `evidence/gate_receipt.json`.",
                    "evidence": json.dumps(gate_exec or {"missing": True}),
                }
            )
        )

    probe_states = san.get("probe_states") or {}
    if isinstance(probe_states, dict):
        unmeasured = sorted(k for k, v in probe_states.items() if str(v).upper() == "UNMEASURED")
        if unmeasured:
            findings.append(
                _normalize_finding_dict(
                    {
                        "severity": "P1",
                        "category": "provenance",
                        "file_path": "evidence/goldwood.json",
                        "line": 0,
                        "symbol": "",
                        "claim": f"Unmeasured GWI probe(s) detected prior to sealing: {', '.join(unmeasured)}.",
                        "evidence": f"probe_states={unmeasured}",
                    }
                )
            )

    touched = list(san.get("touched_files") or [])
    src_touched = [
        p for p in touched if p.startswith("src/") and p.endswith((".py", ".ts", ".tsx", ".js", ".rs", ".go"))
    ]
    test_touched = [p for p in touched if p.startswith("tests/") or ".test." in p or ".spec." in p]
    if src_touched and not test_touched:
        findings.append(
            _normalize_finding_dict(
                {
                    "severity": "P1",
                    "category": "test_gap",
                    "file_path": src_touched[0],
                    "line": 0,
                    "symbol": "",
                    "claim": f"Source files changed ({', '.join(src_touched[:3])}) without paired test changes.",
                    "evidence": f"touched_files={touched[:8]}",
                }
            )
        )

    diff_part = packet.get("prompt", "").split("[Code Diff / Snapshot]\n", 1)[-1]
    cur_file = ""
    for raw_ln in diff_part.splitlines():
        if raw_ln.startswith("+++ b/"):
            cur_file = raw_ln[6:].strip()
        elif raw_ln.startswith("+") and not raw_ln.startswith("+++"):
            for pat_name, rx in _UNSAFE_DIFF_PATTERNS:
                if rx.search(raw_ln):
                    findings.append(
                        _normalize_finding_dict(
                            {
                                "severity": "P1",
                                "category": "security",
                                "file_path": cur_file,
                                "line": 0,
                                "symbol": pat_name,
                                "claim": f"Unsafe construct `{pat_name}` introduced in diff.",
                                "evidence": raw_ln[:180],
                            }
                        )
                    )

    return [
        {
            "model_id": "elenchus-local-structural/1.0",
            "lineage": "deterministic",
            "verdict": "CHALLENGE" if findings else "APPROVE",
            "execution_status": "OK",
            "summary": (
                f"Deterministic structural challenge found {len(findings)} advisory item(s) "
                "(non-independent single-engine sanity pass)."
            ),
            "findings": findings,
        }
    ]


def _query_openai_compat_reviewer(model_id: str, url: str, prompt: str, timeout: float = 90.0) -> dict[str, Any]:
    safe_url = _redact_url(url)
    url_secrets = _extract_url_secrets(url)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    request_url = url
    try:
        sp = urllib.parse.urlsplit((url or "").strip())
        if sp.username is not None or sp.password is not None:
            raw_user = urllib.parse.unquote(sp.username or "")
            raw_pass = urllib.parse.unquote(sp.password or "")
            tok = base64.b64encode(f"{raw_user}:{raw_pass}".encode()).decode("ascii")
            url_secrets.append(tok)
            headers["Authorization"] = f"Basic {tok}"
            host = sp.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            port_part = f":{sp.port}" if sp.port else ""
            request_url = urllib.parse.urlunsplit((sp.scheme, f"{host}{port_part}", sp.path or "", sp.query, ""))
    except Exception:
        pass
    payload = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        request_url,
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
        obj = json.loads(raw_body)
        if not isinstance(obj, dict):
            raise ValueError("HTTP reviewer response must be a JSON object.")
        if obj.get("error"):
            err_val = obj["error"]
            if isinstance(err_val, dict):
                err_msg = str(err_val.get("message") or err_val.get("type") or err_val.get("code") or "upstream error")
            else:
                err_msg = str(err_val)
            safe_err = _redact_sensitive_text(err_msg, url_secrets)[:160]
            raise ValueError(f"HTTP endpoint error payload: {safe_err}")
        if "choices" in obj:
            choices = obj.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("HTTP endpoint returned empty or malformed `choices` list.")
            msg = choices[0].get("message") or {}
            content = _trim_blank_outer_lines(
                str((msg.get("content") if isinstance(msg, dict) else "") or choices[0].get("text") or "")
            )
            if not content:
                raise ValueError("HTTP endpoint returned empty completion content in `choices[0]`.")
        elif "verdict" in obj or "findings" in obj:
            content = raw_body
        else:
            raise ValueError("HTTP endpoint response lacks `choices` completion payload.")
        rev = parse_reviewer_output(model_id, content, extra_secrets=url_secrets)
        return _scrub_review_dict(rev, url_secrets)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
        safe_exc = _redact_sensitive_text(str(exc), url_secrets)[:240]
        failed_rev = {
            "model_id": model_id,
            "lineage": classify_model_lineage(model_id),
            "verdict": "CHALLENGE",
            "execution_status": "FAILED",
            "summary": f"HTTP reviewer invocation failed ({safe_url}): {safe_exc}",
            "findings": [
                _normalize_finding_dict(
                    {
                        "severity": "P1",
                        "category": "provenance",
                        "claim": f"Independent HTTP reviewer `{model_id}` failed to respond: {safe_exc}",
                        "evidence": safe_url,
                    }
                )
            ],
        }
        return _scrub_review_dict(failed_rev, url_secrets)


def _parse_reviews_file(reviews_file: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_text = reviews_file.read_text(encoding="utf-8")
    stripped = raw_text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            loaded = json.loads(stripped)
            if isinstance(loaded, list):
                return [x for x in loaded if isinstance(x, dict)], {}
            if isinstance(loaded, dict):
                env_bindings: dict[str, Any] = {}
                if isinstance(loaded.get("bindings"), dict):
                    env_bindings.update(loaded["bindings"])
                for k in ("packet_sha256", "worktree_sha", "tree_sha", "commit_sha"):
                    if loaded.get(k):
                        env_bindings.setdefault(k, loaded[k])
                if isinstance(loaded.get("reviews"), list):
                    return [x for x in loaded["reviews"] if isinstance(x, dict)], env_bindings
                return [loaded], env_bindings
        except json.JSONDecodeError:
            pass
    oc_parsed = parse_oc_triad_stdout(raw_text)
    if oc_parsed:
        return oc_parsed, {}
    return [parse_reviewer_output("imported-review", raw_text)], {}


def _verify_review_bindings(
    reviews_list: list[dict[str, Any]],
    envelope_bindings: dict[str, Any],
    packet_bindings: dict[str, Any],
    is_file_import: bool,
) -> bool:
    """Validate optional/imported review bindings against current packet/worktree/tree digests."""
    exp_pkt = str(packet_bindings.get("packet_sha256") or "")
    exp_wt = str(packet_bindings.get("worktree_sha") or "")
    exp_tree = str(packet_bindings.get("tree_sha") or "")

    sources: list[dict[str, Any]] = []
    if envelope_bindings:
        sources.append(envelope_bindings)
    for item in reviews_list:
        if isinstance(item.get("bindings"), dict):
            sources.append(item["bindings"])
        item_top = {k: item[k] for k in ("packet_sha256", "worktree_sha", "tree_sha") if item.get(k)}
        if item_top:
            sources.append(item_top)

    for src in sources:
        for key, exp_val in (("packet_sha256", exp_pkt), ("worktree_sha", exp_wt), ("tree_sha", exp_tree)):
            got = str(src.get(key) or "").strip()
            if got and got != exp_val:
                raise ValueError(
                    f"Imported review bundle is bound to a different {key} ({got!r} != {exp_val!r}); re-run reviewers."
                )

    if not is_file_import:
        return True

    def _source_matches(src: dict[str, Any]) -> bool:
        if not src:
            return False
        got_pkt = str(src.get("packet_sha256") or "").strip()
        got_wt = str(src.get("worktree_sha") or "").strip()
        got_tree = str(src.get("tree_sha") or "").strip()
        if got_pkt and exp_pkt and got_pkt == exp_pkt:
            return True
        if got_wt and got_tree and exp_wt and exp_tree and got_wt == exp_wt and got_tree == exp_tree:
            return True
        return False

    if _source_matches(envelope_bindings):
        return True
    if reviews_list and all(
        _source_matches(
            {
                **(item_bindings if isinstance(item_bindings := item.get("bindings"), dict) else {}),
                **{k: item[k] for k in ("packet_sha256", "worktree_sha", "tree_sha") if item.get(k)},
            }
        )
        for item in reviews_list
    ):
        return True
    return False


def _record_triad_from_elenchus(target_dir: Path, subpath: str, receipt: dict[str, Any]) -> None:
    panel = receipt.get("panel") or {}
    if not panel.get("independent_panel"):
        return
    models = panel.get("models") or []
    ok_models = [
        m
        for m in models
        if isinstance(m, dict)
        and m.get("model_id")
        and m.get("lineage") != "deterministic"
        and str(m.get("execution_status") or "OK").upper() != "FAILED"
    ]
    actual_models = [str(m["model_id"]) for m in ok_models]
    fable_m = next((m["model_id"] for m in ok_models if m.get("lineage") == "anthropic"), None)
    astra_m = next((m["model_id"] for m in ok_models if m.get("lineage") == "openai"), None)
    extra_m = next(
        (m["model_id"] for m in ok_models if m.get("lineage") not in ("anthropic", "openai", "deterministic")),
        None,
    )
    disp = "APPROVED" if receipt.get("seal_status") in ("READY_UNANIMOUS", "READY_ADJUDICATED") else "NOT_APPROVED"
    record_triad_audit(
        target_dir,
        subpath=subpath,
        disposition=disp,
        fable_model=fable_m,
        astra_model=astra_m,
        gx10_model=extra_m,
        notes=f"Elenchus {receipt.get('seal_status')} (mode={receipt.get('execution_mode', 'unknown')}, N_eff={panel.get('n_eff', 1.0)})",
        commit_sha_override=str((receipt.get("bindings") or {}).get("commit_sha") or ""),
        panel_models=actual_models,
    )


_OC_COUNCIL_SLOTS: tuple[tuple[str, str], ...] = (
    ("CLAUDE FABLE 5.1", "amazon-bedrock/global.anthropic.claude-fable-5-1"),
    ("GPT-6 ASTRA", "amazon-bedrock/global.openai.gpt-6-astra"),
)


def _make_failed_council_review(model_id: str, reason_msg: str, evidence: str = "oc triad") -> dict[str, Any]:
    msg = _redact_sensitive_text(reason_msg)[:240]
    return {
        "model_id": model_id,
        "lineage": classify_model_lineage(model_id),
        "verdict": "CHALLENGE",
        "execution_status": "FAILED",
        "summary": msg,
        "findings": [
            _normalize_finding_dict(
                {
                    "severity": "P1",
                    "category": "provenance",
                    "claim": msg,
                    "evidence": evidence,
                }
            )
        ],
    }


def run_elenchus_challenge(
    target_path: str | Path = ".",
    intent: str = "",
    reviews_file: str | Path | None = None,
    reviews: list[dict[str, Any]] | None = None,
    runner: str = "auto",
    http_endpoints: list[str] | None = None,
    blind_scores: bool = True,
    write_receipt: bool = True,
    ledger_file: Path | None = None,
) -> dict[str, Any]:
    """
    Execute an Elenchus pre-seal cross-lineage adversarial challenge on `target_path`,
    calibrate convergence vs. prompt-anchoring vs. lineage splits, and persist `evidence/elenchus_challenge.json`.
    """
    from ..bridge import append_triad_provenance_event

    packet = build_elenchus_challenge_packet(target_path=target_path, intent=intent, blind_scores=blind_scores)
    target_dir = Path(packet["target_dir"])
    subpath = str(packet["subpath"])

    collected_reviews: list[dict[str, Any]] = []
    envelope_bindings: dict[str, Any] = {}
    execution_mode = runner

    if reviews is not None:
        collected_reviews = list(reviews)
        execution_mode = "explicit"
    elif reviews_file is not None:
        rf_path = Path(reviews_file).expanduser().resolve()
        if not rf_path.is_file():
            raise FileNotFoundError(f"Reviews file does not exist: {rf_path}")
        collected_reviews, envelope_bindings = _parse_reviews_file(rf_path)
        execution_mode = "reviews_file"
    else:
        endpoints = list(http_endpoints or [])
        extra_url = os.environ.get("KB_ELENCHUS_EXTRA_URL", "").strip()
        extra_model = os.environ.get("KB_ELENCHUS_EXTRA_MODEL", "").strip()
        if extra_url and extra_model:
            endpoints.append(f"{extra_model}={extra_url}")

        if runner == "http":
            if not endpoints:
                raise ValueError("`--runner http` requires at least one `--http-endpoint model_id=url`")
            for ep in endpoints:
                if "=" not in ep:
                    raise ValueError(f"Invalid --http-endpoint '{ep}'; expected `model_id=http://...`")
                m_id, url = ep.split("=", 1)
                collected_reviews.append(
                    getattr(
                        sys.modules.get("sovereign_kb.elenchus"),
                        "_query_openai_compat_reviewer",
                        _query_openai_compat_reviewer,
                    )(m_id.strip(), url.strip(), packet["prompt"])
                )
            execution_mode = "http"
        elif runner in ("oc", "auto"):
            oc_bin = shutil.which("oc") or str(Path.home() / ".local" / "bin" / "oc")
            can_run_oc = (
                (runner == "oc" or not os.environ.get("PYTEST_CURRENT_TEST"))
                and Path(oc_bin).is_file()
                and os.access(oc_bin, os.X_OK)
            )
            used_oc = False
            used_http = False
            if can_run_oc:
                env_oc = {**os.environ, "OC_NO_WARM_START": "1"}
                try:
                    proc = subprocess.run(
                        [oc_bin, "triad", packet["prompt"]],
                        cwd=packet["repo_root"],
                        env=env_oc,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=240,
                    )
                    oc_reviews = parse_oc_triad_stdout(proc.stdout)
                    if oc_reviews:
                        collected_reviews.extend(oc_reviews)
                        used_oc = True
                    if proc.returncode != 0:
                        used_oc = True
                        present_lins = {r["lineage"] for r in oc_reviews}
                        err_snip = _redact_sensitive_text((proc.stderr or proc.stdout or "").strip())[:160]
                        for lbl, mid in _OC_COUNCIL_SLOTS:
                            if classify_model_lineage(mid) not in present_lins:
                                collected_reviews.append(
                                    _make_failed_council_review(
                                        mid,
                                        f"Council reviewer `{lbl}` failed during `oc triad` (exit={proc.returncode}): {err_snip}",
                                    )
                                )
                except subprocess.TimeoutExpired as exc:
                    used_oc = True
                    raw_out = (
                        exc.stdout.decode("utf-8", errors="replace")
                        if isinstance(exc.stdout, bytes)
                        else (exc.stdout or "")
                    )
                    partial_revs = parse_oc_triad_stdout(raw_out)
                    collected_reviews.extend(partial_revs)
                    present_lins = {r["lineage"] for r in partial_revs}
                    added_fail = False
                    for lbl, mid in _OC_COUNCIL_SLOTS:
                        if classify_model_lineage(mid) not in present_lins:
                            collected_reviews.append(
                                _make_failed_council_review(
                                    mid,
                                    f"Council reviewer `{lbl}` timed out after {exc.timeout}s during `oc triad`.",
                                )
                            )
                            added_fail = True
                    if not added_fail and not any(r.get("execution_status") == "FAILED" for r in partial_revs):
                        collected_reviews.append(
                            _make_failed_council_review(
                                _OC_COUNCIL_SLOTS[-1][1],
                                f"`oc triad` timed out after {exc.timeout}s before clean exit.",
                            )
                        )
                except OSError as exc:
                    used_oc = True
                    safe_err = _redact_sensitive_text(str(exc))[:160]
                    for lbl, mid in _OC_COUNCIL_SLOTS:
                        collected_reviews.append(
                            _make_failed_council_review(
                                mid,
                                f"Council runner `oc triad` failed to execute `{lbl}`: {safe_err}",
                            )
                        )
            if endpoints and runner == "auto":
                for ep in endpoints:
                    if "=" in ep:
                        m_id, url = ep.split("=", 1)
                        collected_reviews.append(
                            getattr(
                                sys.modules.get("sovereign_kb.elenchus"),
                                "_query_openai_compat_reviewer",
                                _query_openai_compat_reviewer,
                            )(m_id.strip(), url.strip(), packet["prompt"])
                        )
                        used_http = True
            if used_oc and not used_http:
                execution_mode = "oc_triad"
            elif used_oc and used_http:
                execution_mode = "hybrid"
            elif used_http:
                execution_mode = "http"
            else:
                collected_reviews = _run_offline_structural_probe(target_dir, packet)
                execution_mode = "offline"
        else:
            collected_reviews = _run_offline_structural_probe(target_dir, packet)
            execution_mode = "offline"

    review_binding_verified = _verify_review_bindings(
        collected_reviews,
        envelope_bindings,
        packet["bindings"],
        is_file_import=(execution_mode == "reviews_file"),
    )
    import_bindings_proof: dict[str, Any] = {}
    if execution_mode == "reviews_file":
        import_bindings_proof = {
            "source": "reviews_file",
            "reviewer_count": len(collected_reviews),
            "envelope": {
                k: envelope_bindings[k]
                for k in ("packet_sha256", "worktree_sha", "tree_sha", "commit_sha")
                if envelope_bindings.get(k)
            },
            "item_bindings": [
                {
                    **(
                        {
                            k: item["bindings"][k]
                            for k in ("packet_sha256", "worktree_sha", "tree_sha")
                            if isinstance(item.get("bindings"), dict) and item["bindings"].get(k)
                        }
                    ),
                    **({k: item[k] for k in ("packet_sha256", "worktree_sha", "tree_sha") if item.get(k)}),
                }
                for item in collected_reviews
            ],
        }
    verdict_bundle = synthesize_elenchus_verdict(
        collected_reviews,
        packet,
        target_dir=target_dir,
        execution_mode=execution_mode,
        review_binding_verified=review_binding_verified,
    )
    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    receipt: dict[str, Any] = {
        "schema": "elenchus.challenge.v1",
        "protocol_version": ELENCHUS_PROTOCOL_VERSION,
        "generated_at": now_iso,
        "adjudicated_at": None,
        "execution_mode": execution_mode,
        "review_binding_verified": review_binding_verified,
        "import_bindings": import_bindings_proof,
        "project_name": packet["project_name"],
        "subpath": subpath,
        "intent": packet["intent"],
        "score_blind": packet["score_blind"],
        "bindings": packet["bindings"],
        "panel": verdict_bundle["panel"],
        "reviewer_summaries": verdict_bundle["reviewer_summaries"],
        "raw_reviews": verdict_bundle["raw_reviews"],
        "clusters": verdict_bundle["clusters"],
        "metrics": verdict_bundle["metrics"],
        "seal_status": verdict_bundle["seal_status"],
    }

    if write_receipt and not is_bundled_starter_vault_path(target_dir):
        ev_dir = target_dir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        out_path = ev_dir / "elenchus_challenge.json"
        out_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        receipt["receipt_path"] = str(out_path)
        append_triad_provenance_event(
            event_type="elenchus.challenged",
            payload={
                "project_name": packet["project_name"],
                "seal_status": receipt["seal_status"],
                "panel_mode": receipt["panel"]["panel_mode"],
                "n_eff": receipt["panel"]["n_eff"],
                "clusters_count": receipt["metrics"]["total_clusters"],
                "disagreement_index": receipt["metrics"]["disagreement_index"],
                "tree_sha": packet["bindings"]["tree_sha"],
                "worktree_sha": packet["bindings"]["worktree_sha"],
            },
            ledger_file=ledger_file,
            commit_sha=str(packet["bindings"]["commit_sha"]),
        )
        if receipt["seal_status"] == "READY_UNANIMOUS":
            _record_triad_from_elenchus(target_dir, subpath, receipt)

    return receipt
