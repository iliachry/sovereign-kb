"""
Sovereign KB — Autonomous Human-Like Video Playback Auditor (`video_playback.py`).

Provides full multimodal video playback inspection across the Sovereign Organon:
1. Native Video Playback Stream: sends the raw `.mp4` video binary directly to Amazon Nova Pro
   via AWS Bedrock Converse API, performing end-to-end continuous playback inspection
   (pacing, motion glitches, UI legibility, narrative flow, watchability score).
2. Keyframe Extraction & Triad Routing: optionally extracts uniform timeline snapshots
   and evaluates via OpenCode parallel Tri-Council (Fable 5.1 x Astra).
3. GWI / Sovereign Organon integration: writes structured `evidence/video_audit.json`
   and generates human-readable markdown reports.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_PROMPT = """You are conducting an autonomous human-like playback audit of this video deliverable.
Act as a strict, expert human director and reviewer who is watching the video playback from beginning to end.

Evaluate and report across 5 specific criteria:
1. Visual Pacing & Camera Transitions: Does the sequence move smoothly or are there awkward camera jerks, jarring cuts, or dead-time stalls?
2. UI & Typography Legibility: Are labels, text overlays, subtitles, and graphics clear, sharp, well-contrasted, and readable without squinting?
3. Narrative & Demonstration Flow: What key phases are shown? Does the storyline make sense from an engineering/product perspective?
4. Glitch & Defect Detection: Did you detect any frozen frames, render tearing, visual pop-in, audio-visual desyncs, or corrupt frames?
5. Final Verdict: Explicitly state "APPROVED FOR RELEASE" or "REVISE", along with an overall Watchability Score out of 10 and a concise summary.
"""


def _ensure_bedrock_env() -> None:
    bedrock_env = Path.home() / ".config" / "bedrock" / "bedrock.env"
    if bedrock_env.is_file():
        try:
            for line in bedrock_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.replace("export ", "").strip()
                    v = v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass


def audit_video_playback_native(
    video_path: Path,
    model_id: str = "amazon.nova-pro-v1:0",
    prompt: Optional[str] = None,
    region_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends the entire raw video bytes directly to Amazon Bedrock Converse API
    for end-to-end human playback inspection.
    """
    _ensure_bedrock_env()
    
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    ext = video_path.suffix.lstrip(".").lower()
    if ext not in ("mp4", "mov", "mkv", "webm"):
        raise ValueError(f"Unsupported video container: .{ext} (expected mp4, mov, mkv, webm)")

    video_bytes = video_path.read_bytes()
    size_mb = len(video_bytes) / (1024 * 1024)

    try:
        import boto3
    except ImportError:
        raise RuntimeError("`boto3` is required for Bedrock native video playback audit. Run `uv pip install boto3`.")

    active_region = (
        region_name
        or os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("AWS_REGION")
        or "eu-central-1"
    )
    client = boto3.client("bedrock-runtime", region_name=active_region)
    active_prompt = prompt or DEFAULT_PROMPT

    t0 = time.perf_counter()
    response = client.converse(
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "video": {
                            "format": ext,
                            "source": {"bytes": video_bytes},
                        }
                    },
                    {
                        "text": active_prompt,
                    },
                ],
            }
        ],
    )
    latency_s = time.perf_counter() - t0

    content_blocks = response.get("output", {}).get("message", {}).get("content", [])
    raw_text = content_blocks[0].get("text", "") if content_blocks else ""
    usage = response.get("usage", {})

    verdict_str = "REVISE"
    if "APPROVED FOR RELEASE" in raw_text or "VERDICT: APPROVED" in raw_text.upper():
        verdict_str = "APPROVED"
    elif "APPROVED" in raw_text.split("Final Verdict:")[-1].upper():
        verdict_str = "APPROVED"

    return {
        "status": "PASS" if verdict_str == "APPROVED" else "FAIL",
        "verdict": verdict_str,
        "video_path": str(video_path.resolve()),
        "file_name": video_path.name,
        "file_size_bytes": len(video_bytes),
        "file_size_mb": round(size_mb, 2),
        "model": model_id,
        "region": active_region,
        "latency_s": round(latency_s, 2),
        "usage": usage,
        "review_markdown": raw_text,
    }


def extract_timeline_snapshots(
    video_path: Path,
    output_dir: Path,
    num_frames: int = 12,
) -> List[Path]:
    """
    Extracts evenly-spaced snapshot frames across the video duration using ffmpeg.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    ffprobe_bin = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

    if not Path(ffmpeg_bin).exists() or not Path(ffprobe_bin).exists():
        raise RuntimeError("ffmpeg/ffprobe required for frame extraction.")

    # Get duration
    cmd_dur = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        dur_str = subprocess.check_output(cmd_dur, text=True).strip()
        duration = float(dur_str)
    except Exception as e:
        raise RuntimeError(f"Could not determine video duration: {e}")

    frames: List[Path] = []
    step = duration / (num_frames + 1)
    for i in range(1, num_frames + 1):
        t_sec = round(i * step, 2)
        out_frame = output_dir / f"frame_{i:02d}_t{t_sec:.1f}s.jpg"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-ss", str(t_sec),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(out_frame),
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0 and out_frame.is_file():
            frames.append(out_frame)

    return frames


def audit_video_triad_keyframes(
    video_path: Path,
    snapshots_dir: Path,
    num_frames: int = 12,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extracts keyframes and executes parallel audit via `oc triad` (Fable 5.1 x Astra).
    """
    frames = extract_timeline_snapshots(video_path, snapshots_dir, num_frames=num_frames)
    oc_bin = shutil.which("oc") or str(Path.home() / ".local" / "bin" / "oc")
    if not Path(oc_bin).exists():
        raise RuntimeError("`oc` CLI is required for Triad keyframe audit.")

    active_prompt = prompt or (
        f"Conduct a rigorous cinematic and technical review of these {len(frames)} chronological keyframes "
        f"extracted from {video_path.name}. Evaluate visual pacing, layout, typography legibility, and technical accuracy. "
        "Conclude with explicit verdict: APPROVED FOR RELEASE or REVISE."
    )

    args = [oc_bin, "triad", active_prompt]
    for f in frames:
        args.extend(["-f", str(f)])

    t0 = time.perf_counter()
    proc = subprocess.run(args, capture_output=True, text=True)
    latency_s = time.perf_counter() - t0

    output_text = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
    verdict = "APPROVED" if "APPROVED" in output_text else "REVISE"

    return {
        "status": "PASS" if verdict == "APPROVED" else "FAIL",
        "verdict": verdict,
        "video_path": str(video_path.resolve()),
        "frames_extracted": len(frames),
        "frames_dir": str(snapshots_dir.resolve()),
        "latency_s": round(latency_s, 2),
        "triad_output": output_text,
    }


def cmd_video_audit(args: argparse.Namespace) -> None:
    """CLI handler for `kb video audit`."""
    target = Path(args.target).expanduser().resolve()
    if target.is_dir():
        # Find primary mp4
        candidates = sorted(
            list(target.glob("*.mp4"))
            + list(target.glob("artifacts/*.mp4"))
            + list(target.glob("docs/**/*.mp4")),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        # prefer compressed companion if <= 20MB
        comp = [c for c in candidates if "compressed" in c.name or "20mb" in c.name]
        target_video = comp[0] if comp else (candidates[0] if candidates else None)
        if not target_video:
            print(f"❌ Error: No `.mp4` video files found in {target}")
            sys.exit(1)
    else:
        target_video = target

    print(f"🎬 Sovereign Video Playback Audit: {target_video.name} ({target_video.stat().st_size / 1024 / 1024:.2f} MB)")
    
    mode = getattr(args, "mode", "native")
    result: Dict[str, Any] = {}

    if mode in ("native", "all"):
        print(f"📡 Sending raw video binary to Amazon Bedrock ({args.model})...")
        native_res = audit_video_playback_native(
            target_video,
            model_id=args.model,
            prompt=getattr(args, "prompt", None),
        )
        result["native_playback"] = native_res
        print(f"\n=== Native Video Playback Review ({native_res['model']}) ===")
        print(native_res["review_markdown"])
        print(f"\nVerdict: {native_res['verdict']} (Latency: {native_res['latency_s']}s)")

    if mode in ("triad", "all"):
        snap_dir = target_video.parent / ".video_snapshots"
        print(f"\n📸 Extracting timeline keyframes to {snap_dir}...")
        triad_res = audit_video_triad_keyframes(
            target_video,
            snapshots_dir=snap_dir,
            num_frames=getattr(args, "frames", 12),
        )
        result["triad_keyframes"] = triad_res
        print(f"\n=== Triad (Fable 5.1 x Astra) Evaluation ===")
        print(triad_res["triad_output"][:1200] + ("..." if len(triad_res["triad_output"]) > 1200 else ""))
        print(f"\nTriad Verdict: {triad_res['verdict']}")

    if getattr(args, "write_evidence", False):
        ev_dir = target_video.parent if target_video.parent.name == "evidence" else target_video.parent / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        ev_file = ev_dir / "video_audit.json"
        ev_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\n✓ Saved video audit evidence to {ev_file}")

    # Check exit code
    overall_ok = True
    if "native_playback" in result and result["native_playback"]["verdict"] != "APPROVED":
        overall_ok = False
    if "triad_keyframes" in result and result["triad_keyframes"]["verdict"] != "APPROVED":
        overall_ok = False

    if getattr(args, "strict", False) and not overall_ok:
        sys.exit(1)


def register_video_subparser(subparsers: argparse._SubParsersAction, show_all_aliases: bool = False) -> None:
    """Register `skb video` subcommand."""
    p = subparsers.add_parser(
        "video",
        help="Autonomous human-like video playback auditor (native video stream + triad keyframes)",
    )
    v_sub = p.add_subparsers(dest="video_subcmd")
    
    p_audit = v_sub.add_parser("audit", help="Run cognitive playback audit on a video file or project")
    p_audit.add_argument("target", help="Path to video file (.mp4) or project directory")
    p_audit.add_argument(
        "--mode",
        choices=("native", "triad", "all"),
        default="native",
        help="Audit mode: `native` (full video to Nova Pro), `triad` (keyframes to Fable/Astra), or `all` (both)",
    )
    p_audit.add_argument(
        "--model",
        default="amazon.nova-pro-v1:0",
        help="Bedrock video model for native playback (default: amazon.nova-pro-v1:0)",
    )
    p_audit.add_argument(
        "--frames",
        type=int,
        default=12,
        help="Number of keyframes to extract for triad mode (default: 12)",
    )
    p_audit.add_argument("--prompt", help="Custom audit evaluation prompt")
    p_audit.add_argument("--write-evidence", action="store_true", help="Save audit result to evidence/video_audit.json")
    p_audit.add_argument("--strict", action="store_true", help="Exit 1 if verdict is REVISE")
    p_audit.set_defaults(func=cmd_video_audit)
