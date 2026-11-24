"""6-gate deterministic vault and skills verification command (`cmd_verify`)."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from .. import install_check
from .config_db import get_db, resolve_hips_bin, resolve_vault_dir
from .markdown_embed import (
    _is_vault_no_embed,
    build_stem_lookup,
    extract_wikilinks,
    get_embedding,
    list_vault_md_files,
    parse_frontmatter,
)
from .retrieval_notes import call_llm
from .sync_hips import _is_index_stale, _verify_fts_index, cmd_sync


def cmd_verify(args, no_auto_sync: bool = False) -> None:
    """
    6-Gate Deterministic Vault & Environment Doctor, preceded by a check of the doctor itself:
      Gate 0: Tool Install Freshness (an installed copy whose source checkout has newer commits fails;
              uncommitted edits there or an unverifiable copy only warn; see `sovereign_kb.install_check`)
      Gate 1: Frontmatter Schema & Date Freshness
      Gate 2: Wikilink Integrity (0 broken [[wikilinks]])
      Gate 3: Graph Connectivity (0 unintended orphan notes)
      Gate 4: SQLite FTS5, HIPS Block-Max WAND & Vector Embedding Completeness (+ RSI Ledger Chain)
      Gate 5: Git & Binary Hygiene (0 tracked .mp4/.pyc files)
      Gate 6: Agent Skills & Zero-Auth Plugins Frontmatter Integrity (valid YAML frontmatter & schemas)
    """
    vault_dir = resolve_vault_dir(getattr(args, "vault", None), must_exist=True)
    allow_lexical = _is_vault_no_embed(vault_dir, getattr(args, "allow_lexical_only", False))
    if getattr(args, "heal", False):
        from sovereign_kb.rsi import heal_and_consolidate_vault

        heal_rep = heal_and_consolidate_vault(
            vault_dir=vault_dir,
            list_md_fn=list_vault_md_files,
            parse_fm_fn=parse_frontmatter,
            extract_links_fn=extract_wikilinks,
            build_stem_fn=build_stem_lookup,
            sync_fn=cmd_sync,
            call_llm_fn=call_llm,
            use_llm=False,
            no_embed=allow_lexical,
            consolidate_episodic=False,
        )
        if not getattr(args, "json", False):
            healed = list(
                dict.fromkeys(
                    list(heal_rep.get("healed_frontmatter") or [])
                    + list(heal_rep.get("healed_orphans") or [])
                    + (["INDEX.md"] if heal_rep.get("index_created") else [])
                )
            )
            if healed:
                print(f"[skb verify --heal] Repaired {len(healed)} file(s): {', '.join(healed)}")
            else:
                print("[skb verify --heal] No repairs needed.")

    conn = get_db(vault_dir)
    md_files = list_vault_md_files(vault_dir)
    stem_lookup = build_stem_lookup(vault_dir, md_files)
    install, install_violations, warnings = install_check.verify_gate()
    violations: list[dict] = list(install_violations)

    inbound_counts: dict[str, int] = {str(p.relative_to(vault_dir)): 0 for p in md_files}
    for p in md_files:
        rel = str(p.relative_to(vault_dir))
        text = p.read_text(encoding="utf-8", errors="replace")
        meta = parse_frontmatter(text)
        if rel not in ("AGENTS.md", "README.md"):
            if meta.get("yaml_error"):
                violations.append({"gate": "Gate 1 (Frontmatter)", "file": rel, "issue": str(meta["yaml_error"])})
            for req_key in ("title", "type", "updated", "tags"):
                if not meta.get(req_key):
                    violations.append(
                        {"gate": "Gate 1 (Frontmatter)", "file": rel, "issue": f"Missing frontmatter field '{req_key}'"}
                    )

        for target in extract_wikilinks(text, stem_lookup, rel):
            if target not in inbound_counts:
                violations.append(
                    {"gate": "Gate 2 (Wikilinks)", "file": rel, "issue": f"Broken wikilink target '{target}'"}
                )
            elif target != rel:
                inbound_counts[target] += 1

    for rel, count in inbound_counts.items():
        if rel in ("INDEX.md", "AGENTS.md", "README.md"):
            continue
        if count == 0:
            violations.append(
                {"gate": "Gate 3 (Graph Connectivity)", "file": rel, "issue": "Orphan note (0 inbound wikilinks)"}
            )

    needs_auto_sync = _is_index_stale(conn, vault_dir, md_files)
    if needs_auto_sync:
        if not (no_auto_sync or getattr(args, "no_auto_sync", False)):
            conn.close()
            cmd_sync(
                argparse.Namespace(
                    vault=str(vault_dir),
                    force=False,
                    no_embed=allow_lexical,
                    quiet=getattr(args, "json", False),
                )
            )
            conn = get_db(vault_dir)
            needs_auto_sync = _is_index_stale(conn, vault_dir, md_files)
        if needs_auto_sync:
            violations.append(
                {
                    "gate": "Gate 4 (Index Sync)",
                    "file": ".kb/index.db",
                    "issue": "Index mtime is stale relative to vault Markdown files on disk; run 'sovereign-kb sync'",
                }
            )

    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    eligible_vec_count = conn.execute("SELECT COUNT(*) FROM chunks WHERE is_vector_eligible = 1").fetchone()[0]
    vec_count = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE is_vector_eligible = 1 AND embedding IS NOT NULL"
    ).fetchone()[0]
    hips_idx_dir = vault_dir / ".kb" / "hips-index"
    hips_ready = hips_idx_dir.is_dir() and any(hips_idx_dir.iterdir())
    hips_bin = resolve_hips_bin()
    require_embed = getattr(args, "require_embeddings", False)
    if doc_count != len(md_files):
        violations.append(
            {
                "gate": "Gate 4 (Index Sync)",
                "file": ".kb/index.db",
                "issue": f"Indexed document count ({doc_count}) != vault markdown file count ({len(md_files)}); run 'sovereign-kb sync'",
            }
        )
    if vault_dir.name == "me-kb" and hips_bin and not hips_ready:
        violations.append(
            {
                "gate": "Gate 4 (HIPS Index)",
                "file": ".kb/hips-index",
                "issue": "HIPS Block-Max WAND index is missing; run 'kb sync' or 'kb hips index'",
            }
        )
    if chunk_count == 0:
        violations.append(
            {
                "gate": "Gate 4 (Embeddings)",
                "file": ".kb/index.db",
                "issue": "Zero indexed chunks; run 'sovereign-kb sync'",
            }
        )
    elif not allow_lexical and vec_count < eligible_vec_count:
        # Fail if partial embeddings exist (0 < vec_count < eligible_vec_count), or if --require-embeddings is set,
        # or if an embedding server is currently reachable online.
        if vec_count > 0 or require_embed or get_embedding("ping") is not None:
            violations.append(
                {
                    "gate": "Gate 4 (Embeddings)",
                    "file": ".kb/index.db",
                    "issue": f"Incomplete vector embeddings ({vec_count}/{eligible_vec_count} eligible chunks); run 'sovereign-kb sync'",
                }
            )
    try:
        _verify_fts_index(conn)
        conn.commit()
    except sqlite3.DatabaseError as e:
        violations.append({"gate": "Gate 4 (FTS5 Integrity)", "file": ".kb/index.db", "issue": str(e)})

    if (vault_dir / ".kb" / "rsi-ledger.jsonl").is_file():
        from sovereign_kb.rsi import verify_ledger_chain

        chain_ok, _ = verify_ledger_chain(vault_dir)
        if not chain_ok:
            violations.append(
                {
                    "gate": "Gate 4 (RSI Ledger Chain)",
                    "file": ".kb/rsi-ledger.jsonl",
                    "issue": "Tampered or broken SHA-256 hash chain in .kb/rsi-ledger.jsonl",
                }
            )

    links_count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    conn.close()

    legacy_mac_pat = re.compile(r"\bmac[12]\b(?!-offload|-full-test-matrix)")
    naming_files = list(md_files)
    if vault_dir.name == "me-kb":
        skill_md = vault_dir / ".agents" / "skills" / "personal-codex" / "SKILL.md"
        if skill_md.is_file():
            naming_files.append(skill_md)

    for p in naming_files:
        rel = str(p.relative_to(vault_dir))
        fence_char: str | None = None
        fence_len = 0
        check_mac_naming = vault_dir.name == "me-kb" and not (
            rel.startswith("04-episodic/") or rel == "02-operating-system/autonomous-rsi-distillations.md"
        )
        for line_no, raw_line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if (
                check_mac_naming
                and "Canonical Mac Mini Naming Rule" not in raw_line
                and legacy_mac_pat.search(raw_line)
            ):
                violations.append(
                    {
                        "gate": "Gate 5 (Canonical Mac Mini Naming)",
                        "file": f"{rel}:{line_no}",
                        "issue": "Deprecated Mac Mini shorthand detected; strictly use canonical workstation hostnames",
                    }
                )
            if p not in md_files:
                continue
            m_fence = re.match(r"^\s*(`{3,}|~{3,})", raw_line)
            if m_fence:
                run = m_fence.group(1)
                if fence_char is None:
                    fence_char = run[0]
                    fence_len = len(run)
                elif run[0] == fence_char and len(run) >= fence_len:
                    fence_char = None
                    fence_len = 0
                continue
            if fence_char is None and re.match(r"^(<{7} |={7}$|>{7} )", raw_line):
                violations.append(
                    {
                        "gate": "Gate 5 (Git Conflict Markers)",
                        "file": f"{rel}:{line_no}",
                        "issue": f"Unresolved Git merge/rebase conflict marker: {raw_line[:30]}",
                    }
                )
                break

    if (vault_dir / ".git").exists():
        git_ls = subprocess.run(
            ["git", "-C", str(vault_dir), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        for tracked in git_ls:
            if tracked.endswith((".mp4", ".mov", ".pyc", ".DS_Store")) or "__pycache__" in tracked:
                violations.append(
                    {
                        "gate": "Gate 5 (Git Hygiene)",
                        "file": tracked,
                        "issue": "Tracked binary video or bytecode artifact in git index",
                    }
                )

    op_cli = Path.home() / ".local" / "bin" / "openproject-query"
    episodic_dir = vault_dir / "04-episodic"
    if op_cli.is_file() and episodic_dir.is_dir() and vault_dir.name == "me-kb":
        ep_logs = sorted(episodic_dir.glob("*-log.md"))
        if ep_logs:
            latest_ep = ep_logs[-1]
            try:
                op_res = subprocess.run(
                    [str(op_cli), "--verify-log", str(latest_ep)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                if op_res.returncode != 0:
                    violations.append(
                        {
                            "gate": "Gate 5 (OpenProject Reference Drift)",
                            "file": str(latest_ep.relative_to(vault_dir)),
                            "issue": (op_res.stderr or op_res.stdout).strip()
                            or f"openproject-query exited {op_res.returncode}",
                        }
                    )
            except subprocess.TimeoutExpired:
                violations.append(
                    {
                        "gate": "Gate 5 (OpenProject Reference Drift)",
                        "file": str(latest_ep.relative_to(vault_dir)),
                        "issue": "openproject-query --verify-log timed out after 15s",
                    }
                )

    # Gate 6: Agent Skills & Zero-Auth Plugins Frontmatter Integrity
    from sovereign_kb.gwi import validate_skill_frontmatter

    all_skills_to_check: list[Path] = sorted(
        p
        for p in vault_dir.rglob("SKILL.md")
        if not any(part.startswith(".") for part in p.relative_to(vault_dir).parts)
    )
    if vault_dir.name == "me-kb":
        skills_root = Path.home() / ".gemini" / "config" / "skills"
        plugins_root = Path.home() / ".gemini" / "config" / "plugins"
        if skills_root.exists():
            for sdir in sorted(skills_root.iterdir()):
                smd = sdir / "SKILL.md"
                if sdir.is_dir() and smd.is_file():
                    all_skills_to_check.append(smd)
        if plugins_root.exists():
            for pdir in sorted(plugins_root.iterdir()):
                ps_dir = pdir / "skills"
                if ps_dir.exists():
                    for sk in sorted(ps_dir.iterdir()):
                        smd = sk / "SKILL.md"
                        if sk.is_dir() and smd.is_file():
                            all_skills_to_check.append(smd)

    for smd in all_skills_to_check:
        ok, err = validate_skill_frontmatter(smd)
        if not ok:
            try:
                rel_f = str(smd.relative_to(Path.home()))
            except ValueError:
                rel_f = str(smd)
            violations.append(
                {
                    "gate": "Gate 6 (Agent Skills)",
                    "file": rel_f,
                    "issue": err,
                }
            )

    report = {
        "status": "PASS" if not violations else "FAIL",
        "timestamp": datetime.datetime.now().isoformat(),
        "vault_dir": str(vault_dir),
        "documents": len(md_files),
        "chunks": chunk_count,
        "vectors": vec_count,
        "hips_indexed": hips_ready,
        "links": links_count,
        "skills_checked": len(all_skills_to_check),
        "install": install,
        "violations_count": len(violations),
        "violations": violations,
        "warnings_count": len(warnings),
        "warnings": warnings,
    }

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        hips_tag = ", HIPS WAND: OK" if hips_ready else ""
        print(f"=== Sovereign-KB Deterministic Verification (6-Gate Doctor): {report['status']} ===")
        print(f"  {install_check.GATE:<27} : {install_check.gate_summary(install)}")
        print(
            f"  Gate 1 (Frontmatter Schema) : {'PASS' if not any(v['gate'].startswith('Gate 1') for v in violations) else 'FAIL'}"
        )
        print(
            f"  Gate 2 (Wikilink Integrity) : {'PASS' if not any(v['gate'].startswith('Gate 2') for v in violations) else 'FAIL'}"
        )
        print(
            f"  Gate 3 (Graph Connectivity) : {'PASS' if not any(v['gate'].startswith('Gate 3') for v in violations) else 'FAIL'}"
        )
        print(
            f"  Gate 4 (FTS5, HIPS & Vecs)  : {'PASS' if not any(v['gate'].startswith('Gate 4') for v in violations) else 'FAIL'} ({vec_count}/{chunk_count} vectors{hips_tag})"
        )
        print(
            f"  Gate 5 (Git & Media Hygiene): {'PASS' if not any(v['gate'].startswith('Gate 5') for v in violations) else 'FAIL'}"
        )
        print(
            f"  Gate 6 (Skills & Plugins)   : {'PASS' if not any(v['gate'].startswith('Gate 6') for v in violations) else 'FAIL'} ({len(all_skills_to_check)} skills verified)"
        )
        if violations:
            print("\nViolations detected:")
            for v in violations:
                print(f"  - [{v['gate']}] {v['file']}: {v['issue']}")
            if not getattr(args, "heal", False) and any(v["gate"].startswith(("Gate 1", "Gate 3")) for v in violations):
                print(
                    "\nTip: Run 'skb verify --heal' to auto-generate missing YAML frontmatter and link orphan notes into INDEX.md."
                )
        if warnings:
            print("\nWarnings (not failing):")
            for w in warnings:
                print(f"  - [{w['gate']}] {w['file']}: {w['issue']}")

    if violations:
        sys.exit(1)
