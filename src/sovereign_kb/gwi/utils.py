"""Regex guards, skill frontmatter validator, file discovery, and profile detector."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ALLOW_RANDOM_MARKER = "gwi:allow-random"

_JS_RANDOM_RE = re.compile(r"""(?<![\w.'"``])Math\.random\s*\(""")
_PY_RANDOM_RE = re.compile(r"""(?<![\w.'"``])random\.(?:random|randint|choice|shuffle|uniform|gauss)\s*\(""")
_PY_SEEDED_RE = re.compile(r"""random\.seed\s*\(|random\.Random\s*\(|np\.random\.(?:default_rng|seed)|PYTHONHASHSEED""")
_LUFS_RE = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_LRA_RE = re.compile(r"\bLRA:\s*(\d+(?:\.\d+)?)\s*LU")
_PEAK_RE = re.compile(r"\b(?:Peak|True peak):\s*(-?\d+(?:\.\d+)?)\s*dB(?:FS|TP)", re.IGNORECASE)
_MD_BOLD_LEAK_RE = re.compile(r"(?<!\\)\*\*[^*\n]+\*\*")
_OVERFULL_PT_RE = re.compile(r"Overfull \\hbox \(([0-9.]+)pt too wide\)")
_SECRET_LEAK_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|" + "gl" + r"pat-[A-Za-z0-9\-_]{20,}|ghp_[A-Za-z0-9]{36})\b"
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def validate_skill_frontmatter(path: Path | str) -> tuple[bool, str]:
    """Validate YAML frontmatter in a SKILL.md file with zero mandatory dependencies.

    Checks:
      1. Delimiters: Must start with '---' and contain a closing '---'.
      2. Parsing: Validates valid YAML mapping (via PyYAML if available or strict stdlib scanner).
      3. Required keys: Must define 'name:'.
      4. Syntax: Catches dangling unkeyed mappings, unclosed quotes, tabs in indentation,
         and illegal unquoted ': ' inside mapping values.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return False, f"File does not exist: {p}"
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return False, f"Cannot read file: {e}"

    txt = txt.lstrip("\ufeff")
    if not txt.startswith("---"):
        return False, "File does not start with frontmatter delimiter '---'"
    parts = txt.split("---", 2)
    if len(parts) < 3:
        return False, "Missing closing frontmatter delimiter '---'"

    fm_block = parts[1]

    # Fast-path: try PyYAML if available in active environment
    try:
        import yaml  # type: ignore[import-untyped]

        try:
            data = yaml.safe_load(fm_block)
            if not isinstance(data, dict):
                return False, "Frontmatter must parse into a YAML key-value mapping"
            if "name" not in data:
                return False, "Missing mandatory 'name' key in frontmatter"
            return True, "OK"
        except Exception as e:
            return False, f"YAML parse error: {e}"
    except ImportError:
        pass

    # Deterministic zero-dependency YAML frontmatter scanner
    lines = fm_block.splitlines()
    in_block_scalar = False
    block_scalar_indent: int | None = None
    has_name = False
    parent_can_have_children = False

    for idx, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue

        lstripped = line.lstrip(" \t")
        indent_str = line[: len(line) - len(lstripped)]
        stripped = lstripped

        if "\t" in indent_str:
            return False, f"Line {idx}: Tabs are not allowed for YAML indentation"

        indent = len(indent_str)

        if in_block_scalar:
            if block_scalar_indent is None:
                block_scalar_indent = indent
            if indent >= block_scalar_indent:
                continue
            else:
                in_block_scalar = False
                block_scalar_indent = None

        if indent == 0:
            if ":" not in stripped:
                return False, f"Line {idx}: Expected 'key: value' mapping, got '{stripped}'"
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()
            parent_can_have_children = val == ""
            if key == "name":
                has_name = True
            if val in ("|", ">", "|-", ">-", "|+", ">+"):
                in_block_scalar = True
                block_scalar_indent = None
                continue
            if val.startswith('"') or val.startswith("'"):
                q = val[0]
                if not (val.endswith(q) and len(val) > 1 and not val.endswith("\\" + q)):
                    return False, f"Line {idx}: Unclosed quote in value for '{key}'"
            else:
                if ": " in val:
                    return (
                        False,
                        f"Line {idx}: Unquoted value for '{key}' contains ': ' which breaks YAML mapping syntax",
                    )
        else:
            if not in_block_scalar:
                if not parent_can_have_children:
                    return (
                        False,
                        f"Line {idx}: Indented mapping/text '{stripped}' is not allowed under a scalar property",
                    )
                if stripped.startswith("- ") or stripped.startswith("-"):
                    continue
                if ":" in stripped:
                    pass
                else:
                    return False, f"Line {idx}: Unexpected indented text '{stripped}' outside block scalar"

    if not has_name:
        return False, "Missing mandatory 'name' key in frontmatter"

    return True, "OK"


def _iter_source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    ignore_dirs = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "out",
        "coverage",
        "example",
        "examples",
        "fixtures",
        "corpus",
        "goula-constructions",
        "target",
        ".godot",
        "addons",
        "Library",
        "Temp",
        "Obj",
        "Logs",
        "Recordings",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "test-results",
    }
    out: list[Path] = []
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if any(part in ignore_dirs for part in p.parts):
            continue
        if p.is_file() and p.suffix.lower() in suffixes:
            out.append(p)
    return sorted(out)


def _is_test_file(path: Path) -> bool:
    if any(part in ("tests", "test", "__tests__", "e2e") for part in path.parts):
        return True
    return bool(re.search(r"(^|[._-])(test|spec)s?([._-]|$)", path.stem))


def _check_bash_negated_grep_trap(gate_path: Path) -> list[str]:
    """
    Detect the classic Bash `set -e` trap where `! grep ...` on a standalone line
    does NOT abort the script when grep finds a match (POSIX/Bash exempts `!` pipelines from `set -e`).
    """
    if not gate_path.is_file():
        return []
    issues: list[str] = []
    text = gate_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^!\s+grep\b", stripped) and "||" not in stripped and "&&" not in stripped:
            remaining = [ln.strip() for ln in lines[idx:] if ln.strip() and not ln.strip().startswith("#")]
            if remaining:
                issues.append(
                    f"{gate_path.name}:{idx} uses standalone `! grep` before subsequent commands; "
                    "Bash `set -e` ignores non-zero exit codes of `!`-negated commands unless guarded by `|| exit 1`."
                )
    return issues


def _extract_catalog_ids(root: Path) -> tuple[list[str], str]:
    """Extract registered feature IDs (`FEAT-01`, `F-01`, `CAP-01`, etc.) from AGENTS.md and docs/FEATURE_CATALOG.md."""
    ids: list[str] = []
    source_used = "none"
    for rel in ("docs/FEATURE_CATALOG.md", "AGENTS.md", "PAPER_SPEC.md"):
        p = root / rel
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore")
            found = re.findall(r"\b(?:FEAT|F|CAP|THM|CLAIM)-\d{2,3}\b", text)
            if found:
                for item in found:
                    if item not in ids:
                        ids.append(item)
                source_used = rel if source_used == "none" else f"{source_used}, {rel}"
    return ids, source_used


def detect_profile(target: Path) -> str:
    """Auto-detect whether `target` should be evaluated under `video`, `sim`, `paper`, `kb`, or `app` profile."""
    if target.is_file():
        suf = target.suffix.lower()
        if suf in (".mp4", ".mov", ".webm", ".mkv"):
            return "video"
        if suf == ".tex":
            return "paper"
        target = target.parent

    if (
        (target / "project.godot").is_file()
        or (target / "scenes").is_dir()
        or (target / "ProjectSettings" / "ProjectVersion.txt").is_file()
    ):
        return "sim"

    kb_tier_dirs = sum(
        1
        for t in ("00-identity", "01-now", "02-operating-system", "03-life-domains", "04-episodic")
        if (target / t).is_dir()
    )
    has_code_root = (
        (target / "src").is_dir() or (target / "package.json").is_file() or (target / "Cargo.toml").is_file()
    )
    if not has_code_root and ((target / ".kb" / "index.db").is_file() or kb_tier_dirs >= 2):
        return "kb"

    tex_files = [
        p for p in target.glob("*.tex") if "\\documentclass" in p.read_text(encoding="utf-8", errors="ignore")[:4000]
    ]
    if tex_files and not (target / "src").is_dir():
        return "paper"

    mp4s = list(target.glob("*.mp4"))
    srts = list(target.glob("*.srt"))
    if mp4s and (srts or (not (target / "src").is_dir() and not (target / "package.json").is_file())):
        return "video"

    return "app"
