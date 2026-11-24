"""F-05 regression: every path that writes `chunks.embedding` stores unit vectors, not just the helper.

me-kb's `.kb/index.db` is shared with `skb` (schema v3), and me-kb requires every stored vector to
have norm 1 (`tests/test_kb_verify.py::test_schema_v3_optimizations_and_admission` in me-kb).
On 2026-11-24 that test failed on a Mac whose `skb` ran 1eb9005: `kb verify` hands over to
`skb verify`, and its auto-sync stored the vectors of three just-edited notes exactly as the
embedding model returned them (norms 17.1 to 18.5). The normalising fix (a6da841) had landed on
the other line of history only, and its test (`test_fix_vector_normalization.py`) exercises
`unit_vector_blob` alone, so it cannot tell whether `cmd_sync` still calls it. These tests drive
the sync and verify commands themselves with a fake embedding endpoint that answers like Ollama.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import sqlite3
import urllib.request
import zlib
from pathlib import Path

import pytest

from sovereign_kb import cli as cli_mod

RAW_NORM = 18.4  # norm of the raw nomic-embed-text vectors found in me-kb's index
DIM = 768


class _FakeResponse:
    def __init__(self, vector: list[float]) -> None:
        self._body = json.dumps({"embedding": vector}).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


@pytest.fixture()
def raw_embedder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer every embedding request with a prompt-dependent vector of norm RAW_NORM; return the prompts."""
    prompts: list[str] = []

    def fake_urlopen(req: urllib.request.Request, *args: object, **kwargs: object) -> _FakeResponse:
        prompt = str(json.loads(req.data or b"{}").get("prompt", ""))
        prompts.append(prompt)
        raw = [1.0 + zlib.crc32(f"{i}|{prompt}".encode()) % 997 / 997.0 for i in range(DIM)]
        scale = RAW_NORM / math.sqrt(sum(x * x for x in raw))
        return _FakeResponse([x * scale for x in raw])

    monkeypatch.delenv("KB_NO_EMBED", raising=False)
    monkeypatch.setenv("KB_HIPS_BIN", "")
    monkeypatch.setenv("KB_EMBED_URL", "http://127.0.0.1:9/api/embeddings")
    monkeypatch.delenv("KB_EMBED_FALLBACK_URLS", raising=False)
    monkeypatch.setattr(cli_mod, "_EMBED_CIRCUIT_OPEN", False)
    monkeypatch.setattr(cli_mod, "_ACTIVE_EMBED_URL", None)
    cli_mod._EMBED_QUERY_CACHE.clear()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return prompts


def _note(path: Path, title: str, body: str) -> None:
    path.write_text(
        f"---\ntitle: {title}\ntype: note\nupdated: 2026-11-24\ntags: [test]\n---\n\n{body}",
        encoding="utf-8",
    )


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    _note(v / "INDEX.md", "Index", "# Index\n\nStart at [[alpha]].\n")
    _note(
        v / "alpha.md",
        "Alpha",
        "# Alpha\n\nBack to [[INDEX]].\n\n## Quartz\n\nQuartz notes stay as they are.\n\n"
        "## Basalt\n\nBasalt notes get edited.\n",
    )
    return v


def _sync(vault: Path) -> None:
    cli_mod.cmd_sync(argparse.Namespace(vault=str(vault), force=False, no_embed=False, quiet=True))


def _vectors(vault: Path) -> list[tuple[str, str, float]]:
    """(rel_path, heading, norm) of every stored embedding."""
    with sqlite3.connect(vault / ".kb" / "index.db") as conn:
        rows = conn.execute(
            "SELECT rel_path, heading, embedding FROM chunks WHERE embedding IS NOT NULL ORDER BY id"
        ).fetchall()
    return [(rel, heading, math.sqrt(sum(x * x for x in array.array("f", blob)))) for rel, heading, blob in rows]


def _not_unit(vault: Path) -> list[tuple[str, str, float]]:
    return [(rel, heading, round(norm, 2)) for rel, heading, norm in _vectors(vault) if abs(norm - 1.0) >= 1e-4]


def _prompts_for(prompts: list[str], heading: str) -> int:
    return sum(1 for p in prompts if f"— Alpha > {heading}\n" in p)


def test_sync_stores_unit_vectors_for_new_and_reused_embeddings(vault: Path, raw_embedder: list[str]) -> None:
    _sync(vault)
    vectors = _vectors(vault)
    assert len(vectors) >= 4, vectors
    assert _not_unit(vault) == []

    # A row an older skb wrote raw, in a note that is then edited elsewhere: its chunk is unchanged,
    # so sync reuses the stored vector instead of embedding the text again.
    with sqlite3.connect(vault / ".kb" / "index.db") as conn:
        conn.execute(
            "UPDATE chunks SET embedding = ? WHERE rel_path = 'alpha.md' AND heading = 'Quartz'",
            (array.array("f", [3.0, 4.0] * (DIM // 2)).tobytes(),),
        )
    alpha = vault / "alpha.md"
    alpha.write_text(alpha.read_text(encoding="utf-8") + "\n## Gneiss\n\nA new section.\n", encoding="utf-8")
    before = list(raw_embedder)
    cli_mod._EMBED_QUERY_CACHE.clear()  # a re-embed must reach the endpoint, not the in-process cache
    _sync(vault)

    assert _not_unit(vault) == []
    assert _prompts_for(raw_embedder[len(before) :], "Quartz") == 0, "the unchanged chunk was re-embedded"
    assert _prompts_for(raw_embedder[len(before) :], "Gneiss") == 1


def test_verify_auto_sync_stores_unit_vectors_for_an_edited_note(
    vault: Path, raw_embedder: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """The 2026-11-24 path: a note is edited, nothing syncs, and `verify` finds the index stale."""
    _sync(vault)
    alpha = vault / "alpha.md"
    alpha.write_text(
        alpha.read_text(encoding="utf-8").replace("get edited.", "got edited after the last sync."), encoding="utf-8"
    )
    before = len(raw_embedder)
    cli_mod._EMBED_QUERY_CACHE.clear()
    capsys.readouterr()

    cli_mod.cmd_verify(
        argparse.Namespace(
            vault=str(vault),
            json=True,
            heal=False,
            allow_lexical_only=False,
            no_auto_sync=False,
            require_embeddings=True,
        )
    )

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PASS", report["violations"]
    assert _prompts_for(raw_embedder[before:], "Basalt") == 1, "verify did not re-embed the edited chunk"
    assert _not_unit(vault) == []
