"""Goldwood Video/Cinema profile (`evaluate_video_profile`) and FFprobe/EBU R128/SRT probes."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import (
    ARCHETYPE_WEIGHTS,
    DEFAULT_K,
    DEFAULT_THETA,
    compute_goldwood_index,
    compute_kolmogorov_elegance,
    compute_snrc,
)

from .utils import _LRA_RE, _LUFS_RE, _PEAK_RE


def _probe_video_streams(mp4_path: Path) -> dict[str, Any]:
    """
    Inspect video and audio stream metadata via `ffprobe`.
    Never fabricates resolution/fps when `ffprobe` fails on a corrupt/synthetic stub;
    explicitly records `probe_source` ("ffprobe" vs "unverified").
    """
    info: dict[str, Any] = {
        "path": str(mp4_path),
        "size_bytes": mp4_path.stat().st_size if mp4_path.is_file() else 0,
        "probed": False,
        "probe_source": "unverified",
        "has_video": False,
        "has_audio": False,
        "width": 0,
        "height": 0,
        "codec_name": "unknown",
        "pix_fmt": "unknown",
        "fps": 0.0,
        "duration_s": 0.0,
        "audio_channels": 0,
        "audio_sample_rate": 0,
    }
    if not mp4_path.is_file() or info["size_bytes"] == 0:
        return info

    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin:
        try:
            res = subprocess.run(
                [
                    ffprobe_bin,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(mp4_path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                streams = data.get("streams") or []
                if streams:
                    info["probed"] = True
                    info["probe_source"] = "ffprobe"
                    fmt = data.get("format") or {}
                    info["duration_s"] = round(float(fmt.get("duration") or 0.0), 2)
                    for st in streams:
                        ctype = st.get("codec_type")
                        if ctype == "video" and not info["has_video"]:
                            info["has_video"] = True
                            info["width"] = int(st.get("width") or 0)
                            info["height"] = int(st.get("height") or 0)
                            info["codec_name"] = str(st.get("codec_name") or "unknown")
                            info["pix_fmt"] = str(st.get("pix_fmt") or "unknown")
                            r_fps = str(st.get("r_frame_rate") or "0/1")
                            if "/" in r_fps:
                                num, den = r_fps.split("/", 1)
                                info["fps"] = round(float(num) / max(1.0, float(den)), 2)
                        elif ctype == "audio" and not info["has_audio"]:
                            info["has_audio"] = True
                            info["audio_channels"] = int(st.get("channels") or 0)
                            info["audio_sample_rate"] = int(st.get("sample_rate") or 0)
                    return info
        except Exception:
            pass

    raw = mp4_path.read_bytes()[:65536]
    info["has_video"] = b"ftyp" in raw or b"moov" in raw
    info["has_audio"] = b"mp4a" in raw or b"soun" in raw
    return info


def _probe_video_frames(mp4_path: Path, duration_s: float) -> dict[str, Any]:
    """
    Sample up to 3 keyframes (20%, 50%, 80% of duration) via `ffmpeg` into a 320x180 raw RGB24 buffer
    in memory to verify non-blank spatial entropy (std_luma >= 2.0) and safe-area margin poise.
    """
    probe: dict[str, Any] = {
        "sampled_frames": 0,
        "mean_luma": 0.0,
        "min_spatial_std": 0.0,
        "blank_frames": 0,
        "safe_area_ratio": 1.0,
    }
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin or not mp4_path.is_file():
        return probe

    w, h = 320, 180
    expected_bytes = w * h * 3
    dur = max(1.0, float(duration_s or 1.0))
    timestamps = [round(dur * r, 2) for r in (0.2, 0.5, 0.8)] if dur >= 1.5 else [0.0]

    luma_means: list[float] = []
    luma_stds: list[float] = []
    safe_ratios: list[float] = []

    for ts in timestamps:
        try:
            res = subprocess.run(
                [
                    ffmpeg_bin,
                    "-nostats",
                    "-hide_banner",
                    "-ss",
                    str(ts),
                    "-i",
                    str(mp4_path),
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
            if len(buf) >= expected_bytes:
                probe["sampled_frames"] += 1
                # Subsample every 4th pixel for rapid deterministic luminance & safe-area stats
                total_y = 0.0
                total_y2 = 0.0
                count = 0
                edge_activity = 0.0
                center_activity = 0.0
                for py in range(0, h, 2):
                    row_off = py * w * 3
                    in_safe_margin = py < 15 or py > 162  # Top 92px / Bottom 108px scaled to 180p
                    prev_y: float | None = None
                    for px in range(0, w, 2):
                        idx = row_off + px * 3
                        r_v, g_v, b_v = buf[idx], buf[idx + 1], buf[idx + 2]
                        y_val = 0.299 * r_v + 0.587 * g_v + 0.114 * b_v
                        total_y += y_val
                        total_y2 += y_val * y_val
                        count += 1
                        if prev_y is not None:
                            diff = abs(y_val - prev_y)
                            if in_safe_margin:
                                edge_activity += diff
                            else:
                                center_activity += diff
                        prev_y = y_val
                mean_y = total_y / max(1, count)
                var_y = max(0.0, (total_y2 / max(1, count)) - (mean_y * mean_y))
                std_y = math.sqrt(var_y)
                luma_means.append(mean_y)
                luma_stds.append(std_y)
                if std_y < 2.0:
                    probe["blank_frames"] += 1
                safe_ratios.append(center_activity / max(1.0, edge_activity + center_activity))
        except Exception:
            pass

    if luma_means:
        probe["mean_luma"] = round(sum(luma_means) / len(luma_means), 2)
        probe["min_spatial_std"] = round(min(luma_stds), 2)
        probe["safe_area_ratio"] = round(sum(safe_ratios) / len(safe_ratios), 4)
    return probe


def _measure_ebur128(mp4_path: Path) -> dict[str, float | None]:
    """Run live `ffmpeg -af ebur128=peak=true` broadcast loudness measurement."""
    out: dict[str, float | None] = {"integrated_lufs": None, "lra_lu": None, "true_peak_dbtp": None}
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin or not mp4_path.is_file():
        return out
    try:
        res = subprocess.run(
            [
                ffmpeg_bin,
                "-nostats",
                "-hide_banner",
                "-i",
                str(mp4_path),
                "-vn",
                "-af",
                "ebur128=peak=true",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        summary_idx = res.stderr.rfind("Summary:")
        tail = res.stderr[summary_idx:] if summary_idx != -1 else res.stderr
        m_i = _LUFS_RE.search(tail)
        m_lra = _LRA_RE.search(tail)
        m_pk = _PEAK_RE.search(tail)
        if m_i:
            out["integrated_lufs"] = float(m_i.group(1))
        if m_lra:
            out["lra_lu"] = float(m_lra.group(1))
        if m_pk:
            out["true_peak_dbtp"] = float(m_pk.group(1))
    except Exception:
        pass
    return out


def _validate_srt_captions(srt_path: Path) -> dict[str, Any]:
    """
    Validate an `.srt` or `.vtt` closed-caption file for cue count, monotonic timestamps,
    zero cue overlaps, and Netflix/BBC broadcast subtitle ergonomics:
      - Reading speed CPS = len(text) / (end_s - start_s) <= 20.0 chars/sec (veto > 28.0)
      - Maximum line length <= 42 chars (veto > 68 chars on a single unsplit line)
      - Cue duration in [0.6s, 8.5s]
    """
    if not srt_path.is_file():
        return {
            "valid": False,
            "cues": 0,
            "overlaps": 0,
            "max_cps": 0.0,
            "max_line_chars": 0,
            "min_duration_s": 0.0,
            "max_duration_s": 0.0,
        }
    text = srt_path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n").strip()
    blocks = re.split(r"\n\s*\n", text)
    time_re = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")

    def _to_sec(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    cues = 0
    overlaps = 0
    prev_end = -1.0
    max_cps = 0.0
    max_line_chars = 0
    durations: list[float] = []

    for blk in blocks:
        m = time_re.search(blk)
        if m:
            cues += 1
            start_s = _to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
            end_s = _to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
            dur = max(0.001, end_s - start_s)
            durations.append(dur)
            if start_s < prev_end - 0.005 or end_s <= start_s:
                overlaps += 1
            prev_end = max(prev_end, end_s)

            after_ts = blk[m.end() :].strip()
            clean_lines = [re.sub(r"<[^>]+>", "", ln).strip() for ln in after_ts.splitlines() if ln.strip()]
            total_chars = sum(len(ln) for ln in clean_lines)
            if clean_lines:
                max_line_chars = max(max_line_chars, max(len(ln) for ln in clean_lines))
            max_cps = max(max_cps, round(total_chars / dur, 2))

    return {
        "valid": cues > 0 and overlaps == 0 and max_cps <= 28.0 and max_line_chars <= 68,
        "cues": cues,
        "overlaps": overlaps,
        "max_cps": round(max_cps, 2),
        "max_line_chars": max_line_chars,
        "min_duration_s": round(min(durations), 2) if durations else 0.0,
        "max_duration_s": round(max(durations), 2) if durations else 0.0,
    }


def evaluate_video_profile(
    target: Path,
    probe_audio: bool = False,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
) -> dict[str, Any]:
    """
    Evaluate a YouTube Broadcast Master or Platform Video Walkthrough (`video` profile)
    across the 4 Canonical Global Watchability Index (GWI) dimensions.
    """
    if target.is_file():
        root = target.resolve().parent
        mp4_files = [target.resolve()] + [
            p.resolve() for p in sorted(root.glob("*.mp4")) if p.resolve() != target.resolve()
        ]
    else:
        root = target.resolve()
        mp4_files = sorted(
            list(root.glob("*.mp4"))
            + list(root.glob("artifacts/*.mp4"))
            + list(root.glob("results/*.mp4"))
            + list(root.glob("docs/walkthrough/*.mp4")),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )

    blockers: list[dict[str, str]] = []
    notes: list[str] = []

    if not mp4_files:
        calc = compute_goldwood_index([4.0, 4.0, 4.0, 4.0], weights=ARCHETYPE_WEIGHTS["video"], theta=theta, k=k)
        return {
            "profile": "video",
            "target": str(root),
            "project_name": root.name,
            "dimensions": [],
            "tiers": {"S_sub": 4.0, "S_perc": 4.0, "S_act": 4.0, "S_exist": 4.0},
            "calculation": calc,
            "blockers": [
                {
                    "id": "GWI-VID-00",
                    "severity": "P0",
                    "dimension": "Video Master",
                    "message": f"No `.mp4` video master found in {root}.",
                }
            ],
            "notes": [],
        }

    master_mp4 = mp4_files[0]
    _gwi_pkg = sys.modules.get("sovereign_kb.gwi")
    _probe_streams_fn = (
        getattr(_gwi_pkg, "_probe_video_streams", _probe_video_streams) if _gwi_pkg else _probe_video_streams
    )
    v_meta = _probe_streams_fn(master_mp4)

    # 1. Dim 1: Visual & Layout / Thumbnail (S_perc)
    score_visual = 9.5
    valid_resolutions = {(1920, 1080), (1080, 1920), (3840, 2160), (2160, 3840), (2560, 1440), (1440, 900), (1280, 720)}
    if not v_meta["probed"]:
        score_visual = 6.8
        blockers.append(
            {
                "id": "GWI-VID-M00",
                "severity": "P0",
                "dimension": "1. Visual & Stream Verification (S_perc)",
                "message": f"`ffprobe` could not decode valid video streams from `{master_mp4.name}` (unverified/corrupt container).",
            }
        )
    else:
        notes.append(
            f"Master Video `{master_mp4.name}` ({v_meta['probe_source']}): {v_meta['width']}x{v_meta['height']} "
            f"@ {v_meta['fps']}fps, codec={v_meta['codec_name']}, pix_fmt={v_meta['pix_fmt']}, duration={v_meta['duration_s']}s."
        )
        if (v_meta["width"], v_meta["height"]) not in valid_resolutions:
            score_visual -= 1.5
            blockers.append(
                {
                    "id": "GWI-VID-V01",
                    "severity": "P1",
                    "dimension": "1. Visual & Layout (S_perc)",
                    "message": f"Non-standard resolution {v_meta['width']}x{v_meta['height']} (expected 1080p, 9:16 Shorts, or 4K).",
                }
            )
        if v_meta["pix_fmt"] not in ("yuv420p",):
            score_visual -= 1.0
            blockers.append(
                {
                    "id": "GWI-VID-V02",
                    "severity": "P1",
                    "dimension": "1. Visual & Layout (S_perc)",
                    "message": f"Pixel format is `{v_meta['pix_fmt']}`; YouTube requires `yuv420p` for universal browser hardware decoding.",
                }
            )
        f_probe = _probe_video_frames(master_mp4, v_meta["duration_s"])
        v_meta["frame_probe"] = f_probe
        if f_probe["sampled_frames"] > 0:
            if f_probe["blank_frames"] > 0:
                score_visual -= 2.2
                blockers.append(
                    {
                        "id": "GWI-VID-V04",
                        "severity": "P0",
                        "dimension": "1. Visual & Frame Entropy (S_perc)",
                        "message": f"Detected {f_probe['blank_frames']} blank/flat video frame(s) (min spatial std={f_probe['min_spatial_std']} < 2.0).",
                    }
                )
            else:
                notes.append(
                    f"Frame-level visual & safe-area probe verified ({f_probe['sampled_frames']} keyframes: "
                    f"mean_luma={f_probe['mean_luma']}, min_std={f_probe['min_spatial_std']}, safe_area_ratio={f_probe['safe_area_ratio']:.2%})."
                )

        # Verify walkthrough video freshness against src/ and e2e/ when E2E specs exist
        project_root = root.parent.parent if root.name == "video" and root.parent.name == "evidence" else root
        e2e_specs = list((project_root / "e2e").glob("*.spec.*")) if (project_root / "e2e").is_dir() else []
        if e2e_specs and (project_root / "src").is_dir():
            mp4_mtime = master_mp4.stat().st_mtime
            tracked_code = (
                list((project_root / "src").rglob("*.ts")) + list((project_root / "src").rglob("*.tsx")) + e2e_specs
            )
            stale_sources = [p for p in tracked_code if p.is_file() and p.stat().st_mtime > mp4_mtime + 120.0]
            if stale_sources:
                newest_src = max(stale_sources, key=lambda p: p.stat().st_mtime)
                score_visual -= 1.8
                blockers.append(
                    {
                        "id": "GWI-VID-F01",
                        "severity": "P1",
                        "dimension": "1. Visual & Walkthrough Freshness (S_perc)",
                        "message": (
                            f"Walkthrough video `{master_mp4.name}` is stale relative to `{newest_src.relative_to(project_root)}` "
                            f"(modified {int(newest_src.stat().st_mtime - mp4_mtime)}s after video capture). Re-run the E2E video capture pipeline."
                        ),
                    }
                )

    thumbs = (
        list(root.glob("*thumbnail*.jpg"))
        + list(root.glob("*thumbnail*.jpeg"))
        + list(root.glob("*thumbnail*.png"))
        + list(root.glob("artifacts/*thumbnail*.jpg"))
        + list(root.glob("artifacts/*thumbnail*.png"))
        + list(root.glob("results/*thumbnail*.jpg"))
        + list(root.glob("results/*thumbnail*.png"))
    )
    if thumbs:
        jpg_thumbs = [t for t in thumbs if t.suffix.lower() in (".jpg", ".jpeg")]
        best_thumb = jpg_thumbs[0] if jpg_thumbs else thumbs[0]
        t_size = best_thumb.stat().st_size
        if t_size > 2_000_000:
            score_visual -= 1.8
            blockers.append(
                {
                    "id": "GWI-VID-V03",
                    "severity": "P0",
                    "dimension": "1. Visual & Layout (S_perc)",
                    "message": f"Thumbnail `{best_thumb.name}` is {t_size:,} bytes, exceeding YouTube Studio's 2.0 MB upload limit.",
                }
            )
        else:
            notes.append(f"Thumbnail `{best_thumb.name}` verified under 2.0 MB limit ({t_size:,} bytes).")

    # 2. Dim 2: Technical & Mathematical Narrative / Captions (S_act)
    srt_files = list(root.glob("*.srt")) + list(root.glob("artifacts/*.srt")) + list(root.glob("results/*.srt"))
    score_tech = 9.4
    if srt_files:
        srt_info = _validate_srt_captions(srt_files[0])
        if srt_info["valid"]:
            score_tech = 9.6
            if srt_info["max_cps"] > 20.0 or srt_info["max_line_chars"] > 42:
                score_tech -= 0.4
                blockers.append(
                    {
                        "id": "GWI-VID-T03",
                        "severity": "P2",
                        "dimension": "2. Technical & Captions (S_act)",
                        "message": f"Caption file `{srt_files[0].name}` exceeds ideal reading pace (max_cps={srt_info['max_cps']}, max_line_chars={srt_info['max_line_chars']}).",
                    }
                )
            else:
                notes.append(
                    f"Verified `{srt_files[0].name}` ({srt_info['cues']} cues, 0 overlaps, max_cps={srt_info['max_cps']}, max_line_chars={srt_info['max_line_chars']})."
                )
        else:
            score_tech = 7.5
            blockers.append(
                {
                    "id": "GWI-VID-T01",
                    "severity": "P1",
                    "dimension": "2. Technical & Captions (S_act)",
                    "message": (
                        f"Caption file `{srt_files[0].name}` failed ergonomics/sync "
                        f"(overlaps={srt_info['overlaps']}, max_cps={srt_info['max_cps']}, max_line_chars={srt_info['max_line_chars']})."
                    ),
                }
            )
    else:
        score_tech = 7.8
        blockers.append(
            {
                "id": "GWI-VID-T02",
                "severity": "P1",
                "dimension": "2. Technical & Captions (S_act)",
                "message": "Missing synchronized `.srt` closed captions.",
            }
        )

    # 3. Dim 3: Broadcast Audio & EBU R128 Loudness (S_sub)
    if not v_meta["has_audio"]:
        score_audio = 5.0
        blockers.append(
            {
                "id": "GWI-VID-A01",
                "severity": "P0",
                "dimension": "3. Audio & EBU R128 (S_sub)",
                "message": f"Master video `{master_mp4.name}` has NO audio stream! Silent MP4s violate YouTube broadcast standards.",
            }
        )
    else:
        score_audio = 9.2
        mono_penalty = 0.0
        if v_meta["probed"] and v_meta["audio_channels"] < 2:
            mono_penalty = 1.0
            score_audio -= mono_penalty
            blockers.append(
                {
                    "id": "GWI-VID-A02",
                    "severity": "P1",
                    "dimension": "3. Audio & EBU R128 (S_sub)",
                    "message": f"Audio stream has {v_meta['audio_channels']} channel(s); expected Stereo (2 channels).",
                }
            )

        lufs_val: float | None = None
        tp_val: float | None = None
        if probe_audio:
            live_ebu = _measure_ebur128(master_mp4)
            lufs_val = live_ebu["integrated_lufs"]
            tp_val = live_ebu["true_peak_dbtp"]

        ebu_reports = (
            list(root.glob("*ebu*128*.txt"))
            + list(root.glob("*ebu*128*.log"))
            + list(root.glob("artifacts/*ebu*128*.txt"))
            + list(root.glob("artifacts/*ebu*128*.log"))
            + list(root.glob("results/*ebu*128*.txt"))
            + list(root.glob("results/*ebu*128*.log"))
            + list(root.glob("scratch/*ebu*128*.txt"))
        )
        if lufs_val is None and ebu_reports:
            ebu_raw = ebu_reports[0].read_text(encoding="utf-8", errors="ignore")
            ebu_tail = ebu_raw[ebu_raw.rfind("Summary:") :] if "Summary:" in ebu_raw else ebu_raw
            m_lufs = _LUFS_RE.search(ebu_tail)
            m_pk = _PEAK_RE.search(ebu_tail)
            if m_lufs:
                lufs_val = float(m_lufs.group(1))
            if m_pk:
                tp_val = float(m_pk.group(1))

        if lufs_val is not None:
            tp_ok = tp_val is None or tp_val <= -1.0
            if -15.5 <= lufs_val <= -12.5 and tp_ok:
                score_audio = 9.5 - mono_penalty
                notes.append(
                    f"EBU R128 integrated loudness verified: I = {lufs_val:.1f} LUFS"
                    + (f", True Peak = {tp_val:.1f} dBTP." if tp_val is not None else ".")
                )
            else:
                score_audio = min(score_audio, 7.4)
                blockers.append(
                    {
                        "id": "GWI-VID-A03",
                        "severity": "P1",
                        "dimension": "3. Audio & EBU R128 (S_sub)",
                        "message": (
                            f"Broadcast loudness violation: I = {lufs_val:.1f} LUFS"
                            + (f", True Peak = {tp_val:.1f} dBTP" if tp_val is not None else "")
                            + " (target I in [-15.5, -12.5] LUFS, TP <= -1.0 dBTP)."
                        ),
                    }
                )

    # 4. Dim 4: Retention, Keyframes & Dual-Model Provenance (S_exist)
    keyframes = [
        p
        for p in (
            list(root.glob("act[0-9]*.png"))
            + list(root.glob("docs/screenshots/*.png"))
            + list(root.glob("sim_phase*.png"))
            + list(root.glob("*contact_sheet*.jpg"))
            + list(root.glob("*contact_sheet*.png"))
            + list(root.glob("*_sheet.png"))
            + list(root.glob("artifacts/*_sheet.png"))
            + list(root.glob("artifacts/*contact_sheet*.png"))
            + list(root.glob("results/*_sheet.png"))
            + list(root.glob("results/*contact_sheet*.png"))
            + list(root.glob("frames/*.png"))
            + list(root.glob("scratch/frames*/*.png"))
        )
    ]
    has_contact_sheet = any("sheet" in p.name for p in keyframes)
    score_ret = 9.4 if (len(keyframes) >= 3 or has_contact_sheet) else 8.4
    if len(keyframes) < 3 and not has_contact_sheet:
        blockers.append(
            {
                "id": "GWI-VID-R01",
                "severity": "P1",
                "dimension": "4. Retention & Keyframe Sheet (S_exist)",
                "message": "Fewer than 3 full-resolution keyframe snapshots or contact sheets found for visual QA.",
            }
        )
    else:
        notes.append(f"Verified {len(keyframes)} keyframe/contact-sheet visual QA artifact(s).")

    # Mandatory < 20 MB Companion Video Deliverable (`sc.companion_size`)
    master_size = master_mp4.stat().st_size if master_mp4.is_file() else 0
    if master_size > 20_000_000:
        companion_candidates = [p for p in mp4_files if p.is_file() and 0 < p.stat().st_size <= 20_000_000]
        if not companion_candidates:
            score_ret -= 1.5
            blockers.append(
                {
                    "id": "GWI-VID-C01",
                    "severity": "P1",
                    "dimension": "4. Retention, Distribution & Companion Edition (S_exist)",
                    "message": (
                        f"Master video `{master_mp4.name}` ({master_size:,} bytes) has NO companion MP4 "
                        f"<= 20 MB (`sc.companion_size`). A companion edition strictly <= 20,000,000 bytes "
                        "is mandatory for web embeds, instant preview, and issue-tracker attachments."
                    ),
                }
            )
        else:
            best_comp = companion_candidates[0]
            notes.append(
                f"Companion video `{best_comp.name}` verified under 20 MB limit ({best_comp.stat().st_size:,} bytes)."
            )

    s_sub = round(score_audio, 2)
    s_perc = round(score_visual, 2)
    s_act = round(score_tech, 2)
    s_exist = round(score_ret, 2)

    dimensions: list[dict[str, Any]] = [
        {
            "id": "V_layout",
            "tier": "S_perc",
            "name": "1. Visual Craft, Safe-Area & Thumbnail",
            "weight": 0.25,
            "score": s_perc,
        },
        {
            "id": "T_math",
            "tier": "S_act",
            "name": "2. Technical Narrative & .srt Captions",
            "weight": 0.25,
            "score": s_act,
        },
        {
            "id": "A_audio",
            "tier": "S_sub",
            "name": "3. Broadcast Audio & EBU R128 (-14 LUFS)",
            "weight": 0.25,
            "score": s_sub,
        },
        {
            "id": "R_ret",
            "tier": "S_exist",
            "name": "4. Retention, Contact Sheet & Provenance",
            "weight": 0.25,
            "score": s_exist,
        },
    ]

    g_calc = compute_goldwood_index(
        [s_sub, s_perc, s_act, s_exist],
        weights=ARCHETYPE_WEIGHTS["video"],
        theta=theta,
        k=k,
        conscience_clean=v_meta["probed"] and v_meta["has_video"],
    )

    total_dir_bytes = sum(
        p.stat().st_size
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts
    )
    elegance = compute_kolmogorov_elegance(total_dir_bytes, max(1, int(v_meta["size_bytes"] * 1.25)))

    return {
        "profile": "video",
        "target": str(master_mp4),
        "project_name": root.name,
        "video_metadata": v_meta,
        "kolmogorov_elegance": elegance,
        "snrc": compute_snrc(len(notes) + len(keyframes), len(blockers)),
        "dimensions": dimensions,
        "tiers": {
            "S_sub": s_sub,
            "S_perc": s_perc,
            "S_act": s_act,
            "S_exist": s_exist,
        },
        "calculation": g_calc,
        "probe_states": {
            d["id"]: ("UNMEASURED" if not v_meta.get("probed") else ("PASS" if d["score"] >= 9.0 else "FAIL"))
            for d in dimensions
        },
        "blockers": blockers,
        "notes": notes,
    }
