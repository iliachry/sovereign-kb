"""Regression: sync must store unit-normalized float32 embeddings.

me-kb's `.kb/index.db` is shared with `kb_cli.py`, whose schema-v3 contract (and
`tests/test_kb_verify.py::test_schema_v3_optimizations_and_admission`) requires every
stored vector to have norm 1. Raw Ollama `nomic-embed-text` vectors have norm ~18.
"""
from __future__ import annotations

import array
import math

from sovereign_kb.cli import unit_vector_blob


def _norm(blob: bytes) -> float:
    return math.sqrt(sum(x * x for x in array.array("f", blob)))


def test_raw_vector_is_normalized():
    raw = [3.0, 4.0] * 384  # norm ~ 138
    blob = unit_vector_blob(raw)
    assert blob is not None and len(blob) == 768 * 4
    assert abs(_norm(blob) - 1.0) < 1e-4


def test_cached_json_and_bytes_are_normalized():
    assert abs(_norm(unit_vector_blob("[3.0, 4.0]")) - 1.0) < 1e-4
    assert abs(_norm(unit_vector_blob(array.array("f", [6.0, 8.0]).tobytes())) - 1.0) < 1e-4


def test_invalid_vectors_rejected():
    for bad in (None, [], [0.0, 0.0], [float("nan"), 1.0], [float("inf"), 1.0], b"bad", "not json"):
        assert unit_vector_blob(bad) is None
