"""Gate 0 (Tool Install): is this `sovereign-kb` the code its source checkout holds?

`uv tool install <checkout>` without `--editable` copies the package into the tool's own site-packages. Commits to
the checkout after that change nothing that `skb`, `gwi`, `organon` and `evr` run until the tool is reinstalled, and
nothing used to say so: on 2026-11-24 one Mac ran six-day-old code for every `kb gwi audit`.

The copy's `*.dist-info/direct_url.json` (PEP 610) records the directory it was built from. This module compares the
copy's Python files with that directory's by content, and asks git which differences are committed:

- `fresh`           the copy matches the checkout, so reinstalling would change nothing
- `uncommitted`     the copy matches the checkout's last commit; the checkout has edits nobody has committed yet
- `stale`           the checkout's committed code differs from the copy (without git: any difference at all)
- `unverifiable`    the copy cannot be compared: its source directory is gone or its install metadata unreadable
- `editable`, `source`, `no-local-source`   nothing installed that can fall behind, or nothing local to compare

`skb verify` fails Gate 0 on `stale` and warns on `uncommitted` and `unverifiable`. Every command-line run of `skb`,
`gwi`, `organon` and `evr` prints one stderr line for `stale` and `unverifiable`. `skb --version` names the state.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import unquote, urlparse

PACKAGE = "sovereign_kb"
FIX_COMMAND = "uv tool upgrade sovereign-kb --reinstall"
GATE = "Gate 0 (Tool Install)"
GIT_TIMEOUT_S = 10.0

FRESH = "fresh"
UNCOMMITTED = "uncommitted"
STALE = "stale"
UNVERIFIABLE = "unverifiable"
EDITABLE = "editable"
SOURCE = "source"
NO_LOCAL_SOURCE = "no-local-source"

_RUNNING_PACKAGE = Path(__file__).resolve().parent


def _python_files(root: Path) -> dict[str, Path]:
    """`*.py` files under `root` by POSIX path relative to it, bytecode caches left out."""
    files: dict[str, Path] = {}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if "__pycache__" not in rel.parts and path.is_file():
            files[rel.as_posix()] = path
    return files


def _same_content(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _files(n: int) -> str:
    return f"{n} Python file" if n == 1 else f"{n} Python files"


def _sample(names: list[str], limit: int = 5) -> str:
    shown = ", ".join(names[:limit])
    return shown if len(names) <= limit else f"{shown} and {len(names) - limit} more"


def _dist_info_beside(package_dir: Path) -> Path | None:
    """The `*.dist-info` next to the package: present when the package is an installed copy in site-packages."""
    try:
        found = sorted(package_dir.parent.glob(f"{PACKAGE}-*.dist-info"))
    except OSError:
        return None
    if len(found) <= 1:
        return found[0] if found else None
    for info in found:  # leftovers of an interrupted upgrade: take the one whose RECORD lists this package
        try:
            if f"{PACKAGE}/__init__.py," in (info / "RECORD").read_text(encoding="utf-8", errors="replace"):
                return info
        except OSError:
            continue
    return found[0] if found else None


def _dist_info_on_path() -> Path | None:
    """Installed `sovereign-kb` metadata anywhere on `sys.path`; an editable install keeps it apart from the code."""
    for entry in sys.path:
        if not entry:
            continue
        try:
            found = sorted(Path(entry).glob(f"{PACKAGE}-*.dist-info"))
        except OSError:
            continue
        if found:
            return found[0]
    return None


def _path_from_file_url(url: str) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    if os.name == "nt":
        import nturl2path

        return Path(nturl2path.url2pathname(parsed.path))
    return Path(unquote(parsed.path))


def _installed_at(info: Path | None) -> str | None:
    if info is None:
        return None
    for marker in (info / "RECORD", info):
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(marker.stat().st_mtime))
        except OSError:
            continue
    return None


def _fix_command(prefix: Path, info: Path, source: Path | None) -> str:
    """The reinstall that rebuilds this copy, for the installer that made it; `source=None` when it has moved."""
    target = shlex.quote(str(source)) if source is not None else "<path to the sovereign-kb checkout>"
    if (prefix / "uv-receipt.toml").is_file():
        return FIX_COMMAND if source is not None else f"uv tool install --force {target}"
    try:
        installer = (info / "INSTALLER").read_text(encoding="utf-8").strip()
    except OSError:
        installer = ""
    python = shlex.quote(sys.executable)
    if installer == "uv":
        return f"uv pip install --python {python} --reinstall {target}"
    return f"{python} -m pip install --force-reinstall --no-deps {target}"


def _git(cwd: Path, *args: str) -> str | None:
    """Read-only git query; None when git is missing, refuses, or times out."""
    # A hook or a caller's GIT_DIR / GIT_INDEX_FILE would point git at another repository: drop them.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        done = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(cwd), *args],
            capture_output=True,
            env=env,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode("utf-8", "surrogateescape") if done.returncode == 0 else None


def _git_blob_id(data: bytes, hex_len: int) -> str:
    algorithm = "sha256" if hex_len == 64 else "sha1"
    return hashlib.new(algorithm, b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _split_by_git(
    source_package: Path, differing: list[str], installed: dict[str, Path]
) -> tuple[list[str], list[str]] | None:
    """Split differing files into (stale, uncommitted) by the checkout's git state; None when git cannot tell."""
    # The package's path below the top level, e.g. "src/sovereign_kb/": `git status` names files from the top.
    prefix_out = _git(source_package, "rev-parse", "--show-prefix")
    # Last commit's blobs under the package, named relative to it (ls-tree lists the current directory).
    head_out = _git(source_package, "ls-tree", "-r", "-z", "HEAD")
    status_out = _git(
        source_package,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored",
        "--no-renames",
        "--",
        ".",
    )
    if prefix_out is None or head_out is None or status_out is None:
        return None
    prefix = prefix_out.strip()

    head: dict[str, str] = {}
    for entry in head_out.split("\0"):
        meta, tab, path = entry.partition("\t")
        fields = meta.split()
        if tab and len(fields) == 3 and fields[1] == "blob":
            head[path] = fields[2]
    dirty: set[str] = set()  # modified, staged, deleted or untracked in the checkout
    ignored: set[str] = set()
    for entry in status_out.split("\0"):
        code, path = entry[:2], entry[3:]
        if len(entry) > 3 and path.startswith(prefix):
            (ignored if code == "!!" else dirty).add(path[len(prefix) :])

    stale: list[str] = []
    uncommitted: list[str] = []
    for rel in differing:
        copy = installed.get(rel)
        if copy is None and rel in ignored:
            continue  # git ignores it, so the build leaves it out and a reinstall cannot change it
        if rel not in dirty:
            stale.append(rel)  # the checkout holds the committed version, and the copy differs from it
            continue
        committed = head.get(rel)
        if committed is None and copy is None:
            uncommitted.append(rel)  # a new file nobody has committed yet
            continue
        try:
            same_as_commit = (
                committed is not None
                and copy is not None
                and _git_blob_id(copy.read_bytes(), len(committed)) == committed
            )
        except OSError:
            same_as_commit = False
        # The copy holds the last commit's version: only uncommitted edits differ. Otherwise the commit moved too.
        (uncommitted if same_as_commit else stale).append(rel)
    return stale, uncommitted


def _report(
    status: str,
    package_dir: Path,
    message: str,
    *,
    source: Path | None = None,
    info: Path | None = None,
    stale: list[str] | None = None,
    uncommitted: list[str] | None = None,
    fix: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "package_dir": str(package_dir),
        "source": str(source) if source is not None else None,
        "installed_at": _installed_at(info),
        "stale_files": list(stale or []),
        "uncommitted_files": list(uncommitted or []),
        "fix": fix,
        "message": message,
    }


def inspect_install(
    package_dir: Path | None = None,
    *,
    dist_info: Path | None = None,
    prefix: Path | None = None,
    quick: bool = False,
) -> dict[str, Any]:
    """Describe how the `sovereign_kb` package at `package_dir` (default: the running one) is installed.

    `quick` skips the `sys.path` search that only names an editable or source run, which cannot fall behind.
    """
    pkg = Path(package_dir) if package_dir is not None else _RUNNING_PACKAGE
    info = dist_info if dist_info is not None else _dist_info_beside(pkg)
    if info is None:
        if not quick:
            info = _dist_info_on_path()
        if info is None:
            return _report(SOURCE, pkg, f"running from {pkg} (not an installed copy)")

    direct_url = info / "direct_url.json"
    if not direct_url.is_file():
        return _report(NO_LOCAL_SOURCE, pkg, "installed from a package index; no local checkout to compare", info=info)
    try:
        direct = json.loads(direct_url.read_text(encoding="utf-8"))
        if not isinstance(direct, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as exc:
        return _report(
            UNVERIFIABLE, pkg, f"cannot read {direct_url} ({exc}), so this copy cannot be checked", info=info
        )
    url = str(direct.get("url") or "")
    dir_info = direct.get("dir_info")
    source = _path_from_file_url(url)
    if not isinstance(dir_info, dict) or source is None:
        return _report(
            NO_LOCAL_SOURCE, pkg, f"installed from {url or 'an unknown source'}; no local checkout to compare"
        )
    if dir_info.get("editable"):
        return _report(EDITABLE, pkg, f"editable install of {source}", source=source, info=info)
    try:
        beside = info.parent.resolve() == pkg.resolve().parent
    except OSError:
        beside = False
    if not beside:
        return _report(SOURCE, pkg, f"running from {pkg}, not from the copy installed from {source}", source=source)

    tool_prefix = prefix if prefix is not None else Path(sys.prefix)
    source_package = next(
        (cand for cand in (source / "src" / PACKAGE, source / PACKAGE) if (cand / "__init__.py").is_file()), None
    )
    if source_package is None:
        gone = "no longer exists" if not source.is_dir() else f"holds no {PACKAGE} package any more"
        hint = _fix_command(tool_prefix, info, None)
        return _report(
            UNVERIFIABLE,
            pkg,
            f"this copy was built from {source}, which {gone}, so nothing can tell whether it is current; "
            f"reinstall it from where the checkout lives now: {hint}",
            source=source,
            info=info,
            fix=hint,
        )

    installed = _python_files(pkg)
    checkout = _python_files(source_package)
    differing = sorted(
        rel for rel in installed.keys() | checkout.keys() if not _same_content(installed.get(rel), checkout.get(rel))
    )
    split = _split_by_git(source_package, differing, installed) if differing else ([], [])
    stale, uncommitted = split if split is not None else (differing, [])

    if stale:
        fix = _fix_command(tool_prefix, info, source)
        when = _installed_at(info)
        also = f" ({_files(len(uncommitted))} more differ only by uncommitted edits)" if uncommitted else ""
        message = (
            f"this copy{f' (installed {when})' if when else ''} differs from the code committed in {source} in "
            f"{_files(len(stale))}: {_sample(stale)}{also}; run: {fix}"
        )
        return _report(STALE, pkg, message, source=source, info=info, stale=stale, uncommitted=uncommitted, fix=fix)
    if uncommitted:
        message = (
            f"copy of {source}, matches its last commit; this copy lacks uncommitted edits there in "
            f"{_files(len(uncommitted))} ({_sample(uncommitted)}), which make it stale only once committed"
        )
        return _report(UNCOMMITTED, pkg, message, source=source, info=info, uncommitted=uncommitted)
    return _report(FRESH, pkg, f"copy of {source}, matches it ({_files(len(installed))})", source=source, info=info)


def banner_line(report: dict[str, Any]) -> str | None:
    """The one stderr line a command-line run prints, or None when there is nothing to warn about."""
    status = report.get("status")
    if status == STALE:
        when = f" (installed {report['installed_at']})" if report.get("installed_at") else ""
        return (
            f"sovereign-kb: STALE INSTALL: this copy{when} differs from the code committed in {report['source']} "
            f"in {_files(len(report['stale_files']))}; run: {report['fix']}"
        )
    if status == UNVERIFIABLE:
        return f"sovereign-kb: UNVERIFIED INSTALL: {report['message']}"
    return None


def warn_if_stale(stream: TextIO | None = None) -> None:
    """Print the banner for a stale or unverifiable copy. Entry points call it for command-line runs only."""
    try:
        line = banner_line(inspect_install(quick=True))
    except Exception:  # a broken check must never break the command it guards
        return
    if line:
        print(line, file=stream if stream is not None else sys.stderr)


def verify_gate() -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    """Gate 0 of `skb verify`: (report, violations, warnings). Only a stale copy fails the gate."""
    try:
        report = inspect_install()
    except Exception as exc:  # the doctor still runs its other gates
        report = _report(UNVERIFIABLE, _RUNNING_PACKAGE, f"the install check failed ({exc})")
    entry = {"gate": GATE, "file": report["source"] or report["package_dir"], "issue": report["message"]}
    if report["status"] == STALE:
        return report, [entry], []
    if report["status"] in (UNCOMMITTED, UNVERIFIABLE):
        return report, [], [entry]
    return report, [], []


def gate_summary(report: dict[str, Any]) -> str:
    """Result column of the human-readable Gate 0 line."""
    status = report.get("status")
    if status == STALE:
        return f"FAIL (stale copy: {_files(len(report['stale_files']))} behind the checkout's commits)"
    if status == UNCOMMITTED:
        return f"WARN (matches the last commit; {_files(len(report['uncommitted_files']))} uncommitted)"
    if status == UNVERIFIABLE:
        return "WARN (copy cannot be checked)"
    return {
        FRESH: "PASS (copy matches its checkout)",
        EDITABLE: "PASS (editable install)",
        SOURCE: "PASS (running from source)",
    }.get(str(status), "PASS (no local checkout to compare)")


def version_line() -> str:
    """Second line of `skb --version`: how this copy is installed and whether it is current."""
    try:
        report = inspect_install()
    except Exception as exc:
        return f"install: unknown (the install check failed: {exc})"
    label = {STALE: "STALE: ", UNVERIFIABLE: "UNVERIFIED: "}.get(report["status"], "")
    return f"install: {label}{report['message']}"
