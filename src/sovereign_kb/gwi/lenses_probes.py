"""Lens auto-detection, WCAG contrast math, PNG contact-sheet grid probes, and PDF typography probes."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_science import SCI_LENS_ID

from .lenses_catalog import CRAFT_LENSES
from .utils import _iter_source_files, detect_profile


def detect_active_lenses(target: Path, profile: str = "auto") -> list[dict[str, Any]]:
    """
    Deterministically detect which Bi-Polar Specialist Lenses (4 Verga + 4 Chrysos) apply to `target`
    based on profile, dependency manifests (`package.json`, `pyproject.toml`, `Cargo.toml`), and source files.
    """
    resolved = target.expanduser().resolve()
    chosen_profile = detect_profile(resolved) if profile == "auto" else profile
    root = resolved if resolved.is_dir() else resolved.parent

    pkg_deps: set[str] = set()
    pkg_json = root / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
            pkg_deps.update((data.get("dependencies") or {}).keys())
            pkg_deps.update((data.get("devDependencies") or {}).keys())
        except Exception:
            pass

    pyproject_toml = root / "pyproject.toml"
    if pyproject_toml.is_file():
        py_txt = pyproject_toml.read_text(encoding="utf-8", errors="ignore").lower()
        for py_dep in (
            "torch",
            "transformers",
            "google-genai",
            "openai",
            "anthropic",
            "scipy",
            "numpy",
            "biopython",
            "fastapi",
            "sqlalchemy",
            "google-cloud-bigquery",
            "pytest",
        ):
            if re.search(rf"\b{re.escape(py_dep)}\b", py_txt):
                pkg_deps.add(py_dep)

    cargo_toml = root / "Cargo.toml"
    if cargo_toml.is_file():
        cargo_txt = cargo_toml.read_text(encoding="utf-8", errors="ignore").lower()
        if "bevy" in cargo_txt:
            pkg_deps.add("bevy")

    present_suffixes: set[str] = set()
    if resolved.is_file():
        present_suffixes.add(resolved.suffix.lower())
    elif root.is_dir():
        for p in _iter_source_files(
            root,
            (
                ".glsl",
                ".wgsl",
                ".frag",
                ".vert",
                ".glb",
                ".gltf",
                ".godot",
                ".tscn",
                ".unity",
                ".tsx",
                ".jsx",
                ".vue",
                ".css",
                ".mjs",
                ".mp4",
                ".webm",
                ".wav",
                ".mp3",
                ".srt",
                ".vtt",
                ".tex",
                ".bib",
                ".sql",
                ".sqlx",
                ".graphql",
                ".ipynb",
                ".fasta",
                ".pdb",
                ".cif",
                ".lock",
            ),
        ):
            present_suffixes.add(p.suffix.lower())

    active: list[dict[str, Any]] = []
    for lens_id, spec in CRAFT_LENSES.items():
        matched_reasons: list[str] = []
        if chosen_profile in spec["applies_to_profiles"]:
            if lens_id == "typography-editor" and chosen_profile in ("paper", "kb"):
                matched_reasons.append(f"profile={chosen_profile}")
            elif lens_id == "spatial-cinema" and chosen_profile == "video":
                matched_reasons.append("profile=video")
            elif lens_id == "technical-artist" and chosen_profile == "sim":
                matched_reasons.append("profile=sim")
            elif lens_id == "ux-ergonomics" and chosen_profile == "app":
                matched_reasons.append("profile=app")
            elif (
                lens_id == "web-perf-edge"
                and chosen_profile == "app"
                and (pkg_json.is_file() or (root / "wrangler.toml").is_file() or ".tsx" in present_suffixes)
            ):
                matched_reasons.append("profile=app")
            elif lens_id == "principal-verification" and (
                chosen_profile == "kb"
                or (root / "AGENTS.md").is_file()
                or pkg_json.is_file()
                or pyproject_toml.is_file()
                or cargo_toml.is_file()
            ):
                matched_reasons.append(f"profile={chosen_profile}")
            elif (
                lens_id == SCI_LENS_ID
                and root.is_dir()
                and not (resolved.is_file() and resolved.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"))
            ):
                matched_reasons.append(f"integrity=always(profile={chosen_profile})")

        dep_hits = sorted(pkg_deps.intersection(spec["trigger_deps"]))
        if dep_hits and (chosen_profile != "paper" or "paper" in spec["applies_to_profiles"]):
            matched_reasons.append(f"deps={','.join(dep_hits[:3])}")

        suf_hits = sorted(present_suffixes.intersection(spec["trigger_suffixes"]))
        if suf_hits and (chosen_profile != "paper" or "paper" in spec["applies_to_profiles"]):
            matched_reasons.append(f"files={','.join(suf_hits[:3])}")

        if lens_id == "data-api-architect" and not matched_reasons:
            if (root / "supabase").is_dir() or list(root.glob("*openapi*.y*ml")) or list(root.glob("*openapi*.json")):
                matched_reasons.append("schema=openapi/supabase")

        if lens_id == "ai-ml-science" and not matched_reasons:
            if (root / "sim" / "run_sim.py").is_file() or (root / "sim" / "verify_sim.py").is_file():
                matched_reasons.append("sim=executable_sim")

        if matched_reasons:
            entry = dict(spec)
            entry["activation_reasons"] = matched_reasons
            active.append(entry)

    return active


def srgb_to_relative_luminance(r_8: int, g_8: int, b_8: int) -> float:
    """Compute WCAG 2.1 linearized relative luminance L in [0.0, 1.0] from 8-bit sRGB channels."""

    def _lin(c: int) -> float:
        v = max(0.0, min(1.0, c / 255.0))
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r_8) + 0.7152 * _lin(g_8) + 0.0722 * _lin(b_8)


def _parse_hex_rgb(hex_str: str) -> tuple[int, int, int] | None:
    s = hex_str.strip().lstrip("#")
    if len(s) in (3, 4) and all(c in "0123456789abcdefABCDEF" for c in s):
        s = "".join(c * 2 for c in s[:3])
    if len(s) in (6, 8) and all(c in "0123456789abcdefABCDEF" for c in s):
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return None


def compute_wcag_contrast_ratio(color_a: str, color_b: str) -> float:
    """Compute WCAG 2.1 contrast ratio (L_max + 0.05) / (L_min + 0.05) in [1.0, 21.0] for two hex colors."""
    rgb_a = _parse_hex_rgb(color_a)
    rgb_b = _parse_hex_rgb(color_b)
    if rgb_a is None or rgb_b is None:
        return 1.0
    la = srgb_to_relative_luminance(*rgb_a)
    lb = srgb_to_relative_luminance(*rgb_b)
    l_max, l_min = max(la, lb), min(la, lb)
    return round((l_max + 0.05) / (l_min + 0.05), 2)


def probe_image_grid_telemetry(img_path: Path, grid_cols: int = 3, grid_rows: int = 2) -> dict[str, Any]:
    """
    Decode an image or contact sheet (`1920x1080`) into an RGB24 buffer via `ffmpeg` (or PNG header fallback)
    and compute deterministic Fable x Astra perceptual metrics:
      - Rec.709 luminance Y = 0.2126R + 0.7152G + 0.0722B: mean_y, std_y, clipped_pct (Y < 2 or Y > 253)
      - Gradient / Sobel edge density: fraction of pixels with |grad Y| > 18.0
      - Pairwise tile dissimilarity (MSE) across grid cells so identical repeated frames are detected
    """
    out: dict[str, Any] = {
        "probed": False,
        "width": 0,
        "height": 0,
        "mean_y": 0.0,
        "std_y": 0.0,
        "clipped_pct": 0.0,
        "edge_density": 0.0,
        "min_tile_mse": 0.0,
    }
    if not img_path.is_file() or img_path.stat().st_size < 64:
        return out

    raw_head = img_path.read_bytes()[:32]
    if raw_head.startswith(b"\x89PNG\r\n\x1a\n") and len(raw_head) >= 24:
        out["width"] = int.from_bytes(raw_head[16:20], "big")
        out["height"] = int.from_bytes(raw_head[20:24], "big")

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return out

    w, h = 360, 204
    expected = w * h * 3
    try:
        res = subprocess.run(
            [
                ffmpeg_bin,
                "-nostats",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(img_path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={w}:{h}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        buf = res.stdout
        if len(buf) < expected:
            return out

        out["probed"] = True
        if out["width"] == 0:
            out["width"], out["height"] = 1920, 1080

        luma = [0.0] * (w * h)
        sum_y = 0.0
        sum_y2 = 0.0
        clipped = 0
        n_pix = w * h
        for i in range(n_pix):
            off = i * 3
            yv = 0.2126 * buf[off] + 0.7152 * buf[off + 1] + 0.0722 * buf[off + 2]
            luma[i] = yv
            sum_y += yv
            sum_y2 += yv * yv
            if yv < 2.0 or yv > 253.0:
                clipped += 1

        mean_y = sum_y / n_pix
        std_y = math.sqrt(max(0.0, (sum_y2 / n_pix) - (mean_y * mean_y)))

        edges = 0
        inner_count = 0
        for py in range(1, h - 1):
            row_off = py * w
            for px in range(1, w - 1):
                gx = abs(luma[row_off + px + 1] - luma[row_off + px - 1])
                gy = abs(luma[row_off + w + px] - luma[row_off - w + px])
                if (gx + gy) > 18.0:
                    edges += 1
                inner_count += 1

        tile_w = max(8, w // max(1, grid_cols))
        tile_h = max(8, int((h * 0.75) // max(1, grid_rows)))
        tiles: list[list[float]] = []
        for gr in range(grid_rows):
            for gc in range(grid_cols):
                ty0 = min(h - tile_h, gr * tile_h)
                tx0 = min(w - tile_w, gc * tile_w)
                vec: list[float] = []
                for ty in range(ty0 + 4, ty0 + tile_h - 4, 2):
                    for tx in range(tx0 + 4, tx0 + tile_w - 4, 2):
                        vec.append(luma[ty * w + tx])
                if vec:
                    tiles.append(vec)

        pair_mses: list[float] = []
        for idx in range(len(tiles) - 1):
            a, b = tiles[idx], tiles[idx + 1]
            m_len = min(len(a), len(b))
            if m_len > 0:
                mse = sum((a[k] - b[k]) ** 2 for k in range(m_len)) / m_len
                pair_mses.append(mse)

        out["mean_y"] = round(mean_y, 2)
        out["std_y"] = round(std_y, 2)
        out["clipped_pct"] = round((clipped * 100.0) / n_pix, 2)
        out["edge_density"] = round(edges / max(1, inner_count), 4)
        out["min_tile_mse"] = round(min(pair_mses), 2) if pair_mses else 0.0
    except Exception:
        pass
    return out


def probe_pdf_typography(pdf_path: Path) -> dict[str, Any]:
    """
    Inspect compiled PDF via Poppler `pdffonts` and `pdfimages -list` (plus binary stream fallback):
      - `type3_fonts`: count of bitmapped Type-3 fonts (must be 0)
      - `unembedded_fonts`: count of non-embedded fonts (must be 0)
      - `min_image_ppi`: minimum effective raster image DPI (target >= 150 PPI)
    """
    info: dict[str, Any] = {
        "probed": False,
        "total_fonts": 0,
        "type3_fonts": 0,
        "unembedded_fonts": 0,
        "raster_images": 0,
        "min_image_ppi": None,
    }
    if not pdf_path.is_file() or pdf_path.stat().st_size < 128:
        return info

    raw_bytes = pdf_path.read_bytes()
    if not raw_bytes.startswith(b"%PDF-"):
        return info

    if b"/Subtype /Type3" in raw_bytes or b"/Subtype/Type3" in raw_bytes:
        info["probed"] = True
        info["type3_fonts"] = raw_bytes.count(b"/Subtype /Type3") + raw_bytes.count(b"/Subtype/Type3")
    elif len(raw_bytes) >= 512 and b"corrupt" not in raw_bytes[:128].lower():
        info["probed"] = True

    pdffonts_bin = shutil.which("pdffonts")
    if pdffonts_bin:
        try:
            res = subprocess.run(
                [pdffonts_bin, str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = res.stdout.strip().splitlines()
                if len(lines) >= 2:
                    info["probed"] = True
                    t3 = 0
                    unemb = 0
                    total = 0
                    for ln in lines[2:]:
                        parts = ln.split()
                        if len(parts) >= 5:
                            total += 1
                            if "Type 3" in ln:
                                t3 += 1
                            yn_tokens = [p for p in parts if p in ("yes", "no")]
                            if yn_tokens and yn_tokens[0] == "no":
                                unemb += 1
                    info["total_fonts"] = total
                    info["type3_fonts"] = max(info["type3_fonts"], t3)
                    info["unembedded_fonts"] = unemb
        except Exception:
            pass

    pdfimages_bin = shutil.which("pdfimages")
    if pdfimages_bin:
        try:
            res_img = subprocess.run(
                [pdfimages_bin, "-list", str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res_img.returncode == 0 and res_img.stdout.strip():
                lines = res_img.stdout.strip().splitlines()
                ppis: list[int] = []
                for ln in lines[2:]:
                    cols = ln.split()
                    if len(cols) >= 14 and cols[2] == "image":
                        try:
                            x_ppi = int(cols[12])
                            y_ppi = int(cols[13])
                            if x_ppi > 0 and y_ppi > 0:
                                ppis.append(min(x_ppi, y_ppi))
                        except ValueError:
                            pass
                info["raster_images"] = len(ppis)
                if ppis:
                    info["min_image_ppi"] = min(ppis)
        except Exception:
            pass

    return info


_TAILWIND_SRGB_HEX: dict[str, str] = {
    "white": "#ffffff",
    "black": "#000000",
    "slate-50": "#f8fafc",
    "slate-100": "#f1f5f9",
    "slate-200": "#e2e8f0",
    "slate-300": "#cbd5e1",
    "slate-400": "#94a3b8",
    "slate-500": "#64748b",
    "slate-600": "#475569",
    "slate-700": "#334155",
    "slate-800": "#1e293b",
    "slate-900": "#0f172a",
    "slate-950": "#020617",
    "gray-50": "#f9fafb",
    "gray-100": "#f3f4f6",
    "gray-200": "#e5e7eb",
    "gray-300": "#d1d5db",
    "gray-400": "#9ca3af",
    "gray-500": "#6b7280",
    "gray-600": "#4b5563",
    "gray-700": "#374151",
    "gray-800": "#1f2937",
    "gray-900": "#111827",
    "gray-950": "#030712",
    "zinc-50": "#fafafa",
    "zinc-100": "#f4f4f5",
    "zinc-400": "#a1a1aa",
    "zinc-500": "#71717a",
    "zinc-800": "#27272a",
    "zinc-900": "#18181b",
    "zinc-950": "#09090b",
}


def _audit_css_token_wcag_contrast(css_and_ui_text: str) -> dict[str, Any]:
    """
    Extract semantic surface/background (`--*-bg*`, `--*-surface*`, `--*-canvas*`) and primary text
    (`--*-text*`, `--*-ink*`, `--*-fg*`) hex tokens (including 3/4/6/8-digit hex, `light-dark(...)`,
    and Tailwind `bg-*` + `text-*` class pairs) and compute worst-case WCAG 2.1 contrast ratio.
    """
    blocks = re.findall(r"\{([^{}]+)\}", css_and_ui_text)
    worst_cr = 21.0
    worst_pair = ""
    checked_pairs = 0

    def _is_bg_var(k: str) -> bool:
        kl = k.lower()
        return any(
            t in kl for t in ("bg-0", "bg-1", "bg-surface", "bg-primary", "surface-primary", "bg-canvas", "--bg:")
        ) or (
            any(t in kl for t in ("bg", "surface", "canvas"))
            and not any(
                x in kl for x in ("-2", "-3", "border", "hover", "accent", "glow", "badge", "elevated", "chip", "glass")
            )
        )

    def _is_fg_var(k: str) -> bool:
        kl = k.lower()
        return any(
            t in kl
            for t in ("ink-0", "ink-1", "text-primary", "ink-primary", "fg-primary", "--text:", "--ink:", "--fg:")
        ) or (
            any(t in kl for t in ("text", "ink", "fg"))
            and not any(
                m in kl for m in ("-2", "-3", "muted", "dim", "sub", "secondary", "disabled", "faint", "tertiary")
            )
        )

    var_re = re.compile(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\b")
    ld_re = re.compile(r"(--[\w-]+)\s*:\s*light-dark\(\s*(#[0-9a-fA-F]{3,8})\s*,\s*(#[0-9a-fA-F]{3,8})\s*\)")

    scopes = blocks if any(var_re.search(b) or ld_re.search(b) for b in blocks) else [css_and_ui_text]
    for blk in scopes:
        vars_in_blk = dict(var_re.findall(blk))
        if vars_in_blk:
            bgs = {k: v for k, v in vars_in_blk.items() if _is_bg_var(k)}
            fgs = {k: v for k, v in vars_in_blk.items() if _is_fg_var(k)}
            for bk, bv in bgs.items():
                for fk, fv in fgs.items():
                    cr = compute_wcag_contrast_ratio(bv, fv)
                    checked_pairs += 1
                    if cr < worst_cr:
                        worst_cr = cr
                        worst_pair = f"{fk} ({fv}) on {bk} ({bv})"
            if "--mt-accent" in vars_in_blk and "--mt-accent-contrast" in vars_in_blk:
                cr_btn = compute_wcag_contrast_ratio(vars_in_blk["--mt-accent"], vars_in_blk["--mt-accent-contrast"])
                checked_pairs += 1
                if cr_btn < worst_cr:
                    worst_cr = cr_btn
                    worst_pair = (
                        f"--mt-accent-contrast ({vars_in_blk['--mt-accent-contrast']}) "
                        f"on --mt-accent ({vars_in_blk['--mt-accent']})"
                    )

        ld_matches = ld_re.findall(blk)
        if ld_matches:
            light_vars = {k: l_hex for k, l_hex, _ in ld_matches}
            dark_vars = {k: d_hex for k, _, d_hex in ld_matches}
            for mode_name, mode_map in (("light", light_vars), ("dark", dark_vars)):
                bgs = {k: v for k, v in mode_map.items() if _is_bg_var(k)}
                fgs = {k: v for k, v in mode_map.items() if _is_fg_var(k)}
                for bk, bv in bgs.items():
                    for fk, fv in fgs.items():
                        cr = compute_wcag_contrast_ratio(bv, fv)
                        checked_pairs += 1
                        if cr < worst_cr:
                            worst_cr = cr
                            worst_pair = f"{fk} ({fv} {mode_name}) on {bk} ({bv} {mode_name})"

    # Audit explicit Tailwind bg-* + text-* utility pairs on the same element class attribute
    class_attr_re = re.compile(r"""class(?:Name)?\s*=\s*["'`]([^"'`]+)["'`]""")
    for cm in class_attr_re.finditer(css_and_ui_text):
        tokens = cm.group(1).split()
        bg_toks = [t[3:] for t in tokens if t.startswith("bg-") and t[3:] in _TAILWIND_SRGB_HEX]
        fg_toks = [t[5:] for t in tokens if t.startswith("text-") and t[5:] in _TAILWIND_SRGB_HEX]
        for bt in bg_toks:
            for ft in fg_toks:
                cr = compute_wcag_contrast_ratio(_TAILWIND_SRGB_HEX[bt], _TAILWIND_SRGB_HEX[ft])
                checked_pairs += 1
                if cr < worst_cr:
                    worst_cr = cr
                    worst_pair = f"text-{ft} ({_TAILWIND_SRGB_HEX[ft]}) on bg-{bt} ({_TAILWIND_SRGB_HEX[bt]})"

    return {
        "checked_pairs": checked_pairs,
        "min_contrast_ratio": round(worst_cr, 2) if checked_pairs > 0 else None,
        "worst_pair": worst_pair,
    }
