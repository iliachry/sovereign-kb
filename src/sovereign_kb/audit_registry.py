"""Sovereign Audit Provenance & Subproject Cache Registry (`~/.swm/audit_registry.json` & `.swm/passport.yaml`).

Tracks when each project (and subproject) was last audited, at what Git commit and
scoped tree SHA, with which models (`kb-gwi`, `claude-fable-5-1`, `gpt-6-astra`, `qwen3.6-35b-a3b`),
and under which exact versions of `kb` (`sovereign-kb`), `gwi`, and `swm` (`software-machine`).
Enables O(1) subproject cache hits (`--incremental`) when a subproject's scoped Git tree
and `gwi_version` have not changed since its last passing audit.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__ as KB_VERSION
from .gwi_core import GWI_STANDARD_VERSION

_DEFAULT_AUDIT_REGISTRY_PATH = Path.home() / ".swm" / "audit_registry.json"
AUDIT_REGISTRY_PATH = _DEFAULT_AUDIT_REGISTRY_PATH
_STARTER_VAULT_DIR = (Path(__file__).resolve().parent / "starter_vault").resolve()
_WARNED_SWM_BIN_VALUES: set[str] = set()


def is_bundled_starter_vault_path(path: Path) -> bool:
    """Return True if `path` (following symlinks and case-insensitive FS aliases) resolves to or inside `starter_vault`."""
    try:
        starter_stat = os.stat(_STARTER_VAULT_DIR)
    except OSError:
        starter_stat = None
    expanded = path.expanduser()
    try:
        resolved = expanded.resolve()
    except OSError:
        resolved = expanded
    for cand in (expanded, resolved, *expanded.parents, *resolved.parents):
        if cand == _STARTER_VAULT_DIR:
            return True
        if starter_stat is not None:
            try:
                st = os.stat(cand)
                if st.st_dev == starter_stat.st_dev and st.st_ino == starter_stat.st_ino:
                    return True
            except OSError:
                continue
    return False


def resolve_swm_bin() -> Path | None:
    """Locate the optional `swm` (`software-machine`) Studio Engine binary.

    Setting `SWM_BIN=""` explicitly forces Open-Core standalone mode (used by tests and CI).
    """
    if "SWM_BIN" in os.environ:
        raw = os.environ["SWM_BIN"].strip()
        if not raw:
            return None
        cand = Path(raw).expanduser()
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand.resolve()
        if raw not in _WARNED_SWM_BIN_VALUES:
            _WARNED_SWM_BIN_VALUES.add(raw)
            print(
                f"[organon] Warning: SWM_BIN='{raw}' is missing or non-executable; running in Open-Core mode.",
                file=sys.stderr,
            )
        return None
    which_swm = shutil.which("swm")
    if which_swm:
        return Path(which_swm).resolve()
    local_swm = Path.home() / ".local" / "bin" / "swm"
    if local_swm.is_file() and os.access(local_swm, os.X_OK):
        return local_swm.resolve()
    return None


def resolve_organon_vault_state() -> tuple[Path, str]:
    """Resolve the active Organon knowledge vault and its configuration provenance.

    Returns `(vault_path, source)` where `source` is one of:
      - `"env"`               : `ME_KB_DIR` or `KB_VAULT_DIR` explicitly set
      - `"cwd"`               : `Path.cwd()` (or an ancestor) is an initialized vault
      - `"config"`            : `~/.config/sovereign-kb/config.json` specifies `vault_dir`
      - `"studio_default"`    : `~/GitLab/me-kb` exists on a Studio workstation
      - `"default_existing"`  : `~/sovereign-kb-vault` exists and is initialized
      - `"forbidden_starter"` : Explicit config/symlink targets read-only bundled `starter_vault`
      - `"unconfigured"`      : No vault configured or initialized anywhere (never returns `starter_vault`)
    """
    for env_key in ("ME_KB_DIR", "KB_VAULT_DIR"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            env_p = Path(env_val).expanduser().resolve()
            if is_bundled_starter_vault_path(env_p):
                return env_p, "forbidden_starter"
            return env_p, "env"

    ignore_default_mekb = os.environ.get("ORGANON_IGNORE_DEFAULT_ME_KB", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    default_mekb = (Path.home() / "GitLab" / "me-kb").resolve()

    try:
        cwd = Path.cwd().resolve()
        for candidate in [cwd, *cwd.parents]:
            if (
                is_bundled_starter_vault_path(candidate)
                or (candidate / "src" / "sovereign_kb" / "__init__.py").is_file()
            ):
                continue
            if ignore_default_mekb and candidate == default_mekb:
                continue
            if (candidate / ".kb").is_dir() or (
                (candidate / "INDEX.md").is_file() and (candidate / "00-identity").is_dir()
            ):
                return candidate, "cwd"
    except OSError:
        pass

    cfg_path = Path.home() / ".config" / "sovereign-kb" / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and str(cfg.get("vault_dir", "")).strip():
                cfg_v = Path(str(cfg["vault_dir"]).strip()).expanduser().resolve()
                if is_bundled_starter_vault_path(cfg_v):
                    return cfg_v, "forbidden_starter"
                if not (ignore_default_mekb and cfg_v == default_mekb):
                    return cfg_v, "config"
        except Exception:
            pass

    if not ignore_default_mekb and default_mekb.is_dir() and not is_bundled_starter_vault_path(default_mekb):
        return default_mekb, "studio_default"

    default_skb_space = Path.home() / "sovereign-kb-vault"
    default_skb_vault = default_skb_space.resolve()
    if (default_skb_space.is_symlink() or default_skb_space.exists()) and is_bundled_starter_vault_path(
        default_skb_vault
    ):
        return default_skb_vault, "forbidden_starter"
    if default_skb_vault.is_dir() and (
        (default_skb_vault / ".kb").is_dir() or (default_skb_vault / "INDEX.md").is_file()
    ):
        return default_skb_vault, "default_existing"

    return default_skb_vault, "unconfigured"


def resolve_active_vault_dir() -> Path:
    """Resolve the active Organon knowledge vault across Studio (`me-kb`) and Open-Core (`skb`) setups."""
    vault_path, source = resolve_organon_vault_state()
    if source == "forbidden_starter" or is_bundled_starter_vault_path(vault_path):
        for cand in (Path.home() / "sovereign-kb-vault", Path.home() / ".swm" / "unconfigured-vault"):
            if not is_bundled_starter_vault_path(cand):
                return cand.resolve()
        import tempfile

        td_fallback = Path(tempfile.gettempdir()) / "sovereign-kb-unconfigured-vault"
        if is_bundled_starter_vault_path(td_fallback):
            raise ValueError("Bundled starter_vault cannot be used as active vault directory")
        return td_fallback
    return vault_path


def _should_skip_default_registry_write(repo_root: Path) -> bool:
    """Prevent pytest temporary directories from polluting the user's real `~/.swm/audit_registry.json`."""
    if AUDIT_REGISTRY_PATH != _DEFAULT_AUDIT_REGISTRY_PATH:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    s = str(repo_root)
    return any(tok in s for tok in ("/var/folders/", "/tmp/", "/private/tmp/", "pytest-of-"))


def _run_git(cwd: Path, *args: str, timeout: float = 3.0) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


def find_git_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    probe = resolved.parent if resolved.is_file() else resolved
    if not probe.is_dir():
        return resolved
    top = _run_git(probe, "rev-parse", "--show-toplevel")
    if top:
        top_p = Path(top).resolve()
        home = Path.home().resolve()
        if top_p not in (home, home / "Projects", home / "GitLab", home / "GitHub"):
            return top_p
    return probe


def get_sovereign_versions() -> dict[str, str]:
    """Return current versions and Git SHAs for `kb` (`sovereign-kb`), `gwi`, `swm`, and the active vault."""
    pkg_repo = Path(__file__).resolve().parents[2]
    skb_root = pkg_repo if (pkg_repo / ".git").exists() else (Path.home() / "Projects" / "sovereign-kb")
    kb_sha = _run_git(skb_root, "rev-parse", "--short", "HEAD") if (skb_root / ".git").exists() else ""

    swm_bin = resolve_swm_bin()
    if swm_bin is None:
        swm_version = "open-core"
        swm_sha_out = "not-installed"
    else:
        swm_root: Path | None = None
        for cand in (
            Path.home() / "Projects" / "software-machine",
            Path.home() / "GitLab" / "software-machine",
        ):
            if cand.is_dir():
                swm_root = cand
                break
        swm_sha = (
            _run_git(swm_root, "rev-parse", "--short", "HEAD")
            if (swm_root is not None and (swm_root / ".git").exists())
            else ""
        )
        swm_version = "0.2.0"
        if swm_root is not None:
            swm_init = swm_root / "src" / "software_machine" / "__init__.py"
            if swm_init.is_file():
                m = re.search(
                    r'__version__\s*=\s*["\']([^"\']+)["\']',
                    swm_init.read_text(encoding="utf-8", errors="ignore"),
                )
                if m:
                    swm_version = m.group(1)
        swm_sha_out = swm_sha or "unknown"

    mekb_root, vault_source = resolve_organon_vault_state()
    if vault_source in ("unconfigured", "forbidden_starter") or not mekb_root.is_dir():
        mekb_sha_out = "unconfigured"
    elif (mekb_root / ".git").exists():
        mekb_sha_out = _run_git(mekb_root, "rev-parse", "--short", "HEAD") or "unknown"
    else:
        mekb_sha_out = "unknown"

    return {
        "kb_version": KB_VERSION,
        "kb_sha": kb_sha or "unknown",
        "gwi_version": GWI_STANDARD_VERSION,
        "swm_version": swm_version,
        "swm_sha": swm_sha_out,
        "me_kb_sha": mekb_sha_out,
    }


def discover_subprojects(repo_root: Path) -> list[dict[str, str]]:
    """Auto-discover auditable subprojects inside a repository so multi-deliverable repos can be audited per-unit."""
    from .gwi import detect_profile

    root = find_git_root(repo_root)
    if not root.is_dir():
        return []

    subprojects: list[dict[str, str]] = []
    seen_subpaths: set[str] = set()

    def _add(subpath: str, profile: str, kind: str) -> None:
        norm = subpath.strip("/").strip() or "."
        if norm in seen_subpaths:
            return
        seen_subpaths.add(norm)
        name = root.name if norm == "." else f"{root.name}:{norm}"
        subprojects.append(
            {
                "name": name,
                "subpath": norm,
                "profile": profile,
                "kind": kind,
                "abs_path": str((root if norm == "." else root / norm).resolve()),
            }
        )

    # 1. Root deliverable
    root_prof = detect_profile(root)
    _add(".", root_prof, "root")

    # 2. Child paper directories (`paper/`, `paper_web/`, `book/`, `manuscript/`)
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in ("node_modules", "dist", "build", "target", "venv", ".venv", "evidence", "artifacts"):
            continue
        if (child / "main.tex").is_file():
            _add(child.name, "paper", "paper-dir")
        elif (
            child.name in ("web", "frontend", "client", "app")
            and ((child / "AGENTS.md").is_file() or root_prof != "app")
            and ((child / "package.json").is_file() or (child / "index.html").is_file())
        ):
            _add(child.name, "app", "web-dir")
        elif child.name == "video" and ((child / "package.json").is_file() or (child / "index.html").is_file()):
            _add(child.name, "app", "cinema-engine")
        elif child.name in ("apps", "packages", "services"):
            for pkg in sorted(child.iterdir()):
                if pkg.is_dir() and not pkg.name.startswith(".") and (pkg / "AGENTS.md").is_file():
                    rel = f"{child.name}/{pkg.name}"
                    _add(rel, detect_profile(pkg), "workspace-pkg")

    # 3. Standalone master `.mp4` showcase/walkthrough files at root or 1 level deep
    for mp4 in sorted(root.glob("*.mp4")):
        low = mp4.name.lower()
        if any(s in low for s in ("_compressed.mp4", "_20mb.mp4", "_under20mb.mp4", "_web.mp4", "_small.mp4")):
            continue
        _add(mp4.name, "video", "master-video")

    return subprojects


def compute_subproject_fingerprint(
    repo_root: Path,
    subpath: str,
    sibling_subpaths: list[str] | None = None,
) -> dict[str, Any]:
    """Compute a fast, deterministic Git commit + scoped tree hash for a subproject."""
    root = find_git_root(repo_root)
    norm = subpath.strip("/").strip() or "."
    commit_sha = _run_git(root, "rev-parse", "--short", "HEAD")
    full_commit_sha = _run_git(root, "rev-parse", "HEAD")

    target_path = root if norm == "." else (root / norm)
    siblings = [s.strip("/").strip() for s in (sibling_subpaths or []) if s.strip("/").strip() not in ("", ".", norm)]

    tree_sha = ""
    dirty = False

    if norm == ".":
        # Hash `git ls-tree HEAD` entries at root, excluding sibling subproject directories/files
        ls_out = _run_git(root, "ls-tree", "HEAD")
        if ls_out:
            top_excluded = {s.split("/")[0] for s in siblings}
            kept_lines = []
            for line in ls_out.splitlines():
                parts = line.split("\t", 1)
                entry_name = parts[1].strip() if len(parts) == 2 else ""
                if entry_name not in top_excluded:
                    kept_lines.append(line)
            tree_sha = hashlib.sha256("\n".join(kept_lines).encode("utf-8")).hexdigest()[:16]
        else:
            tree_sha = "uncommitted"

        status_out = _run_git(root, "status", "--porcelain")
        if status_out:
            top_excluded = {s.split("/")[0] for s in siblings}
            for line in status_out.splitlines():
                parts_st = line.strip().split(None, 1)
                rel_file = (parts_st[1] if len(parts_st) == 2 else line).strip().strip('"')
                top_seg = rel_file.split("/")[0]
                if (
                    top_seg not in top_excluded
                    and not rel_file.startswith(".swm/")
                    and not rel_file.startswith("evidence/")
                ):
                    dirty = True
                    break
    else:
        rev_tree = _run_git(root, "rev-parse", f"HEAD:{norm}")
        if rev_tree:
            tree_sha = rev_tree[:16]
        elif target_path.is_file():
            h = hashlib.sha256()
            with target_path.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            tree_sha = h.hexdigest()[:16]
        else:
            tree_sha = "uncommitted"

        status_out = _run_git(root, "status", "--porcelain", "--", norm)
        dirty = bool(status_out.strip())

    # Compute deterministic fingerprint of untracked/tracked runtime evidence & diagnostics artifacts
    ev_hasher = hashlib.sha256()
    ev_root = target_path if target_path.is_dir() else target_path.parent
    if ev_root.is_dir():
        seen_ev: set[Path] = set()
        ev_candidates: list[Path] = []
        for pat in (
            "evidence/**/*.json",
            "evidence/**/*.txt",
            "artifacts/**/*.json",
            "artifacts/**/*.txt",
            "results/**/*.json",
            "**/walkthrough-report.json",
        ):
            for p_ev in sorted(ev_root.glob(pat)):
                if any(part in (".git", "node_modules", ".venv", "target", "dist", "build") for part in p_ev.parts):
                    continue
                if p_ev.name in ("goldwood.json", "gate_receipt.json") or p_ev in seen_ev:
                    continue
                if p_ev.is_file():
                    seen_ev.add(p_ev)
                    ev_candidates.append(p_ev)
        ev_candidates.sort(key=lambda p: str(p))
        for p_ev in ev_candidates:
            try:
                rel_ev = str(p_ev.relative_to(ev_root))
            except ValueError:
                rel_ev = p_ev.name
            ev_hasher.update(rel_ev.encode("utf-8"))
            try:
                ev_hasher.update(p_ev.read_bytes())
            except OSError:
                pass
    evidence_sha = ev_hasher.hexdigest()[:16]

    return {
        "commit_sha": commit_sha or "uncommitted",
        "full_commit_sha": full_commit_sha or "",
        "tree_sha": tree_sha or "unknown",
        "evidence_sha": evidence_sha,
        "dirty": dirty,
    }


def load_audit_registry() -> dict[str, Any]:
    if AUDIT_REGISTRY_PATH.is_file():
        try:
            data = json.loads(AUDIT_REGISTRY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("projects"), dict):
                return data
        except Exception:
            pass
    return {
        "schema_version": "1.0.0",
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "current_versions": get_sovereign_versions(),
        "projects": {},
    }


def save_audit_registry(registry: dict[str, Any]) -> Path:
    AUDIT_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    registry["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    registry["current_versions"] = get_sovereign_versions()
    AUDIT_REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return AUDIT_REGISTRY_PATH


def _is_git_tracked(repo_root: Path, rel_path: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", rel_path],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _update_passport_yaml_audit(repo_root: Path, project_entry: dict[str, Any], force_write: bool = False) -> None:
    """Synchronize `last_audit` and `subprojects` into `<repo_root>/.swm/passport.yaml` when explicitly requested."""
    if not force_write:
        return
    passport_path = repo_root / ".swm" / "passport.yaml"
    if not passport_path.is_file():
        return
    try:
        raw = passport_path.read_text(encoding="utf-8")
        # Strip any previous auto-managed `# --- Sovereign Audit Provenance ---` block
        marker = "# --- Sovereign Audit Provenance (Auto-Managed by kb gwi / swm) ---"
        if marker in raw:
            raw = raw.split(marker, 1)[0].rstrip() + "\n"
        else:
            raw = raw.rstrip() + "\n"

        last_a = project_entry.get("last_audit") or {}
        triad = project_entry.get("triad") or {}
        versions = last_a.get("versions") or get_sovereign_versions()
        models = last_a.get("models") or []
        subs = project_entry.get("subprojects") or {}

        lines = [
            marker,
            "last_audit:",
            f"  audited_at: '{last_a.get('audited_at', '')}'",
            f"  commit_sha: '{last_a.get('commit_sha', '')}'",
            f"  tree_sha: '{last_a.get('tree_sha', '')}'",
            f"  profile: '{last_a.get('profile', 'auto')}'",
            f"  goldwood_g: {last_a.get('goldwood_g', 0.0)}",
            f"  coupled_g: {last_a.get('coupled_g', 0.0)}",
            f"  status: '{last_a.get('status', 'UNAUDITED')}'",
            f"  blockers_count: {last_a.get('blockers_count', 0)}",
            f"  models: {json.dumps(models)}",
            "  triad:",
            f"    ever_audited: {str(bool(triad.get('ever_audited', False))).lower()}",
            f"    audited_at: {json.dumps(triad.get('audited_at'))}",
            f"    commit_sha: {json.dumps(triad.get('commit_sha'))}",
            f"    disposition: '{triad.get('disposition', 'UNAUDITED')}'",
            f"    fable_model: {json.dumps(triad.get('fable_model'))}",
            f"    astra_model: {json.dumps(triad.get('astra_model'))}",
            "  versions:",
            f"    kb_version: '{versions.get('kb_version', KB_VERSION)}'",
            f"    kb_sha: '{versions.get('kb_sha', '')}'",
            f"    gwi_version: '{versions.get('gwi_version', GWI_STANDARD_VERSION)}'",
            f"    swm_version: '{versions.get('swm_version', '0.2.0')}'",
            f"    swm_sha: '{versions.get('swm_sha', '')}'",
            f"    me_kb_sha: '{versions.get('me_kb_sha', '')}'",
        ]
        if subs:
            lines.append("subprojects_audit:")
            for sp_key, sp_val in sorted(subs.items()):
                sp_triad = sp_val.get("triad") or {}
                sp_ver = sp_val.get("versions") or versions
                lines.extend(
                    [
                        f"  {json.dumps(sp_key)}:",
                        f"    profile: '{sp_val.get('profile', 'auto')}'",
                        f"    audited_at: {json.dumps(sp_val.get('audited_at'))}",
                        f"    commit_sha: {json.dumps(sp_val.get('commit_sha'))}",
                        f"    tree_sha: {json.dumps(sp_val.get('tree_sha'))}",
                        f"    goldwood_g: {json.dumps(sp_val.get('goldwood_g'))}",
                        f"    coupled_g: {json.dumps(sp_val.get('coupled_g'))}",
                        f"    status: '{sp_val.get('status', 'UNAUDITED')}'",
                        f"    blockers_count: {sp_val.get('blockers_count', 0)}",
                        f"    models: {json.dumps(sp_val.get('models') or [])}",
                        f"    triad_disposition: '{sp_triad.get('disposition', 'UNAUDITED')}'",
                        f"    triad_audited_at: {json.dumps(sp_triad.get('audited_at'))}",
                        f"    triad_commit_sha: {json.dumps(sp_triad.get('commit_sha'))}",
                        f"    gwi_version: '{sp_ver.get('gwi_version', GWI_STANDARD_VERSION)}'",
                        f"    kb_version: '{sp_ver.get('kb_version', KB_VERSION)}'",
                        f"    swm_version: '{sp_ver.get('swm_version', '0.2.0')}'",
                    ]
                )
        passport_path.write_text(raw + "\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def record_gwi_audit(
    target_path: Path,
    report: dict[str, Any],
    models_used: list[str] | None = None,
) -> dict[str, Any]:
    """Record a GWI audit result for a project or subproject into `~/.swm/audit_registry.json` and `.swm/passport.yaml`."""
    resolved = target_path.expanduser().resolve()
    repo_root = find_git_root(resolved)
    repo_name = repo_root.name

    try:
        rel = resolved.relative_to(repo_root)
        subpath = str(rel) if str(rel) != "." else "."
    except ValueError:
        subpath = "."

    discovered = discover_subprojects(repo_root)
    sibling_subpaths = [d["subpath"] for d in discovered]
    fp = compute_subproject_fingerprint(repo_root, subpath, sibling_subpaths=sibling_subpaths)
    versions = get_sovereign_versions()

    calc = report.get("calculation") or {}
    cl = report.get("craft_lenses") or {}
    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    registry = load_audit_registry()
    proj = registry["projects"].setdefault(
        repo_name,
        {
            "repo_name": repo_name,
            "repo_root": str(repo_root),
            "last_audit": {},
            "triad": {
                "ever_audited": False,
                "audited_at": None,
                "commit_sha": None,
                "disposition": "UNAUDITED",
                "fable_model": None,
                "astra_model": None,
            },
            "subprojects": {},
        },
    )
    proj["repo_root"] = str(repo_root)

    # Ensure all discovered subprojects have at least a stub entry so `kb gwi status` shows unaudited siblings too
    for d in discovered:
        sp = d["subpath"]
        if sp not in proj["subprojects"]:
            sp_fp = compute_subproject_fingerprint(repo_root, sp, sibling_subpaths=sibling_subpaths)
            proj["subprojects"][sp] = {
                "subpath": sp,
                "name": d["name"],
                "profile": d["profile"],
                "kind": d["kind"],
                "audited_at": None,
                "commit_sha": sp_fp["commit_sha"],
                "tree_sha": None,
                "goldwood_g": None,
                "coupled_g": None,
                "status": "UNAUDITED",
                "blockers_count": None,
                "models": [],
                "triad": {
                    "ever_audited": False,
                    "audited_at": None,
                    "commit_sha": None,
                    "disposition": "UNAUDITED",
                    "fable_model": None,
                    "astra_model": None,
                },
                "versions": {},
            }

    prev_sp = proj["subprojects"].get(subpath) or {}
    prev_triad = (
        prev_sp.get("triad")
        or proj.get("triad")
        or {
            "ever_audited": False,
            "audited_at": None,
            "commit_sha": None,
            "disposition": "UNAUDITED",
            "fable_model": None,
            "astra_model": None,
        }
    )

    base_models = list(models_used or [f"kb-gwi/{GWI_STANDARD_VERSION}"])
    if prev_triad.get("ever_audited") and prev_triad.get("commit_sha") == fp["commit_sha"]:
        for m_id in (prev_triad.get("fable_model"), prev_triad.get("astra_model")):
            if m_id and m_id not in base_models:
                base_models.append(m_id)

    audit_flags = report.get("audit_flags") or {}
    audit_record = {
        "subpath": subpath,
        "name": repo_name if subpath == "." else f"{repo_name}:{subpath}",
        "profile": report.get("profile", "auto"),
        "audited_at": now_iso,
        "commit_sha": fp["commit_sha"],
        "full_commit_sha": fp["full_commit_sha"],
        "tree_sha": fp["tree_sha"],
        "evidence_sha": fp.get("evidence_sha", ""),
        "dirty_at_audit": fp["dirty"],
        "strict_lenses": bool(audit_flags.get("strict_lenses", False)),
        "probe_audio": bool(audit_flags.get("probe_audio", False)),
        "execute_gate": bool(audit_flags.get("execute_gate", False)),
        "goldwood_g": calc.get("goldwood_g"),
        "coupled_g": cl.get("coupled_goldwood_g", calc.get("goldwood_g")),
        "status": calc.get("status", "FAIL"),
        "blockers_count": len(report.get("blockers") or []),
        "models": base_models,
        "triad": prev_triad,
        "versions": versions,
    }

    proj["subprojects"][subpath] = audit_record
    if subpath == "." or not proj.get("last_audit"):
        proj["last_audit"] = audit_record

    in_gate_exec = os.environ.get("GWI_IN_GATE_EXECUTION") == str(repo_root.resolve())
    if not _should_skip_default_registry_write(repo_root) and not in_gate_exec:
        save_audit_registry(registry)
        _update_passport_yaml_audit(repo_root, proj)
        raw_root = str(repo_root)
        is_temp_or_test = (
            bool(os.environ.get("PYTEST_CURRENT_TEST"))
            or "/pytest-of-" in raw_root
            or raw_root.startswith(("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/"))
        )
        if not is_temp_or_test:
            try:
                from .bridge import record_gwi_outcome_feedback

                record_gwi_outcome_feedback(target_path=resolved, report=report, commit_sha=fp["commit_sha"])
            except Exception:
                pass
    return audit_record


def record_triad_audit(
    target_path: Path,
    subpath: str = ".",
    disposition: str = "APPROVED",
    fable_model: str = "amazon-bedrock/global.anthropic.claude-fable-5-1",
    astra_model: str = "amazon-bedrock/global.openai.gpt-6-astra",
    gx10_model: str | None = None,
    notes: str = "",
    audited_at: str | None = None,
    commit_sha_override: str | None = None,
) -> dict[str, Any]:
    """Record a Multi-Model Tri-Council (`Fable 5.1` x `GPT-6 Astra`) audit for a project/subproject."""
    resolved = target_path.expanduser().resolve()
    repo_root = find_git_root(resolved)
    repo_name = repo_root.name

    if subpath == "." and resolved != repo_root:
        try:
            subpath = str(resolved.relative_to(repo_root))
        except ValueError:
            subpath = "."

    discovered = discover_subprojects(repo_root)
    sibling_subpaths = [d["subpath"] for d in discovered]
    fp = compute_subproject_fingerprint(repo_root, subpath, sibling_subpaths=sibling_subpaths)
    versions = get_sovereign_versions()
    now_iso = audited_at or datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    c_sha = commit_sha_override or fp["commit_sha"]

    registry = load_audit_registry()
    proj = registry["projects"].setdefault(
        repo_name,
        {
            "repo_name": repo_name,
            "repo_root": str(repo_root),
            "last_audit": {},
            "triad": {},
            "subprojects": {},
        },
    )
    proj["repo_root"] = str(repo_root)

    triad_info = {
        "ever_audited": True,
        "audited_at": now_iso,
        "commit_sha": c_sha,
        "tree_sha": fp["tree_sha"],
        "disposition": disposition.upper(),
        "fable_model": fable_model,
        "astra_model": astra_model,
        "gx10_model": gx10_model,
        "notes": notes,
        "versions": versions,
    }
    proj["triad"] = triad_info

    sp_entry = proj["subprojects"].setdefault(
        subpath,
        {
            "subpath": subpath,
            "name": repo_name if subpath == "." else f"{repo_name}:{subpath}",
            "profile": "auto",
            "audited_at": now_iso,
            "commit_sha": c_sha,
            "tree_sha": fp["tree_sha"],
            "goldwood_g": None,
            "coupled_g": None,
            "status": "PASS" if disposition.upper() == "APPROVED" else "FAIL",
            "blockers_count": 0 if disposition.upper() == "APPROVED" else 1,
            "models": [],
            "triad": triad_info,
            "versions": versions,
        },
    )
    sp_entry["triad"] = triad_info
    models_list = list(sp_entry.get("models") or [f"kb-gwi/{GWI_STANDARD_VERSION}"])
    for m in (fable_model, astra_model, gx10_model):
        if m and m not in models_list:
            models_list.append(m)
    sp_entry["models"] = models_list

    if subpath == "." and proj.get("last_audit"):
        proj["last_audit"]["triad"] = triad_info
        proj["last_audit"]["models"] = models_list

    if not _should_skip_default_registry_write(repo_root):
        save_audit_registry(registry)
        _update_passport_yaml_audit(repo_root, proj)
    return triad_info


def evaluate_subproject_freshness(
    repo_root: Path,
    subpath: str = ".",
    requested_profile: str = "auto",
    require_strict_lenses: bool = False,
    require_probe_audio: bool = False,
    require_execute_gate: bool = False,
) -> dict[str, Any]:
    """Check whether `<repo_root>:<subpath>` has a valid, up-to-date audit or needs re-evaluation."""
    root = find_git_root(repo_root)
    repo_name = root.name
    discovered = discover_subprojects(root)
    sibling_subpaths = [d["subpath"] for d in discovered]
    fp = compute_subproject_fingerprint(root, subpath, sibling_subpaths=sibling_subpaths)
    cur_versions = get_sovereign_versions()

    registry = load_audit_registry()
    proj = registry.get("projects", {}).get(repo_name) or {}
    sp_rec = (proj.get("subprojects") or {}).get(subpath) or {}

    audited_at = sp_rec.get("audited_at")
    rec_tree = sp_rec.get("tree_sha")
    rec_ev_sha = sp_rec.get("evidence_sha")
    rec_commit = sp_rec.get("commit_sha")
    rec_status = sp_rec.get("status", "UNAUDITED")
    rec_ver = sp_rec.get("versions") or {}
    triad = sp_rec.get("triad") or proj.get("triad") or {}

    kb_sha_drift = bool(
        not rec_ver.get("kb_sha")
        or (cur_versions.get("kb_sha") and rec_ver.get("kb_sha") != cur_versions.get("kb_sha"))
    )
    ev_sha_drift = bool(not rec_ev_sha or rec_ev_sha != fp.get("evidence_sha", ""))
    profile_drift = bool(
        requested_profile not in ("auto", "", None)
        and sp_rec.get("profile")
        and sp_rec.get("profile") != requested_profile
    )
    strictness_drift = bool(
        (require_strict_lenses and not sp_rec.get("strict_lenses", False))
        or (require_probe_audio and not sp_rec.get("probe_audio", False))
        or (require_execute_gate and not sp_rec.get("execute_gate", False))
    )

    if not audited_at or not rec_tree:
        freshness = "UNAUDITED"
    elif fp["dirty"]:
        freshness = "DIRTY"
    elif rec_tree != fp["tree_sha"]:
        freshness = "STALE_CODE"
    elif ev_sha_drift:
        freshness = "STALE_EVIDENCE"
    elif rec_ver.get("gwi_version") != cur_versions["gwi_version"] or kb_sha_drift:
        freshness = "VERSION_DRIFT"
    elif profile_drift:
        freshness = "PROFILE_MISMATCH"
    elif strictness_drift:
        freshness = "LOWER_STRICTNESS"
    elif rec_status != "PASS":
        freshness = "FAILED"
    else:
        freshness = "FRESH"

    return {
        "repo_name": repo_name,
        "repo_root": str(root),
        "subpath": subpath,
        "target_label": repo_name if subpath == "." else f"{repo_name}:{subpath}",
        "profile": sp_rec.get("profile") or next((d["profile"] for d in discovered if d["subpath"] == subpath), "auto"),
        "freshness": freshness,
        "is_fresh_pass": freshness == "FRESH" and rec_status == "PASS",
        "current_commit": fp["commit_sha"],
        "current_tree": fp["tree_sha"],
        "current_evidence_sha": fp.get("evidence_sha", ""),
        "dirty": fp["dirty"],
        "audited_at": audited_at,
        "audited_commit": rec_commit,
        "audited_tree": rec_tree,
        "audited_evidence_sha": rec_ev_sha,
        "strict_lenses": bool(sp_rec.get("strict_lenses", False)),
        "probe_audio": bool(sp_rec.get("probe_audio", False)),
        "execute_gate": bool(sp_rec.get("execute_gate", False)),
        "goldwood_g": sp_rec.get("goldwood_g"),
        "coupled_g": sp_rec.get("coupled_g"),
        "status": rec_status,
        "blockers_count": sp_rec.get("blockers_count"),
        "models": sp_rec.get("models") or [],
        "triad_ever_audited": bool(triad.get("ever_audited", False)),
        "triad_disposition": triad.get("disposition", "UNAUDITED"),
        "triad_audited_at": triad.get("audited_at"),
        "triad_commit_sha": triad.get("commit_sha"),
        "versions": rec_ver,
        "current_versions": cur_versions,
    }
