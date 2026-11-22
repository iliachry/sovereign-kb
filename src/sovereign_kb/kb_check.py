#!/usr/bin/env python3
"""me-kb episodic & milestone reflection check — deterministic pre-push gate (stdlib only).

Verifies that significant code/architecture milestones being pushed to any repository
are captured in the sovereign knowledge base (~/GitLab/me-kb) before the push completes.

Checked against the exact commits being pushed:
  1. Self-Exemption: pushes to `me-kb` itself are automatically exempt.
  2. Trivial Commits: pure chores/maintenance (e.g. `chore(deps):`, `ci:`, `style:`) or
     commits containing `[skip-kb]` or `[no-kb]` pass with an informational notice.
  3. Milestone Matching: for `feat`, `fix`, `refactor`, `perf`, or significant changes,
     verifies that the SHA (>= 7 hex chars, e.g. `git log --format=%h`) of at least one commit
     being pushed appears in today's episodic log (`me-kb/04-episodic/YYYY-MM-DD-log.md`),
     yesterday's log, or `01-now/now.md`.
     The repository name or a ticket reference (`OP#101`, `FEAT-01`) alone is NOT evidence:
     both recur across many pushes, so any earlier same-day mention satisfied the gate without
     this push ever being logged. Workflow: commit -> write the SHA into the log -> push.

Usage:
  kb_check.py [REPO] [--base SHA] [--head SHA] [--vault PATH]
  kb_check.py [REPO] --pre-push        # reads git pre-push stdin: <lref> <lsha> <rref> <rsha>
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

ZERO_SHA = "0" * 40

GENERIC_STOPWORDS = {
    "test",
    "tests",
    "repo",
    "app",
    "apps",
    "dev",
    "api",
    "monorepo",
    "frontend",
    "backend",
    "web",
    "main",
    "core",
    "client",
    "server",
    "data",
    "layer",
    "testing",
    "service",
    "services",
    "system",
    "engine",
    "docs",
    "doc",
    "platform",
    "src",
}
TRIVIAL_PREFIXES = (
    "chore(deps)",
    "chore(lint)",
    "chore(format)",
    "style:",
    "style(",
    "ci:",
    "ci(",
    "build(deps)",
    "test(typo)",
    "docs(typo)",
)

SKIP_MARKERS = ("[skip-kb]", "[no-kb]", "[kb-skip]", "--skip-kb")
HEX_TOKEN_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _locate_vault(override_path: str | None = None) -> Path | None:
    candidates = []
    if override_path:
        candidates.append(Path(override_path).expanduser())
    for env_var in ("ME_KB_VAULT", "KB_VAULT", "VAULT_DIR"):
        val = os.environ.get(env_var)
        if val:
            candidates.append(Path(val).expanduser())
    candidates.extend(
        [
            Path.home() / "GitLab" / "me-kb",
            Path.home() / "me-kb",
        ]
    )
    for c in candidates:
        if (c / "04-episodic").is_dir():
            return c.resolve()
    return None


def _has_commit(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _base_from_remotes(repo: Path, head: str) -> str | None:
    """Parent of the first commit reachable from `head` but from no remote-tracking ref (None if none/root)."""
    first = _git(repo, "rev-list", "--reverse", head, "--not", "--remotes").stdout.split()
    if not first:
        return None
    return _git(repo, "rev-parse", "--verify", "--quiet", f"{first[0]}^").stdout.strip() or None


def _pre_push_ranges(repo: Path, stdin: str) -> list[tuple[str | None, str]]:
    ranges: list[tuple[str | None, str]] = []
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO_SHA:
            continue  # malformed or branch deletion
        lsha, rsha = parts[1], parts[3]
        if rsha == ZERO_SHA or not _has_commit(repo, rsha):
            # New branch, or remote tip not present locally (e.g. unfetched force-push): use the
            # `--not --remotes` range; get_commits_in_range re-derives it when this is None.
            rsha = _base_from_remotes(repo, lsha)
        ranges.append((rsha, lsha))
    return ranges


def get_commits_in_range(repo: Path, base: str | None, head: str) -> list[tuple[str, str]]:
    """Returns list of (full_sha, subject) being pushed.

    With no usable base (None, zero SHA, or a SHA absent from the local object store), the range is
    every commit reachable from `head` but from no remote-tracking ref. If that is empty, everything is
    already on a remote and nothing is being pushed (no `log -n 10` fallback).
    """
    if base and base != ZERO_SHA and _has_commit(repo, base):
        shas = _git(repo, "rev-list", "--no-merges", "--reverse", f"{base}..{head}").stdout.split()
    else:
        shas = _git(repo, "rev-list", "--no-merges", "--reverse", head, "--not", "--remotes").stdout.split()
    commits = []
    for sha in shas:
        subj = _git(repo, "log", "-1", "--format=%s", sha).stdout.strip()
        commits.append((sha.lower(), subj))
    return commits


def check_kb_reflection(
    repo: Path,
    base: str | None,
    head: str,
    vault_path: Path | None = None,
    strict: bool = True,
    today: datetime.date | None = None,
) -> tuple[bool, str, list[str]]:
    """
    Checks if the commits in base..head are reflected in me-kb.
    Returns: (passed: bool, message: str, details: list[str])
    """
    repo_name = repo.name

    # 1. Exempt self (me-kb)
    if repo_name == "me-kb" or (repo / ".kb" / "index.db").is_file():
        return True, f"[kb-check] {repo_name} is the sovereign knowledge base itself (self-exempt)", []

    # 2. Extract commits being pushed
    commits = get_commits_in_range(repo, base, head)
    if not commits:
        return True, f"[kb-check] {repo_name}: zero unpushed commits in range", []

    # 3. Check for skip markers or pure trivial chores
    all_skip = True
    all_trivial = True
    for _sha, subj in commits:
        subj_lower = subj.lower()
        if not any(marker in subj_lower for marker in SKIP_MARKERS):
            all_skip = False
        if not any(subj_lower.startswith(pref) for pref in TRIVIAL_PREFIXES):
            all_trivial = False

    if all_skip:
        return True, f"[kb-check] {repo_name}: skip marker detected in commit message(s) (exempt)", []
    if all_trivial:
        return True, f"[kb-check] {repo_name}: {len(commits)} maintenance commit(s) (chore/ci/style) (exempt)", []

    # 4. Locate me-kb vault
    vault = _locate_vault(str(vault_path) if vault_path else None)
    if not vault:
        # Vault not found on this machine — warn but do not hard-block
        return True, f"[kb-check] {repo_name}: me-kb vault not found on local system (check bypassed)", []

    # 5. Load today's log, yesterday's log, and now.md
    today = today or datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    today_file = vault / "04-episodic" / f"{today.isoformat()}-log.md"
    yesterday_file = vault / "04-episodic" / f"{yesterday.isoformat()}-log.md"
    now_file = vault / "01-now" / "now.md"

    corpus_sources: list[tuple[Path, str]] = []
    for f in (today_file, yesterday_file, now_file):
        if f.is_file():
            try:
                corpus_sources.append((f, f.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass

    if not corpus_sources:
        return (
            not strict,
            f"[kb-check] {repo_name}: no recent episodic log or now.md found in {vault}",
            [f"Expected today's log at {today_file}"],
        )

    # 6. Evidence = a SHA prefix (>= 7 hex chars) of a commit being pushed. Repo-name and ticket
    # mentions are reported as hints only — they recur across pushes and proved nothing.
    commit_shas = {sha.lower() for sha, _ in commits}
    full_lower = repo_name.lower()
    repo_variants = {full_lower, full_lower.replace("-", "_"), full_lower.replace("-", " ")}
    repo_patterns = [re.compile(r"(?<![a-zA-Z0-9_-])" + re.escape(v) + r"(?![a-zA-Z0-9_-])") for v in repo_variants]

    # 7. Search for a pushed-commit SHA in the episodic corpus
    matched: tuple[Path, int, str, str] | None = None
    repo_hint: tuple[Path, int] | None = None
    for fpath, text in corpus_sources:
        for idx, line in enumerate(text.splitlines(), 1):
            line_lower = line.lower()
            for tok in HEX_TOKEN_RE.findall(line_lower):
                if any(s_.startswith(tok) for s_ in commit_shas):
                    matched = (fpath, idx, tok, line.strip())
                    break
            if matched:
                break
            if repo_hint is None and any(p.search(line_lower) for p in repo_patterns):
                repo_hint = (fpath, idx)
        if matched:
            break

    rel_today = today_file.relative_to(vault)
    if matched:
        fpath, line_no, tok, line_text = matched
        return (
            True,
            f"[kb-check] {repo_name} commit {tok} logged in me-kb ({fpath.relative_to(vault)}:{line_no}) PASS",
            [f"Evidence: {line_text[:90]}"],
        )

    # 8. No match found
    commit_summary = [f"  • {sha[:8]} {subj}" for sha, subj in commits[:5]]
    if len(commits) > 5:
        commit_summary.append(f"  • ... and {len(commits) - 5} more commit(s)")

    msg = f"[kb-check] {repo_name} FAIL — {len(commits)} commit(s) not reflected in me-kb ({rel_today}):"
    head_short = commits[-1][0][:7]  # tip of the push (rev-list is --reverse)
    details = [
        *commit_summary,
        f"No SHA of the commit(s) being pushed appears in {today_file.name}, yesterday's log or now.md.",
    ]
    if repo_hint is not None:
        details.append(
            f"('{repo_name}' is mentioned at {repo_hint[0].relative_to(vault)}:{repo_hint[1]}, "
            "but a repo-name mention is not evidence that this push was logged.)"
        )
    details += [
        "To satisfy the sovereign reflection gate, log the SHA you are pushing:",
        f'  1) kb capture "{repo_name} {head_short}: <summary>"',
        f"  2) Or write `{head_short}` into ~/GitLab/me-kb/{rel_today}",
        "Override knowingly if trivial: add [skip-kb] to the commit message.",
    ]
    return False, msg, details


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic me-kb pre-push reflection check")
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--vault")
    ap.add_argument("--pre-push", action="store_true")
    ap.add_argument("--warn-only", action="store_true")
    args = ap.parse_args(argv)

    repo_dir = _git(Path(args.repo), "rev-parse", "--show-toplevel").stdout.strip() or args.repo
    repo = Path(repo_dir)

    strict = not args.warn_only and os.environ.get("KB_CHECK_WARN_ONLY") != "1"

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
        passed, msg, details = check_kb_reflection(
            repo,
            base,
            head,
            vault_path=Path(args.vault) if args.vault else None,
            strict=strict,
        )
        print(msg)
        for d in details:
            print(f"  {d}")
        if not passed:
            failed = True

    return 1 if (failed and strict) else 0


if __name__ == "__main__":
    sys.exit(main())
