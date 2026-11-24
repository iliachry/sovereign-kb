"""Markdown fence stripping, inline formatting normalization, and anti-hedging approval guards."""

from __future__ import annotations

import re
from typing import Any

from .constants import (
    _APPROVE_TAIL_DISQUALIFY_RE,
    _LABELED_APPROVE_LINE_RE,
    _STANDALONE_APPROVE_LINE_RE,
    _VALID_CATEGORIES,
    _VALID_SEVERITIES,
    _safe_int,
)
from .redaction import _extract_url_secrets, _redact_sensitive_text


def _normalize_verdict(raw_verdict: str, findings: list[dict[str, Any]]) -> str:
    up = (raw_verdict or "").strip().upper().replace("-", "_").replace(" ", "_")
    if up in ("VETO", "REJECT", "REJECTED", "BLOCK", "FAIL", "FAILED"):
        return "VETO"
    if up in ("CHALLENGE", "REQUEST_CHANGES", "REVISE", "NOT_APPROVED", "WARN"):
        return "CHALLENGE"
    if any(f.get("severity") == "P0" for f in findings):
        return "VETO"
    if any(f.get("severity") == "P1" for f in findings):
        return "CHALLENGE"
    if up in ("APPROVE", "APPROVED", "PASS", "LGTM"):
        return "APPROVE"
    return "CHALLENGE"


def _normalize_finding_dict(item: dict[str, Any], extra_secrets: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    secrets = list(extra_secrets) + list(_extract_url_secrets(""))
    sev = str(item.get("severity", "P1")).strip().upper()
    if sev not in _VALID_SEVERITIES:
        sev = "P1"
    cat = str(item.get("category", "correctness")).strip().lower()
    if cat not in _VALID_CATEGORIES:
        cat = "correctness"
    raw_fp = str(item.get("file_path") or item.get("file") or item.get("path") or "").strip()
    fp_scrubbed = _redact_sensitive_text(raw_fp, secrets).replace("\\", "/")
    fp_raw = _redact_sensitive_text(fp_scrubbed, secrets)
    while fp_raw.startswith("./") or fp_raw.startswith("/"):
        fp_raw = fp_raw[2:] if fp_raw.startswith("./") else fp_raw.lstrip("/")
    line_parsed = _safe_int(item.get("line", 0), 0)
    line_no = min(10_000_000, max(0, line_parsed if line_parsed is not None else 0))
    sym = _redact_sensitive_text(str(item.get("symbol", "") or "").strip(), secrets)
    raw_claim = str(
        item.get("claim")
        or item.get("message")
        or item.get("title")
        or item.get("description")
        or item.get("summary")
        or item.get("detail")
        or item.get("details")
        or item.get("reason")
        or item.get("issue")
        or item.get("finding")
        or ""
    ).strip()
    evidence = _redact_sensitive_text(str(item.get("evidence") or "").strip(), secrets)
    claim = _redact_sensitive_text(raw_claim, secrets)
    if not claim:
        loc_bits = " ".join(x for x in (fp_raw, sym) if x).strip()
        if evidence:
            claim = evidence[:240]
        elif loc_bits:
            claim = f"Unspecified {sev} finding at {loc_bits}"
        else:
            claim = f"Unspecified {sev} finding"
    return {
        "severity": sev,
        "category": cat,
        "file_path": fp_raw,
        "line": line_no,
        "symbol": sym,
        "claim": claim,
        "evidence": evidence,
    }


_VETO_DISPOSITION_LINE_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*]\s*)?\*{0,2}(?:DISPOSITION|VERDICT|DECISION|STATUS|RESULT)\*{0,2}\s*"
    r"[:=—-]\*{0,2}\s*\*{0,2}(?:VETO|REJECT(?:ED)?|FAIL(?:ED)?|BLOCK(?:ED)?)\b",
    re.IGNORECASE,
)
_CHALLENGE_DISPOSITION_LINE_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*]\s*)?\*{0,2}(?:DISPOSITION|VERDICT|DECISION|STATUS|RESULT)\*{0,2}\s*"
    r"[:=—-]\*{0,2}\s*\*{0,2}(?:CHALLENGE|REVISE|WARN|NOT[_ ]APPROVED|REQUEST[_ ]CHANGES)\b",
    re.IGNORECASE,
)
_STANDALONE_VETO_LINE_RE = re.compile(
    r"^(?:#+\s*)?\*{0,2}(?:VETO|REJECTED|DO\s+NOT\s+MERGE)\*{0,2}[.!]*$",
    re.IGNORECASE,
)
_STANDALONE_CHALLENGE_LINE_RE = re.compile(
    r"^(?:#+\s*)?\*{0,2}(?:CHALLENGE|NOT\s+APPROVED|REQUEST\s+CHANGES)\*{0,2}[.!]*$",
    re.IGNORECASE,
)
_ANY_LABELED_DISPOSITION_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*]\s*)?\*{0,2}(?:DISPOSITION|VERDICT|DECISION|STATUS|RESULT)\*{0,2}\s*[:=—-]\s*(.+)$",
    re.IGNORECASE,
)
_PROSE_VETO_ASSERTION_RE = re.compile(
    r"(?:\b(?:my|our|final|overall|actual)\s+)?\b(?:verdict|disposition|decision|conclusion)\s+"
    r"(?:is|remains|stays)\s+\*{0,2}(?:VETO|REJECT(?:ED)?|FAIL(?:ED)?|BLOCK(?:ED)?)\b"
    r"|^(?:actually|overall|final|conclusion)\s*[:=—-]\s*\*{0,2}(?:VETO|REJECT(?:ED)?|FAIL(?:ED)?|BLOCK(?:ED)?)\b"
    r"|\bI\s+(?:veto|reject)\b",
    re.IGNORECASE,
)
_PROSE_CHALLENGE_ASSERTION_RE = re.compile(
    r"(?:\b(?:my|our|final|overall|actual)\s+)?\b(?:verdict|disposition|decision|conclusion)\s+"
    r"(?:is|remains|stays)\s+\*{0,2}(?:CHALLENGE|REVISE|WARN|NOT\s+APPROVED|REQUEST\s+CHANGES)\b"
    r"|^(?:actually|overall|final|conclusion)\s*[:=—-]\s*\*{0,2}(?:CHALLENGE|REVISE|WARN|NOT\s+APPROVED|REQUEST\s+CHANGES)\b"
    r"|\bI\s+(?:cannot\s+approve|do\s+not\s+approve)\b",
    re.IGNORECASE,
)
_EXAMPLE_HEADING_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*]\s*)?\*{0,2}(?:example|sample|template|schema|placeholder|dummy|hypothetical|mock|"
    r"expected\s+(?:output|format|json|response)|output\s+format)(?:\s+[A-Za-z0-9_-]+){0,4}\*{0,2}:?\s*$",
    re.IGNORECASE,
)
_EXAMPLE_COLON_CLAUSE_RE = re.compile(
    r"\b(?:example|sample|template|schema|placeholder|output\s+format|expected\s+format|format\s+only|"
    r"hypothetical|dummy|mock)\b[^.!\n]{0,80}:\s*$",
    re.IGNORECASE,
)
_ZERO_QUANT_GUARD = r"(?<!\bno )(?<!\bnone )(?<!\bzero )(?<!\b0 )"
_NEG_AUX_PASSIVE_SRC = (
    r"(?:(?:(?:is|are|was|were|has|have|had)\s+)?(?:still\s+)?not\s+(?:yet\s+)?(?:been\s+)?(?:yet\s+)?|"
    r"(?:(?:is|are|was|were|has|have|had)\s+)?(?:still\s+)?(?:never(?:\s+been)?|yet\s+to\s+be)\s+|"
    r"(?:isn't|aren't|wasn't|weren't|hasn't|haven't|hadn't|did\s+not|didn't)\s+(?:still\s+)?(?:yet\s+)?(?:been\s+)?(?:yet\s+)?)"
)
_ASSERTIVE_NEG_CLAUSE_SRC = (
    r"do\s+not\s+(?:merge|seal|ship|approve)|not\s+ready|"
    r"(?:review|audit|verification|coverage|work|testing)\s+(?:is|was|remains?)\s+(?:still\s+)?(?:incomplete|skipped|omitted)|"
    r"evaluation\s+(?:was|is)\s+(?:still\s+)?skipped|"
    + _ZERO_QUANT_GUARD
    + r"tests?\s+(?:"
    + _NEG_AUX_PASSIVE_SRC
    + r"(?:run|executed)|failed|failing)|"
    r"(?:ci|build|gate|tests?|pipeline)\s+(?:is|are|remains?)\s+red|"
    r"cannot\s+(?:approve|seal)|can't\s+(?:approve|seal)|not\s+approved|request\s+changes|"
    + _ZERO_QUANT_GUARD
    + r"changes\s+requested|"
    r"subject\s+to\s+fixing|[1-9]\d*\s+p[01]\b|"
    r"(?:once|when|until|unless|provided(?:\s+that)?|assuming(?:\s+that)?|only\s+(?:if|when|after))\s+"
    r"(?:the\s+|all\s+)?(?:unit\s+|integration\s+|regression\s+)?(?:tests?|ci|checks?|build|gate|migration|fixes?|parser|changes?)\s+"
    r"(?:pass(?:es)?|succeeds?|is\s+(?:green|tested|fixed|verified)|are\s+(?:green|tested|fixed|verified|passing|added|run))|"
    r"(?:tests?|ci|checks?|build|gate|migration)\s+(?:must|need\s+to|needs?\s+to|have\s+to|has\s+to)\s+"
    r"(?:pass|succeed|be\s+(?:run|added|fixed|green|tested|verified))|"
    r"(?:needs?|requires?)\s+(?:fixing|remediation|rework|to\s+be\s+fixed)|"
    r"(?:is|are|remains?)\s+(?:still\s+)?(?:broken|flaky|buggy|unimplemented|unfixed|untested|unverified)|"
    + _ZERO_QUANT_GUARD
    + r"(?:tests?|ci|checks?|build|gate|migration|parser|code)\s+(?:still\s+)?(?:flaky|broken|buggy|untested|unverified)|"
    r"(?:must|need\s+to|needs\s+to|have\s+to|has\s+to)\s+be\s+(?:fixed|resolved|addressed|remediated|repaired|corrected|reverted|reworked|tested|verified|added|run)|"
    r"(?:must|need\s+to|needs\s+to|have\s+to|has\s+to)\s+(?:fix|resolve|address|remediate|repair|correct|revert|rework|add\s+tests?|run\s+tests?)|"
    r"should\s+be\s+(?:fixed|resolved|addressed|remediated|repaired|corrected|reverted|reworked)|"
    + _ZERO_QUANT_GUARD
    + r"(?:fix(?:es)?|remediation|rework|changes?)\s+(?:is\s+|are\s+)?required|"
    + _ZERO_QUANT_GUARD
    + r"(?:tests?|ci|checks?|build|gate|pipeline)\s+(?:(?:is|are|remains?)\s+(?:still\s+)?failing|failed|fails?\b(?!\s+(?:closed|safely|cleanly|gracefully|as\s+expected|with\b)))|"
    + _ZERO_QUANT_GUARD
    + r"(?:parser|app|server|cli|service|code|handler|function)\s+crashes\b|"
    + _ZERO_QUANT_GUARD
    + r"security\s+vulnerabilit(?:y|ies)\s+in\b|"
    + _ZERO_QUANT_GUARD
    + r"(?:(?:[1-9]\d*|one|two|three|four|five|a|an|the)\s+)?p[01]s?"
    r"(?:\s+(?:findings?|issues?|blockers?|defects?|items?|regressions?))?\s+"
    r"(?:(?:(?:is|are|was|were|remains?|stays?)\s+)?(?:still\s+)?"
    r"(?:remain(?:s|ing)?(?!\s+(?:adjudication[- ]blocked|quarantined|gated|held|protected|blocked\s+from\s+sealing|rejected\s+by))|"
    r"outstanding|unresolved|open|left|unfixed|blocking|unaddressed|persists?|reopened|partially\s+(?:fixed|resolved|addressed))|"
    r"(?:(?:has|have|had|did)\s+(?:still\s+)?(?:not|never)|hasn't|haven't|hadn't|didn't)\s+(?:yet\s+)?land(?:ed)?|"
    + _NEG_AUX_PASSIVE_SRC
    + r"(?:fixed|resolved|addressed|closed|remediated|mitigated|applied|landed|merged|deployed))|"
    + _ZERO_QUANT_GUARD
    + r"(?:(?:the|a|an)\s+)?p[01]s?\s+fix(?:es)?\s+(?:"
    r"(?:is|are|remains?|stays?)\s+(?:still\s+)?(?:pending|incomplete|missing|needed|required|open)|"
    r"(?:(?:has|have|had|did)\s+(?:still\s+)?(?:not|never)|hasn't|haven't|hadn't|didn't)\s+(?:yet\s+)?(?:land(?:ed)?|merge(?:d)?|apply|applied)|"
    + _NEG_AUX_PASSIVE_SRC
    + r"(?:applied|landed|merged|deployed|fixed|resolved|addressed|verified|tested))|"
    r"(?:one|two|three|four|five)\s+p[01]\b"
)
_ASSERTIVE_NEG_HEADING_RE = re.compile(r"\b(?:" + _ASSERTIVE_NEG_CLAUSE_SRC + r")\b", re.IGNORECASE)
_BARE_NEG_PROSE_RE = re.compile(
    r"\b(?:"
    + _ASSERTIVE_NEG_CLAUSE_SRC
    + r"|"
    + _ZERO_QUANT_GUARD
    + r"(?:review|audit|verification|coverage|work)\s+incomplete"
    + r"|"
    + _ZERO_QUANT_GUARD
    + r"incomplete\s+(?:review|audit|verification)"
    + r"|conditional\s+(?:on|approval)|provisional\s+approval"
    + r"|"
    + _ZERO_QUANT_GUARD
    + r"pending\s+(?:review|tests?|verification|fix\w*|changes?))\b"
    + r"|^\s*(?:#+\s*)?fix\s+the\s+[A-Za-z0-9_-]+\b",
    re.IGNORECASE,
)


_FENCE_SEQ_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")


def _trim_blank_outer_lines(text: str) -> str:
    """Strip leading/trailing blank lines and trailing whitespace without altering first-line indentation."""
    lines = (text or "").splitlines(keepends=True)
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    if start >= end:
        return ""
    return "".join(lines[start:end]).rstrip()


def _match_commonmark_open_fence(raw_ln: str) -> tuple[str, int, str] | None:
    stripped_eol = raw_ln.rstrip("\r\n")
    indent = len(stripped_eol) - len(stripped_eol.lstrip(" "))
    if indent > 3:
        return None
    m = _FENCE_SEQ_RE.match(stripped_eol[indent:])
    if not m:
        return None
    seq, rest = m.group(1), m.group(2)
    fc = seq[0]
    if fc == "`" and "`" in rest:
        return None
    return fc, len(seq), rest.strip().lower()


def _is_commonmark_close_fence(raw_ln: str, fc: str, min_len: int) -> bool:
    stripped_eol = raw_ln.rstrip("\r\n")
    indent = len(stripped_eol) - len(stripped_eol.lstrip(" "))
    if indent > 3:
        return False
    m = _FENCE_SEQ_RE.match(stripped_eol[indent:])
    if not m:
        return False
    seq, rest = m.group(1), m.group(2)
    return seq[0] == fc and len(seq) >= min_len and all(ch in (" ", "\t") for ch in rest)


def _fenced_line_flags(lines: list[str]) -> list[bool]:
    is_fenced: list[bool] = []
    fc = ""
    fl = 0
    for raw_ln in lines:
        if not fc:
            mf = _match_commonmark_open_fence(raw_ln)
            if mf:
                fc, fl, _ = mf
                is_fenced.append(True)
            else:
                is_fenced.append(False)
        else:
            is_fenced.append(True)
            if _is_commonmark_close_fence(raw_ln, fc, fl):
                fc = ""
                fl = 0
    return is_fenced


def _non_fenced_unquoted_lines(text: str) -> list[str]:
    lines = (text or "").splitlines()
    flags = _fenced_line_flags(lines)
    out: list[str] = []
    for raw_ln, fenced in zip(lines, flags, strict=False):
        if fenced:
            continue
        s = raw_ln.strip()
        if s and not s.startswith(">"):
            out.append(s)
    return out


_INLINE_HTML_FMT_RE = re.compile(
    r"<(/?(?:em|strong|b|i|u|s|del|ins|mark|span|sub|sup|small|big|font))\b[^>\n]{0,120}>",
    re.IGNORECASE,
)
_MD_INLINE_LINK_RE = re.compile(r"!?\[([^\]\n]{1,200})\](?:\([^)\n]{0,240}\)|\[[^\]\n]{0,120}\])")
_NBSP_ENTITY_RE = re.compile(r"(?:&nbsp;|&#160;|&#xa0;)", re.IGNORECASE)
_CODE_SUBJECT_NOUN_RE = re.compile(
    r"(`+)(ci|tests?|build|gate|checks?|pipeline|migration|parser|code|must|should|cannot|needs?|requires?)\1",
    re.IGNORECASE,
)


def _normalize_unicode_prose_punct(s: str) -> str:
    return (
        s.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u02bc", "'")
        .replace("\u2032", "'")
        .replace("\uff07", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\uff02", '"')
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2212", "-")
        .replace("\u2013", "—")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace("\u205f", " ")
        .replace("\u3000", " ")
    )


def _strip_inline_formatting(s: str) -> str:
    s = re.sub(r"<!--.*?-->", "", s)
    s = _normalize_unicode_prose_punct(_NBSP_ENTITY_RE.sub(" ", s))
    s = re.sub(r"[\u200b-\u200d\ufeff\u2060]", "", s)
    for _ in range(2):
        s = _MD_INLINE_LINK_RE.sub(r"\1", s)
    s = _INLINE_HTML_FMT_RE.sub("", s)
    s = re.sub(r"\\([*_~])", r"\1", s)
    m_lead = re.match(r"^(\s*(?:#+\s*)?(?:[-*+]|\d+[.)])\s+)(.*)$", s)
    if m_lead:
        lead, rest = m_lead.group(1), m_lead.group(2)
    else:
        lead, rest = "", s
    rest = re.sub(r"\*+|~+", "", rest)
    rest = re.sub(r"(?<![A-Za-z0-9_])_+|_+(?![A-Za-z0-9_])", "", rest)
    return re.sub(r"[ \t]{2,}", " ", lead + rest)


def _strip_inline_code_and_quotes(s: str) -> str:
    s = _normalize_unicode_prose_punct(s)
    s = re.sub(r'"[^"\n]{1,240}"', " ", s)
    s = re.sub(r"(?<![A-Za-z0-9])'[^'\n]{1,240}'(?![A-Za-z0-9])", " ", s)
    s = _CODE_SUBJECT_NOUN_RE.sub(r"\2", s)
    s = re.sub(r"(`+)(.+?)\1", " ", s)
    return _strip_inline_formatting(s)


def _strip_closing_atx_hashes(ln: str) -> str:
    if ln.lstrip().startswith("#"):
        return re.sub(r"[ \t]+#+[ \t]*$", "", ln)
    return ln


def _is_example_intro_line(ln: str) -> bool:
    bare = _strip_inline_code_and_quotes(_strip_closing_atx_hashes(ln)).strip()
    if not bare:
        return False
    return bool(_EXAMPLE_HEADING_RE.match(bare) or _EXAMPLE_COLON_CLAUSE_RE.search(bare))


def _is_unconditional_approve_line(ln: str) -> bool:
    ln_fmt = _strip_inline_formatting(_strip_closing_atx_hashes(ln))
    m = _LABELED_APPROVE_LINE_RE.match(ln_fmt) or _STANDALONE_APPROVE_LINE_RE.match(ln_fmt)
    if not m:
        return False
    tail = " ".join(grp.strip() for grp in m.groups() if grp and grp.strip()).strip()
    if not tail:
        return True
    tail_fmt = _strip_inline_formatting(tail)
    if (
        _APPROVE_TAIL_DISQUALIFY_RE.search(tail_fmt)
        or _BARE_NEG_PROSE_RE.search(tail_fmt)
        or re.search(r"\b[1-9]\d*\s+p[01]\b", tail_fmt, re.IGNORECASE)
    ):
        return False
    tail_norm = tail_fmt.strip("(). ")
    return not tail_norm or bool(_BENIGN_ZERO_SUMMARY_RE.match(tail_norm))


_ANY_STANDALONE_APPROVE_CAND_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*+]\s*)?\*{0,2}APPROVED?\*{0,2}(?!-[A-Za-z])(?:\s+.*|[:=—.,;!()\-].*)?$",
    re.IGNORECASE,
)
_APPROVE_WORD_RE = (
    r"(?<!→ )(?<!-> )(?<!=> )(?<!== )(?<!!= )"
    r"(?<!\bfalse[- ])(?<!\bbogus[- ])(?<!\bspurious[- ])"
    r"(?<!\d )(?<!\btwo )(?<!\bsix )(?<!\bten )"
    r"(?<!\bfour )(?<!\bfive )(?<!\bnine )(?<!\bboth )(?<!\brows )"
    r"(?<!\bthree )(?<!\bseven )(?<!\beight )(?<!\bcases )(?<!\bcells )(?<!\blines )(?<!\bforms )(?<!\bitems )"
    r"(?<!\bprobes )(?<!\binputs )(?<!\bcounts )(?<!\btables )"
    r"(?<!\bvectors )(?<!\bsamples )(?<!\bstrings )"
    r"(?<!\bvariants )(?<!\bexamples )"
    r"(?<!\bcitations )"
    r"\b(?:approv(?:e|ed|ing|al)"
    r"(?!\s+(?:claim|line|cand|candidate|gate|path|regex|check|guard|block|fence|"
    r"json|mode|status|verdict|disposition|token|header|heading)s?\b)"
    r"|lgtm|ship\s+it)\b"
)
_AFTER_CONDITION_CLAUSE = (
    r"after\s+(?:fixing|resolving|adding|addressing|updating|removing|completing|"
    r"running|testing|verifying|tests?\b|ci\b|changes?\b|review\b)"
)
_CONDITIONAL_APPROVE_PROSE_RE = re.compile(
    _APPROVE_WORD_RE + r"[^.;:|→\n]{0,80}\b(?:"
    r"if|when(?!\s+(?:followed|preceded|accompanied|paired)\s+by\b)|unless|until|once|"
    r"subject\s+to|contingent|dependent|provisional|tentative|"
    r"assuming|provided|only\s+(?:if|when|after|for)|pending|awaiting|except|but|however|"
    r"requires?|needs?|must|" + _AFTER_CONDITION_CLAUSE + r")\b",
    re.IGNORECASE,
)
_CROSS_BOUNDARY_CONDITIONAL_APPROVE_RE = re.compile(
    _APPROVE_WORD_RE + r"[^.;:|→\n]{0,80}(?:[.!?,;:—-]?\s*(?:\n\s*)?(?:"
    r"once|when(?!\s+(?:followed|preceded|accompanied|paired)\s+by\b)|whether|but|however|although|though|wait|waiting|"
    r"provided(?:\s+that)?|assuming(?:\s+that)?|unless|until|if|"
    r"only\s+(?:if|when|after|for)|subject\s+to|contingent\s+(?:on|upon)|"
    r"dependent\s+(?:on|upon)|pending|awaiting|except(?:\s+(?:if|when|for|that))?|"
    r"as\s+long\s+as|so\s+long\s+as|on\s+condition|" + _AFTER_CONDITION_CLAUSE + r")\b"
    r"|(?:[.!?;:—-]\s*(?:\n\s*)?|\n\s*)still\b)",
    re.IGNORECASE,
)
_PRE_CONDITION_APPROVE_PROSE_RE = re.compile(
    r"\b(?:if|when|unless|until|once|provided(?:\s+that)?|assuming(?:\s+that)?|subject\s+to|"
    r"contingent\s+(?:on|upon)|dependent\s+(?:on|upon)|only\s+(?:if|when|after|for)|"
    r"as\s+long\s+as|so\s+long\s+as|on\s+condition|" + _AFTER_CONDITION_CLAUSE + r")\b"
    r"[^.;:|→\n]{0,80}\b(?:approv(?:e|ed|ing|al)|lgtm|ship\s+it)\b",
    re.IGNORECASE,
)
_LEADING_CONDITIONAL_CLAUSE_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:once|when|if|unless|until|provided(?:\s+that)?|assuming(?:\s+that)?|"
    r"subject\s+to|contingent\s+(?:on|upon)|dependent\s+(?:on|upon)|only\s+(?:if|when|after|for)|"
    r"as\s+long\s+as|so\s+long\s+as|on\s+condition|" + _AFTER_CONDITION_CLAUSE + r")\b",
    re.IGNORECASE,
)
_PROSE_APPROVAL_CLAIM_RE = re.compile(
    r"(?:\b(?:i|we)\s+approve\b|"
    r"\b(?:this|change|patch|pr|diff|commit|implementation|work)\s+is\s+approved\b|"
    r"^\s*(?:approval|lgtm|ship\s+it)\b)",
    re.IGNORECASE,
)
_POST_APPROVAL_CAVEAT_RE = re.compile(
    r"\b(?:if|when|unless|until|once|whether|but|however|still|except|although|though|"
    r"provided|assuming|subject\s+to|contingent|dependent|provisional|tentative|"
    r"pending|awaiting|wait|waiting|only|must|requires?|required|needs?|should|"
    r"incomplete|untested|unverified|broken|flaky|buggy|failing|failed|blocked|fixing|"
    r"as\s+long\s+as|so\s+long\s+as|on\s+condition|" + _AFTER_CONDITION_CLAUSE + r")\b",
    re.IGNORECASE,
)
_HEADING_CAVEAT_RE = re.compile(
    r"^#+\s*(?:[-*+]\s*)?(?:\d+[.)]\s*)?\*{0,2}(?:"
    r"caveats?|conditions?|conditional\s+approval|blockers?|todos?|fixmes?|"
    r"pending(?:\s+[A-Za-z0-9_-]+){0,3}|required\s+changes?|(?:open\s+|known\s+)?issues?|"
    r"remaining\s+(?:work|issues?|items?|tasks?)|outstanding(?:\s+[A-Za-z0-9_-]+){0,3}"
    r")(?:\s+(?:for|of|to|on|before|prior\s+to|blocking)\s+[A-Za-z0-9 _-]+)?\*{0,2}\s*:?\s*$",
    re.IGNORECASE,
)
_CAVEAT_INLINE_OR_LABEL_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\d+[.)]\s*)?\*{0,2}(?:"
    r"caveats?|conditions?|conditional\s+approval|blockers?|required\s+changes?|(?:open\s+|known\s+)?issues?|"
    r"todos?|fixmes?|pending(?:\s+[A-Za-z0-9_-]+){0,2}|"
    r"remaining\s+(?:work|issues?|items?|tasks?)|outstanding(?:\s+[A-Za-z0-9_-]+){0,2}"
    r")(?:\s+(?:for|of|to|on|before|prior\s+to|blocking)\s+[A-Za-z0-9 _-]+)?"
    r"(?::\s*\*{0,2}|\*{0,2}\s*[:—]|\*{0,2}\s+--(?:\s|$))\s*(.*)$",
    re.IGNORECASE,
)


def _has_disqualifying_caveat_label(bare: str) -> bool:
    m = _CAVEAT_INLINE_OR_LABEL_RE.match(bare)
    if not m:
        return False
    tail = m.group(1).strip().strip("(). ")
    if not tail:
        return True
    return not bool(_BENIGN_ZERO_SUMMARY_RE.match(tail))


def _parse_fenced_spans(text: str) -> list[tuple[int, int, int, int, str, bool]]:
    """Return `(block_start, block_end, inner_start, inner_end, info_lower, closed)` for fenced blocks."""
    spans: list[tuple[int, int, int, int, str, bool]] = []
    pos = 0
    fc = ""
    fl = 0
    b_start = 0
    in_start = 0
    info = ""
    for raw_ln in (text or "").splitlines(keepends=True):
        ln_len = len(raw_ln)
        if not fc:
            mf = _match_commonmark_open_fence(raw_ln)
            if mf:
                fc, fl, info = mf
                b_start = pos
                in_start = pos + ln_len
        else:
            if _is_commonmark_close_fence(raw_ln, fc, fl):
                spans.append((b_start, pos + ln_len, in_start, pos, info, True))
                fc = ""
                fl = 0
                info = ""
        pos += ln_len
    if fc:
        spans.append((b_start, len(text), in_start, len(text), info, False))
    return spans


_SECTION_LABEL_ONLY_WORDS = frozenset(
    {
        "finding",
        "findings",
        "issue",
        "issues",
        "defect",
        "defects",
        "blocker",
        "blockers",
        "details",
        "description",
        "claim",
        "notes",
    }
)


def _is_section_label_only(text: str) -> bool:
    cleaned = _strip_inline_formatting((text or "").strip()).strip("(). ")
    low = re.sub(r"^#+\s*", "", cleaned).strip().lower().rstrip(":")
    return bool(low) and low in _SECTION_LABEL_ONLY_WORDS


_NON_DEFECT_CLAIM_RE = re.compile(
    r"^(?:"
    r"(?:none|n/?a|nil|0|zero|clean|lgtm|none\s+detected|none\s+found)"
    r"|(?:(?:none|no|zero)(?!-)\s+(?:new\s+|open\s+|blocking\s+|critical\s+|major\s+|remaining\s+){0,2}"
    r"(?:p0|p1|p2|p0/p1|defects?|issues?|blockers?|findings?|regressions?|concerns?|warnings?)"
    r"(?:\s+(?:found|detected|identified|remain(?:ing)?|reported|observed))?)"
    r")\.?$",
    re.IGNORECASE,
)


def _is_non_defect_summary_line(claim_text: str) -> bool:
    cleaned = _strip_inline_formatting((claim_text or "").strip()).strip("(). ")
    if not cleaned:
        return True
    if _NON_DEFECT_CLAIM_RE.match(cleaned):
        return True
    return bool(_BENIGN_ZERO_SUMMARY_RE.match(cleaned))


_BENIGN_ZERO_SUMMARY_RE = re.compile(
    r"^(?:"
    r"(?:(?:summary|findings|issues|defects|blockers|notes|warnings|status|todos?|fixmes?|caveats?|conditions?|known\s+issues?|remaining\s+work|outstanding)\s*[:=—-]\s*)?"
    r"(?:none|n/?a|nil|clean|lgtm|looks\s+good|0|zero|none\s+detected|"
    r"(?:none|no|zero)\s+(?:p0|p1|p2|critical|major|blocking\s+)?"
    r"(?:findings|issues|defects|blockers|regressions|changes\s+requested|concerns|objections|warnings|todos?|fixmes?)"
    r"(?:\s+(?:found|detected|remain(?:ing)?|reported))?)"
    r"|(?:\(?\s*(?:0|zero)\s+p0(?:\s*[,;/|]\s*(?:0|zero)\s+p1"
    r"(?:\s*[,;/|]\s*\d+\s*(?:p2|p3|info|nit)s?"
    r"(?:\s+(?:[A-Za-z-]+\s+){0,4}(?:follow-ups?|notes?|suggestions?|recommendations?|nits?|items?|hardening)"
    r"(?:\s+recommended(?:\s+before\s+or\s+after\s+(?:commit|merge))?)?)?)*"
    r"(?:\s*(?:[,;—-]\s*)?(?:no\s+blockers?|zero\s+blockers?|clean|blockers?\s*[:=]\s*(?:0|none)|"
    r"findings?|issues?|defects?|blockers?|detected|found|remain(?:ing)?|reported))*"
    r")?\s*\)?)"
    r"|(?:p0\s*[:=]\s*0(?:\s*[,;/|]\s*p1\s*[:=]\s*0(?:\s*[,;/|]\s*p2\s*[:=]\s*\d+)?)?)"
    r")\.?$",
    re.IGNORECASE,
)


def _is_benign_post_approval_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if set(stripped) <= {"-", "=", "*"} and len(stripped) >= 3:
        return True
    unquoted = _strip_inline_formatting(_strip_closing_atx_hashes(re.sub(r"^(?:>+\s*)+", "", stripped).strip()))
    if not unquoted:
        return True
    if _APPROVE_TAIL_DISQUALIFY_RE.search(unquoted) or _BARE_NEG_PROSE_RE.search(unquoted):
        return False
    cleaned = re.sub(r"^(?:#+\s*|[-*+]\s*)", "", unquoted).strip("` ")
    if not cleaned:
        return True
    if _LABELED_APPROVE_LINE_RE.match(unquoted) or _STANDALONE_APPROVE_LINE_RE.match(unquoted):
        return _is_unconditional_approve_line(unquoted)
    return bool(_BENIGN_ZERO_SUMMARY_RE.match(cleaned))
