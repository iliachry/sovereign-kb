"""F-14: retrieval-tie governance runs on the shared Soar S1 -> S2 deliberation loop (no hand-written S2 rule)."""

from __future__ import annotations

from pathlib import Path

from sovereign_kb.soar import parse_soar, resolve_retrieval_tie_with_soar
from sovereign_kb.soar.vault_agent import _build_dynamic_tie_soar_rules, _is_valid_learned_chunk

THREE = [
    {"rel_path": "05-episodic/log1.md", "doc_type": "episodic", "score": 0.85},
    {"rel_path": "03-life-domains/note2.md", "doc_type": "life-domain", "score": 0.83},
    {"rel_path": "01-identity/sovereign-codex.md", "doc_type": "identity", "score": 0.80},
]
SAME_TIER = [
    {"rel_path": "a.md", "doc_type": "episodic", "score": 0.40},
    {"rel_path": "b.md", "doc_type": "episodic", "score": 0.90},
]


def test_program_has_no_hand_written_s2_rule_and_proposes_every_candidate() -> None:
    src, doc_map, ranks = _build_dynamic_tie_soar_rules(THREE, {"identity": 4, "life-domain": 2, "episodic": 1})
    prods = parse_soar(src)
    assert not any(any(at.attr == "impasse" for c in p.conditions for at in c.attr_tests) for p in prods)
    assert len(doc_map) == 3
    assert min(ranks, key=ranks.__getitem__).endswith("sovereign-codex-md")


def test_all_candidates_tie_in_s2_and_authority_chunk_is_general(tmp_path: Path) -> None:
    res = resolve_retrieval_tie_with_soar(tmp_path, "three", THREE, persist_chunks=True)
    assert res["goal_achieved"] and res["zero_impasse_replay"]
    assert res["first_run_impasses"] == 1
    delib = res["deliberations"][0]
    assert delib["engine"] == "authority"
    assert len(delib["candidates"]) == 3  # 3rd+ candidates compete too
    assert res["reranked_candidates"][0]["rel_path"] == "01-identity/sovereign-codex.md"

    persisted = (tmp_path / ".kb" / "learned_chunks.soar").read_text(encoding="utf-8")
    chunks = parse_soar(persisted)
    assert len(chunks) == 1 and _is_valid_learned_chunk(chunks[0])
    assert "^doc" not in persisted  # generalises over tier/authority, not specific documents

    again = resolve_retrieval_tie_with_soar(tmp_path, "again", THREE, persist_chunks=False)
    assert again["preloaded_chunks"] == 1
    assert again["first_run_impasses"] == 0 and again["goal_achieved"]


def test_equal_authority_uses_score_and_persists_no_unprovable_chunk(tmp_path: Path) -> None:
    res = resolve_retrieval_tie_with_soar(tmp_path, "same", SAME_TIER, persist_chunks=True)
    assert res["goal_achieved"] and res["zero_impasse_replay"]
    assert res["reranked_candidates"][0]["rel_path"] == "b.md"
    # Equal authority gives no provable dominance, so nothing is persisted (the old code inflated authority by 1)
    assert res["chunk_names"] == []
    assert not (tmp_path / ".kb" / "learned_chunks.soar").exists()


def test_identifier_like_ids_stay_constants(tmp_path: Path) -> None:
    cands = [{"id": "A1", "doc_type": "episodic", "score": 0.9}, {"id": "B2", "doc_type": "episodic", "score": 0.1}]
    res = resolve_retrieval_tie_with_soar(tmp_path, "q", cands, persist_chunks=False)
    assert res["goal_achieved"] and res["zero_impasse_replay"]
    assert res["reranked_candidates"][0]["id"] == "A1"
    assert res["selected_doc"] == "A1-doc0"
