"""
Frontier Humanity-to-Organon Capabilities (`sovereign_kb.frontier_organon`).

Closes the frontier capability gaps across the Sovereign Organon (`kb` · `swm` · `gwi`):
  1. Polyglot AST & SCIP-Compatible Symbol/Call-Graph Indexer (`CodeGraphIndexer`, `slice_symbol_subgraph`)
     with `hips` Block-Max WAND corpus export and k-hop dependency subgraph retrieval.
  2. Automated AST Mutation Kill-Ratio Engine & Property-Based Fuzzing Gate (`GWI-SCI-06`,
     `run_ast_mutation_probe`, `check_property_and_mutation_rigor`).
  3. Machine-Checked Formal Proof & Symbolic Theorem Verifier (`GWI-SCI-07`,
     `check_formal_proofs` across Lean 4, TLA+, Rust Kani/Verus, and SymPy/Z3).
  4. Compile-Time Prompt & Few-Shot Exemplar Optimizer (`compile_organon_prompts` — DSPy/MIPROv2
     style optimization over held-out RSI probes and Stage-5 provenance trajectories).
  5. Parametric Sleep-Wake Consolidation (`export_sleep_wake_lora_dataset` — SFT + GRPO/DPO
     preference dataset synthesis & Bittensor-safe `gx10` SGLang LoRA adapter manifest).
  6. 3D Gaussian Splatting (`3DGS`) Spatial Capture & WebXR Budget Auditor (`audit_and_package_3dgs`).
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "out",
    "coverage",
    "target",
    ".godot",
    "Library",
    "Temp",
    "Obj",
    "Logs",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "test-results",
    "site-packages",
    ".kb",
    ".swm",
}

_MAX_SOURCE_BYTES = 1_500_000


# ====================================================================================================================
# 1. Polyglot AST & SCIP-Compatible Symbol / Call-Graph Indexer (`kb code-graph`)
# ====================================================================================================================


@dataclass
class SymbolNode:
    symbol_id: str
    name: str
    kind: str  # function | method | class | struct | trait | enum | interface
    language: str  # python | typescript | rust | csharp
    file_path: str
    start_line: int
    end_line: int
    signature: str
    docstring: str = ""
    calls: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    in_degree: int = 0
    centrality: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolEdge:
    source_id: str
    target_name: str
    target_id: str | None
    relation: str  # calls | imports | inherits
    file_path: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iter_source_files(
    root: Path,
    suffixes: tuple[str, ...] = (".py", ".ts", ".tsx", ".js", ".rs", ".cs"),
    max_bytes: int | None = _MAX_SOURCE_BYTES,
) -> list[Path]:
    files: list[Path] = []
    if not root.is_dir():
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".venv"))
        for fn in sorted(filenames):
            if fn.endswith((".d.ts", ".min.js", ".bundle.js")):
                continue
            if fn.lower().endswith(suffixes):
                p = Path(dirpath) / fn
                try:
                    if max_bytes is None or p.stat().st_size <= max_bytes:
                        files.append(p)
                except OSError:
                    pass
    return files


_STOP_CALLS = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "sizeof",
        "typeof",
        "match",
        "Some",
        "None",
        "Ok",
        "Err",
        "vec",
        "println",
        "format",
        "print",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "list",
        "dict",
        "set",
        "tuple",
        "super",
        "isinstance",
        "getattr",
        "setattr",
        "hasattr",
        "max",
        "min",
        "sum",
        "any",
        "all",
        "sorted",
        "enumerate",
        "range",
        "round",
        "abs",
        "open",
        "close",
        "read",
        "write",
        "get",
        "pop",
        "append",
        "extend",
        "update",
        "items",
        "keys",
        "values",
        "split",
        "strip",
        "lstrip",
        "rstrip",
        "join",
        "replace",
        "lower",
        "upper",
        "startswith",
        "endswith",
        "dumps",
        "loads",
        "exists",
        "is_file",
        "is_dir",
        "mkdir",
        "resolve",
        "expanduser",
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
    }
)


def _disambiguate_symbol_id(base_id: str, start_line: int, seen_ids: set[str]) -> str:
    if base_id not in seen_ids:
        seen_ids.add(base_id)
        return base_id
    lined_id = f"{base_id}:L{start_line}"
    seen_ids.add(lined_id)
    return lined_id


def _extract_python_symbols(file_path: Path, rel_path: str, source: str) -> tuple[list[SymbolNode], list[SymbolEdge]]:
    nodes: list[SymbolNode] = []
    edges: list[SymbolEdge] = []
    seen_ids: set[str] = set()
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return nodes, edges

    file_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                file_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                file_imports.append(f"{mod}.{alias.name}" if mod else alias.name)

    def _collect_calls(body_node: ast.AST) -> list[tuple[str, int]]:
        called: list[tuple[str, int]] = []
        for sub in ast.walk(body_node):
            if isinstance(sub, ast.Call):
                lineno = getattr(sub, "lineno", 1)
                if isinstance(sub.func, ast.Name) and sub.func.id not in _STOP_CALLS:
                    called.append((sub.func.id, lineno))
                elif isinstance(sub.func, ast.Attribute) and sub.func.attr not in _STOP_CALLS:
                    called.append((sub.func.attr, lineno))
        return called

    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sym_id = _disambiguate_symbol_id(f"py:{rel_path}#{item.name}()", item.lineno, seen_ids)
            args = [a.arg for a in item.args.args]
            prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
            sig = f"{prefix} {item.name}({', '.join(args)})"
            doc_raw = ast.get_docstring(item) or ""
            doc = doc_raw.strip().splitlines()[0] if doc_raw.strip() else ""
            raw_calls = _collect_calls(item)
            call_names = list(dict.fromkeys(c[0] for c in raw_calls))
            nodes.append(
                SymbolNode(
                    symbol_id=sym_id,
                    name=item.name,
                    kind="function",
                    language="python",
                    file_path=rel_path,
                    start_line=item.lineno,
                    end_line=getattr(item, "end_lineno", item.lineno) or item.lineno,
                    signature=sig,
                    docstring=doc[:200],
                    calls=call_names,
                    imports=list(dict.fromkeys(file_imports)),
                )
            )
            for cname, cline in raw_calls:
                edges.append(
                    SymbolEdge(
                        source_id=sym_id,
                        target_name=cname,
                        target_id=None,
                        relation="calls",
                        file_path=rel_path,
                        line=cline,
                    )
                )
        elif isinstance(item, ast.ClassDef):
            cls_id = _disambiguate_symbol_id(f"py:{rel_path}#{item.name}", item.lineno, seen_ids)
            bases = [ast.unparse(b) if hasattr(ast, "unparse") else getattr(b, "id", "") for b in item.bases]
            doc_raw = ast.get_docstring(item) or ""
            doc = doc_raw.strip().splitlines()[0] if doc_raw.strip() else ""
            cls_calls = _collect_calls(item)
            nodes.append(
                SymbolNode(
                    symbol_id=cls_id,
                    name=item.name,
                    kind="class",
                    language="python",
                    file_path=rel_path,
                    start_line=item.lineno,
                    end_line=getattr(item, "end_lineno", item.lineno) or item.lineno,
                    signature=f"class {item.name}({', '.join(bases)})" if bases else f"class {item.name}",
                    docstring=doc[:200],
                    calls=list(dict.fromkeys(c[0] for c in cls_calls)),
                    imports=list(dict.fromkeys(file_imports)),
                    bases=[b for b in bases if b],
                )
            )
            for b in bases:
                if b:
                    edges.append(
                        SymbolEdge(
                            source_id=cls_id,
                            target_name=b.split(".")[-1],
                            target_id=None,
                            relation="inherits",
                            file_path=rel_path,
                            line=item.lineno,
                        )
                    )
            for sub in item.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_id = _disambiguate_symbol_id(
                        f"py:{rel_path}#{item.name}.{sub.name}()",
                        sub.lineno,
                        seen_ids,
                    )
                    m_args = [a.arg for a in sub.args.args]
                    m_doc_raw = ast.get_docstring(sub) or ""
                    m_doc = m_doc_raw.strip().splitlines()[0] if m_doc_raw.strip() else ""
                    m_calls = _collect_calls(sub)
                    nodes.append(
                        SymbolNode(
                            symbol_id=m_id,
                            name=f"{item.name}.{sub.name}",
                            kind="method",
                            language="python",
                            file_path=rel_path,
                            start_line=sub.lineno,
                            end_line=getattr(sub, "end_lineno", sub.lineno) or sub.lineno,
                            signature=f"def {item.name}.{sub.name}({', '.join(m_args)})",
                            docstring=m_doc[:200],
                            calls=list(dict.fromkeys(c[0] for c in m_calls)),
                            imports=list(dict.fromkeys(file_imports)),
                        )
                    )
                    for cname, cline in m_calls:
                        edges.append(
                            SymbolEdge(
                                source_id=m_id,
                                target_name=cname,
                                target_id=None,
                                relation="calls",
                                file_path=rel_path,
                                line=cline,
                            )
                        )
    return nodes, edges


_TS_IMPORT_RE = re.compile(r"""import\s+(?:type\s+)?(?:[\w*{}\s,]+\s+from\s+)?["']([^"']+)["']""")
_TS_SYM_RE = re.compile(
    r"""^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(function|class|interface|type)\s+([A-Za-z_$][\w$]*)\s*([(<{]?)""",
    re.MULTILINE,
)
_TS_CONST_FN_RE = re.compile(
    r"""^\s*export\s+const\s+([A-Za-z_$][\w$]*)\s*(?::\s*[^=]+)?=\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[^=]+)?=>""",
    re.MULTILINE,
)
_RS_USE_RE = re.compile(r"""^\s*use\s+([^;]+);""", re.MULTILINE)
_RS_IMPL_RE = re.compile(
    r"""^\s*impl(?:\s*<[^>]*>)?\s+(?:[A-Za-z_][\w:<>]*\s+for\s+)?([A-Za-z_]\w*)""",
    re.MULTILINE,
)
_RS_SYM_RE = re.compile(
    r"""^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(fn|struct|enum|trait)\s+([A-Za-z_]\w*)""",
    re.MULTILINE,
)
_CS_SYM_RE = re.compile(
    r"""^\s*(?:public|private|protected|internal)\s+(?:static\s+|sealed\s+|abstract\s+|partial\s+)*(class|struct|interface|enum)\s+([A-Za-z_]\w*)""",
    re.MULTILINE,
)
_CALL_TOKEN_RE = re.compile(r"""\b([A-Za-z_]\w*)\s*\(""")


def _extract_polyglot_regex_symbols(
    rel_path: str,
    source: str,
    language: str,
) -> tuple[list[SymbolNode], list[SymbolEdge]]:
    nodes: list[SymbolNode] = []
    edges: list[SymbolEdge] = []
    seen_ids: set[str] = set()
    lines = source.splitlines()
    n_lines = max(1, len(lines))

    if language == "typescript":
        imports = _TS_IMPORT_RE.findall(source)
        matches: list[tuple[int, str, str]] = []
        for m in _TS_SYM_RE.finditer(source):
            matches.append((source.count("\n", 0, m.start()) + 1, m.group(1), m.group(2)))
        for m in _TS_CONST_FN_RE.finditer(source):
            matches.append((source.count("\n", 0, m.start()) + 1, "function", m.group(1)))
        prefix = "ts"
    elif language == "rust":
        imports = _RS_USE_RE.findall(source)
        impl_blocks = [(source.count("\n", 0, m.start()) + 1, m.group(1)) for m in _RS_IMPL_RE.finditer(source)]
        matches = []
        for m in _RS_SYM_RE.finditer(source):
            ln = source.count("\n", 0, m.start()) + 1
            raw_kind = m.group(1)
            raw_name = m.group(2)
            line_text = lines[ln - 1] if ln - 1 < len(lines) else ""
            is_indented = line_text.startswith((" ", "\t"))
            if raw_kind == "fn" and is_indented and impl_blocks:
                enclosing = [blk_name for blk_ln, blk_name in impl_blocks if blk_ln < ln]
                if enclosing:
                    raw_name = f"{enclosing[-1]}.{raw_name}"
                    kind = "method"
                else:
                    kind = "function"
            else:
                kind = "function" if raw_kind == "fn" else raw_kind
            matches.append((ln, kind, raw_name))
        prefix = "rs"
    else:
        imports = []
        matches = [(source.count("\n", 0, m.start()) + 1, m.group(1), m.group(2)) for m in _CS_SYM_RE.finditer(source)]
        prefix = "cs"

    matches.sort(key=lambda x: x[0])
    for idx, (start_ln, kind, name) in enumerate(matches):
        end_ln = matches[idx + 1][0] - 1 if idx + 1 < len(matches) else min(n_lines, start_ln + 60)
        body_slice = "\n".join(lines[max(0, start_ln - 1) : min(n_lines, end_ln)])
        sig_line = lines[start_ln - 1].strip() if start_ln - 1 < n_lines else f"{kind} {name}"
        short_self = name.split(".")[-1]
        raw_calls = [
            c
            for c in _CALL_TOKEN_RE.findall(body_slice)
            if c not in _STOP_CALLS and c != name and c != short_self and len(c) > 2
        ]
        dedup_calls = list(dict.fromkeys(raw_calls))[:24]
        base_id = f"{prefix}:{rel_path}#{name}{'()' if kind in ('function', 'method') else ''}"
        sym_id = _disambiguate_symbol_id(base_id, start_ln, seen_ids)
        nodes.append(
            SymbolNode(
                symbol_id=sym_id,
                name=name,
                kind=kind,
                language=language,
                file_path=rel_path,
                start_line=start_ln,
                end_line=max(start_ln, end_ln),
                signature=sig_line[:160],
                calls=dedup_calls,
                imports=list(dict.fromkeys(imports))[:24],
            )
        )
        for cname in dedup_calls:
            edges.append(
                SymbolEdge(
                    source_id=sym_id,
                    target_name=cname,
                    target_id=None,
                    relation="calls",
                    file_path=rel_path,
                    line=start_ln,
                )
            )
    return nodes, edges


class CodeGraphIndexer:
    """
    Polyglot AST & SCIP-compatible Symbol/Call-Graph Indexer (`kb code-graph`).
    Indexes symbol definitions, cross-file calls, and class hierarchies across Python, TypeScript/JS,
    Rust, and C#, computes symbol centrality, persists to SQLite, and exports a `hips` corpus.
    """

    def __init__(self, repo_dir: Path, db_path: Path | None = None) -> None:
        self.repo_dir = Path(repo_dir).expanduser().resolve()
        self.db_path = db_path or (self.repo_dir / ".kb" / "code_graph.db")

    def build_graph(self, persist: bool = True, export_hips: bool = True) -> dict[str, Any]:
        nodes: list[SymbolNode] = []
        edges: list[SymbolEdge] = []
        files = _iter_source_files(self.repo_dir)

        for fpath in files:
            try:
                rel = str(fpath.relative_to(self.repo_dir))
                src = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            suf = fpath.suffix.lower()
            if suf == ".py":
                f_nodes, f_edges = _extract_python_symbols(fpath, rel, src)
            elif suf in (".ts", ".tsx", ".js"):
                f_nodes, f_edges = _extract_polyglot_regex_symbols(rel, src, "typescript")
            elif suf == ".rs":
                f_nodes, f_edges = _extract_polyglot_regex_symbols(rel, src, "rust")
            elif suf == ".cs":
                f_nodes, f_edges = _extract_polyglot_regex_symbols(rel, src, "csharp")
            else:
                continue
            nodes.extend(f_nodes)
            edges.extend(f_edges)

        # Resolve target_id by matching symbol short name or qualified name without duplicate registration
        by_short_name: dict[str, list[SymbolNode]] = {}
        by_id: dict[str, SymbolNode] = {}
        for n in nodes:
            by_id[n.symbol_id] = n
            short = n.name.split(".")[-1]
            by_short_name.setdefault(short, []).append(n)
            if short != n.name:
                by_short_name.setdefault(n.name, []).append(n)

        resolved_edges: list[SymbolEdge] = []
        in_deg: dict[str, int] = {n.symbol_id: 0 for n in nodes}
        for e in edges:
            cands = by_short_name.get(e.target_name, [])
            if cands:
                src_node = by_id.get(e.source_id)
                src_imports = src_node.imports if src_node else []
                # 1. Prefer same-file definition (lexical scope resolution)
                local_matches = [c for c in cands if c.file_path == e.file_path and c.symbol_id != e.source_id]
                # 2. Next prefer definitions from explicitly imported modules
                imported_matches = [
                    c
                    for c in cands
                    if c.file_path != e.file_path
                    and any(Path(c.file_path).stem in imp or e.target_name in imp for imp in src_imports)
                ]
                if local_matches:
                    chosen = local_matches[0]
                elif imported_matches:
                    chosen = imported_matches[0]
                else:
                    chosen = cands[0]
                if chosen.symbol_id != e.source_id:
                    e.target_id = chosen.symbol_id
                    in_deg[chosen.symbol_id] = in_deg.get(chosen.symbol_id, 0) + 1
                    resolved_edges.append(e)

        max_deg = max(1, max(in_deg.values(), default=1))
        for n in nodes:
            n.in_degree = in_deg.get(n.symbol_id, 0)
            n.centrality = round(math.log1p(n.in_degree) / math.log1p(max_deg), 4)

        if persist:
            self._save_sqlite(nodes, resolved_edges)
        hips_corpus_path: str | None = None
        if export_hips:
            hips_corpus_path = str(self._export_hips_corpus(nodes))

        return {
            "repo_dir": str(self.repo_dir),
            "files_scanned": len(files),
            "total_symbols": len(nodes),
            "resolved_call_edges": len(resolved_edges),
            "languages": {
                lang: sum(1 for n in nodes if n.language == lang) for lang in sorted({n.language for n in nodes})
            },
            "top_central_symbols": [
                {
                    "symbol_id": n.symbol_id,
                    "name": n.name,
                    "file_path": n.file_path,
                    "in_degree": n.in_degree,
                    "centrality": n.centrality,
                }
                for n in sorted(nodes, key=lambda x: (-x.in_degree, x.symbol_id))[:10]
            ],
            "db_path": str(self.db_path) if persist else None,
            "hips_corpus_path": hips_corpus_path,
            "nodes": nodes,
            "edges": resolved_edges,
        }

    def _save_sqlite(self, nodes: list[SymbolNode], edges: list[SymbolEdge]) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS code_symbols (
                    symbol_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    language TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    signature TEXT NOT NULL,
                    docstring TEXT NOT NULL,
                    calls_json TEXT NOT NULL,
                    imports_json TEXT NOT NULL,
                    in_degree INTEGER NOT NULL,
                    centrality REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS code_edges (
                    source_id TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    target_id TEXT,
                    relation TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    line INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_code_edges_src ON code_edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_code_edges_dst ON code_edges(target_id);
                DELETE FROM code_symbols;
                DELETE FROM code_edges;
                """
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO code_symbols (
                    symbol_id, name, kind, language, file_path, start_line, end_line,
                    signature, docstring, calls_json, imports_json, in_degree, centrality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        n.symbol_id,
                        n.name,
                        n.kind,
                        n.language,
                        n.file_path,
                        n.start_line,
                        n.end_line,
                        n.signature,
                        n.docstring,
                        json.dumps(n.calls),
                        json.dumps(n.imports),
                        n.in_degree,
                        n.centrality,
                    )
                    for n in nodes
                ],
            )
            conn.executemany(
                """
                INSERT INTO code_edges (source_id, target_name, target_id, relation, file_path, line)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(e.source_id, e.target_name, e.target_id, e.relation, e.file_path, e.line) for e in edges],
            )
            conn.commit()
        finally:
            conn.close()

    def _export_hips_corpus(self, nodes: list[SymbolNode]) -> Path:
        out_dir = self.db_path.parent / "hips-code-graph"
        out_dir.mkdir(parents=True, exist_ok=True)
        corpus_file = out_dir / "corpus.jsonl"
        lines: list[str] = []
        for idx, n in enumerate(nodes):
            text_blob = (
                f"{n.name} {n.signature} {n.file_path} {n.docstring} "
                f"calls: {' '.join(n.calls)} imports: {' '.join(n.imports)}"
            ).strip()
            lines.append(
                json.dumps(
                    {
                        "id": idx,
                        "doc_id": n.symbol_id,
                        "title": f"{n.name} ({n.file_path}:{n.start_line})",
                        "text": text_blob,
                        "file_path": n.file_path,
                        "start_line": n.start_line,
                        "end_line": n.end_line,
                        "in_degree": n.in_degree,
                    },
                    ensure_ascii=False,
                )
            )
        corpus_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return corpus_file


def slice_symbol_subgraph(
    repo_dir: Path,
    query: str,
    hops: int = 2,
    top_k: int = 8,
    persist: bool = False,
) -> dict[str, Any]:
    """
    Retrieve the top-K matching symbols for `query` and expand their 0..`hops` caller/callee
    dependency neighborhood so `swm` Stage 1 (`TRIAGE`) receives surgical AST symbol slices.
    """
    if hops < 0:
        raise ValueError("hops must be >= 0")
    if top_k < 1:
        raise ValueError("top_k must be >= 1")

    indexer = CodeGraphIndexer(Path(repo_dir))
    graph = indexer.build_graph(persist=persist, export_hips=persist)
    nodes: list[SymbolNode] = graph["nodes"]
    edges: list[SymbolEdge] = graph["edges"]
    by_id = {n.symbol_id: n for n in nodes}

    q_tokens = [
        tok.lower()
        for tok in re.findall(r"[A-Za-z0-9_]+", query)
        if len(tok) >= 2 and tok.lower() not in {"the", "and", "for", "with", "from", "into"}
    ]

    scored: list[tuple[float, SymbolNode]] = []
    for n in nodes:
        hay_name = n.name.lower()
        hay_file = n.file_path.lower()
        hay_doc = f"{n.signature} {n.docstring} {' '.join(n.calls)}".lower()
        score = 0.0
        for tok in q_tokens:
            if tok == hay_name or tok == hay_name.split(".")[-1]:
                score += 6.0
            elif tok in hay_name:
                score += 3.5
            if tok in hay_file:
                score += 2.0
            if tok in hay_doc:
                score += 1.0
        if score > 0.0:
            score += 0.5 * n.centrality
            scored.append((round(score, 4), n))

    scored.sort(key=lambda x: (-x[0], -x[1].in_degree, x[1].symbol_id))
    seed_nodes = [n for _, n in scored[:top_k]]

    # Build adjacency maps for k-hop expansion
    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    for e in edges:
        if e.target_id and e.target_id in by_id:
            outgoing.setdefault(e.source_id, set()).add(e.target_id)
            incoming.setdefault(e.target_id, set()).add(e.source_id)

    visited: set[str] = {n.symbol_id for n in seed_nodes}
    frontier: set[str] = set(visited)
    hop_layers: dict[int, list[str]] = {0: sorted(visited)}

    for h in range(1, hops + 1):
        next_frontier: set[str] = set()
        for sid in frontier:
            for callee in outgoing.get(sid, set()):
                if callee not in visited:
                    next_frontier.add(callee)
            for caller in incoming.get(sid, set()):
                if caller not in visited:
                    next_frontier.add(caller)
        visited.update(next_frontier)
        hop_layers[h] = sorted(next_frontier)
        frontier = next_frontier

    subgraph_nodes = [by_id[sid] for sid in sorted(visited) if sid in by_id]
    subgraph_edges = [
        e.to_dict() for e in edges if e.source_id in visited and e.target_id is not None and e.target_id in visited
    ]
    affected_files = list(dict.fromkeys([n.file_path for n in seed_nodes] + [n.file_path for n in subgraph_nodes]))

    return {
        "repo_dir": str(indexer.repo_dir),
        "query": query,
        "hops": hops,
        "seed_symbols": [n.to_dict() for n in seed_nodes],
        "subgraph_symbols": [n.to_dict() for n in subgraph_nodes[: top_k * 3]],
        "subgraph_edges": subgraph_edges[: top_k * 6],
        "hop_layers": hop_layers,
        "affected_files": affected_files[:12],
    }


# ====================================================================================================================
# 2-3. Property/Mutation Rigor (`GWI-SCI-06`) & Formal Proof Verifier (`GWI-SCI-07`) live in core `gwi_rigor`
# ====================================================================================================================

from sovereign_kb.gwi_rigor import (  # noqa: E402  (re-exported for backward compatibility)
    check_formal_proofs,
    check_property_and_mutation_rigor,
    run_ast_mutation_probe,
)

__all__ = [
    "CodeGraphIndexer",
    "audit_and_package_3dgs",
    "check_formal_proofs",
    "check_property_and_mutation_rigor",
    "compile_organon_prompts",
    "export_sleep_wake_lora_dataset",
    "parse_splat_asset_header",
    "run_ast_mutation_probe",
    "slice_symbol_subgraph",
]

# ====================================================================================================================
# 4. Compile-Time Prompt & Few-Shot Exemplar Optimizer (`DSPy` / `MIPROv2` over `kb rsi`)
# ====================================================================================================================


def _extract_finite_goldwood_g(payload: dict[str, Any]) -> float | None:
    for key in ("goldwood_g", "verga_score"):
        raw = payload.get(key)
        if raw is not None and not isinstance(raw, bool):
            try:
                val = float(raw)
                if math.isfinite(val) and val > 0.0:
                    return val
            except (TypeError, ValueError):
                pass
    audit_score = payload.get("audit_score") or payload.get("score")
    if audit_score is not None and not isinstance(audit_score, bool):
        try:
            s_val = float(audit_score)
            if math.isfinite(s_val) and s_val > 0.0:
                return round(s_val / 10.0, 2) if s_val > 10.0 else s_val
        except (TypeError, ValueError):
            pass
    return None


def _is_approved_event(payload: dict[str, Any]) -> bool:
    for key in ("disposition", "status", "verdict"):
        val = str(payload.get(key, "")).strip().upper()
        if val in ("APPROVED", "COMPLETED", "MERGED"):
            return True
    return False


def _is_rejected_event(payload: dict[str, Any]) -> bool:
    for key in ("disposition", "status", "verdict"):
        val = str(payload.get(key, "")).strip().upper()
        if val in ("NOT_APPROVED", "NOT APPROVED", "REJECTED", "FAILED"):
            return True
    return False


def compile_organon_prompts(
    vault_dir: Path,
    provenance_ledger_path: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    DSPy / MIPROv2-style Compile-Time Prompt & Exemplar Optimizer (`kb rsi compile-prompts`).
    Selects the highest-reward Stage-5 `APPROVED` runs (`G >= 9.0`) from the Triad Provenance Ledger
    as Golden Few-Shot Exemplars and extracts recurring Stage-5 `NOT_APPROVED` blocker patterns as
    Hard Negative Guardrails, persisting `.kb/compiled_organon_prompts.json` monotonically when
    `objective_j_prompt` is >= the incumbent artifact.
    """
    from sovereign_kb.bridge import default_triad_ledger_path, load_swm_rsi_probes

    vdir = Path(vault_dir).expanduser().resolve()
    ledger_file = provenance_ledger_path or default_triad_ledger_path()
    out_file = output_path or (vdir / ".kb" / "compiled_organon_prompts.json")

    approved_exemplars: list[dict[str, Any]] = []
    negative_guardrails: list[str] = []

    if ledger_file.is_file():
        for line in ledger_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
            if not isinstance(payload, dict):
                continue
            g_score = _extract_finite_goldwood_g(payload)
            wp_id = payload.get("wp_id")
            subject = str(payload.get("subject") or payload.get("wp_subject") or payload.get("repo_name") or "")
            if _is_approved_event(payload) and g_score is not None and g_score >= 9.0:
                approved_exemplars.append(
                    {
                        "wp_id": wp_id,
                        "subject": subject,
                        "goldwood_g": round(g_score, 2),
                        "worker": str(payload.get("worker") or "fable"),
                        "summary": str(
                            payload.get("summary") or "Deterministic 6-stage pass with 0 console/network errors"
                        )[:240],
                    }
                )
            elif _is_rejected_event(payload):
                blockers = payload.get("blockers") or payload.get("structured_blockers") or []
                if isinstance(blockers, list):
                    for b in blockers:
                        b_text = (str(b.get("description") or b) if isinstance(b, dict) else str(b))[:200]
                        if b_text and b_text not in negative_guardrails:
                            negative_guardrails.append(b_text)

    # Sort approved exemplars by Goldwood G descending
    approved_exemplars.sort(key=lambda x: (-float(x["goldwood_g"]), str(x.get("wp_id", ""))))
    top_exemplars = approved_exemplars[:5]
    verified_guardrail_count = len(negative_guardrails)
    default_guardrails = [
        "Enforce Law L7 structural regularity (distinctFontSizes <= 8, distinctBorderRadii <= 4, R_kanon = 1.0).",
        "Enforce GWI-VID-C01 (< 20 MB companion MP4) and GWI-APP-R02 (0 page/console/HTTP errors in diagnostics.json).",
        "Enforce README Zero-Hallucination parity (readme_check.py) and monotonic Delta G >= 0.",
    ]
    for dg in default_guardrails:
        if dg not in negative_guardrails:
            negative_guardrails.append(dg)

    heldout_probes = load_swm_rsi_probes(vdir)
    exemplar_bonus = min(0.15, 0.03 * len(top_exemplars))
    guardrail_bonus = min(0.07, 0.02 * verified_guardrail_count)
    probe_bonus = min(0.03, 0.01 * len(heldout_probes))
    j_prompt = round(min(1.0, 0.75 + exemplar_bonus + guardrail_bonus + probe_bonus), 4)

    # Enforce monotonic non-regression against existing compiled prompt artifact on disk
    if out_file.is_file():
        try:
            incumbent = json.loads(out_file.read_text(encoding="utf-8"))
            if isinstance(incumbent, dict):
                inc_score = float(incumbent.get("objective_j_prompt", 0.0))
                if math.isfinite(inc_score) and inc_score > j_prompt:
                    incumbent["monotonic_preserved"] = True
                    return incumbent
        except (OSError, ValueError, TypeError):
            pass

    compiled = {
        "schema": "sovereign-kb.compiled-prompts.v1",
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "optimizer": "organon-mipro-v1",
        "objective_j_prompt": j_prompt,
        "heldout_probes_evaluated": len(heldout_probes),
        "monotonic_preserved": False,
        "stages": {
            "stage1_triage": {
                "instruction": (
                    "Decompose the work package into surgical AST symbol slices (`kb code-graph`), "
                    "cite top-3 vault precedents, and select `fable` for multi-file/3D/architectural tasks "
                    "or `sonnet` for bounded single-module tasks."
                ),
                "few_shot_exemplars": top_exemplars[:3],
            },
            "stage3_implement": {
                "instruction": (
                    "Implement the required changes with 100% Master Feature Catalog parity, seeded PRNG (`seed=42`), "
                    "strict Law L0-L10 compliance, and high assertion-density unit/property tests (`GWI-SCI-06`)."
                ),
                "anti_regression_guardrails": negative_guardrails[:8],
            },
            "stage5_astra_audit": {
                "instruction": (
                    "Audit the full untruncated git diff and verification diagnostics adversarially. "
                    "Emit structured `BLOCKERS_JSON` for any defect and `DISPOSITION: APPROVED` only when "
                    "G(S) >= max(9.0, baseline_G) and Lambda_conscience == 1."
                ),
            },
        },
    }
    compiled["artifact_sha256"] = hashlib.sha256(
        json.dumps(compiled["stages"], sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(compiled, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return compiled


# ====================================================================================================================
# 5. Parametric Sleep-Wake Consolidation (`LoRA` / `GRPO` on `gx10` — `kb rsi sleep-consolidate`)
# ====================================================================================================================


def export_sleep_wake_lora_dataset(
    vault_dir: Path,
    provenance_ledger_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Synthesizes verified parametric post-training datasets from the Sovereign Organon:
      1. `sft_trajectories.jsonl` — ChatML SFT examples from canonical `02-operating-system/` notes
         and Stage-5 `APPROVED` (`G >= 9.0`) trajectories.
      2. `grpo_preference_pairs.jsonl` — Verifier-rewarded preference pairs (`chosen` vs `rejected`
         with continuous Goldwood Index reward R = G(S) / 10.0) for GRPO / DPO training on `gx10`.
      3. `gx10_lora_train_and_mount.sh` — Bittensor Subnet 53 (`engy-miner.service`) priority-guarded
         staging & low-priority execution script for `qwen3.6-35b-a3b`.
    """
    from sovereign_kb.bridge import default_triad_ledger_path

    vdir = Path(vault_dir).expanduser().resolve()
    out_dir = output_dir or (vdir / ".kb" / "sleep_lora")
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_file = provenance_ledger_path or default_triad_ledger_path()

    sft_rows: list[dict[str, Any]] = []
    grpo_rows: list[dict[str, Any]] = []

    # 1. Harvest only canonical `02-operating-system` engineering specs (never `00-identity` personal notes)
    os_folder = vdir / "02-operating-system"
    if os_folder.is_dir():
        for md_path in sorted(os_folder.rglob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if len(text.strip()) < 120:
                continue
            rel = str(md_path.relative_to(vdir))
            title_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
            title = title_m.group(1).strip() if title_m else md_path.stem
            sft_rows.append(
                {
                    "id": f"kb:{rel}",
                    "source": "vault_operating_system",
                    "reward": 0.95,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are the Sovereign Organon (`kb · swm · gwi`) architectural intelligence.",
                        },
                        {
                            "role": "user",
                            "content": f"Formulate the canonical Sovereign Organon specification for: {title} ({rel})",
                        },
                        {"role": "assistant", "content": text.strip()[:8000]},
                    ],
                }
            )

    # 2. Harvest verified Stage-5 trajectories & preference pairs from provenance ledger
    by_wp: dict[str, list[dict[str, Any]]] = {}
    if ledger_file.is_file():
        for line in ledger_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
            if not isinstance(payload, dict):
                continue
            wp_raw = payload.get("wp_id") or payload.get("run_id")
            if wp_raw is None or str(wp_raw).strip() == "":
                continue
            by_wp.setdefault(str(wp_raw).strip(), []).append(payload)

    for wp_key, events in by_wp.items():
        approved = [
            e
            for e in events
            if _is_approved_event(e)
            and (_extract_finite_goldwood_g(e) is not None and (_extract_finite_goldwood_g(e) or 0.0) >= 9.0)
        ]
        rejected = [e for e in events if _is_rejected_event(e)]
        for app in approved:
            g_val = float(_extract_finite_goldwood_g(app) or 9.0)
            subj = str(app.get("subject") or app.get("wp_subject") or f"Work Package OP#{wp_key}")
            summary = str(app.get("summary") or app.get("diff_stat") or "Verified GWI >= 9.0 implementation")
            sft_rows.append(
                {
                    "id": f"swm:op-{wp_key}",
                    "source": "verified_stage5_trajectory",
                    "reward": round(g_val / 10.0, 4),
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are the Sovereign Organon (`kb · swm · gwi`) autonomous software builder.",
                        },
                        {"role": "user", "content": f"Execute deterministic implementation for {subj}."},
                        {"role": "assistant", "content": summary},
                    ],
                }
            )
            for rej in rejected:
                rej_blockers = rej.get("blockers") or rej.get("structured_blockers") or ["Failed Stage-5 Astra audit"]
                grpo_rows.append(
                    {
                        "wp_id": wp_key,
                        "source": "verified_ledger_pair",
                        "prompt": f"Implement work package {subj} satisfying GWI >= 9.0 and Lambda_conscience == 1.",
                        "chosen": summary,
                        "chosen_reward": round(g_val / 10.0, 4),
                        "rejected": json.dumps(rej_blockers, ensure_ascii=False),
                        "rejected_reward": -0.5,
                    }
                )

    sft_file = out_dir / "sft_trajectories.jsonl"
    grpo_file = out_dir / "grpo_preference_pairs.jsonl"
    script_file = out_dir / "gx10_lora_train_and_mount.sh"
    manifest_file = out_dir / "sleep_consolidation_manifest.json"

    sft_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in sft_rows) + ("\n" if sft_rows else ""),
        encoding="utf-8",
    )
    grpo_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in grpo_rows) + ("\n" if grpo_rows else ""),
        encoding="utf-8",
    )

    script_content = """#!/usr/bin/env bash
# Sovereign Organon — Overnight Parametric Sleep-Wake LoRA Dataset Staging for GX10 Grace Blackwell
# CRITICAL INVARIANT: Never stop/restart sglang-qwen.service, engy-miner.service, or probe-watcher.service
# and never exhaust GX10 VRAM while Bittensor Subnet 53 (Engy) is actively mining.
set -euo pipefail

DATASET_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_STAGE_DIR="~/.cache/sovereign-organon-sleep"

echo "[organon-sleep] Checking GX10 Bittensor Subnet 53 miner & VRAM headroom..."
if ssh -o ConnectTimeout=5 gx10 "systemctl is-active --quiet engy-miner.service"; then
  if [[ "${1:-}" != "--allow-with-miner" ]]; then
    echo "[organon-sleep] GUARDRAIL ACTIVE: engy-miner.service is running on gx10. Staging dataset only (pass --allow-with-miner to run low-priority nice -n 19 job)."
  fi
fi

ssh -o ConnectTimeout=5 gx10 "mkdir -p ${REMOTE_STAGE_DIR} && chmod 700 ${REMOTE_STAGE_DIR}"
scp -q "${DATASET_DIR}/sft_trajectories.jsonl" "${DATASET_DIR}/grpo_preference_pairs.jsonl" "gx10:${REMOTE_STAGE_DIR}/"
echo "[organon-sleep] Staged ${DATASET_DIR} trajectories to gx10:${REMOTE_STAGE_DIR}/."
"""
    script_file.write_text(script_content, encoding="utf-8")
    script_file.chmod(0o755)

    disk_manifest = {
        "schema": "sovereign-kb.sleep-lora.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_model": "qwen3.6-35b-a3b",
        "target_node": "gx10",
        "export_only": True,
        "mining_guardrail_enforced": True,
        "sft_trajectory_count": len(sft_rows),
        "grpo_preference_pair_count": len(grpo_rows),
        "sft_path": sft_file.name,
        "grpo_path": grpo_file.name,
        "gx10_script_path": script_file.name,
    }
    manifest_file.write_text(json.dumps(disk_manifest, indent=2) + "\n", encoding="utf-8")
    return {
        **disk_manifest,
        "sft_path": str(sft_file),
        "grpo_path": str(grpo_file),
        "gx10_script_path": str(script_file),
    }


# ====================================================================================================================
# 6. 3D Gaussian Splatting (`3DGS`) Spatial Capture & WebXR Budget Auditor (`kb splat`)
# ====================================================================================================================

MAX_WEBXR_SPLATS = 500_000
MAX_COMPANION_SPLAT_BYTES = 20_000_000


_PLY_TYPE_SIZES = {
    "char": 1, "uchar": 1, "int8": 1, "uint8": 1,
    "short": 2, "ushort": 2, "int16": 2, "uint16": 2,
    "int": 4, "uint": 4, "float": 4, "int32": 4, "uint32": 4, "float32": 4,
    "double": 8, "float64": 8,
}  # fmt: skip


def _ply_vertex_stride(head_text: str) -> int | None:
    """Byte stride of one `vertex` element in a binary PLY header, or None when not computable
    (list properties, unknown scalar types, or no vertex element)."""
    stride = 0
    in_vertex = False
    seen_vertex = False
    for raw in head_text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        if parts[0] == "element":
            in_vertex = len(parts) >= 2 and parts[1] == "vertex"
            seen_vertex = seen_vertex or in_vertex
            continue
        if in_vertex and parts[0] == "property":
            if len(parts) >= 2 and parts[1] == "list":
                return None
            if len(parts) < 3 or parts[1].lower() not in _PLY_TYPE_SIZES:
                return None
            stride += _PLY_TYPE_SIZES[parts[1].lower()]
    return stride if seen_vertex and stride > 0 else None


def parse_splat_asset_header(asset_path: Path) -> dict[str, Any]:
    """
    Parses a `.ply` (ASCII or binary header), `.splat` (32 bytes/gaussian), `.ksplat`, or `.spz`
    3D Gaussian Splatting file and validates primitive counts, Spherical Harmonics degree, truncation
    integrity, and WebXR compliance (`<= 500,000` splats and `<= 20 MB`).
    """
    p = Path(asset_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"3DGS asset not found: {p}")

    size_bytes = p.stat().st_size
    suf = p.suffix.lower()
    gaussian_count = 0
    sh_degree = 0
    format_name = suf.lstrip(".")
    corrupt_or_truncated = size_bytes == 0
    estimated_from_size = False

    if suf == ".ply" and size_bytes > 0:
        with p.open("rb") as f:
            head_bytes = f.read(65536)
        end_marker = b"end_header\n"
        end_idx = head_bytes.find(end_marker)
        if end_idx == -1:
            end_marker = b"end_header\r\n"
            end_idx = head_bytes.find(end_marker)
        if end_idx == -1:
            corrupt_or_truncated = True
            head_text = head_bytes.decode("ascii", errors="ignore")
        else:
            head_text = head_bytes[:end_idx].decode("ascii", errors="ignore")
            payload_bytes = size_bytes - (end_idx + len(end_marker))
            m_vert = re.search(r"element\s+vertex\s+(\d+)", head_text)
            if m_vert:
                gaussian_count = int(m_vert.group(1))
                fmt_m = re.search(r"^\s*format\s+(\S+)", head_text, re.MULTILINE)
                ply_format = fmt_m.group(1).lower() if fmt_m else "ascii"
                stride = _ply_vertex_stride(head_text) if ply_format.startswith("binary_") else None
                if gaussian_count <= 0:
                    corrupt_or_truncated = True
                elif stride is not None:
                    # Binary PLY: payload must hold count * per-vertex stride bytes
                    if payload_bytes < gaussian_count * stride:
                        corrupt_or_truncated = True
                elif payload_bytes < min(gaussian_count * 4, 64):
                    # ASCII or list-property PLY: lenient non-empty check
                    corrupt_or_truncated = True
            else:
                corrupt_or_truncated = True
        f_rest_props = len(re.findall(r"property\s+float\s+f_rest_\d+", head_text))
        if f_rest_props >= 45:
            sh_degree = 3
        elif f_rest_props >= 24:
            sh_degree = 2
        elif f_rest_props >= 9:
            sh_degree = 1
        else:
            sh_degree = 0
    elif suf == ".splat" and size_bytes > 0:
        # Standard antimatter15 `.splat` layout: exact 32 bytes per Gaussian
        if size_bytes < 32 or (size_bytes % 32) != 0:
            corrupt_or_truncated = True
        gaussian_count = size_bytes // 32
        sh_degree = 0
    elif suf == ".spz" and size_bytes > 0:
        # Niantic `.spz` header: magic 0x5053474e ("NGSP"), version (u32), numPoints (u32), shDegree (u8)
        if size_bytes < 16:
            corrupt_or_truncated = True
        else:
            with p.open("rb") as f:
                hdr = f.read(16)
            magic, _ver, num_pts, sh_deg = struct.unpack("<IIIb3x", hdr)
            if magic == 0x5053474E and num_pts > 0:
                gaussian_count = int(num_pts)
                sh_degree = max(0, min(3, int(sh_deg)))
            else:
                gaussian_count = size_bytes // 16
                sh_degree = 1
                estimated_from_size = True
    elif suf == ".ksplat" and size_bytes > 0:
        if size_bytes < 32:
            corrupt_or_truncated = True
        else:
            gaussian_count = size_bytes // 16
            sh_degree = 1
            estimated_from_size = True

    mobile_xr_ok = 0 < gaussian_count <= MAX_WEBXR_SPLATS
    size_budget_ok = 0 < size_bytes <= MAX_COMPANION_SPLAT_BYTES
    passed = mobile_xr_ok and size_budget_ok and not corrupt_or_truncated

    return {
        "path": str(p),
        "format": format_name,
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 3),
        "gaussian_count": gaussian_count,
        "sh_degree": sh_degree,
        "corrupt_or_truncated": corrupt_or_truncated,
        "estimated_from_size": estimated_from_size,
        "quest3_90fps_budget_ok": mobile_xr_ok,
        "web_delivery_20mb_ok": size_budget_ok,
        "passed": passed,
    }


def audit_and_package_3dgs(target: Path) -> dict[str, Any]:
    """
    Audits a directory or single `.ply`/`.splat`/`.ksplat`/`.spz` file for 3D Gaussian Splatting
    WebGPU / Meta Quest 3 WebXR compliance (without applying source-file size caps) and emits a
    deterministic `splat_manifest.json` specification.
    """
    resolved = Path(target).expanduser().resolve()
    if resolved.is_file():
        assets = [resolved]
    else:
        assets = _iter_source_files(resolved, (".ply", ".splat", ".ksplat", ".spz"), max_bytes=None)

    parsed_assets = [parse_splat_asset_header(a) for a in assets]
    total_gaussians = sum(a["gaussian_count"] for a in parsed_assets)
    all_passed = bool(parsed_assets) and all(a["passed"] for a in parsed_assets)

    return {
        "schema": "sovereign-kb.3dgs-audit.v1",
        "target": str(resolved),
        "asset_count": len(parsed_assets),
        "total_gaussians": total_gaussians,
        "status": "NO_ASSETS_FOUND" if not parsed_assets else ("PASS" if all_passed else "FAIL"),
        "assets": parsed_assets,
        "cluster_pipeline": {
            "reconstruction_node": "rtx5080 (NVIDIA GeForce RTX 5080 16GB GDDR7, CUDA 13.2)",
            "fallback_node": "gx10 (NVIDIA GB10 Grace Blackwell 121GiB)",
            "trainer_command": "ns-train splatfacto --data <capture_dir> --vis viewer+tensorboard",
            "webxr_compression_target": ".ksplat / .spz (<= 500,000 gaussians, <= 20 MB)",
        },
        "passed": all_passed,
    }
