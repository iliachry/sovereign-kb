#!/usr/bin/env python3
"""
GWI Law L7 — Simplicity Favors Regularity (Η Απλότητα Ευνοεί την Κανονικότητα)
Zero-dependency CI & Pre-Commit Gate (GWI+ v1.4.0).

Enforces canonical structural regularity across Web/UI stylesheets & markup:
  - distinct_font_sizes     <= 8  (veto > 12)
  - distinct_border_radii   <= 4  (veto > 6)
  - distinct_grid_templates <= 5  (veto > 7)
  - R_kanon                 == 1.0000
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "out",
    "coverage",
    "example",
    "examples",
    "fixtures",
    "goula-constructions",
    "target",
    ".godot",
    "addons",
    "Library",
    "Temp",
    "Obj",
    "Logs",
    "Recordings",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "test-results",
    ".kb",
    "site-packages",
    "vendor",
    "third_party",
}
NON_UI_DIRS = {"docs", "artifacts", "scripts", "evidence"}
UI_SUFFIXES = (".tsx", ".jsx", ".vue", ".css", ".html", ".svelte")


def iter_ui_files(root: Path) -> list[Path]:
    all_ui: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".worktree")]
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.suffix.lower() in UI_SUFFIXES:
                all_ui.append(p)
    app_ui = [p for p in all_ui if not any(part in NON_UI_DIRS for part in p.relative_to(root).parts)]
    return app_ui if app_ui else all_ui


def compute_regularity_index(n_fs: int, n_br: int, n_gt: int) -> float:
    e_fs = max(0, n_fs - 8) / 4.0
    e_br = max(0, n_br - 4) / 2.0
    e_gt = max(0, n_gt - 5) / 2.0
    return round(math.exp(-0.35 * (e_fs + e_br + e_gt)), 4)


def audit_law_l7(root: Path) -> dict[str, object]:
    ui_files = iter_ui_files(root)
    css_only = [f for f in ui_files if f.suffix.lower() == ".css"]
    html_like = [f for f in ui_files if f.suffix.lower() in (".html", ".vue", ".svelte")]
    jsx_like = [f for f in ui_files if f.suffix.lower() in (".tsx", ".jsx")]

    markup_parts = [f.read_text(encoding="utf-8", errors="ignore") for f in html_like]
    for jf in jsx_like:
        txt = jf.read_text(encoding="utf-8", errors="ignore")
        markup_parts.append(re.sub(r'\bstyle\s*=\s*["\'][^"\']*["\']', "", txt))

    ui_text = "\n".join(markup_parts)
    css_text = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in css_only)

    style_blocks = re.findall(r"<style\b[^>]*>(.*?)</style>", ui_text, flags=re.DOTALL | re.IGNORECASE)
    inline_styles = re.findall(r'\bstyle\s*=\s*["\']([^"\']+)["\']', ui_text, flags=re.IGNORECASE)
    raw_css = "\n".join(style_blocks) + "\n" + "\n".join(inline_styles) + "\n" + css_text
    clean_css = re.sub(r"/\*.*?\*/", "", raw_css, flags=re.DOTALL)

    def _norm(v: str) -> str:
        s = re.sub(r"\s*!important\b", "", v.strip().lower())
        s = re.sub(r"\s*,\s*", ", ", s)
        s = re.sub(r"\(\s+", "(", s)
        s = re.sub(r"\s+\)", ")", s)
        return re.sub(r"\s+", " ", s)

    skip_prefixes = ("inherit", "initial", "unset", "revert")

    font_sizes = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])font-size\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(skip_prefixes) and _norm(m) not in ("100%", "1em") and "${" not in m
    }
    font_sizes.update(_norm(m) for m in re.findall(r"\btext-\[(\d+(?:\.\d+)?(?:px|rem|em))\]", ui_text))

    border_radii = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])border-radius\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(skip_prefixes) and _norm(m) not in ("0", "0px") and "${" not in m
    }
    border_radii.update(_norm(m) for m in re.findall(r"\brounded-\[(\d+(?:\.\d+)?(?:px|rem))\]", ui_text))

    grid_templates = {
        _norm(m)
        for m in re.findall(r"(?<![-\w])grid-template-columns\s*:\s*([^;\"'}\n]+)", clean_css, flags=re.IGNORECASE)
        if _norm(m) and not _norm(m).startswith(("none", "inherit", "initial", "unset")) and "${" not in m
    }

    n_fs, n_br, n_gt = len(font_sizes), len(border_radii), len(grid_templates)
    r_kanon = compute_regularity_index(n_fs, n_br, n_gt)
    passed = n_fs <= 8 and n_br <= 4 and n_gt <= 5 and r_kanon == 1.0
    veto = n_fs > 12 or n_br > 6 or n_gt > 7

    return {
        "project_name": root.name,
        "files_scanned": len(ui_files),
        "distinct_font_sizes": n_fs,
        "distinct_border_radii": n_br,
        "distinct_grid_templates": n_gt,
        "font_sizes": sorted(font_sizes),
        "border_radii": sorted(border_radii),
        "grid_templates": sorted(grid_templates),
        "r_kanon": r_kanon,
        "passed": passed,
        "status": "PASS" if passed else ("VETO" if veto else "WARN"),
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    strict = "--strict" in args or True
    pos = [a for a in args if not a.startswith("-")]
    target = Path(pos[0]).expanduser().resolve() if pos else Path.cwd().resolve()

    res = audit_law_l7(target)
    if as_json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(
            f"[Law L7 · R_kanon] {res['project_name']}: "
            f"files={res['files_scanned']} | "
            f"font_sizes={res['distinct_font_sizes']}/8 | "
            f"radii={res['distinct_border_radii']}/4 | "
            f"grids={res['distinct_grid_templates']}/5 | "
            f"R_kanon={res['r_kanon']:.4f} [{res['status']}]"
        )
        if not res["passed"]:
            print(f"  font_sizes ({res['distinct_font_sizes']}): {res['font_sizes']}")
            print(f"  border_radii ({res['distinct_border_radii']}): {res['border_radii']}")
            print(f"  grid_templates ({res['distinct_grid_templates']}): {res['grid_templates']}")
    return 0 if (res["passed"] or not strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
