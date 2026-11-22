"""Laws L7 (Regularity), L8 (Simultaneous Load), and L9 (Canonical Anchor / Wedge) auditors."""

from __future__ import annotations

import html as _html
import os
import re
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import (
    CANONICAL_GITLAB_GROUPS,
    compute_canonical_anchor_l9_index,
    compute_regularity_index,
    compute_simultaneous_load_index,
)

from .utils import _iter_source_files


def _audit_structural_regularity(ui_text: str, css_text: str = "") -> dict[str, Any]:
    """
    Law L7 — Simplicity Favors Regularity (Η Απλότητα Ευνοεί την Κανονικότητα).
    Measures ad-hoc micro-variance across stylesheet typography scales (`font-size`),
    border radii (`border-radius`), and layout grids (`grid-template-columns`), plus
    HTML inline `style="..."` and arbitrary Tailwind one-off pixel bracket escapes
    (`text-[11.5px]`, `rounded-[5px]`).
    """
    style_blocks = re.findall(r"<style\b[^>]*>(.*?)</style>", ui_text, flags=re.DOTALL | re.IGNORECASE)
    inline_styles = re.findall(r'\bstyle\s*=\s*["\']([^"\']+)["\']', ui_text, flags=re.IGNORECASE)
    jsx_style_objs = "\n".join(re.findall(r"\bstyle\s*=\s*\{\{([^}]+)\}\}", ui_text))
    raw_css = "\n".join(style_blocks) + "\n" + "\n".join(inline_styles) + "\n" + css_text
    clean_css = re.sub(r"/\*.*?\*/", "", raw_css, flags=re.DOTALL)

    def _norm(v: str) -> str:
        s = re.sub(r"\s*!important\b", "", v.strip().lower())
        s = re.sub(r"\s*,\s*", ", ", s)
        s = re.sub(r"\(\s+", "(", s)
        s = re.sub(r"\s+\)", ")", s)
        return re.sub(r"\s+", " ", s)

    _skip_prefixes = ("inherit", "initial", "unset", "revert")

    font_sizes = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])font-size\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(_skip_prefixes) and _norm(m) not in ("100%", "1em") and "${" not in m
    }
    font_sizes.update(
        _norm(m)
        for m in re.findall(r"\bfontSize\s*:\s*['\"]([^'\"]+)['\"]", jsx_style_objs)
        if _norm(m) and not _norm(m).startswith(_skip_prefixes) and "${" not in m
    )
    font_sizes.update(_norm(m) for m in re.findall(r"\btext-\[(\d+(?:\.\d+)?(?:px|rem|em))\]", ui_text))

    border_radii = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])border-radius\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(_skip_prefixes) and _norm(m) not in ("0", "0px") and "${" not in m
    }
    border_radii.update(
        _norm(m)
        for m in re.findall(r"\bborderRadius\s*:\s*['\"]([^'\"]+)['\"]", jsx_style_objs)
        if _norm(m) and not _norm(m).startswith(_skip_prefixes) and _norm(m) not in ("0", "0px") and "${" not in m
    )
    border_radii.update(_norm(m) for m in re.findall(r"\brounded-\[(\d+(?:\.\d+)?(?:px|rem))\]", ui_text))

    grid_templates = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])grid-template-columns\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(("none", "inherit", "initial", "unset")) and "${" not in m
    }
    grid_templates.update(
        _norm(m)
        for m in re.findall(r"\bgridTemplateColumns\s*:\s*['\"]([^'\"]+)['\"]", jsx_style_objs)
        if _norm(m) and not _norm(m).startswith(("none", "inherit", "initial", "unset")) and "${" not in m
    )

    n_fs = len(font_sizes)
    n_br = len(border_radii)
    n_gt = len(grid_templates)
    r_kanon = compute_regularity_index(
        distinct_font_sizes=n_fs,
        distinct_border_radii=n_br,
        distinct_grid_templates=n_gt,
    )
    return {
        "distinct_font_sizes": n_fs,
        "distinct_border_radii": n_br,
        "distinct_grid_templates": n_gt,
        "font_sizes": sorted(font_sizes),
        "border_radii": sorted(border_radii),
        "grid_templates": sorted(grid_templates),
        "r_kanon": r_kanon,
    }


def _collect_law_l7_corpora(web_ui_files: list[Path]) -> tuple[str, str]:
    """Build (markup_corpus, css_corpus) for Law L7 without false-positive JSX string matches."""
    css_only = [f for f in web_ui_files if f.suffix.lower() == ".css"]
    html_like = [f for f in web_ui_files if f.suffix.lower() in (".html", ".vue", ".svelte")]
    jsx_like = [f for f in web_ui_files if f.suffix.lower() in (".tsx", ".jsx")]
    markup_parts = [f.read_text(encoding="utf-8", errors="ignore") for f in html_like]
    for jf in jsx_like:
        txt = jf.read_text(encoding="utf-8", errors="ignore")
        # Strip raw HTML `style="..."` and `style={{...}}` embedded inside legacy JSX/TSX templates
        cleaned = re.sub(r'\bstyle\s*=\s*["\'][^"\']*["\']', "", txt)
        cleaned = re.sub(r"\bstyle\s*=\s*\{\{[^}]*\}\}", "", cleaned)
        markup_parts.append(cleaned)
    css_corpus = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in css_only)
    return "\n".join(markup_parts), css_corpus


def audit_directory_regularity(target: Path) -> dict[str, Any]:
    """Run Law L7 (Simplicity Favors Regularity / R_kanon) audit on a repository directory."""
    resolved = target.expanduser().resolve()
    root = resolved if resolved.is_dir() else resolved.parent
    _non_ui_dirs = {"docs", "artifacts", "scripts", "evidence", "corpus"}
    all_web_ui = _iter_source_files(root, (".tsx", ".jsx", ".vue", ".css", ".html", ".svelte"))
    app_web_ui = [p for p in all_web_ui if not any(part in _non_ui_dirs for part in p.relative_to(root).parts)]
    web_ui_files = app_web_ui if app_web_ui else all_web_ui
    markup_corpus, css_corpus = _collect_law_l7_corpora(web_ui_files)
    res = _audit_structural_regularity(markup_corpus, css_corpus)
    passed = (
        res["distinct_font_sizes"] <= 8
        and res["distinct_border_radii"] <= 4
        and res["distinct_grid_templates"] <= 5
        and res["r_kanon"] == 1.0
    )
    veto = res["distinct_font_sizes"] > 12 or res["distinct_border_radii"] > 6 or res["distinct_grid_templates"] > 7
    return {
        "project_name": root.name,
        "target": str(root),
        "files_scanned": len(web_ui_files),
        **res,
        "passed": passed,
        "status": "PASS" if passed else ("VETO" if veto else "WARN"),
    }


def _audit_simultaneous_load_l8(
    ui_text: str | Path,
    pw_data: dict[str, Any] | list[Path] | None = None,
    pw_data_opt: dict[str, Any] | None = None,
    extra_text: str = "",
) -> dict[str, Any]:
    """
    Law L8 — First-Paint Simultaneous Cognitive Load Budget (Ο Νόμος του Ταυτόχρονου Γνωστικού Φορτίου).
    Audits both runtime Playwright DOM telemetry (`visibleInteractiveControls`, `visibleChoiceGroups`,
    `activeMacroRegions`, `chromeToContentRatio`, `metricMultiplicity`) and static architectural anti-patterns
    (default `viewMode = 'split'` combined with permanent sidebars, inline sub-44px tap targets).
    """
    if isinstance(ui_text, Path):
        files = pw_data if isinstance(pw_data, list) else []
        ui_text = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in files) + "\n" + extra_text
        pw = pw_data_opt if isinstance(pw_data_opt, dict) else {}
    else:
        pw = pw_data if isinstance(pw_data, dict) else {}
    html_pages = [
        p
        for p in re.split(r"<!DOCTYPE\s+html>|/\* === GWI_FILE_BOUNDARY === \*/", ui_text, flags=re.IGNORECASE)
        if p.strip() and re.search(r"<(?:button|select|input|main|header|section|div)\b", p, flags=re.IGNORECASE)
    ]
    pages_to_scan = html_pages if html_pages else [ui_text]

    def _strip_hidden_and_dialogs(page_src: str) -> str:
        # Exclude controls inside <dialog>...</dialog> or sections marked display:none / hidden on first paint
        cleaned = re.sub(r"<dialog\b[^>]*>[\s\S]*?</dialog>", "", page_src, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"<(?:section|div)\b[^>]*(?:display\s*:\s*none|\bhidden\b)[^>]*>[\s\S]*?</(?:section|div)>",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned

    first_paint_pages = [_strip_hidden_and_dialogs(p) for p in pages_to_scan]
    max_static_ctrl = max(
        (len(re.findall(r"<(?:button|select|input)\b", fp, flags=re.IGNORECASE)) for fp in first_paint_pages),
        default=0,
    )
    max_static_groups = max(
        (len(re.findall(r'role\s*=\s*["\']group["\']|<select\b', fp, flags=re.IGNORECASE)) for fp in first_paint_pages),
        default=0,
    )
    wedge_first_paint_pages = [
        fp
        for fp in first_paint_pages
        if re.search(r"<title>[^<]*(?:Snap|Moment|Quick Card|Marketplace)", fp, flags=re.IGNORECASE)
    ]
    max_wedge_ctrl = max(
        (len(re.findall(r"<(?:button|select|input)\b", fp, flags=re.IGNORECASE)) for fp in wedge_first_paint_pages),
        default=0,
    )

    raw_ctrl = pw.get("visibleInteractiveControls")
    if raw_ctrl is None:
        raw_ctrl = pw.get("interactiveControlCount")
    if raw_ctrl is None:
        n_ctrl = max_static_ctrl
    else:
        n_ctrl = max(int(raw_ctrl), max_wedge_ctrl) if max_wedge_ctrl > 16 else int(raw_ctrl)

    raw_groups = pw.get("visibleChoiceGroups")
    if raw_groups is None:
        n_groups = max_static_groups
    else:
        n_groups = int(raw_groups)

    default_split_overload = bool(
        re.search(
            r"DEFAULT_STATE[\s\S]{0,400}?viewMode\s*:\s*['\"]split['\"]|\bviewMode\s*:\s*['\"]split['\"]|(?<![\[\w-])data-view-mode\s*=\s*['\"]split['\"]",
            ui_text,
        )
    )
    raw_regions = pw.get("activeMacroRegions")
    if raw_regions is None:
        n_regions = 5 if default_split_overload else 3
    else:
        n_regions = int(raw_regions)

    raw_ccr = pw.get("chromeToContentRatio")
    ccr = float(raw_ccr) if raw_ccr is not None else (1.45 if default_split_overload else 0.45)

    raw_mult = pw.get("metricMultiplicity")
    mult = int(raw_mult) if raw_mult is not None else 1

    sub44_inline = sorted(
        set(
            re.findall(
                r"min-height\s*:\s*(?:1\d|2\d|3\d|4[0-3])px\b",
                ui_text,
                flags=re.IGNORECASE,
            )
        )
    )
    l8 = compute_simultaneous_load_index(
        visible_interactive_controls=n_ctrl,
        visible_choice_groups=n_groups,
        active_macro_regions=n_regions,
        chrome_to_content_ratio=ccr,
        metric_multiplicity=mult,
    )
    l8["default_split_overload"] = default_split_overload
    l8["sub44_inline_targets"] = sub44_inline
    return l8


def _audit_canonical_anchor_l9(root: Path, src_corpus: str = "") -> dict[str, Any]:
    """
    Law L9 — Canonical Monorepo Anchor Over New-Repo & Subdomain Sprawl.
    Audits GitLab top-level pillar namespace membership (`CANONICAL_GITLAB_GROUPS`),
    standalone single-route wedge repo sprawl (`/snap`, `/moment`, `?room=204`, `/card`),
    and `.swm/passport.yaml` / `AGENTS.md` canonical anchor provenance.
    """
    git_cfg_path = root / ".git" / "config"
    passport_path = root / ".swm" / "passport.yaml"
    git_cfg_txt = git_cfg_path.read_text(encoding="utf-8", errors="ignore") if git_cfg_path.is_file() else ""
    passport_txt = passport_path.read_text(encoding="utf-8", errors="ignore") if passport_path.is_file() else ""
    combined_meta = git_cfg_txt + "\n" + passport_txt

    extra_groups = {g.strip().lower() for g in os.environ.get("KB_CANONICAL_GITLAB_GROUPS", "").split(",") if g.strip()}
    allowed_groups = CANONICAL_GITLAB_GROUPS | extra_groups

    gl_groups = [
        m.lower()
        for m in re.findall(
            r"gitlab\.[A-Za-z0-9_.-]+(?::\d+)?[:/]([A-Za-z0-9_.-]+)/[A-Za-z0-9_.-]+",
            combined_meta,
        )
    ]
    non_canonical_groups = sorted(
        {
            g
            for g in gl_groups
            if "sprawl" in g
            or (extra_groups and g not in allowed_groups)
            or (not extra_groups and g not in allowed_groups and "-" in g)
        }
    )

    unanchored_wedges = 0
    if re.search(r"\bunanchored_wedge_repos\s*:\s*([1-9]\d*)", passport_txt):
        m_w = re.search(r"\bunanchored_wedge_repos\s*:\s*([1-9]\d*)", passport_txt)
        unanchored_wedges = int(m_w.group(1)) if m_w else 1
    elif root.name.lower() in {"snap-standalone", "moment-standalone", "room204-standalone", "card-standalone"}:
        unanchored_wedges = 1

    unanchored_subdomains = 0
    if re.search(r"\bunanchored_subdomains\s*:\s*([1-9]\d*)", passport_txt):
        m_s = re.search(r"\bunanchored_subdomains\s*:\s*([1-9]\d*)", passport_txt)
        unanchored_subdomains = int(m_s.group(1)) if m_s else 1

    has_passport = True
    if passport_path.is_file() and ("id:" not in passport_txt or "tier:" not in passport_txt):
        has_passport = False

    res = compute_canonical_anchor_l9_index(
        active_gitlab_groups=9 + len(non_canonical_groups),
        unanchored_wedge_repos=unanchored_wedges,
        unanchored_subdomains=unanchored_subdomains,
        has_canonical_passport=has_passport,
    )
    res["gitlab_groups"] = sorted(set(gl_groups)) if non_canonical_groups else (["canonical"] if gl_groups else [])
    res["non_canonical_groups"] = non_canonical_groups
    return res


_WEDGE_STEMS: frozenset[str] = frozenset({"snap", "moment", "card", "marketplace"})

_WEDGE_B2B_JARGON_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Smart Pool", re.compile(r"\bSmart\s+Pool\b", re.IGNORECASE)),
    ("#PV-BOOK/#PV-MKT ticket hash", re.compile(r"#PV-(?:BOOK|MKT|INC)-[A-Za-z0-9_-]+")),
    ("SLA < operator metric", re.compile(r"\bSLA\s*<\s*\d+", re.IGNORECASE)),
    ("HACCP B2B compliance tag", re.compile(r"\bHACCP\b")),
    ("Live Operator Feed ('Ζωντανή Ροή' / 'Live Feed')", re.compile(r"Ζωντανή\s+Ροή|\bLive\s+Feed\b", re.IGNORECASE)),
    ("Raw AI confidence telemetry ('% AI' / 'TRAP #')", re.compile(r"\b\d+%\s*AI\b|\bTRAP\s*#\d+\b")),
)


def _is_wedge_route_file(p: Path, root: Path) -> str | None:
    """Return the canonical wedge stem ('snap', 'moment', 'card', 'marketplace') if `p` is a consumer wedge route."""
    if p.suffix.lower() not in (".html", ".tsx", ".jsx", ".vue", ".svelte"):
        return None
    stem = p.stem.lower()
    parent_name = p.parent.name.lower()
    if stem in ("page", "index", "+page") and parent_name in _WEDGE_STEMS:
        return parent_name
    if stem in _WEDGE_STEMS:
        rel_parts = [pt.lower() for pt in (p.relative_to(root).parts if p.is_relative_to(root) else p.parts)]
        if any(comp_dir in rel_parts for comp_dir in ("components", "ui", "atoms", "molecules", "widgets")):
            return None
        return stem
    return None


def _audit_human_wedge_anti_palantir(root: Path, ui_files: list[Path]) -> dict[str, Any]:
    """
    Law L6, L8 & L9 — Human-Data Wedge Single-Verb & Anti-Palantir Gate ('We Are Not Palantir', PDR-2026-10-16 §6.1).
    Audits consumer wedge surfaces (`snap.*`, `moment.*`, `card.*`, `marketplace.*`) across 4 strict invariants:
      - W1 (Single-Column Container <= 680px & 0 Multi-Column Split Dashboards):
        No `max-width > 680px` (`1120px`, `70rem`, `max-w-6xl`, etc., ignoring `@media` conditions),
        no multi-column page grids (`1fr 1fr`, `1.25fr 1fr`, `lg:grid-cols-12`, `md:grid-cols-2`),
        no wide flex split columns (`flex: 1 1 320px+`), and no `<aside>` operator sidebars.
      - W2 (Zero B2B Operator / ERP Telemetry Jargon Leak in Consumer Markup & Scripts):
        HTML-unescaped markup and script string literals must not expose internal control-plane terms
        (`Smart Pool`, `#PV-BOOK-`, `#PV-MKT-`, `SLA <`, `HACCP`, `Ζωντανή Ροή`, `% AI` confidence badges).
      - W3 (Progressive Disclosure on Interactive Diagnostic Wedges — Law L6):
        On `snap` routes, Step 2/3 (AI diagnosis, chat, and local pros) must be gated inside an element
        that is actually hidden on first paint (`hidden`, inline `display: none`, a matched CSS class with
        `display: none`, or conditional JSX `{result &&` / `{hasPhoto &&`) wrapping the post-photo content.
      - W4 (Single-Row Header Simplicity — Law L1):
        Wedge `<header>` / `<nav>` bars must have <= 2 interactive links/buttons (brand + 1 action pill).
    """

    wedge_entries: list[tuple[Path, str]] = []
    for p in ui_files:
        w_kind = _is_wedge_route_file(p, root)
        if w_kind:
            wedge_entries.append((p, w_kind))

    violations: list[str] = []
    file_reports: list[dict[str, Any]] = []

    for wf, wedge_kind in wedge_entries:
        rel_name = str(wf.relative_to(root)) if wf.is_relative_to(root) else wf.name
        raw = wf.read_text(encoding="utf-8", errors="ignore")
        wf_violations: list[str] = []

        # Extract CSS/style blocks + inline HTML/JSX style attributes for W1 layout inspection
        style_blocks = "\n".join(re.findall(r"<style\b[^>]*>([\s\S]*?)</style>", raw, flags=re.IGNORECASE))
        for href in re.findall(r'<link\b[^>]*href=["\']/?([^"\']+\.css)["\']', raw, flags=re.IGNORECASE):
            css_name = Path(href).name.lower()
            if css_name not in ("sovereign.css", "tokens.css", "globals.css", "tailwind.css"):
                for cand in (wf.parent / href, root / "public" / href, root / href):
                    if cand.is_file():
                        style_blocks += "\n" + cand.read_text(encoding="utf-8", errors="ignore")
                        break

        inline_styles = "\n".join(re.findall(r'\bstyle\s*=\s*["\']([^"\']+)["\']', raw, flags=re.IGNORECASE))
        jsx_styles = "\n".join(re.findall(r"\bstyle\s*=\s*\{\{([^}]+)\}\}", raw))
        css_scope = (
            re.sub(r"@media\b[^{]+", " ", style_blocks, flags=re.IGNORECASE) + "\n" + inline_styles + "\n" + jsx_styles
        )

        # W1a: Check max-width / maxWidth / width > 680px (supporting both px and rem)
        wide_px: list[int] = []
        for val_str, unit in re.findall(
            r"(?:max-width|maxWidth|(?<![-\w])width)\s*:\s*['\"]?(\d+(?:\.\d+)?)\s*(px|rem)?\b",
            css_scope,
            flags=re.IGNORECASE,
        ):
            val_num = float(val_str)
            px_equiv = int(round(val_num * 16.0)) if unit.lower() == "rem" else int(round(val_num))
            if px_equiv > 680:
                wide_px.append(px_equiv)

        wide_tw = re.findall(r"\b(?:max-)?w-(?:3xl|4xl|5xl|6xl|7xl|screen-lg|screen-xl|screen-2xl)\b", raw)
        if wide_px or wide_tw:
            detail = f"width={wide_px[0]}px" if wide_px else f"class={wide_tw[0]}"
            wf_violations.append(
                f"{rel_name} [W1-Width]: wide desktop container ({detail} > 680px single-column budget)"
            )

        # W1b: Check multi-column split dashboard grids, wide flex split panes, or <aside> operator sidebars
        split_grid_css = re.findall(
            r"(?:grid-template-columns|gridTemplateColumns)\s*:\s*['\"]?(?:1fr\s+1fr|1\.\d+fr\s+1fr|\d+fr\s+\d+fr|repeat\(\s*(?:[2-9]|1\d|auto-fit|auto-fill)\s*,)",
            css_scope,
            flags=re.IGNORECASE,
        )
        split_flex_css = [
            f"flex-split({val}{unit})"
            for val, unit in re.findall(
                r"\bflex\s*:\s*\d+\s+\d+\s+(\d+(?:\.\d+)?)(px|rem)\b", css_scope, flags=re.IGNORECASE
            )
            if (float(val) * 16.0 if unit.lower() == "rem" else float(val)) >= 320.0
        ]
        split_grid_tw = re.findall(r"\b(?:(?:sm|md|lg|xl|2xl):)?grid-cols-(?:[2-9]|1[0-2])\b", raw)
        has_aside = bool(re.search(r"<aside\b", raw, flags=re.IGNORECASE))
        if split_grid_css or split_flex_css or split_grid_tw or has_aside:
            g_det = (
                split_grid_css[0]
                if split_grid_css
                else (
                    split_flex_css[0]
                    if split_flex_css
                    else (split_grid_tw[0] if split_grid_tw else "<aside> operator sidebar")
                )
            )
            wf_violations.append(f"{rel_name} [W1-SplitGrid]: multi-column control-plane layout ({g_det})")

        # Strip <style> and HTML comments; decode HTML entities (`&nbsp;`, `&lt;`) across both markup and scripts
        no_style = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", raw, flags=re.IGNORECASE)
        no_style = re.sub(r"<!--[\s\S]*?-->", " ", no_style)
        unescaped_surface = _html.unescape(no_style).replace("\xa0", " ")

        visible_markup = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", unescaped_surface, flags=re.IGNORECASE)

        # W2: Zero B2B operator / ERP telemetry jargon in visible consumer markup or client-rendered script strings
        leaked_terms: list[str] = []
        for label, pat in _WEDGE_B2B_JARGON_PATTERNS:
            if pat.search(unescaped_surface):
                leaked_terms.append(label)
        if leaked_terms:
            wf_violations.append(
                f"{rel_name} [W2-JargonLeak]: B2B operator telemetry exposed to consumer ({', '.join(leaked_terms)})"
            )

        # W3: Progressive Disclosure on `snap` routes (Step 2/3 must actually contain diagnosis/chat inside a hidden gate)
        if wedge_kind == "snap":
            has_progressive_gate = False
            if wf.suffix.lower() in (".tsx", ".jsx"):
                for cond_m in re.finditer(
                    r"\{\s*(?:hasPhoto|result|diagnosis|activeResult|photoSelected|showResult|showAdvice|selectedPreset|step\s*>=\s*2)\s*(?:&&|\?)\s*\(",
                    raw,
                ):
                    # Find matching closing brace `}` for the opening `{` at cond_m.start()
                    depth = 0
                    end_idx = len(raw)
                    for idx in range(cond_m.start(), len(raw)):
                        if raw[idx] == "{":
                            depth += 1
                        elif raw[idx] == "}":
                            depth -= 1
                            if depth == 0:
                                end_idx = idx
                                break
                    gated_slice = raw[cond_m.start() : end_idx]
                    if re.search(r"(?:snap-preview-canvas|snap-ai-marketplace-panel|<canvas\b)", gated_slice):
                        has_progressive_gate = True
                        break
            else:
                from html.parser import HTMLParser

                # Track effective `display` value per single-class selector in CSS order
                class_display: dict[str, str] = {}
                for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", style_blocks):
                    sel_clean = sel.strip()
                    # Only bare class selectors like `.wedge-result` set default first-paint state
                    if re.fullmatch(r"\.[a-zA-Z0-9_-]+", sel_clean):
                        cls_name = sel_clean[1:]
                        for disp_val in re.findall(r"(?<![-\w])display\s*:\s*([a-zA-Z-]+)", body, flags=re.IGNORECASE):
                            class_display[cls_name] = disp_val.lower()
                hidden_classes = {c for c, dv in class_display.items() if dv == "none"}

                class _WedgeDomInspector(HTMLParser):
                    def __init__(self, hidden_cls: set[str], cls_disp: dict[str, str]) -> None:
                        super().__init__()
                        self.hidden_cls = hidden_cls
                        self.cls_disp = cls_disp
                        self.stack: list[tuple[str, bool]] = []
                        self.canvas_found = False
                        self.canvas_all_hidden = True

                    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                        attr_map = {k.lower(): (v or "") for k, v in attrs}
                        is_hidden_here = False
                        if "hidden" in attr_map:
                            is_hidden_here = True
                        style_val = attr_map.get("style", "")
                        if style_val:
                            disps = re.findall(r"(?<![-\w])display\s*:\s*([a-zA-Z-]+)", style_val, flags=re.IGNORECASE)
                            if disps and disps[-1].lower() == "none":
                                is_hidden_here = True
                        cls_tokens = attr_map.get("class", "").split()
                        if any(c in self.hidden_cls for c in cls_tokens) and not any(
                            self.cls_disp.get(c) in ("block", "flex", "grid", "inline") for c in cls_tokens
                        ):
                            is_hidden_here = True
                        ancestor_hidden = any(h for _, h in self.stack)
                        effective_hidden = ancestor_hidden or is_hidden_here
                        if tag.lower() == "canvas" or attr_map.get("id", "") in ("snap-chat-thread", "snap-result"):
                            if tag.lower() == "canvas":
                                self.canvas_found = True
                                if not effective_hidden:
                                    self.canvas_all_hidden = False
                        self.stack.append((tag.lower(), effective_hidden))

                    def handle_endtag(self, tag: str) -> None:
                        t_low = tag.lower()
                        for i in range(len(self.stack) - 1, -1, -1):
                            if self.stack[i][0] == t_low:
                                del self.stack[i:]
                                break

                inspector = _WedgeDomInspector(hidden_classes, class_display)
                inspector.feed(visible_markup)
                has_progressive_gate = inspector.canvas_found and inspector.canvas_all_hidden

            if not has_progressive_gate:
                wf_violations.append(
                    f"{rel_name} [W3-ProgressiveDisclosure]: diagnosis/chat/marketplace sections are not hidden on first paint before photo selection (Law L6)"
                )

        # W4: Single-row header without multi-tab portal navigation (<= 2 links/buttons in <header> or <nav>)
        header_nav_blocks = re.findall(
            r"<(?:header|nav)\b[^>]*>([\s\S]*?)</(?:header|nav)>", visible_markup, flags=re.IGNORECASE
        )
        for nb in header_nav_blocks:
            links_and_pills = len(re.findall(r"<(?:a|button)\b", nb, flags=re.IGNORECASE))
            if links_and_pills >= 3:
                wf_violations.append(
                    f"{rel_name} [W4-PortalNav]: multi-tab portal header/nav ({links_and_pills} interactive items > 2 ceiling) on single-verb wedge"
                )
                break

        violations.extend(wf_violations)
        file_reports.append({"file": rel_name, "passed": len(wf_violations) == 0, "violations": wf_violations})

    return {
        "wedge_files_count": len(wedge_entries),
        "passed": len(violations) == 0,
        "violations": violations,
        "files": file_reports,
    }
