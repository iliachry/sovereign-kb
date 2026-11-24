"""Elenchus (`skb elenchus` / `organon elenchus`) — modular cross-lineage challenge & Law L10 gate package."""

from __future__ import annotations

import shutil
import subprocess
import urllib
import urllib.error
import urllib.parse
import urllib.request

from .adjudication import adjudicate_elenchus_finding, evaluate_elenchus_gate
from .cli_ops import _print_elenchus_report, cmd_elenchus, register_elenchus_subparser
from .constants import (
    ANCHOR_ECHO_THRESHOLD,
    DEFAULT_GWI_PASS_THRESHOLD,
    DEFAULT_MAX_DIFF_CHARS,
    ELENCHUS_PROTOCOL_VERSION,
    MIN_INDEPENDENT_N_EFF,
    MIN_REJECTION_REASON_CHARS,
    classify_model_lineage,
)
from .council import _query_openai_compat_reviewer, _record_triad_from_elenchus, run_elenchus_challenge
from .git_diff import _collect_bounded_diff, build_elenchus_challenge_packet
from .git_hash import (
    _compute_gwi_semantic_sha256,
    _compute_worktree_content_sha,
    _detect_gwi_schema_contradiction,
    _normalize_git_rel_path,
    _run_git_cmd,
)
from .panel import compute_effective_panel_size, synthesize_elenchus_verdict
from .prose_rules import _strip_inline_formatting
from .redaction import _redact_sensitive_text, _scrub_review_dict
from .review_parser import parse_oc_triad_stdout, parse_reviewer_output

__all__ = [
    "ANCHOR_ECHO_THRESHOLD",
    "DEFAULT_GWI_PASS_THRESHOLD",
    "DEFAULT_MAX_DIFF_CHARS",
    "ELENCHUS_PROTOCOL_VERSION",
    "MIN_INDEPENDENT_N_EFF",
    "MIN_REJECTION_REASON_CHARS",
    "_collect_bounded_diff",
    "_compute_gwi_semantic_sha256",
    "_compute_worktree_content_sha",
    "_detect_gwi_schema_contradiction",
    "_normalize_git_rel_path",
    "_print_elenchus_report",
    "_query_openai_compat_reviewer",
    "_record_triad_from_elenchus",
    "_redact_sensitive_text",
    "_run_git_cmd",
    "_scrub_review_dict",
    "_strip_inline_formatting",
    "adjudicate_elenchus_finding",
    "build_elenchus_challenge_packet",
    "classify_model_lineage",
    "cmd_elenchus",
    "compute_effective_panel_size",
    "evaluate_elenchus_gate",
    "parse_oc_triad_stdout",
    "parse_reviewer_output",
    "register_elenchus_subparser",
    "run_elenchus_challenge",
    "shutil",
    "subprocess",
    "synthesize_elenchus_verdict",
    "urllib",
]
