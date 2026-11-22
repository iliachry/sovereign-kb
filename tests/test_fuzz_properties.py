"""Property-based fuzz tests (hypothesis) for verdict parsers, Elenchus synthesis, and `.evr` round-trips.

All runs are derandomized so CI results are reproducible; tests skip cleanly when hypothesis is absent.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from sovereign_kb.elenchus import (  # noqa: E402
    compute_effective_panel_size,
    parse_reviewer_output,
    synthesize_elenchus_verdict,
)
from sovereign_kb.evr import EvrBuilder, EvrCodec, EvrReader  # noqa: E402
from sovereign_kb.video_playback import parse_review_verdict  # noqa: E402

FUZZ = settings(
    derandomize=True,
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
FUZZ_SLOW = FUZZ

# ---------------------------------------------------------------------------
# video_playback.parse_review_verdict
# ---------------------------------------------------------------------------

# An "anchored approval line": a `Verdict: APPROVED` line or a line that is exactly APPROVED [FOR RELEASE].
_ANCHORED_APPROVAL_RE = re.compile(
    r"^\s*\**\s*(?:(?:final\s+)?verdict\s*\**\s*[:\-]\s*\**\s*APPROVED"
    r"|APPROVED(?:\s+FOR\s+RELEASE)?\s*\**\s*\.?\s*$)",
    re.IGNORECASE | re.MULTILINE,
)

_prose_line = st.one_of(
    st.text(max_size=60),
    st.sampled_from(
        [
            "This cannot be approved.",
            "Not APPROVED yet",
            "approved with caveats",
            "Verdict: REVISE",
            "Verdict: APPROVED",
            "**Verdict**: APPROVED",
            "APPROVED",
            "  approved for release.",
            "Final verdict - REJECTED",
            "verdict: approved-ish",
            "The reviewer APPROVED nothing",
        ]
    ),
)
_review_text = st.lists(_prose_line, max_size=8).map("\n".join)


@FUZZ
@given(st.text())
def test_video_verdict_arbitrary_unicode_never_raises(text: str) -> None:
    assert parse_review_verdict(text) in ("APPROVED", "REVISE")


@FUZZ
@given(_review_text)
def test_video_verdict_approval_requires_anchored_line(text: str) -> None:
    if parse_review_verdict(text) == "APPROVED":
        assert _ANCHORED_APPROVAL_RE.search(text), text


@FUZZ
@given(_review_text, st.sampled_from(["Verdict: REVISE", "verdict - revise", "**Final Verdict:** REVISE"]))
def test_video_verdict_trailing_revise_wins(text: str, revise_line: str) -> None:
    assert parse_review_verdict(f"{text}\n{revise_line}") == "REVISE"


# ---------------------------------------------------------------------------
# elenchus reviewer parsing / synthesis / panel size
# ---------------------------------------------------------------------------

_LLM_MODELS = [
    "amazon-bedrock/global.anthropic.claude-fable-5-1",
    "amazon-bedrock/global.openai.gpt-6-astra",
    "qwen3.6-35b-a3b",
    "gemini-2.5-pro",
    "deepseek-r2",
    "mistral-large",
    "meta.llama4",
]
_ANY_MODELS = [*_LLM_MODELS, "skb-elenchus-local-deterministic", "mystery-model-x", ""]

_p0_line = st.builds(
    lambda pfx, claim: f"{pfx}{claim}",
    st.sampled_from(["- [P0] ", "- **P0**: ", "* P0 — ", "P0: ", "- P0 - "]),
    st.sampled_from(
        [
            "SQL injection in src/db.py query builder",
            "data loss when cache flush races shutdown",
            "signature check is skipped for empty manifests",
        ]
    ),
)
_reviewer_noise = st.lists(
    st.one_of(
        st.text(max_size=40),
        st.sampled_from(
            ["APPROVE", "Verdict: APPROVE", "LGTM, approve.", "## Findings", "Overall: ship it", "```", "> quoted"]
        ),
    ),
    max_size=5,
)


@FUZZ
@given(_reviewer_noise, _p0_line, _reviewer_noise)
def test_reviewer_with_p0_finding_never_approves(before: list[str], p0: str, after: list[str]) -> None:
    text = "\n".join([*before, p0, *after])
    parsed = parse_reviewer_output("amazon-bedrock/global.openai.gpt-6-astra", text)
    p0_found = any(f["severity"] == "P0" for f in parsed["findings"])
    if p0_found:
        assert parsed["verdict"] != "APPROVE", text


@FUZZ
@given(st.text(max_size=300))
def test_reviewer_parser_arbitrary_text_is_total(text: str) -> None:
    parsed = parse_reviewer_output("qwen3.6-35b-a3b", text)
    assert parsed["verdict"] in ("APPROVE", "CHALLENGE", "VETO")
    if any(f["severity"] == "P0" for f in parsed["findings"]):
        assert parsed["verdict"] != "APPROVE"


def _packet(tmp: Path) -> dict:
    return {"target_dir": str(tmp), "repo_root": str(tmp), "intent": "fuzz", "bindings": {}}


_finding = st.fixed_dictionaries(
    {
        "severity": st.sampled_from(["P0", "P1", "P2", "P3"]),
        "claim": st.sampled_from(["race in flush", "missing bounds check", "off-by-one in pager", "stale cache"]),
        "file_path": st.sampled_from(["", "src/a.py", "src/b.py"]),
    }
)
_structured_review = st.fixed_dictionaries(
    {
        "model_id": st.sampled_from(_ANY_MODELS),
        "verdict": st.sampled_from(["APPROVE", "CHALLENGE", "VETO", "", "approve", "garbage"]),
        "execution_status": st.sampled_from(["OK", "", "FAILED", "TIMEOUT", "error", None]),
        "findings": st.lists(_finding, max_size=2),
    }
)


@FUZZ
@given(st.lists(_structured_review, min_size=1, max_size=5))
def test_synthesis_failed_reviewer_never_ready_unanimous(tmp_path: Path, reviews: list[dict]) -> None:
    syn = synthesize_elenchus_verdict(reviews, _packet(tmp_path), target_dir=tmp_path)
    failed = any(str(r["execution_status"] or "").strip().upper() not in ("", "OK") for r in reviews)
    if failed:
        assert syn["seal_status"] != "READY_UNANIMOUS"
    if any(r["verdict"] == "VETO" or any(f["severity"] == "P0" for f in r["findings"]) for r in syn["raw_reviews"]):
        assert syn["seal_status"] != "READY_UNANIMOUS"


_approve_review = st.builds(
    lambda m: {"model_id": m, "verdict": "APPROVE", "execution_status": "OK", "findings": []},
    st.sampled_from(_LLM_MODELS),
)
_failed_review = st.builds(
    lambda m, st_, v: {"model_id": m, "verdict": v, "execution_status": st_, "findings": []},
    st.sampled_from(_ANY_MODELS),
    st.sampled_from(["FAILED", "TIMEOUT", "error", "cancelled"]),
    st.sampled_from(["APPROVE", "CHALLENGE", ""]),
)


@FUZZ
@given(st.lists(_approve_review, min_size=2, max_size=4), _failed_review, st.randoms(use_true_random=False))
def test_synthesis_otherwise_unanimous_panel_with_failure_never_ready(
    tmp_path: Path, approvals: list[dict], failed: dict, rnd
) -> None:
    # Regression: a FAILED deterministic reviewer was excluded from llm_verdicts and sealed READY_UNANIMOUS.
    reviews = [*approvals, failed]
    rnd.shuffle(reviews)
    syn = synthesize_elenchus_verdict(reviews, _packet(tmp_path), target_dir=tmp_path)
    assert syn["seal_status"] != "READY_UNANIMOUS"


@FUZZ
@given(st.lists(st.tuples(st.sampled_from(_LLM_MODELS), _reviewer_noise, _p0_line), min_size=1, max_size=3))
def test_synthesis_raw_text_p0_never_ready(tmp_path: Path, items: list[tuple[str, list[str], str]]) -> None:
    reviews = [{"model_id": m, "raw_text": "\n".join([*noise, p0])} for m, noise, p0 in items]
    syn = synthesize_elenchus_verdict(reviews, _packet(tmp_path), target_dir=tmp_path)
    if any(f["severity"] == "P0" for r in syn["raw_reviews"] for f in r["findings"]):
        assert syn["seal_status"] != "READY_UNANIMOUS"


_panel_member = st.one_of(
    st.sampled_from(_ANY_MODELS),
    st.fixed_dictionaries(
        {
            "model_id": st.sampled_from(_ANY_MODELS),
            "execution_status": st.sampled_from(["OK", "", "FAILED", None, "timeout"]),
        }
    ),
)


@FUZZ
@given(st.lists(_panel_member, max_size=8), st.randoms(use_true_random=False))
def test_panel_n_eff_bounds_and_permutation_invariance(members: list, rnd) -> None:
    panel = compute_effective_panel_size(members)
    n_ok = panel["llm_reviewer_count"]
    assert 1.0 <= panel["n_eff"] <= max(1, n_ok) + 1e-9
    assert panel["n_eff"] <= max(1, len(panel["distinct_llm_lineages"])) + 1e-9
    if panel["gate_closable"]:
        assert n_ok >= 2 and len(panel["distinct_llm_lineages"]) >= 2
    shuffled = list(members)
    rnd.shuffle(shuffled)
    other = compute_effective_panel_size(shuffled)
    for key in ("n_eff", "lineage_counts", "independent_panel", "gate_closable", "failed_reviewer_count"):
        assert other[key] == panel[key]


# ---------------------------------------------------------------------------
# evr builder / reader round-trip and single-byte tamper detection
# ---------------------------------------------------------------------------

_SIGN_KEY = "fuzz-test-key"
_name = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=12)
_files = st.dictionaries(
    st.builds(
        lambda d, n, e: f"{d}{n}{e}",
        st.sampled_from(["", "docs/", "src/"]),
        _name,
        st.sampled_from([".txt", ".dat", ".md"]),
    ),
    st.binary(max_size=4096),
    min_size=1,
    max_size=4,
)


def _build(files: dict[str, bytes], codec: EvrCodec | None) -> bytes:
    b = EvrBuilder(title="fuzz", author="fuzz", polyglot=False, sign_key=_SIGN_KEY, created_unix_ns=1)
    for path, data in sorted(files.items()):
        b.add_bytes(path, data, codec=codec)
    return b.build_bytes()


_CLEAN_ERRORS = (ValueError, KeyError, TypeError)


@FUZZ_SLOW
@given(_files, st.sampled_from([None, EvrCodec.RAW, EvrCodec.ZLIB]))
def test_evr_round_trip(files: dict[str, bytes], codec: EvrCodec | None) -> None:
    blob = _build(files, codec)
    with tempfile.TemporaryDirectory() as td:
        cap = Path(td) / "fuzz.evr"
        cap.write_bytes(blob)
        with EvrReader(cap) as r:
            res = r.verify(sign_key=_SIGN_KEY)
            assert res["valid"] is True, res["errors"]
            assert res["signature_verified"] is True
            out = r.extract_all(Path(td) / "out")
        assert len(out) == len(files)
        for path, data in files.items():
            assert (Path(td) / "out" / path).read_bytes() == data


@FUZZ_SLOW
@given(st.binary(max_size=4096))
def test_evr_round_trip_tensor_suffix_bytes(data: bytes) -> None:
    blob = _build({"x.bin": data}, None)
    with EvrReader(blob) as r:
        assert r.verify(sign_key=_SIGN_KEY)["valid"] is True
        assert r.read_chunk("x.bin") == data


@FUZZ_SLOW
@given(_files, st.data())
def test_evr_single_byte_flip_detected_or_clean_error(files: dict[str, bytes], data: st.DataObject) -> None:
    blob = bytearray(_build(files, None))
    idx = data.draw(st.integers(min_value=0, max_value=len(blob) - 1), label="flip_index")
    mask = data.draw(st.integers(min_value=1, max_value=255), label="xor_mask")
    blob[idx] ^= mask
    try:
        r = EvrReader(bytes(blob))
    except _CLEAN_ERRORS:
        return
    try:
        res = r.verify(sign_key=_SIGN_KEY)
        if res["valid"]:
            # A flip that lands in unbound padding may verify, but content must then be byte-identical.
            for path, original in files.items():
                assert r.read_chunk(path) == original, (idx, path)
    except _CLEAN_ERRORS:
        pass
    finally:
        r.close()
