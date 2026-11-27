"""Unit and CLI tests for F-24 Sovereign Vault Training & Distillation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sovereign_kb.training import datasets
from sovereign_kb.training.corpus import (
    chunk_markdown_by_headings,
    deterministic_split,
    extract_json_object,
    extract_wikilinks,
    load_chunks,
    load_notes,
    parse_frontmatter,
    rrf_fuse,
    scrub_secrets,
)


def _make_vault(tmp_path: Path) -> Path:
    (tmp_path / "01-now").mkdir(parents=True)
    (tmp_path / "02-operating-system").mkdir(parents=True)
    (tmp_path / "04-episodic").mkdir(parents=True)

    (tmp_path / "01-now" / "now.md").write_text(
        "---\ntitle: Active Focus\ntype: now\ntags: [now]\nrelated:\n  - '[[02-operating-system/gwi]]'\n---\n\n"
        "# Focus\n\nWorking with [[02-operating-system/laws|Laws]] and `192.168.1.10` or user@example.org.\n\n"
        "### Somatic, Medical Baseline\nPrivate medical note excluded in public-safe.\n\n"
        "## Architecture\nDeterministic systems engineering.\n",
        encoding="utf-8",
    )
    (tmp_path / "02-operating-system" / "gwi.md").write_text(
        "---\ntitle: Goldwood Index\ntype: operating-system\n---\n\n# GWI\n\n"
        + " ".join(["GWI gate enforces G >= 9.0 across all lenses."] * 8),
        encoding="utf-8",
    )
    (tmp_path / "02-operating-system" / "laws.md").write_text(
        "---\ntitle: Subtractive Laws\ntype: operating-system\n---\n\n# Laws\n\n"
        + " ".join(["Law L1 enforces single-row <= 56px navigation."] * 8),
        encoding="utf-8",
    )
    (tmp_path / "04-episodic" / "2026-11-27-sample-audit.md").write_text(
        "# Sample Audit\nToken ghp_abcdefghijklmnop123456 scrubbed. "
        + " ".join(["Verified zero P0 and zero P1 defects."] * 10),
        encoding="utf-8",
    )
    (tmp_path / "04-episodic" / "private-notes.md").write_text(
        "# Private\nShould be excluded from public-safe.\n",
        encoding="utf-8",
    )
    for i in range(4):
        (tmp_path / "02-operating-system" / f"topic_{i}.md").write_text(
            f"---\ntitle: Topic {i}\n---\n# Topic {i}\n" + " ".join([f"topic_{i}_term_{j}" for j in range(25)]),
            encoding="utf-8",
        )
    return tmp_path


def test_frontmatter_wikilinks_and_scrubbing() -> None:
    fm, body = parse_frontmatter("---\ntitle: Demo\ntags: [a, b]\n---\nLink [[foo/bar|Baz]] and [[baz#sec]].")
    assert fm["title"] == "Demo"
    assert fm["tags"] == ["a", "b"]
    assert extract_wikilinks(body) == ("foo/bar", "baz")

    cleaned = scrub_secrets("mail me@demo.org token ghp_1234567890abcdef1234")
    assert "me@demo.org" not in cleaned
    assert "ghp_1234567890abcdef1234" not in cleaned


def test_public_safe_privacy_and_split(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    notes = load_notes(vault, scope="public-safe")
    rels = {n.rel_path for n in notes}
    assert "04-episodic/private-notes.md" not in rels
    now = next(n for n in notes if n.rel_path == "01-now/now.md")
    assert "Private medical note" not in now.body
    assert "user@example.org" not in now.body
    assert "192.168.1.10" not in now.body
    assert "Deterministic systems engineering" in now.body

    chunks = load_chunks(vault, scope="public-safe", max_words=20, overlap_words=5)
    by_file: dict[str, set[str]] = {}
    for ch in chunks:
        by_file.setdefault(ch.rel_path, set()).add(ch.split)
        assert ch.split == deterministic_split(ch.rel_path)
    assert all(len(s) == 1 for s in by_file.values())


def test_chunking_json_and_rrf() -> None:
    chunks = chunk_markdown_by_headings("# A\n" + " ".join(["w"] * 90), title="Doc", max_words=40, overlap_words=8)
    assert len(chunks) >= 3
    assert all(len(txt.split()) <= 40 for _, txt in chunks)

    parsed = extract_json_object('prefix {"verdict": "APPROVED", "gwi_score_est": 9.4} suffix')
    assert parsed is not None and parsed["verdict"] == "APPROVED"
    assert extract_json_object("no json") is None

    fused = rrf_fuse([["a.md", "b.md"], ["b.md", "c.md"]])
    assert fused[0][0] == "b.md"


def test_dataset_builders_and_cli(tmp_path: Path, monkeypatch) -> None:
    vault = _make_vault(tmp_path / "vault")
    out_dir = tmp_path / "out"

    ret = datasets.write_retrieval_datasets(vault, out_dir, scope="public-safe")
    assert ret["train"] >= 3
    lm = datasets.write_nanolm_corpus(vault, out_dir, scope="public-safe")
    assert lm["train"]["docs"] >= 3

    def fake_teacher(_api_base, _model, _api_key, sys_prompt, _user_prompt, **_kw):
        if "critique evaluation" in sys_prompt:
            return [
                {
                    "prompt": "Review top bar layout against Law L1.",
                    "chosen": json.dumps(
                        {
                            "verdict": "CHANGES_REQUESTED",
                            "gwi_score_est": 7.5,
                            "p0_findings": [],
                            "p1_findings": ["Two-row bar"],
                            "violated_laws": ["L1"],
                            "concise_remediation": "Collapse to 1 row <=56px.",
                        }
                    ),
                    "rejected": "Looks fine.",
                },
                {
                    "prompt": "Invalid schema should be dropped.",
                    "chosen": json.dumps({"verdict": "OK"}),
                    "rejected": "No.",
                },
            ]
        return [{"user": "What is the GWI gate?", "assistant": "G >= 9.0 with zero P0 defects across all lenses."}]

    monkeypatch.setattr(datasets, "_call_teacher_json", fake_teacher)
    c_counts = datasets.build_critic_dataset(vault, out_dir, max_windows=10)
    t_counts = datasets.build_twin_dataset(vault, out_dir, max_chunks=10)
    assert c_counts["train"] + c_counts["val"] >= 1
    assert t_counts["train"] + t_counts["val"] >= 1
    assert not any("Invalid schema" in ln for ln in (out_dir / "critic_train.jsonl").read_text().splitlines())

    proc = subprocess.run(
        [sys.executable, "-m", "sovereign_kb.cli", "--vault", str(vault), "train", "datasets", "--track", "retrieval"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"track": "retrieval"' in proc.stdout
