"""Sovereign agent skills inventory, frontmatter validator, and profile router (`cmd_skills`)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from .scaffold import ARCHETYPE_SKILL_ROUTING, GWI_LENS_SKILL_ROUTING, GWI_PROFILE_SKILL_ROUTING


def _parse_skill_description(md_path: Path, use_pyyaml: bool = True) -> str:
    try:
        txt = md_path.read_text(encoding="utf-8")
        if txt.startswith("---"):
            fm = txt.split("---", 2)[1]
            if use_pyyaml:
                try:
                    import yaml

                    data = yaml.safe_load(fm)
                    if isinstance(data, dict) and "description" in data and data["description"] is not None:
                        s_lines = [ln.strip() for ln in str(data["description"]).strip().splitlines() if ln.strip()]
                        return s_lines[0] if s_lines else ""
                except Exception:
                    pass
            lines = fm.splitlines()
            for idx, line in enumerate(lines):
                if line.startswith("description:"):
                    raw_val = line[len("description:") :].strip()
                    m_block = re.match(r"^([>|])(?:[-+0-9]*)?(?:\s+#.*)?$", raw_val)
                    if m_block:
                        style = m_block.group(1)
                        cont: list[str] = []
                        base_indent: int | None = None
                        for nxt in lines[idx + 1 :]:
                            if not nxt.strip():
                                continue
                            indent = len(nxt) - len(nxt.lstrip(" \t"))
                            if indent == 0:
                                break
                            if base_indent is None:
                                base_indent = indent
                            elif indent < base_indent:
                                break
                            cont.append(nxt.strip())
                        if not cont:
                            return ""
                        if style == "|":
                            return cont[0]
                        return " ".join(cont)
                    if len(raw_val) >= 2 and raw_val[0] == raw_val[-1] and raw_val[0] in ('"', "'"):
                        return raw_val[1:-1]
                    return re.sub(r"\s+#.*$", "", raw_val).strip()
    except Exception:
        pass
    return ""


def cmd_skills(args) -> None:
    skills_root = Path.home() / ".gemini" / "config" / "skills"
    plugins_root = Path.home() / ".gemini" / "config" / "plugins"

    from sovereign_kb.gwi import validate_skill_frontmatter

    if getattr(args, "verify", False):
        all_skills: list[Path] = []
        if skills_root.exists():
            for sdir in sorted(skills_root.iterdir()):
                smd = sdir / "SKILL.md"
                if sdir.is_dir() and smd.is_file():
                    all_skills.append(smd)
        if plugins_root.exists():
            for pdir in sorted(plugins_root.iterdir()):
                ps_dir = pdir / "skills"
                if ps_dir.exists():
                    for sk in sorted(ps_dir.iterdir()):
                        smd = sk / "SKILL.md"
                        if sk.is_dir() and smd.is_file():
                            all_skills.append(smd)

        errs: list[tuple[Path, str]] = []
        for smd in all_skills:
            ok, msg = validate_skill_frontmatter(smd)
            if not ok:
                errs.append((smd, msg))

        if errs:
            print(f"❌ Skill Frontmatter Verification FAILED ({len(errs)} corrupt skill(s) out of {len(all_skills)}):")
            for smd, msg in errs:
                try:
                    rel = str(smd.relative_to(Path.home()))
                except ValueError:
                    rel = str(smd)
                print(f"  - {rel}: {msg}")
            sys.exit(1)
        else:
            print(
                f"✓ All {len(all_skills)} installed Sovereign Agent Skills & Plugin frontmatters verified (100% valid YAML)."
            )
            return

    def _parse_desc(md_path: Path) -> str:
        return _parse_skill_description(md_path)

    standalone_skills: list[dict[str, Any]] = []
    total_ref_modules = 0
    if skills_root.exists():
        for sdir in sorted(skills_root.iterdir()):
            smd = sdir / "SKILL.md"
            if sdir.is_dir() and smd.exists():
                refs = list((sdir / "references").rglob("*.md")) if (sdir / "references").exists() else []
                total_ref_modules += len(refs)
                standalone_skills.append(
                    {
                        "id": sdir.name,
                        "source": "global-skill",
                        "path": str(smd),
                        "ref_modules": len(refs),
                        "description": _parse_desc(smd),
                    }
                )

    plugins: list[dict[str, Any]] = []
    plugin_skill_count = 0
    if plugins_root.exists():
        for pdir in sorted(plugins_root.iterdir()):
            if not pdir.is_dir():
                continue
            pskills_dir = pdir / "skills"
            pskills = []
            if pskills_dir.exists():
                for sk in sorted(pskills_dir.iterdir()):
                    if sk.is_dir() and (sk / "SKILL.md").exists():
                        pskills.append(sk.name)
            plugin_skill_count += len(pskills)
            plugins.append(
                {
                    "id": pdir.name,
                    "path": str(pdir),
                    "skills_count": len(pskills),
                    "skills": pskills,
                }
            )

    query_str = " ".join(getattr(args, "query", []) or []).strip().lower()
    if query_str == "search":
        query_str = ""
    elif query_str.startswith("search "):
        query_str = query_str[len("search ") :].strip()

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "summary": {
                        "global_skills": len(standalone_skills),
                        "plugins": len(plugins),
                        "plugin_skills": plugin_skill_count,
                        "total_top_level_skills": len(standalone_skills) + plugin_skill_count,
                        "bundled_reference_modules": total_ref_modules,
                    },
                    "archetype_routing": {k: [x[0] for x in v] for k, v in ARCHETYPE_SKILL_ROUTING.items()},
                    "gwi_profile_routing": GWI_PROFILE_SKILL_ROUTING,
                    "gwi_lens_routing": GWI_LENS_SKILL_ROUTING,
                    "skills": standalone_skills,
                    "plugins": plugins,
                },
                indent=2,
            )
        )
        return

    print("=== Sovereign Agent Skills & Zero-Auth Plugin Matrix (`kb skills`) ===")
    print(
        f"  Global Skills: {len(standalone_skills)} | Zero-Auth Plugins: {len(plugins)} ({plugin_skill_count} skills) "
        f"| Total Top-Level Skills: {len(standalone_skills) + plugin_skill_count} | Reference Modules: {total_ref_modules}"
    )

    def _inject_into_repo(repo_dir: Path, explicit_arch: str | None = None, explicit_prof: str | None = None) -> bool:
        from sovereign_kb.gwi import detect_profile

        arch = explicit_arch
        if not arch:
            prof = explicit_prof or detect_profile(repo_dir)
            if prof == "paper":
                arch = "paper-ieee-arxiv"
            elif prof == "sim":
                arch = "game-sim-3d"
            elif (repo_dir / "Cargo.toml").is_file() and not (repo_dir / "package.json").is_file():
                arch = "rust-systems"
            elif (repo_dir / "pyproject.toml").is_file() and not (repo_dir / "package.json").is_file():
                arch = "python-uv-ai"
            elif (repo_dir / "tokens" / "metatopia-tokens.json").is_file():
                arch = "metatopia-chameleon"
            else:
                arch = "web-ts-3d-gis"

        skill_rows = "\n".join(f"| `{sname}` | `{spath}` |" for sname, spath in ARCHETYPE_SKILL_ROUTING.get(arch, []))
        section_md = (
            f"\n## Activated Sovereign Agent Skills (`kb skills --archetype {arch}`)\n\n"
            "| Skill ID | Local Entry Point (`SKILL.md` + `references/`) |\n"
            "| :--- | :--- |\n"
            f"{skill_rows}\n"
        )
        agents_file = repo_dir / "AGENTS.md"
        if agents_file.is_file():
            existing = agents_file.read_text(encoding="utf-8")
            if "Activated Sovereign Agent Skills" in existing:
                print(f"  [skip]   {repo_dir.name:34s} ({arch}) — already contains Activated Sovereign Agent Skills")
                return False
            agents_file.write_text(existing.rstrip() + "\n" + section_md, encoding="utf-8")
            print(f"  [inject] {repo_dir.name:34s} ({arch}) -> {agents_file}")
            return True
        else:
            agents_file.write_text(f"# AGENTS.md — `{repo_dir.name}`\n" + section_md, encoding="utf-8")
            print(f"  [create] {repo_dir.name:34s} ({arch}) -> {agents_file}")
            return True

    if getattr(args, "inject_all", False):
        seen_real: set[Path] = set()
        injected_count = 0
        checked_count = 0
        for parent in (Path.home() / "GitLab", Path.home() / "Projects"):
            if not parent.is_dir():
                continue
            for child in sorted(parent.iterdir()):
                if not child.is_dir():
                    continue
                real = child.resolve()
                if real in seen_real or real.name == "me-kb":
                    continue
                if not (real / ".git").exists() and not (real / "AGENTS.md").is_file():
                    continue
                seen_real.add(real)
                checked_count += 1
                if _inject_into_repo(real, getattr(args, "archetype", None), getattr(args, "profile", None)):
                    injected_count += 1
        print(f"\n[kb skills --inject-all] Processed {checked_count} repositories ({injected_count} newly injected).")
        return

    if getattr(args, "inject", None):
        repo_dir = Path(args.inject).expanduser().resolve()
        if not repo_dir.is_dir():
            print(f"Error: Directory not found: {repo_dir}", file=sys.stderr)
            sys.exit(1)
        _inject_into_repo(repo_dir, getattr(args, "archetype", None), getattr(args, "profile", None))
        return

    if getattr(args, "archetype", None):
        arch = args.archetype
        print(f"\n[Archetype Routing: {arch}]")
        for sname, spath in ARCHETYPE_SKILL_ROUTING.get(arch, []):
            print(f"  - {sname:24s} -> {spath}")
        return

    if getattr(args, "profile", None):
        prof = args.profile
        print(f"\n[GWI Profile Routing: {prof}]")
        for sname in GWI_PROFILE_SKILL_ROUTING.get(prof, []):
            print(f"  - {sname}")
        return

    if getattr(args, "lens", None):
        lens = args.lens
        print(f"\n[GWI Craft Lens Routing: {lens}]")
        for sname in GWI_LENS_SKILL_ROUTING.get(lens, []):
            print(f"  - {sname}")
        return

    if query_str:
        print(f"\n[Search Results for '{query_str}']")
        hits = 0
        for s in standalone_skills:
            if query_str in s["id"].lower() or query_str in s["description"].lower():
                print(f"  [skill]  {s['id']:28s} ({s['ref_modules']} refs) : {s['description'][:90]}")
                hits += 1
            rdir = Path(s["path"]).parent / "references"
            if rdir.exists():
                for rmd in sorted(rdir.rglob("*.md")):
                    if query_str in rmd.stem.lower() or query_str in str(rmd.parent.name).lower():
                        print(f"  [module] {s['id']}/{rmd.stem:24s} -> {rmd}")
                        hits += 1
        for p in plugins:
            for sk in p["skills"]:
                if query_str in sk.lower() or query_str in p["id"].lower():
                    print(f"  [plugin:{p['id']}] {sk}")
                    hits += 1
        if hits == 0:
            print("  No matching skills or reference modules found.")
        return

    print("\n1. Multi-Module Router Suites (`~/.gemini/config/skills/`):")
    for s in standalone_skills:
        if s["ref_modules"] > 0 or s["id"] in (
            "goldwood-gwi",
            "personal-kb",
            "gx10-cluster",
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "webapp-testing",
            "mcp-builder",
        ):
            ref_tag = f"[{s['ref_modules']:2d} modules]" if s["ref_modules"] > 0 else "[core skill]"
            print(f"  - {s['id']:25s} {ref_tag:12s} : {s['description'][:82]}")

    print("\n2. Zero-Auth Installed Plugins (`~/.gemini/config/plugins/`):")
    for p in plugins:
        print(f"  - {p['id']:36s} : {p['skills_count']:2d} skills")

    print("\n3. GWI Profile -> Skill Bundle Routing:")
    for prof, slist in GWI_PROFILE_SKILL_ROUTING.items():
        print(f"  - {prof:6s} : {', '.join(slist)}")
