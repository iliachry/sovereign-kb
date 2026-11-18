"""
Elenchus (`skb elenchus` / `organon elenchus`) — Pre-Seal Cross-Lineage Challenge & Human Adjudication Gate (`F-20`).

Closes the epistemic verification gap between deterministic quality scoring (`gwi`) and cryptographic sealing
(`.evr`): an immutable `.evr` capsule proves *what* was recorded, whereas Elenchus subjects the underlying
worktree, intent, and score-blind evidence to uncorrelated cross-lineage adversarial challenge and explicit
human adjudication before sealing.

Key Invariants:
  1. Uncorrelated Lineage Independence (`N_eff = exp(H) >= 1.80`): strips gateway/region prefixes before
     classifying true model lineage (`anthropic`, `openai`, `qwen`, `google`, `meta`, `deepseek`, `mistral`,
     `amazon`, `deterministic`). Single-lineage or deterministic runs stay in `DEGRADED_SINGLE_LINEAGE` and
     cannot satisfy `--require-elenchus`.
  2. Score-Blind Challenge Packet: strips composite `G(S)`, tier scores, and PASS/FAIL verdicts from the prompt
     by default so challenger models cannot sycophantically anchor on reported scores.
  3. Prompt-Anchoring Calibration vs. True Divergence: computes multi-reviewer 2/3-gram overlap against
     non-code prompt instructions (`PROMPT_ANCHORED_ECHO`) vs. repo-grounded convergence
     (`INDEPENDENT_CONVERGENCE`) vs. cross-lineage splits (`LINEAGE_SPLIT`).
  4. Law L10 Anti-Rubber-Stamp Adjudication & Worktree Binding: rejecting a challenger finding requires an
     explicit human rationale (>= 12 chars); bulk `ALL` override is forbidden when multiple pending clusters
     exist; any source/test or GWI semantic drift immediately invalidates the challenge receipt (`STALE_WORKTREE`).
"""

from __future__ import annotations

import argparse
import base64
import datetime
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from .audit_registry import (
    compute_subproject_fingerprint,
    find_git_root,
    is_bundled_starter_vault_path,
    record_triad_audit,
)

ELENCHUS_PROTOCOL_VERSION = "1.0.0"
MIN_INDEPENDENT_N_EFF = 1.80
ANCHOR_ECHO_THRESHOLD = 0.65
MIN_REJECTION_REASON_CHARS = 12
DEFAULT_MAX_DIFF_CHARS = 24000
DEFAULT_GWI_PASS_THRESHOLD = 9.0

_VALID_SEVERITIES = ("P0", "P1", "P2", "INFO")
_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "INFO": 3}
_VALID_CATEGORIES = ("correctness", "security", "test_gap", "spec_drift", "performance", "provenance")
_VALID_VERDICTS = ("APPROVE", "CHALLENGE", "VETO")
_VERDICT_SCORE = {"APPROVE": 0.0, "CHALLENGE": 0.5, "VETO": 1.0}
_VERIFIED_LLM_LINEAGES = frozenset({"anthropic", "openai", "qwen", "google", "meta", "deepseek", "mistral", "amazon"})
_VALID_EXECUTION_MODES = frozenset({"explicit", "reviews_file", "http", "oc_triad", "hybrid", "offline", "auto"})
_RECEIPT_BASENAMES = frozenset({"goldwood.json", "gate_receipt.json", "elenchus_challenge.json", "passport.yaml"})
_VOLATILE_GWI_KEYS = frozenset(
    {"generatedAt", "generated_at", "audit_provenance", "evidence_file", "evr_capsule", "elenchus_gate"}
)
_VOLATILE_GATE_RECEIPT_KEYS = frozenset(
    {"executed_at", "timestamp_epoch", "duration_ms", "output_tail", "cached", "reused_recent_receipt", "age_seconds"}
)
_INLINE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_PARAM_RE = re.compile(r"(?:key|token|secret|pass|pwd|auth|sig|cred|bearer)", re.IGNORECASE)
_LABELED_APPROVE_LINE_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*+]\s*)?\*{0,2}(?:DISPOSITION|VERDICT|DECISION|STATUS|RESULT)\*{0,2}\s*"
    r"[:=—-]\*{0,2}\s*\*{0,2}APPROVED?\*{0,2}(?:\s*(?:—|--|-|:|\(|,|;|\.|!)\s*(.*))?$",
    re.IGNORECASE,
)
_STANDALONE_APPROVE_LINE_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*+]\s*)?\*{0,2}APPROVED?\*{0,2}(?:\s*(?:—|--|-|:|\(|,|;|\.|!)\s*(.*))?$",
    re.IGNORECASE,
)
_APPROVE_TAIL_DISQUALIFY_RE = re.compile(
    r"\b(?:if|when|unless|until|whether|but|however|except|although|though|incomplete|skipped|pending|"
    r"fail|failed|failing|blocked|conditional|assuming|provided|never|cannot|can't|not\s+approved|"
    r"would|should|could|subject|contingent|dependent|provisional|tentative|partial|only|fixing|"
    r"untested|unverified|require|requires|required|need|needs|must|awaiting|wait|waiting|"
    r"do\s+not\s+(?:merge|seal|ship|approve)|once)\b",
    re.IGNORECASE,
)

_GATEWAY_PREFIXES = (
    "amazon-bedrock/",
    "bedrock/",
    "openrouter/",
    "vertex_ai/",
    "vertex/",
    "azure/",
    "together/",
    "fireworks/",
    "groq/",
    "litellm/",
    "ollama/",
    "vllm/",
    "sglang/",
    "huggingface/",
    "hf/",
)
_REGION_PREFIXES = ("global.", "us.", "eu.", "ap.", "apac.", "emea.")

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "have",
        "has",
        "had",
        "was",
        "were",
        "are",
        "not",
        "but",
        "can",
        "will",
        "should",
        "could",
        "would",
        "may",
        "might",
        "about",
        "when",
        "where",
        "what",
        "which",
        "there",
        "their",
        "they",
        "them",
        "then",
        "than",
        "also",
        "only",
        "before",
        "after",
        "more",
        "most",
        "some",
        "such",
        "each",
        "both",
        "all",
        "any",
        "none",
        "does",
        "did",
        "been",
        "being",
        "its",
        "our",
        "your",
        "use",
        "used",
        "using",
        "without",
        "within",
        "verify",
        "check",
        "ensure",
        "code",
        "file",
    }
)

_SCORE_BLIND_DROP_KEYS = frozenset(
    {
        "goldwood_g",
        "coupled_goldwood_g",
        "raw_goldwood_g",
        "unmasked_goldwood_g",
        "linear_score",
        "barrier_product",
        "min_tier_score",
        "compositeScoreG",
        "linearScoreG",
        "barrierProductPsi",
        "minTierScore",
        "sovereignGateG",
        "status",
        "verdict",
        "tiers",
        "dimensions",
        "calculation",
        "craft_lenses",
        "craftLenses",
        "notes",
        "jacobianDgDs",
        "headroomGainDg",
        "highestLeverageTier",
    }
)

_FINDING_PATH_RE = re.compile(
    r"`?([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:py|ts|tsx|js|jsx|rs|go|sh|md|toml|json|yaml|yml))(?::(\d+))?`?"
)
_PROSE_SEV_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `**P1**: desc`, `### **P1 (cat)**: desc`, `- __[P1]__ (cat) — desc`
    re.compile(
        r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?(?:\*{1,3}|_{1,3})\[?(P0|P1|P2|INFO)\]?(?:\s*\([^)]*\))?(?:\*{1,3}|_{1,3})(?:\s*\([^)]*\))?\s*(?::|—|--|-)?\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    # `**P1 — desc**`, `**P1 — title** in `file:line``, `**P1 (security): desc**`, `**[P1] — desc**`
    re.compile(
        r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?(?:\*{1,3}|_{1,3})\[?(P0|P1|P2|INFO)\]?(?:\s*\([^)]*\))?\s*(?::|—|--|-)\s*(.+?)(?:\*{1,3}|_{1,3})(?:\s+(.+))?\s*$",
        re.IGNORECASE,
    ),
    # `- [P1] desc`, `### [P1] (cat): desc`
    re.compile(
        r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?\[\s*(P0|P1|P2|INFO)(?:\s*(?:\([^)\]\n]*\)|[:/-]\s*[A-Za-z0-9_ -]{1,32}))?\s*\](?:\s*\([^)]*\))?\s*(?::|—|--|-)?\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    # `- P1: desc`, `### P1 — desc`, `1) P1 (cat) — desc`, `P1 (cat) desc`
    re.compile(
        r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?(P0|P1|P2|INFO)"
        r"(?:\s*\([^)\]\n]+\)\s*(?:(?::|—|--|-\s)\s*|\s+(?!(?:is|are|was|were|has\s+been|have\s+been)\s+(?:fixed|resolved|closed|addressed|remediated|verified)\b))"
        r"|\s*(?::|—|--|-\s)\s*)(.+?)\s*$",
        re.IGNORECASE,
    ),
)
_UNSAFE_DIFF_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("eval_call", re.compile(r"\beval\s*\(")),
    ("exec_call", re.compile(r"\bexec\s*\(")),
    ("shell_true", re.compile(r"\bshell\s*=\s*True\b")),
    ("pickle_loads", re.compile(r"\bpickle\.loads?\s*\(")),
)


_TABLE_CELL_SEV_RE = re.compile(
    r"^\[?\s*(P0|P1|P2|INFO)(?!\s*[/|—-]\s*(?:P[0-3]|INFO)\b)(?:\s*\([^)\]\n]{1,80}\)|\s*/\s*[A-Za-z_]{2,24})?\s*\]?$",
    re.IGNORECASE,
)
_TABLE_DEFECT_RESOLVED_RE = re.compile(
    r"^(?:[✅✓☑]+\s*)?(?:closed|fixed|resolved|verified\s+(?:closed|fixed|resolved|clean))\.?$",
    re.IGNORECASE,
)
_TABLE_AUDIT_PASS_RE = re.compile(r"^(?:[✅✓☑]+\s*)?pass(?:ed)?\.?$", re.IGNORECASE)
_TABLE_CHECKMARK_ONLY_RE = re.compile(r"^[✅✓☑]+\.?$")
_TABLE_HEADER_LABELS = frozenset(
    {
        "#",
        "id",
        "no",
        "index",
        "file",
        "path",
        "location",
        "line",
        "lines",
        "severity",
        "sev",
        "priority",
        "level",
        "category",
        "type",
        "claim",
        "description",
        "finding",
        "findings",
        "issue",
        "issues",
        "summary",
        "details",
        "status",
        "state",
        "result",
        "verdict",
        "notes",
        "evidence",
        "recommendation",
        "impact",
        "reproduced",
        "confirmed",
        "symbol",
        "component",
        "components",
        "module",
        "modules",
        "area",
        "areas",
        "scope",
        "target",
        "layer",
        "service",
        "package",
        "domain",
        "section",
        "topic",
        "feature",
        "check",
        "checks",
        "gate",
        "gates",
        "metric",
        "rule",
        "test",
        "tests",
        "stage",
    }
)
_TABLE_STATUS_HEADER_COLS = frozenset({"status", "state", "result", "verdict", "outcome", "resolution", "disposition"})
_TABLE_SEV_HEADER_COLS = frozenset({"severity", "sev", "priority", "level"})
_TABLE_CLAIM_DATA_HEADER_COLS = frozenset(
    {"claim", "description", "finding", "findings", "issue", "issues", "file", "path", "location", "symbol", "evidence"}
)
_HDR_P0_COUNT_RE = re.compile(
    r"^(?:(?:open|active|unresolved|remaining|new)\s+)?p0s?"
    r"(?:\s*(?:count|findings?|issues?|blockers?|defects?|items?|\([^)\n]{1,32}\)))?$",
    re.IGNORECASE,
)
_HDR_P1_COUNT_RE = re.compile(
    r"^(?:(?:(?:open|active|unresolved|remaining|new)\s+)?p1s?"
    r"(?:\s*(?:count|findings?|issues?|blockers?|defects?|items?|\([^)\n]{1,32}\)))?"
    r"|(?:(?:open|active|unresolved|remaining)\s+)?p0\s*[/+&|-]\s*p1"
    r"(?:\s*(?:count|findings?|issues?|blockers?|defects?|\([^)\n]{1,32}\)))?"
    r"|(?:(?:open|active|unresolved|remaining)\s+)?blockers?(?:\s*count)?)$",
    re.IGNORECASE,
)
_WORD_SEV_COUNT_MAP = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
_POS_SEV_COUNT_CELL_RE = re.compile(
    r"^(?:\+?([1-9]\d{0,5})\.?|(one|two|three|four|five))\+?"
    r"(?:\s*(?:open|unresolved|blocking|found|remaining|issues?|findings?|blockers?|defects?|\([^)]*\)))?$",
    re.IGNORECASE,
)
_GFM_DELIM_ROW_RE = re.compile(r"^\s*\|?(?:\s*:?-{1,}:?\s*\|)+\s*:?-{1,}:?\s*\|?\s*$")
_TAG_ONLY_SEV_LINE_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?\[?\s*(P0|P1|P2|INFO)(?:\s*(\([^)\]\n]{1,120}\)))?\s*\]?\s*[:—-]?\s*$",
    re.IGNORECASE,
)


def _split_gfm_table_cells(raw_row: str) -> list[str]:
    s = raw_row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: list[str] = []
    cur: list[str] = []
    i = 0
    n = len(s)
    in_ticks = 0
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n and in_ticks == 0:
            nxt = s[i + 1]
            if nxt == "|":
                cur.append("|")
                i += 2
                continue
            cur.append(ch)
            cur.append(nxt)
            i += 2
            continue
        if ch == "`":
            j = i
            while j < n and s[j] == "`":
                j += 1
            run = j - i
            if in_ticks == 0:
                in_ticks = run
            elif run == in_ticks:
                in_ticks = 0
            cur.append(s[i:j])
            i = j
            continue
        if ch == "|" and in_ticks == 0:
            cells.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _match_inline_prose_sev(cell_raw: str) -> tuple[str, str] | None:
    c_trim = cell_raw.strip()
    if not c_trim or c_trim.startswith(("`", '"', "'")):
        return None
    cand_fmt = _strip_inline_formatting(c_trim)
    if _TAG_ONLY_SEV_LINE_RE.match(cand_fmt):
        return None
    for candidate in (c_trim, cand_fmt):
        for pat in _PROSE_SEV_PATTERNS:
            m = pat.match(candidate)
            if m:
                sev = m.group(1).upper()
                body = m.group(2).strip()
                if m.lastindex and m.lastindex >= 3 and m.group(3):
                    body = f"{body} {m.group(3).strip()}".strip()
                body = _strip_inline_formatting(body).strip()
                if body and re.search(r"[A-Za-z0-9]", body):
                    return sev, body
    return None


def _unwrap_table_code_and_fmt(cell_text: str) -> str:
    s = re.sub(r"</?code\b[^>\n]{0,80}>", "", cell_text or "", flags=re.IGNORECASE)
    s = re.sub(r"(`+)([^`\n]+)\1", r"\2", s)
    return _strip_inline_formatting(s).strip()


def _parse_table_row_finding(
    raw_ln: str,
    allow_pipeless: bool = False,
    header_cols: list[str] | tuple[str, ...] = (),
) -> tuple[str, str] | None:
    s = (raw_ln or "").strip()
    if not s or _GFM_DELIM_ROW_RE.match(s):
        return None
    if not s.startswith("|"):
        if not (allow_pipeless and "|" in s):
            return None
    elif s.count("|") < 2:
        return None
    raw_cells = _split_gfm_table_cells(s)
    cells = [_strip_inline_formatting(c).strip() for c in raw_cells]
    non_empty = [(idx, c) for idx, c in enumerate(cells) if c]
    if not non_empty:
        return None
    if all(set(c) <= {"-", ":", " "} for _, c in non_empty):
        return None
    norm_headers = [_unwrap_table_code_and_fmt(h).lower().rstrip(":") for h in header_cols]
    sev_match: str | None = None
    sev_pos = -1
    inline_claim = ""
    for idx, c in non_empty:
        cand_sev = (
            _unwrap_table_code_and_fmt(raw_cells[idx])
            if idx < len(norm_headers) and norm_headers[idx] in _TABLE_SEV_HEADER_COLS
            else c
        )
        m = _TABLE_CELL_SEV_RE.match(cand_sev)
        if m:
            sev_match = m.group(1).upper()
            sev_pos = idx
            break
    if sev_match is None:
        for idx, _ in non_empty:
            mi = _match_inline_prose_sev(raw_cells[idx])
            if mi is not None:
                sev_match, inline_claim = mi
                sev_pos = idx
                break
    if norm_headers and sev_match != "P0":
        found_cnt_hdr = False
        for h_re, sev_lbl in ((_HDR_P0_COUNT_RE, "P0"), (_HDR_P1_COUNT_RE, "P1")):
            if sev_lbl == "P1" and sev_match in ("P0", "P1"):
                continue
            for idx, _ in non_empty:
                if idx < len(norm_headers) and h_re.match(norm_headers[idx]):
                    mc = _POS_SEV_COUNT_CELL_RE.match(_unwrap_table_code_and_fmt(raw_cells[idx]))
                    if mc:
                        sev_match = sev_lbl
                        sev_pos = idx
                        cnt_val = mc.group(1) or _WORD_SEV_COUNT_MAP.get((mc.group(2) or "").lower(), "1")
                        inline_claim = f"{sev_lbl} count={cnt_val}"
                        found_cnt_hdr = True
                        break
            if found_cnt_hdr:
                break
    if sev_match is None:
        return None
    other_pairs = [
        (idx, c)
        for idx, c in non_empty
        if idx != sev_pos and not (idx == 0 and sev_pos > 0 and re.match(r"^#?\d+$", _unwrap_table_code_and_fmt(c)))
    ]
    if not other_pairs:
        if inline_claim and not _is_non_defect_summary_line(inline_claim):
            return sev_match, inline_claim
        return None
    other_vals = [c for _, c in other_pairs]
    _sev_hdr_tokens = {"p0", "p1", "p2", "p3", "info", "p0/p1"}
    if (
        not inline_claim
        and any(_unwrap_table_code_and_fmt(c).lower().rstrip(":") in _TABLE_HEADER_LABELS for c in other_vals)
        and all(
            _unwrap_table_code_and_fmt(c).lower().rstrip(":") in (_TABLE_HEADER_LABELS | _sev_hdr_tokens)
            for c in other_vals
        )
    ):
        return None
    if not inline_claim and len(other_vals) == 1 and _TABLE_CHECKMARK_ONLY_RE.match(other_vals[0]):
        return None
    if norm_headers:
        is_defect_row = bool(inline_claim) or (
            bool(set(norm_headers) & _TABLE_SEV_HEADER_COLS) and bool(set(norm_headers) & _TABLE_CLAIM_DATA_HEADER_COLS)
        )
        if any(
            idx < len(norm_headers)
            and norm_headers[idx] in _TABLE_STATUS_HEADER_COLS
            and (
                _TABLE_DEFECT_RESOLVED_RE.match(_unwrap_table_code_and_fmt(raw_cells[idx]))
                or (not is_defect_row and _TABLE_AUDIT_PASS_RE.match(_unwrap_table_code_and_fmt(raw_cells[idx])))
            )
            for idx, _ in other_pairs
        ):
            return None
    else:
        if not inline_claim and all(
            _TABLE_DEFECT_RESOLVED_RE.match(c)
            or _TABLE_AUDIT_PASS_RE.match(c)
            or _TABLE_CHECKMARK_ONLY_RE.match(c)
            or _is_non_defect_summary_line(c)
            for c in other_vals
        ):
            return None
        if inline_claim and all(_TABLE_DEFECT_RESOLVED_RE.match(c) for c in other_vals):
            return None
    if not inline_claim and all(_is_non_defect_summary_line(c) for c in other_vals):
        return None
    if (
        inline_claim
        and _is_non_defect_summary_line(inline_claim)
        and all(_is_non_defect_summary_line(c) for c in other_vals)
    ):
        return None
    merged: list[str] = []
    if inline_claim:
        merged.append(inline_claim)
    k = 0
    while k < len(other_pairs):
        idx_k, val_k = other_pairs[k]
        if (
            k + 1 < len(other_pairs)
            and other_pairs[k + 1][0] == idx_k + 1
            and _FINDING_PATH_RE.fullmatch(val_k)
            and re.fullmatch(r"[Ll]?\d{1,7}", other_pairs[k + 1][1])
        ):
            merged.append(f"{val_k.strip('`')}:{other_pairs[k + 1][1].lstrip('Ll')}")
            k += 2
        else:
            merged.append(val_k)
            k += 1
    body = " — ".join(merged).strip()
    if not body or not re.search(r"[A-Za-z0-9]", body):
        return None
    return sev_match, body


def _parse_prose_finding_line(raw_ln: str) -> tuple[str, str] | None:
    ln_raw = _strip_closing_atx_hashes((raw_ln or "").strip())
    if not ln_raw:
        return None
    if not ln_raw.startswith("|"):
        mi = _match_inline_prose_sev(ln_raw)
        if mi is not None:
            return mi
    return _parse_table_row_finding(raw_ln, allow_pipeless=False)


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


def classify_model_lineage(model_id: str) -> str:
    """
    Map a provider-qualified model identifier to its uncorrelated training lineage.
    Strips cloud gateway (`amazon-bedrock/`, `openrouter/`, ...) and region (`global.`, `us.`, `eu.`)
    routing prefixes BEFORE matching family tokens so Bedrock-hosted Anthropic/OpenAI models never
    collapse into `"amazon"`.
    """
    raw = (model_id or "").strip().lower()
    if not raw:
        return "unknown"

    core = raw
    changed = True
    while changed:
        changed = False
        for pfx in _GATEWAY_PREFIXES:
            if core.startswith(pfx):
                core = core[len(pfx) :]
                changed = True
                break
        for rpfx in _REGION_PREFIXES:
            if core.startswith(rpfx):
                core = core[len(rpfx) :]
                changed = True
                break

    if any(tok in core for tok in ("kb-gwi", "skb-", "elenchus-local", "deterministic", "heuristic")):
        return "deterministic"
    if core.startswith(("anthropic.", "anthropic/")) or any(
        tok in core for tok in ("claude", "fable", "sonnet", "opus", "haiku")
    ):
        return "anthropic"
    if core.startswith(("openai.", "openai/")) or re.search(
        r"(?:^|[./_-])(?:gpt-\d|gpt\d|o1(?:[-.]|$)|o3(?:[-.]|$)|o4(?:[-.]|$)|astra)",
        core,
    ):
        return "openai"
    if "qwen" in core:
        return "qwen"
    if any(tok in core for tok in ("gemini", "gemma", "google.")):
        return "google"
    if any(tok in core for tok in ("llama", "meta.")):
        return "meta"
    if "deepseek" in core:
        return "deepseek"
    if any(tok in core for tok in ("mistral", "mixtral", "codestral", "pixtral")):
        return "mistral"
    if any(tok in core for tok in ("nova", "titan", "amazon.")):
        return "amazon"

    head = core.split("/")[0].split(".")[0].split(":")[0]
    sanitized = re.sub(r"[^a-z0-9_-]+", "-", head).strip("-")
    return f"other:{sanitized}" if sanitized else "unknown"


def compute_effective_panel_size(
    models: list[str | dict[str, Any]],
    failed_models: set[str] | list[str] | None = None,
) -> dict[str, Any]:
    """
    Compute Shannon-Hill effective independent lineage count `N_eff = exp(H)` over verified,
    successfully executed LLM reviewers. Single-lineage, unverified-alias, failed, or
    deterministic panels fail closed (`gate_closable = False`).
    """
    failed_set = {str(x).strip() for x in (failed_models or []) if str(x).strip()}
    classified: list[dict[str, str]] = []
    llm_lineages: list[str] = []
    det_count = 0
    unverified_count = 0
    failed_count = 0

    for item in models:
        if isinstance(item, dict):
            m_str = str(item.get("model_id") or item.get("model") or "").strip()
            exec_st = str(item.get("execution_status") or "OK").strip().upper()
        else:
            m_str = str(item or "").strip()
            exec_st = "FAILED" if m_str in failed_set else "OK"
        if not m_str:
            continue
        raw_lin = classify_model_lineage(m_str)
        if exec_st == "FAILED":
            failed_count += 1
            classified.append(
                {
                    "model_id": m_str,
                    "lineage": raw_lin if raw_lin in _VERIFIED_LLM_LINEAGES else "unverified",
                    "raw_lineage": raw_lin,
                    "execution_status": "FAILED",
                }
            )
            continue
        if raw_lin == "deterministic":
            det_count += 1
            classified.append(
                {
                    "model_id": m_str,
                    "lineage": "deterministic",
                    "raw_lineage": raw_lin,
                    "execution_status": "OK",
                }
            )
        elif raw_lin in _VERIFIED_LLM_LINEAGES:
            llm_lineages.append(raw_lin)
            classified.append(
                {
                    "model_id": m_str,
                    "lineage": raw_lin,
                    "raw_lineage": raw_lin,
                    "execution_status": "OK",
                }
            )
        else:
            unverified_count += 1
            classified.append(
                {
                    "model_id": m_str,
                    "lineage": "unverified",
                    "raw_lineage": raw_lin,
                    "execution_status": "OK",
                }
            )

    m_llm = len(llm_lineages)
    counts = Counter(llm_lineages)
    if m_llm == 0:
        entropy = 0.0
        n_eff = 1.0
    else:
        entropy = -sum((c / m_llm) * math.log(c / m_llm) for c in counts.values() if c > 0)
        n_eff = math.exp(entropy)

    n_eff_round = round(n_eff, 4)
    independent_panel = m_llm >= 2 and n_eff >= MIN_INDEPENDENT_N_EFF
    panel_mode = "CROSS_LINEAGE_INDEPENDENT" if independent_panel else "DEGRADED_SINGLE_LINEAGE"
    if independent_panel:
        warning = ""
    elif failed_count or unverified_count:
        warning = (
            f"DEGRADED NON-INDEPENDENT SANITY — verified LLM lineages={sorted(counts.keys())}, "
            f"failed={failed_count}, unverified={unverified_count}; advisory only, cannot close --require-elenchus gate"
        )
    else:
        warning = "DEGRADED NON-INDEPENDENT SANITY — single or deterministic lineage; advisory only, cannot close --require-elenchus gate"

    return {
        "models": classified,
        "total_reviewers": len(classified),
        "llm_reviewer_count": m_llm,
        "deterministic_reviewer_count": det_count,
        "unverified_reviewer_count": unverified_count,
        "failed_reviewer_count": failed_count,
        "lineage_counts": dict(sorted(counts.items())),
        "distinct_llm_lineages": sorted(counts.keys()),
        "shannon_entropy": round(entropy, 4),
        "n_eff": n_eff_round,
        "min_independent_n_eff": MIN_INDEPENDENT_N_EFF,
        "independent_panel": independent_panel,
        "panel_mode": panel_mode,
        "gate_closable": bool(independent_panel),
        "warning": warning,
    }


def _run_git_cmd(root: Path, *args: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.decode("utf-8", errors="surrogateescape")
    except Exception:
        return 1, ""


def _normalize_git_rel_path(rel_path: str) -> str:
    """Strip leading `./` and `/` without mangling trailing newlines or POSIX backslashes."""
    s = rel_path or ""
    if os.name == "nt":
        s = s.replace("\\", "/")
    while s.startswith("./") or s.startswith("/"):
        s = s[2:] if s.startswith("./") else s.lstrip("/")
    return s


def _strip_dot_slash(rel_path: str) -> str:
    s = (rel_path or "").rstrip("\r\n").replace("\\", "/")
    while s.startswith("./") or s.startswith("/"):
        s = s[2:] if s.startswith("./") else s.lstrip("/")
    return s


def _is_skipped_evidence_path(rel_posix: str) -> bool:
    norm = _normalize_git_rel_path(rel_posix)
    parts = Path(norm).parts
    if any(part in (".git", "__pycache__", ".pytest_cache") for part in parts):
        return True
    name = parts[-1] if parts else ""
    if "evidence" in parts and (name in _RECEIPT_BASENAMES or name.endswith(".evr")):
        return True
    return False


def _walk_plain_dir_files(base_root: Path) -> set[str]:
    paths: set[str] = set()
    for cur_dir, dirnames, filenames in os.walk(base_root, topdown=True, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__", ".pytest_cache", "node_modules"))
        base = Path(cur_dir)
        for name in sorted(filenames):
            p_cur = base / name
            rel_posix = _normalize_git_rel_path(p_cur.relative_to(base_root).as_posix())
            if rel_posix and not _is_skipped_evidence_path(rel_posix):
                paths.add(rel_posix)
    return paths


def _hash_plain_directory_tree(dir_path: Path) -> str:
    hasher = hashlib.sha256()
    for rel_posix in sorted(_walk_plain_dir_files(dir_path)):
        p_tr = dir_path / rel_posix
        rel_b = rel_posix.encode("utf-8", errors="surrogateescape")
        lstat = p_tr.lstat()
        if stat.S_ISLNK(lstat.st_mode):
            payload = os.readlink(p_tr).encode("utf-8", errors="surrogateescape")
            hasher.update(b"L")
            hasher.update(struct.pack(">IIQ", 0o120000, len(rel_b), len(payload)))
            hasher.update(rel_b)
            hasher.update(payload)
        elif stat.S_ISREG(lstat.st_mode):
            with p_tr.open("rb") as fh:
                fstat = os.fstat(fh.fileno())
                mode = 0o755 if (fstat.st_mode & 0o111) else 0o644
                hasher.update(b"F")
                hasher.update(struct.pack(">IIQ", mode, len(rel_b), int(fstat.st_size)))
                hasher.update(rel_b)
                total = 0
                for chunk in iter(lambda: fh.read(65536), b""):
                    total += len(chunk)
                    hasher.update(chunk)
                if total != int(fstat.st_size):
                    raise OSError(f"File size changed while hashing `{p_tr}`")
    return hasher.hexdigest()[:32]


def _compute_worktree_content_sha(target_dir: Path, _visited: frozenset[Path] = frozenset()) -> str:
    """
    Compute a length-delimited SHA-256 manifest digest over tracked + untracked-nonignored files
    and nested gitlink/submodule worktrees (preserving leading dotfiles, encoding entry type,
    git exec-bit mode, path length/bytes, and streaming file/symlink/submodule payloads; failing loudly on OSError).
    """
    root = target_dir if target_dir.is_dir() else target_dir.parent
    resolved_root = root.resolve()
    if resolved_root in _visited:
        return "cycle"
    next_visited = _visited | {resolved_root}

    hasher = hashlib.sha256()
    rel_paths: set[str] = set()

    rc, ls_out = _run_git_cmd(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if rc == 0:
        for rel_s in ls_out.split("\0"):
            if not rel_s:
                continue
            rel_posix = _normalize_git_rel_path(rel_s)
            if rel_posix and not _is_skipped_evidence_path(rel_posix):
                rel_paths.add(rel_posix)
    else:
        rel_paths = _walk_plain_dir_files(root)

    for rel_posix in sorted(rel_paths):
        p_tr = root / rel_posix
        rel_b = rel_posix.encode("utf-8", errors="surrogateescape")
        try:
            lstat = p_tr.lstat()
        except FileNotFoundError:
            hasher.update(b"D")
            hasher.update(struct.pack(">IIQ", 0, len(rel_b), 0))
            hasher.update(rel_b)
            continue

        if stat.S_ISLNK(lstat.st_mode):
            payload = os.readlink(p_tr).encode("utf-8", errors="surrogateescape")
            hasher.update(b"L")
            hasher.update(struct.pack(">IIQ", 0o120000, len(rel_b), len(payload)))
            hasher.update(rel_b)
            hasher.update(payload)
        elif stat.S_ISREG(lstat.st_mode):
            with p_tr.open("rb") as fh:
                fstat = os.fstat(fh.fileno())
                mode = 0o755 if (fstat.st_mode & 0o111) else 0o644
                hasher.update(b"F")
                hasher.update(struct.pack(">IIQ", mode, len(rel_b), int(fstat.st_size)))
                hasher.update(rel_b)
                total = 0
                for chunk in iter(lambda: fh.read(65536), b""):
                    total += len(chunk)
                    hasher.update(chunk)
                if total != int(fstat.st_size):
                    raise OSError(f"File size changed while hashing `{p_tr}`")
        elif stat.S_ISDIR(lstat.st_mode):
            if (p_tr / ".git").exists():
                rc_sh, sub_head_out = _run_git_cmd(p_tr, "rev-parse", "--verify", "--quiet", "HEAD")
                sub_head = sub_head_out.strip() if rc_sh == 0 and sub_head_out.strip() else "unborn"
                sub_wt = _compute_worktree_content_sha(p_tr, _visited=next_visited)
            else:
                sub_head = "no-git"
                sub_wt = _hash_plain_directory_tree(p_tr)
            payload = f"head={sub_head}|wt={sub_wt}".encode("utf-8")
            hasher.update(b"G")
            hasher.update(struct.pack(">IIQ", 0o160000, len(rel_b), len(payload)))
            hasher.update(rel_b)
            hasher.update(payload)
        else:
            mode = stat.S_IMODE(lstat.st_mode)
            hasher.update(b"M")
            hasher.update(struct.pack(">IIQ", mode, len(rel_b), 0))
            hasher.update(rel_b)

    return hasher.hexdigest()[:32]


def _is_real_num(val: Any) -> bool:
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return False
    try:
        return math.isfinite(float(val))
    except (TypeError, ValueError, OverflowError):
        return False


def _detect_gwi_schema_contradiction(obj: dict[str, Any]) -> str | None:
    if not isinstance(obj, dict):
        return "Malformed GWI object: expected dict."
    if "calculation" in obj and obj["calculation"] is not None and not isinstance(obj["calculation"], dict):
        return "Malformed GWI object: `calculation` must be a dict."
    calc = obj.get("calculation") if isinstance(obj.get("calculation"), dict) else {}
    p_top = str(obj.get("project") or "").strip()
    p_rep = str(obj.get("project_name") or "").strip()
    if p_top and p_rep and p_top != p_rep:
        return f"Contradictory GWI project fields: project={p_top!r} vs project_name={p_rep!r}."
    s_top = str(obj.get("status") or "").strip().upper()
    s_calc = str(calc.get("status") or "").strip().upper()
    if s_top and s_calc and s_top != s_calc:
        return f"Contradictory GWI status fields: status={s_top!r} vs calculation.status={s_calc!r}."

    has_g_top = "compositeScoreG" in obj and obj["compositeScoreG"] is not None
    has_g_calc = "goldwood_g" in calc and calc["goldwood_g"] is not None
    has_g_root = "goldwood_g" in obj and obj["goldwood_g"] is not None
    if has_g_top and not _is_real_num(obj["compositeScoreG"]):
        return f"Malformed GWI `compositeScoreG`: {obj['compositeScoreG']!r} is not a finite number."
    if has_g_calc and not _is_real_num(calc["goldwood_g"]):
        return f"Malformed GWI `calculation.goldwood_g`: {calc['goldwood_g']!r} is not a finite number."
    if has_g_root and not _is_real_num(obj["goldwood_g"]):
        return f"Malformed GWI `goldwood_g`: {obj['goldwood_g']!r} is not a finite number."
    score_pairs = [
        (name, float(val))
        for name, val in (
            ("compositeScoreG", obj.get("compositeScoreG")),
            ("calculation.goldwood_g", calc.get("goldwood_g")),
            ("goldwood_g", obj.get("goldwood_g")),
        )
        if _is_real_num(val)
    ]
    for idx_s in range(len(score_pairs)):
        for jdx_s in range(idx_s + 1, len(score_pairs)):
            n1, v1 = score_pairs[idx_s]
            n2, v2 = score_pairs[jdx_s]
            if abs(v1 - v2) > 1e-3:
                return f"Contradictory GWI score fields: {n1}={v1} vs {n2}={v2}."

    for cl_key in ("craft_lenses", "craftLenses"):
        if cl_key in obj and obj[cl_key] is not None and not isinstance(obj[cl_key], dict):
            return f"Malformed GWI `{cl_key}`: expected dict."

    cl1 = obj.get("craft_lenses") if isinstance(obj.get("craft_lenses"), dict) else None
    cl2 = obj.get("craftLenses") if isinstance(obj.get("craftLenses"), dict) else None

    eff_status = s_calc or s_top
    if eff_status not in {"PASS", "REVISE", "FAIL", "WARN", "VETO"}:
        return f"Invalid or missing GWI status: {eff_status!r}."

    v_top = str(obj.get("verdict") or "").strip().upper()
    v_calc = str(calc.get("verdict") or "").strip().upper()
    if v_top and v_calc and v_top != v_calc:
        return f"Contradictory GWI verdict fields: verdict={v_top!r} vs calculation.verdict={v_calc!r}."
    eff_verdict = v_calc or v_top
    if eff_status == "PASS" and eff_verdict in {"FAIL", "FAILED", "REJECT", "REJECTED", "VETO", "REVISE", "CHALLENGE"}:
        return f"Contradictory GWI report: status is 'PASS' while verdict={eff_verdict!r}."

    raw_g = calc.get("goldwood_g") if has_g_calc else obj.get("compositeScoreG", obj.get("goldwood_g"))
    if not _is_real_num(raw_g):
        return "GWI object lacks a valid finite numeric score (`goldwood_g` / `compositeScoreG`)."
    g_val = float(raw_g)
    if g_val < 0.0 or g_val > 10.0 + 1e-6:
        return f"GWI score {g_val!r} is outside valid bounds [0.0, 10.0]."

    def _inspect_craft_lens_alias(cl_label: str, cl_d: dict[str, Any]) -> tuple[str | None, float | None, bool | None]:
        veto_keys = ("veto_triggered", "vetoTriggered", "verga_veto_triggered", "chrysos_veto_triggered")
        seen_true = False
        seen_explicit_false = False
        for vk in veto_keys:
            if vk in cl_d and cl_d[vk] is not None:
                if not isinstance(cl_d[vk], bool):
                    return f"Malformed GWI `{cl_label}.{vk}`: {cl_d[vk]!r}.", None, None
                if cl_d[vk] is True:
                    seen_true = True
                    if eff_status == "PASS":
                        return f"Contradictory GWI report: status is 'PASS' while {cl_label}.{vk}=True.", None, None
        if (
            isinstance(cl_d.get("veto_triggered"), bool)
            and isinstance(cl_d.get("vetoTriggered"), bool)
            and cl_d["veto_triggered"] != cl_d["vetoTriggered"]
        ):
            return f"Contradictory `{cl_label}` veto fields: veto_triggered vs vetoTriggered.", None, None
        if cl_d.get("veto_triggered") is False and (
            cl_d.get("verga_veto_triggered") is True or cl_d.get("chrysos_veto_triggered") is True
        ):
            return f"Contradictory `{cl_label}` veto fields: overall False while pole veto is True.", None, None
        if cl_d.get("veto_triggered") is False or cl_d.get("vetoTriggered") is False:
            seen_explicit_false = True

        lenses_raw = cl_d.get("lenses")
        if lenses_raw is not None:
            if not isinstance(lenses_raw, list) or not all(isinstance(lr, dict) for lr in lenses_raw):
                return f"Malformed GWI `{cl_label}.lenses`: expected list of objects.", None, None
            for lr in lenses_raw:
                if "veto_triggered" in lr and lr["veto_triggered"] is not None:
                    if not isinstance(lr["veto_triggered"], bool):
                        return (
                            f"Malformed GWI `{cl_label}.lenses[].veto_triggered`: {lr['veto_triggered']!r}.",
                            None,
                            None,
                        )
                    if lr["veto_triggered"] is True:
                        seen_true = True
                        if eff_status == "PASS":
                            return (
                                f"Contradictory GWI report: status is 'PASS' while `{cl_label}.lenses[]` triggered veto.",
                                None,
                                None,
                            )
                        if seen_explicit_false:
                            return (
                                f"Contradictory `{cl_label}` veto fields: False while lens item has veto_triggered=True.",
                                None,
                                None,
                            )

        coupled_vals: list[float] = []
        for ckey in ("coupled_goldwood_g", "coupledGoldwoodG"):
            if ckey in cl_d and cl_d[ckey] is not None:
                cval = cl_d[ckey]
                if not _is_real_num(cval) or float(cval) < 0.0 or float(cval) > 10.0 + 1e-6:
                    return f"Malformed `{cl_label}.{ckey}`: {cval!r}.", None, None
                cfloat = float(cval)
                if eff_status == "PASS" and cfloat + 1e-6 < DEFAULT_GWI_PASS_THRESHOLD:
                    return (
                        f"Contradictory GWI report: status is 'PASS' while {cl_label}.{ckey}="
                        f"{cfloat:.2f} < {DEFAULT_GWI_PASS_THRESHOLD:.2f}.",
                        None,
                        None,
                    )
                coupled_vals.append(cfloat)
        if len(coupled_vals) == 2 and abs(coupled_vals[0] - coupled_vals[1]) > 1e-3:
            return f"Contradictory `{cl_label}` coupled score fields.", None, None
        coupled_eff = coupled_vals[0] if coupled_vals else None
        veto_state: bool | None = True if seen_true else (False if seen_explicit_false else None)
        return None, coupled_eff, veto_state

    cl1_info: tuple[float | None, bool | None] | None = None
    cl2_info: tuple[float | None, bool | None] | None = None
    if cl1 is not None:
        err1, cg1, vt1 = _inspect_craft_lens_alias("craft_lenses", cl1)
        if err1:
            return err1
        cl1_info = (cg1, vt1)
    if cl2 is not None:
        err2, cg2, vt2 = _inspect_craft_lens_alias("craftLenses", cl2)
        if err2:
            return err2
        cl2_info = (cg2, vt2)
    if cl1_info is not None and cl2_info is not None:
        cg1, vt1 = cl1_info
        cg2, vt2 = cl2_info
        if cg1 is not None and cg2 is not None and abs(cg1 - cg2) > 1e-3:
            return f"Contradictory craft lens scores: craft_lenses={cg1} vs craftLenses={cg2}."
        if vt1 is not None and vt2 is not None and vt1 != vt2:
            return f"Contradictory craft lens veto flags: craft_lenses={vt1} vs craftLenses={vt2}."

    th_calc = calc.get("theta") if "theta" in calc and calc["theta"] is not None else None
    th_top = obj.get("barrierTheta") if "barrierTheta" in obj and obj["barrierTheta"] is not None else None
    for th_name, th_raw in (("calculation.theta", th_calc), ("barrierTheta", th_top)):
        if th_raw is not None:
            if not _is_real_num(th_raw) or float(th_raw) + 1e-6 < 7.0 or float(th_raw) > 10.0 + 1e-6:
                return f"Malformed or out-of-range GWI `{th_name}`: {th_raw!r}."
    if th_calc is not None and th_top is not None and abs(float(th_calc) - float(th_top)) > 1e-3:
        return f"Contradictory GWI theta fields: calculation.theta={th_calc} vs barrierTheta={th_top}."

    lam_vals: list[tuple[str, float]] = []
    for lam_key, lam_src in (("calculation.conscience_lambda", calc), ("conscienceLambda", obj)):
        raw_key = lam_key.split(".")[-1]
        if raw_key in lam_src and lam_src[raw_key] is not None:
            lv = lam_src[raw_key]
            if not (isinstance(lv, bool) or _is_real_num(lv)):
                return f"Malformed GWI `{lam_key}`: {lv!r}."
            lf = float(1.0 if lv is True else (0.0 if lv is False else lv))
            if eff_status == "PASS" and abs(lf - 1.0) > 1e-6:
                return f"Contradictory GWI report: status is 'PASS' while `{lam_key}` is {lv!r}."
            lam_vals.append((lam_key, lf))
    if len(lam_vals) == 2 and abs(lam_vals[0][1] - lam_vals[1][1]) > 1e-6:
        return f"Contradictory GWI conscience fields: {lam_vals[0][0]}={lam_vals[0][1]} vs {lam_vals[1][0]}={lam_vals[1][1]}."

    if eff_status == "PASS":
        if g_val + 1e-6 < DEFAULT_GWI_PASS_THRESHOLD:
            return (
                f"Contradictory GWI report: status is 'PASS' while score G={g_val:.2f} < "
                f"{DEFAULT_GWI_PASS_THRESHOLD:.2f}."
            )
        if "sovereignGateG" in obj and obj["sovereignGateG"] is not None:
            gate_g = obj["sovereignGateG"]
            if not _is_real_num(gate_g) or float(gate_g) + 1e-6 < DEFAULT_GWI_PASS_THRESHOLD or float(gate_g) > 10.0:
                return f"Contradictory GWI `sovereignGateG` threshold: {gate_g!r}."
            if g_val + 1e-6 < float(gate_g):
                return f"Contradictory GWI report: score G={g_val:.2f} < sovereignGateG={float(gate_g):.2f}."
        tiers_obj = obj.get("tiers")
        if tiers_obj is not None:
            if not isinstance(tiers_obj, dict):
                return "Malformed GWI `tiers`: expected dict."
            theta_val = calc.get("theta", obj.get("barrierTheta", 7.0))
            if not _is_real_num(theta_val) or float(theta_val) + 1e-6 < 7.0 or float(theta_val) > 10.0 + 1e-6:
                return f"Malformed or out-of-range GWI `theta`: {theta_val!r}."
            for tk, tv in tiers_obj.items():
                if not _is_real_num(tv) or float(tv) < 0.0 or float(tv) > 10.0 + 1e-6:
                    return f"Malformed GWI tier score `{tk}={tv!r}`."
                if float(tv) + 1e-6 < float(theta_val):
                    return (
                        f"Contradictory GWI report: status is 'PASS' while tier `{tk}={float(tv):.2f}` < "
                        f"theta={float(theta_val):.2f}."
                    )
        blockers_obj = obj.get("blockers")
        if blockers_obj is not None:
            if not isinstance(blockers_obj, list):
                return "Malformed GWI `blockers`: expected list."
            for b in blockers_obj:
                if isinstance(b, dict) and str(b.get("severity") or "").strip().upper() == "P0":
                    return "Contradictory GWI report: status is 'PASS' while a P0 blocker is recorded."

    return None


def _canonical_gwi_semantics(obj: dict[str, Any]) -> dict[str, Any]:
    """Normalize both `evidence/goldwood.json` and live `evaluate_target()` reports into comparable semantics."""
    if not isinstance(obj, dict):
        return {}
    calc = obj.get("calculation") if isinstance(obj.get("calculation"), dict) else {}
    tiers = obj.get("tiers") if isinstance(obj.get("tiers"), dict) else {}
    cl1 = obj.get("craft_lenses") if isinstance(obj.get("craft_lenses"), dict) else None
    cl2 = obj.get("craftLenses") if isinstance(obj.get("craftLenses"), dict) else None
    cl_dict = cl1 if cl1 is not None else (cl2 if cl2 is not None else {})
    raw_blockers = obj.get("blockers")
    blockers = (
        sorted((str(b.get("id") or b.get("message") or b) if isinstance(b, dict) else str(b)) for b in raw_blockers)
        if isinstance(raw_blockers, list)
        else []
    )
    raw_g = calc.get("goldwood_g", obj.get("compositeScoreG", obj.get("goldwood_g")))
    goldwood_g = round(float(raw_g), 4) if _is_real_num(raw_g) else None
    raw_coupled = cl_dict.get("coupled_goldwood_g", cl_dict.get("coupledGoldwoodG"))
    coupled_g = round(float(raw_coupled), 4) if _is_real_num(raw_coupled) else None
    veto_triggered = bool(
        any(
            d.get(vk) is True
            or (
                isinstance(d.get("lenses"), list)
                and any(isinstance(lr, dict) and lr.get("veto_triggered") is True for lr in d["lenses"])
            )
            for d in (cl1, cl2)
            if isinstance(d, dict)
            for vk in ("veto_triggered", "vetoTriggered", "verga_veto_triggered", "chrysos_veto_triggered")
        )
    )
    norm_tiers = {str(k): round(float(v), 4) if _is_real_num(v) else v for k, v in sorted(tiers.items())}
    return {
        "project": str(obj.get("project_name") or obj.get("project") or "").strip(),
        "profile": str(obj.get("profile") or "").strip().lower(),
        "goldwood_g": goldwood_g,
        "status": str(calc.get("status") or obj.get("status") or "").strip().upper(),
        "tiers": norm_tiers,
        "coupled_g": coupled_g,
        "veto_triggered": veto_triggered,
        "blockers": blockers,
    }


def _compute_gwi_semantic_sha256(obj: dict[str, Any]) -> str:
    sem = _canonical_gwi_semantics(obj)
    canon = json.dumps(sem, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()


def _strip_volatile_gwi_payload(obj: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for k, v in obj.items():
        if k in _VOLATILE_GWI_KEYS:
            continue
        if k == "gate_execution" and isinstance(v, dict):
            cleaned[k] = {gk: gv for gk, gv in v.items() if gk not in _VOLATILE_GATE_RECEIPT_KEYS}
        else:
            cleaned[k] = v
    return cleaned


def _compute_gwi_receipt_sha256(goldwood_path: Path) -> str:
    """Hash `evidence/goldwood.json` canonically while ignoring volatile timestamps and gate metadata."""
    if not goldwood_path.is_file():
        return ""
    raw_bytes = goldwood_path.read_bytes()
    try:
        obj = json.loads(raw_bytes.decode("utf-8"))
        if isinstance(obj, dict):
            cleaned = _strip_volatile_gwi_payload(obj)
            canon = json.dumps(cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            return hashlib.sha256(canon).hexdigest()
    except Exception:
        pass
    return "MALFORMED:" + hashlib.sha256(raw_bytes).hexdigest()


def _compute_gate_receipt_sha256(gate_receipt_path: Path) -> str:
    """Hash `evidence/gate_receipt.json` by semantic execution fields while ignoring volatile timing/log tails."""
    if not gate_receipt_path.is_file():
        return ""
    raw_bytes = gate_receipt_path.read_bytes()
    try:
        obj = json.loads(raw_bytes.decode("utf-8"))
        if isinstance(obj, dict):
            cleaned = {k: v for k, v in obj.items() if k not in _VOLATILE_GATE_RECEIPT_KEYS}
            canon = json.dumps(cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            return hashlib.sha256(canon).hexdigest()
    except Exception:
        pass
    return "MALFORMED:" + hashlib.sha256(raw_bytes).hexdigest()


def _compute_file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_target_and_scope(target_path: str | Path) -> tuple[Path, Path, str]:
    resolved = Path(target_path).expanduser().resolve()
    target_dir = resolved if resolved.is_dir() else resolved.parent
    repo_root = find_git_root(target_dir)
    try:
        rel = target_dir.relative_to(repo_root)
        subpath = rel.as_posix() if str(rel) != "." else "."
    except ValueError:
        subpath = "."
    return target_dir, repo_root, subpath


def _format_untracked_hunk(repo_root: Path, rel_posix: str, max_bytes: int = 32768) -> tuple[str, bool]:
    p_file = repo_root / rel_posix
    try:
        lstat = p_file.lstat()
        if not stat.S_ISREG(lstat.st_mode):
            return "", False
        mode_oct = "100755" if (lstat.st_mode & 0o111) else "100644"
        raw = p_file.read_bytes()
    except OSError:
        return "", True
    if b"\x00" in raw[:8192]:
        return (
            f"diff --git a/{rel_posix} b/{rel_posix}\nnew file mode {mode_oct}\nBinary files /dev/null and b/{rel_posix} differ\n",
            True,
        )
    truncated_note = len(raw) > max_bytes
    if not truncated_note:
        try:
            snippet = raw.decode("utf-8")
        except UnicodeDecodeError:
            return (
                f"diff --git a/{rel_posix} b/{rel_posix}\nnew file mode {mode_oct}\nBinary files /dev/null and b/{rel_posix} differ\n",
                True,
            )
    else:
        snippet = raw[:max_bytes].decode("utf-8", errors="replace")
    lines = snippet.splitlines()
    header = (
        f"diff --git a/{rel_posix} b/{rel_posix}\n"
        f"new file mode {mode_oct}\n"
        f"--- /dev/null\n"
        f"+++ b/{rel_posix}\n"
        f"@@ -0,0 +1,{max(1, len(lines))} @@\n"
    )
    body = "\n".join(f"+{ln}" for ln in lines)
    if truncated_note:
        body += "\n+... [untracked file truncated to budget]"
    return header + (body + "\n" if body else ""), truncated_note


_GIT_DIFF_PIN_ARGS = ("-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "-c", "diff.context=3")


def _extract_numstat_opaque_paths(numstat_out: str) -> list[str]:
    opaque: list[str] = []
    if not numstat_out:
        return opaque
    tokens = numstat_out.split("\0")
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        idx += 1
        if not tok:
            continue
        parts = tok.split("\t", 2)
        if len(parts) != 3:
            continue
        ins, dels, rel_s = parts
        if ins == "-" and dels == "-":
            clean = _normalize_git_rel_path(rel_s)
            if clean and not _is_skipped_evidence_path(clean) and clean not in opaque:
                opaque.append(clean)
    return opaque


def _synthesize_utf8_unified_diff(
    repo_root: Path, rel_posix: str, has_head: bool, max_bytes: int = 32768
) -> str | None:
    p_cur = repo_root / rel_posix
    if p_cur.is_symlink():
        return None
    if p_cur.exists():
        if not p_cur.is_file():
            return None
        try:
            cur_raw = p_cur.read_bytes()
        except OSError:
            return None
        if len(cur_raw) > max_bytes or b"\x00" in cur_raw[:8192]:
            return None
        try:
            cur_txt = cur_raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        cur_txt = ""

    old_txt = ""
    if has_head:
        try:
            head_proc = subprocess.run(
                ["git", "-C", str(repo_root), "show", f"HEAD:{rel_posix}"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if head_proc.returncode == 0:
                old_raw = head_proc.stdout
                if len(old_raw) > max_bytes or b"\x00" in old_raw[:8192]:
                    return None
                try:
                    old_txt = old_raw.decode("utf-8")
                except UnicodeDecodeError:
                    return None
            else:
                rc_ex, _ = _run_git_cmd(repo_root, "cat-file", "-e", f"HEAD:{rel_posix}")
                if rc_ex == 0:
                    return None
        except Exception:
            return None

    diff_lines = list(
        difflib.unified_diff(
            old_txt.splitlines(keepends=True),
            cur_txt.splitlines(keepends=True),
            fromfile=f"a/{rel_posix}",
            tofile=f"b/{rel_posix}",
            n=3,
        )
    )
    if not diff_lines:
        return None
    return f"diff --git a/{rel_posix} b/{rel_posix}\n" + "".join(diff_lines)


def _collect_bounded_diff(
    repo_root: Path, dirty: bool, max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS, _visited: frozenset[Path] = frozenset()
) -> tuple[str, list[str], bool]:
    resolved_root = repo_root.resolve()
    if resolved_root in _visited:
        return "", [], True
    next_visited = _visited | {resolved_root}

    touched: list[str] = []
    rc_st, st_out = _run_git_cmd(repo_root, "status", "--porcelain", "-uall", "-z")
    if rc_st == 0 and st_out:
        tokens = st_out.split("\0")
        idx = 0
        while idx < len(tokens):
            tok = tokens[idx]
            idx += 1
            if len(tok) < 4:
                continue
            status_xy = tok[:2]
            rel = _normalize_git_rel_path(tok[3:])
            if status_xy[0] in ("R", "C") and idx < len(tokens):
                idx += 1
            if rel and not _is_skipped_evidence_path(rel) and rel not in touched:
                touched.append(rel)

    diff_parts: list[str] = []
    any_untracked_truncated = False
    diff_cmd_failed = False
    opaque_paths: list[str] = []
    rc_head, _ = _run_git_cmd(repo_root, "rev-parse", "--verify", "--quiet", "HEAD")
    has_head = rc_head == 0

    if dirty:
        if has_head:
            rc_d, d_out = _run_git_cmd(
                repo_root,
                *_GIT_DIFF_PIN_ARGS,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--no-renames",
                "HEAD",
            )
            rc_ns, ns_out = _run_git_cmd(
                repo_root, "diff", "--numstat", "-z", "--no-ext-diff", "--no-textconv", "--no-renames", "HEAD"
            )
            if rc_d != 0 or rc_ns != 0:
                diff_cmd_failed = True
            else:
                if d_out.strip():
                    diff_parts.append(d_out.rstrip("\n") + "\n")
                for op in _extract_numstat_opaque_paths(ns_out):
                    if op not in opaque_paths:
                        opaque_paths.append(op)
        else:
            rc_c, c_out = _run_git_cmd(
                repo_root, *_GIT_DIFF_PIN_ARGS, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames"
            )
            rc_s_cached, s_cached_out = _run_git_cmd(
                repo_root,
                *_GIT_DIFF_PIN_ARGS,
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--no-renames",
            )
            rc_ns_w, ns_w = _run_git_cmd(
                repo_root, "diff", "--numstat", "-z", "--no-ext-diff", "--no-textconv", "--no-renames"
            )
            rc_ns_c, ns_c = _run_git_cmd(
                repo_root, "diff", "--cached", "--numstat", "-z", "--no-ext-diff", "--no-textconv", "--no-renames"
            )
            if rc_c != 0 or rc_s_cached != 0 or rc_ns_w != 0 or rc_ns_c != 0:
                diff_cmd_failed = True
            for chunk in (s_cached_out, c_out):
                if chunk.strip():
                    diff_parts.append(chunk.rstrip("\n") + "\n")
            for ns_blk in (ns_c, ns_w):
                for op in _extract_numstat_opaque_paths(ns_blk):
                    if op not in opaque_paths:
                        opaque_paths.append(op)

        for rel_op in opaque_paths:
            if rel_op not in touched:
                touched.append(rel_op)
            syn = _synthesize_utf8_unified_diff(repo_root, rel_op, has_head=has_head)
            if syn:
                diff_parts.append(syn)
            else:
                any_untracked_truncated = True

        # Include dirty nested submodule / gitlink diffs recursively
        for rel_t in list(touched):
            p_sub = repo_root / rel_t
            if p_sub.is_dir() and not p_sub.is_symlink():
                if (p_sub / ".git").exists():
                    rc_sub_st, sub_st = _run_git_cmd(p_sub, "status", "--porcelain", "-z")
                    sub_dirty = bool(rc_sub_st == 0 and sub_st.strip())
                    sub_diff, _, sub_trunc = _collect_bounded_diff(
                        p_sub, sub_dirty, max_diff_chars=max_diff_chars, _visited=next_visited
                    )
                    if sub_diff.strip():
                        diff_parts.append(f"# Submodule diff: {rel_t}\n{sub_diff.rstrip()}\n")
                    if rc_sub_st != 0 or sub_trunc:
                        any_untracked_truncated = True
                else:
                    any_untracked_truncated = True

        rc_u, u_out = _run_git_cmd(repo_root, "ls-files", "-z", "--others", "--exclude-standard")
        if rc_u != 0:
            diff_cmd_failed = True
        elif u_out:
            for rel_u in sorted(x for x in u_out.split("\0") if x):
                rel_clean = _normalize_git_rel_path(rel_u)
                if _is_skipped_evidence_path(rel_clean):
                    continue
                if rel_clean not in touched:
                    touched.append(rel_clean)
                hunk, hunk_trunc = _format_untracked_hunk(repo_root, rel_clean)
                if hunk_trunc:
                    any_untracked_truncated = True
                if hunk:
                    diff_parts.append(hunk)

    diff_text = "\n".join(part.rstrip("\n") for part in diff_parts if part.strip())
    if diff_text:
        diff_text += "\n"
    if not diff_text and has_head and not diff_cmd_failed:
        rc_s, s_out = _run_git_cmd(
            repo_root,
            *_GIT_DIFF_PIN_ARGS,
            "show",
            "--format=",
            "--unified=3",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--no-renames",
            "HEAD",
        )
        rc_ns_h, ns_h = _run_git_cmd(
            repo_root, "show", "--numstat", "-z", "--no-ext-diff", "--no-textconv", "--format=", "HEAD"
        )
        if rc_s != 0 or rc_ns_h != 0:
            diff_cmd_failed = True
        elif s_out.strip():
            diff_text = s_out
        if rc_ns_h == 0 and ns_h:
            for rel_op in _extract_numstat_opaque_paths(ns_h):
                if rel_op not in touched:
                    touched.append(rel_op)
                syn = _synthesize_utf8_unified_diff(repo_root, rel_op, has_head=False)
                if syn:
                    diff_text = (diff_text.rstrip("\n") + "\n" if diff_text else "") + syn
                else:
                    any_untracked_truncated = True
        rc_n, n_out = _run_git_cmd(repo_root, "show", "-z", "--name-only", "--format=", "HEAD")
        if rc_n == 0:
            for ln in n_out.split("\0"):
                if not ln:
                    continue
                rel = _normalize_git_rel_path(ln)
                if rel and rel not in touched:
                    touched.append(rel)

    diff_truncated = bool(any_untracked_truncated or diff_cmd_failed)
    if len(diff_text) > max_diff_chars:
        diff_truncated = True
        diff_text = diff_text[:max_diff_chars] + "\n... [diff truncated to budget] ..."
    return diff_text, touched, diff_truncated


def _sanitize_goldwood_for_challenge(
    goldwood_path: Path,
    gate_receipt_path: Path,
    touched_files: list[str],
    blind_scores: bool = True,
) -> dict[str, Any]:
    sanitized: dict[str, Any] = {
        "score_blind": bool(blind_scores),
        "touched_files": list(touched_files),
    }
    if goldwood_path.is_file():
        try:
            raw_obj = json.loads(goldwood_path.read_text(encoding="utf-8"))
            if isinstance(raw_obj, dict):
                cleaned_obj = _strip_volatile_gwi_payload(raw_obj)
                if blind_scores:
                    for k, v in cleaned_obj.items():
                        if k not in _SCORE_BLIND_DROP_KEYS:
                            sanitized[k] = v
                else:
                    sanitized["goldwood_evidence"] = cleaned_obj
        except Exception:
            pass
    if gate_receipt_path.is_file():
        try:
            gr = json.loads(gate_receipt_path.read_text(encoding="utf-8"))
            if isinstance(gr, dict):
                sanitized["gate_execution"] = {
                    "passed": gr.get("passed") is True,
                    "executed": gr.get("executed") is True,
                    "fresh_for_tree": gr.get("fresh_for_tree") is True,
                    "command": gr.get("command"),
                    "exit_code": gr.get("exit_code") if _is_strict_int(gr.get("exit_code")) else None,
                }
        except Exception:
            pass
    return sanitized


def _build_instructions_block(resolved_intent: str) -> str:
    return (
        "You are an independent adversarial engineering reviewer executing an Elenchus pre-seal challenge.\n"
        "Do NOT assume passing gates or clean formatting guarantee semantic soundness.\n"
        "Challenge whether the implementation, behavioral tests, error handling, security boundaries, and provenance\n"
        "actually satisfy the stated intent. Cite exact repository file paths, line numbers, and symbol names.\n"
        "Return ONLY a JSON object with this exact schema:\n"
        "{\n"
        '  "verdict": "APPROVE | CHALLENGE | VETO",\n'
        '  "summary": "concise technical verdict rationale",\n'
        '  "findings": [\n'
        "    {\n"
        '      "severity": "P0 | P1 | P2 | INFO",\n'
        '      "category": "correctness | security | test_gap | spec_drift | performance | provenance",\n'
        '      "file_path": "relative/path.py",\n'
        '      "line": 0,\n'
        '      "symbol": "optional_symbol",\n'
        '      "claim": "concrete defect, edge case, or blind spot",\n'
        '      "evidence": "specific code/test citation"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        f"\n[Stated Intent]\n{resolved_intent}\n"
    )


def build_elenchus_challenge_packet(
    target_path: str | Path = ".",
    intent: str = "",
    blind_scores: bool = True,
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS,
) -> dict[str, Any]:
    """
    Construct a score-blind, tree-bound adversarial challenge packet for independent model review.
    Strips Goldwood scores/verdicts by default so challengers cannot anchor on reported grades.
    """
    target_dir, repo_root, subpath = _resolve_target_and_scope(target_path)
    fp = compute_subproject_fingerprint(repo_root, subpath)
    worktree_sha = _compute_worktree_content_sha(target_dir)

    goldwood_path = target_dir / "evidence" / "goldwood.json"
    gate_receipt_path = target_dir / "evidence" / "gate_receipt.json"
    gwi_receipt_sha256 = _compute_gwi_receipt_sha256(goldwood_path)
    gwi_semantic_sha256 = ""
    if goldwood_path.is_file():
        try:
            gwi_obj = json.loads(goldwood_path.read_text(encoding="utf-8"))
            if isinstance(gwi_obj, dict):
                gwi_semantic_sha256 = _compute_gwi_semantic_sha256(gwi_obj)
        except Exception:
            gwi_semantic_sha256 = ""
    gate_receipt_sha256 = _compute_gate_receipt_sha256(gate_receipt_path)

    resolved_intent = intent.strip()
    if not resolved_intent:
        rc_log, log_out = _run_git_cmd(repo_root, "log", "-n", "1", "--pretty=%s")
        if rc_log == 0 and log_out.strip():
            resolved_intent = log_out.strip()
        else:
            resolved_intent = "Verify repository soundness and behavioral evidence prior to sealing"

    diff_text, touched_files, diff_truncated = _collect_bounded_diff(
        repo_root, bool(fp.get("dirty")), max_diff_chars=max_diff_chars
    )
    sanitized_ev = _sanitize_goldwood_for_challenge(
        goldwood_path=goldwood_path,
        gate_receipt_path=gate_receipt_path,
        touched_files=touched_files,
        blind_scores=blind_scores,
    )
    if diff_truncated:
        sanitized_ev["diff_truncated"] = True
        sanitized_ev["coverage_complete"] = False

    instructions_block = _build_instructions_block(resolved_intent)

    diff_sha256 = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    prompt = (
        instructions_block
        + f"\n[Target Project]\nname={target_dir.name} | subpath={subpath} | commit={fp['commit_sha']} | tree={fp['tree_sha']}\n"
        + f"\n[Sanitized Raw Evidence (score_blind={bool(blind_scores)})]\n"
        + json.dumps(sanitized_ev, indent=2, sort_keys=True)
        + "\n\n[Code Diff / Snapshot]\n"
        + (diff_text or "(no git diff available; inspect repository files directly)")
        + "\n"
    )
    packet_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    return {
        "schema": "elenchus.packet.v1",
        "target_dir": str(target_dir),
        "repo_root": str(repo_root),
        "project_name": target_dir.name,
        "subpath": subpath,
        "intent": resolved_intent,
        "score_blind": bool(blind_scores),
        "sanitized_evidence": sanitized_ev,
        "prompt_instructions_and_intent": instructions_block,
        "prompt": prompt,
        "bindings": {
            "commit_sha": fp["commit_sha"],
            "full_commit_sha": fp["full_commit_sha"],
            "tree_sha": fp["tree_sha"],
            "worktree_sha": worktree_sha,
            "dirty": bool(fp["dirty"]),
            "gwi_receipt_sha256": gwi_receipt_sha256,
            "gwi_semantic_sha256": gwi_semantic_sha256,
            "gate_receipt_sha256": gate_receipt_sha256,
            "diff_sha256": diff_sha256,
            "diff_truncated": diff_truncated,
            "max_diff_chars": int(max_diff_chars),
            "coverage_complete": not diff_truncated,
            "packet_sha256": packet_sha256,
        },
    }


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
    for raw_ln, fenced in zip(lines, flags):
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
    for raw_ln, fenced in zip(lines, flags):
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


def _content_tokens(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9_]{3,}", (text or "").lower())
    return {t for t in toks if t not in _STOPWORDS}


def _extract_ngrams_2_3(text: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z0-9_]+", (text or "").lower()) if len(w) >= 2]
    grams: set[str] = set()
    for n in (2, 3):
        for i in range(max(0, len(words) - n + 1)):
            grams.add(" ".join(words[i : i + n]))
    return grams


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def _findings_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    fp_a = a["file_path"]
    fp_b = b["file_path"]
    if fp_a and fp_b and fp_a != fp_b:
        return False
    if fp_a and fp_b and fp_a == fp_b:
        sym_a = a["symbol"].lower()
        sym_b = b["symbol"].lower()
        if sym_a and sym_b and sym_a == sym_b:
            return True
        if a["line"] > 0 and b["line"] > 0 and abs(a["line"] - b["line"]) <= 15:
            return True
        if _jaccard(a["tokens"], b["tokens"]) >= 0.25:
            return True
    return _jaccard(a["tokens"], b["tokens"]) >= 0.38


def _is_path_grounded(rel_path: str, target_dir: Path | None, repo_root: Path | None) -> bool:
    clean = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts:
        return False
    for base in (target_dir, repo_root):
        if base is None:
            continue
        try:
            cand = (base / clean).resolve()
            cand.relative_to(base.resolve())
            if cand.is_file():
                return True
        except Exception:
            continue
    return False


def _compute_seal_status(
    panel: dict[str, Any],
    clusters: list[dict[str, Any]],
    llm_verdicts: list[str],
    execution_mode: str = "explicit",
    review_binding_verified: bool = True,
) -> str:
    for c in clusters:
        dec = str((c.get("adjudication") or {}).get("decision") or "").upper()
        if dec == "ACCEPT_FINDING":
            return "BLOCKED_DEFECT_CONFIRMED"
    for c in clusters:
        dec = str((c.get("adjudication") or {}).get("decision") or "").upper()
        if dec == "ESCALATE":
            return "BLOCKED_ESCALATED"
    if not review_binding_verified:
        return "UNBOUND_IMPORT_ADVISORY"
    if not panel.get("independent_panel", False):
        return "DEGRADED_NON_INDEPENDENT"
    req_clusters = [c for c in clusters if c.get("requires_adjudication")]
    for c in req_clusters:
        if str((c.get("adjudication") or {}).get("status") or "") != "ADJUDICATED":
            return "PENDING_ADJUDICATION"
    if req_clusters:
        return "READY_ADJUDICATED"
    if llm_verdicts and all(v == "APPROVE" for v in llm_verdicts):
        return "READY_UNANIMOUS"
    return "PENDING_ADJUDICATION"


def synthesize_elenchus_verdict(
    reviews: list[dict[str, Any]],
    packet: dict[str, Any],
    target_dir: Path | None = None,
    execution_mode: str = "explicit",
    review_binding_verified: bool = True,
) -> dict[str, Any]:
    """
    Aggregate cross-lineage reviewer outputs into calibrated dispute clusters (`INDEPENDENT_CONVERGENCE`,
    `PROMPT_ANCHORED_ECHO`, `LINEAGE_SPLIT`, `INCOMPLETE_COVERAGE`, `SINGLE_LINEAGE_NOTE`), compute
    Hill independence `N_eff`, prompt-anchoring ratio, `disagreement_index`, and deterministic `seal_status`.
    """
    t_dir = Path(target_dir).resolve() if target_dir is not None else Path(packet["target_dir"]).resolve()
    r_root = Path(packet["repo_root"]).resolve()

    normalized_reviews: list[dict[str, Any]] = []
    for r in reviews:
        if not isinstance(r, dict):
            raise ValueError("Malformed reviewer record: expected dict.")
        mid = str(r.get("model_id") or r.get("model") or "unknown").strip()
        raw_exec_st = str(r.get("execution_status") or "OK").strip().upper()
        if "raw_text" in r and "findings" not in r:
            nr = parse_reviewer_output(mid, str(r["raw_text"]))
            if raw_exec_st == "FAILED":
                nr["execution_status"] = "FAILED"
        else:
            if "findings" in r:
                raw_f_val = r["findings"]
                if not isinstance(raw_f_val, list) or not all(isinstance(f, dict) for f in raw_f_val):
                    raise ValueError("Malformed reviewer `findings`: expected list of finding objects.")
                raw_f = raw_f_val
            else:
                raw_f = []
            norm_f = [_normalize_finding_dict(f) for f in raw_f]
            nr = {
                "model_id": mid,
                "lineage": classify_model_lineage(mid),
                "verdict": _normalize_verdict(str(r.get("verdict") or ""), norm_f),
                "execution_status": raw_exec_st if raw_exec_st in ("OK", "FAILED") else "OK",
                "summary": str(r.get("summary") or "").strip(),
                "findings": norm_f,
            }
        normalized_reviews.append(_scrub_review_dict(nr))

    panel = compute_effective_panel_size(normalized_reviews)
    llm_lineages_present = set(panel["distinct_llm_lineages"])
    prompt_instr = str(packet.get("prompt_instructions_and_intent") or packet.get("intent") or "")
    prompt_ngrams = _extract_ngrams_2_3(prompt_instr)
    prompt_tokens = _content_tokens(prompt_instr)

    # Flatten findings with metadata
    flat_items: list[dict[str, Any]] = []
    for r in normalized_reviews:
        for f in r["findings"]:
            if not f.get("claim"):
                continue
            item = dict(f)
            item["model_id"] = r["model_id"]
            item["lineage"] = r["lineage"]
            item["tokens"] = _content_tokens(f"{f['symbol']} {f['claim']} {f['evidence']}")
            item["ngrams"] = _extract_ngrams_2_3(f"{f['claim']} {f['evidence']}")
            flat_items.append(item)

    # Synthesize split/challenge clusters over verified non-failed LLM reviewers; failed non-deterministic
    # reviewers always inject CHALLENGE so failures never silently become READY_UNANIMOUS.
    llm_reviews = [
        r
        for r in normalized_reviews
        if classify_model_lineage(r["model_id"]) in _VERIFIED_LLM_LINEAGES and r["execution_status"] != "FAILED"
    ]
    llm_verdicts = [
        "CHALLENGE" if r["execution_status"] == "FAILED" else r["verdict"]
        for r in normalized_reviews
        if r["lineage"] != "deterministic"
    ]
    has_split_verdict = ("APPROVE" in [r["verdict"] for r in llm_reviews]) and any(
        r["verdict"] in ("CHALLENGE", "VETO") for r in llm_reviews
    )

    # Cluster findings greedily
    raw_clusters: list[list[dict[str, Any]]] = []
    for item in flat_items:
        placed = False
        for grp in raw_clusters:
            if any(_findings_match(item, rep) for rep in grp):
                grp.append(item)
                placed = True
                break
        if not placed:
            raw_clusters.append([item])

    clusters: list[dict[str, Any]] = []
    for grp in raw_clusters:
        best_sev = min((g["severity"] for g in grp), key=lambda s: _SEVERITY_RANK.get(s, 9))
        cats = Counter(g["category"] for g in grp)
        best_cat = cats.most_common(1)[0][0]
        file_paths = [g["file_path"] for g in grp if g["file_path"]]
        primary_file = Counter(file_paths).most_common(1)[0][0] if file_paths else ""
        lines_non_zero = [g["line"] for g in grp if g["line"] > 0]
        primary_line = min(lines_non_zero) if lines_non_zero else 0
        syms = [g["symbol"] for g in grp if g["symbol"]]
        primary_sym = Counter(syms).most_common(1)[0][0] if syms else ""

        lineages = sorted({g["lineage"] for g in grp})
        llm_lin_in_cluster = sorted({g["lineage"] for g in grp if g["lineage"] in _VERIFIED_LLM_LINEAGES})
        model_ids = sorted({g["model_id"] for g in grp})
        grounded = _is_path_grounded(primary_file, t_dir, r_root)

        # Compute anchoring_ratio across distinct lineages in the cluster
        anchoring_ratio = 0.0
        if len(llm_lin_in_cluster) >= 2:
            per_lin_grams: dict[str, set[str]] = {}
            per_lin_tokens: dict[str, set[str]] = {}
            for g in grp:
                if g["lineage"] not in _VERIFIED_LLM_LINEAGES:
                    continue
                per_lin_grams.setdefault(g["lineage"], set()).update(g["ngrams"])
                per_lin_tokens.setdefault(g["lineage"], set()).update(g["tokens"])
            gram_df = Counter(ng for lin_grams in per_lin_grams.values() for ng in lin_grams)
            shared_grams = {ng for ng, cnt in gram_df.items() if cnt >= 2}
            tok_df = Counter(tk for lin_toks in per_lin_tokens.values() for tk in lin_toks)
            shared_toks = {tk for tk, cnt in tok_df.items() if cnt >= 2}
            ngram_ratio = (len(shared_grams & prompt_ngrams) / len(shared_grams)) if shared_grams else 0.0
            tok_ratio = (len(shared_toks & prompt_tokens) / len(shared_toks)) if shared_toks else 0.0
            anchoring_ratio = ngram_ratio if grounded else max(ngram_ratio, tok_ratio)
        anchoring_ratio = round(anchoring_ratio, 4)

        if not panel["independent_panel"]:
            signal_class = "SINGLE_LINEAGE_NOTE"
        elif len(llm_lin_in_cluster) >= 2:
            if anchoring_ratio >= ANCHOR_ECHO_THRESHOLD and not grounded:
                signal_class = "PROMPT_ANCHORED_ECHO"
            else:
                signal_class = "INDEPENDENT_CONVERGENCE"
        else:
            signal_class = "LINEAGE_SPLIT"

        requires_adj = (best_sev in ("P0", "P1")) or (
            signal_class == "LINEAGE_SPLIT" and best_sev in ("P0", "P1", "P2")
        )
        rep_claim = max((g["claim"] for g in grp), key=len)
        rep_evidence = max((g["evidence"] for g in grp if g["evidence"]), key=len, default="")

        clusters.append(
            {
                "cluster_id": "",
                "severity": best_sev,
                "category": best_cat,
                "signal_class": signal_class,
                "file_path": primary_file,
                "line": primary_line,
                "symbol": primary_sym,
                "grounded_in_repo": grounded,
                "anchoring_ratio": anchoring_ratio,
                "lineages": lineages,
                "model_ids": model_ids,
                "claim": rep_claim,
                "evidence": rep_evidence,
                "requires_adjudication": requires_adj,
                "adjudication": {
                    "status": "PENDING" if requires_adj else "ADVISORY",
                    "decision": None,
                    "adjudicator": None,
                    "reason": "",
                    "adjudicated_at": None,
                },
                "member_findings": [
                    {
                        "model_id": g["model_id"],
                        "lineage": g["lineage"],
                        "severity": g["severity"],
                        "file_path": g["file_path"],
                        "line": g["line"],
                        "symbol": g["symbol"],
                        "claim": g["claim"],
                    }
                    for g in grp
                ],
            }
        )

    # Sort deterministically by severity then file/claim and assign sequential ELC-001 IDs
    clusters.sort(key=lambda c: (_SEVERITY_RANK.get(c["severity"], 9), c["file_path"], c["claim"]))
    for idx, c in enumerate(clusters, 1):
        c["cluster_id"] = f"ELC-{idx:03d}"

    # Guarantee top-level verdict splits or non-APPROVE challenges always have >= 1 adjudicable cluster
    has_split_cluster = any(c["signal_class"] == "LINEAGE_SPLIT" for c in clusters)
    if panel["independent_panel"] and has_split_verdict and not has_split_cluster:
        dissenters = [r for r in llm_reviews if r["verdict"] in ("CHALLENGE", "VETO")]
        approvers = [r for r in llm_reviews if r["verdict"] == "APPROVE"]
        if any(not d["findings"] for d in dissenters) or not clusters:
            clusters.append(
                {
                    "cluster_id": "ELC-SPLIT-001",
                    "severity": "P1",
                    "category": "provenance",
                    "signal_class": "LINEAGE_SPLIT",
                    "file_path": "",
                    "line": 0,
                    "symbol": "",
                    "grounded_in_repo": False,
                    "anchoring_ratio": 0.0,
                    "lineages": sorted({d["lineage"] for d in dissenters}),
                    "model_ids": sorted({d["model_id"] for d in dissenters}),
                    "claim": (
                        f"Cross-lineage top-level verdict split: "
                        f"{sorted({d['lineage'] for d in dissenters})} voted {[d['verdict'] for d in dissenters]} "
                        f"while {sorted({a['lineage'] for a in approvers})} voted APPROVE."
                    ),
                    "evidence": "; ".join(d.get("summary", "") for d in dissenters if d.get("summary"))[:240],
                    "requires_adjudication": True,
                    "adjudication": {
                        "status": "PENDING",
                        "decision": None,
                        "adjudicator": None,
                        "reason": "",
                        "adjudicated_at": None,
                    },
                    "member_findings": [],
                }
            )
    elif panel["independent_panel"] and not clusters and any(v in ("CHALLENGE", "VETO") for v in llm_verdicts):
        clusters.append(
            {
                "cluster_id": "ELC-CHALLENGE-001",
                "severity": "P1",
                "category": "provenance",
                "signal_class": "INDEPENDENT_CONVERGENCE",
                "file_path": "",
                "line": 0,
                "symbol": "",
                "grounded_in_repo": False,
                "anchoring_ratio": 0.0,
                "lineages": sorted(llm_lineages_present),
                "model_ids": sorted({r["model_id"] for r in llm_reviews}),
                "claim": "Independent panel challenged/vetoed completion without itemized findings.",
                "evidence": "; ".join(r.get("summary", "") for r in llm_reviews if r.get("summary"))[:240],
                "requires_adjudication": True,
                "adjudication": {
                    "status": "PENDING",
                    "decision": None,
                    "adjudicator": None,
                    "reason": "",
                    "adjudicated_at": None,
                },
                "member_findings": [],
            }
        )

    # Always surface incomplete diff coverage uniformly across all execution modes whenever diff is truncated
    diff_truncated = bool((packet.get("bindings") or {}).get("diff_truncated", False))
    incomplete_coverage = diff_truncated
    if (
        diff_truncated
        and panel["independent_panel"]
        and not any(c.get("cluster_id") == "ELC-COVERAGE-001" for c in clusters)
    ):
        clusters.append(
            {
                "cluster_id": "ELC-COVERAGE-001",
                "severity": "P1",
                "category": "provenance",
                "signal_class": "INCOMPLETE_COVERAGE",
                "file_path": "",
                "line": 0,
                "symbol": "",
                "grounded_in_repo": False,
                "anchoring_ratio": 0.0,
                "lineages": sorted(llm_lineages_present),
                "model_ids": sorted({r["model_id"] for r in llm_reviews}),
                "claim": "Challenge packet diff exceeded budget and was truncated before tool-less reviewer inspection.",
                "evidence": "bindings.diff_truncated=True; tool-less reviewers could not inspect the complete diff.",
                "requires_adjudication": True,
                "adjudication": {
                    "status": "PENDING",
                    "decision": None,
                    "adjudicator": None,
                    "reason": "",
                    "adjudicated_at": None,
                },
                "member_findings": [],
            }
        )

    # Ensure if an independent panel issued CHALLENGE/VETO and existing clusters were all advisory,
    # the top cluster becomes explicitly adjudicable so the state machine can never deadlock.
    if (
        panel["independent_panel"]
        and any(v in ("CHALLENGE", "VETO") for v in llm_verdicts)
        and clusters
        and not any(c["requires_adjudication"] for c in clusters)
    ):
        clusters[0]["requires_adjudication"] = True
        clusters[0]["adjudication"]["status"] = "PENDING"

    # Disagreement index: blend of split cluster ratio and cross-lineage verdict divergence
    n_clusters = len(clusters)
    n_split = sum(1 for c in clusters if c["signal_class"] == "LINEAGE_SPLIT")
    n_conv = sum(1 for c in clusters if c["signal_class"] == "INDEPENDENT_CONVERGENCE")
    n_echo = sum(1 for c in clusters if c["signal_class"] == "PROMPT_ANCHORED_ECHO")
    n_single = sum(1 for c in clusters if c["signal_class"] == "SINGLE_LINEAGE_NOTE")
    n_req = sum(1 for c in clusters if c["requires_adjudication"])

    lineage_verdict_scores: list[float] = []
    for lin in sorted(llm_lineages_present):
        v_list = [_VERDICT_SCORE.get(r["verdict"], 0.5) for r in llm_reviews if r["lineage"] == lin]
        if v_list:
            lineage_verdict_scores.append(max(v_list))
    verdict_spread = (
        (max(lineage_verdict_scores) - min(lineage_verdict_scores)) if len(lineage_verdict_scores) >= 2 else 0.0
    )
    split_ratio = (n_split / n_clusters) if n_clusters > 0 else 0.0
    if n_clusters > 0:
        disagreement_index = round(min(1.0, max(0.0, 0.7 * split_ratio + 0.3 * verdict_spread)), 4)
    else:
        disagreement_index = round(verdict_spread, 4)

    seal_status = _compute_seal_status(
        panel,
        clusters,
        llm_verdicts,
        execution_mode=execution_mode,
        review_binding_verified=review_binding_verified,
    )
    reviewer_summaries = [
        {
            "model_id": r["model_id"],
            "lineage": r["lineage"],
            "verdict": r["verdict"],
            "execution_status": r["execution_status"],
            "findings_count": len(r["findings"]),
            "summary": r["summary"][:280],
        }
        for r in normalized_reviews
    ]

    return {
        "panel": panel,
        "reviewer_summaries": reviewer_summaries,
        "raw_reviews": normalized_reviews,
        "clusters": clusters,
        "metrics": {
            "total_clusters": n_clusters,
            "independent_convergence_count": n_conv,
            "prompt_anchored_echo_count": n_echo,
            "lineage_split_count": n_split,
            "single_lineage_count": n_single,
            "requires_adjudication_count": n_req,
            "incomplete_coverage": incomplete_coverage,
            "disagreement_index": disagreement_index,
        },
        "seal_status": seal_status,
    }


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
            tok = base64.b64encode(f"{raw_user}:{raw_pass}".encode("utf-8")).decode("ascii")
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
                **(item.get("bindings") if isinstance(item.get("bindings"), dict) else {}),
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
    from .bridge import append_triad_provenance_event

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
                collected_reviews.append(_query_openai_compat_reviewer(m_id.strip(), url.strip(), packet["prompt"]))
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
                            _query_openai_compat_reviewer(m_id.strip(), url.strip(), packet["prompt"])
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


def adjudicate_elenchus_finding(
    target_path: str | Path,
    finding_id: str,
    decision: str,
    reason: str = "",
    adjudicator: str = "human-operator",
    ledger_file: Path | None = None,
) -> dict[str, Any]:
    """
    Record an explicit human adjudication (`accept`, `reject`, `escalate`) on a disputed Elenchus cluster.
    Enforces Law L10 anti-rubber-stamp guardrails (`reason >= 12 chars` on reject, no bulk `ALL` reject when
    multiple pending clusters exist, and strict worktree freshness check).
    """
    from .bridge import append_triad_provenance_event

    target_dir, repo_root, subpath = _resolve_target_and_scope(target_path)
    if is_bundled_starter_vault_path(target_dir):
        raise ValueError("Refusing to write Elenchus adjudication inside bundled starter_vault")

    receipt_path = target_dir / "evidence" / "elenchus_challenge.json"
    if not receipt_path.is_file():
        raise FileNotFoundError(f"Missing Elenchus challenge receipt: {receipt_path}. Run `skb elenchus run` first.")

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError(f"Malformed Elenchus challenge receipt: {receipt_path}")
    schema_err = _validate_receipt_nested_schema(receipt)
    if schema_err:
        raise ValueError(f"Malformed Elenchus challenge receipt: {schema_err}")

    bindings = receipt.get("bindings") or {}
    fp = compute_subproject_fingerprint(repo_root, subpath)
    try:
        cur_worktree_sha = _compute_worktree_content_sha(target_dir)
    except OSError as exc:
        raise ValueError(f"Cannot verify worktree state for Elenchus adjudication: {exc}") from exc

    rec_tree = str(bindings.get("tree_sha") or "").strip()
    rec_wt = str(bindings.get("worktree_sha") or "").strip()
    if not rec_tree or not rec_wt or rec_tree != fp["tree_sha"] or rec_wt != cur_worktree_sha:
        raise ValueError(
            "Cannot adjudicate stale Elenchus receipt: repository changed since challenge run. Re-run `skb elenchus run` first."
        )

    goldwood_path = target_dir / "evidence" / "goldwood.json"
    rec_gwi_sha = str(bindings.get("gwi_receipt_sha256") or "").strip()
    if rec_gwi_sha:
        if not goldwood_path.is_file() or _compute_gwi_receipt_sha256(goldwood_path) != rec_gwi_sha:
            raise ValueError(
                "Cannot adjudicate stale Elenchus receipt: `evidence/goldwood.json` changed or disappeared since challenge run."
            )
    elif goldwood_path.is_file():
        raise ValueError(
            "Cannot adjudicate stale Elenchus receipt: `evidence/goldwood.json` appeared after challenge run."
        )

    gate_receipt_path = target_dir / "evidence" / "gate_receipt.json"
    rec_gate_sha = str(bindings.get("gate_receipt_sha256") or "").strip()
    if rec_gate_sha:
        if not gate_receipt_path.is_file() or _compute_gate_receipt_sha256(gate_receipt_path) != rec_gate_sha:
            raise ValueError(
                "Cannot adjudicate stale Elenchus receipt: `evidence/gate_receipt.json` changed or disappeared since challenge run."
            )
    elif gate_receipt_path.is_file():
        raise ValueError(
            "Cannot adjudicate stale Elenchus receipt: `evidence/gate_receipt.json` appeared after challenge run."
        )

    dec_low = (decision or "").strip().lower()
    if dec_low in ("accept", "accept_finding", "confirm", "fix"):
        norm_decision = "ACCEPT_FINDING"
    elif dec_low in ("reject", "reject_finding", "dismiss", "override"):
        norm_decision = "REJECT_FINDING"
    elif dec_low in ("escalate", "hold", "block"):
        norm_decision = "ESCALATE"
    else:
        raise ValueError(f"Unsupported adjudication decision '{decision}'. Use accept, reject, or escalate.")

    clean_reason = (reason or "").strip()
    if norm_decision == "REJECT_FINDING" and len(clean_reason) < MIN_REJECTION_REASON_CHARS:
        raise ValueError(
            f"Explicit rationale (>= {MIN_REJECTION_REASON_CHARS} chars) is required to reject/override a challenger finding."
        )

    clusters = receipt.get("clusters") or []
    fid_clean = (finding_id or "").strip()
    if not fid_clean:
        raise ValueError("`--finding <cluster_id|ALL>` is required for adjudication.")

    if fid_clean.upper() == "ALL":
        pending = [
            c
            for c in clusters
            if c.get("requires_adjudication")
            and str((c.get("adjudication") or {}).get("status") or "") != "ADJUDICATED"
        ]
        if not pending:
            raise ValueError("No pending adjudicable Elenchus clusters remain for 'ALL'.")
        req_clusters = [c for c in clusters if c.get("requires_adjudication")]
        has_blocked = any(
            str((c.get("adjudication") or {}).get("decision") or "").upper() in ("ACCEPT_FINDING", "ESCALATE")
            for c in req_clusters
        )
        if norm_decision == "REJECT_FINDING" and (len(pending) > 1 or len(req_clusters) > 1 or has_blocked):
            raise ValueError(
                "Bulk 'ALL' override is forbidden when multiple disputed clusters exist or any cluster was accepted/escalated; adjudicate each cluster ID explicitly."
            )
        targets = pending
    else:
        targets = [c for c in clusters if str(c.get("cluster_id", "")).upper() == fid_clean.upper()]
        if not targets:
            raise ValueError(f"Unknown Elenchus cluster ID '{fid_clean}'.")

    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    adj_name = (adjudicator or "").strip() or "human-operator"
    for c in targets:
        c["adjudication"] = {
            "status": "ADJUDICATED",
            "decision": norm_decision,
            "adjudicator": adj_name,
            "reason": clean_reason,
            "adjudicated_at": now_iso,
        }

    reviewer_summaries = receipt.get("reviewer_summaries") or []
    panel = compute_effective_panel_size(reviewer_summaries)
    receipt["panel"] = panel
    llm_verdicts = [
        "CHALLENGE"
        if str(r.get("execution_status") or "OK").upper() == "FAILED"
        else str(r.get("verdict") or "CHALLENGE")
        for r in reviewer_summaries
        if str(r.get("lineage") or "") != "deterministic"
    ]
    exec_mode = str(receipt.get("execution_mode") or "explicit")
    rev_bound = bool(receipt.get("review_binding_verified", exec_mode != "reviews_file"))
    new_seal_status = _compute_seal_status(
        panel,
        clusters,
        llm_verdicts,
        execution_mode=exec_mode,
        review_binding_verified=rev_bound,
    )
    receipt["seal_status"] = new_seal_status
    receipt["adjudicated_at"] = now_iso

    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    append_triad_provenance_event(
        event_type="elenchus.adjudicated",
        payload={
            "project_name": receipt.get("project_name", target_dir.name),
            "finding_id": fid_clean.upper() if fid_clean.upper() == "ALL" else targets[0]["cluster_id"],
            "decision": norm_decision,
            "adjudicator": adj_name,
            "seal_status": new_seal_status,
            "tree_sha": fp["tree_sha"],
        },
        ledger_file=ledger_file,
        commit_sha=str(fp["commit_sha"]),
    )

    if new_seal_status in ("READY_UNANIMOUS", "READY_ADJUDICATED"):
        _record_triad_from_elenchus(target_dir, subpath, receipt)

    return receipt


def _safe_float(val: Any, default: float = 0.0) -> float:
    if not _is_real_num(val):
        return default
    try:
        return float(val)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_int(val: Any, default: int | None = None) -> int | None:
    if isinstance(val, bool) or val is None:
        return default
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError):
        return default


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool) and _is_real_num(val)


def _validate_receipt_nested_schema(loaded: dict[str, Any]) -> str | None:
    for bk in ("score_blind", "review_binding_verified"):
        if bk in loaded and not isinstance(loaded[bk], bool):
            return f"Malformed boolean `{bk}` in Elenchus challenge receipt."

    bindings = loaded.get("bindings")
    if not isinstance(bindings, dict):
        return "Missing `bindings` object in Elenchus challenge receipt."
    if "max_diff_chars" in bindings and not _is_strict_int(bindings["max_diff_chars"]):
        return "Malformed `bindings.max_diff_chars` in Elenchus receipt."
    for bk in ("dirty", "diff_truncated", "coverage_complete"):
        if bk in bindings and not isinstance(bindings[bk], bool):
            return f"Malformed boolean `bindings.{bk}` in Elenchus receipt."

    raw_panel = loaded.get("panel")
    if raw_panel is not None:
        if not isinstance(raw_panel, dict):
            return "Malformed `panel` object in Elenchus challenge receipt."
        for bk in ("independent_panel", "gate_closable"):
            if bk in raw_panel and not isinstance(raw_panel[bk], bool):
                return f"Malformed boolean `panel.{bk}` in Elenchus challenge receipt."
        for num_k in ("n_eff", "shannon_entropy", "min_independent_n_eff"):
            if num_k in raw_panel and not _is_real_num(raw_panel[num_k]):
                return f"Malformed numeric `panel.{num_k}` in Elenchus challenge receipt."
        raw_lins = raw_panel.get("distinct_llm_lineages")
        if raw_lins is not None and (not isinstance(raw_lins, list) or not all(isinstance(x, str) for x in raw_lins)):
            return "Malformed `panel.distinct_llm_lineages` in Elenchus receipt."

    raw_metrics = loaded.get("metrics")
    if raw_metrics is not None:
        if not isinstance(raw_metrics, dict):
            return "Malformed `metrics` object in Elenchus challenge receipt."
        if "incomplete_coverage" in raw_metrics and not isinstance(raw_metrics["incomplete_coverage"], bool):
            return "Malformed boolean `metrics.incomplete_coverage` in Elenchus receipt."
        for num_k in ("disagreement_index", "mean_anchoring_ratio", "anchoring_ratio", "prompt_anchoring_ratio"):
            if num_k in raw_metrics and not _is_real_num(raw_metrics[num_k]):
                return f"Malformed numeric `metrics.{num_k}` in Elenchus receipt."
        if "total_clusters" in raw_metrics and not _is_strict_int(raw_metrics["total_clusters"]):
            return "Malformed `metrics.total_clusters` in Elenchus receipt."

    raw_clusters = loaded.get("clusters")
    if raw_clusters is not None:
        if not isinstance(raw_clusters, list) or not all(isinstance(c, dict) for c in raw_clusters):
            return "Malformed `clusters` list in Elenchus challenge receipt."
        for cl in raw_clusters:
            for bk in ("requires_adjudication", "grounded_in_repo"):
                if bk in cl and not isinstance(cl[bk], bool):
                    return f"Malformed boolean `{bk}` in Elenchus cluster record."
            if "line" in cl and not _is_strict_int(cl["line"]):
                return "Malformed `line` in Elenchus cluster record."
            if "anchoring_ratio" in cl and not _is_real_num(cl["anchoring_ratio"]):
                return "Malformed `anchoring_ratio` in Elenchus cluster record."
            if "adjudication" in cl and not isinstance(cl["adjudication"], dict):
                return "Malformed `adjudication` in Elenchus cluster record."
            if "lineages" in cl and (
                not isinstance(cl["lineages"], list) or not all(isinstance(x, str) for x in cl["lineages"])
            ):
                return "Malformed `lineages` in Elenchus cluster record."

    raw_summaries = loaded.get("reviewer_summaries")
    if raw_summaries is not None:
        if not isinstance(raw_summaries, list) or not all(isinstance(rs, dict) for rs in raw_summaries):
            return "Malformed `reviewer_summaries` list in Elenchus challenge receipt."
        for rs in raw_summaries:
            if "findings_count" in rs and not _is_strict_int(rs["findings_count"]):
                return "Malformed `findings_count` in Elenchus `reviewer_summaries`."

    raw_reviews = loaded.get("raw_reviews")
    if raw_reviews is not None:
        if not isinstance(raw_reviews, list) or not all(isinstance(r, dict) for r in raw_reviews):
            return "Malformed `raw_reviews` list in Elenchus challenge receipt."
        for rev in raw_reviews:
            if "findings" in rev:
                r_findings = rev["findings"]
                if not isinstance(r_findings, list) or not all(isinstance(f, dict) for f in r_findings):
                    return "Malformed nested `raw_reviews[].findings` in Elenchus challenge receipt."
                for f in r_findings:
                    if "line" in f and not _is_strict_int(f["line"]):
                        return "Malformed `line` in nested `raw_reviews[].findings`."
            elif "raw_text" not in rev:
                return "Malformed reviewer record in `raw_reviews`: missing `findings` list."

    imp = loaded.get("import_bindings")
    if imp is not None:
        if not isinstance(imp, dict):
            return "Malformed `import_bindings` object in Elenchus challenge receipt."
        item_b = imp.get("item_bindings")
        if item_b is not None and (not isinstance(item_b, list) or not all(isinstance(x, dict) for x in item_b)):
            return "Malformed `import_bindings.item_bindings` in Elenchus challenge receipt."
        if "envelope" in imp and imp["envelope"] is not None and not isinstance(imp["envelope"], dict):
            return "Malformed `import_bindings.envelope` in Elenchus challenge receipt."

    return None


def _corrupted_gate_result(message: str, n_eff: float = 0.0) -> dict[str, Any]:
    return {
        "passed": False,
        "seal_status": "CORRUPTED",
        "reason_code": "CORRUPTED_OR_CONTRADICTORY_RECEIPT",
        "message": message,
        "n_eff": _safe_float(n_eff, 0.0),
        "pending_ids": [],
    }


def evaluate_elenchus_gate(
    target_path: str | Path,
    receipt: dict[str, Any] | None = None,
    gwi_report: dict[str, Any] | None = None,
    require_independent: bool = True,
) -> dict[str, Any]:
    """
    Fail-closed verifier gating `.evr` sealing (`--require-elenchus`) and `organon finish`.
    Checks receipt presence, worktree/tree SHA freshness, gate/GWI/report semantic bindings,
    imported review binding, recomputed cross-lineage independence `N_eff >= 1.80`, and
    contradiction-free human adjudication closure.
    """
    target_dir, repo_root, subpath = _resolve_target_and_scope(target_path)
    loaded = receipt
    if loaded is None:
        r_path = target_dir / "evidence" / "elenchus_challenge.json"
        if not r_path.is_file():
            return {
                "passed": False,
                "seal_status": "MISSING",
                "reason_code": "MISSING_ELENCHUS_RECEIPT",
                "message": "Missing `evidence/elenchus_challenge.json`. Run `skb elenchus run .` before sealing.",
                "n_eff": 0.0,
                "pending_ids": [],
            }
        try:
            loaded = json.loads(r_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return _corrupted_gate_result(f"Malformed `evidence/elenchus_challenge.json`: {exc}")

    if not isinstance(loaded, dict):
        return _corrupted_gate_result("Malformed Elenchus challenge receipt: expected JSON object.")

    raw_panel_hdr = loaded.get("panel")
    n_eff_fallback = _safe_float(raw_panel_hdr.get("n_eff") if isinstance(raw_panel_hdr, dict) else None, 0.0)
    schema_err = _validate_receipt_nested_schema(loaded)
    if schema_err:
        return _corrupted_gate_result(schema_err, n_eff_fallback)
    bindings = loaded["bindings"]

    rec_tree_sha = str(bindings.get("tree_sha") or "").strip()
    rec_worktree_sha = str(bindings.get("worktree_sha") or "").strip()
    if not rec_tree_sha or not rec_worktree_sha:
        return {
            "passed": False,
            "seal_status": "STALE",
            "reason_code": "STALE_WORKTREE",
            "message": "Elenchus challenge receipt lacks `tree_sha` or `worktree_sha` binding.",
            "n_eff": n_eff_fallback,
            "pending_ids": [],
        }

    fp = compute_subproject_fingerprint(repo_root, subpath)
    try:
        cur_worktree_sha = _compute_worktree_content_sha(target_dir)
    except OSError as exc:
        return {
            "passed": False,
            "seal_status": "STALE",
            "reason_code": "STALE_WORKTREE",
            "message": f"Cannot verify worktree content digest cleanly: {exc}",
            "n_eff": n_eff_fallback,
            "pending_ids": [],
        }
    if rec_tree_sha != fp["tree_sha"] or rec_worktree_sha != cur_worktree_sha:
        return {
            "passed": False,
            "seal_status": "STALE",
            "reason_code": "STALE_WORKTREE",
            "message": "Elenchus challenge receipt is stale: repository files/tree changed since challenge run.",
            "n_eff": n_eff_fallback,
            "pending_ids": [],
        }

    gate_receipt_path = target_dir / "evidence" / "gate_receipt.json"
    rec_gate_sha = str(bindings.get("gate_receipt_sha256") or "").strip()
    if rec_gate_sha:
        if not gate_receipt_path.is_file():
            return {
                "passed": False,
                "seal_status": "MISSING_GATE",
                "reason_code": "MISSING_GATE_RECEIPT",
                "message": "Bound `evidence/gate_receipt.json` was removed after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        try:
            cur_gate_sha = _compute_gate_receipt_sha256(gate_receipt_path)
        except OSError as exc:
            return {
                "passed": False,
                "seal_status": "INVALID_GATE",
                "reason_code": "INVALID_GATE_RECEIPT",
                "message": f"Cannot hash bound `evidence/gate_receipt.json`: {exc}",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if cur_gate_sha != rec_gate_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GATE",
                "reason_code": "STALE_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` changed after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }

    if gate_receipt_path.is_file():
        try:
            gr_obj = json.loads(gate_receipt_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "passed": False,
                "seal_status": "INVALID_GATE",
                "reason_code": "INVALID_GATE_RECEIPT",
                "message": f"Malformed `evidence/gate_receipt.json`: {exc}",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        gr_ec = gr_obj.get("exit_code") if isinstance(gr_obj, dict) else None
        gr_fft = gr_obj.get("fresh_for_tree") if isinstance(gr_obj, dict) else None
        if (
            not isinstance(gr_obj, dict)
            or gr_obj.get("passed") is not True
            or gr_obj.get("executed") is not True
            or not _is_strict_int(gr_ec)
            or gr_ec != 0
            or ("fresh_for_tree" in gr_obj and gr_fft is not None and not isinstance(gr_fft, bool))
        ):
            return {
                "passed": False,
                "seal_status": "INVALID_GATE",
                "reason_code": "INVALID_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` did not execute and pass cleanly (literal true passed/executed and int exit_code==0 required).",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        raw_gr_tree = gr_obj.get("tree_sha")
        raw_gr_wt = gr_obj.get("worktree_sha")
        gr_tree = raw_gr_tree.strip() if isinstance(raw_gr_tree, str) else ""
        gr_wt = raw_gr_wt.strip() if isinstance(raw_gr_wt, str) else ""
        if not gr_tree or gr_tree != fp["tree_sha"] or not gr_wt or gr_wt != cur_worktree_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GATE",
                "reason_code": "STALE_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` tree_sha or worktree_sha does not match current repository state.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if not bool(fp.get("dirty")) and gr_obj.get("fresh_for_tree") is False:
            return {
                "passed": False,
                "seal_status": "STALE_GATE",
                "reason_code": "STALE_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` reports `fresh_for_tree=False`.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if not rec_gate_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GATE",
                "reason_code": "STALE_GATE_RECEIPT",
                "message": "`evidence/gate_receipt.json` appeared after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }

    goldwood_path = target_dir / "evidence" / "goldwood.json"
    rec_gwi_sha = str(bindings.get("gwi_receipt_sha256") or "").strip()
    rec_gwi_sem_sha = str(bindings.get("gwi_semantic_sha256") or "").strip()
    rec_proj = str(loaded.get("project_name") or "").strip()
    if rec_gwi_sha:
        if not goldwood_path.is_file():
            return {
                "passed": False,
                "seal_status": "MISSING_GWI",
                "reason_code": "MISSING_GWI_RECEIPT",
                "message": "Bound `evidence/goldwood.json` was removed after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if _compute_gwi_receipt_sha256(goldwood_path) != rec_gwi_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": "Elenchus challenge receipt is stale: `evidence/goldwood.json` metrics changed since challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
    if goldwood_path.is_file():
        if not rec_gwi_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": "`evidence/goldwood.json` appeared after Elenchus challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        try:
            g_disk = json.loads(goldwood_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": f"Malformed `evidence/goldwood.json`: {exc}",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if not isinstance(g_disk, dict):
            return _corrupted_gate_result("Malformed `evidence/goldwood.json`: expected JSON object.", n_eff_fallback)
        disk_contra = _detect_gwi_schema_contradiction(g_disk)
        if disk_contra:
            return _corrupted_gate_result(disk_contra, n_eff_fallback)
        if rec_gwi_sha and rec_gwi_sem_sha and _compute_gwi_semantic_sha256(g_disk) != rec_gwi_sem_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": "`evidence/goldwood.json` semantic metrics drifted since challenge run.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        g_disk_sem = _canonical_gwi_semantics(g_disk)
        if g_disk_sem["status"] != "PASS":
            return {
                "passed": False,
                "seal_status": "GWI_FAIL",
                "reason_code": "GWI_NOT_PASS",
                "message": f"`evidence/goldwood.json` status is '{g_disk_sem['status']}' (expected PASS).",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if g_disk_sem["project"] and rec_proj and g_disk_sem["project"] != rec_proj:
            return {
                "passed": False,
                "seal_status": "GWI_MISMATCH",
                "reason_code": "GWI_REPORT_MISMATCH",
                "message": f"`evidence/goldwood.json` project '{g_disk_sem['project']}' does not match '{rec_proj}'.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }

    if gwi_report is not None and not isinstance(gwi_report, dict):
        return _corrupted_gate_result("Malformed GWI report: expected dict.", n_eff_fallback)

    if isinstance(gwi_report, dict):
        rep_contra = _detect_gwi_schema_contradiction(gwi_report)
        if rep_contra:
            return _corrupted_gate_result(rep_contra, n_eff_fallback)
        g_sem = _canonical_gwi_semantics(gwi_report)
        if g_sem["status"] != "PASS":
            return {
                "passed": False,
                "seal_status": "GWI_FAIL",
                "reason_code": "GWI_NOT_PASS",
                "message": f"Cannot close Elenchus sealing gate while GWI status is '{g_sem['status'] or 'MISSING'}' (expected PASS).",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if g_sem["project"] and rec_proj and g_sem["project"] != rec_proj:
            return {
                "passed": False,
                "seal_status": "GWI_MISMATCH",
                "reason_code": "GWI_REPORT_MISMATCH",
                "message": f"GWI report project '{g_sem['project']}' does not match Elenchus target '{rec_proj}'.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        if rec_gwi_sem_sha and _compute_gwi_semantic_sha256(gwi_report) != rec_gwi_sem_sha:
            return {
                "passed": False,
                "seal_status": "STALE_GWI",
                "reason_code": "STALE_GWI_RECEIPT",
                "message": "Sealed GWI report semantics differ from the `evidence/goldwood.json` bound into Elenchus.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }

    raw_reviews = loaded.get("raw_reviews")
    if not isinstance(raw_reviews, list) or not raw_reviews or not all(isinstance(r, dict) for r in raw_reviews):
        return _corrupted_gate_result(
            "Elenchus receipt lacks valid `raw_reviews` evidence for independent reconstruction.",
            n_eff_fallback,
        )

    exec_mode = str(loaded.get("execution_mode") or "explicit")
    if exec_mode not in _VALID_EXECUTION_MODES:
        return _corrupted_gate_result(f"Unsupported Elenchus `execution_mode`: {exec_mode!r}.", n_eff_fallback)
    if exec_mode == "offline" and any(
        classify_model_lineage(str(r.get("model_id") or r.get("model") or "")) != "deterministic" for r in raw_reviews
    ):
        return _corrupted_gate_result(
            "`execution_mode='offline'` contradicts non-deterministic reviewer entries in `raw_reviews`.",
            n_eff_fallback,
        )

    raw_budget = bindings.get("max_diff_chars")
    if "max_diff_chars" not in bindings or isinstance(raw_budget, bool) or raw_budget is None:
        return _corrupted_gate_result("Elenchus receipt lacks valid `bindings.max_diff_chars` budget.", n_eff_fallback)
    if not _is_strict_int(raw_budget):
        return _corrupted_gate_result("Malformed `bindings.max_diff_chars` in Elenchus receipt.", n_eff_fallback)
    max_chars_val = raw_budget
    if max_chars_val <= 0 or max_chars_val > DEFAULT_MAX_DIFF_CHARS:
        return _corrupted_gate_result(
            f"`bindings.max_diff_chars` ({max_chars_val}) is outside allowed bounds (1..{DEFAULT_MAX_DIFF_CHARS}).",
            n_eff_fallback,
        )

    rec_diff_sha = str(bindings.get("diff_sha256") or "").strip()
    rec_pkt_sha = str(bindings.get("packet_sha256") or "").strip()
    if not rec_diff_sha or not rec_pkt_sha:
        return _corrupted_gate_result(
            "Elenchus receipt lacks `bindings.diff_sha256` or `bindings.packet_sha256` integrity digest.",
            n_eff_fallback,
        )

    live_diff, _, live_trunc = _collect_bounded_diff(repo_root, bool(fp.get("dirty")), max_diff_chars=max_chars_val)
    live_diff_sha = hashlib.sha256(live_diff.encode("utf-8")).hexdigest()
    if live_diff_sha != rec_diff_sha:
        return _corrupted_gate_result(
            "Stored `bindings.diff_sha256` contradicts reconstructed repository diff.",
            n_eff_fallback,
        )
    if bool(bindings.get("diff_truncated", False)) != bool(live_trunc) or (
        bool(bindings.get("coverage_complete", not live_trunc)) != (not bool(live_trunc))
    ):
        return _corrupted_gate_result(
            "Stored `bindings.diff_truncated` or `coverage_complete` contradicts current diff truncation state.",
            n_eff_fallback,
        )

    cur_gwi_now = _compute_gwi_receipt_sha256(goldwood_path)
    cur_gate_now = _compute_gate_receipt_sha256(gate_receipt_path)
    if cur_gwi_now == rec_gwi_sha and cur_gate_now == rec_gate_sha:
        intent_str = str(loaded.get("intent") or "").strip()
        if not intent_str:
            return _corrupted_gate_result(
                "Elenchus receipt lacks non-empty `intent` for packet reconstruction.", n_eff_fallback
            )
        live_pkt = build_elenchus_challenge_packet(
            target_dir,
            intent=intent_str,
            blind_scores=bool(loaded.get("score_blind", True)),
            max_diff_chars=max_chars_val,
        )
        if str((live_pkt.get("bindings") or {}).get("packet_sha256") or "") != rec_pkt_sha:
            return _corrupted_gate_result(
                "Stored `bindings.packet_sha256` contradicts reconstructed challenge packet.",
                n_eff_fallback,
            )

    if exec_mode != "reviews_file" and bool(loaded.get("import_bindings")):
        return _corrupted_gate_result(
            "Non-empty `import_bindings` contradicts non-import `execution_mode`.",
            n_eff_fallback,
        )

    if exec_mode == "reviews_file":
        imp = loaded.get("import_bindings")
        if not isinstance(imp, dict):
            return {
                "passed": False,
                "seal_status": "UNBOUND_IMPORT_ADVISORY",
                "reason_code": "UNBOUND_REVIEW_IMPORT",
                "message": "Imported `--reviews` receipt lacks persisted binding proof; advisory only.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
        item_b = imp.get("item_bindings")
        if (
            not isinstance(item_b, list)
            or len(item_b) != len(raw_reviews)
            or not all(isinstance(x, dict) for x in item_b)
        ):
            return _corrupted_gate_result(
                "`import_bindings.item_bindings` cardinality does not match `raw_reviews`.",
                n_eff_fallback,
            )
        try:
            imp_verified = _verify_review_bindings(
                item_b,
                dict(imp.get("envelope") or {}),
                bindings,
                is_file_import=True,
            )
        except ValueError as exc:
            return _corrupted_gate_result(f"Imported review binding contradiction: {exc}", n_eff_fallback)
        if not imp_verified or loaded.get("review_binding_verified") is not True:
            return {
                "passed": False,
                "seal_status": "UNBOUND_IMPORT_ADVISORY",
                "reason_code": "UNBOUND_REVIEW_IMPORT",
                "message": "Imported `--reviews` transcript lacks verified packet/worktree binding; advisory only.",
                "n_eff": n_eff_fallback,
                "pending_ids": [],
            }
    elif "review_binding_verified" in loaded and loaded.get("review_binding_verified") is not True:
        return {
            "passed": False,
            "seal_status": "UNBOUND_IMPORT_ADVISORY",
            "reason_code": "UNBOUND_REVIEW_IMPORT",
            "message": "Elenchus review binding flag is unverified; advisory only.",
            "n_eff": n_eff_fallback,
            "pending_ids": [],
        }

    reviewer_summaries = loaded.get("reviewer_summaries")
    if not isinstance(reviewer_summaries, list) or not reviewer_summaries:
        return _corrupted_gate_result("Elenchus receipt lacks valid `reviewer_summaries` evidence.", n_eff_fallback)

    expected_syn = synthesize_elenchus_verdict(
        raw_reviews,
        {
            "target_dir": str(target_dir),
            "repo_root": str(repo_root),
            "intent": str(loaded.get("intent") or ""),
            "prompt_instructions_and_intent": _build_instructions_block(str(loaded.get("intent") or "")),
            "bindings": bindings,
        },
        target_dir=target_dir,
        execution_mode=exec_mode,
        review_binding_verified=True,
    )
    exp_summaries = expected_syn["reviewer_summaries"]
    if len(reviewer_summaries) != len(exp_summaries):
        return _corrupted_gate_result("`reviewer_summaries` count contradicts `raw_reviews`.", n_eff_fallback)
    for rs, es in zip(reviewer_summaries, exp_summaries):
        rs_count = _safe_int(rs.get("findings_count", -1)) if isinstance(rs, dict) else None
        if (
            not isinstance(rs, dict)
            or rs_count is None
            or (
                str(rs.get("model_id") or "").strip(),
                classify_model_lineage(str(rs.get("model_id") or "")),
                str(rs.get("verdict") or "").upper(),
                str(rs.get("execution_status") or "OK").upper(),
                rs_count,
            )
            != (
                es["model_id"],
                es["lineage"],
                es["verdict"],
                es["execution_status"],
                es["findings_count"],
            )
        ):
            return _corrupted_gate_result("Stored `reviewer_summaries` contradicts `raw_reviews`.", n_eff_fallback)

    recomputed_panel = expected_syn["panel"]
    stored_panel = loaded.get("panel")
    if not isinstance(stored_panel, dict):
        return _corrupted_gate_result("Elenchus receipt lacks valid `panel` object.", float(recomputed_panel["n_eff"]))

    raw_lins = stored_panel.get("distinct_llm_lineages") or []
    if not isinstance(raw_lins, list):
        return _corrupted_gate_result("Malformed `panel.distinct_llm_lineages` in Elenchus receipt.")
    stored_lins = sorted(str(x) for x in raw_lins)
    stored_n_eff = stored_panel.get("n_eff")
    if not _is_real_num(stored_n_eff):
        return _corrupted_gate_result(
            "Malformed `panel.n_eff` in Elenchus receipt.",
            float(recomputed_panel["n_eff"]),
        )
    if (
        bool(stored_panel.get("independent_panel", False)) != bool(recomputed_panel["independent_panel"])
        or abs(float(stored_n_eff) - float(recomputed_panel["n_eff"])) > 1e-3
        or stored_lins != list(recomputed_panel["distinct_llm_lineages"])
    ):
        return _corrupted_gate_result(
            "Stored Elenchus panel independence metrics contradict reconstructed `raw_reviews` panel.",
            float(recomputed_panel["n_eff"]),
        )

    n_eff = float(recomputed_panel["n_eff"])
    if require_independent and not recomputed_panel["independent_panel"]:
        return {
            "passed": False,
            "seal_status": str(loaded.get("seal_status") or "DEGRADED_NON_INDEPENDENT"),
            "reason_code": "NON_INDEPENDENT_PANEL",
            "message": (
                f"Elenchus panel is non-independent (mode={recomputed_panel['panel_mode']}, N_eff={n_eff:.2f} < {MIN_INDEPENDENT_N_EFF:.2f}). "
                "Cross-lineage review is required to close `--require-elenchus`."
            ),
            "n_eff": n_eff,
            "pending_ids": [],
        }

    clusters = loaded.get("clusters")
    if not isinstance(clusters, list):
        return _corrupted_gate_result("Elenchus receipt `clusters` must be a list.", n_eff)

    exp_clusters = expected_syn["clusters"]
    if len(clusters) != len(exp_clusters):
        return _corrupted_gate_result(
            f"Stored `clusters` count ({len(clusters)}) contradicts reconstructed cluster count ({len(exp_clusters)}).",
            n_eff,
        )
    stored_metrics = loaded.get("metrics")
    if isinstance(stored_metrics, dict) and "total_clusters" in stored_metrics:
        met_tot = _safe_int(stored_metrics.get("total_clusters"))
        if met_tot is None or met_tot != len(clusters):
            return _corrupted_gate_result("Stored `metrics.total_clusters` contradicts `clusters` length.", n_eff)

    for sc, ec in zip(clusters, exp_clusters):
        sc_line = _safe_int(sc.get("line", 0)) if isinstance(sc, dict) else None
        if not isinstance(sc, dict) or sc_line is None:
            return _corrupted_gate_result("Malformed cluster record in Elenchus receipt.", n_eff)
        if (
            str(sc.get("cluster_id") or ""),
            str(sc.get("severity") or "").upper(),
            str(sc.get("file_path") or ""),
            sc_line,
            str(sc.get("symbol") or ""),
            str(sc.get("claim") or ""),
            bool(sc.get("requires_adjudication")),
        ) != (
            ec["cluster_id"],
            ec["severity"],
            ec["file_path"],
            ec["line"],
            ec["symbol"],
            ec["claim"],
            ec["requires_adjudication"],
        ):
            return _corrupted_gate_result(
                f"Stored cluster `{sc.get('cluster_id')}` was altered relative to reconstructed `raw_reviews`.",
                n_eff,
            )
        if ec["signal_class"] in ("LINEAGE_SPLIT", "INCOMPLETE_COVERAGE", "SINGLE_LINEAGE_NOTE") and (
            str(sc.get("signal_class") or "") != ec["signal_class"]
        ):
            return _corrupted_gate_result(
                f"Stored cluster `{sc.get('cluster_id')}` signal_class disagrees with reconstructed `raw_reviews`.",
                n_eff,
            )

    for c in clusters:
        if not isinstance(c, dict):
            return _corrupted_gate_result("Malformed cluster record in Elenchus receipt.", n_eff)
        sev = str(c.get("severity") or "").upper()
        sig = str(c.get("signal_class") or "").upper()
        mandatory_adj = (sev in ("P0", "P1")) or (
            sig in ("LINEAGE_SPLIT", "INCOMPLETE_COVERAGE") and sev in ("P0", "P1", "P2")
        )
        if mandatory_adj and not bool(c.get("requires_adjudication")):
            return _corrupted_gate_result(
                f"Mandatory disputed cluster `{c.get('cluster_id')}` had `requires_adjudication=False`.",
                n_eff,
            )
        adj = c.get("adjudication")
        if not isinstance(adj, dict):
            return _corrupted_gate_result(f"Cluster `{c.get('cluster_id')}` lacks adjudication object.", n_eff)
        adj_status = str(adj.get("status") or "")
        dec = str(adj.get("decision") or "").upper()
        reason = str(adj.get("reason") or "").strip()
        if adj_status == "ADJUDICATED":
            if dec not in ("ACCEPT_FINDING", "REJECT_FINDING", "ESCALATE"):
                return _corrupted_gate_result(
                    f"Cluster `{c.get('cluster_id')}` has invalid adjudication decision `{dec}`.",
                    n_eff,
                )
            if dec == "REJECT_FINDING" and len(reason) < MIN_REJECTION_REASON_CHARS:
                return _corrupted_gate_result(
                    f"Cluster `{c.get('cluster_id')}` was rejected without a >= {MIN_REJECTION_REASON_CHARS}-char rationale.",
                    n_eff,
                )
        elif dec:
            return _corrupted_gate_result(
                f"Cluster `{c.get('cluster_id')}` specifies decision `{dec}` while status is `{adj_status}`.",
                n_eff,
            )

    llm_verdicts = [
        "CHALLENGE"
        if str(r.get("execution_status") or "OK").upper() == "FAILED"
        else str(r.get("verdict") or "CHALLENGE").upper()
        for r in reviewer_summaries
        if classify_model_lineage(str(r.get("model_id") or "")) != "deterministic"
    ]
    expected_seal = _compute_seal_status(
        recomputed_panel,
        clusters,
        llm_verdicts,
        execution_mode=exec_mode,
        review_binding_verified=True,
    )
    stored_seal = str(loaded.get("seal_status") or "UNKNOWN")
    if stored_seal != expected_seal:
        return _corrupted_gate_result(
            f"Stored `seal_status` ('{stored_seal}') contradicts recomputed state ('{expected_seal}').",
            n_eff,
        )

    pending_ids = [
        str(c.get("cluster_id"))
        for c in clusters
        if c.get("requires_adjudication") and str((c.get("adjudication") or {}).get("status") or "") != "ADJUDICATED"
    ]
    blocked_ids = [
        str(c.get("cluster_id"))
        for c in clusters
        if str((c.get("adjudication") or {}).get("decision") or "").upper() in ("ACCEPT_FINDING", "ESCALATE")
    ]

    if expected_seal not in ("READY_UNANIMOUS", "READY_ADJUDICATED") or blocked_ids or pending_ids:
        action_ids = blocked_ids or pending_ids
        return {
            "passed": False,
            "seal_status": expected_seal,
            "reason_code": expected_seal,
            "message": (
                f"Elenchus seal status is '{expected_seal}' (disputed/blocking clusters: {action_ids or ['verdict-split']}). "
                "Resolve defects or adjudicate via `skb elenchus adjudicate`."
            ),
            "n_eff": n_eff,
            "pending_ids": action_ids,
        }

    return {
        "passed": True,
        "seal_status": expected_seal,
        "reason_code": "OK",
        "message": f"Elenchus gate passed ({expected_seal}, N_eff={n_eff:.2f}).",
        "n_eff": n_eff,
        "pending_ids": [],
    }


def _print_elenchus_report(receipt: dict[str, Any]) -> None:
    panel = receipt.get("panel") if isinstance(receipt.get("panel"), dict) else {}
    metrics = receipt.get("metrics") if isinstance(receipt.get("metrics"), dict) else {}
    raw_lins = panel.get("distinct_llm_lineages")
    lins_list = [str(x) for x in raw_lins] if isinstance(raw_lins, list) and raw_lins else ["deterministic"]
    print(f"=== Elenchus Pre-Seal Adversarial Challenge ({receipt.get('project_name')}) ===")
    print(
        f"  Seal Status   : {receipt.get('seal_status')} | Mode={panel.get('panel_mode')} | "
        f"N_eff={_safe_float(panel.get('n_eff'), 1.0):.2f} (lineages={', '.join(lins_list)})"
    )
    if panel.get("warning"):
        print(f"  Warning       : {panel['warning']}")
    print(
        f"  Disagreements : clusters={metrics.get('total_clusters', 0)} | "
        f"convergence={metrics.get('independent_convergence_count', 0)} | "
        f"echo={metrics.get('prompt_anchored_echo_count', 0)} | "
        f"split={metrics.get('lineage_split_count', 0)} | "
        f"disagreement_index={_safe_float(metrics.get('disagreement_index'), 0.0):.2f}"
    )
    bindings = receipt.get("bindings") if isinstance(receipt.get("bindings"), dict) else {}
    print(
        f"  Bindings      : commit={bindings.get('commit_sha')} | tree={bindings.get('tree_sha')} | "
        f"worktree={bindings.get('worktree_sha')} | score_blind={receipt.get('score_blind')}"
    )
    raw_clusters = receipt.get("clusters")
    for c in raw_clusters if isinstance(raw_clusters, list) else []:
        if not isinstance(c, dict):
            continue
        adj = c.get("adjudication") if isinstance(c.get("adjudication"), dict) else {}
        loc = f"{c['file_path']}:{c['line']}" if c.get("file_path") else "(repo-wide)"
        adj_lbl = adj.get("decision") or adj.get("status") or "PENDING"
        print(
            f"  • [{c.get('severity', 'P1')}] {c.get('cluster_id', '')} ({c.get('signal_class', '')}, "
            f"alpha={_safe_float(c.get('anchoring_ratio'), 0.0):.2f}) @ {loc} [{adj_lbl}]: {c.get('claim', '')}"
        )


def register_elenchus_subparser(subparsers: Any, show_all_aliases: bool = False) -> None:
    """Register `elenchus` CLI subcommand onto `skb` / `organon` parser."""
    e_help = (
        "Pre-seal cross-lineage adversarial challenge, independence calibration & human adjudication gate"
        if show_all_aliases
        else None
    )
    p = subparsers.add_parser("elenchus", **({"help": e_help} if e_help else {}))
    p.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=("status", "run", "adjudicate", "prompt"),
        help="Elenchus action (`status`, `run`, `adjudicate`, `prompt`)",
    )
    p.add_argument("target", nargs="?", default=".", help="Target repository or project path (default: .)")
    p.add_argument("--intent", default="", help="Stated task intent to challenge against implementation")
    p.add_argument("--reviews", default="", help="Path to JSON review array or parallel `oc triad` transcript")
    p.add_argument(
        "--runner",
        choices=("auto", "oc", "http", "offline"),
        default="auto",
        help="Reviewer execution backend (`auto`, `oc`, `http`, `offline`)",
    )
    p.add_argument("--offline", action="store_true", help="Shortcut for `--runner offline`")
    p.add_argument(
        "--http-endpoint",
        action="append",
        default=[],
        help="OpenAI-compatible endpoint in `model_id=http://.../v1/chat/completions` format (repeatable)",
    )
    p.add_argument(
        "--include-scores",
        action="store_true",
        help="Include numeric GWI scores in challenge prompt (disables default score-blind mode)",
    )
    p.add_argument("--finding", default="", help="Disputed cluster ID (e.g. `ELC-001`) or `ALL` for `adjudicate`")
    p.add_argument("--decision", default="", help="Human decision (`accept`, `reject`, `escalate`) for `adjudicate`")
    p.add_argument("--reason", default="", help="Explicit human rationale (>= 12 chars required for `reject`)")
    p.add_argument("--by", default="human-operator", help="Human adjudicator identifier")
    p.add_argument("--strict", action="store_true", help="Exit with code 1 if gate does not pass")
    p.add_argument("--json", action="store_true", help="Output machine-readable JSON")


def cmd_elenchus(args: argparse.Namespace) -> None:
    action = getattr(args, "action", "status") or "status"
    target = getattr(args, "target", ".") or "."
    as_json = bool(getattr(args, "json", False))
    target_dir, _, _ = _resolve_target_and_scope(target)

    if action == "prompt":
        packet = build_elenchus_challenge_packet(
            target_dir,
            intent=str(getattr(args, "intent", "") or ""),
            blind_scores=not bool(getattr(args, "include_scores", False)),
        )
        if as_json:
            print(json.dumps(packet, indent=2))
        else:
            print(packet["prompt"])
        return

    if action == "status":
        r_path = target_dir / "evidence" / "elenchus_challenge.json"
        loaded: dict[str, Any] | None = None
        if r_path.is_file():
            try:
                raw_loaded = json.loads(r_path.read_text(encoding="utf-8"))
                if isinstance(raw_loaded, dict):
                    loaded = raw_loaded
            except Exception:
                loaded = None
        gate = evaluate_elenchus_gate(target_dir, receipt=loaded, require_independent=True)
        payload = {"receipt": loaded, "gate": gate}
        if as_json:
            print(json.dumps(payload, indent=2))
        else:
            if isinstance(loaded, dict):
                _print_elenchus_report(loaded)
                print(f"  Gate Check    : {'PASS' if gate['passed'] else 'BLOCKED'} ({gate['reason_code']})")
            else:
                print(f"=== Elenchus Status ({target_dir.name}): {gate['seal_status']} ===")
                print(f"  {gate['message']}")
        if getattr(args, "strict", False) and not gate["passed"]:
            sys.exit(1)
        return

    if action == "run":
        runner = "offline" if getattr(args, "offline", False) else str(getattr(args, "runner", "auto") or "auto")
        reviews_arg = str(getattr(args, "reviews", "") or "").strip() or None
        try:
            receipt = run_elenchus_challenge(
                target_dir,
                intent=str(getattr(args, "intent", "") or ""),
                reviews_file=reviews_arg,
                runner=runner,
                http_endpoints=list(getattr(args, "http_endpoint", []) or []),
                blind_scores=not bool(getattr(args, "include_scores", False)),
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            if as_json:
                print(json.dumps({"error": str(exc)}, indent=2))
            else:
                print(f"[elenchus run] Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if as_json:
            print(json.dumps(receipt, indent=2))
        else:
            _print_elenchus_report(receipt)
        if getattr(args, "strict", False) and receipt.get("seal_status") not in (
            "READY_UNANIMOUS",
            "READY_ADJUDICATED",
        ):
            sys.exit(1)
        return

    if action == "adjudicate":
        try:
            receipt = adjudicate_elenchus_finding(
                target_dir,
                finding_id=str(getattr(args, "finding", "") or ""),
                decision=str(getattr(args, "decision", "") or ""),
                reason=str(getattr(args, "reason", "") or ""),
                adjudicator=str(getattr(args, "by", "human-operator") or "human-operator"),
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            if as_json:
                print(json.dumps({"error": str(exc)}, indent=2))
            else:
                print(f"[elenchus adjudicate] Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if as_json:
            print(json.dumps(receipt, indent=2))
        else:
            _print_elenchus_report(receipt)
        if getattr(args, "strict", False) and receipt.get("seal_status") not in (
            "READY_UNANIMOUS",
            "READY_ADJUDICATED",
        ):
            sys.exit(1)
        return


__all__ = [
    "ANCHOR_ECHO_THRESHOLD",
    "ELENCHUS_PROTOCOL_VERSION",
    "MIN_INDEPENDENT_N_EFF",
    "MIN_REJECTION_REASON_CHARS",
    "adjudicate_elenchus_finding",
    "build_elenchus_challenge_packet",
    "classify_model_lineage",
    "cmd_elenchus",
    "compute_effective_panel_size",
    "evaluate_elenchus_gate",
    "parse_oc_triad_stdout",
    "parse_reviewer_output",
    "register_elenchus_subparser",
    "run_elenchus_challenge",
    "synthesize_elenchus_verdict",
]
