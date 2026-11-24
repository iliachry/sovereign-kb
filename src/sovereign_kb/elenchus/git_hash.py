"""Git subprocess wrapper, canonical worktree hashing, and GWI/gate receipt SHA helpers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..audit_registry import find_git_root
from .constants import (
    _RECEIPT_BASENAMES,
    _VOLATILE_GATE_RECEIPT_KEYS,
    _VOLATILE_GWI_KEYS,
    DEFAULT_GWI_PASS_THRESHOLD,
    _is_real_num,
)


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

    _rgc = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)
    rc, ls_out = _rgc(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
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
                rc_sh, sub_head_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
                    p_tr, "rev-parse", "--verify", "--quiet", "HEAD"
                )
                sub_head = sub_head_out.strip() if rc_sh == 0 and sub_head_out.strip() else "unborn"
                sub_wt = _compute_worktree_content_sha(p_tr, _visited=next_visited)
            else:
                sub_head = "no-git"
                sub_wt = _hash_plain_directory_tree(p_tr)
            payload = f"head={sub_head}|wt={sub_wt}".encode()
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


def _detect_gwi_schema_contradiction(obj: dict[str, Any]) -> str | None:
    if not isinstance(obj, dict):
        return "Malformed GWI object: expected dict."
    if "calculation" in obj and obj["calculation"] is not None and not isinstance(obj["calculation"], dict):
        return "Malformed GWI object: `calculation` must be a dict."
    raw_calc = obj.get("calculation")
    calc: dict[str, Any] = raw_calc if isinstance(raw_calc, dict) else {}
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
        if val is not None and _is_real_num(val)
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

    raw_cl1 = obj.get("craft_lenses")
    cl1: dict[str, Any] | None = raw_cl1 if isinstance(raw_cl1, dict) else None
    raw_cl2 = obj.get("craftLenses")
    cl2: dict[str, Any] | None = raw_cl2 if isinstance(raw_cl2, dict) else None

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
    if raw_g is None or not _is_real_num(raw_g):
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
    raw_calc = obj.get("calculation")
    calc: dict[str, Any] = raw_calc if isinstance(raw_calc, dict) else {}
    raw_tiers = obj.get("tiers")
    tiers: dict[str, Any] = raw_tiers if isinstance(raw_tiers, dict) else {}
    raw_cl1 = obj.get("craft_lenses")
    cl1: dict[str, Any] | None = raw_cl1 if isinstance(raw_cl1, dict) else None
    raw_cl2 = obj.get("craftLenses")
    cl2: dict[str, Any] | None = raw_cl2 if isinstance(raw_cl2, dict) else None
    cl_dict: dict[str, Any] = cl1 if cl1 is not None else (cl2 if cl2 is not None else {})
    raw_blockers = obj.get("blockers")
    blockers = (
        sorted((str(b.get("id") or b.get("message") or b) if isinstance(b, dict) else str(b)) for b in raw_blockers)
        if isinstance(raw_blockers, list)
        else []
    )
    raw_g = calc.get("goldwood_g", obj.get("compositeScoreG", obj.get("goldwood_g")))
    goldwood_g = round(float(raw_g), 4) if raw_g is not None and _is_real_num(raw_g) else None
    raw_coupled = cl_dict.get("coupled_goldwood_g", cl_dict.get("coupledGoldwoodG"))
    coupled_g = round(float(raw_coupled), 4) if raw_coupled is not None and _is_real_num(raw_coupled) else None
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
