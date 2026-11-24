"""URL and credential redaction helpers for Elenchus challenge logs and packets."""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

from .constants import _INLINE_URL_RE, _SENSITIVE_PARAM_RE


def _redact_url(raw_url: str) -> str:
    try:
        sp = urllib.parse.urlsplit((raw_url or "").strip())
        if not sp.scheme or not sp.netloc:
            return "<redacted-url>"
        host = sp.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port_part = f":{sp.port}" if sp.port else ""
        return urllib.parse.urlunsplit((sp.scheme, f"{host}{port_part}", sp.path or "", "", ""))
    except Exception:
        return "<redacted-url>"


def _extract_url_secrets(raw_url: str) -> list[str]:
    secrets: set[str] = set()
    try:
        sp = urllib.parse.urlsplit((raw_url or "").strip())
        for val in (sp.username, sp.password, sp.fragment):
            if val:
                for cand in (val, urllib.parse.unquote(val)):
                    if len(cand) >= 4:
                        secrets.add(cand)
        if sp.query:
            for k, v in urllib.parse.parse_qsl(sp.query, keep_blank_values=False):
                if v and (len(v) >= 4 or _SENSITIVE_PARAM_RE.search(k or "")):
                    secrets.add(v)
                    unq = urllib.parse.unquote(v)
                    if unq:
                        secrets.add(unq)
    except Exception:
        pass
    for env_k in (
        "KB_ELENCHUS_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_BEDROCK_API_KEY",
        "BEDROCK_API_KEY",
        "GEMINI_API_KEY",
        "GITLAB_TOKEN",
        "GITLAB_PAT",
        "GITHUB_TOKEN",
        "CF_TOKEN",
        "OPENPROJECT_API_TOKEN",
        "JIRA_API_TOKEN",
    ):
        ev = os.environ.get(env_k, "").strip()
        if len(ev) >= 6:
            secrets.add(ev)
    return sorted(secrets, key=len, reverse=True)


def _redact_urls_in_text(text: str) -> str:
    return _INLINE_URL_RE.sub(lambda m: _redact_url(m.group(0)), text or "")


_SECRET_KV_KEY_PAT = (
    r"(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|bearer[_-]?token|access[_-]?key|secret[_-]?key|private[_-]?key)"
    r"(?:[_-][A-Za-z0-9]+)*"
    r"|(?:[A-Za-z0-9]+[_-])*(?:token|secret|password|passwd|pwd|authorization|credentials?)"
)
_KV_QUOTED_OPEN_RE = re.compile(rf"""(?i)((?:\\*['"])?\b(?:{_SECRET_KV_KEY_PAT})\b(?:\\*['"])?\s*[:=]\s*)(\\*)(['"])""")
_INVALID_KEY_QUOTED_OPEN_RE = re.compile(
    r"""(?i)((?:incorrect|invalid)\s+api\s+key(?:\s+provided)?\s*[:=]\s*)(\\*)(['"])"""
)


def _scan_delimited_secret_end(text: str, val_start: int, backslashes: str, quote_ch: str) -> int:
    k = len(backslashes)
    line_end = text.find("\n", val_start)
    if line_end == -1:
        line_end = len(text)
    cr_pos = text.find("\r", val_start, line_end)
    if cr_pos != -1:
        line_end = cr_pos

    is_pow2_minus_1 = k > 0 and ((k + 1) & k) == 0
    mod_step = 2 * (k + 1) if is_pow2_minus_1 else 0
    last_candidate_end = -1
    idx = val_start
    while idx < line_end:
        q_pos = text.find(quote_ch, idx, line_end)
        if q_pos == -1:
            break
        bs = 0
        p = q_pos - 1
        while p >= val_start and text[p] == "\\":
            bs += 1
            p -= 1
        if k == 0:
            last_candidate_end = q_pos + 1
            if bs % 2 == 0:
                return q_pos + 1
        elif is_pow2_minus_1:
            if bs >= k:
                last_candidate_end = q_pos + 1
                if bs % mod_step == k:
                    return q_pos + 1
        elif bs >= k:
            last_candidate_end = q_pos + 1
            if bs == k:
                return q_pos + 1
        idx = q_pos + 1
    return last_candidate_end


def _sub_delimited_secrets(text: str, rx: re.Pattern[str]) -> str:
    out: list[str] = []
    pos = 0
    n = len(text)
    while pos < n:
        m = rx.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        val_start = m.end()
        bs_str = m.group(2)
        q_ch = m.group(3)
        end_pos = _scan_delimited_secret_end(text, val_start, bs_str, q_ch)
        if end_pos != -1 and (end_pos - val_start - len(bs_str) - 1) >= 3:
            out.append(text[pos : m.start()])
            out.append(f"{m.group(1)}{bs_str}{q_ch}<redacted-secret>{bs_str}{q_ch}")
            pos = end_pos
        else:
            out.append(text[pos:val_start])
            pos = val_start
    return "".join(out)


def _redact_sensitive_text(text: str, extra_secrets: list[str] | tuple[str, ...] = ()) -> str:
    out = _redact_urls_in_text(text or "")
    expanded: list[str] = []
    for raw_sec in extra_secrets:
        s = str(raw_sec or "").strip()
        if len(s) >= 4:
            expanded.append(s)
            quoted = urllib.parse.quote(s, safe="")
            if len(quoted) >= 4 and quoted != s:
                expanded.append(quoted)
    for sec in sorted(set(expanded), key=len, reverse=True):
        out = out.replace(sec, "<redacted-secret>")
    out = re.sub(r"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{4,}", r"\1<redacted-secret>", out)
    out = _sub_delimited_secrets(out, _INVALID_KEY_QUOTED_OPEN_RE)
    out = re.sub(
        r"(?i)((?:incorrect|invalid)\s+api\s+key(?:\s+provided)?\s*[:=]\s*)[^\s\"'\\,;}{]+",
        r"\1<redacted-secret>",
        out,
    )
    out = re.sub(
        r"(?i)([?&][^=&#\s\"'<>]*(?:key|token|secret|pass|pwd|auth|sig|cred|bearer)[^=&#\s\"'<>]*=)[^&#\s\"'<>]+",
        r"\1<redacted-secret>",
        out,
    )
    out = _sub_delimited_secrets(out, _KV_QUOTED_OPEN_RE)
    out = re.sub(
        rf"""(?i)((?:\\*['"])?\b(?:{_SECRET_KV_KEY_PAT})\b(?:\\*['"])?\s*[:=]\s*)[^\s"'\\,;}}{{]{{4,}}""",
        r"\1<redacted-secret>",
        out,
    )
    out = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{16,}|glpat-[0-9A-Za-z_-]{8,}|"
        r"gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
        r"(?:AKIA|ASIA)[0-9A-Z]{16})\b",
        "<redacted-secret>",
        out,
    )
    return out


def _scrub_review_dict(rev: dict[str, Any], extra_secrets: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(rev, dict):
        return rev
    secrets = list(extra_secrets) + list(_extract_url_secrets(""))
    for rev_key in ("summary", "raw_text", "model_id"):
        if rev.get(rev_key) is not None:
            rev[rev_key] = _redact_sensitive_text(str(rev[rev_key]), secrets)
    raw_findings = rev.get("findings")
    if isinstance(raw_findings, list):
        for f in raw_findings:
            if isinstance(f, dict):
                for f_key in ("file_path", "symbol", "claim", "evidence"):
                    if f.get(f_key) is not None:
                        f[f_key] = _redact_sensitive_text(str(f[f_key]), secrets)
    return rev
