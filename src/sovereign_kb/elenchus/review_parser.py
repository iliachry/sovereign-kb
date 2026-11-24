"""Structured JSON, GFM table, and prose finding parser for Elenchus reviewer outputs."""

from __future__ import annotations

import json
import re
from typing import Any

from .constants import (
    _FINDING_PATH_RE,
    _GFM_DELIM_ROW_RE,
    _LABELED_APPROVE_LINE_RE,
    _STANDALONE_APPROVE_LINE_RE,
    _TAG_ONLY_SEV_LINE_RE,
    _safe_int,
    classify_model_lineage,
)
from .git_hash import _strip_dot_slash
from .prose_rules import (
    _ANY_LABELED_DISPOSITION_RE,
    _ANY_STANDALONE_APPROVE_CAND_RE,
    _ASSERTIVE_NEG_HEADING_RE,
    _BARE_NEG_PROSE_RE,
    _CHALLENGE_DISPOSITION_LINE_RE,
    _CONDITIONAL_APPROVE_PROSE_RE,
    _CROSS_BOUNDARY_CONDITIONAL_APPROVE_RE,
    _HEADING_CAVEAT_RE,
    _LEADING_CONDITIONAL_CLAUSE_RE,
    _POST_APPROVAL_CAVEAT_RE,
    _PRE_CONDITION_APPROVE_PROSE_RE,
    _PROSE_APPROVAL_CLAIM_RE,
    _PROSE_CHALLENGE_ASSERTION_RE,
    _PROSE_VETO_ASSERTION_RE,
    _STANDALONE_CHALLENGE_LINE_RE,
    _STANDALONE_VETO_LINE_RE,
    _VETO_DISPOSITION_LINE_RE,
    _fenced_line_flags,
    _has_disqualifying_caveat_label,
    _is_benign_post_approval_line,
    _is_example_intro_line,
    _is_non_defect_summary_line,
    _is_section_label_only,
    _is_unconditional_approve_line,
    _non_fenced_unquoted_lines,
    _normalize_finding_dict,
    _normalize_verdict,
    _parse_fenced_spans,
    _strip_closing_atx_hashes,
    _strip_inline_code_and_quotes,
    _strip_inline_formatting,
    _trim_blank_outer_lines,
)
from .redaction import _scrub_review_dict
from .table_parser import (
    _parse_prose_finding_line,
    _parse_table_row_finding,
    _split_gfm_table_cells,
    _unwrap_table_code_and_fmt,
)


def _extract_prose_findings(
    text: str,
    severities: tuple[str, ...] = ("P0", "P1", "P2", "INFO"),
    extra_secrets: list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    raw_lines = (text or "").splitlines()
    fence_flags = _fenced_line_flags(raw_lines)
    in_gfm_table = False
    table_headers: list[str] = []
    prev_unquoted = ""
    idx = 0
    n = len(raw_lines)
    while idx < n:
        raw_ln = raw_lines[idx]
        fenced = fence_flags[idx]
        s = raw_ln.strip()
        if fenced or not s or s.startswith(">"):
            in_gfm_table = False
            table_headers = []
            prev_unquoted = ""
            idx += 1
            continue
        if _GFM_DELIM_ROW_RE.match(s):
            in_gfm_table = True
            if prev_unquoted and "|" in prev_unquoted and not prev_unquoted.startswith("#"):
                table_headers = [
                    _unwrap_table_code_and_fmt(c).lower().rstrip(":") for c in _split_gfm_table_cells(prev_unquoted)
                ]
            else:
                table_headers = []
            prev_unquoted = ""
            idx += 1
            continue
        if in_gfm_table and (
            "|" not in s or s.startswith("#") or (not s.startswith("|") and bool(re.match(r"^(?:[-*+]|\d+[.)])\s+", s)))
        ):
            in_gfm_table = False
            table_headers = []
        prev_unquoted = s
        if in_gfm_table:
            parsed_line = _parse_table_row_finding(s, allow_pipeless=True, header_cols=table_headers)
        else:
            parsed_line = _parse_prose_finding_line(s)
        sev_tag_init: str | None = None
        cat_note_init = ""
        if parsed_line is not None and not in_gfm_table and _is_section_label_only(parsed_line[1]):
            sev_tag_init = parsed_line[0].upper()
            parsed_line = None
        if parsed_line is None and not in_gfm_table:
            if sev_tag_init is None:
                cand_tag = _strip_inline_formatting(_strip_closing_atx_hashes(s))
                m_tag = _TAG_ONLY_SEV_LINE_RE.match(cand_tag)
                if m_tag:
                    sev_tag_init = m_tag.group(1).upper()
                    cat_note_init = (m_tag.group(2) or "").strip()
            if sev_tag_init is not None:
                sev_tag = sev_tag_init
                cat_note = cat_note_init
                k = idx + 1
                while k < n and not fence_flags[k]:
                    k_fmt = _strip_inline_formatting(_strip_closing_atx_hashes(raw_lines[k].strip()))
                    if not k_fmt.strip() or _is_section_label_only(k_fmt):
                        k += 1
                        continue
                    break
                if k < n and not fence_flags[k]:
                    nxt = raw_lines[k].strip()
                    nxt_fmt = _strip_inline_formatting(_strip_closing_atx_hashes(nxt))
                    nxt_body = re.sub(r"^#+\s*", "", nxt_fmt).strip()
                    if nxt_body and _is_non_defect_summary_line(nxt_body):
                        idx = k
                    elif (
                        nxt_body
                        and not nxt.startswith((">", "|"))
                        and not _GFM_DELIM_ROW_RE.match(nxt)
                        and not _TAG_ONLY_SEV_LINE_RE.match(nxt_fmt)
                        and _parse_prose_finding_line(nxt) is None
                        and not _ANY_LABELED_DISPOSITION_RE.match(nxt_fmt)
                        and not _LABELED_APPROVE_LINE_RE.match(nxt_fmt)
                        and not _ANY_STANDALONE_APPROVE_CAND_RE.match(nxt_fmt)
                    ):
                        full_body = f"{cat_note} {nxt_body}".strip() if cat_note else nxt_body
                        parsed_line = (sev_tag, full_body)
                        idx = k
                    elif sev_tag in ("P0", "P1") and (not nxt_body or not _is_non_defect_summary_line(nxt_body)):
                        parsed_line = (sev_tag, f"Unresolved {sev_tag} marker {cat_note}".strip())
                elif sev_tag in ("P0", "P1"):
                    parsed_line = (sev_tag, f"Unresolved {sev_tag} marker {cat_note}".strip())
        idx += 1
        if not parsed_line:
            continue
        sev, body = parsed_line
        if sev not in severities or _is_non_defect_summary_line(body):
            continue
        file_path = ""
        line_no = 0
        m_path = _FINDING_PATH_RE.search(body)
        if m_path:
            file_path = _strip_dot_slash(m_path.group(1))
            if m_path.group(2):
                line_no = min(10_000_000, max(0, _safe_int(m_path.group(2), 0) or 0))
        out.append(
            _normalize_finding_dict(
                {
                    "severity": sev,
                    "category": "correctness",
                    "file_path": file_path,
                    "line": line_no,
                    "symbol": "",
                    "claim": body,
                    "evidence": s[:240],
                },
                extra_secrets,
            )
        )
    return out


def _soft_wrapped_blocks(text: str) -> list[tuple[str, bool]]:
    lines = (text or "").splitlines()
    flags = _fenced_line_flags(lines)
    blocks: list[tuple[str, bool]] = []
    cur: list[str] = []
    for raw_ln, fenced in zip(lines, flags, strict=False):
        s = raw_ln.strip()
        is_setext_ul = bool(cur and re.match(r"^(?:={1,}|-{1,})\s*$", s))
        if fenced or not s or s.startswith((">", "#", "|")):
            if cur:
                blocks.append((" ".join(cur), False))
                cur = []
            continue
        if is_setext_ul:
            blocks.append((" ".join(cur), True))
            cur = []
            continue
        s_fmt = _strip_inline_formatting(_strip_closing_atx_hashes(s))
        if (
            re.match(r"^(?:[-*+]|\d+[.)])\s+", s)
            or _ANY_LABELED_DISPOSITION_RE.match(s_fmt)
            or _LABELED_APPROVE_LINE_RE.match(s_fmt)
            or _ANY_STANDALONE_APPROVE_CAND_RE.match(s_fmt)
        ):
            if cur:
                blocks.append((" ".join(cur), False))
                cur = []
            cur.append(_strip_inline_code_and_quotes(s))
            continue
        cur.append(_strip_inline_code_and_quotes(s))
    if cur:
        blocks.append((" ".join(cur), False))
    return blocks


def _prefix_allows_approve(prefix: str, check_example_intro: bool = True) -> bool:
    nf_lines = _non_fenced_unquoted_lines(prefix)
    if not nf_lines:
        return True
    seen_approval_claim = False
    for i, ln in enumerate(nf_lines):
        ln_norm = _strip_closing_atx_hashes(ln)
        ln_fmt = _strip_inline_formatting(ln_norm)
        m_any_disp = _ANY_LABELED_DISPOSITION_RE.match(ln_fmt)
        m_app_cand = _LABELED_APPROVE_LINE_RE.match(ln_fmt) or _ANY_STANDALONE_APPROVE_CAND_RE.match(ln_fmt)
        if m_any_disp or m_app_cand:
            if not _is_unconditional_approve_line(ln_norm):
                return False
            if check_example_intro:
                prev_window = nf_lines[max(0, i - 3) : i]
                if any(_is_example_intro_line(p) for p in prev_window):
                    return False
            for prev_ln in nf_lines[max(0, i - 2) : i]:
                if not prev_ln.startswith(("#", "|")) and _LEADING_CONDITIONAL_CLAUSE_RE.match(
                    _strip_inline_code_and_quotes(prev_ln)
                ):
                    return False
            for j in range(i + 1, len(nf_lines)):
                nxt = _strip_closing_atx_hashes(nf_lines[j])
                if nxt.startswith("#"):
                    if _HEADING_CAVEAT_RE.match(_strip_inline_code_and_quotes(nxt)):
                        return False
                    break
                if not _is_benign_post_approval_line(nxt):
                    return False
            seen_approval_claim = True
            continue
        bare = _strip_inline_code_and_quotes(ln_norm)
        is_setext = (
            i + 1 < len(nf_lines)
            and not ln_norm.startswith(("#", ">", "|"))
            and bool(re.match(r"^(?:={1,}|-{1,})\s*$", nf_lines[i + 1]))
        )
        if ln_norm.startswith("#") or is_setext:
            hdr_bare = bare if ln_norm.startswith("#") else f"# {bare}"
            hdr_body = re.sub(r"^#+\s*", "", hdr_bare).strip()
            if (
                _VETO_DISPOSITION_LINE_RE.match(hdr_bare)
                or _CHALLENGE_DISPOSITION_LINE_RE.match(hdr_bare)
                or _STANDALONE_VETO_LINE_RE.match(hdr_bare)
                or _STANDALONE_CHALLENGE_LINE_RE.match(hdr_bare)
                or _HEADING_CAVEAT_RE.match(hdr_bare)
                or _has_disqualifying_caveat_label(hdr_body)
                or _PROSE_VETO_ASSERTION_RE.search(hdr_body)
                or _PROSE_CHALLENGE_ASSERTION_RE.search(hdr_body)
                or _ASSERTIVE_NEG_HEADING_RE.search(hdr_body)
                or _CONDITIONAL_APPROVE_PROSE_RE.search(hdr_body)
                or _PRE_CONDITION_APPROVE_PROSE_RE.search(hdr_body)
                or (is_setext and _BARE_NEG_PROSE_RE.search(bare))
                or (seen_approval_claim and _POST_APPROVAL_CAVEAT_RE.search(hdr_body))
            ):
                return False
            m_hdr_claim = _PROSE_APPROVAL_CLAIM_RE.search(hdr_body)
            if m_hdr_claim:
                if _POST_APPROVAL_CAVEAT_RE.search(hdr_body[m_hdr_claim.end() :]):
                    return False
                seen_approval_claim = True
            continue
        if (
            _VETO_DISPOSITION_LINE_RE.match(bare)
            or _CHALLENGE_DISPOSITION_LINE_RE.match(bare)
            or _STANDALONE_VETO_LINE_RE.match(bare)
            or _STANDALONE_CHALLENGE_LINE_RE.match(bare)
            or _PROSE_VETO_ASSERTION_RE.search(bare)
            or _PROSE_CHALLENGE_ASSERTION_RE.search(bare)
            or _BARE_NEG_PROSE_RE.search(bare)
            or _CONDITIONAL_APPROVE_PROSE_RE.search(bare)
            or _PRE_CONDITION_APPROVE_PROSE_RE.search(bare)
            or _has_disqualifying_caveat_label(bare)
        ):
            return False
        if not bare.lstrip().startswith("|"):
            if seen_approval_claim and _POST_APPROVAL_CAVEAT_RE.search(bare):
                return False
            m_claim = _PROSE_APPROVAL_CLAIM_RE.search(bare)
            if m_claim:
                if _POST_APPROVAL_CAVEAT_RE.search(bare[m_claim.end() :]):
                    return False
                seen_approval_claim = True
    for blk, is_setext_blk in _soft_wrapped_blocks(prefix):
        if (
            _BARE_NEG_PROSE_RE.search(blk)
            or _CONDITIONAL_APPROVE_PROSE_RE.search(blk)
            or _PRE_CONDITION_APPROVE_PROSE_RE.search(blk)
            or (
                is_setext_blk
                and (
                    _HEADING_CAVEAT_RE.match(f"# {blk}")
                    or _has_disqualifying_caveat_label(blk)
                    or _ASSERTIVE_NEG_HEADING_RE.search(blk)
                )
            )
        ):
            return False
    stripped_block = "\n".join(_strip_inline_code_and_quotes(x) for x in nf_lines if not x.startswith("#"))
    if _CROSS_BOUNDARY_CONDITIONAL_APPROVE_RE.search(stripped_block):
        return False
    if check_example_intro:
        for ln in nf_lines[-3:]:
            if _is_example_intro_line(ln):
                return False
    return True


def _try_extract_json_review_with_span(raw_text: str) -> tuple[dict[str, Any], int, int] | None:
    stripped = _trim_blank_outer_lines(raw_text)
    if not stripped:
        return None
    fences = _parse_fenced_spans(stripped)

    def _is_valid_review_obj(obj: Any) -> bool:
        return isinstance(obj, dict) and ("verdict" in obj or "findings" in obj or "disposition" in obj)

    def _prefix_obj_allows(obj: dict[str, Any], prefix: str) -> bool:
        raw_v = str(obj.get("verdict") or obj.get("disposition") or "")
        if _normalize_verdict(raw_v, []) != "APPROVE":
            return True
        return _prefix_allows_approve(prefix, check_example_intro=True)

    def _post_tail_allows(obj: dict[str, Any], tail_after: str) -> bool:
        t = tail_after.strip()
        if not t:
            return True
        raw_v = str(obj.get("verdict") or obj.get("disposition") or "")
        if _normalize_verdict(raw_v, []) != "APPROVE":
            return True
        if _extract_prose_findings(t, severities=("P0", "P1")):
            return False
        if re.search(r"\b(?:CHALLENGE|VETO|NOT_APPROVED|NOT APPROVED|REQUEST_CHANGES|REJECTED?)\b", t.upper()):
            return False
        return all(_is_benign_post_approval_line(ln) for ln in t.splitlines())

    if stripped.lstrip().startswith("{") and not fences:
        try:
            obj = json.loads(stripped.lstrip())
            if _is_valid_review_obj(obj):
                return obj, 0, len(stripped)
        except Exception:
            pass

    for b_start, b_end, in_start, in_end, info, closed in reversed(fences):
        if not closed or info not in ("", "json", "jsonc"):
            continue
        inner = stripped[in_start:in_end].strip()
        if not inner.startswith("{"):
            continue
        try:
            obj = json.loads(inner)
        except Exception:
            continue
        if (
            _is_valid_review_obj(obj)
            and _prefix_obj_allows(obj, stripped[:b_start])
            and _post_tail_allows(obj, stripped[b_end:])
        ):
            return obj, b_start, b_end

    decoder = json.JSONDecoder()
    brace_positions = [m.start() for m in re.finditer(r"(?:^|\n)\s*\{", stripped)]
    for pos in reversed(brace_positions[-40:]):
        brace_idx = stripped.find("{", pos)
        if brace_idx == -1:
            continue
        if any(bs <= brace_idx < be for bs, be, *_ in fences):
            continue
        try:
            obj, end_idx = decoder.raw_decode(stripped, brace_idx)
        except Exception:
            continue
        if (
            _is_valid_review_obj(obj)
            and not stripped[end_idx:].strip()
            and _prefix_obj_allows(obj, stripped[:brace_idx])
        ):
            return obj, brace_idx, end_idx
    return None


def _try_extract_json_review(raw_text: str) -> dict[str, Any] | None:
    res = _try_extract_json_review_with_span(raw_text)
    return res[0] if res is not None else None


def _has_anchored_prose_approval(raw_text: str) -> bool:
    lines = (raw_text or "").splitlines()
    is_fenced = _fenced_line_flags(lines)

    for idx, raw_ln in enumerate(lines):
        if is_fenced[idx]:
            continue
        ln = raw_ln.strip()
        if not ln or ln.startswith(">"):
            continue
        ln_fmt = _strip_inline_formatting(_strip_closing_atx_hashes(ln))
        m = _LABELED_APPROVE_LINE_RE.match(ln_fmt) or _STANDALONE_APPROVE_LINE_RE.match(ln_fmt)
        if not m:
            continue
        if not _is_unconditional_approve_line(ln):
            return False
        if not _prefix_allows_approve("\n".join(lines[:idx]), check_example_intro=True):
            return False
        if all(_is_benign_post_approval_line(lines[j]) for j in range(idx + 1, len(lines)) if not is_fenced[j]):
            return True
        return False
    return False


def parse_reviewer_output(
    model_id: str, raw_text: str, extra_secrets: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    """
    Parse an independent reviewer's output (strict JSON or Markdown/prose) into normalized
    Elenchus verdict + finding records.
    """
    lin = classify_model_lineage(model_id)
    if not (raw_text or "").strip():
        return {
            "model_id": model_id,
            "lineage": lin,
            "verdict": "CHALLENGE",
            "execution_status": "FAILED",
            "summary": "Reviewer produced empty output.",
            "findings": [],
        }
    stripped = _trim_blank_outer_lines(raw_text)
    extracted = _try_extract_json_review_with_span(stripped)
    if extracted is not None:
        json_obj, j_start, j_end = extracted
        if "findings" in json_obj:
            raw_findings_val = json_obj["findings"]
            if not isinstance(raw_findings_val, list) or not all(isinstance(f, dict) for f in raw_findings_val):
                return {
                    "model_id": model_id,
                    "lineage": lin,
                    "verdict": "CHALLENGE",
                    "execution_status": "FAILED",
                    "summary": "Malformed JSON reviewer `findings`: expected list of finding objects.",
                    "findings": [],
                }
            raw_findings = raw_findings_val
        else:
            raw_findings = []
        findings = [_normalize_finding_dict(f, extra_secrets) for f in raw_findings if isinstance(f, dict)]
        outside_text = stripped[:j_start] + "\n" + stripped[j_end:]
        for pf in _extract_prose_findings(outside_text, severities=("P0", "P1"), extra_secrets=extra_secrets):
            if not any(ef["severity"] == pf["severity"] and ef["claim"] == pf["claim"] for ef in findings):
                findings.append(pf)
        verdict = _normalize_verdict(str(json_obj.get("verdict") or json_obj.get("disposition") or ""), findings)
        summary = str(json_obj.get("summary") or json_obj.get("notes") or "").strip()
        return _scrub_review_dict(
            {
                "model_id": model_id,
                "lineage": lin,
                "verdict": verdict,
                "execution_status": "OK",
                "summary": summary or verdict,
                "findings": findings,
            },
            extra_secrets,
        )

    findings = _extract_prose_findings(raw_text, extra_secrets=extra_secrets)

    nf_prose = [_strip_inline_code_and_quotes(x) for x in _non_fenced_unquoted_lines(raw_text)]
    has_explicit_prose_veto = any(
        _VETO_DISPOSITION_LINE_RE.match(x)
        or _STANDALONE_VETO_LINE_RE.match(x)
        or bool(_PROSE_VETO_ASSERTION_RE.search(x))
        for x in nf_prose
    )
    has_explicit_prose_challenge = any(
        _CHALLENGE_DISPOSITION_LINE_RE.match(x)
        or _STANDALONE_CHALLENGE_LINE_RE.match(x)
        or bool(_PROSE_CHALLENGE_ASSERTION_RE.search(x))
        or bool(_BARE_NEG_PROSE_RE.search(x))
        or bool(re.search(r"\b(?:NOT_APPROVED|NOT APPROVED|REQUEST_CHANGES|REQUEST CHANGES)\b", x.upper()))
        for x in nf_prose
    )
    anchored_approve = (
        not has_explicit_prose_veto
        and not has_explicit_prose_challenge
        and _has_anchored_prose_approval(raw_text)
        and not any(f["severity"] in ("P0", "P1") for f in findings)
    )
    if (
        any(f["severity"] == "P0" for f in findings)
        or has_explicit_prose_veto
        or (not anchored_approve and any(bool(re.search(r"\bVETO\b", x)) for x in nf_prose))
    ):
        verdict = "VETO"
    elif has_explicit_prose_challenge or (
        not anchored_approve and any(bool(re.search(r"\bCHALLENGE\b", x.upper())) for x in nf_prose)
    ):
        verdict = "CHALLENGE"
    elif anchored_approve:
        verdict = "APPROVE"
    elif any(f["severity"] == "P1" for f in findings):
        verdict = "CHALLENGE"
    elif findings:
        verdict = "CHALLENGE"
    else:
        verdict = "CHALLENGE"

    summary_lines = [ln for ln in _non_fenced_unquoted_lines(raw_text) if not ln.startswith("#")]
    summary = summary_lines[-1][:240] if summary_lines else verdict
    return _scrub_review_dict(
        {
            "model_id": model_id,
            "lineage": lin,
            "verdict": verdict,
            "execution_status": "OK",
            "summary": summary,
            "findings": findings,
        },
        extra_secrets,
    )


def parse_oc_triad_stdout(stdout: str) -> list[dict[str, Any]]:
    """Split parallel dual-model CLI stdout (`CLAUDE FABLE 5.1` + `GPT-6 ASTRA`) into independent reviews."""
    text = stdout or ""
    header_re = re.compile(
        r"^===\s+(CLAUDE FABLE 5\.1|GPT-6 ASTRA)\s*\(exit=(\d+)\)\s*===\s*$",
        re.MULTILINE,
    )
    matches = list(header_re.finditer(text))
    if not matches:
        return []

    model_map = {
        "CLAUDE FABLE 5.1": "amazon-bedrock/global.anthropic.claude-fable-5-1",
        "GPT-6 ASTRA": "amazon-bedrock/global.openai.gpt-6-astra",
    }
    parsed: list[dict[str, Any]] = []
    for idx, m in enumerate(matches):
        label = m.group(1)
        rc = int(m.group(2))
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = _trim_blank_outer_lines(text[start:end])
        turns = re.split(r"(?m)^> build · [^\r\n]+$", body)
        final_body = next((_trim_blank_outer_lines(seg) for seg in reversed(turns) if seg.strip()), body)
        model_id = model_map[label]
        rev = parse_reviewer_output(model_id, final_body)
        if rc != 0 or not body:
            rev["execution_status"] = "FAILED"
            if rev["verdict"] == "APPROVE" or not body:
                rev["verdict"] = "CHALLENGE"
            if not rev["findings"]:
                rev["findings"].append(
                    _normalize_finding_dict(
                        {
                            "severity": "P1",
                            "category": "provenance",
                            "claim": f"Reviewer process failed (exit={rc}, empty={not body}) before issuing review.",
                            "evidence": f"exit={rc}",
                        }
                    )
                )
        parsed.append(rev)
    return parsed
