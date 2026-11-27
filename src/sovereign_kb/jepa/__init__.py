"""Unified Vault JEPA (`sovereign_kb.jepa`) — Joint-Embedding Predictive Architecture for Sovereign-KB."""

from __future__ import annotations

from .cli_ops import cmd_jepa
from .code_jepa import run_code_graph_jepa
from .features import (
    ACTIONS,
    JEPATransition,
    VaultCorpus,
    embed_query_for_corpus,
    extract_transitions,
    hash_text_vector,
    load_vault_corpus,
)
from .heal import heal_vault_links_with_jepa
from .hierarchical import ChunkRecord, load_chunk_records, retrieve_chunks_with_hjepa, train_hierarchical_jepa
from .model import JEPAConfig, VaultJEPA
from .ops import (
    DEFAULT_CHECKPOINT_REL,
    benchmark_jepa,
    detect_surprise,
    discover_gaps,
    jepa_hybrid_search,
    jepa_status,
    plan_latent_trajectory,
    predict_latent,
    rerank_search_with_jepa,
    train_vault_jepa,
)
from .soar_bridge import resolve_impasse_with_jepa

__all__ = [
    "ACTIONS",
    "ChunkRecord",
    "DEFAULT_CHECKPOINT_REL",
    "JEPAConfig",
    "JEPATransition",
    "VaultCorpus",
    "VaultJEPA",
    "benchmark_jepa",
    "cmd_jepa",
    "detect_surprise",
    "discover_gaps",
    "embed_query_for_corpus",
    "extract_transitions",
    "hash_text_vector",
    "heal_vault_links_with_jepa",
    "jepa_hybrid_search",
    "jepa_status",
    "load_chunk_records",
    "load_vault_corpus",
    "plan_latent_trajectory",
    "predict_latent",
    "rerank_search_with_jepa",
    "resolve_impasse_with_jepa",
    "retrieve_chunks_with_hjepa",
    "run_code_graph_jepa",
    "train_hierarchical_jepa",
    "train_vault_jepa",
]
