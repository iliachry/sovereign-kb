#!/usr/bin/env python3
"""README.md drift check — deterministic pre-push gate (stdlib only, runnable as a plain script).

Every check is evaluated against the exact commit being pushed (git objects, not the working tree):

  1. References   — every relative Markdown link/image and every repo-relative path written in README.md
                    (`tests/…`, `scripts/…`, `src/…`, `./…`) exists in the pushed commit.
  2. CLI commands — every `<cli> <subcommand>` shown in README shell blocks (CLI names taken from
                    `[project.scripts]` in pyproject.toml) is a real subcommand (`<cli> <sub> --help` exits 0).
  3. Surface drift — CLI commands (Typer/Click/argparse) and HTTP routes (FastAPI) added since the remote
                    base must be mentioned in README.md; removed ones must no longer be advertised.
  4. Test counts  — hard-coded counts such as "168 total tests" or "21/21 pytest" must equal
                    `pytest --collect-only`.

Limitation (by design): it cannot judge whether prose is semantically stale. It only catches structural drift
(dead paths, phantom commands, undocumented new surface, wrong numbers).

Usage:
  readme_check.py [REPO] [--base SHA] [--head SHA] [--no-runtime]
  readme_check.py [REPO] --pre-push        # reads git pre-push stdin: <lref> <lsha> <rref> <rsha>
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

ZERO_SHA = "0" * 40
PATH_PREFIXES = ("tests/", "scripts/", "src/", "benchmarks/", "evidence/", "prototype/", "figures/", "docs/", "slides/")
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
TICK_RE = re.compile(r"`([^`\s]+)`")
FENCE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.S)
TYPER_RE = re.compile(
    r"@(?:app|cli|\w+_app|\w+_cli)\.command\(([^)]*)\)\s*\n(?:\s*@[^\n]*\n)*\s*(?:async\s+)?def\s+(\w+)"
)
ARGPARSE_RE = re.compile(r"add_parser\(\s*[\"']([\w-]+)[\"']")
ROUTE_RE = re.compile(r"@(?:app|router)\.(?:get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']")
README_ROUTE_RE = re.compile(r"(?<![\w.])(/(?:api|v\d|healthz|landing|docs)[A-Za-z0-9_./{}-]*)")
COUNT_RES = (
    re.compile(r"\b(\d+)\s*/\s*(\d+)\s*(?:pytest|tests?)\b", re.I),
    re.compile(r"`?\b(\d+)`?\s+(?:total\s+)?tests\b(?!\s+collected)", re.I),
)
SKIP_CHARS = ("<", ">", "*", "{", "}", "$", "...", "…")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _show(repo: Path, sha: str, path: str) -> str | None:
    r = _git(repo, "show", f"{sha}:{path}")
    return r.stdout if r.returncode == 0 else None


def _tree(repo: Path, sha: str) -> tuple[set[str], set[str]]:
    files = set(_git(repo, "ls-tree", "-r", "--name-only", sha).stdout.splitlines())
    dirs: set[str] = set()
    for f in files:
        parts = f.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return files, dirs


def _exists(path: str, files: set[str], dirs: set[str]) -> bool:
    p = os.path.normpath(path).lstrip("/")
    return p in files or p in dirs or p == "."


# --------------------------------------------------------------------------- 1. references
def reference_issues(readme: str, files: set[str], dirs: set[str]) -> list[str]:
    issues: list[str] = []
    seen: set[str] = set()

    def _check(raw: str, kind: str) -> None:
        target = re.sub(r":\d[\d,-]*$", "", raw.split("#")[0].split("?")[0].rstrip(".,:;"))
        if not target or target in seen or any(c in target for c in SKIP_CHARS):
            return
        seen.add(target)
        if target.startswith("../") or target.startswith("~"):
            return
        if not _exists(target, files, dirs):
            issues.append(f"[ref] {kind} `{raw}` does not exist in this commit")

    for raw in LINK_RE.findall(readme):
        if re.match(r"^[a-zA-Z][\w+.-]*:", raw) or raw.startswith("#"):
            continue  # http:, mailto:, file:, sidecar:, in-page anchors
        _check(raw, "link")
    candidates = list(TICK_RE.findall(readme))
    for lang, block in re.findall(r"```([a-zA-Z]*)\n(.*?)```", readme, flags=re.S):
        if lang.lower() in ("bash", "sh", "shell", "zsh", "console"):  # example output blocks are not checked
            candidates.extend(block.split())
    for tok in candidates:
        tok = tok.strip("\"'()[],;")
        if tok.startswith("./"):
            tok = tok[2:]
        if tok.startswith(PATH_PREFIXES) and not tok.startswith("-"):
            _check(tok, "path")
    return issues


def reference_issues_worktree(root: Path) -> list[str]:
    """Reference check against the working tree (used by the GWI `pv.readme_doc_freshness` probe)."""
    readme_p = root / "README.md"
    if not readme_p.is_file():
        return ["[ref] README.md missing"]
    ls = _git(root, "ls-files") if (root / ".git").exists() else None
    if ls is not None and ls.returncode == 0:
        files = set(ls.stdout.splitlines())
        files |= set(_git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines())
    else:
        files = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}
    dirs: set[str] = set()
    for f in files:
        parts = f.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return reference_issues(readme_p.read_text(encoding="utf-8", errors="ignore"), files, dirs)


# --------------------------------------------------------------------------- 2. CLI commands
def cli_names(repo: Path, sha: str) -> set[str]:
    raw = _show(repo, sha, "pyproject.toml")
    if not raw or tomllib is None:
        return set()
    try:
        return set((tomllib.loads(raw).get("project", {}).get("scripts") or {}).keys())
    except Exception:
        return set()


def advertised_commands(readme: str, clis: set[str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    lines: list[str] = []
    for block in FENCE_RE.findall(readme):
        lines.extend(block.splitlines())
    lines.extend(re.findall(r"`([^`\n]+)`", readme))
    for line in lines:
        toks = line.strip().lstrip("$ ").split()
        while toks and "=" in toks[0] and not toks[0].startswith("-"):
            toks = toks[1:]
        if toks[:2] == ["uv", "run"]:
            toks = toks[2:]
        if len(toks) >= 2 and toks[0] in clis and re.fullmatch(r"[a-z][\w-]*", toks[1]):
            found.add((toks[0], toks[1]))
    return found


def _uv_prefix(repo: Path) -> list[str]:
    base = ["uv", "run", "--quiet", "--directory", str(repo)]
    return base + (["--frozen"] if (repo / "uv.lock").is_file() else [])


def command_issues(repo: Path, cmds: set[tuple[str, str]]) -> list[str]:
    issues: list[str] = []
    for cli, sub in sorted(cmds):
        try:
            r = subprocess.run([*_uv_prefix(repo), cli, sub, "--help"], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(f"[cli] could not probe `{cli} {sub}`: {exc}")
            continue
        if r.returncode != 0:
            issues.append(
                f"[cli] README advertises `{cli} {sub}` but it is not a valid subcommand (exit {r.returncode})"
            )
    return issues


# --------------------------------------------------------------------------- 3. surface drift
def surface(repo: Path, sha: str) -> tuple[set[str], set[str]]:
    cmds: set[str] = set()
    routes: set[str] = set()
    r = _git(
        repo,
        "grep",
        "-l",
        "-E",
        r"\.command\(|add_parser\(|@(app|router)\.(get|post|put|delete|patch)\(",
        sha,
        "--",
        "*.py",
    )
    for line in r.stdout.splitlines():
        path = line.split(":", 1)[1] if ":" in line else line
        if path.startswith("tests/") or "/tests/" in path:
            continue
        src = _show(repo, sha, path) or ""
        for args, fn in TYPER_RE.findall(src):
            a = args.strip()
            m = re.match(r"[\"']([\w-]+)[\"']", a) or re.search(r"\bname\s*=\s*[\"']([\w-]+)[\"']", a)
            cmds.add(m.group(1) if m else fn.replace("_", "-"))
        cmds.update(ARGPARSE_RE.findall(src))
        routes.update(r_ for r_ in ROUTE_RE.findall(src) if r_ != "/")
    return cmds, routes


def _norm_route(r: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", r.rstrip("/"))


def surface_issues(repo: Path, base: str, head: str, readme: str, clis: set[str]) -> list[str]:
    base_c, base_r = surface(repo, base)
    head_c, head_r = surface(repo, head)
    # Root README plus nested component READMEs and docs/*.md guides all count as documentation.
    docs = readme
    files, _ = _tree(repo, head)
    for f in sorted(files):
        if f != "README.md" and (f.endswith("/README.md") or (f.startswith("docs/") and f.endswith(".md"))):
            docs += "\n" + (_show(repo, head, f) or "")
    documented_c = (
        {sub for _, sub in advertised_commands(docs, clis)}
        if clis
        else set(re.findall(r"\b([a-z][\w-]*)\b", docs))
    )
    documented_r = {_norm_route(m) for m in README_ROUTE_RE.findall(docs)}
    issues: list[str] = []
    for c in sorted(head_c - base_c):
        if c not in documented_c:
            issues.append(f"[surface] new CLI command `{c}` added in this push but not shown in README.md")
    for c in sorted(base_c - head_c):
        if c in documented_c:
            issues.append(f"[surface] CLI command `{c}` was removed but README.md still advertises it")
    for r_ in sorted(head_r - base_r):
        if _norm_route(r_) not in documented_r:
            issues.append(f"[surface] new HTTP route `{r_}` added in this push but not documented in README.md")
    for r_ in sorted(base_r - head_r):
        if _norm_route(r_) in documented_r:
            issues.append(f"[surface] HTTP route `{r_}` was removed but README.md still documents it")
    return issues


# --------------------------------------------------------------------------- 4. test counts
def count_claims(readme: str) -> set[int]:
    claims: set[int] = set()
    for rx in COUNT_RES:
        for m in rx.finditer(readme):
            claims.add(int(m.groups()[-1]))
    return claims


def collected_tests(repo: Path) -> int | None:
    if not (repo / "tests").is_dir():
        return None
    if (repo / "pyproject.toml").is_file():
        cmd = [*_uv_prefix(repo), "pytest", "--collect-only", "-q"]
    else:
        cmd = ["uvx", "--quiet", "pytest", "--collect-only", "-q"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=repo)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+) tests? collected", r.stdout) or re.search(r"^(\d+) tests?\b", r.stdout, re.M)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- driver
def check(repo: Path, base: str | None, head: str, runtime: bool = True) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    readme = _show(repo, head, "README.md")
    if readme is None:
        return ["[ref] README.md missing in pushed commit"], notes
    files, dirs = _tree(repo, head)
    issues = reference_issues(readme, files, dirs)
    clis = cli_names(repo, head)

    head_is_worktree = _git(repo, "rev-parse", "HEAD").stdout.strip() == _git(repo, "rev-parse", head).stdout.strip()
    if runtime and head_is_worktree:
        issues += command_issues(repo, advertised_commands(readme, clis))
        claims = count_claims(readme)
        if claims:
            n = collected_tests(repo)
            if n is None:
                notes.append(f"README claims test counts {sorted(claims)} but pytest collection was unavailable")
            else:
                for c in sorted(claims - {n}):
                    issues.append(f"[count] README.md claims {c} tests but pytest collects {n}")
    elif runtime:
        notes.append("runtime checks (CLI --help, pytest count) skipped: pushed commit is not the checked-out HEAD")

    if base and base != ZERO_SHA:
        issues += surface_issues(repo, base, head, readme, clis)
        changed = _git(repo, "diff", "--name-only", base, head).stdout.split()
        if changed and "README.md" not in changed:
            notes.append(
                f"README.md untouched while {len(changed)} file(s) changed — check whether the prose needs updating"
            )
    else:
        notes.append("no remote base: surface-drift check skipped")
    return issues, notes


def _pre_push_ranges(repo: Path, stdin: str) -> list[tuple[str | None, str]]:
    ranges: list[tuple[str | None, str]] = []
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO_SHA:
            continue  # malformed or branch deletion
        lsha, rsha = parts[1], parts[3]
        if rsha == ZERO_SHA:
            first = _git(repo, "rev-list", "--reverse", lsha, "--not", "--remotes").stdout.split()
            rsha = None
            if first:
                parent = _git(repo, "rev-parse", "--verify", "--quiet", f"{first[0]}^")
                rsha = parent.stdout.strip() or None
        ranges.append((rsha, lsha))
    return ranges


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic README.md drift check")
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--pre-push", action="store_true")
    ap.add_argument("--no-runtime", action="store_true")
    args = ap.parse_args(argv)
    repo = Path(_git(Path(args.repo), "rev-parse", "--show-toplevel").stdout.strip() or args.repo)

    if args.pre_push:
        ranges = _pre_push_ranges(repo, sys.stdin.read())
    else:
        base = args.base
        if base is None:
            up = _git(repo, "rev-parse", "--verify", "--quiet", "@{upstream}")
            base = up.stdout.strip() or None
        ranges = [(base, args.head)]

    failed = False
    for base, head in ranges:
        issues, notes = check(repo, base, head, runtime=not args.no_runtime)
        label = f"{repo.name} {(base or 'ROOT')[:8]}..{head[:8]}"
        for n in notes:
            print(f"[readme-check] {label} note: {n}")
        if issues:
            failed = True
            print(f"[readme-check] {label} FAIL — README.md is out of sync:")
            for i in issues:
                print(f"  ✗ {i}")
        else:
            print(f"[readme-check] {label} PASS")
    if failed:
        print(
            "[readme-check] Fix README.md (or the code) and amend before pushing. Override only knowingly: git push --no-verify"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
