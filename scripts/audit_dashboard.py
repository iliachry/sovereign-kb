#!/usr/bin/env python3
"""Capture real browser evidence for the `skb dashboard` UI (GWI UX lens + Release Evidence R).

Starts the live dashboard server on an ephemeral port against a throwaway copy of the bundled
starter vault (or ``--vault``), drives it with headless Chromium (Playwright) at the three GWI
viewports (390x844, 768x1024, 1440x900) and writes:

  evidence/playwright/report.json        runtime diagnostics + UX/subtractive telemetry (measured)
  evidence/dashboard_3viewport_sheet.png 3x1 responsive contact sheet (ffmpeg hstack)
  evidence/playwright/viewport-<w>.png   the individual viewport screenshots

Every number in the report is read from the rendered page; nothing is hardcoded. Worst case across
the three viewports is reported for each budget metric, with per-viewport values kept alongside.

Run from the repository root:

    uv run --with playwright==1.58.0 python scripts/audit_dashboard.py
    uv run --with playwright==1.58.0 python scripts/audit_dashboard.py --video-dir /tmp/walkthrough
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
VIEWPORTS = [(390, 844), (768, 1024), (1440, 900)]
SEARCH_QUERY = "Goldwood Index"

# Measured inside the page. Returns raw per-viewport metrics; aggregation happens in Python.
MEASURE_JS = r"""
() => {
  const W = innerWidth, H = innerHeight;
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const inFold = (el) => { const r = el.getBoundingClientRect(); return r.bottom > 0 && r.top < H && r.right > 0 && r.left < W; };
  const area = (r) => Math.max(0, Math.min(r.right, W) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, H) - Math.max(r.top, 0));
  const doc = document.documentElement;

  // L4: horizontal overflow
  const horizontalOverflowPx = Math.max(0, doc.scrollWidth - doc.clientWidth);

  // L1: header rows (cluster visible direct-child tops of the header's flex rows) and items
  const header = document.querySelector('header, [role=banner]');
  let headerRows = 0, headerItems = 0, headerHeightPx = 0;
  if (header && visible(header)) {
    headerHeightPx = Math.round(header.getBoundingClientRect().height);
    const leaves = [...header.querySelectorAll('a[href],button,select,input,[class*=pill],[class*=badge],[class*=brand]')]
      .filter(visible)
      .filter((el) => !el.parentElement.closest('[class*=pill],[class*=badge],[class*=brand]') || el.matches('a,button,select,input'));
    headerItems = leaves.length;
    const tops = leaves.map((el) => el.getBoundingClientRect()).map((r) => r.top + r.height / 2).sort((a, b) => a - b);
    for (let i = 0; i < tops.length; i++) if (i === 0 || tops[i] - tops[i - 1] > 16) headerRows++;
  }

  // Interactive controls in the first fold (L8) and tap targets (WCAG 2.5.5; inline text links exempt)
  const SEL = 'a[href],button,summary,input:not([type=hidden]),select,textarea,[role=button],[tabindex]:not([tabindex="-1"])';
  const controls = [...document.querySelectorAll(SEL)].filter((el) => visible(el) && inFold(el));
  const tapSizes = controls
    .filter((el) => !(el.tagName === 'A' && getComputedStyle(el).display === 'inline'))
    .map((el) => { const r = el.getBoundingClientRect(); return { id: el.id || el.className || el.tagName, min: Math.min(r.width, r.height) }; });
  const smallest = tapSizes.reduce((m, t) => (m === null || t.min < m.min ? t : m), null);

  // L8: choice groups and macro regions
  const choiceGroups = [...document.querySelectorAll('select,[role=tablist],[role=radiogroup],fieldset')].filter((el) => visible(el) && inFold(el)).length;
  const landmarks = [...document.querySelectorAll('header,nav,main,aside,footer,[role=banner],[role=navigation],[role=main],[role=complementary],[role=contentinfo]')]
    .filter((el) => visible(el) && inFold(el));
  const uniq = [...new Set(landmarks)];
  const chromeArea = uniq.filter((el) => !el.matches('main,[role=main]')).reduce((s, el) => s + area(el.getBoundingClientRect()), 0);
  const mainEl = uniq.find((el) => el.matches('main,[role=main]'));
  const contentArea = mainEl ? area(mainEl.getBoundingClientRect()) - 0 : W * H;

  // L3: expanded analytical tables in the first fold; L5: primary CTAs in the first fold
  const expandedTablesVisible = [...document.querySelectorAll('table')].filter((el) => visible(el) && inFold(el)).length;
  const primaryCtaCount = [...document.querySelectorAll('button.primary,[class*=btn-primary],button[type=submit],input[type=submit]')]
    .filter((el) => visible(el) && inFold(el)).length;

  // HUD occlusion over the graph canvas: area of positioned overlays intersecting the canvas
  let hudOcclusionRatio = null;
  const canvas = [...document.querySelectorAll('canvas')].find((c) => visible(c) && inFold(c));
  if (canvas) {
    const cr = canvas.getBoundingClientRect();
    const cA = cr.width * cr.height;
    let occ = 0;
    for (const el of canvas.parentElement.querySelectorAll('*')) {
      if (el === canvas || !visible(el)) continue;
      const pos = getComputedStyle(el).position;
      if (pos !== 'absolute' && pos !== 'fixed') continue;
      const r = el.getBoundingClientRect();
      occ += Math.max(0, Math.min(r.right, cr.right) - Math.max(r.left, cr.left)) * Math.max(0, Math.min(r.bottom, cr.bottom) - Math.max(r.top, cr.top));
    }
    hudOcclusionRatio = cA > 0 ? Math.min(1, occ / cA) : null;
  }

  // WCAG 2.1 text contrast: every visible text element in the fold vs its effective solid background
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null; // gradient/image: not decidable from CSS
      const c = parse(cs.backgroundColor);
      if (c && c.a >= 0.99) return c;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  };
  let minRatio = null, worst = null, checked = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const el = walker.currentNode.parentElement;
    if (!el || seen.has(el) || !walker.currentNode.textContent.trim()) continue;
    seen.add(el);
    if (!visible(el) || !inFold(el) || el.closest('svg,canvas,script,style')) continue;
    const fg = parse(getComputedStyle(el).color); const bg = bgOf(el);
    if (!fg || !bg || fg.a < 0.99) continue;
    const L1 = lum(fg), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    checked++;
    if (minRatio === null || ratio < minRatio) { minRatio = ratio; worst = (el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + el.className) + ' "' + el.textContent.trim().slice(0, 40) + '"'; }
  }

  return {
    viewport: [W, H], horizontalOverflowPx, headerRows, headerItems, headerHeightPx,
    visibleInteractiveControls: controls.length,
    minTapTargetPx: smallest ? Math.round(smallest.min * 10) / 10 : null, smallestTapTarget: smallest ? String(smallest.id) : null,
    visibleChoiceGroups: choiceGroups, activeMacroRegions: uniq.length,
    chromeToContentRatio: contentArea > 0 ? Math.round((chromeArea / contentArea) * 1000) / 1000 : null,
    expandedTablesVisible, primaryCtaCount,
    hudOcclusionRatio: hudOcclusionRatio === null ? null : Math.round(hudOcclusionRatio * 1000) / 1000,
    minTextContrastRatio: minRatio === null ? null : Math.round(minRatio * 100) / 100, worstContrastElement: worst, contrastChecked: checked,
  };
}
"""

WORST = {  # metric -> aggregation across viewports
    "horizontalOverflowPx": max,
    "headerRows": max,
    "headerItems": max,
    "visibleInteractiveControls": max,
    "visibleChoiceGroups": max,
    "activeMacroRegions": max,
    "chromeToContentRatio": max,
    "expandedTablesVisible": max,
    "primaryCtaCount": max,
    "hudOcclusionRatio": max,
    "minTapTargetPx": min,
}


def _start_server(vault: Path) -> tuple[subprocess.Popen[str], str]:
    skb = Path(sys.executable).with_name("skb")
    cmd = [str(skb) if skb.exists() else "skb", "dashboard", "--port", "0", "--native", "--no-gx10"]
    env = {**os.environ, "KB_VAULT_DIR": str(vault), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 120
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            raise SystemExit(f"dashboard server exited early (code {proc.returncode})")
        m = re.search(r"Live Web UI\s*:\s*(http://\S+)", line)
        if m:
            return proc, m.group(1)
    proc.kill()
    raise SystemExit("dashboard server did not report its URL within 120s")


def _secret_leaks(html: str) -> list[str]:
    sys.path.insert(0, str(REPO / "src"))
    from sovereign_kb.gwi import _SECRET_LEAK_RE  # noqa: PLC0415

    return sorted({m.group(0)[:12] + "…" for m in _SECRET_LEAK_RE.finditer(html)})


def _compose_sheet(shots: list[Path], out: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required to compose the 3-viewport sheet")
    inputs: list[str] = []
    for s in shots:
        inputs += ["-i", str(s)]
    scale = ";".join(f"[{i}:v]scale=-2:900[v{i}]" for i in range(len(shots)))
    stack = "".join(f"[v{i}]" for i in range(len(shots))) + f"hstack=inputs={len(shots)}"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex",
            f"{scale};{stack}",
            "-frames:v",
            "1",
            str(out),
        ],
        check=True,
    )


def run(vault_src: Path, out_dir: Path, video_dir: Path | None) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    work = Path(tempfile.mkdtemp(prefix="skb-dash-audit-"))
    vault = work / "vault"
    shutil.copytree(vault_src, vault, ignore=shutil.ignore_patterns(".kb", ".git"))
    (out_dir / "playwright").mkdir(parents=True, exist_ok=True)
    proc, url = _start_server(vault)

    page_errors: list[str] = []
    console_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []
    per_vp: list[dict[str, Any]] = []
    shots: list[Path] = []
    steps_to_value: int | None = None
    search_results = 0
    html = ""
    dark_metrics: dict[str, Any] | None = None
    gate_details_visible = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for w, h in VIEWPORTS:
                ctx_kw: dict[str, Any] = {"viewport": {"width": w, "height": h}, "color_scheme": "light"}
                if video_dir and w == 1440:
                    video_dir.mkdir(parents=True, exist_ok=True)
                    ctx_kw["record_video_dir"] = str(video_dir)
                    ctx_kw["record_video_size"] = {"width": w, "height": h}
                context = browser.new_context(**ctx_kw)
                page = context.new_page()
                page.on("pageerror", lambda e: page_errors.append(str(e)))
                page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
                page.on("response", lambda r: http_errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)
                page.on("requestfailed", lambda r: failed_requests.append(f"{r.failure} {r.url}"))
                page.goto(url, wait_until="networkidle", timeout=120_000)
                page.wait_for_timeout(800)
                metrics = page.evaluate(MEASURE_JS)
                per_vp.append(metrics)
                shot = out_dir / "playwright" / f"viewport-{w}.png"
                page.screenshot(path=str(shot))
                shots.append(shot)
                if w == 1440:
                    html = page.content()
                    # L6: the collapsed gate summary must reveal every gate pill in one click.
                    if page.locator("details.gate-summary > summary").count():
                        page.click("details.gate-summary > summary")
                        page.wait_for_timeout(200)
                        gate_details_visible = page.locator("#gate-strip .status-pill:visible").count()
                        page.click("details.gate-summary > summary")
                    # Core value = ranked retrieval results. Count the user actions it takes to get there.
                    actions = 0
                    page.fill("#search-input", SEARCH_QUERY)
                    actions += 1
                    try:
                        with page.expect_response(lambda r: "/api/search" in r.url, timeout=60_000) as resp_info:
                            page.press("#search-input", "Enter")
                            actions += 1
                        resp = resp_info.value
                        hits = len((resp.json() or {}).get("results") or []) if resp.ok else 0
                        page.wait_for_timeout(400)
                        search_results = min(hits, page.locator("#search-hits > *").count())
                        steps_to_value = actions if search_results else None
                    except Exception:
                        steps_to_value = None
                    page.wait_for_timeout(600)
                    page.mouse.wheel(0, 1400)
                    page.wait_for_timeout(600)
                    page.mouse.wheel(0, 1400)
                    page.wait_for_timeout(600)
                context.close()
            # Contrast must also hold in the dark theme.
            context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=120_000)
            page.wait_for_timeout(800)
            dark_metrics = page.evaluate(MEASURE_JS)
            context.close()
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)

    sheet = out_dir / "dashboard_3viewport_sheet.png"
    _compose_sheet(shots, sheet)

    report: dict[str, Any] = {
        "generator": "scripts/audit_dashboard.py",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "browser": "chromium (playwright 1.58.0, headless)",
        # Repo-relative only: absolute home paths must never land in evidence (OPSEC test F-02).
        "vaultSource": str(vault_src.relative_to(REPO)) if vault_src.is_relative_to(REPO) else "<external vault>",
        "viewports": per_vp,
        "pageErrors": page_errors,
        "consoleErrors": console_errors,
        "httpErrors": http_errors,
        "failedRequests": failed_requests,
        "secretLeaks": _secret_leaks(html),
        "stepsToValue": steps_to_value,
        "searchResultsRendered": search_results,
        "gateDetailsVisibleAfterOneClick": gate_details_visible,
        "darkTheme": dark_metrics,
        "contactSheet": str(sheet.relative_to(REPO)) if sheet.is_relative_to(REPO) else str(sheet),
    }
    for key, agg in WORST.items():
        vals = [v[key] for v in per_vp if v.get(key) is not None]
        report[key] = agg(vals) if vals else None
    ratios = [
        v["minTextContrastRatio"] for v in [*per_vp, dark_metrics or {}] if v.get("minTextContrastRatio") is not None
    ]
    report["wcagContrast"] = {
        "minTextContrastRatio": min(ratios) if ratios else None,
        "lightMin": min((v["minTextContrastRatio"] for v in per_vp if v.get("minTextContrastRatio")), default=None),
        "darkMin": (dark_metrics or {}).get("minTextContrastRatio"),
    }
    report["stateCompleteness"] = "search-results-rendered" if search_results else "search-results-missing"
    (out_dir / "playwright" / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", type=Path, default=REPO / "src/sovereign_kb/starter_vault")
    ap.add_argument("--out", type=Path, default=REPO / "evidence")
    ap.add_argument("--video-dir", type=Path, default=None, help="Record the 1440px walkthrough as .webm here")
    args = ap.parse_args()
    rep = run(args.vault.expanduser().resolve(), args.out.expanduser().resolve(), args.video_dir)
    summary = {k: rep[k] for k in (*WORST, "stepsToValue", "wcagContrast")}
    summary["errors"] = {
        k: len(rep[k]) for k in ("pageErrors", "consoleErrors", "httpErrors", "failedRequests", "secretLeaks")
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
