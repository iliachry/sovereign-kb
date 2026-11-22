"""Gate receipts, top-level target evaluator, evidence emission, reporting, and Tri-Council prompt builders."""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from sovereign_kb.gwi_core import (
    ARCHETYPE_WEIGHTS,
    DEFAULT_K,
    DEFAULT_THETA,
    DEFAULT_THRESHOLD_G,
    GWI_STANDARD_VERSION,
    compute_coupled_goldwood_g,
    compute_goldwood_index,
)

from .lenses_eval import evaluate_craft_lenses
from .profile_app import evaluate_app_profile
from .profile_kb import evaluate_kb_profile
from .profile_paper import evaluate_paper_profile
from .profile_sim import evaluate_sim_profile
from .profile_video import evaluate_video_profile
from .utils import detect_profile


def _compute_worktree_content_sha(root: Path) -> str:
    """Compute a canonical length-delimited SHA over tracked + untracked worktree files (delegates to Elenchus)."""
    from ..elenchus import _compute_worktree_content_sha as _canonical_worktree_sha

    return _canonical_worktree_sha(root)


def execute_or_verify_gate_receipt(target_dir: Path, execute_gate: bool = False) -> dict[str, Any]:
    """Run or validate a tree-hash-bound and worktree-content-bound behavioral gate receipt (`evidence/gate_receipt.json`)."""
    from ..audit_registry import compute_subproject_fingerprint, find_git_root

    root = target_dir if target_dir.is_dir() else target_dir.parent
    git_root = find_git_root(root)
    try:
        subpath = str(root.relative_to(git_root)) if root != git_root else "."
    except ValueError:
        subpath = "."
    fp = compute_subproject_fingerprint(git_root, subpath)
    tree_sha = str(fp.get("tree_sha") or "unknown")
    is_dirty = bool(fp.get("dirty", False))
    worktree_sha = _compute_worktree_content_sha(root)
    clean_git_tree = (not is_dirty) and (tree_sha not in ("unknown", "uncommitted"))
    receipt_path = root / "evidence" / "gate_receipt.json"

    if not execute_gate and receipt_path.is_file():
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                matches_content = (
                    clean_git_tree
                    and existing.get("tree_sha") == tree_sha
                    and existing.get("worktree_sha") == worktree_sha
                )
                existing["receipt_verified"] = bool(matches_content)
                existing["fresh_for_tree"] = bool(matches_content)
                return existing
        except Exception:
            pass

    if not execute_gate:
        return {
            "executed": False,
            "receipt_verified": False,
            "fresh_for_tree": False,
            "passed": True,
            "tree_sha": tree_sha,
            "worktree_sha": worktree_sha,
            "dirty": is_dirty,
            "command": None,
            "exit_code": None,
        }

    # Only reuse a recent passing receipt when the Git tree is clean, committed, and working-tree content SHA matches
    if receipt_path.is_file() and clean_git_tree:
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                isinstance(existing, dict)
                and existing.get("tree_sha") == tree_sha
                and existing.get("worktree_sha") == worktree_sha
                and not existing.get("dirty", False)
                and existing.get("exit_code") == 0
                and (time.time() - float(existing.get("timestamp_epoch", 0))) < 600
            ):
                existing["cached"] = True
                existing["receipt_verified"] = True
                existing["fresh_for_tree"] = True
                existing["reused_recent_receipt"] = True
                return existing
        except Exception:
            pass

    gate_script = root / "scripts" / "quality_gate.sh"
    if gate_script.is_file():
        cmd = ["bash", "scripts/quality_gate.sh"]
    elif (root / "pyproject.toml").is_file() or (root / "tests").is_dir():
        cmd = ["uv", "run", "pytest", "-q"] if shutil.which("uv") else ["python3", "-m", "pytest", "-q"]
    elif (root / "package.json").is_file():
        cmd = ["npm", "test", "--", "--run"]
    elif (root / "Cargo.toml").is_file():
        cmd = ["cargo", "test", "--quiet"]
    else:
        return {
            "executed": False,
            "receipt_verified": False,
            "fresh_for_tree": False,
            "passed": False,
            "tree_sha": tree_sha,
            "worktree_sha": worktree_sha,
            "dirty": is_dirty,
            "command": "<no quality_gate.sh or test runner found>",
            "exit_code": 127,
        }

    t0 = time.perf_counter()
    env = {**os.environ, "GWI_IN_GATE_EXECUTION": str(root.resolve())}
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
            start_new_session=True,
        )
        rc = proc.returncode
        raw_tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-600:]
        out_tail = raw_tail.replace(str(Path.home()) + "/", "~/")
    except subprocess.TimeoutExpired:
        rc = 124
        out_tail = "Quality gate timed out after 180s"

    dur_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    post_fp = compute_subproject_fingerprint(git_root, subpath)
    post_worktree_sha = _compute_worktree_content_sha(root)
    post_tree_sha = str(post_fp.get("tree_sha") or "unknown")
    post_dirty = bool(post_fp.get("dirty", False))
    source_mutated = bool(post_worktree_sha != worktree_sha or post_tree_sha != tree_sha)
    post_clean_git_tree = bool(
        post_tree_sha not in ("unknown", "uncommitted", "") and not post_dirty and not source_mutated
    )

    cmd_str = " ".join(cmd).replace(str(Path.home()) + "/", "~/")
    receipt = {
        "executed": True,
        "cached": False,
        "receipt_verified": bool(not source_mutated),
        "fresh_for_tree": bool(clean_git_tree and post_clean_git_tree),
        "passed": bool(rc == 0 and not source_mutated),
        "source_mutated_during_gate": source_mutated,
        "tree_sha": post_tree_sha,
        "worktree_sha": post_worktree_sha,
        "dirty": bool(is_dirty or post_dirty),
        "commit_sha": post_fp.get("commit_sha"),
        "command": cmd_str,
        "exit_code": rc,
        "duration_ms": dur_ms,
        "executed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "timestamp_epoch": round(time.time(), 2),
        "output_tail": out_tail,
    }
    try:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return receipt


def evaluate_target(
    target: Path,
    profile: str = "auto",
    probe_audio: bool = False,
    theta: float = DEFAULT_THETA,
    k: float = DEFAULT_K,
    strict_lenses: bool = False,
    execute_gate: bool = False,
) -> dict[str, Any]:
    """Evaluate any project directory, LaTeX treatise, or video file under the appropriate GWI profile."""
    resolved = target.expanduser().resolve()
    chosen_profile = detect_profile(resolved) if profile == "auto" else profile

    in_recursive_gate = os.environ.get("GWI_IN_GATE_EXECUTION") == str(resolved)
    gate_info: dict[str, Any] | None = None
    gate_veto = False
    if resolved.is_dir() and chosen_profile in ("app", "sim"):
        gate_info = execute_or_verify_gate_receipt(
            resolved,
            execute_gate=bool(execute_gate and not in_recursive_gate),
        )
        if execute_gate and not in_recursive_gate and not gate_info.get("passed", False):
            gate_veto = True

    if chosen_profile == "sim":
        report = evaluate_sim_profile(resolved, theta=theta, k=k)
    elif chosen_profile == "video":
        report = evaluate_video_profile(resolved, probe_audio=probe_audio, theta=theta, k=k)
    elif chosen_profile == "paper":
        report = evaluate_paper_profile(resolved, theta=theta, k=k)
    elif chosen_profile == "kb":
        report = evaluate_kb_profile(resolved, theta=theta, k=k)
    else:
        report = evaluate_app_profile(resolved, theta=theta, k=k)

    report["audit_flags"] = {
        "strict_lenses": bool(strict_lenses),
        "probe_audio": bool(probe_audio),
        "execute_gate": bool(execute_gate),
    }

    if gate_info is not None:
        report["gate_execution"] = gate_info
        if gate_veto:
            report.setdefault("blockers", []).append(
                {
                    "id": "GWI-APP-U05",
                    "severity": "P0",
                    "dimension": "Tier 3 (Actionability / Run-Bound Verification)",
                    "message": (
                        f"VETO: Run-bound behavioral quality gate failed "
                        f"(exit={gate_info.get('exit_code')}, cmd=`{gate_info.get('command')}`)"
                    ),
                }
            )

    cl = evaluate_craft_lenses(resolved, report, strict_lenses=strict_lenses)
    # Surface GWI-SCI-* findings as first-class report blockers (P0/P1 with blocker=True veto via the lens above).
    for f in (cl.get("science_integrity") or {}).get("findings") or []:
        report.setdefault("blockers", []).append(
            {
                "id": f["id"],
                "severity": f["severity"],
                "dimension": "Science Integrity (Measurement Provenance, Test Reality & Security Soundness)",
                "message": f"{'VETO' if f['blocker'] else 'WARN'}: {f['title']} — " + "; ".join(f["evidence"][:3]),
            }
        )
    verga_unmeasured = any(
        lr.get("unmeasured_probes", 0) > 0 for lr in cl.get("lenses", []) if lr.get("pole") == "verga"
    )
    chrysos_unmeasured = any(
        lr.get("unmeasured_probes", 0) > 0 for lr in cl.get("lenses", []) if lr.get("pole") == "chrysos"
    )
    any_unmeasured = verga_unmeasured or chrysos_unmeasured
    sub_metrics = cl.get("subtractive_metrics") or None

    if (
        gate_veto
        or cl.get("veto_triggered")
        or (strict_lenses and any_unmeasured)
        or (sub_metrics and "edgeDensity" in sub_metrics)
    ):
        tiers = report["tiers"]
        capped_perc = float(tiers["S_perc"])
        capped_sub = float(tiers["S_sub"])
        capped_act = float(tiers["S_act"])
        if cl.get("verga_veto_triggered") or (strict_lenses and verga_unmeasured):
            capped_perc = min(capped_perc, float(cl["verga_pole_score"]))
        if cl.get("chrysos_veto_triggered") or (strict_lenses and chrysos_unmeasured):
            capped_sub = min(capped_sub, float(cl["chrysos_pole_score"]))
        if gate_veto:
            capped_act = min(capped_act, 6.20)
        tiers["S_perc"] = round(capped_perc, 2)
        tiers["S_sub"] = round(capped_sub, 2)
        tiers["S_act"] = round(capped_act, 2)
        w_key = chosen_profile if chosen_profile in ARCHETYPE_WEIGHTS else "app"
        extra_sub = (
            sub_metrics
            if chosen_profile != "app"
            else (
                {"edgeDensity": sub_metrics["edgeDensity"]} if (sub_metrics and "edgeDensity" in sub_metrics) else None
            )
        )
        report["calculation"] = compute_goldwood_index(
            [tiers["S_sub"], tiers["S_perc"], tiers["S_act"], tiers["S_exist"]],
            weights=ARCHETYPE_WEIGHTS[w_key],
            theta=theta,
            k=k,
            conscience_clean=bool(report["calculation"].get("conscience_lambda", 1) == 1 and not gate_veto),
            subtractive_metrics=extra_sub,
        )
        tiers["S_sub"] = round(float(report["calculation"]["tier_scores"][0]), 2)
        tiers["S_perc"] = round(float(report["calculation"]["tier_scores"][1]), 2)
        tiers["S_act"] = round(float(report["calculation"]["tier_scores"][2]), 2)
        cl["coupled_goldwood_g"] = compute_coupled_goldwood_g(
            report["calculation"],
            cl["chrysos_pole_score"],
            cl["verga_pole_score"],
            veto_triggered=bool(cl.get("veto_triggered") or gate_veto),
        )

    report["craft_lenses"] = cl
    report["standard"] = f"Goldwood Index & Global Watchability Index (GWI) v{GWI_STANDARD_VERSION}"
    report["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return report


def write_goldwood_evidence(target_dir: Path, report: dict[str, Any]) -> Path:
    """Write standardized `evidence/goldwood.json` for staging/release verification."""
    root = target_dir if target_dir.is_dir() else target_dir.parent
    ev_dir = root / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    out_path = ev_dir / "goldwood.json"

    calc = report["calculation"]
    payload = {
        "standard": report.get("standard", f"Goldwood Release Standard v{GWI_STANDARD_VERSION}"),
        "standardVersion": GWI_STANDARD_VERSION,
        "psiVariant": calc.get("psi_variant", "normalized_ceiling_9.0"),
        "profile": report["profile"],
        "project": report["project_name"],
        "generatedAt": report["generated_at"],
        "compositeScoreG": calc["goldwood_g"],
        "linearScoreG": calc["linear_score"],
        "barrierProductPsi": calc["barrier_product"],
        "barrierTheta": calc["theta"],
        "sovereignGateG": DEFAULT_THRESHOLD_G,
        "minTierScore": calc["min_tier_score"],
        "conscienceLambda": calc["conscience_lambda"],
        "kolmogorovElegance": report.get("kolmogorov_elegance", 1.0),
        "snrc": report.get("snrc", 1.0),
        "jacobianDgDs": calc.get("jacobian_dG_dS", []),
        "headroomGainDg": calc.get("headroom_gain_dG", []),
        "highestLeverageTier": calc.get("highest_leverage_tier", "S_sub"),
        "status": calc["status"],
        "verdict": calc["verdict"],
        "tiers": report["tiers"],
        "dimensions": report["dimensions"],
        "craftLenses": report.get("craft_lenses", {}),
        "blockers": report["blockers"],
        "notes": report["notes"],
    }
    serialized = json.dumps(payload, indent=2).replace(str(Path.home()) + "/", "~/")
    out_path.write_text(serialized + "\n", encoding="utf-8")
    return out_path


def format_audit_report(report: dict[str, Any]) -> str:
    """Render a clean, tabular console scorecard for `gwi audit`."""
    calc = report["calculation"]
    tiers = report["tiers"]
    lines = [
        "=== Unified Goldwood Index (G) & Global Watchability Index (GWI) Scorecard ===",
        f"  Target Project    : {report['project_name']} ({report['target']})",
        f"  Evaluation Profile: {report['profile'].upper()}  (Standard v{GWI_STANDARD_VERSION})",
        f"  Formula           : G(S) = (sum alpha_i S_i) * prod Psi(S_i)  [theta={calc['theta']}, k={calc['k']}]",
        "",
        "  [1. Four Ontological Tiers (Chrysos x Verga)]",
        f"    • Tier I   (S_sub   / Firmitas   — Substrate & Gate Bedrock) : {tiers['S_sub']:5.2f} / 10  (Psi = {calc['psi_values'][0]:.4f})",
        f"    • Tier II  (S_perc  / Venustas   — Perceptual & Visual Poise): {tiers['S_perc']:5.2f} / 10  (Psi = {calc['psi_values'][1]:.4f})",
        f"    • Tier III (S_act   / Utilitas   — Agency, Math & Flow)      : {tiers['S_act']:5.2f} / 10  (Psi = {calc['psi_values'][2]:.4f})",
        f"    • Tier IV  (S_exist / Syneidesis — Conscience & Provenance)  : {tiers['S_exist']:5.2f} / 10  (Psi = {calc['psi_values'][3]:.4f})",
        "",
        "  [2. Domain Dimension Breakdown]",
    ]
    for d in report.get("dimensions") or []:
        lines.append(
            f"    • [{d['id']:<8}] {d['name']:<44} : {d['score']:5.2f} / 10  (w={d['weight']:.2f}, tier={d['tier']})"
        )

    lines.extend(
        [
            "",
            "  [3. Composite GWI Synthesis]",
            f"    • Linear Weighted Score (sum alpha_i S_i) : {calc['linear_score']:5.2f} / 10.00",
            f"    • Non-Linear Barrier Product (prod Psi)   : {calc['barrier_product']:.4f}",
            f"    • Kolmogorov Elegance E(L)                : {report.get('kolmogorov_elegance', 1.0):.4f}",
            f"    • Signal-to-Noise Ratio (SNRC)            : {report.get('snrc', 1.0):.2f}",
            f"    • Jacobian Sensitivity dG/dS              : {calc.get('jacobian_dG_dS', [])}  (Highest Leverage: {calc.get('highest_leverage_tier', 'S_sub')})",
            f"    • Conscience / Console Hygiene (Lambda)   : {calc['conscience_lambda']}",
            f"    • UNIVERSAL GOLDWOOD INDEX G(S)           : {calc['goldwood_g']:5.2f} / 10.00  -->  {calc['verdict']}",
        ]
    )

    blockers = report.get("blockers") or []
    if blockers:
        lines.append(f"\n  [4. Audit Blockers & Remediation Targets ({len(blockers)})]")
        for b in blockers:
            lines.append(f"    ! [{b['severity']}] {b['id']} ({b['dimension']}): {b['message']}")
    elif (report.get("craft_lenses") or {}).get("veto_triggered"):
        lines.append("\n  [4. Audit Blockers]: 0 core tier blockers, but Craft Lens VETO triggered (see Section 6).")
    else:
        lines.append("\n  [4. Audit Blockers]: 0 blockers detected. Ready for 9+ Sovereign Release.")

    notes = report.get("notes") or []
    if notes:
        lines.append("\n  [5. Verified Evidence Notes]")
        for n in notes:
            lines.append(f"    ✓ {n}")

    cl = report.get("craft_lenses") or {}
    lenses_list = cl.get("lenses") or []
    if lenses_list:
        lines.append(
            f"\n  [6. Multi-Disciplinary Craft Lenses (Chrysos={cl['chrysos_pole_score']:.2f} x Verga={cl['verga_pole_score']:.2f} -> Coupled G={cl['coupled_goldwood_g']:.2f})]"
        )
        for lr in lenses_list:
            reasons = ", ".join(lr.get("activation_reasons") or [])
            lines.append(f"    • [{lr['id']:<18}] {lr['title']} : {lr['score']:5.2f} / 10  ({reasons})")
            lines.append(f"      Contact Sheet : {lr['contact_sheet']}")
            for pf in lr.get("probe_findings") or []:
                lines.append(f"      - {pf}")

    return "\n".join(lines)


def _collect_git_warm_start_briefing(target_path: str | Path) -> str:
    """Collect a compact mechanical Git warm-start header (Rule 1: Warm-Start, Don't Restrict)."""
    p = Path(target_path).expanduser().resolve()
    repo_dir = p if p.is_dir() else p.parent
    try:
        if not (repo_dir / ".git").exists():
            check = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                check=False,
            )
            if check.returncode != 0:
                return f"- Working Path: {p} (non-Git directory)"

        commits = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "-n", "3", "--oneline"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "-sb"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        diff_stat = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "HEAD", "--stat"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return f"- Working Path: {p} (git unavailable)"
    return (
        f"- Repo Path: {repo_dir}\n"
        f"- Recent Commits:\n{commits or '  (none)'}\n"
        f"- Git Status (-sb):\n{status or '  (clean)'}\n"
        f"- Working Tree Diff Stat (vs HEAD):\n{diff_stat or '  (no uncommitted tracked diffs vs HEAD)'}"
    )


def build_multi_model_prompt(report: dict[str, Any], agent: str = "astra") -> str:
    """Generate a ready-to-run OpenCode / GX10 adversarial audit prompt pre-loaded with GWI & Git warm-start telemetry."""
    calc = report["calculation"]
    blocker_lines = "\n".join(
        f"- [{b['severity']}] {b['id']} ({b['dimension']}): {b['message']}" for b in (report.get("blockers") or [])
    )
    if not blocker_lines:
        blocker_lines = "- None detected by static/telemetry probe (verify edge cases adversarially)."

    cl = report.get("craft_lenses") or {}
    lens_blocks: list[str] = []
    for lr in cl.get("lenses") or []:
        constraints = "; ".join(lr.get("builder_constraints") or [])
        lens_blocks.append(
            f"- **{lr['title']} (`{lr['id']}` — Score {lr['score']:.2f}/10)**:\n"
            f"  - Contact Sheet Required: `{lr['contact_sheet']}`\n"
            f"  - Craft Constraints: {constraints}"
        )
    lens_section = "\n".join(lens_blocks) if lens_blocks else "- Standard Chrysos Software Hygiene"

    role_header = {
        "astra": "You are GPT-6 Astra (Independent Adversarial Auditor).",
        "fable": "You are Claude Fable 5.1 (Heavy Architecture & 3D/UX Systems Lead).",
        "gx10": "You are Qwen 3.6 35B Sovereign Principal Reviewer.",
        "triad": "You are a Tri-Council Principal Reviewer (Claude Fable 5.1 / GPT-6 Astra running in parallel).",
    }.get(agent, "You are an Independent Goldwood 9+ Principal Auditor.")

    warm_start = _collect_git_warm_start_briefing(report["target"])

    return (
        f"{role_header}\n"
        f"Audit target `{report['project_name']}` ({report['target']}) under the Unified Goldwood Index (G >= 9.0) "
        f"profile `{report['profile']}`.\n\n"
        f"Mechanical Git Warm-Start Briefing (Rule 1 — Warm-Start, Don't Restrict; all tools remain 100% enabled):\n"
        f"{warm_start}\n\n"
        f"Current Deterministic GWI Telemetry:\n"
        f"- Linear Weighted Score: {calc['linear_score']:.2f} / 10\n"
        f"- Barrier Product prod Psi(S_i): {calc['barrier_product']:.4f}\n"
        f"- Barrier-Penalized Goldwood Index G(S): {calc['goldwood_g']:.2f} / 10 ({calc['verdict']})\n"
        f"- Coupled Chrysos x Verga Score: {cl.get('coupled_goldwood_g', calc['goldwood_g']):.2f} / 10 "
        f"(Chrysos={cl.get('chrysos_pole_score', 9.5):.2f}, Verga={cl.get('verga_pole_score', 9.5):.2f})\n"
        f"- Tiers: S_sub={report['tiers']['S_sub']}, S_perc={report['tiers']['S_perc']}, "
        f"S_act={report['tiers']['S_act']}, S_exist={report['tiers']['S_exist']}\n\n"
        f"Active Multi-Disciplinary Craft Lenses (Wooden Pole / Verga):\n"
        f"{lens_section}\n\n"
        f"Detected Blockers to Verify / Remediate:\n"
        f"{blocker_lines}\n\n"
        f"Instructions:\n"
        f"1. Use the Mechanical Warm-Start Briefing above to skip blind repo discovery, while keeping full tool access to inspect any surrounding files or run checks independently.\n"
        f"2. Inspect the codebase and verify every Master Feature Catalog item in AGENTS.md / docs/FEATURE_CATALOG.md.\n"
        f"3. Enforce all active Craft Lens constraints and cite exact contact-sheet coordinates `(sheet_id, cell_or_page, metric)`.\n"
        f"4. Score all 4 Ontological Tiers (S_sub, S_perc, S_act, S_exist) on [0, 10] and compute G(S).\n"
        f"5. Issue `APPROVED FOR 9+ RELEASE` only if G(S) >= 9.0, min(S_i) >= {calc['theta']}, Lambda == 1, and 0 P0/P1 or Verga lens vetoes remain.\n"
    )


def discover_default_portfolio_dirs() -> list[Path]:
    """Locate active workspace repositories via GWI_PORTFOLIO_DIRS, user config, or dynamic workspace discovery."""
    env_dirs = os.environ.get("GWI_PORTFOLIO_DIRS", "").strip()
    if env_dirs:
        return [Path(p.strip()).expanduser().resolve() for p in env_dirs.split(":") if p.strip()]

    cfg_path = Path.home() / ".config" / "sovereign-kb" / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(cfg.get("portfolio_dirs"), list):
                return [Path(p).expanduser().resolve() for p in cfg["portfolio_dirs"] if Path(p).expanduser().exists()]
        except Exception:
            pass

    candidates: list[Path] = []
    for root in (Path.home() / "Projects", Path.home() / "GitLab"):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "AGENTS.md").is_file() and (child / ".git").exists():
                resolved = child.resolve()
                if resolved not in candidates:
                    candidates.append(resolved)
    return candidates
