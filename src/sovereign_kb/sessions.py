"""
Sovereign-KB Live Agent Session, Background Task & Deadlock Auditor (`kb sessions` / `kb ps`).

Eliminates superficial `tail -n 2` transcript checks and fabricated time estimates by performing
full-transcript state reconstruction, background task log error detection (`error CS*`, `Traceback`,
`SyntaxError`), and non-batchmode Unity/engine modal deadlock detection across local and remote nodes.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ARCHIVE_SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
}
ARCHIVE_SKIP_NAMES = {".DS_Store"}
PROTECTED_STATES = (
    "ACTIVE_TOOL_LOOP",
    "WAITING_ON_BG_TASK",
    "DEADLOCKED_BG_TASK",
    "INTERRUPTED_MID_TURN",
)

IMMEDIATE_DEADLOCK_PATTERNS = [
    re.compile(r"error CS\d+:", re.I),
    re.compile(r"Scripts have compiler errors", re.I),
]

FATAL_TAIL_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\):", re.I),
    re.compile(r"SyntaxError:", re.I),
    re.compile(r"Segmentation fault", re.I),
    re.compile(r"panic:", re.I),
]

RECOVERY_MARKER_RE = re.compile(r"(?:^> build ·|^→ Read |^=== [A-Z0-9 _.-]+ ===|\bdone$|\bok$)", re.I)


def inspect_session(conv_dir: Path, now: float | None = None) -> dict[str, Any] | None:
    if now is None:
        now = time.time()
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if not tpath.exists():
        return None

    mtime = tpath.stat().st_mtime
    tasks_dir = conv_dir / ".system_generated" / "tasks"
    if tasks_dir.is_dir():
        for tlog in tasks_dir.glob("task-*.log"):
            try:
                mtime = max(mtime, tlog.stat().st_mtime)
            except OSError:
                pass
    transcript_task_mtime = mtime
    # Also track sibling conversations/<cid>.db and .db-wal mtimes for active/in-progress liveness
    parent_agy = conv_dir.parent.parent
    conv_db_dir = parent_agy / "conversations"
    db_mtime = mtime
    if conv_db_dir.is_dir():
        for suf in (".db", ".db-wal", ".db-shm"):
            cand = conv_db_dir / f"{conv_dir.name}{suf}"
            if cand.exists():
                try:
                    db_mtime = max(db_mtime, cand.stat().st_mtime)
                except OSError:
                    pass

    age_s = int(max(0.0, now - transcript_task_mtime))
    active_age_s = int(max(0.0, now - db_mtime))

    try:
        raw_lines = tpath.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except Exception:
        return None
    if not raw_lines:
        return None

    steps: list[dict[str, Any]] = []
    torn_tail = False
    for idx_line, line in enumerate(raw_lines):
        try:
            steps.append(json.loads(line))
        except Exception:
            if idx_line == len(raw_lines) - 1:
                torn_tail = True
            continue
    if not steps and not torn_tail:
        return None
    if torn_tail and not steps:
        return {
            "conversation_id": conv_dir.name,
            "state": "ACTIVE_TOOL_LOOP",
            "last_activity_age_s": age_s,
            "total_steps": 1,
            "last_step_index": 0,
            "last_step_type": "IN_PROGRESS_STREAM",
            "last_step_tools": [],
            "first_user_prompt": "",
            "last_user_prompt": "",
            "last_user_step": -1,
            "running_tasks": [],
        }

    first_user_prompt = ""
    last_user_prompt = ""
    last_user_step = -1
    raw_running_steps: list[dict[str, Any]] = []
    resolved_task_ids: set[str] = set()

    task_resolve_re = re.compile(
        r"(?:sender=[^\s/]+/(task-\d+)|Task id \"[^\"]+/(task-\d+)\" finished|Task \"[^\"]+/(task-\d+)\" cancelled|cannot kill task \"[^\"]+/(task-\d+)\")"
    )

    for st in steps:
        idx = st.get("step_index", -1)
        stype = st.get("type", "")
        status = st.get("status", "")
        content = str(st.get("content") or "")

        for m in task_resolve_re.finditer(content):
            for grp in m.groups():
                if grp:
                    resolved_task_ids.add(grp)

        if stype == "USER_INPUT":
            clean = re.sub(r"<[^>]+>", " ", content)
            clean = " ".join(clean.split()).strip()
            if not first_user_prompt:
                first_user_prompt = clean[:110]
            last_user_prompt = clean[:110]
            last_user_step = idx

        if status == "RUNNING":
            raw_running_steps.append(
                {
                    "step_index": idx,
                    "task_id": f"task-{idx}",
                    "type": stype,
                    "created_at": st.get("created_at", ""),
                    "preview": " ".join(content.split())[:140],
                }
            )

    # Never drop unresolved background tasks based on a 1-hour transcript cutoff (P0-5 / P0-3 fix)
    running_steps = [r for r in raw_running_steps if r["task_id"] not in resolved_task_ids]

    # Check task log files for any unresolved running background tasks
    task_diagnostics: list[dict[str, Any]] = []
    has_deadlock_error = False

    for rst in running_steps:
        step_idx = rst["step_index"]
        log_file = tasks_dir / f"task-{step_idx}.log"
        errors_found: list[str] = []
        log_age_s = -1
        if log_file.exists():
            try:
                log_age_s = int(max(0.0, now - log_file.stat().st_mtime))
                log_txt = log_file.read_text(encoding="utf-8", errors="replace")
                all_lines = log_txt.splitlines()
                for line in all_lines:
                    if any(p.search(line) for p in IMMEDIATE_DEADLOCK_PATTERNS):
                        errors_found.append(line.strip()[:140])
                # For Python/shell tracebacks inside opencode subagent probes, only flag as a deadlock
                # if the log has been idle >= 45s AND no subsequent recovery/tool step occurred in the tail.
                if not errors_found and log_age_s >= 45:
                    tail_lines = all_lines[-25:]
                    last_err_idx = -1
                    last_rec_idx = -1
                    for idx_l, line in enumerate(tail_lines):
                        if any(p.search(line) for p in FATAL_TAIL_PATTERNS):
                            last_err_idx = idx_l
                        elif RECOVERY_MARKER_RE.search(line.strip()):
                            last_rec_idx = idx_l
                    if last_err_idx >= 0 and last_err_idx > last_rec_idx:
                        errors_found.append(tail_lines[last_err_idx].strip()[:140])
            except Exception:
                pass
        if errors_found:
            has_deadlock_error = True
        task_diagnostics.append(
            {
                "task_id": f"task-{step_idx}",
                "log_path": str(log_file) if log_file.exists() else None,
                "log_idle_s": log_age_s,
                "fatal_errors": errors_found[:5],
            }
        )

    last_step = steps[-1]
    last_type = last_step.get("type", "")
    last_status = last_step.get("status", "")
    last_tools = [tc.get("name", "") for tc in (last_step.get("tool_calls") or []) if isinstance(tc, dict)]
    last_content = str(last_step.get("content") or "").strip()

    # Determine ground-truth state without guessing
    if torn_tail:
        state = "ACTIVE_TOOL_LOOP"
    elif running_steps and has_deadlock_error:
        state = "DEADLOCKED_BG_TASK"
    elif running_steps:
        state = "WAITING_ON_BG_TASK"
    elif (
        last_status == "IN_PROGRESS"
        or (last_type == "PLANNER_RESPONSE" and last_tools)
        or last_type in ("GENERIC", "SYSTEM_MESSAGE")
    ):
        state = "ACTIVE_TOOL_LOOP" if active_age_s < 180 else "INTERRUPTED_MID_TURN"
        age_s = active_age_s
    elif last_type == "PLANNER_RESPONSE" and last_content and not last_tools:
        state = "IDLE_COMPLETE"
    else:
        state = "IDLE"

    return {
        "conversation_id": conv_dir.name,
        "state": state,
        "last_activity_age_s": age_s,
        "total_steps": len(steps),
        "last_step_index": last_step.get("step_index", -1),
        "last_step_type": last_type,
        "last_step_tools": last_tools,
        "first_user_prompt": first_user_prompt,
        "last_user_prompt": last_user_prompt,
        "last_user_step": last_user_step,
        "running_tasks": task_diagnostics,
    }


def _get_cluster_env(var_name: str) -> str:
    val = os.environ.get(var_name, "").strip()
    if val:
        return val
    vault_file = Path.home() / ".config" / "agents" / "secrets.env"
    if vault_file.is_file():
        try:
            for line in vault_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(rf"^\s*(?:export\s+)?{re.escape(var_name)}=['\"]?([^'\"\s#]+)['\"]?", line)
                if m:
                    return m.group(1).strip()
        except Exception:
            pass
    return ""


def _run_node_shell(node: str, script: str, timeout: int = 6) -> str:
    if node in ("local", "localhost"):
        proc = subprocess.run(["/bin/zsh", "-c", script], capture_output=True, text=True, timeout=timeout)
        return proc.stdout.strip()
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", node, script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.stdout.strip()


def scan_remote_unity_deadlocks(heal: bool = False) -> list[dict[str, Any]]:
    """Check local host and optional remote peers (`KB_REMOTE_PEER`, `KB_REMOTE_WIN_PEER`) for stuck non-batchmode Unity processes."""
    findings: list[dict[str, Any]] = []
    remote_unix = _get_cluster_env("KB_REMOTE_PEER")
    unix_nodes = ["local", *([remote_unix] if remote_unix else [])]
    for node in unix_nodes:
        try:
            out = _run_node_shell(node, "ps aux | grep 'Unity.app/Contents/MacOS/Unity' | grep -v grep || true")
            if not out:
                continue

            log_out = _run_node_shell(
                node,
                "newest=$(ls -t /tmp/unity*.log 2>/dev/null | head -n 1); "
                'if [ -n "$newest" ]; then '
                '  echo "AGE:$(( $(date +%s) - $(stat -f %m "$newest") ))"; '
                "  grep -E 'error CS[0-9]+:' \"$newest\" 2>/dev/null | tail -n 5 || true; "
                "fi",
            )
            log_lines = [ln.strip() for ln in log_out.splitlines() if ln.strip()]
            log_age_s = 0
            compile_errors: list[str] = []
            for ln in log_lines:
                if ln.startswith("AGE:"):
                    try:
                        log_age_s = int(ln.split(":", 1)[1])
                    except ValueError:
                        pass
                else:
                    compile_errors.append(ln)

            for line in out.splitlines():
                if "-executeMethod" not in line or "zsh -c" in line:
                    continue
                parts = line.split()
                pid = parts[1] if len(parts) > 1 and parts[1].isdigit() else ""
                has_batchmode = "-batchmode" in line
                is_deadlocked = (not has_batchmode) and (bool(compile_errors) or log_age_s > 90)
                healed = False
                if is_deadlocked and heal and pid:
                    _run_node_shell(node, f"kill -9 {pid} 2>/dev/null || true", timeout=5)
                    healed = True
                findings.append(
                    {
                        "node": node,
                        "pid": pid,
                        "process": " ".join(parts[:12]),
                        "has_batchmode": has_batchmode,
                        "log_idle_s": log_age_s,
                        "compile_errors": compile_errors,
                        "deadlocked": is_deadlocked,
                        "healed": healed,
                    }
                )
        except Exception:
            pass

    # Check optional Windows GPU peer (`KB_REMOTE_WIN_PEER`) for non-batchmode Unity.exe CLI -executeMethod runs
    win_peer = _get_cluster_env("KB_REMOTE_WIN_PEER")
    if win_peer:
        try:
            rtx_out = _run_node_shell(
                win_peer,
                'powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name=\'Unity.exe\'\\" | ForEach-Object { \\"$($_.ProcessId)|$($_.CommandLine)\\" }"',
                timeout=5,
            )
            for line in rtx_out.splitlines():
                line_s = line.strip()
                if not line_s or "-executeMethod" not in line_s:
                    continue
                pid_rtx, _, cmd_rtx = line_s.partition("|")
                pid_rtx = pid_rtx.strip()
                cmd_rtx = cmd_rtx.strip() or line_s
                has_batchmode = "-batchmode" in cmd_rtx
                is_deadlocked = not has_batchmode
                healed = False
                if is_deadlocked and heal and pid_rtx.isdigit():
                    _run_node_shell(win_peer, f"taskkill /F /PID {pid_rtx}", timeout=5)
                    healed = True
                findings.append(
                    {
                        "node": win_peer,
                        "pid": pid_rtx,
                        "process": cmd_rtx[:140],
                        "has_batchmode": has_batchmode,
                        "log_idle_s": -1,
                        "compile_errors": [],
                        "deadlocked": is_deadlocked,
                        "healed": healed,
                    }
                )
        except Exception:
            pass

    return findings


@contextmanager
def _store_lock(agy_dir: Path) -> Any:
    """Exclusive flock across `kb sessions --clean` and `agy-sidebar-sync` to prevent concurrent store writes."""
    agy_dir.mkdir(parents=True, exist_ok=True)
    lock_path = agy_dir / ".sidebar-sync.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    res, shift = 0, 0
    while True:
        if pos >= len(buf):
            raise ValueError("Truncated varint in protobuf stream")
        b = buf[pos]
        pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, pos
        shift += 7
        if shift > 63:
            raise ValueError("Varint shift overflow in protobuf stream")


def _slugify_prompt(text: str, max_len: int = 40) -> str:
    words = re.findall(r"[a-zA-Z0-9\u0370-\u03ff\u1f00-\u1fff]+", text.lower())
    if not words:
        return "session"
    slug = "-".join(words)[:max_len].strip("-")
    return slug or "session"


def _dir_size_bytes(path: Path) -> int:
    if not path.exists() or path.is_symlink():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for fn in files:
            fp = os.path.join(root, fn)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


def _snapshot_sqlite_db(src_db: Path, dest_db: Path) -> bool:
    """Create a WAL-consistent SQLite backup of `conversations/<cid>.db` before unlinking."""
    if not src_db.is_file():
        return False
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    try:
        src_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True, timeout=5.0)
        dst_conn = sqlite3.connect(str(dest_db), timeout=5.0)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        return dest_db.is_file() and dest_db.stat().st_size > 0
    except Exception:
        try:
            shutil.copy2(src_db, dest_db)
            return dest_db.is_file()
        except Exception:
            return False


def _archive_session_deliverables(
    conv_dir: Path,
    archive_root: Path,
    first_prompt: str,
    mtime: float,
    dry_run: bool = False,
    conv_db_path: Path | None = None,
    annot_path: Path | None = None,
    session_meta: dict[str, Any] | None = None,
) -> tuple[int, int, str | None]:
    """
    Archive ALL user/artifact files, `.user_uploaded/`, `transcript.jsonl`, `transcript_full.jsonl`,
    `tasks/*.log`, `annotations/<cid>.pbtxt`, and a WAL-safe `conversation.db` snapshot into
    `archive-artifacts/<date>_<slug>_<cid[:8]>/` before any deletion occurs.
    """
    cid = conv_dir.name
    candidates: list[tuple[Path, Path, int]] = []

    if conv_dir.is_dir():
        for root, dirs, files in os.walk(conv_dir, followlinks=False):
            rel_root = Path(root).relative_to(conv_dir)
            # Prune heavy cache directories (`node_modules`, `.git`, `__pycache__`, `.venv`)
            dirs[:] = [d for d in dirs if d not in ARCHIVE_SKIP_DIRS and not os.path.islink(os.path.join(root, d))]
            for fn in sorted(files):
                if fn in ARCHIVE_SKIP_NAMES:
                    continue
                fpath = Path(root) / fn
                if fpath.is_symlink() or not fpath.is_file():
                    continue
                rel = rel_root / fn
                # Inside `.system_generated`, keep transcripts and task logs; skip transient step/chunk cache
                if rel.parts and rel.parts[0] == ".system_generated":
                    if (
                        len(rel.parts) >= 3
                        and rel.parts[1] == "logs"
                        and fn
                        in (
                            "transcript.jsonl",
                            "transcript_full.jsonl",
                        )
                    ):
                        pass
                    elif len(rel.parts) >= 3 and rel.parts[1] == "tasks" and fn.endswith(".log"):
                        pass
                    else:
                        continue
                try:
                    sz = fpath.stat().st_size
                except OSError:
                    continue
                candidates.append((fpath, rel, sz))

    has_db = bool(conv_db_path and conv_db_path.is_file())
    has_annot = bool(annot_path and annot_path.is_file())
    if not candidates and not has_db and not has_annot:
        return 0, 0, None

    date_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    slug = _slugify_prompt(first_prompt)
    base_name = f"{date_str}_{slug}_{cid[:8]}"
    dest_dir = archive_root / base_name
    suffix_idx = 2
    while dest_dir.exists():
        dest_dir = archive_root / f"{base_name}_v{suffix_idx}"
        suffix_idx += 1

    total_bytes = sum(sz for _, _, sz in candidates)
    if has_db and conv_db_path:
        try:
            total_bytes += conv_db_path.stat().st_size
        except OSError:
            pass
    if has_annot and annot_path:
        try:
            total_bytes += annot_path.stat().st_size
        except OSError:
            pass

    file_count = len(candidates) + (1 if has_db else 0) + (1 if has_annot else 0)

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src, rel, _ in candidates:
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            if not target.exists():
                raise RuntimeError(f"Archive verification failed for {target}")
        if has_db and conv_db_path:
            if not _snapshot_sqlite_db(conv_db_path, dest_dir / "conversation.db"):
                raise RuntimeError(f"SQLite backup failed for {conv_db_path}")
        if has_annot and annot_path:
            shutil.copy2(annot_path, dest_dir / f"{cid}.pbtxt")
        manifest = {
            "conversation_id": cid,
            "first_user_prompt": first_prompt,
            "archived_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "files_archived": file_count,
            "bytes_archived": total_bytes,
            **(session_meta or {}),
        }
        _atomic_write_bytes(
            dest_dir / "manifest.json",
            (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
            mode=0o644,
        )

    return file_count, total_bytes, str(dest_dir)


def _discover_live_disk_ids(agy_dir: Path) -> set[str]:
    """Return all conversation IDs that currently have a live `.db` or `brain/<cid>` directory on disk."""
    live: set[str] = set()
    brain_dir = agy_dir / "brain"
    conv_db_dir = agy_dir / "conversations"
    if brain_dir.is_dir():
        for p in brain_dir.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                live.add(p.name)
    if conv_db_dir.is_dir():
        for p in conv_db_dir.iterdir():
            if p.is_file() and p.name.endswith(".db") and not p.name.startswith("."):
                live.add(p.name[:-3])
    return live


def _sync_sidebar_stores(
    agy_dir: Path,
    app_storage_path: Path | None,
    valid_ids: set[str] | None = None,
    newly_deleted_ids: set[str] | None = None,
) -> dict[str, Any]:
    """
    TOCTOU-safe, self-healing synchronization of `deleted_conversations.json`, `conversation_summaries.db`
    (+ SQLite `BEFORE INSERT` trigger), `agyhub_summaries_proto.pb`, and `app_storage.json`.
    - Blacklists ONLY explicitly deleted conversations (`newly_deleted_ids | blacklist`), never `NOT IN (valid_ids)`.
    - Self-heals `deleted_conversation_ids` if a conversation exists live on disk and is not in `newly_deleted_ids`.
    """
    newly_deleted = set(newly_deleted_ids or set())
    live_on_disk = (_discover_live_disk_ids(agy_dir) | set(valid_ids or set())) - newly_deleted

    blacklist_path = agy_dir / "deleted_conversations.json"
    db_path = agy_dir / "conversation_summaries.db"
    pb_path = agy_dir / "agyhub_summaries_proto.pb"

    blacklist: set[str] = set()
    if blacklist_path.exists():
        try:
            loaded = json.loads(blacklist_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                blacklist.update(str(x) for x in loaded if isinstance(x, str))
        except Exception:
            pass

    blacklist.update(newly_deleted)
    # Self-heal: any conversation currently live on disk is removed from blacklist
    blacklist -= live_on_disk
    _atomic_write_bytes(
        blacklist_path,
        (json.dumps(sorted(blacklist), indent=2) + "\n").encode("utf-8"),
        mode=0o600,
    )

    db_rows_remaining = 0
    trigger_installed = False
    db_error: str | None = None
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS deleted_conversation_ids (conversation_id TEXT PRIMARY KEY)")
                if blacklist:
                    conn.executemany(
                        "INSERT OR IGNORE INTO deleted_conversation_ids (conversation_id) VALUES (?)",
                        [(cid,) for cid in sorted(blacklist)],
                    )
                # Self-heal SQLite blacklist table for any conversation that is currently live on disk
                if live_on_disk:
                    conn.executemany(
                        "DELETE FROM deleted_conversation_ids WHERE conversation_id = ?",
                        [(cid,) for cid in sorted(live_on_disk)],
                    )
                # Also record any summary row whose .db and brain dir are BOTH gone AND is idle in summaries
                existing_summary_ids = [
                    str(r[0]) for r in conn.execute("SELECT conversation_id FROM conversation_summaries").fetchall()
                ]
                for sid in existing_summary_ids:
                    if sid not in live_on_disk and sid in blacklist:
                        conn.execute(
                            "INSERT OR IGNORE INTO deleted_conversation_ids (conversation_id) VALUES (?)",
                            (sid,),
                        )

                conn.execute(
                    "DELETE FROM conversation_summaries "
                    "WHERE conversation_id IN (SELECT conversation_id FROM deleted_conversation_ids)"
                )
                try:
                    conn.execute(
                        "DELETE FROM conversations_fts "
                        "WHERE conversation_id IN (SELECT conversation_id FROM deleted_conversation_ids)"
                    )
                except sqlite3.OperationalError:
                    pass

                conn.execute("DROP TRIGGER IF EXISTS block_ghost_summaries_insert")
                conn.execute(
                    """
                    CREATE TRIGGER block_ghost_summaries_insert
                    BEFORE INSERT ON conversation_summaries
                    WHEN NEW.conversation_id IN (SELECT conversation_id FROM deleted_conversation_ids)
                    BEGIN
                        SELECT RAISE(IGNORE);
                    END;
                    """
                )
                conn.commit()
                db_rows_remaining = int(conn.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0])
                trigger_installed = True
            finally:
                conn.close()
        except Exception as exc:
            db_error = str(exc)

    pb_chunks_kept = 0
    pb_bytes = 0
    if pb_path.exists():
        try:
            st0 = pb_path.stat()
            data = pb_path.read_bytes()
            pos = 0
            kept_chunks: list[bytes] = []
            while pos < len(data):
                start = pos
                tag, pos = _read_varint(data, pos)
                wire_type = tag & 7
                if wire_type == 0:
                    _, pos = _read_varint(data, pos)
                    kept_chunks.append(data[start:pos])
                elif wire_type == 1:
                    pos += 8
                    if pos > len(data):
                        raise ValueError("Truncated fixed64 in protobuf stream")
                    kept_chunks.append(data[start:pos])
                elif wire_type == 5:
                    pos += 4
                    if pos > len(data):
                        raise ValueError("Truncated fixed32 in protobuf stream")
                    kept_chunks.append(data[start:pos])
                elif wire_type == 2:
                    length, pos = _read_varint(data, pos)
                    if pos + length > len(data):
                        raise ValueError("Truncated length-delimited field in protobuf stream")
                    payload = data[pos : pos + length]
                    pos += length
                    if len(payload) >= 38 and payload[0] == 0x0A and payload[1] == 0x24:
                        cid = payload[2:38].decode("ascii", errors="ignore")
                        # Drop ONLY positively blacklisted conversations; preserve unknown/new ones!
                        if cid not in blacklist or cid in live_on_disk:
                            kept_chunks.append(data[start:pos])
                    else:
                        kept_chunks.append(data[start:pos])
                else:
                    raise ValueError(f"Unsupported protobuf wire type: {wire_type}")

            new_data = b"".join(kept_chunks)
            pb_chunks_kept = len(kept_chunks)
            pb_bytes = len(new_data)
            if new_data != data:
                st1 = pb_path.stat()
                if (st1.st_ino, st1.st_mtime_ns) == (st0.st_ino, st0.st_mtime_ns):
                    _atomic_write_bytes(pb_path, new_data, mode=0o600)
        except Exception:
            pass

    app_storage_pruned = 0
    if app_storage_path and app_storage_path.exists():
        try:
            st0 = app_storage_path.stat()
            app_data = json.loads(app_storage_path.read_text(encoding="utf-8"))
            changed = False
            prefix = "antigravity-multi-conversation-layout-v3-"
            for k in list(app_data.keys()):
                if k.startswith(prefix) and k != prefix + "index":
                    cid = k[len(prefix) :]
                    if cid in blacklist and cid not in live_on_disk:
                        del app_data[k]
                        app_storage_pruned += 1
                        changed = True
            for pane_key in ("aux-pane-session", "aux-pane-v2-session"):
                if pane_key in app_data and isinstance(app_data[pane_key], str):
                    try:
                        pane_obj = json.loads(app_data[pane_key])
                        pane_changed = False
                        for subkey in ("conversationPanes", "overviewPanelOpen"):
                            if isinstance(pane_obj.get(subkey), dict):
                                for cid in list(pane_obj[subkey].keys()):
                                    if cid in blacklist and cid not in live_on_disk:
                                        del pane_obj[subkey][cid]
                                        app_storage_pruned += 1
                                        pane_changed = True
                        if pane_changed:
                            app_data[pane_key] = json.dumps(pane_obj)
                            changed = True
                    except Exception:
                        pass
            if changed:
                st1 = app_storage_path.stat()
                if (st1.st_ino, st1.st_mtime_ns) == (st0.st_ino, st0.st_mtime_ns):
                    _atomic_write_bytes(
                        app_storage_path,
                        json.dumps(app_data).encode("utf-8"),
                        mode=0o600,
                    )
        except Exception:
            pass

    res: dict[str, Any] = {
        "blacklist_count": len(blacklist),
        "db_rows_remaining": db_rows_remaining,
        "sqlite_trigger_installed": trigger_installed,
        "pb_chunks_kept": pb_chunks_kept,
        "pb_bytes": pb_bytes,
        "app_storage_keys_pruned": app_storage_pruned,
    }
    if db_error:
        res["db_error"] = db_error
    return res


def _query_summaries_liveness(db_path: Path) -> dict[str, tuple[str, int, str]]:
    """Read `(status, not_fully_idle, title)` from `conversation_summaries.db`."""
    out: dict[str, tuple[str, int, str]] = {}
    if not db_path.is_file():
        return out
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(conversation_summaries)").fetchall()}
            if {"conversation_id", "status", "not_fully_idle"}.issubset(cols):
                title_col = "title" if "title" in cols else "''"
                for cid, st, nfi, title in conn.execute(
                    f"SELECT conversation_id, status, not_fully_idle, {title_col} FROM conversation_summaries"
                ):
                    out[str(cid)] = (str(st or ""), int(nfi or 0), str(title or ""))
        finally:
            conn.close()
    except Exception:
        pass
    return out


def clean_antigravity_sessions(
    agy_dir: Path | None = None,
    app_storage_path: Path | None = None,
    archive_dir: Path | None = None,
    dry_run: bool = False,
    keep_ids: list[str] | None = None,
    min_idle_seconds: int = 120,
    now: float | None = None,
) -> dict[str, Any]:
    """
    Deterministically archive deliverables, transcripts, and WAL-consistent SQLite snapshots from completed/idle
    Antigravity sessions and purge them from `brain/`, `conversations/`, `annotations/`, `presence/`,
    `conversation_summaries.db`, `agyhub_summaries_proto.pb`, and `app_storage.json` while strictly protecting
    all active/waiting/deadlocked/interrupted sessions and the current active chat.
    """
    if now is None:
        now = time.time()
    if agy_dir is None:
        agy_dir = Path.home() / ".gemini" / "antigravity"
    if app_storage_path is None:
        app_storage_path = Path.home() / "Library" / "Application Support" / "Antigravity" / "app_storage.json"
    if archive_dir is None:
        archive_dir = agy_dir / "archive-artifacts"

    keep_prefixes = [k.strip() for k in (keep_ids or []) if k and k.strip()]
    brain_dir = agy_dir / "brain"
    conv_db_dir = agy_dir / "conversations"
    annot_dir = agy_dir / "annotations"
    presence_dir = agy_dir / "presence"

    with _store_lock(agy_dir):
        all_cids = _discover_live_disk_ids(agy_dir)
        summaries_liveness = _query_summaries_liveness(agy_dir / "conversation_summaries.db")

        cleaned: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        valid_kept_ids: set[str] = set()
        newly_deleted_ids: set[str] = set()
        total_files_archived = 0
        total_bytes_archived = 0
        total_bytes_freed = 0

        for cid in sorted(all_cids):
            c_brain = brain_dir / cid
            c_db = conv_db_dir / f"{cid}.db"
            c_annot = annot_dir / f"{cid}.pbtxt"
            c_pres = presence_dir / f"{cid}.lock"
            info = inspect_session(c_brain, now=now) if c_brain.is_dir() else None

            sum_status, sum_not_idle, sum_title = summaries_liveness.get(cid, ("", 0, ""))

            if info is not None:
                state = info["state"]
                age_s = int(info["last_activity_age_s"])
                first_prompt = info.get("first_user_prompt") or sum_title or ""
                mtime = now - age_s
            else:
                state = "ORPHAN_NO_TRANSCRIPT"
                mtime = 0.0
                for candidate in (
                    c_db,
                    conv_db_dir / f"{cid}.db-wal",
                    conv_db_dir / f"{cid}.db-shm",
                    c_brain,
                    c_pres,
                    c_annot,
                ):
                    if candidate.exists():
                        try:
                            mtime = max(mtime, candidate.stat().st_mtime)
                        except OSError:
                            pass
                age_s = int(max(0.0, now - mtime)) if mtime > 0 else 999999
                first_prompt = sum_title or ""

            # 1. Explicit --keep prefix match
            if any(cid.startswith(kp) for kp in keep_prefixes):
                valid_kept_ids.add(cid)
                kept.append(
                    {
                        "conversation_id": cid,
                        "state": state,
                        "last_activity_age_s": age_s,
                        "reason": "explicit_keep",
                        "first_user_prompt": first_prompt,
                    }
                )
                continue

            # 2. Never delete active tool loops, waiting/deadlocked background tasks, or interrupted turns
            if state in PROTECTED_STATES:
                valid_kept_ids.add(cid)
                kept.append(
                    {
                        "conversation_id": cid,
                        "state": state,
                        "last_activity_age_s": age_s,
                        "reason": f"protected_state:{state}",
                        "first_user_prompt": first_prompt,
                    }
                )
                continue

            # 3. Respect language_server's authoritative running/not-idle flags in conversation_summaries.db
            if (sum_status and sum_status != "CASCADE_RUN_STATUS_IDLE") or sum_not_idle != 0:
                valid_kept_ids.add(cid)
                kept.append(
                    {
                        "conversation_id": cid,
                        "state": state,
                        "last_activity_age_s": age_s,
                        "reason": f"summary_live:{sum_status or 'not_fully_idle'}",
                        "first_user_prompt": first_prompt,
                    }
                )
                continue

            # 4. Protect recently active sessions (and give ORPHAN_NO_TRANSCRIPT a 1-hour grace window by default)
            effective_min_idle = (
                max(min_idle_seconds, 3600)
                if (state == "ORPHAN_NO_TRANSCRIPT" and min_idle_seconds > 0)
                else min_idle_seconds
            )
            if age_s < effective_min_idle:
                valid_kept_ids.add(cid)
                kept.append(
                    {
                        "conversation_id": cid,
                        "state": state,
                        "last_activity_age_s": age_s,
                        "reason": f"active_within_{effective_min_idle}s",
                        "first_user_prompt": first_prompt,
                    }
                )
                continue

            # 5. Archive all deliverables, transcripts, task logs, and a WAL-safe DB snapshot BEFORE deleting
            try:
                files_arch, bytes_arch, arch_dest = _archive_session_deliverables(
                    c_brain,
                    archive_dir,
                    first_prompt,
                    mtime if mtime > 0 else now,
                    dry_run=dry_run,
                    conv_db_path=c_db,
                    annot_path=c_annot,
                    session_meta={"state": state, "last_activity_age_s": age_s},
                )
            except Exception as exc:
                valid_kept_ids.add(cid)
                kept.append(
                    {
                        "conversation_id": cid,
                        "state": state,
                        "last_activity_age_s": age_s,
                        "reason": f"archive_error:{exc}",
                        "first_user_prompt": first_prompt,
                    }
                )
                continue

            total_files_archived += files_arch
            total_bytes_archived += bytes_arch

            related_paths = [
                c_brain,
                c_db,
                conv_db_dir / f"{cid}.db-shm",
                conv_db_dir / f"{cid}.db-wal",
                c_annot,
                c_pres,
            ]
            session_bytes = sum(_dir_size_bytes(rp) for rp in related_paths)
            total_bytes_freed += session_bytes
            newly_deleted_ids.add(cid)

            if not dry_run:
                if c_brain.is_dir():
                    shutil.rmtree(c_brain, ignore_errors=True)
                # Unlink -wal and -shm together with .db
                for rp in (
                    conv_db_dir / f"{cid}.db-wal",
                    conv_db_dir / f"{cid}.db-shm",
                    c_db,
                    c_annot,
                    c_pres,
                ):
                    try:
                        rp.unlink(missing_ok=True)
                    except OSError:
                        pass

            cleaned.append(
                {
                    "conversation_id": cid,
                    "state": state,
                    "last_activity_age_s": age_s,
                    "first_user_prompt": first_prompt,
                    "deliverables_archived": files_arch,
                    "archive_path": arch_dest,
                    "bytes_freed": session_bytes,
                }
            )

        sidebar_sync: dict[str, Any] = {}
        if not dry_run:
            sidebar_sync = _sync_sidebar_stores(
                agy_dir=agy_dir,
                app_storage_path=app_storage_path,
                valid_ids=valid_kept_ids,
                newly_deleted_ids=newly_deleted_ids,
            )

        return {
            "dry_run": dry_run,
            "cleaned_count": len(cleaned),
            "kept_count": len(kept),
            "deliverables_archived_count": total_files_archived,
            "deliverables_archived_bytes": total_bytes_archived,
            "bytes_freed": total_bytes_freed,
            "mb_freed": round(total_bytes_freed / (1024 * 1024), 2),
            "archive_dir": str(archive_dir),
            "cleaned_sessions": cleaned,
            "kept_sessions": kept,
            "sidebar_sync": sidebar_sync,
        }


def register_sessions_subparser(subparsers: Any, show_all_aliases: bool = False) -> None:
    for alias in ("sessions", "chats", "ps"):
        s_help = (
            "Live agent session, background task & deadlock auditor (aliases: chats, ps)" if show_all_aliases else None
        )
        p = subparsers.add_parser(alias, **({"help": s_help} if s_help else {}))
        p.add_argument(
            "-m",
            "--minutes",
            type=int,
            default=0,
            help="Filter to sessions active within the last N minutes (default: 0 = all open sidebar sessions)",
        )
        p.add_argument(
            "--check-remote",
            action="store_true",
            help="Also inspect configured remote peer (`KB_REMOTE_PEER`) for deadlocked processes (or clean remote peer with `--clean`)",
        )
        p.add_argument(
            "--heal",
            action="store_true",
            help="Automatically kill deadlocked non-batchmode Unity modals that hit C# compile errors",
        )
        p.add_argument(
            "--clean",
            "--prune",
            dest="clean",
            action="store_true",
            help="Archive deliverables from completed/idle Antigravity sessions and purge them from disk & sidebar",
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview which completed/idle sessions would be archived & purged by `--clean` without deleting",
        )
        p.add_argument(
            "--keep",
            action="append",
            default=[],
            help="Conversation ID or prefix to explicitly keep during `--clean` (may be specified multiple times)",
        )
        p.add_argument(
            "--include-recent",
            action="store_true",
            help="Also clean idle sessions modified within the last 120s (use on idle nodes only)",
        )
        p.add_argument(
            "--quiet",
            action="store_true",
            help="Only emit output when a deadlock is detected or healed (for cron/launchd use)",
        )
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON session report")


def cmd_sessions(args: argparse.Namespace) -> None:
    if getattr(args, "clean", False):
        dry_run = bool(getattr(args, "dry_run", False))
        keep_ids = list(getattr(args, "keep", None) or [])
        include_recent = bool(getattr(args, "include_recent", False))
        min_idle = 0 if include_recent else 120
        report = clean_antigravity_sessions(
            dry_run=dry_run,
            keep_ids=keep_ids,
            min_idle_seconds=min_idle,
        )
        if getattr(args, "check_remote", False):
            remote_peer = _get_cluster_env("KB_REMOTE_PEER")
            if remote_peer:
                remote_flags = ["sessions", "--clean", "--json"]
                if include_recent:
                    remote_flags.append("--include-recent")
                if dry_run:
                    remote_flags.append("--dry-run")
                for kid in keep_ids:
                    if re.fullmatch(r"[A-Za-z0-9_-]+", kid):
                        remote_flags.extend(["--keep", kid])
                try:
                    raw_rem = _run_node_shell(
                        remote_peer,
                        f"~/.local/bin/skb {' '.join(remote_flags)} 2>/dev/null",
                        timeout=120,
                    )
                    brace_idx = raw_rem.find("{")
                    report["remote_peer"] = (
                        json.loads(raw_rem[brace_idx:]) if brace_idx >= 0 else {"raw_output": raw_rem}
                    )
                except Exception as exc:
                    report["remote_peer"] = {"error": str(exc)}

        if getattr(args, "json", False):
            print(json.dumps(report, indent=2))
            return

        mode_lbl = "DRY-RUN PREVIEW" if dry_run else "EXECUTED"
        print(
            f"=== Sovereign Antigravity Session Archiver & Sidebar Cleaner (`kb sessions --clean` [{mode_lbl}]) ===\n"
            f"  Sessions Purged : {report['cleaned_count']} | Sessions Kept (Active/Protected): {report['kept_count']}\n"
            f"  Deliverables    : {report['deliverables_archived_count']} file(s) archived -> {report['archive_dir']}\n"
            f"  Disk Freed      : {report['mb_freed']:.2f} MB ({report['bytes_freed']:,} bytes)"
        )
        if report["sidebar_sync"]:
            ss = report["sidebar_sync"]
            print(
                f"  Sidebar Sync    : SQLite rows={ss['db_rows_remaining']} (trigger={'ON' if ss['sqlite_trigger_installed'] else 'OFF'}) "
                f"| Protobuf chunks={ss['pb_chunks_kept']} ({ss['pb_bytes']} B) | Blacklist={ss['blacklist_count']}"
            )
        for c in report["cleaned_sessions"]:
            print(
                f"    [CLEANED] {c['conversation_id']} ({c['state']}, idle={c['last_activity_age_s']}s, "
                f"archived={c['deliverables_archived']}, freed={c['bytes_freed'] / (1024 * 1024):.2f} MB)"
            )
        for k in report["kept_sessions"]:
            print(
                f"    [KEPT   ] {k['conversation_id']} ({k['state']}, idle={k['last_activity_age_s']}s, reason={k['reason']})"
            )
        if "remote_peer" in report:
            rm2 = report["remote_peer"]
            peer_lbl = _get_cluster_env("KB_REMOTE_PEER") or "remote"
            if "cleaned_count" in rm2:
                print(
                    f"\n  [Remote `{peer_lbl}` Clean ({mode_lbl})]: Purged={rm2['cleaned_count']} | "
                    f"Kept={rm2['kept_count']} | Archived={rm2['deliverables_archived_count']} | "
                    f"Freed={rm2['mb_freed']:.2f} MB"
                )
            else:
                print(f"\n  [Remote `{peer_lbl}` Clean]: {rm2}")
        return

    brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
    minutes = int(getattr(args, "minutes", 0))
    cutoff_s = minutes * 60 if minutes > 0 else 0
    now = time.time()

    sessions: list[dict[str, Any]] = []
    if brain_dir.exists():
        for conv_dir in brain_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if not tpath.exists():
                continue
            info = inspect_session(conv_dir, now=now)
            if not info:
                continue
            if cutoff_s > 0 and info["last_activity_age_s"] > cutoff_s and info["state"] not in PROTECTED_STATES:
                continue
            sessions.append(info)

    sessions.sort(key=lambda x: (0 if x["state"] != "IDLE_COMPLETE" else 1, x["last_activity_age_s"]))

    active_count = sum(
        1 for s in sessions if s["state"] in ("ACTIVE_TOOL_LOOP", "WAITING_ON_BG_TASK", "DEADLOCKED_BG_TASK")
    )
    quiet = getattr(args, "quiet", False)

    remote_checks = []
    # In --quiet (cron/launchd) mode, skip remote SSH completely when no local session is active/waiting
    if getattr(args, "check_remote", False) or getattr(args, "heal", False):
        if not quiet or active_count > 0:
            remote_checks = scan_remote_unity_deadlocks(heal=getattr(args, "heal", False))

    deadlocked_count = sum(1 for s in sessions if s["state"] == "DEADLOCKED_BG_TASK") + sum(
        1 for r in remote_checks if r.get("deadlocked")
    )

    if quiet and deadlocked_count == 0:
        return

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "active_sessions_count": active_count,
                    "deadlocked_count": deadlocked_count,
                    "safe_to_restart": active_count == 0,
                    "sessions": sessions,
                    "remote_unity_checks": remote_checks,
                },
                indent=2,
            )
        )
        return

    window_lbl = f"{minutes}m" if minutes > 0 else "all-open"
    print(
        f"=== Sovereign Agent Session & Background Task Auditor (`kb sessions`, window={window_lbl}, total={len(sessions)}) ===\n"
        f"  Active / Running Sessions : {active_count} | Deadlocks Detected: {deadlocked_count} "
        f"| Safe to Restart IDE: {'YES' if active_count == 0 else 'NO'}"
    )

    for s in sessions:
        cid = s["conversation_id"]
        st = s["state"]
        age = s["last_activity_age_s"]
        step = s["last_step_index"]
        prompt = s["first_user_prompt"] or "(no user prompt)"
        print(f"\n  [{st:20s}] {cid} (step={step}, last_event={age}s ago)")
        print(f"    Initial Prompt : {prompt}")
        if s["last_user_prompt"] and s["last_user_prompt"] != s["first_user_prompt"]:
            print(f"    Latest Prompt  : [step {s['last_user_step']}] {s['last_user_prompt']}")
        if s["running_tasks"]:
            for rt in s["running_tasks"]:
                print(f"    Running Task   : {rt['task_id']} (log idle={rt['log_idle_s']}s)")
                for err in rt["fatal_errors"]:
                    print(f"      [FATAL ERROR]: {err}")

    if remote_checks:
        print("\n  [Remote Cluster Engine Checks]")
        for r in remote_checks:
            status_lbl = "DEADLOCKED (NO -batchmode + CS ERROR)" if r["deadlocked"] else "RUNNING"
            if r["healed"]:
                status_lbl = "HEALED (KILLED STUCK UNITY MODAL)"
            print(f"    - [{r['node']}] {status_lbl}: {r['process']}")
            for cerr in r["compile_errors"]:
                print(f"      [COMPILE ERROR]: {cerr}")
