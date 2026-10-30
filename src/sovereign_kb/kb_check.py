#!/usr/bin/env python3
"""me-kb episodic & milestone reflection check — deterministic pre-push gate (stdlib only).

Verifies that significant code/architecture milestones being pushed to any repository
are captured in the sovereign knowledge base (~/GitLab/me-kb) before the push completes.

Checked against the exact commits being pushed:
  1. Self-Exemption: pushes to `me-kb` itself are automatically exempt.
  2. Trivial Commits: pure chores/maintenance (e.g. `chore(deps):`, `ci:`, `style:`) or
     commits containing `[skip-kb]` or `[no-kb]` pass with an informational notice.
  3. Milestone Matching: for `feat`, `fix`, `refactor`, `perf`, or significant changes,
     verifies that at least one of the following appears in today's episodic log
     (`me-kb/04-episodic/YYYY-MM-DD-log.md`), yesterday's log, or `01-now/now.md`:
       - The repository name or slug (e.g. `my-service`, `web-platform`, `core-engine`)
       - Any commit SHA (short 7-char or full) being pushed
       - Any ticket/feature reference (e.g. `OP#101`, `PROJ-532`, `FEAT-01`, `ADR-01`)

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
TICKET_RE = re.compile(r"\b(?:OP#\d+|DT-\d+|PV-[A-Z0-9-]+|FEAT-\d+|F-\d+|ISSUE-\d+|[A-Z]{2,}-\d+)\b", re.I)


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


def get_commits_in_range(repo: Path, base: str | None, head: str) -> list[tuple[str, str]]:
    """Returns list of (short_sha, subject) being pushed."""
    if base and base != ZERO_SHA:
        cmd = ["log", "--no-merges", "--oneline", f"{base}..{head}"]
    else:
        # Check commits not in remotes
        unpushed = _git(repo, "rev-list", "--no-merges", "--reverse", head, "--not", "--remotes").stdout.splitlines()
        if unpushed:
            commits = []
            for sha in unpushed:
                subj = _git(repo, "log", "-1", "--format=%s", sha).stdout.strip()
                commits.append((sha[:8], subj))
            return commits
        cmd = ["log", "--no-merges", "-n", "10", "--oneline", head]

    out = _git(repo, *cmd).stdout.strip()
    if not out:
        return []
    res = []
    for line in out.splitlines():
        parts = line.split(" ", 1)
        sha = parts[0]
        subj = parts[1] if len(parts) > 1 else ""
        res.append((sha, subj))
    return res


def check_kb_reflection(
    repo: Path,
    base: str | None,
    head: str,
    vault_path: Path | None = None,
    strict: bool = True,
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
    for sha, subj in commits:
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
    today = datetime.date.today()
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

    # 6. Extract search patterns
    # Primary repo variants: exact name, normalized slugs, and anchor prefix (e.g. "acme-platform" -> "acme")
    full_lower = repo_name.lower()
    repo_variants = {full_lower, full_lower.replace("-", "_"), full_lower.replace("-", " ")}
    parts = full_lower.split("-")
    if (
        len(parts) > 1
        and len(parts[0]) >= 4
        and parts[0] not in GENERIC_STOPWORDS
        and parts[0] not in {"test", "mock", "demo", "temp"}
    ):
        repo_variants.add(parts[0])

    commit_shas = {sha.lower() for sha, _ in commits}
    tickets = set()
    for _, subj in commits:
        for t in TICKET_RE.findall(subj):
            tickets.add(t.upper())

    # 7. Search for matches in episodic logs
    matched_target = None
    matched_file = None
    matched_line_no = None
    matched_line_text = None

    for fpath, text in corpus_sources:
        lines = text.splitlines()
        for idx, line in enumerate(lines, 1):
            line_lower = line.lower()
            line_upper = line.upper()

            # Check repo variants
            for variant in repo_variants:
                # Word-boundary pattern to avoid matching accidental substrings
                pattern = r"(?<![a-zA-Z0-9_-])" + re.escape(variant) + r"(?![a-zA-Z0-9_-])"
                if re.search(pattern, line_lower):
                    matched_target = f"repo '{variant}'"
                    matched_file = fpath
                    matched_line_no = idx
                    matched_line_text = line.strip()
                    break
            if matched_target:
                break

            # Check commit SHAs
            for sha in commit_shas:
                if sha in line_lower:
                    matched_target = f"commit sha '{sha}'"
                    matched_file = fpath
                    matched_line_no = idx
                    matched_line_text = line.strip()
                    break
            if matched_target:
                break

            # Check tickets
            for ticket in tickets:
                if ticket in line_upper:
                    matched_target = f"ticket '{ticket}'"
                    matched_file = fpath
                    matched_line_no = idx
                    matched_line_text = line.strip()
                    break
            if matched_target:
                break
        if matched_target:
            break

    rel_today = today_file.relative_to(vault)
    if matched_target:
        rel_matched = matched_file.relative_to(vault)
        return (
            True,
            f"[kb-check] {repo_name} matched in me-kb ({rel_matched}:{matched_line_no}) PASS",
            [f"Evidence: {matched_line_text[:90]}"],
        )

    # 8. No match found
    commit_summary = [f"  • {sha} {subj}" for sha, subj in commits[:5]]
    if len(commits) > 5:
        commit_summary.append(f"  • ... and {len(commits) - 5} more commit(s)")

    msg = f"[kb-check] {repo_name} FAIL — {len(commits)} commit(s) not reflected in me-kb ({rel_today}):"
    details = [
        *commit_summary,
        f"Neither '{repo_name}' nor commit IDs appear in {today_file.name}.",
        "To satisfy the sovereign reflection gate:",
        f'  1) Capture this milestone: kb capture "{repo_name}: <summary>"',
        f"  2) Or record it in ~/GitLab/me-kb/{rel_today}",
        "Override knowingly if trivial: git push --no-verify or add [skip-kb] to commit message.",
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
