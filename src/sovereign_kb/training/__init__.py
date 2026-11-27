"""F-24: Sovereign Vault Training & Distillation (`skb train`)."""

from __future__ import annotations

from .cli_ops import cmd_train
from .corpus import (
    NoteChunk,
    NoteDocument,
    deterministic_split,
    extract_json_object,
    extract_wikilinks,
    load_chunks,
    load_notes,
    rrf_fuse,
    scrub_secrets,
)
from .datasets import (
    build_critic_dataset,
    build_twin_dataset,
    write_nanolm_corpus,
    write_retrieval_datasets,
)

__all__ = [
    "NoteChunk",
    "NoteDocument",
    "build_critic_dataset",
    "build_twin_dataset",
    "cmd_train",
    "deterministic_split",
    "extract_json_object",
    "extract_wikilinks",
    "load_chunks",
    "load_notes",
    "rrf_fuse",
    "scrub_secrets",
    "write_nanolm_corpus",
    "write_retrieval_datasets",
]
