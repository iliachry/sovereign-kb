"""Bounded worktree diff collection, untracked hunks, and score-blind challenge packet builder."""

from __future__ import annotations

import difflib
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..audit_registry import compute_subproject_fingerprint
from .constants import (
    _SCORE_BLIND_DROP_KEYS,
    DEFAULT_MAX_DIFF_CHARS,
    _is_strict_int,
)
from .git_hash import (
    _compute_gate_receipt_sha256,
    _compute_gwi_receipt_sha256,
    _compute_gwi_semantic_sha256,
    _compute_worktree_content_sha,
    _is_skipped_evidence_path,
    _normalize_git_rel_path,
    _resolve_target_and_scope,
    _run_git_cmd,
    _strip_volatile_gwi_payload,
)


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
                rc_ex, _ = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
                    repo_root, "cat-file", "-e", f"HEAD:{rel_posix}"
                )
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
    rc_st, st_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
        repo_root, "status", "--porcelain", "-uall", "-z"
    )
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
    rc_head, _ = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
        repo_root, "rev-parse", "--verify", "--quiet", "HEAD"
    )
    has_head = rc_head == 0

    if dirty:
        if has_head:
            rc_d, d_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
                repo_root,
                *_GIT_DIFF_PIN_ARGS,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--no-renames",
                "HEAD",
            )
            rc_ns, ns_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
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
            rc_c, c_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
                repo_root, *_GIT_DIFF_PIN_ARGS, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames"
            )
            rc_s_cached, s_cached_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
                repo_root,
                *_GIT_DIFF_PIN_ARGS,
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--no-renames",
            )
            rc_ns_w, ns_w = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
                repo_root, "diff", "--numstat", "-z", "--no-ext-diff", "--no-textconv", "--no-renames"
            )
            rc_ns_c, ns_c = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
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
                    rc_sub_st, sub_st = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
                        p_sub, "status", "--porcelain", "-z"
                    )
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

        rc_u, u_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
            repo_root, "ls-files", "-z", "--others", "--exclude-standard"
        )
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
        rc_s, s_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
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
        rc_ns_h, ns_h = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
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
        rc_n, n_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
            repo_root, "show", "-z", "--name-only", "--format=", "HEAD"
        )
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
        rc_log, log_out = getattr(sys.modules.get("sovereign_kb.elenchus"), "_run_git_cmd", _run_git_cmd)(
            repo_root, "log", "-n", "1", "--pretty=%s"
        )
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
