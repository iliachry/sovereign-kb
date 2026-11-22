"""Regression tests for kb_check / readme_check pre-push gates and RSI rollback safety.

Every test builds throwaway git repos (plus bare remotes populated via `git clone --bare` + `git fetch`, never
`git push`) under tmp_path and calls the gate functions directly. The me-kb vault is always a tmp_path fake.
"""

from __future__ import annotations

import datetime
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sovereign_kb import kb_check, readme_check, rsi

TODAY = datetime.date(2026, 1, 15)
MISSING_SHA = "1234567890abcdef1234567890abcdef12345678"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, name: str, content: str, msg: str) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _init(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.hooksPath", "/dev/null")
    return repo


def _publish(repo: Path, tmp_path: Path) -> Path:
    """Mirror the current repo into a bare remote and fetch it back, so refs/remotes/origin/* exist."""
    remote = tmp_path / f"{repo.name}-remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(remote)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "fetch", "-q", "origin")
    return remote


def _vault(tmp_path: Path, log: str = "# log\n") -> Path:
    vault = tmp_path / "fake-me-kb"
    (vault / "04-episodic").mkdir(parents=True)
    (vault / "01-now").mkdir()
    (vault / "04-episodic" / f"{TODAY.isoformat()}-log.md").write_text(log, encoding="utf-8")
    return vault


def _check(repo: Path, base: str | None, head: str, vault: Path) -> tuple[bool, str, list[str]]:
    return kb_check.check_kb_reflection(repo, base, head, vault_path=vault, strict=True, today=TODAY)


# --------------------------------------------------------------------------- kb_check
def test_kb_new_branch_with_only_already_pushed_commits_passes(tmp_path: Path) -> None:
    repo = _init(tmp_path, "widget-svc")
    head = _commit(repo, "a.py", "x=1\n", "feat: already shipped")
    _publish(repo, tmp_path)
    _git(repo, "checkout", "-qb", "feature")
    vault = _vault(tmp_path)

    ranges = kb_check._pre_push_ranges(repo, f"refs/heads/feature {head} refs/heads/feature {'0' * 40}\n")
    assert ranges == [(None, head)]
    assert kb_check.get_commits_in_range(repo, None, head) == []
    passed, msg, _ = _check(repo, None, head, vault)
    assert passed and "zero unpushed" in msg

    # A genuinely new commit on the new branch is still gated.
    new = _commit(repo, "b.py", "y=1\n", "feat: new thing")
    ((base, h),) = kb_check._pre_push_ranges(repo, f"refs/heads/feature {new} refs/heads/feature {'0' * 40}\n")
    assert [s for s, _ in kb_check.get_commits_in_range(repo, base, h)] == [new]
    assert not _check(repo, base, h, vault)[0]


def test_kb_seven_char_short_sha_in_log_matches(tmp_path: Path) -> None:
    repo = _init(tmp_path, "widget-svc")
    _commit(repo, "a.py", "x=1\n", "chore: init")
    _publish(repo, tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "b.py", "y=1\n", "feat: unlogged")
    short7 = _git(repo, "rev-parse", "--short=7", head)

    commits = kb_check.get_commits_in_range(repo, None, head)
    assert commits == [(head, "feat: unlogged")]  # full SHA kept

    assert not _check(repo, None, head, _vault(tmp_path / "v0"))[0]
    for b in (None, base):
        vault = _vault(tmp_path / f"v-{b is None}", f"- shipped `{short7}` today\n")
        passed, msg, _ = _check(repo, b, head, vault)
        assert passed, msg
    # A 7-char token that is not a prefix of a pushed SHA does not count.
    other = "0000000" if not head.startswith("0000000") else "fffffff"
    assert not _check(repo, base, head, _vault(tmp_path / "v2", f"- {other}\n"))[0]


def test_kb_missing_remote_sha_falls_back_to_remotes_range(tmp_path: Path) -> None:
    repo = _init(tmp_path, "widget-svc")
    _commit(repo, "a.py", "x=1\n", "feat: base")
    _publish(repo, tmp_path)
    head = _commit(repo, "b.py", "y=1\n", "feat: after unfetched force-push")
    vault = _vault(tmp_path)

    ((base, h),) = kb_check._pre_push_ranges(repo, f"refs/heads/main {head} refs/heads/main {MISSING_SHA}\n")
    assert base != MISSING_SHA
    assert [s for s, _ in kb_check.get_commits_in_range(repo, MISSING_SHA, head)] == [head]
    passed, msg, _ = _check(repo, MISSING_SHA, head, vault)
    assert not passed and "FAIL" in msg  # previously passed vacuously on an empty range


def test_kb_bare_repo_prefix_no_longer_satisfies_gate(tmp_path: Path) -> None:
    repo = _init(tmp_path, "sovereign-widget")
    head = _commit(repo, "a.py", "x=1\n", "feat: thing")
    unrelated = _vault(tmp_path / "a", "- sovereign cloud musings, nothing about this repo\n")
    assert not _check(repo, None, head, unrelated)[0]
    # Even the full repo name is only a hint now; the pushed SHA is the evidence.
    name_only = _vault(tmp_path / "b", "- shipped sovereign-widget v1\n")
    assert not _check(repo, None, head, name_only)[0]
    logged = _vault(tmp_path / "c", f"- shipped sovereign-widget v1 ({head[:7]})\n")
    assert _check(repo, None, head, logged)[0]


def test_kb_ticket_or_earlier_unrelated_mention_is_not_evidence(tmp_path: Path) -> None:
    repo = _init(tmp_path, "ticket-svc")
    head = _commit(repo, "a.py", "x=1\n", "feat(F-20): elenchus gate")
    # Ticket id and repo name from an earlier, unrelated entry the same day do not satisfy the gate.
    vault = _vault(tmp_path / "v", "- 09:00 ticket-svc: stale-work scan, F-20 still open\n")
    passed, _, details = _check(repo, None, head, vault)
    assert not passed
    assert any(head[:7] in d for d in details)


# --------------------------------------------------------------------------- readme_check
def test_readme_missing_base_does_not_treat_everything_as_new(tmp_path: Path) -> None:
    repo = _init(tmp_path, "svc")
    _commit(repo, "README.md", "# svc\n", "docs")
    _commit(repo, "srv.py", '@app.get("/legacy")\ndef l():\n    pass\n', "feat: legacy (undocumented, pre-existing)")
    _publish(repo, tmp_path)
    _commit(repo, "README.md", "# svc\n\nEndpoints: `GET /search` returns hits.\n", "docs: search")
    head = _commit(
        repo, "srv.py", '@app.get("/legacy")\ndef l():\n    pass\n@app.get("/search")\ndef s():\n    pass\n', "feat"
    )

    issues, notes = readme_check.check(repo, MISSING_SHA, head, runtime=False)
    assert issues == [], issues
    assert not any("no remote base" in n for n in notes)
    ((b, h),) = readme_check._pre_push_ranges(repo, f"refs/heads/main {head} refs/heads/main {MISSING_SHA}\n")
    assert b == _git(repo, "rev-parse", "origin/main") and h == head


def test_readme_unprefixed_route_documented_literally(tmp_path: Path) -> None:
    repo = _init(tmp_path, "svc")
    base = _commit(repo, "README.md", "# svc\n", "docs")
    _commit(
        repo,
        "srv.py",
        '@app.get("/search")\ndef s():\n    pass\n@app.get("/items/{item_id}")\ndef i(item_id):\n    pass\n',
        "feat",
    )
    head = _commit(repo, "README.md", "# svc\n\n`GET /searches` and `GET /items/:id`.\n", "docs")
    issues, _ = readme_check.check(repo, base, head, runtime=False)
    assert issues == ["[surface] new HTTP route `/search` added in this push but not documented in README.md"]

    head = _commit(repo, "README.md", "# svc\n\n`GET /search` and `GET /items/<id>`.\n", "docs")
    issues, _ = readme_check.check(repo, base, head, runtime=False)
    assert issues == []


# --------------------------------------------------------------------------- rsi
def _stub_heal_fns() -> dict[str, Any]:
    return {
        "list_md_fn": lambda v: sorted(p for p in v.rglob("*.md") if ".kb" not in p.parts),
        "parse_fm_fn": lambda text: {},
        "extract_links_fn": lambda text, lookup=None: [],
        "build_stem_fn": lambda v, files=None: {},
        "sync_fn": lambda ns: None,
    }


def test_rsi_rollback_only_restores_files_untouched_since_consolidation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "03-domains").mkdir(parents=True)
    a, b = vault / "03-domains" / "a.md", vault / "03-domains" / "b.md"
    a.write_text("# A\nbody a\n", encoding="utf-8")
    b.write_text("# B\nbody b\n", encoding="utf-8")

    res = rsi.heal_and_consolidate_vault(vault_dir=vault, consolidate_episodic=False, **_stub_heal_fns())
    snaps = res["snapshots"]
    assert {a, b, vault / "INDEX.md"} <= set(snaps.written)
    b.write_text("USER EDIT during cycle\n", encoding="utf-8")

    restored, skipped = rsi._rollback_consolidation(snaps)
    assert a.read_text(encoding="utf-8") == "# A\nbody a\n"
    assert b.read_text(encoding="utf-8") == "USER EDIT during cycle\n"
    assert skipped == [str(b)]
    assert not (vault / "INDEX.md").exists()  # created by consolidation, untouched -> removed
    assert str(a) in restored


def _eval(j: float) -> dict[str, Any]:
    return {
        "objective_j": j,
        "mrr_at_5": 0.5,
        "ndcg_at_5": 0.5,
        "recall_at_1": 0.5,
        "ledger_intrusions": 0,
        "probes_count": 1,
        "misses": [],
    }


@pytest.fixture
def rsi_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import sovereign_kb.soar as soar_pkg

    vault = tmp_path / "vault"
    (vault / ".kb").mkdir(parents=True)

    def get_db(_v: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE documents (id INTEGER)")
        conn.execute("INSERT INTO documents VALUES (1)")
        return conn

    probes = [{"query": "q", "expected": "x.md"}]
    monkeypatch.setattr(rsi, "build_benchmark_suite", lambda conn, v: probes)
    monkeypatch.setattr(rsi, "split_benchmark_suite", lambda conn, v, probes=None: (probes, []))
    monkeypatch.setattr(rsi, "invoke_hybrid_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(
        soar_pkg,
        "resolve_retrieval_tie_with_soar",
        lambda **k: {"chunks_compiled": 0, "zero_impasse_replay": True},
    )
    base_policy = rsi.load_retrieval_policy(vault)
    monkeypatch.setattr(
        rsi,
        "tune_retrieval_policy",
        lambda conn, v, fn: {
            "policy": base_policy,
            "policy_sha256": rsi.compute_policy_sha256(base_policy),
            "updated": False,
            "candidates_evaluated": 0,
        },
    )

    def run(heal, js: list[float]) -> dict[str, Any]:
        seq = iter(js)
        monkeypatch.setattr(rsi, "evaluate_retrieval_benchmark", lambda *a, **k: _eval(next(seq)))
        monkeypatch.setattr(rsi, "heal_and_consolidate_vault", heal)
        return rsi.run_rsi_cycle(
            vault_dir=vault,
            get_db_fn=get_db,
            hybrid_retrieve_fn=lambda *a, **k: [],
            sync_fn=lambda ns: None,
            list_md_fn=lambda v: [],
            parse_fm_fn=lambda t: {},
            extract_links_fn=lambda t, lk=None: [],
            build_stem_fn=lambda v, f=None: {},
        )

    return vault, run


def _heal_result(snaps: dict) -> dict[str, Any]:
    return {
        "healed_frontmatter": [],
        "healed_orphans": [],
        "distilled_entries": 0,
        "rejected_noise_bullets": 0,
        "snapshots": snaps,
    }


def test_rsi_heal_tolerance_strict_when_no_files_written(rsi_harness) -> None:
    vault, run = rsi_harness
    # Snapshots contain the distill/INDEX keys but nothing was written: a -1e-4 heal dip must roll back.
    idle = rsi._SnapshotMap({vault / "INDEX.md": None, vault / "02-operating-system/x.md": None})
    rep = run(lambda **k: _heal_result(idle), [1.0, 0.9999, 0.9999])
    assert rep["status"] == "ROLLED_BACK_ON_REGRESSION"

    # When consolidation really wrote a file, the same dip is within the heal tolerance.
    note = vault / "n.md"
    note.write_text("healed\n", encoding="utf-8")
    busy = rsi._SnapshotMap({note: "orig\n"})
    busy.written[note] = "healed\n"
    rep = run(lambda **k: _heal_result(busy), [1.0, 0.9999, 0.9999])
    assert rep["status"] != "ROLLED_BACK_ON_REGRESSION"
    assert note.read_text(encoding="utf-8") == "healed\n"


def test_rsi_heal_tolerance_also_covers_heldout_dip(rsi_harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for 0c5f1d5: the heal tolerance applies to heldout_delta_j, not only delta_j_heal."""
    vault, run = rsi_harness
    held = [{"query": "h", "expected": "y.md"}]
    monkeypatch.setattr(rsi, "split_benchmark_suite", lambda conn, v, probes=None: (probes, held))
    note = vault / "n.md"
    note.write_text("healed\n", encoding="utf-8")
    busy = rsi._SnapshotMap({note: "orig\n"})
    busy.written[note] = "healed\n"
    # before, before_heldout, after_heal, after, after_heldout: only the held-out score dips by 1e-4.
    rep = run(lambda **k: _heal_result(busy), [1.0, 1.0, 1.0, 1.0, 0.9999])
    assert rep["status"] != "ROLLED_BACK_ON_REGRESSION"

    idle = rsi._SnapshotMap({vault / "INDEX.md": None})
    rep = run(lambda **k: _heal_result(idle), [1.0, 1.0, 1.0, 1.0, 0.9999])
    assert rep["status"] == "ROLLED_BACK_ON_REGRESSION"


def test_rsi_cycle_rollback_preserves_concurrent_user_edit(rsi_harness) -> None:
    vault, run = rsi_harness
    mine, theirs = vault / "mine.md", vault / "theirs.md"
    mine.write_text("healed\n", encoding="utf-8")
    theirs.write_text("user edited mid-cycle\n", encoding="utf-8")
    snaps = rsi._SnapshotMap({mine: "orig mine\n", theirs: "orig theirs\n"})
    snaps.written.update({mine: "healed\n", theirs: "healed theirs\n"})
    rep = run(lambda **k: _heal_result(snaps), [1.0, 1.0, 0.5])  # tune regresses -> rollback
    assert rep["status"] == "ROLLED_BACK_ON_REGRESSION"
    assert mine.read_text(encoding="utf-8") == "orig mine\n"
    assert theirs.read_text(encoding="utf-8") == "user edited mid-cycle\n"
    assert rep["consolidation"]["rollback_skipped"] == [str(theirs)]
