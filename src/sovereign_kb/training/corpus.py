"""Generic vault corpus parser, privacy redactor, and deterministic split utilities."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXCLUDED_DIR_NAMES = {".kb", ".claude", ".git", "__pycache__", ".venv", "node_modules"}
DEFAULT_EXCLUDE_GLOBS = ("*private*", "*secret*", "*archive*", "*forensic-dossier*")

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{16,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_GL_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._-]{16,}\b"), r"\1[REDACTED_BEARER]"),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PASSKEY)[A-Z0-9_]*\s*[:=]\s*)['\"]?[^\s'\",;]{6,}['\"]?"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"), "[REDACTED_TS_IP]"),
    (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "[REDACTED_LAN_IP]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,26}\b"), "[REDACTED_IBAN]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
)


@dataclass(frozen=True)
class NoteDocument:
    rel_path: str
    domain: str
    title: str
    note_type: str
    tags: tuple[str, ...]
    related: tuple[str, ...]
    wikilinks: tuple[str, ...]
    body: str
    split: str


@dataclass(frozen=True)
class NoteChunk:
    chunk_id: str
    rel_path: str
    domain: str
    title: str
    heading_path: str
    text: str
    word_count: int
    wikilinks: tuple[str, ...]
    related: tuple[str, ...]
    split: str
    tags: tuple[str, ...] = field(default_factory=tuple)


def deterministic_split(rel_path: str, val_mod: int = 10) -> str:
    """Assign file-level train/val split deterministically by SHA-256 of rel_path."""
    digest = int(hashlib.sha256(rel_path.encode("utf-8")).hexdigest(), 16)
    return "val" if (digest % max(2, val_mod) == 0) else "train"


def is_excluded_path(rel_path: str, exclude_globs: Sequence[str] = DEFAULT_EXCLUDE_GLOBS) -> bool:
    """Return True if any path segment or filename matches a privacy exclusion glob."""
    norm = rel_path.replace("\\", "/").lower()
    name = Path(norm).name
    for pat in exclude_globs:
        pat_l = pat.lower()
        if fnmatch.fnmatch(norm, pat_l) or fnmatch.fnmatch(name, pat_l):
            return True
    return False


def scrub_secrets(text: str) -> str:
    """Scrub tokens, keys, emails, IBANs, and private network IPs."""
    out = text
    for pattern, repl in SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_markdown_headings(text: str, headings: Sequence[str]) -> str:
    """Remove Markdown sections whose heading contains any substring in `headings`."""
    if not headings:
        return text
    needles = tuple(h.lower() for h in headings if h.strip())
    if not needles:
        return text
    lines = text.splitlines()
    out: list[str] = []
    skip_level: int | None = None
    for line in lines:
        hm = HEADING_RE.match(line)
        if hm:
            lvl = len(hm.group(1))
            htxt = hm.group(2).lower()
            if skip_level is not None and lvl <= skip_level:
                skip_level = None
            if any(n in htxt for n in needles):
                skip_level = lvl
                continue
        if skip_level is None:
            out.append(line)
    return "\n".join(out)


def apply_privacy_filter(
    text: str,
    scope: str = "public-safe",
    redact_headings: Sequence[str] = ("somatic, medical",),
) -> str:
    """Apply mandatory secret scrubbing and scope-specific heading redaction."""
    if scope not in {"public-safe", "private-local"}:
        raise ValueError(f"Unsupported scope: {scope!r}")
    out = redact_markdown_headings(text, redact_headings) if scope == "public-safe" else text
    return scrub_secrets(out)


def _parse_simple_yaml_list(val: str) -> list[str]:
    val = val.strip()
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
    return [val.strip("\"'")] if val else []


def parse_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    """Stdlib-only YAML frontmatter parser returning (metadata, body)."""
    m = FRONTMATTER_RE.match(raw_text)
    if not m:
        return {}, raw_text
    fm_text = m.group(1)
    body = raw_text[m.end() :]
    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in fm_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and current_list_key is not None:
            item = line.lstrip()[2:].strip().strip("\"'")
            lst = meta.setdefault(current_list_key, [])
            if isinstance(lst, list):
                lst.append(item)
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            k = key.strip()
            v = val.strip()
            if not v:
                meta[k] = []
                current_list_key = k
            elif v.startswith("["):
                meta[k] = _parse_simple_yaml_list(v)
                current_list_key = None
            else:
                meta[k] = v.strip("\"'")
                current_list_key = None
    return meta, body


def normalize_wikilink(raw_target: str) -> str:
    """Strip alias (`|...`) and heading anchor (`#...`) from a wikilink target."""
    target = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
    if target.endswith(".md"):
        target = target[:-3]
    return target.strip("/")


def extract_wikilinks(text: str) -> tuple[str, ...]:
    """Extract ordered unique normalized wikilink targets from markdown text."""
    seen: dict[str, None] = {}
    for m in WIKILINK_RE.finditer(text):
        norm = normalize_wikilink(m.group(1))
        if norm:
            seen[norm] = None
    return tuple(seen.keys())


def _split_words_with_overlap(text: str, max_words: int, overlap_words: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]
    step = max(1, max_words - overlap_words)
    parts: list[str] = []
    for start in range(0, len(words), step):
        window = words[start : start + max_words]
        if window:
            parts.append(" ".join(window))
        if start + max_words >= len(words):
            break
    return parts


def chunk_markdown_by_headings(
    body: str,
    title: str,
    max_words: int = 450,
    overlap_words: int = 45,
) -> list[tuple[str, str]]:
    """Split markdown body into (heading_path, chunk_text) pairs <= max_words."""
    sections: list[tuple[str, list[str]]] = []
    heading_stack: list[tuple[int, str]] = []
    current_lines: list[str] = []
    current_heading = title

    for line in body.splitlines():
        hm = HEADING_RE.match(line)
        if hm:
            if any(ln.strip() for ln in current_lines):
                sections.append((current_heading, current_lines))
                current_lines = []
            level = len(hm.group(1))
            heading_text = hm.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_text))
            current_heading = " > ".join([title] + [h for _, h in heading_stack if h != title])
            current_lines.append(line)
        else:
            current_lines.append(line)

    if any(ln.strip() for ln in current_lines):
        sections.append((current_heading, current_lines))

    chunks: list[tuple[str, str]] = []
    for heading_path, lines in sections:
        sec_text = "\n".join(lines).strip()
        if not sec_text:
            continue
        for part in _split_words_with_overlap(sec_text, max_words=max_words, overlap_words=overlap_words):
            chunks.append((heading_path, part))
    return chunks


def iter_markdown_files(
    root: Path,
    scope: str = "public-safe",
    exclude_globs: Sequence[str] = DEFAULT_EXCLUDE_GLOBS,
) -> list[Path]:
    """Return sorted markdown paths inside a vault directory."""
    files: list[Path] = []
    for p in sorted(root.rglob("*.md")):
        rel_parts = p.relative_to(root).parts
        if any(part in EXCLUDED_DIR_NAMES or part.startswith(".") for part in rel_parts):
            continue
        rel = p.relative_to(root).as_posix()
        if scope == "public-safe" and is_excluded_path(rel, exclude_globs=exclude_globs):
            continue
        files.append(p)
    return files


def load_notes(
    root: Path,
    scope: str = "public-safe",
    exclude_globs: Sequence[str] = DEFAULT_EXCLUDE_GLOBS,
    redact_headings: Sequence[str] = ("somatic, medical",),
) -> list[NoteDocument]:
    """Load and redact markdown notes from a vault."""
    docs: list[NoteDocument] = []
    for path in iter_markdown_files(root, scope=scope, exclude_globs=exclude_globs):
        rel_path = path.relative_to(root).as_posix()
        domain = rel_path.split("/", 1)[0] if "/" in rel_path else "root"
        raw = path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(raw)
        clean_body = apply_privacy_filter(body, scope=scope, redact_headings=redact_headings)
        title = str(fm.get("title") or path.stem).strip()
        note_type = str(fm.get("type") or "note").strip()
        raw_tags = fm.get("tags") or []
        tags = tuple(str(t).strip() for t in raw_tags) if isinstance(raw_tags, list) else ()
        raw_related = fm.get("related") or []
        related_list: list[str] = []
        if isinstance(raw_related, list):
            for item in raw_related:
                related_list.extend(extract_wikilinks(str(item)))
        wikilinks = extract_wikilinks(clean_body)
        docs.append(
            NoteDocument(
                rel_path=rel_path,
                domain=domain,
                title=title,
                note_type=note_type,
                tags=tags,
                related=tuple(dict.fromkeys(related_list)),
                wikilinks=wikilinks,
                body=clean_body,
                split=deterministic_split(rel_path),
            )
        )
    return docs


def load_chunks(
    root: Path,
    scope: str = "public-safe",
    max_words: int = 450,
    overlap_words: int = 45,
    exclude_globs: Sequence[str] = DEFAULT_EXCLUDE_GLOBS,
    redact_headings: Sequence[str] = ("somatic, medical",),
) -> list[NoteChunk]:
    """Load heading-aware chunks across all matching notes."""
    chunks: list[NoteChunk] = []
    for doc in load_notes(root, scope=scope, exclude_globs=exclude_globs, redact_headings=redact_headings):
        for idx, (heading_path, text) in enumerate(
            chunk_markdown_by_headings(doc.body, doc.title, max_words=max_words, overlap_words=overlap_words)
        ):
            wc = len(text.split())
            if wc < 12:
                continue
            chunk_id = hashlib.sha1(f"{doc.rel_path}::{idx}".encode()).hexdigest()[:16]
            chunks.append(
                NoteChunk(
                    chunk_id=chunk_id,
                    rel_path=doc.rel_path,
                    domain=doc.domain,
                    title=doc.title,
                    heading_path=heading_path,
                    text=text,
                    word_count=wc,
                    wikilinks=extract_wikilinks(text),
                    related=doc.related,
                    split=doc.split,
                    tags=doc.tags,
                )
            )
    return chunks


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract and parse the first balanced JSON object from model output."""
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text, idx)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def rrf_fuse(ranked_lists: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over multiple ranked ID/path lists."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
