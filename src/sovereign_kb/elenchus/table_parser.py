"""GFM Markdown table and prose finding extractors for Elenchus reviews."""

from __future__ import annotations

import re

from .constants import (
    _FINDING_PATH_RE,
    _GFM_DELIM_ROW_RE,
    _HDR_P0_COUNT_RE,
    _HDR_P1_COUNT_RE,
    _POS_SEV_COUNT_CELL_RE,
    _PROSE_SEV_PATTERNS,
    _TABLE_AUDIT_PASS_RE,
    _TABLE_CELL_SEV_RE,
    _TABLE_CHECKMARK_ONLY_RE,
    _TABLE_CLAIM_DATA_HEADER_COLS,
    _TABLE_DEFECT_RESOLVED_RE,
    _TABLE_HEADER_LABELS,
    _TABLE_SEV_HEADER_COLS,
    _TABLE_STATUS_HEADER_COLS,
    _TAG_ONLY_SEV_LINE_RE,
    _WORD_SEV_COUNT_MAP,
)
from .prose_rules import _is_non_defect_summary_line, _strip_closing_atx_hashes, _strip_inline_formatting


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
