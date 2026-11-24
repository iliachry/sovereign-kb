"""Protocol constants, regexes, and header/severity token sets for Elenchus."""

from __future__ import annotations

import math
import re
from typing import Any

ELENCHUS_PROTOCOL_VERSION = "1.0.0"
MIN_INDEPENDENT_N_EFF = 1.80
ANCHOR_ECHO_THRESHOLD = 0.65
MIN_REJECTION_REASON_CHARS = 12
DEFAULT_MAX_DIFF_CHARS = 24000
DEFAULT_GWI_PASS_THRESHOLD = 9.0

_VALID_SEVERITIES = ("P0", "P1", "P2", "INFO")
_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "INFO": 3}
_VALID_CATEGORIES = ("correctness", "security", "test_gap", "spec_drift", "performance", "provenance")
_VALID_VERDICTS = ("APPROVE", "CHALLENGE", "VETO")
_VERDICT_SCORE = {"APPROVE": 0.0, "CHALLENGE": 0.5, "VETO": 1.0}
_VERIFIED_LLM_LINEAGES = frozenset({"anthropic", "openai", "qwen", "google", "meta", "deepseek", "mistral", "amazon"})
_VALID_EXECUTION_MODES = frozenset({"explicit", "reviews_file", "http", "oc_triad", "hybrid", "offline", "auto"})
_RECEIPT_BASENAMES = frozenset({"goldwood.json", "gate_receipt.json", "elenchus_challenge.json", "passport.yaml"})
_VOLATILE_GWI_KEYS = frozenset(
    {"generatedAt", "generated_at", "audit_provenance", "evidence_file", "evr_capsule", "elenchus_gate"}
)
_VOLATILE_GATE_RECEIPT_KEYS = frozenset(
    {"executed_at", "timestamp_epoch", "duration_ms", "output_tail", "cached", "reused_recent_receipt", "age_seconds"}
)
_INLINE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SENSITIVE_PARAM_RE = re.compile(r"(?:key|token|secret|pass|pwd|auth|sig|cred|bearer)", re.IGNORECASE)
_LABELED_APPROVE_LINE_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*+]\s*)?\*{0,2}(?:DISPOSITION|VERDICT|DECISION|STATUS|RESULT)\*{0,2}\s*"
    r"[:=—-]\*{0,2}\s*\*{0,2}APPROVED?\*{0,2}(?:\s*(?:—|--|-|:|\(|,|;|\.|!)\s*(.*))?$",
    re.IGNORECASE,
)
_STANDALONE_APPROVE_LINE_RE = re.compile(
    r"^(?:#+\s*)?(?:[-*+]\s*)?\*{0,2}APPROVED?\*{0,2}(?:\s*(?:—|--|-|:|\(|,|;|\.|!)\s*(.*))?$",
    re.IGNORECASE,
)
_APPROVE_TAIL_DISQUALIFY_RE = re.compile(
    r"\b(?:if|when|unless|until|whether|but|however|except|although|though|incomplete|skipped|pending|"
    r"fail|failed|failing|blocked|conditional|assuming|provided|never|cannot|can't|not\s+approved|"
    r"would|should|could|subject|contingent|dependent|provisional|tentative|partial|only|fixing|"
    r"untested|unverified|require|requires|required|need|needs|must|awaiting|wait|waiting|"
    r"do\s+not\s+(?:merge|seal|ship|approve)|once)\b",
    re.IGNORECASE,
)

_GATEWAY_PREFIXES = (
    "amazon-bedrock/",
    "bedrock/",
    "openrouter/",
    "vertex_ai/",
    "vertex/",
    "azure/",
    "together/",
    "fireworks/",
    "groq/",
    "litellm/",
    "ollama/",
    "vllm/",
    "sglang/",
    "huggingface/",
    "hf/",
)
_REGION_PREFIXES = ("global.", "us.", "eu.", "ap.", "apac.", "emea.")

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "have",
        "has",
        "had",
        "was",
        "were",
        "are",
        "not",
        "but",
        "can",
        "will",
        "should",
        "could",
        "would",
        "may",
        "might",
        "about",
        "when",
        "where",
        "what",
        "which",
        "there",
        "their",
        "they",
        "them",
        "then",
        "than",
        "also",
        "only",
        "before",
        "after",
        "more",
        "most",
        "some",
        "such",
        "each",
        "both",
        "all",
        "any",
        "none",
        "does",
        "did",
        "been",
        "being",
        "its",
        "our",
        "your",
        "use",
        "used",
        "using",
        "without",
        "within",
        "verify",
        "check",
        "ensure",
        "code",
        "file",
    }
)

_SCORE_BLIND_DROP_KEYS = frozenset(
    {
        "goldwood_g",
        "coupled_goldwood_g",
        "raw_goldwood_g",
        "unmasked_goldwood_g",
        "linear_score",
        "barrier_product",
        "min_tier_score",
        "compositeScoreG",
        "linearScoreG",
        "barrierProductPsi",
        "minTierScore",
        "sovereignGateG",
        "status",
        "verdict",
        "tiers",
        "dimensions",
        "calculation",
        "craft_lenses",
        "craftLenses",
        "notes",
        "jacobianDgDs",
        "headroomGainDg",
        "highestLeverageTier",
    }
)

_FINDING_PATH_RE = re.compile(
    r"`?([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:py|ts|tsx|js|jsx|rs|go|sh|md|toml|json|yaml|yml))(?::(\d+))?`?"
)
_PROSE_SEV_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `**P1**: desc`, `### **P1 (cat)**: desc`, `- __[P1]__ (cat) — desc`
    re.compile(
        r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?(?:\*{1,3}|_{1,3})\[?(P0|P1|P2|INFO)\]?(?:\s*\([^)]*\))?(?:\*{1,3}|_{1,3})(?:\s*\([^)]*\))?\s*(?::|—|--|-)?\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    # `**P1 — desc**`, `**P1 — title** in `file:line``, `**P1 (security): desc**`, `**[P1] — desc**`
    re.compile(
        r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?(?:\*{1,3}|_{1,3})\[?(P0|P1|P2|INFO)\]?(?:\s*\([^)]*\))?\s*(?::|—|--|-)\s*(.+?)(?:\*{1,3}|_{1,3})(?:\s+(.+))?\s*$",
        re.IGNORECASE,
    ),
    # `- [P1] desc`, `### [P1] (cat): desc`
    re.compile(
        r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?\[\s*(P0|P1|P2|INFO)(?:\s*(?:\([^)\]\n]*\)|[:/-]\s*[A-Za-z0-9_ -]{1,32}))?\s*\](?:\s*\([^)]*\))?\s*(?::|—|--|-)?\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    # `- P1: desc`, `### P1 — desc`, `1) P1 (cat) — desc`, `P1 (cat) desc`
    re.compile(
        r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?(P0|P1|P2|INFO)"
        r"(?:\s*\([^)\]\n]+\)\s*(?:(?::|—|--|-\s)\s*|\s+(?!(?:is|are|was|were|has\s+been|have\s+been)\s+(?:fixed|resolved|closed|addressed|remediated|verified)\b))"
        r"|\s*(?::|—|--|-\s)\s*)(.+?)\s*$",
        re.IGNORECASE,
    ),
)
_UNSAFE_DIFF_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("eval_call", re.compile(r"\beval\s*\(")),
    ("exec_call", re.compile(r"\bexec\s*\(")),
    ("shell_true", re.compile(r"\bshell\s*=\s*True\b")),
    ("pickle_loads", re.compile(r"\bpickle\.loads?\s*\(")),
)


_TABLE_CELL_SEV_RE = re.compile(
    r"^\[?\s*(P0|P1|P2|INFO)(?!\s*[/|—-]\s*(?:P[0-3]|INFO)\b)(?:\s*\([^)\]\n]{1,80}\)|\s*/\s*[A-Za-z_]{2,24})?\s*\]?$",
    re.IGNORECASE,
)
_TABLE_DEFECT_RESOLVED_RE = re.compile(
    r"^(?:[✅✓☑]+\s*)?(?:closed|fixed|resolved|verified\s+(?:closed|fixed|resolved|clean))\.?$",
    re.IGNORECASE,
)
_TABLE_AUDIT_PASS_RE = re.compile(r"^(?:[✅✓☑]+\s*)?pass(?:ed)?\.?$", re.IGNORECASE)
_TABLE_CHECKMARK_ONLY_RE = re.compile(r"^[✅✓☑]+\.?$")
_TABLE_HEADER_LABELS = frozenset(
    {
        "#",
        "id",
        "no",
        "index",
        "file",
        "path",
        "location",
        "line",
        "lines",
        "severity",
        "sev",
        "priority",
        "level",
        "category",
        "type",
        "claim",
        "description",
        "finding",
        "findings",
        "issue",
        "issues",
        "summary",
        "details",
        "status",
        "state",
        "result",
        "verdict",
        "notes",
        "evidence",
        "recommendation",
        "impact",
        "reproduced",
        "confirmed",
        "symbol",
        "component",
        "components",
        "module",
        "modules",
        "area",
        "areas",
        "scope",
        "target",
        "layer",
        "service",
        "package",
        "domain",
        "section",
        "topic",
        "feature",
        "check",
        "checks",
        "gate",
        "gates",
        "metric",
        "rule",
        "test",
        "tests",
        "stage",
    }
)
_TABLE_STATUS_HEADER_COLS = frozenset({"status", "state", "result", "verdict", "outcome", "resolution", "disposition"})
_TABLE_SEV_HEADER_COLS = frozenset({"severity", "sev", "priority", "level"})
_TABLE_CLAIM_DATA_HEADER_COLS = frozenset(
    {"claim", "description", "finding", "findings", "issue", "issues", "file", "path", "location", "symbol", "evidence"}
)
_HDR_P0_COUNT_RE = re.compile(
    r"^(?:(?:open|active|unresolved|remaining|new)\s+)?p0s?"
    r"(?:\s*(?:count|findings?|issues?|blockers?|defects?|items?|\([^)\n]{1,32}\)))?$",
    re.IGNORECASE,
)
_HDR_P1_COUNT_RE = re.compile(
    r"^(?:(?:(?:open|active|unresolved|remaining|new)\s+)?p1s?"
    r"(?:\s*(?:count|findings?|issues?|blockers?|defects?|items?|\([^)\n]{1,32}\)))?"
    r"|(?:(?:open|active|unresolved|remaining)\s+)?p0\s*[/+&|-]\s*p1"
    r"(?:\s*(?:count|findings?|issues?|blockers?|defects?|\([^)\n]{1,32}\)))?"
    r"|(?:(?:open|active|unresolved|remaining)\s+)?blockers?(?:\s*count)?)$",
    re.IGNORECASE,
)
_WORD_SEV_COUNT_MAP = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
_POS_SEV_COUNT_CELL_RE = re.compile(
    r"^(?:\+?([1-9]\d{0,5})\.?|(one|two|three|four|five))\+?"
    r"(?:\s*(?:open|unresolved|blocking|found|remaining|issues?|findings?|blockers?|defects?|\([^)]*\)))?$",
    re.IGNORECASE,
)
_GFM_DELIM_ROW_RE = re.compile(r"^\s*\|?(?:\s*:?-{1,}:?\s*\|)+\s*:?-{1,}:?\s*\|?\s*$")
_TAG_ONLY_SEV_LINE_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:(?:[-*+]|\d+[.)])\s*)?\[?\s*(P0|P1|P2|INFO)(?:\s*(\([^)\]\n]{1,120}\)))?\s*\]?\s*[:—-]?\s*$",
    re.IGNORECASE,
)


def _is_real_num(val: Any) -> bool:
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return False
    try:
        return math.isfinite(float(val))
    except (TypeError, ValueError, OverflowError):
        return False


def _safe_float(val: Any, default: float = 0.0) -> float:
    if not _is_real_num(val):
        return default
    try:
        return float(val)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_int(val: Any, default: int | None = None) -> int | None:
    if isinstance(val, bool) or val is None:
        return default
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError):
        return default


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool) and _is_real_num(val)


def classify_model_lineage(model_id: str) -> str:
    """
    Map a provider-qualified model identifier to its uncorrelated training lineage.
    Strips cloud gateway (`amazon-bedrock/`, `openrouter/`, ...) and region (`global.`, `us.`, `eu.`)
    routing prefixes BEFORE matching family tokens so Bedrock-hosted Anthropic/OpenAI models never
    collapse into `"amazon"`.
    """
    raw = (model_id or "").strip().lower()
    if not raw:
        return "unknown"

    core = raw
    changed = True
    while changed:
        changed = False
        for pfx in _GATEWAY_PREFIXES:
            if core.startswith(pfx):
                core = core[len(pfx) :]
                changed = True
                break
        for rpfx in _REGION_PREFIXES:
            if core.startswith(rpfx):
                core = core[len(rpfx) :]
                changed = True
                break

    if any(tok in core for tok in ("kb-gwi", "skb-", "elenchus-local", "deterministic", "heuristic")):
        return "deterministic"
    if core.startswith(("anthropic.", "anthropic/")) or any(
        tok in core for tok in ("claude", "fable", "sonnet", "opus", "haiku")
    ):
        return "anthropic"
    if core.startswith(("openai.", "openai/")) or re.search(
        r"(?:^|[./_-])(?:gpt-\d|gpt\d|o1(?:[-.]|$)|o3(?:[-.]|$)|o4(?:[-.]|$)|astra)",
        core,
    ):
        return "openai"
    if "qwen" in core:
        return "qwen"
    if any(tok in core for tok in ("gemini", "gemma", "google.")):
        return "google"
    if any(tok in core for tok in ("llama", "meta.")):
        return "meta"
    if "deepseek" in core:
        return "deepseek"
    if any(tok in core for tok in ("mistral", "mixtral", "codestral", "pixtral")):
        return "mistral"
    if any(tok in core for tok in ("nova", "titan", "amazon.")):
        return "amazon"

    head = core.split("/")[0].split(".")[0].split(":")[0]
    sanitized = re.sub(r"[^a-z0-9_-]+", "-", head).strip("-")
    return f"other:{sanitized}" if sanitized else "unknown"


def _normalize_execution_status(raw: Any) -> str:
    """
    Fail-closed reviewer execution status: a missing/empty status (legacy records that carry a real verdict)
    or an explicit `OK` is `OK`; every other value (`FAILED`, `TIMEOUT`, `ERROR`, `CANCELLED`, ...) is `FAILED`.
    """
    st = str(raw if raw is not None else "").strip().upper()
    return "OK" if st in ("", "OK") else "FAILED"
