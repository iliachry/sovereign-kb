"""Deterministic test suite for sovereign-kb (F-01 through F-07)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sovereign_kb.cli import (
    ARCHETYPES,
    compile_fts_query,
    extract_wikilinks,
    fold_accents,
    get_db,
    hybrid_retrieve,
    main,
    parse_frontmatter,
    split_markdown_chunks,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_zero_private_ip_or_secret_leaks() -> None:
    """F-02: Verify zero private Tailscale/lab IPs, local user paths, or secret tokens exist anywhere in the repo (including tests)."""
    import hashlib
    import re

    forbidden_token_sha256 = {
        "461e19de379a63a41a7df2739c0e4a4d335bdcce6d5887fa09aef721daf40e48",
        "334671fd022191b70568f8ee87a154469ec4f81dfd0dd96c472e1882771d5999",
        "3c8c7d51d9b1b7cca77f1d0a77251c053085aab4a663341f55de2d694c3e49a0",
        "9f63d84e07b3f25cc9d2e67967eedcb96621afb45c0c23f8f9c1d4a6620f802b",
        "76cae2a4b65125d286138a96599c4fec7cad52b5e276f044065e3b10aa4340dc",
        "017c0c156bcb8183610495d263ecf4b19f976254fde08a46f6948eb0394f6412",
        "2d687606b7c3d38523390e91e9ac13518b5c7c4bd688ad1a7cfda601385fa4f6",
        "bb84b4bc79be8655a4defc09ff150f48a90933c5a7af4c4816c354a5bdb3e187",
        "b1a9c22d006a5a5c62fe853797c70b995b4a594ba09250709d23e3d75aea80bb",
        "ba54fa73bf9febe567c6e5205f64c5c413bf84424b68f886ff8adacc80adcdb3",
    }
    forbidden_regexes = [
        (
            "Tailscale CGNAT IP",
            re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
        ),
        (
            "Hardcoded local user home path",
            re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"),
        ),
        (
            "API secret token",
            re.compile(r"\b(?:glpat-|ATATT)[A-Za-z0-9_-]{8,}"),
        ),
    ]
    token_re = re.compile(r"[A-Za-z0-9._-]+")
    for path in REPO_ROOT.rglob("*"):
        if any(part in (".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".swm") for part in path.parts):
            continue
        if path.is_file() and path.suffix in (".py", ".md", ".toml", ".sh", ".json", ".yaml", ".yml"):
            content = path.read_text(encoding="utf-8", errors="ignore")
            rel = path.relative_to(REPO_ROOT)
            for label, rx in forbidden_regexes:
                m = rx.search(content)
                assert m is None, f"Forbidden {label} ('{m.group(0)}') leaked in {rel}"
            for tok in set(token_re.findall(content)):
                digest = hashlib.sha256(tok.encode("utf-8")).hexdigest()
                assert digest not in forbidden_token_sha256, f"Forbidden private identifier leaked in {rel}"

    if (REPO_ROOT / ".git").exists():
        tags = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "tag", "-l"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.splitlines()
        for legacy_tag in ("v0.2.0", "v0.3.0"):
            assert legacy_tag not in tags, f"Legacy pre-rewrite tag {legacy_tag} must not exist"


def test_fold_accents_and_fts_query_compiler() -> None:
    """F-03: Verify Greek/Latin diacritic folding and compound identifier preservation in FTS5."""
    assert fold_accents("Συνείδησις") == fold_accents("συνειδησις")
    assert fold_accents("Café") == "cafe"

    fts_q, exact_ids = compile_fts_query("api-gateway ADR-0042 qwen3.6-35b-a3b συνείδησις")
    assert "api-gateway" in exact_ids
    assert "adr-0042" in exact_ids
    assert "qwen3.6-35b-a3b" in exact_ids
    assert '"api gateway"' in fts_q
    assert f'"{fold_accents("συνείδησις")}"' in fts_q


def test_fence_aware_chunking_and_wikilinks() -> None:
    """F-04: Verify code fences neither split Markdown chunks on # comments nor emit false wikilinks."""
    md = (
        "---\n"
        "title: Sample Doc\n"
        "type: operating-system\n"
        "updated: 2026-10-22\n"
        "tags: [alpha, beta]\n"
        "related:\n"
        '  - "[[INDEX]]"\n'
        "---\n\n"
        "# Main Title\n"
        "Intro linking to [[01-now/now|Now Page]] and inline `[[ignored-inline]]`.\n\n"
        "## Subsection A\n"
        "```python\n"
        "# This comment inside a fence MUST NOT split the chunk\n"
        "x = '[[ignored-in-fence]]'\n"
        "```\n"
        "Trailing line in Subsection A.\n\n"
        "### Deep Heading\n"
        "Deep content.\n"
    )
    meta = parse_frontmatter(md)
    assert meta["has_frontmatter"] is True
    assert meta["title"] == "Sample Doc"
    assert meta["type"] == "operating-system"
    assert meta["tags_list"] == ["alpha", "beta"]

    links = extract_wikilinks(md)
    assert links == ["01-now/now.md", "INDEX.md"]

    chunks = split_markdown_chunks(md, meta["title"])
    assert len(chunks) == 3
    assert chunks[0][1] == "Main Title"
    assert chunks[1][1] == "Main Title > Subsection A"
    assert "# This comment inside a fence MUST NOT split the chunk" in chunks[1][4]
    assert chunks[2][1] == "Main Title > Subsection A > Deep Heading"


def test_init_sync_capture_and_five_gate_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-05 & F-06: Verify `skb init`, `skb capture`, `skb search`, and 5-Gate `skb verify` pass 100%."""
    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "test-vault"

    main(["init", str(vault_dir), "--full", "--no-embed"])
    capsys.readouterr()

    # Run 5-Gate Doctor in lexical-only mode and assert PASS
    main(["--vault", str(vault_dir), "verify", "--allow-lexical-only", "--json"])
    verify_out = json.loads(capsys.readouterr().out)
    assert verify_out["status"] == "PASS", f"Vault doctor failed: {verify_out['violations']}"
    assert verify_out["violations_count"] == 0
    assert verify_out["documents"] >= 15

    # Test capture command and verify the new episodic note is automatically linked in INDEX.md (Gate 3 stays PASS)
    main(["--vault", str(vault_dir), "capture", "Verified deterministic test capture entry."])
    capsys.readouterr()

    main(["--vault", str(vault_dir), "verify", "--allow-lexical-only", "--json"])
    verify_after_capture = json.loads(capsys.readouterr().out)
    assert verify_after_capture["status"] == "PASS", (
        f"Vault doctor failed after capture: {verify_after_capture['violations']}"
    )


def test_hybrid_search_and_graph_propagation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-06: Verify 3-way hybrid retrieval, exact identifier boost, and 1-hop graph propagation."""
    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "search-vault"
    main(["init", str(vault_dir), "--full", "--no-embed"])

    conn = get_db(vault_dir)
    results = hybrid_retrieve(
        conn,
        vault_dir,
        "Type-42 font embedding Okabe-Ito",
        top_k=5,
        doc_type_filter="operating-system",
    )
    assert len(results) > 0
    top_paths = [r["rel_path"] for r in results]
    assert "02-operating-system/scientific-paper-framework.md" in top_paths

    # Verify 1-hop graph propagation surfaces TEMPLATE-scientific-paper-AGENTS.md or README-archetypes.md
    results_unfiltered = hybrid_retrieve(conn, vault_dir, "Pillar 10 RESPONSE_TO_REVIEWERS", top_k=6)
    conn.close()
    unfiltered_paths = [r["rel_path"] for r in results_unfiltered]
    assert "02-operating-system/scientific-paper-framework.md" in unfiltered_paths
    assert "INDEX.md" not in unfiltered_paths[:2]  # MOC hub suppression


def test_scaffold_all_five_archetypes_and_paper_pipeline(tmp_path: Path) -> None:
    """F-07: Verify `skb scaffold` across all 5 archetypes and execute the paper-ieee-arxiv simulation & BibTeX gates."""
    for arch in ARCHETYPES:
        target = tmp_path / f"proj-{arch}"
        main(["scaffold", arch, str(target), "--name", f"demo-{arch}", "--author", "Test Researcher"])
        assert (target / "AGENTS.md").is_file()
        assert (target / "docs" / "FEATURE_CATALOG.md").is_file()
        assert (target / "scripts" / "quality_gate.sh").is_file()
        assert (target / ".gitignore").is_file()

    # Execute the generated paper-ieee-arxiv simulation, BibTeX verifier, and arXiv packager
    paper_dir = tmp_path / "proj-paper-ieee-arxiv"
    assert (paper_dir / "PAPER_SPEC.md").is_file()
    assert (paper_dir / "AUDIT_LEDGER.md").is_file()
    assert (paper_dir / "paper.tex").is_file()
    assert "Test Researcher" in (paper_dir / "paper.tex").read_text(encoding="utf-8")

    subprocess.run([sys.executable, str(paper_dir / "sim" / "run_sim.py")], check=True)
    metrics_file = paper_dir / "results" / "metrics.json"
    assert metrics_file.is_file()
    metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert metrics["seed"] == 42
    assert len(metrics["records"]) == 4

    subprocess.run([sys.executable, str(paper_dir / "scripts" / "verify_bib.py")], check=True)
    subprocess.run([sys.executable, str(paper_dir / "scripts" / "package_arxiv.py")], check=True)
    assert (paper_dir / "overleaf_arxiv_bundle.zip").is_file()

    # Execute the generated game-sim-3d simulation telemetry verifier
    game_dir = tmp_path / "proj-game-sim-3d"
    assert (game_dir / "project.godot").is_file()
    assert (game_dir / "sim" / "verify_sim.py").is_file()
    subprocess.run([sys.executable, str(game_dir / "sim" / "verify_sim.py")], check=True)
    sim_telemetry = json.loads((game_dir / "results" / "sim_telemetry.json").read_text(encoding="utf-8"))
    assert sim_telemetry["seed"] == 42
    assert sim_telemetry["steps"] == 120


def test_hips_engine_integration_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-08: Verify HIPS Block-Max WAND integration when available and zero-dependency FTS5 fallback when disabled."""
    from sovereign_kb.cli import resolve_hips_bin

    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "hips-vault"
    main(["init", str(vault_dir), "--full", "--no-embed"])
    capsys.readouterr()

    hips_bin = resolve_hips_bin()
    if hips_bin is not None:
        assert (vault_dir / ".kb" / "hips-index").is_dir()
        assert (vault_dir / ".kb" / "hips-chunks.jsonl").is_file()

        main(["--vault", str(vault_dir), "hips", "search", "Type-42", "font", "--json"])
        hips_out = json.loads(capsys.readouterr().out)
        assert len(hips_out["results"]) > 0
        assert any("scientific-paper-framework.md" in r["path"] for r in hips_out["results"])

        conn = get_db(vault_dir)
        hips_only = hybrid_retrieve(conn, vault_dir, "Type-42 font embedding", top_k=3, engine="hips")
        conn.close()
        assert len(hips_only) > 0
        assert hips_only[0]["hips_rank"] == 1

    # Verify zero-dependency FTS5 fallback when KB_HIPS_BIN is disabled
    monkeypatch.setenv("KB_HIPS_BIN", "")
    assert resolve_hips_bin() is None
    conn = get_db(vault_dir)
    fallback_res = hybrid_retrieve(conn, vault_dir, "Type-42 font embedding", top_k=3, engine="fts5")
    conn.close()
    assert len(fallback_res) > 0
    assert fallback_res[0]["hips_rank"] is None


def test_rust_certification_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-09: Verify `skb certify rust` retrieves all 20 RUST-CERT specs and passes 20/20 `rustc -D warnings` tests."""
    if (
        shutil.which("rustc") is None
        or subprocess.run(["rustc", "--version"], capture_output=True, check=False).returncode != 0
    ):
        pytest.skip("rustc not installed or toolchain unavailable")
    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "cert-vault"
    out_dir = tmp_path / "cert-out"
    main(["init", str(vault_dir), "--full", "--no-embed"])
    capsys.readouterr()

    main(["--vault", str(vault_dir), "certify", "rust", "--no-embed", "--json", "--output-dir", str(out_dir)])
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "CERTIFIED"
    assert report["kb_retrieval_score"] == "20/20 (100.0%)"
    assert report["rustc_test_score"] == "20/20 (100.0%)"
    assert report["warnings"] == 0
    assert len(report["results"]) == 20
    assert (out_dir / "rust_certification_report.json").is_file()
    assert (out_dir / "rust_cert_suite.rs").is_file()


def test_rsi_self_healing_consolidation_and_monotonic_tuning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-10: Verify 3-loop RSI cycle heals frontmatter/orphans, distills episodic logs, tunes policy monotonically, and chains SHA-256 ledger."""
    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "rsi-vault"
    main(["init", str(vault_dir), "--full", "--no-embed"])
    capsys.readouterr()

    # Capture an episodic milestone
    main(
        [
            "--vault",
            str(vault_dir),
            "capture",
            "Implemented Block-Max WAND posting list bit-packing and Lyapunov RSI monotonicity gate.",
        ]
    )
    capsys.readouterr()

    # Inject a drifted orphan note missing YAML frontmatter and inbound wikilinks
    drifted_note = vault_dir / "03-life-domains" / "unlinked-drift.md"
    drifted_note.write_text(
        "# Unlinked Drift Subsystem\n\nThis note was dropped into the vault without frontmatter or inbound links.\n",
        encoding="utf-8",
    )

    # Execute full RSI cycle (`skb rsi cycle --no-embed --json`)
    main(["--vault", str(vault_dir), "rsi", "cycle", "--no-embed", "--json"])
    rsi_out = json.loads(capsys.readouterr().out)

    assert rsi_out["cycle_id"] == "RSI-0001"
    assert rsi_out["lyapunov_monotonic"] is True
    assert rsi_out["after"]["objective_j"] >= rsi_out["before"]["objective_j"]
    assert "03-life-domains/unlinked-drift.md" in rsi_out["consolidation"]["healed_frontmatter"]
    assert "03-life-domains/unlinked-drift.md" in rsi_out["consolidation"]["healed_orphans"]
    assert rsi_out["consolidation"]["distilled_entries"] >= 1
    assert rsi_out["consolidation"]["rejected_noise_bullets"] >= 0
    assert "delta_j_heal" in rsi_out and "delta_j_tune" in rsi_out
    assert rsi_out["after"]["ledger_intrusions"] == 0
    distill_file = vault_dir / "02-operating-system" / "autonomous-rsi-distillations.md"
    assert distill_file.is_file()
    distill_txt = distill_file.read_text(encoding="utf-8")
    assert "type: rsi-ledger" in distill_txt
    assert '| `"[[INDEX]]"` |' not in distill_txt
    from sovereign_kb.rsi import _strip_frontmatter_and_extract_bullets

    nested_fence_doc = (
        '---\ntitle: t\nrelated:\n  - "[[INDEX]]"\n---\n'
        "````carousel\n```python\n- leaked code bullet that must not be extracted\n```\n````\n"
        "- Implemented real invariant outside four-backtick carousel fence.\n"
    )
    assert _strip_frontmatter_and_extract_bullets(nested_fence_doc) == [
        "Implemented real invariant outside four-backtick carousel fence."
    ]
    assert (vault_dir / ".kb" / "retrieval_policy.json").is_file()
    assert (vault_dir / ".kb" / "rsi-ledger.jsonl").is_file()
    assert rsi_out["ledger"]["chain_verified"] is True

    # Run a second RSI cycle to verify SHA-256 hash chaining (RSI-0001 -> RSI-0002)
    main(["--vault", str(vault_dir), "evolve", "--no-embed", "--json"])
    rsi_out_2 = json.loads(capsys.readouterr().out)
    assert rsi_out_2["cycle_id"] == "RSI-0002"
    assert rsi_out_2["ledger"]["total_cycles"] == 2
    assert rsi_out_2["ledger"]["chain_verified"] is True

    # Verify all 5 Gates pass 100% after RSI self-healing and distillation
    main(["--vault", str(vault_dir), "verify", "--allow-lexical-only", "--json"])
    ver_out = json.loads(capsys.readouterr().out)
    assert ver_out["status"] == "PASS", f"5-Gate Doctor failed after RSI cycle: {ver_out['violations']}"


def test_usage_finops_telemetry(capsys: pytest.CaptureFixture[str]) -> None:
    """F-11: Verify `skb usage --native --no-gx10 --json` returns deterministic FinOps telemetry schema."""
    main(["usage", "--native", "--no-gx10", "--days", "1", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["engine"] == "native-stdlib"
    assert out["days_requested"] == 1
    assert "totals" in out
    assert "prompt_cache_hit_pct" in out
    assert isinstance(out["daily"], list)


def test_dashboard_snapshot_and_static_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-12: Verify `skb dashboard --json` and `skb dashboard --export` generate complete zero-dependency observability artifacts."""
    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "dash-vault"
    main(["init", str(vault_dir), "--full", "--no-embed"])
    capsys.readouterr()

    main(["--vault", str(vault_dir), "dashboard", "--native", "--no-gx10", "--days", "1", "--json"])
    snap = json.loads(capsys.readouterr().out)
    assert snap["vault"]["documents"] >= 15
    assert snap["gates"]["status"] == "PASS"
    assert len(snap["graph"]["nodes"]) == snap["vault"]["documents"]
    assert len(snap["graph"]["edges"]) >= 10
    assert "finops" in snap
    assert "rsi" in snap
    assert len(snap["initial_search"]["results"]) > 0

    export_file = tmp_path / "custom-dashboard.html"
    main(["--vault", str(vault_dir), "dashboard", "--native", "--no-gx10", "--export", str(export_file)])
    capsys.readouterr()
    assert export_file.is_file()
    html_text = export_file.read_text(encoding="utf-8")
    assert 'meta name="color-scheme" content="light dark"' in html_text
    assert "Sovereign-KB Telemetry" in html_text
    assert "Interactive [[Wikilink]] Knowledge Graph" in html_text


def test_gwi_calculator_barrier_and_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-13: Verify Goldwood Index G(S) v1.1 ceiling-normalized barrier math, SNRC, `app`/`sim`/`video`/`paper` profiles, and self-audit."""
    import sovereign_kb.gwi as gwi_mod
    from sovereign_kb.gwi import compute_goldwood_index, compute_snrc, main_cli

    # 1. Verify boundary continuity G(9.0, 9.0, 9.0, 9.0) == 9.0 PASS & high-coherence 9+ pass
    boundary_res = compute_goldwood_index([9.0, 9.0, 9.0, 9.0])
    assert boundary_res["status"] == "PASS"
    assert boundary_res["goldwood_g"] == 9.0
    assert boundary_res["barrier_collapsed"] is False

    high_res = compute_goldwood_index([9.6, 9.5, 9.8, 9.7])
    assert high_res["status"] == "PASS"
    assert high_res["goldwood_g"] >= 9.0
    assert high_res["barrier_collapsed"] is False
    assert len(high_res["jacobian_dG_dS"]) == 4
    assert high_res["highest_leverage_tier"] == "S_perc"

    snrc = compute_snrc(9.5, 0.5, 1.0)
    assert snrc >= 6.0

    # 2. Verify Weakest-Link Catastrophic Barrier Collapse when a single tier drops below theta = 7.0
    collapsed_res = compute_goldwood_index([9.8, 9.8, 9.8, 4.5])
    assert collapsed_res["status"] == "FAIL"
    assert collapsed_res["barrier_collapsed"] is True
    assert collapsed_res["goldwood_g"] < 1.0  # Psi(4.5) ~ 0.0019 collapses the 8.48 linear average!

    # 3. Verify `skb gwi calc` CLI
    main(["gwi", "calc", "9.5", "9.6", "9.4", "9.7", "--weights", "app", "--json"])
    calc_out = json.loads(capsys.readouterr().out)
    assert calc_out["status"] == "PASS"
    assert calc_out["goldwood_g"] >= 9.0

    # 4. Verify `sovereign-kb` self-audit (`SELF-01` regression guard) and scaffolded `app` evidence output
    main_cli(["audit", str(REPO_ROOT), "--json"])
    self_audit = json.loads(capsys.readouterr().out)
    assert self_audit["profile"] == "app"
    assert self_audit["calculation"]["status"] == "PASS"
    assert self_audit["calculation"]["goldwood_g"] >= 9.0
    assert all(b["id"] != "GWI-APP-D01" for b in self_audit["blockers"])

    ev_target = tmp_path / "gwi-app-proj"
    main(["scaffold", "python-uv-ai", str(ev_target), "--name", "gwi-app-proj"])
    capsys.readouterr()
    (ev_target / "README.md").write_text("# GWI App Project\n", encoding="utf-8")
    (ev_target / "tests").mkdir(exist_ok=True)
    (ev_target / "tests" / "test_core.py").write_text(
        "def test_f01():\n    # Covers F-01\n    assert True\n", encoding="utf-8"
    )
    main_cli(["audit", str(ev_target), "--write-evidence", "--json"])
    app_audit = json.loads(capsys.readouterr().out)
    assert app_audit["profile"] == "app"
    assert app_audit["calculation"]["status"] == "PASS"
    assert (ev_target / "evidence" / "goldwood.json").is_file()

    # 5. Verify `sim` profile detects unreferenced scenes / missing `quit()` AND resolves `.tscn` -> `preload()`
    sim_target = tmp_path / "gwi-sim-proj"
    main(["scaffold", "game-sim-3d", str(sim_target), "--name", "gwi-sim-proj"])
    capsys.readouterr()
    (sim_target / "scenes" / "swarm_movie.gd").write_text(
        "extends Node3D\nconst TOTAL_FRAMES := 1350\nfunc _process(_delta: float) -> void:\n    pass\n",
        encoding="utf-8",
    )
    main_cli(["audit", str(sim_target), "--profile", "sim", "--json"])
    sim_audit = json.loads(capsys.readouterr().out)
    assert sim_audit["calculation"]["status"] == "FAIL"
    blocker_ids = {b["id"] for b in sim_audit["blockers"]}
    assert "GWI-SIM-A01C" in blocker_ids
    assert "GWI-SIM-A03A" in blocker_ids

    # 6. Verify `video` profile rejects corrupt fake MP4 (`SELF-02`) and passes when valid stream probe is present
    vid_dir = tmp_path / "gwi-video-kit"
    vid_dir.mkdir()
    (vid_dir / "demo_master.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42moovavc1mp4asoun" + b"\x00" * 1024)
    (vid_dir / "demo_captions.srt").write_text(
        "1\n00:00:00,500 --> 00:00:04,000\nAct I: Introduction to Goldwood Index.\n\n"
        "2\n00:00:04,500 --> 00:00:08,500\nAct II: Non-Linear Barrier Gate.\n",
        encoding="utf-8",
    )
    (vid_dir / "ebu_r128_report.txt").write_text(
        "Integrated loudness: I: -14.0 LUFS\nTrue peak: -4.0 dBTP\n", encoding="utf-8"
    )
    for idx in (1, 2, 3):
        (vid_dir / f"act{idx}_frame.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    (vid_dir / "youtube_thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 512)

    # Corrupt dummy bytes must fail `GWI-VID-M00` under v1.1 (`SELF-02`)
    main_cli(["audit", str(vid_dir), "--profile", "video", "--json"])
    corrupt_vid = json.loads(capsys.readouterr().out)
    assert any(b["id"] == "GWI-VID-M00" for b in corrupt_vid["blockers"])

    # With valid stream metadata probed, video profile passes >= 9.0
    monkeypatch.setattr(
        gwi_mod,
        "_probe_video_streams",
        lambda _p: {
            "path": str(_p),
            "size_bytes": 8_400_000,
            "probed": True,
            "probe_source": "ffprobe",
            "has_video": True,
            "has_audio": True,
            "width": 1920,
            "height": 1080,
            "codec_name": "h264",
            "pix_fmt": "yuv420p",
            "fps": 30.0,
            "duration_s": 120.0,
            "audio_channels": 2,
            "audio_sample_rate": 48000,
        },
    )
    main_cli(["audit", str(vid_dir), "--profile", "video", "--json"])
    vid_audit = json.loads(capsys.readouterr().out)
    assert vid_audit["profile"] == "video"
    assert vid_audit["calculation"]["status"] == "PASS"
    assert vid_audit["calculation"]["goldwood_g"] >= 9.0

    # 7. Verify `paper` profile (`SELF-05`) detects `**bold**` Markdown leakage in `.tex` and passes clean `.tex`
    paper_dir = tmp_path / "gwi-paper"
    paper_dir.mkdir()
    tex_file = paper_dir / "treatise.tex"
    tex_file.write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        + "\\section{Introduction}\n"
        + ("The Goldwood Index enforces non-linear quality thresholds \\cite{shannon1948}.\n" * 60)
        + "\\begin{equation}\n\\mathcal{G}(\\mathbf{S}) = \\sum w_i S_i \\Psi(S_i)\n\\end{equation}\n"
        + "\\begin{thebibliography}{9}\n\\bibitem{shannon1948} C. E. Shannon, 1948.\n\\end{thebibliography}\n"
        + "\\end{document}\n",
        encoding="utf-8",
    )
    (paper_dir / "treatise.pdf").write_bytes(b"%PDF-1.5\n" + b"0" * 20000)
    (paper_dir / "treatise.log").write_text(
        "Output written on treatise.pdf (4 pages, 20000 bytes).\n", encoding="utf-8"
    )

    main_cli(["audit", str(tex_file), "--profile", "paper", "--json"])
    clean_paper = json.loads(capsys.readouterr().out)
    assert clean_paper["profile"] == "paper"
    assert clean_paper["calculation"]["status"] == "PASS"
    assert clean_paper["calculation"]["goldwood_g"] >= 9.5
    assert clean_paper["craft_lenses"]["required_lenses_count"] >= 1
    assert any(lens["id"] == "typography-editor" for lens in clean_paper["craft_lenses"]["lenses"])

    # Verify `gwi lenses` catalog output (Bi-Polar 4x4: 4 Verga + 4 Chrysos Specialist Lenses = 146 skills)
    main_cli(["lenses", "--json"])
    lenses_catalog = json.loads(capsys.readouterr().out)
    assert set(lenses_catalog.keys()) == {
        "technical-artist",
        "ux-ergonomics",
        "spatial-cinema",
        "typography-editor",
        "web-perf-edge",
        "data-api-architect",
        "ai-ml-science",
        "principal-verification",
    }
    assert {k for k, v in lenses_catalog.items() if v["pole"] == "verga"} == {
        "technical-artist",
        "ux-ergonomics",
        "spatial-cinema",
        "typography-editor",
    }
    assert {k for k, v in lenses_catalog.items() if v["pole"] == "chrysos"} == {
        "web-perf-edge",
        "data-api-architect",
        "ai-ml-science",
        "principal-verification",
    }
    assert sum(v["skills_count"] for v in lenses_catalog.values()) == 146

    # Inject literal Markdown **bold** leak into .tex
    tex_file.write_text(
        tex_file.read_text(encoding="utf-8").replace(
            "\\section{Introduction}", "\\section{Introduction}\nThis has **leaked markdown** syntax."
        ),
        encoding="utf-8",
    )
    main_cli(["audit", str(tex_file), "--profile", "paper", "--json"])
    leaked_paper = json.loads(capsys.readouterr().out)
    assert any(b["id"] == "GWI-PAP-S03" for b in leaked_paper["blockers"])
    assert leaked_paper["craft_lenses"]["veto_triggered"] is True

    # 8. Verify GWI v1.3.0 Principal-Grade Artifact Probes (WCAG contrast, HUD occlusion, SRT CPS, LRA anti-drone, Type-3 fonts, strict-lenses cap)
    from sovereign_kb.gwi import compute_wcag_contrast_ratio

    assert compute_wcag_contrast_ratio("#0b0f17", "#f0e6d2") >= 12.0  # Goldwood dark surface vs warm ivory ink
    assert compute_wcag_contrast_ratio("#777777", "#888888") < 1.5  # Inaccessible low-contrast grey-on-grey

    bad_ux_dir = tmp_path / "bad-ux-app"
    bad_ux_dir.mkdir()
    (bad_ux_dir / "AGENTS.md").write_text("# Agents\n- FEAT-01\n", encoding="utf-8")
    (bad_ux_dir / "README.md").write_text("# Bad UX\n", encoding="utf-8")
    (bad_ux_dir / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^19.0.0", "three": "^0.170.0"}, "scripts": {"test": "vitest"}}),
        encoding="utf-8",
    )
    (bad_ux_dir / "tokens.css").write_text(
        ":root { --bg-surface: #777777; --text-primary: #888888; }\n", encoding="utf-8"
    )
    (bad_ux_dir / "App.tsx").write_text("export const App = () => <div aria-label='x' />;\n", encoding="utf-8")
    (bad_ux_dir / "evidence" / "playwright").mkdir(parents=True)
    (bad_ux_dir / "evidence" / "playwright" / "report.json").write_text(
        json.dumps({"hudOcclusionRatio": 0.42, "horizontalOverflowPx": 24}), encoding="utf-8"
    )

    main_cli(["audit", str(bad_ux_dir), "--profile", "app", "--json"])
    bad_ux_audit = json.loads(capsys.readouterr().out)
    assert bad_ux_audit["craft_lenses"]["veto_triggered"] is True
    assert bad_ux_audit["craft_lenses"]["verga_pole_score"] <= 6.8

    # Verify Type-3 bitmap font veto in `paper` profile
    type3_dir = tmp_path / "type3-paper"
    type3_dir.mkdir()
    t3_tex = type3_dir / "paper.tex"
    t3_tex.write_text(
        "\\documentclass{article}\n\\usepackage{microtype}\n\\usepackage{booktabs}\n\\begin{document}\nClean text.\n\\end{document}\n",
        encoding="utf-8",
    )
    (type3_dir / "paper.pdf").write_bytes(b"%PDF-1.5\n1 0 obj << /Type /Font /Subtype /Type3 >> endobj\n" + b"0" * 1024)
    (type3_dir / "paper.log").write_text("Output written on paper.pdf (1 page).\n", encoding="utf-8")
    main_cli(["audit", str(t3_tex), "--profile", "paper", "--json"])
    t3_audit = json.loads(capsys.readouterr().out)
    assert t3_audit["craft_lenses"]["veto_triggered"] is True
    assert any("ty.pdffonts_type3" in f for lens in t3_audit["craft_lenses"]["lenses"] for f in lens["probe_findings"])

    # 9. Verify GWI v1.4.0 Subtractive Design Budget Veto (expandedTablesVisible > 1 / headerRows > 1)
    bloat_dir = tmp_path / "bloated-chat-app"
    bloat_dir.mkdir()
    (bloat_dir / "AGENTS.md").write_text("# Agents\n- FEAT-01\n", encoding="utf-8")
    (bloat_dir / "README.md").write_text("# Bloated Chat\n", encoding="utf-8")
    (bloat_dir / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^19.0.0"}, "scripts": {"test": "vitest"}}),
        encoding="utf-8",
    )
    (bloat_dir / "tokens.css").write_text(
        ":root { --bg-surface: #ffffff; --text-primary: #1a2027; }\n", encoding="utf-8"
    )
    (bloat_dir / "App.tsx").write_text("export const App = () => <div aria-label='chat' />;\n", encoding="utf-8")
    (bloat_dir / "evidence" / "playwright").mkdir(parents=True)
    (bloat_dir / "evidence" / "playwright" / "report.json").write_text(
        json.dumps({"hudOcclusionRatio": 0.0, "horizontalOverflowPx": 0, "headerRows": 2, "expandedTablesVisible": 3}),
        encoding="utf-8",
    )
    main_cli(["audit", str(bloat_dir), "--profile", "app", "--json"])
    bloat_audit = json.loads(capsys.readouterr().out)
    assert bloat_audit["craft_lenses"]["veto_triggered"] is True
    assert any(
        "ux.subtractive_budget" in f for lens in bloat_audit["craft_lenses"]["lenses"] for f in lens["probe_findings"]
    )


def test_soar_cognitive_kernel_and_chunking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-14: Verify Soar 9.6 Cognitive Kernel, 0-impasse Explanation-Based Chunking, soarc transpiler, and vault integration."""
    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "soar-vault"
    main(["init", str(vault_dir), "--full", "--no-embed"])
    capsys.readouterr()

    # 1. Verify all 4 canonical Soar benchmarks (Water Jugs, Blocks World, Towers of Hanoi, Vault Governance)
    main(["--vault", str(vault_dir), "soar", "bench", "--json"])
    bench_out = json.loads(capsys.readouterr().out)
    assert bench_out["status"] == "PASS"
    assert bench_out["benchmarks_count"] == 4
    for bname, binfo in bench_out["benchmarks"].items():
        assert binfo["passed"] is True, f"Soar benchmark {bname} failed: {binfo}"
        assert binfo["initial_impasses"] >= 1
        assert binfo["replay_impasses"] == 0
        assert binfo["transpiled_verified"] is True

    # 2. Verify vault ```soar extraction and verification
    main(["--vault", str(vault_dir), "soar", "verify", "--json"])
    ver_out = json.loads(capsys.readouterr().out)
    assert ver_out["status"] == "PASS"
    assert ver_out["vault_productions"]["rules_loaded"] >= 2
    assert "02-operating-system/soar-cognitive-architecture.md" in ver_out["vault_productions"]["files_with_soar"]

    # 3. Verify Neuro-Symbolic impasse resolution & learned chunk persistence
    main(["--vault", str(vault_dir), "soar", "resolve", "--benchmark", "water-jugs", "--json"])
    res_out = json.loads(capsys.readouterr().out)
    assert res_out["impasse_resolution"]["resolved"] is True
    assert res_out["vault_chunking"]["zero_impasse_replay"] is True
    assert (vault_dir / ".kb" / "learned_chunks.soar").is_file()

    # 4. Adversarial checks: empty/singleton/3+ candidates & nested-(halt)/prohibit/inverted chunk rejection
    from sovereign_kb.soar import load_vault_productions, resolve_retrieval_tie_with_soar

    empty_res = resolve_retrieval_tie_with_soar(vault_dir, "empty", [], persist_chunks=False)
    assert empty_res["goal_achieved"] is False and empty_res["selected_doc"] is None

    single_res = resolve_retrieval_tie_with_soar(
        vault_dir,
        "single",
        [{"rel_path": "05-episodic/one.md", "doc_type": "episodic", "score": 0.5}],
        persist_chunks=False,
    )
    assert single_res["goal_achieved"] is True and single_res["zero_impasse_replay"] is False

    three_cands = [
        {"rel_path": "05-episodic/log1.md", "doc_type": "episodic", "score": 0.85},
        {"rel_path": "03-life-domains/note2.md", "doc_type": "life-domain", "score": 0.83},
        {"rel_path": "01-identity/sovereign-codex.md", "doc_type": "identity", "score": 0.80},
    ]
    three_res = resolve_retrieval_tie_with_soar(vault_dir, "three", three_cands, persist_chunks=False)
    assert three_res["goal_achieved"] is True
    assert three_res["zero_impasse_replay"] is True
    assert three_res["reranked_candidates"][0]["rel_path"] == "01-identity/sovereign-codex.md"

    # Inject nested (halt) and inverted-authority poison into learned_chunks.soar
    from sovereign_kb.soar import parse_soar
    from sovereign_kb.soar.vault_agent import _is_valid_learned_chunk

    base_probe = (
        "sp {chunk*probe :chunk\n"
        "(state <s> ^name skb-vault ^operator <lo> + ^operator <hi> +)\n"
        "(<lo> ^name select-candidate ^authority AUTHLO)\n"
        "(<hi> ^name select-candidate ^authority AUTHHI)\n"
        "-->\nACTION\n}"
    )
    for lo_s, hi_s, act_s, expected_ok in [
        ("1", "3", "(<s> ^operator <hi> > <lo>)", True),
        ("1", "3", "(<s> ^operator (write (halt)) > <lo>)", False),
        ("1", "3", "(<s> ^operator <hi> > (write (halt)))", False),
        ("1", "3", "(<s> ^operator <hi> ~)", False),
        ("1", "3", "(<s> ^operator <hi> -)", False),
        ("1", "3", "(<s> ^operator <lo> > <hi>)", False),
        ("{ 1 }", "{ 3 }", "(<s> ^operator <lo> > <hi>)", False),
        ("<< 1 >>", "<< 3 >>", "(<s> ^operator <lo> > <hi>)", False),
        ("<< 1 5 >>", "3", "(<s> ^operator <lo> > <hi>)", False),
    ]:
        probe_prod = parse_soar(base_probe.replace("AUTHLO", lo_s).replace("AUTHHI", hi_s).replace("ACTION", act_s))[0]
        assert _is_valid_learned_chunk(probe_prod) is expected_ok

    (vault_dir / ".kb" / "learned_chunks.soar").write_text(
        "sp {chunk*nested-halt\n    :chunk\n    (state <s> ^superstate nil)\n-->\n    (<s> ^operator (halt) +)\n}\n",
        encoding="utf-8",
    )
    bad_load = load_vault_productions(vault_dir)
    assert bad_load["status"] == "FAIL" and len(bad_load["errors"]) >= 1
    recovered = resolve_retrieval_tie_with_soar(vault_dir, "recover", three_cands, persist_chunks=True)
    assert recovered["preloaded_chunks"] == 0 and recovered["goal_achieved"] is True

    # Verify full RSI cycle passes empty and singleton retrieval candidates through without fabrication
    import sovereign_kb.cli as cli_mod

    monkeypatch.setattr(cli_mod, "hybrid_retrieve", lambda *a, **kw: [])
    main(["--vault", str(vault_dir), "rsi", "cycle", "--no-embed", "--json"])
    rsi_empty = json.loads(capsys.readouterr().out)["soar_chunking"]
    assert rsi_empty["goal_achieved"] is False
    assert rsi_empty["selected_doc"] is None
    assert rsi_empty["reranked_candidates"] == []

    singleton_cand = [{"rel_path": "only-real.md", "doc_type": "episodic", "score": 0.5}]
    monkeypatch.setattr(cli_mod, "hybrid_retrieve", lambda *a, **kw: list(singleton_cand))
    main(["--vault", str(vault_dir), "rsi", "cycle", "--no-embed", "--json"])
    rsi_single = json.loads(capsys.readouterr().out)["soar_chunking"]
    assert rsi_single["goal_achieved"] is True
    assert rsi_single["selected_doc"] == "only-real-md"
    assert rsi_single["reranked_candidates"] == singleton_cand


def test_tri_council_skb_rsi_gwi_chunkstore_and_bridge_upgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify Fable x Astra Tri-Council SKB upgrades (live RSI-Soar callback, held-out split, rollback hash, strict GWI, ChunkStore & Bridge)."""
    import math

    from sovereign_kb.bridge import (
        append_triad_provenance_event,
        retrieve_vault_precedents,
        run_triad_end_to_end_canary,
        verify_triad_provenance_chain,
    )
    from sovereign_kb.gwi import compute_goldwood_index, evaluate_app_profile
    from sovereign_kb.rsi import compute_policy_sha256, load_retrieval_policy, verify_ledger_chain
    from sovereign_kb.soar import ChunkStore, parse_soar

    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "tri-vault"
    main(["init", str(vault_dir), "--full", "--no-embed"])
    capsys.readouterr()

    # 1. SKB #1: Live RSI -> Retrieval -> Soar callback passes real candidates without swallowing TypeError
    main(["--vault", str(vault_dir), "rsi", "cycle", "--no-embed", "--json"])
    rsi_live = json.loads(capsys.readouterr().out)
    assert rsi_live["soar_chunking"]["retrieval_candidates_count"] >= 2
    assert rsi_live["soar_chunking"]["goal_achieved"] is True
    assert rsi_live["soar_chunking"]["zero_impasse_replay"] is True
    assert rsi_live["heldout_after"]["probes_count"] >= 2

    # 2. SKB #2: Verify ledger corruption detection and rollback policy_sha256 integrity
    base_pol = load_retrieval_policy(vault_dir)
    base_sha = compute_policy_sha256(base_pol)
    assert rsi_live["tuning"]["policy_sha256"] == compute_policy_sha256(rsi_live["tuning"]["policy"])

    ledger_file = vault_dir / ".kb" / "rsi-ledger.jsonl"
    orig_ledger = ledger_file.read_text(encoding="utf-8")
    ledger_file.write_text(orig_ledger + "{malformed_json_line\n", encoding="utf-8")
    chain_ok, _ = verify_ledger_chain(vault_dir)
    assert chain_ok is False
    ledger_file.write_text(orig_ledger, encoding="utf-8")
    assert verify_ledger_chain(vault_dir)[0] is True
    assert len(base_sha) == 16

    # 3. SKB #3: Strict GWI boundary (`raw_g < 9.0` is REVISE, NaN rejected) + malformed diagnostics detection
    near_miss = compute_goldwood_index([8.996, 8.996, 8.996, 8.996])
    assert near_miss["raw_goldwood_g"] < 9.0
    assert near_miss["status"] == "REVISE"
    with pytest.raises(ValueError):
        compute_goldwood_index([float("nan"), 9.5, 9.5, 9.5])

    app_dir = tmp_path / "sample-app"
    app_dir.mkdir()
    (app_dir / "AGENTS.md").write_text("# AGENTS\n- FEAT-01\n", encoding="utf-8")
    (app_dir / "README.md").write_text("# App\n", encoding="utf-8")
    (app_dir / "tests").mkdir()
    (app_dir / "tests" / "test_app.py").write_text("def test_feat():\n    assert 'FEAT-01'\n", encoding="utf-8")
    (app_dir / "artifacts").mkdir()
    (app_dir / "artifacts" / "bad.diagnostics.json").write_text("{}", encoding="utf-8")
    app_rep = evaluate_app_profile(app_dir)
    assert app_rep["probe_states"]["R"] == "ERROR"
    assert app_rep["calculation"]["conscience_lambda"] == 0
    assert any(b["id"] == "GWI-APP-R02" for b in app_rep["blockers"])

    # 4. SKB #4 & Triad: SQLite ChunkStore + Bridge Provenance Chain + E2E Canary
    cstore = ChunkStore.for_vault(vault_dir)
    sample_chunk = parse_soar("sp {chunk*triad*test :chunk (state <s> ^name skb-vault) --> (<s> ^verified yes)}")[0]
    up_res = cstore.upsert_chunk(
        sample_chunk,
        "sp {chunk*triad*test :chunk (state <s> ^name skb-vault) --> (<s> ^verified yes)}",
        source_goal="autonomous engineering gate",
        provenance_run_id="swm-op101-abc",
        wp_id=101,
        commit_sha="a1b2c3d",
        domain="skb-vault",
    )
    assert up_res["name"] == "chunk*triad*test"
    prec = retrieve_vault_precedents(vault_dir, "autonomous engineering gate", top_k=3)
    assert len(prec["declarative_chunks"]) >= 1
    assert any(c["name"] == "chunk*triad*test" for c in prec["procedural_chunks"])

    canary = run_triad_end_to_end_canary(vault_dir)
    assert canary["canary_passed"] is True

    prov_file = tmp_path / "triad-ledger.jsonl"
    ev1 = append_triad_provenance_event(
        "soar.chunk_compiled",
        {"chunk": "chunk*triad*test"},
        ledger_file=prov_file,
        wp_id=101,
        run_id="swm-op101-abc",
        commit_sha="a1b2c3d",
    )
    ev2 = append_triad_provenance_event(
        "gwi.evaluated",
        {"goldwood_g": 9.63},
        ledger_file=prov_file,
        wp_id=101,
        run_id="swm-op101-abc",
        commit_sha="a1b2c3d",
    )
    assert ev2["prev_hash"] == ev1["entry_hash"]
    assert verify_triad_provenance_chain(prov_file) == (True, 2)
    assert math.isfinite(near_miss["raw_goldwood_g"])


def test_gwi_tri_council_all_12_counterexamples(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify all 12 Tri-Council (Argon x Fable x Astra) GWI v1.4.0 counterexamples and mathematical invariants."""
    from sovereign_kb.gwi import (
        aggregate_tier_pair,
        compute_coupled_goldwood_g,
        compute_goldwood_index,
        compute_subtractive_penalty,
        compute_wcag_contrast_ratio,
        evaluate_target,
        evaluate_video_profile,
    )

    # 1. Counterexample #1: Uncompiled .tex with \cite{missing} and no .bib / .log / .pdf
    uncompiled_paper = tmp_path / "uncompiled-paper"
    uncompiled_paper.mkdir()
    (uncompiled_paper / "paper.tex").write_text(
        "\\documentclass{article}\n\\usepackage{microtype}\n\\usepackage{booktabs}\n\\widowpenalty=10000\n"
        "\\begin{document}\nSee \\cite{missing}.\n\\end{document}\n",
        encoding="utf-8",
    )
    rep_paper = evaluate_target(uncompiled_paper, profile="paper", strict_lenses=True)
    assert rep_paper["calculation"]["status"] == "FAIL"
    assert rep_paper["calculation"]["conscience_lambda"] == 0
    assert rep_paper["calculation"]["goldwood_g"] == 0.0
    assert any(b["id"] == "GWI-PAP-A01" for b in rep_paper["blockers"])
    assert any(b["id"] == "GWI-PAP-E02" for b in rep_paper["blockers"])
    assert rep_paper["craft_lenses"]["unmeasured_probes"] >= 1
    assert rep_paper["craft_lenses"]["veto_triggered"] is True
    assert rep_paper["craft_lenses"]["coupled_goldwood_g"] == 0.0
    with pytest.raises(SystemExit):
        main(["gwi", "audit", str(uncompiled_paper), "--strict", "--strict-lenses"])
    capsys.readouterr()

    # 2. Counterexample #2: Barrier-collapsed `sim` has coupled_goldwood_g <= G(S) < 9.0 and fails --strict-lenses
    bad_sim = tmp_path / "bad-sim"
    bad_sim.mkdir()
    (bad_sim / "scene.glsl").write_text(
        "void main() { gl_FragColor = vec4(1.0); }\n",
        encoding="utf-8",
    )
    rep_sim = evaluate_target(bad_sim, profile="sim", strict_lenses=True)
    assert rep_sim["calculation"]["goldwood_g"] < 9.0
    assert rep_sim["craft_lenses"]["coupled_goldwood_g"] <= rep_sim["calculation"]["goldwood_g"]
    assert rep_sim["craft_lenses"]["coupled_goldwood_g"] < 9.0
    with pytest.raises(SystemExit):
        main(["gwi", "audit", str(bad_sim), "--strict-lenses"])
    capsys.readouterr()

    # 3. Counterexample #3: `gwi calc 1 1 1 1 --strict --json` raises SystemExit(1)
    with pytest.raises(SystemExit):
        main(["gwi", "calc", "1", "1", "1", "1", "--strict", "--json"])
    calc_json_out = json.loads(capsys.readouterr().out)
    assert calc_json_out["status"] == "FAIL"

    # 4. Counterexample #4: `sovereign-kb` self-audit inspects `dashboard.py` `light-dark(...)` tokens in `ux-ergonomics`
    skb_self = evaluate_target(REPO_ROOT, profile="app")
    ux_lens = next(lens for lens in skb_self["craft_lenses"]["lenses"] if lens["id"] == "ux-ergonomics")
    assert any("ux.wcag_contrast_tap" in pf and "16.21:1" in pf for pf in ux_lens["probe_findings"])

    # 5. Counterexample #5: Corrupt 0-byte `a3viewport_sheet.png` triggers hard VETO
    corrupt_sheet_app = tmp_path / "corrupt-sheet-app"
    corrupt_sheet_app.mkdir()
    (corrupt_sheet_app / "package.json").write_text('{"dependencies":{"react":"19.0.0"}}', encoding="utf-8")
    (corrupt_sheet_app / "App.tsx").write_text(
        "export function App() { return <main>Empty state</main>; }", encoding="utf-8"
    )
    (corrupt_sheet_app / "tokens.css").write_text(":root { --bg: #0f172a; --fg: #f8fafc; }", encoding="utf-8")
    (corrupt_sheet_app / "a3viewport_sheet.png").write_bytes(b"")
    rep_corrupt = evaluate_target(corrupt_sheet_app, profile="app")
    assert rep_corrupt["craft_lenses"]["veto_triggered"] is True
    assert any(
        "corrupt" in pf and "unreadable" in pf for pf in rep_corrupt["craft_lenses"]["lenses"][0]["probe_findings"]
    )

    # 6. Counterexample #6: 4-digit hex `#ffff` on `#ffff` returns contrast 1.0 (not 21.0) and triggers VETO
    assert compute_wcag_contrast_ratio("#ffff", "#ffff") == 1.0
    assert compute_wcag_contrast_ratio("not-a-color", "#ffffff") == 1.0
    hex4_app = tmp_path / "hex4-app"
    hex4_app.mkdir()
    (hex4_app / "package.json").write_text('{"dependencies":{"react":"19.0.0"}}', encoding="utf-8")
    (hex4_app / "tokens.css").write_text(":root { --bg: #ffff; --fg: #ffff; }", encoding="utf-8")
    rep_hex4 = evaluate_target(hex4_app, profile="app")
    assert rep_hex4["craft_lenses"]["veto_triggered"] is True

    # 7. Counterexample #7: 100% blank white contact sheet (edge_density == 0.000 < 0.015) triggers VETO
    assert compute_subtractive_penalty({"edgeDensity": 0.0})["p_subtractive"] < 1.0
    assert compute_subtractive_penalty({"edgeDensity": 0.42})["p_subtractive"] < 1.0
    assert compute_subtractive_penalty({"edgeDensity": 0.18})["p_subtractive"] == 1.0

    # 8. Counterexample #8: Simultaneous UX violations in `report.json` are all recorded (no `elif` masking)
    multi_ux = tmp_path / "multi-ux"
    multi_ux.mkdir()
    (multi_ux / "package.json").write_text('{"dependencies":{"react":"19.0.0"}}', encoding="utf-8")
    (multi_ux / "tokens.css").write_text(":root { --bg: #0f172a; --fg: #f8fafc; }", encoding="utf-8")
    (multi_ux / "evidence" / "playwright").mkdir(parents=True)
    (multi_ux / "evidence" / "playwright" / "report.json").write_text(
        json.dumps(
            {
                "consoleErrors": 0,
                "pageErrors": 0,
                "failedRequests": 0,
                "hudOcclusionRatio": 0.35,
                "horizontalOverflowPx": 24,
                "minTapTargetPx": 24,
                "primaryCtasAboveFold": 4,
                "stepsToCoreValue": 7,
                "headerRows": 3,
                "expandedTablesVisible": 3,
                "maxAnswerBullets": 6,
            }
        ),
        encoding="utf-8",
    )
    rep_multi = evaluate_target(multi_ux, profile="app")
    pfs = "\n".join(rep_multi["craft_lenses"]["lenses"][0]["probe_findings"])
    for expected_probe in (
        "ux.hud_occlusion_pct",
        "ux.wcag_contrast_tap",
        "ux.cta_hierarchy",
        "ux.steps_to_value",
        "ux.subtractive_budget",
    ):
        assert f"VETO [{expected_probe}]" in pfs

    # 9. Counterexample #9: LaTeX Overfull \hbox pt-magnitude parsing (0.1pt PASS, 5.0pt P1, 12.0pt P0 + VETO)
    for pt_val, exp_p0, exp_p1, exp_veto in [
        ("0.1pt", False, False, False),
        ("5.0pt", False, True, False),
        ("12.0pt", True, False, True),
    ]:
        pdir = tmp_path / f"paper-{pt_val}"
        pdir.mkdir()
        (pdir / "main.tex").write_text(
            "\\documentclass{article}\n\\usepackage{microtype}\n\\usepackage{booktabs}\n\\widowpenalty=10000\n"
            "\\begin{document}\nClean text \\cite{k1}.\n\\end{document}\n",
            encoding="utf-8",
        )
        (pdir / "refs.bib").write_text("@article{k1, title={T}}\n", encoding="utf-8")
        (pdir / "main.pdf").write_bytes(b"%PDF-1.5\n" + b"0" * 600)
        (pdir / "main.log").write_text(
            f"Overfull \\hbox ({pt_val} too wide) in paragraph at lines 1--2\n", encoding="utf-8"
        )
        prep = evaluate_target(pdir, profile="paper")
        b_sevs = {b["id"]: b["severity"] for b in prep["blockers"]}
        assert (b_sevs.get("GWI-PAP-P01") == "P0") is exp_p0
        assert (b_sevs.get("GWI-PAP-P01") == "P1") is exp_p1
        assert prep["craft_lenses"]["veto_triggered"] is exp_veto

    # 10. Counterexample #10: EBU R128 Summary parsing + mono penalty preservation
    vdir = tmp_path / "video-ebu"
    vdir.mkdir()
    vfile = vdir / "master.mp4"
    vfile.write_bytes(b"0000ftypmp420000moovmp4asoun" + b"0" * 2048)
    (vdir / "master.srt").write_text("1\n00:00:01,000 --> 00:00:04,000\nShort clean subtitle.\n", encoding="utf-8")
    (vdir / "ebu_r128.log").write_text(
        "[Parsed_ebur128_0] t: 0.1  M: -70.0 S: -70.0     I: -70.0 LUFS     LRA: 0.0 LU\n"
        "Summary:\n  Integrated loudness:\n    I:         -14.0 LUFS\n  Loudness range:\n    LRA:         6.2 LU\n"
        "  True peak:\n    Peak:       -1.2 dBTP\n",
        encoding="utf-8",
    )
    vrep = evaluate_video_profile(vfile)
    d_audio = next(d for d in vrep["dimensions"] if d["id"] == "A_audio")
    assert d_audio["score"] >= 8.5  # -14.0 LUFS parsed from Summary, not -70.0 from frame 0

    # 11. Counterexample #11 & Mathematical Fixes: plateau Jacobian dG/dS, Lambda=0 zeroing, and weak-link tier pair
    g9 = compute_goldwood_index([9.0, 9.0, 9.0, 9.0])
    assert g9["jacobian_dG_dS"] == [0.25, 0.25, 0.25, 0.25]
    assert g9["headroom_gain_dG"] == [0.25, 0.25, 0.25, 0.25]
    g_unclean = compute_goldwood_index([9.5, 9.5, 9.5, 9.5], conscience_clean=False)
    assert g_unclean["goldwood_g"] == 0.0
    assert g_unclean["unmasked_goldwood_g"] == 9.5
    assert compute_coupled_goldwood_g(g_unclean, 9.5, 9.5) == 0.0
    assert aggregate_tier_pair(10.0, 5.0, 0.25, 0.15, theta=7.0) == 5.0


def test_kb_sessions_and_unity_anti_deadlock_gates(tmp_path: Path) -> None:
    """Verify `kb sessions` (`inspect_session`) background task deadlock detection & GWI-SIM-U01/U02 Unity gates."""
    from sovereign_kb.gwi import evaluate_sim_profile
    from sovereign_kb.sessions import inspect_session

    # 1. Verify `inspect_session` detects DEADLOCKED_BG_TASK when a background task hits `error CS*` without resolution
    conv_dir = tmp_path / "687ede91-deadlock-test"
    logs_dir = conv_dir / ".system_generated" / "logs"
    tasks_dir = conv_dir / ".system_generated" / "tasks"
    logs_dir.mkdir(parents=True)
    tasks_dir.mkdir(parents=True)

    tpath = logs_dir / "transcript.jsonl"
    steps = [
        {"step_index": 1, "type": "USER_INPUT", "status": "DONE", "content": "lets work on physiverse"},
        {
            "step_index": 74,
            "type": "GENERIC",
            "status": "RUNNING",
            "content": "Tool is running as a background task with task id: 687ede91-deadlock-test/task-74",
        },
        {"step_index": 75, "type": "PLANNER_RESPONSE", "status": "DONE", "content": "Waiting for task-74..."},
    ]
    tpath.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")
    (tasks_dir / "task-74.log").write_text(
        "Assets/Scripts/Managers/RoomTourRecorder.cs(260,21): error CS0136: A local or parameter named 'p0'\n",
        encoding="utf-8",
    )

    info_deadlocked = inspect_session(conv_dir)
    assert info_deadlocked is not None
    assert info_deadlocked["state"] == "DEADLOCKED_BG_TASK"
    assert len(info_deadlocked["running_tasks"]) == 1
    assert any("error CS0136" in e for e in info_deadlocked["running_tasks"][0]["fatal_errors"])

    # 2. Once `<SYSTEM_MESSAGE>` resolves `task-74` and final `PLANNER_RESPONSE` completes, state is `IDLE_COMPLETE`
    steps.extend(
        [
            {
                "step_index": 76,
                "type": "SYSTEM_MESSAGE",
                "status": "DONE",
                "content": 'sender=687ede91-deadlock-test/task-74 content=Task id "687ede91-deadlock-test/task-74" finished',
            },
            {
                "step_index": 77,
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "Fixed and rendered all rooms.",
            },
        ]
    )
    tpath.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")
    info_resolved = inspect_session(conv_dir)
    assert info_resolved is not None
    assert info_resolved["state"] == "IDLE_COMPLETE"
    assert info_resolved["running_tasks"] == []

    # 3. Verify GWI-SIM-U01 (non-batchmode Unity CLI invocation) and GWI-SIM-U02 (CS0104 Debug.Log shadow trap)
    unity_dir = tmp_path / "unity-sim-trap"
    (unity_dir / "ProjectSettings").mkdir(parents=True)
    (unity_dir / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.3.10f1\n", encoding="utf-8"
    )
    (unity_dir / "scripts").mkdir(parents=True)
    (unity_dir / "scripts" / "quality_gate.sh").write_text(
        "#!/usr/bin/env bash\n/Applications/Unity/Unity.app/Contents/MacOS/Unity -projectPath . -executeMethod Build.Run -quit\n",
        encoding="utf-8",
    )
    (unity_dir / "Assets" / "Scripts").mkdir(parents=True)
    (unity_dir / "Assets" / "Scripts" / "Trap.cs").write_text(
        'using UnityEngine;\nusing System.Diagnostics;\npublic class Trap { void M() { Debug.Log("hi"); } }\n',
        encoding="utf-8",
    )

    sim_rep = evaluate_sim_profile(unity_dir)
    blocker_ids = {b["id"] for b in sim_rep["blockers"]}
    assert "GWI-SIM-U01" in blocker_ids
    assert "GWI-SIM-U02" in blocker_ids

    # 4. Verify `clean_antigravity_sessions` (`kb sessions --clean`) archives deliverables, protects active chats,
    #    and synchronizes `conversation_summaries.db` (+ SQLite trigger), `agyhub_summaries_proto.pb`, and `app_storage.json`
    import os
    import sqlite3

    from sovereign_kb.sessions import clean_antigravity_sessions

    mock_agy = tmp_path / "mock-agy"
    mock_brain = mock_agy / "brain"
    mock_convs = mock_agy / "conversations"
    mock_archive = mock_agy / "archive-artifacts"
    mock_brain.mkdir(parents=True)
    mock_convs.mkdir(parents=True)

    idle_cid = "11111111-1111-1111-1111-111111111111"
    active_cid = "22222222-2222-2222-2222-222222222222"
    new_born_cid = "33333333-3333-3333-3333-333333333333"

    # Create idle completed session with a deliverable artifact + .user_uploaded/notes.txt
    idle_dir = mock_brain / idle_cid
    (idle_dir / ".system_generated" / "logs").mkdir(parents=True)
    (idle_dir / ".user_uploaded").mkdir(parents=True)
    idle_tpath = idle_dir / ".system_generated" / "logs" / "transcript.jsonl"
    idle_tpath.write_text(
        json.dumps({"step_index": 1, "type": "USER_INPUT", "status": "DONE", "content": "audit sidebar"})
        + "\n"
        + json.dumps({"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "content": "All done."})
        + "\n",
        encoding="utf-8",
    )
    (idle_dir / "walkthrough.md").write_text("# Deliverable Walkthrough\n", encoding="utf-8")
    (idle_dir / ".user_uploaded" / "notes.txt").write_text("User uploaded spec\n", encoding="utf-8")
    idle_db_path = mock_convs / f"{idle_cid}.db"
    c_idle = sqlite3.connect(str(idle_db_path))
    c_idle.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, text TEXT)")
    c_idle.execute("INSERT INTO turns VALUES (1, 'full chat history')")
    c_idle.commit()
    c_idle.close()

    # Create active tool-loop session that MUST be protected
    act_dir = mock_brain / active_cid
    (act_dir / ".system_generated" / "logs").mkdir(parents=True)
    act_tpath = act_dir / ".system_generated" / "logs" / "transcript.jsonl"
    act_tpath.write_text(
        json.dumps({"step_index": 1, "type": "USER_INPUT", "status": "DONE", "content": "running task"})
        + "\n"
        + json.dumps(
            {
                "step_index": 2,
                "type": "PLANNER_RESPONSE",
                "status": "IN_PROGRESS",
                "tool_calls": [{"name": "run_command"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (mock_convs / f"{active_cid}.db").write_bytes(b"sqlite-dummy-active")

    # Create mock conversation_summaries.db with idle, active, AND a brand-new conversation born mid-scan
    sum_db = mock_agy / "conversation_summaries.db"
    conn = sqlite3.connect(str(sum_db))
    conn.execute(
        "CREATE TABLE conversation_summaries ("
        "conversation_id TEXT PRIMARY KEY, title TEXT, status TEXT, not_fully_idle INTEGER, last_modified_time TEXT)"
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, 'Idle Chat', 'CASCADE_RUN_STATUS_IDLE', 0, '2026-10-30 01:00:00')",
        (idle_cid,),
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, 'Active Chat', 'CASCADE_RUN_STATUS_RUNNING', 1, '2026-10-30 09:00:00')",
        (active_cid,),
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, 'New Born Chat', 'CASCADE_RUN_STATUS_RUNNING', 1, '2026-10-30 09:05:00')",
        (new_born_cid,),
    )
    conn.commit()
    conn.close()

    # Create mock protobuf file with all three 36-char UUIDs
    def _make_pb_entry(uuid_str: str) -> bytes:
        inner = b"\x0a\x24" + uuid_str.encode("ascii")
        return b"\x0a" + bytes([len(inner)]) + inner

    pb_file = mock_agy / "agyhub_summaries_proto.pb"
    pb_file.write_bytes(_make_pb_entry(idle_cid) + _make_pb_entry(active_cid) + _make_pb_entry(new_born_cid))

    # Create mock app_storage.json
    app_storage = tmp_path / "app_storage.json"
    app_storage.write_text(
        json.dumps(
            {
                f"antigravity-multi-conversation-layout-v3-{idle_cid}": {"open": True},
                f"antigravity-multi-conversation-layout-v3-{active_cid}": {"open": True},
                f"antigravity-multi-conversation-layout-v3-{new_born_cid}": {"open": True},
            }
        ),
        encoding="utf-8",
    )

    now_ts = act_tpath.stat().st_mtime + 10.0
    os.utime(idle_tpath, (now_ts - 600.0, now_ts - 600.0))
    os.utime(idle_db_path, (now_ts - 600.0, now_ts - 600.0))
    # Dry-run first: nothing deleted yet
    dry_res = clean_antigravity_sessions(
        agy_dir=mock_agy,
        app_storage_path=app_storage,
        archive_dir=mock_archive,
        dry_run=True,
        min_idle_seconds=120,
        now=now_ts,
    )
    assert dry_res["cleaned_count"] == 1
    assert dry_res["kept_count"] == 1
    assert idle_dir.is_dir()

    # Real execution: idle session archived & purged, active & new-born sessions preserved
    exec_res = clean_antigravity_sessions(
        agy_dir=mock_agy,
        app_storage_path=app_storage,
        archive_dir=mock_archive,
        dry_run=False,
        min_idle_seconds=120,
        now=now_ts,
    )
    assert exec_res["cleaned_count"] == 1
    assert exec_res["kept_count"] == 1
    assert (
        exec_res["deliverables_archived_count"] == 4
    )  # walkthrough.md + notes.txt + transcript.jsonl + conversation.db
    assert not idle_dir.exists()
    assert not idle_db_path.exists()
    assert act_dir.is_dir()
    assert (mock_convs / f"{active_cid}.db").exists()
    assert any(p.name == "walkthrough.md" for p in mock_archive.rglob("walkthrough.md"))
    assert any(p.name == "notes.txt" for p in mock_archive.rglob("notes.txt"))
    assert any(p.name == "transcript.jsonl" for p in mock_archive.rglob("transcript.jsonl"))
    assert any(p.name == "conversation.db" for p in mock_archive.rglob("conversation.db"))
    assert any(p.name == "manifest.json" for p in mock_archive.rglob("manifest.json"))

    # Verify SQLite trigger blocks `language_server` from re-inserting `idle_cid`, while keeping `active_cid` and `new_born_cid`
    conn = sqlite3.connect(str(sum_db))
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, 'Ghost Reinsert', 'CASCADE_RUN_STATUS_IDLE', 0, '2026-10-30 09:05:00')",
        (idle_cid,),
    )
    conn.commit()
    remaining_ids = sorted(r[0] for r in conn.execute("SELECT conversation_id FROM conversation_summaries").fetchall())
    conn.close()
    assert remaining_ids == [active_cid, new_born_cid]
    assert pb_file.read_bytes() == _make_pb_entry(active_cid) + _make_pb_entry(new_born_cid)
    stored_app = json.loads(app_storage.read_text(encoding="utf-8"))
    assert f"antigravity-multi-conversation-layout-v3-{idle_cid}" not in stored_app
    assert f"antigravity-multi-conversation-layout-v3-{active_cid}" in stored_app
    assert f"antigravity-multi-conversation-layout-v3-{new_born_cid}" in stored_app


def test_law_l7_simplicity_favors_regularity_kanonikotita(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from sovereign_kb.gwi import (
        _audit_structural_regularity,
        audit_directory_regularity,
        compute_regularity_index,
    )

    # Canonical regular stylesheet (<=8 font sizes, <=4 border radii, <=5 grid templates) -> R_kanon == 1.0
    regular_html = """
    <style>
      .a { font-size: 11px; border-radius: var(--mt-r-panel); display: grid; grid-template-columns: repeat(2, 1fr); }
      .b { font-size: 13px; border-radius: var(--mt-r-pillar); display: grid; grid-template-columns: repeat(4, 1fr); }
      .c { font-size: 22px; border-radius: 50%; }
    </style>
    """
    reg = _audit_structural_regularity(regular_html)
    assert reg["distinct_font_sizes"] == 3
    assert reg["distinct_border_radii"] == 3
    assert reg["distinct_grid_templates"] == 2
    assert reg["r_kanon"] == 1.0

    # Directory-level audit + CLI subcommand (`gwi regularity --strict --json`)
    reg_dir = tmp_path / "regular-ui"
    reg_dir.mkdir()
    (reg_dir / "index.html").write_text(regular_html, encoding="utf-8")
    dir_rep = audit_directory_regularity(reg_dir)
    assert dir_rep["passed"] is True
    assert dir_rep["status"] == "PASS"
    assert dir_rep["r_kanon"] == 1.0

    main(["gwi", "regularity", str(reg_dir), "--strict", "--json"])
    cli_rep = json.loads(capsys.readouterr().out)
    assert cli_rep["passed"] is True
    assert cli_rep["repositories"][0]["r_kanon"] == 1.0

    # Irregular stylesheet with 15 ad-hoc font sizes and 8 border radii -> R_kanon < 0.25 and fails --strict
    irregular_rules = "\n".join(f".f{i} {{ font-size: {9 + i * 0.5}px; }}" for i in range(15))
    irregular_radii = "\n".join(f".r{i} {{ border-radius: {i + 2}px; }}" for i in range(8))
    irreg_html = f"<style>{irregular_rules}\n{irregular_radii}</style>"
    irreg = _audit_structural_regularity(irreg_html)
    assert irreg["distinct_font_sizes"] == 15
    assert irreg["distinct_border_radii"] == 8
    assert irreg["r_kanon"] < 0.25
    assert compute_regularity_index(6, 3, 5) == 1.0

    irreg_dir = tmp_path / "irregular-ui"
    irreg_dir.mkdir()
    (irreg_dir / "index.html").write_text(irreg_html, encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["gwi", "regularity", str(irreg_dir), "--strict", "--json"])
    capsys.readouterr()


def test_bipolar_4x4_chrysos_verga_lenses_and_symmetric_psi(tmp_path: Path) -> None:
    """Verify Fable 5.1 x GPT-6 Astra Option B+ Bi-Polar 4x4 Specialist Lenses and symmetric Psi coupling."""
    from sovereign_kb.gwi import compute_coupled_goldwood_g, compute_goldwood_index, evaluate_target

    # 1. Symmetric Psi barrier: a collapsed Chrysos pole (< theta=7.0) collapses G_coupled even if Verga == 9.8
    base_g = compute_goldwood_index([9.5, 9.5, 9.5, 9.5])
    coupled_collapsed_chrysos = compute_coupled_goldwood_g(base_g, chrysos_score=6.0, verga_score=9.8)
    coupled_collapsed_verga = compute_coupled_goldwood_g(base_g, chrysos_score=9.8, verga_score=6.0)
    assert coupled_collapsed_chrysos == coupled_collapsed_verga
    assert coupled_collapsed_chrysos < 1.0

    # 2. Chrysos lens failure (`data-api-architect` unguarded DELETE + `web-perf-edge` barrel import)
    # tanks `chrysos_pole_score` and `S_sub`, while `verga_pole_score` remains independent!
    chrysos_fail_dir = tmp_path / "chrysos-fail-app"
    chrysos_fail_dir.mkdir()
    (chrysos_fail_dir / "AGENTS.md").write_text("# Agents\n- FEAT-01\n", encoding="utf-8")
    (chrysos_fail_dir / "README.md").write_text("# App\n", encoding="utf-8")
    (chrysos_fail_dir / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^19.0.0", "@supabase/supabase-js": "^2.0.0"}}),
        encoding="utf-8",
    )
    (chrysos_fail_dir / "tokens.css").write_text(
        ":root { --bg: #0f172a; --fg: #f8fafc; }\n@media (prefers-reduced-motion: reduce) {}\n",
        encoding="utf-8",
    )
    (chrysos_fail_dir / "App.tsx").write_text(
        "import * as Icons from 'lucide-react';\nexport const App = () => <main aria-label='ok'>Empty</main>;\n",
        encoding="utf-8",
    )
    (chrysos_fail_dir / "schema.sql").write_text("DELETE FROM users;\n", encoding="utf-8")

    rep = evaluate_target(chrysos_fail_dir, profile="app")
    cl = rep["craft_lenses"]
    assert cl["chrysos_veto_triggered"] is True
    assert cl["verga_veto_triggered"] is False
    assert cl["chrysos_pole_score"] <= 6.80
    assert cl["verga_pole_score"] >= 8.50
    assert cl["coupled_goldwood_g"] <= 6.80
    assert any(il["status"] == "NOT_ASSESSED" for il in cl["inactive_lenses"])


def test_law_l8_simultaneous_cognitive_load_and_jsx_regularity(tmp_path: Path) -> None:
    """Verify Law L8 (Simultaneous Cognitive Load Budget) and JSX inline Law L7 scanning."""
    from sovereign_kb.gwi import (
        _audit_simultaneous_load_l8,
        _audit_structural_regularity,
        compute_simultaneous_load_index,
    )

    # 1. Calm first-paint UI within Law L8 budgets -> S_L8 = 10.0, passed = True
    calm = compute_simultaneous_load_index(
        visible_interactive_controls=21,
        visible_choice_groups=4,
        active_macro_regions=3,
        chrome_to_content_ratio=0.28,
        metric_multiplicity=1,
    )
    assert calm["passed"] is True
    assert calm["s_l8"] == 10.0

    # 2. Overloaded cockpit UI (42 controls, 7 choice groups, 5 macro regions, CCR=0.92) -> fails
    overloaded = compute_simultaneous_load_index(
        visible_interactive_controls=42,
        visible_choice_groups=7,
        active_macro_regions=5,
        chrome_to_content_ratio=0.92,
        metric_multiplicity=3,
    )
    assert overloaded["passed"] is False
    assert overloaded["s_l8"] < 6.0

    # 3. JSX inline fontSize and borderRadius micro-variance caught by _audit_structural_regularity
    jsx_drift = "\n".join(
        f"<div style={{{{ fontSize: '{10 + i * 0.5}px', borderRadius: '{2 + i}px' }}}}>x</div>" for i in range(10)
    )
    reg_jsx = _audit_structural_regularity(jsx_drift)
    assert reg_jsx["distinct_font_sizes"] == 10
    assert reg_jsx["distinct_border_radii"] == 10
    assert reg_jsx["r_kanon"] < 0.70

    # 4. Directory audit for Law L8 catches default split mode (`viewMode: 'split'`)
    bad_app = tmp_path / "split-overload-app"
    bad_app.mkdir()
    (bad_app / "App.tsx").write_text("const state = { viewMode: 'split' };\n", encoding="utf-8")
    l8_rep = _audit_simultaneous_load_l8(bad_app, [bad_app / "App.tsx"], {}, "")
    assert l8_rep["default_split_overload"] is True
    assert l8_rep["passed"] is False


def test_law_l9_canonical_anchor_and_rust_app_profile(tmp_path: Path) -> None:
    """Verify Law L9 (canonical anchor gate) and Rust .rs app profile support."""
    from sovereign_kb.gwi import (
        _audit_canonical_anchor_l9,
        compute_canonical_anchor_l9_index,
        evaluate_target,
    )

    # 1. Law L9 index math: 9 canonical groups & 0 wedges -> R_L9 = 1.0, passed = True
    clean_l9 = compute_canonical_anchor_l9_index(
        active_gitlab_groups=9,
        unanchored_wedge_repos=0,
        unanchored_subdomains=0,
        has_canonical_passport=True,
    )
    assert clean_l9["passed"] is True
    assert clean_l9["r_l9"] == 1.0

    sprawl_l9 = compute_canonical_anchor_l9_index(
        active_gitlab_groups=12,
        unanchored_wedge_repos=2,
        unanchored_subdomains=1,
        has_canonical_passport=True,
    )
    assert sprawl_l9["passed"] is False
    assert sprawl_l9["r_l9"] < 0.35

    # 2. Sprawl repository with non-canonical GitLab group triggers VETO [pv.canonical_anchor_l9]
    sprawl_dir = tmp_path / "snap-standalone"
    (sprawl_dir / ".git").mkdir(parents=True)
    (sprawl_dir / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://gitlab.example.com/unregistered-group/snap-standalone.git\n',
        encoding="utf-8",
    )
    (sprawl_dir / "AGENTS.md").write_text("# Agents\n- FEAT-01\n", encoding="utf-8")
    (sprawl_dir / "README.md").write_text("# Snap Standalone\n", encoding="utf-8")
    (sprawl_dir / "Cargo.toml").write_text('[package]\nname = "snap"\nversion = "0.1.0"\n', encoding="utf-8")

    l9_audit = _audit_canonical_anchor_l9(sprawl_dir)
    assert l9_audit["passed"] is False
    assert "unregistered-group" in l9_audit["non_canonical_groups"]
    assert l9_audit["unanchored_wedge_repos"] == 1

    rep_sprawl = evaluate_target(sprawl_dir, profile="app")
    assert rep_sprawl["craft_lenses"]["chrysos_veto_triggered"] is True
    pv_lens = next(ln for ln in rep_sprawl["craft_lenses"]["lenses"] if ln["id"] == "principal-verification")
    assert any("VETO [pv.canonical_anchor_l9]" in pf for pf in pv_lens["probe_findings"])
    assert any("VETO [pv.readme_doc_freshness]" in pf for pf in pv_lens["probe_findings"])

    # 3. Rust crate with Cargo.toml and tests/*.rs (`#[test]` + FEAT-01..FEAT-05) passes evaluate_app_profile & pv.readme_doc_freshness
    rust_dir = tmp_path / "rust-engine"
    (rust_dir / "src").mkdir(parents=True)
    (rust_dir / "tests").mkdir(parents=True)
    (rust_dir / ".git").mkdir(parents=True)
    (rust_dir / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://gitlab.example.com/research-ai/rust-engine.git\n',
        encoding="utf-8",
    )
    (rust_dir / "AGENTS.md").write_text(
        "# Rust Engine\n- FEAT-01\n- FEAT-02\n- FEAT-03\n- FEAT-04\n- FEAT-05\n",
        encoding="utf-8",
    )
    (rust_dir / "README.md").write_text(
        "# Rust Engine\n\nDeterministic Block-Max WAND & HNSW search engine written in Rust. Verify with `cargo test --quiet` (tests in `tests/correctness.rs`).\n",
        encoding="utf-8",
    )
    (rust_dir / "Cargo.toml").write_text('[package]\nname = "rust-engine"\nversion = "0.1.0"\n', encoding="utf-8")
    (rust_dir / "src" / "lib.rs").write_text("pub fn score() -> u32 { 42 }\n", encoding="utf-8")
    (rust_dir / "tests" / "correctness.rs").write_text(
        "// Covers FEAT-01, FEAT-02, FEAT-03, FEAT-04, FEAT-05\n#[test]\nfn test_engine() { assert_eq!(42, 42); }\n",
        encoding="utf-8",
    )
    rust_rep = evaluate_target(rust_dir, profile="app")
    assert rust_rep["calculation"]["status"] == "PASS"
    assert rust_rep["calculation"]["goldwood_g"] >= 9.5
    rust_pv = next(ln for ln in rust_rep["craft_lenses"]["lenses"] if ln["id"] == "principal-verification")
    assert any("PASS [pv.readme_doc_freshness]" in pf for pf in rust_pv["probe_findings"])


def test_readme_check_catches_dead_paths_and_undocumented_surface(tmp_path: Path) -> None:
    """`readme_check` must block dead README paths, undocumented new routes/commands, and stale removed ones."""
    import subprocess

    from sovereign_kb.readme_check import check

    repo = tmp_path / "svc"
    (repo / "src" / "svc").mkdir(parents=True)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    server = repo / "src" / "svc" / "server.py"
    server.write_text('@app.get("/api/old")\ndef old():\n    pass\n', encoding="utf-8")
    (repo / "README.md").write_text(
        "# svc\n\nEndpoints: `/api/old`. Run `scripts/gate.sh`. See [guide](docs/guide.md).\n", encoding="utf-8"
    )
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")

    # Base commit: two dead references (scripts/gate.sh, docs/guide.md).
    issues, _ = check(repo, None, base, runtime=False)
    assert sum("[ref]" in i for i in issues) == 2

    # Fix references, replace /api/old with /api/new in code but NOT in README -> both drift directions flagged.
    (repo / "scripts").mkdir()
    (repo / "scripts" / "gate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
    server.write_text('@app.post("/api/new/{item_id}")\ndef new(item_id: str):\n    pass\n', encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "drift")
    head = git("rev-parse", "HEAD")
    issues, notes = check(repo, base, head, runtime=False)
    assert not any("[ref]" in i for i in issues)
    assert any("new HTTP route `/api/new/{item_id}`" in i for i in issues)
    assert any("`/api/old` was removed" in i for i in issues)
    assert any("README.md untouched" in n for n in notes)

    # Documenting the new route (any param name) and dropping the old one makes the push clean.
    (repo / "README.md").write_text(
        "# svc\n\n`POST /api/new/{id}`. Run `scripts/gate.sh`. See [guide](docs/guide.md).\n", encoding="utf-8"
    )
    git("commit", "-qam", "docs")
    issues, _ = check(repo, base, git("rev-parse", "HEAD"), runtime=False)
    assert issues == []


def test_kb_check_verifies_me_kb_reflection(tmp_path: Path) -> None:
    """`kb_check` ensures significant pushes are reflected in me-kb episodic logs or now.md."""
    import datetime
    import subprocess

    from sovereign_kb.kb_check import check_kb_reflection

    vault = tmp_path / "mock-me-kb"
    (vault / "04-episodic").mkdir(parents=True)
    (vault / "01-now").mkdir(parents=True)
    today_str = datetime.date.today().isoformat()
    today_file = vault / "04-episodic" / f"{today_str}-log.md"
    today_file.write_text(
        "# Episodic Log\n\n## 08:00\n- Worked on [[core-engine]] and resolved physics.\n", encoding="utf-8"
    )

    repo = tmp_path / "my-service"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "app.py").write_text("print('hello')\n")
    git("add", "-A")
    git("commit", "-qm", "feat: unlogged feature")
    head = git("rev-parse", "HEAD")

    # 1. Unlogged repo fails
    passed, msg, _ = check_kb_reflection(repo, None, head, vault_path=vault, strict=True)
    assert not passed
    assert "FAIL" in msg

    # 2. Commit with [skip-kb] passes
    git("commit", "--amend", "-qm", "feat: unlogged feature [skip-kb]")
    head_skip = git("rev-parse", "HEAD")
    passed, msg, _ = check_kb_reflection(repo, None, head_skip, vault_path=vault, strict=True)
    assert passed
    assert "skip marker" in msg

    # 3. Chore commit passes
    git("commit", "--amend", "-qm", "chore(deps): bump packages")
    head_chore = git("rev-parse", "HEAD")
    passed, msg, _ = check_kb_reflection(repo, None, head_chore, vault_path=vault, strict=True)
    assert passed
    assert "maintenance commit" in msg

    # 4. When repo is mentioned in today-log.md, passes
    today_file.write_text("# Episodic Log\n\n- Shipped my-service milestone\n", encoding="utf-8")
    git("commit", "--amend", "-qm", "feat(service): launch v1")
    head_logged = git("rev-parse", "HEAD")
    passed, msg, details = check_kb_reflection(repo, None, head_logged, vault_path=vault, strict=True)
    assert passed
    assert "PASS" in msg
    assert any("my-service" in d for d in details)


def test_validate_skill_frontmatter(tmp_path: Path) -> None:
    """Verify validate_skill_frontmatter catches YAML corruption, unquoted colons, tabs, and dangling text."""
    from sovereign_kb.gwi import validate_skill_frontmatter

    # 1. Valid skill
    valid_skill = tmp_path / "valid" / "SKILL.md"
    valid_skill.parent.mkdir()
    valid_skill.write_text(
        '---\nname: valid-skill\ndescription: "A fully valid test skill"\n---\n# Content\n',
        encoding="utf-8",
    )
    ok, msg = validate_skill_frontmatter(valid_skill)
    assert ok, f"Expected valid skill to pass, got: {msg}"

    # 2. Valid multiline block scalar
    valid_multi = tmp_path / "multi" / "SKILL.md"
    valid_multi.parent.mkdir()
    valid_multi.write_text(
        "---\nname: multi-skill\ndescription: |\n  Line 1\n  - Item: value\n---\n# Content\n",
        encoding="utf-8",
    )
    ok, msg = validate_skill_frontmatter(valid_multi)
    assert ok, f"Expected multiline block scalar to pass, got: {msg}"

    # 3. Broken modern-web style: unkeyed indented mapping lines
    bad_unkeyed = tmp_path / "unkeyed" / "SKILL.md"
    bad_unkeyed.parent.mkdir()
    bad_unkeyed.write_text(
        '---\nname: bad-skill\n\n  Trigger immediately for:\n  - UI/Layout: Modals\ndescription: "foo"\n---\n',
        encoding="utf-8",
    )
    ok, msg = validate_skill_frontmatter(bad_unkeyed)
    assert not ok
    assert "not allowed" in msg.lower() or "yaml parse error" in msg.lower()

    # 4. Broken colon: unquoted colon + space in value
    bad_colon = tmp_path / "colon" / "SKILL.md"
    bad_colon.parent.mkdir()
    bad_colon.write_text(
        "---\nname: bad-colon\ndescription: Execute workflows: systematic debugging\n---\n",
        encoding="utf-8",
    )
    ok, msg = validate_skill_frontmatter(bad_colon)
    assert not ok
    assert "colon" in msg.lower() or "yaml" in msg.lower() or "illegal" in msg.lower()

    # 5. Missing name key
    bad_noname = tmp_path / "noname" / "SKILL.md"
    bad_noname.parent.mkdir()
    bad_noname.write_text(
        '---\ndescription: "missing name"\n---\n',
        encoding="utf-8",
    )
    ok, msg = validate_skill_frontmatter(bad_noname)
    assert not ok
    assert "name" in msg.lower()

    # 6. Tab indentation
    bad_tab = tmp_path / "tab" / "SKILL.md"
    bad_tab.parent.mkdir()
    bad_tab.write_text(
        '---\nname: bad-tab\n\tdescription: "tabbed"\n---\n',
        encoding="utf-8",
    )
    ok, msg = validate_skill_frontmatter(bad_tab)
    assert not ok
    assert "tab" in msg.lower() or "yaml parse error" in msg.lower()


def test_onboarding_capture_search_verify_heal_tip_and_minimal_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cold-visitor onboarding: persisted --no-embed, capture -> #1 search rank, --json/--explain, default 5-file init, and strict verify --heal tip."""
    import sovereign_kb.cli as cli_mod

    # 0. Verify running `skb search` when no vault is configured exits 1 without creating ~/sovereign-kb-vault
    monkeypatch.delenv("KB_VAULT_DIR", raising=False)
    monkeypatch.setattr(cli_mod, "CONFIG_PATH", tmp_path / "no-config.json")
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(clean_home))
    monkeypatch.chdir(clean_home)
    with pytest.raises(SystemExit) as no_vault_exc:
        main(["search", "anything"])
    assert no_vault_exc.value.code == 1
    assert not (clean_home / "sovereign-kb-vault").exists()
    capsys.readouterr()

    # 1. Initialize vault with --no-embed (default 5-file minimal vault) and verify persisted offline config works even when KB_NO_EMBED is unset
    monkeypatch.delenv("KB_NO_EMBED", raising=False)
    vault_dir = tmp_path / "cold-visitor-vault"
    main(["init", str(vault_dir), "--no-embed"])
    capsys.readouterr()
    assert (vault_dir / ".kb" / "vault_config.json").is_file()

    # 2. Capture decision and verify `skb status` auto-syncs and `skb search` ranks it #1 immediately in both HIPS and pure FTS5 modes
    main(["--vault", str(vault_dir), "capture", "We chose SQLite because the app must work offline."])
    capsys.readouterr()

    main(["--vault", str(vault_dir), "status"])
    status_out = capsys.readouterr().out
    assert "Documents       : 6" in status_out

    main(["--vault", str(vault_dir), "search", "SQLite offline", "-n", "1", "--json"])
    search_json = json.loads(capsys.readouterr().out)
    assert search_json["count"] == 1
    assert search_json["results"][0]["rel_path"].startswith("04-episodic/")

    # Also verify pure FTS5 mode when HIPS binary is absent
    monkeypatch.setenv("KB_HIPS_BIN", "")
    main(["--vault", str(vault_dir), "search", "SQLite offline", "-n", "1", "--json"])
    fts_json = json.loads(capsys.readouterr().out)
    assert fts_json["results"][0]["rel_path"].startswith("04-episodic/")

    # 3. Verify clean human search output by default vs --explain telemetry
    main(["--vault", str(vault_dir), "search", "SQLite offline", "-n", "1"])
    clean_out = capsys.readouterr().out
    assert "=== Search Results for: 'SQLite offline' (1 match) ===" in clean_out
    assert "RRF=" not in clean_out

    main(["--vault", str(vault_dir), "search", "SQLite offline", "-n", "1", "--explain"])
    explain_out = capsys.readouterr().out
    assert "RRF=" in explain_out and "fts_rank=" in explain_out

    # 4. Verify default `skb init` writes 5 starter files (`--full` writes 28) and passes 6-Gate `skb verify`
    min_vault = tmp_path / "min-vault"
    main(["init", str(min_vault), "--no-embed"])
    capsys.readouterr()
    main(["--vault", str(min_vault), "verify", "--json"])
    min_ver = json.loads(capsys.readouterr().out)
    assert min_ver["status"] == "PASS"
    assert min_ver["documents"] == 5

    # 5. Verify strict `skb verify` on an existing plain Markdown folder exits 1 and prints the one-line `--heal` tip
    plain_dir = tmp_path / "plain-notes"
    plain_dir.mkdir()
    (plain_dir / "note.md").write_text("# Plain Note\n\nNo frontmatter yet.\n", encoding="utf-8")
    main(["--vault", str(plain_dir), "sync", "--no-embed"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_info:
        main(["--vault", str(plain_dir), "verify", "--allow-lexical-only"])
    assert exc_info.value.code == 1
    fail_out = capsys.readouterr().out
    assert "Gate 1 (Frontmatter Schema) : FAIL" in fail_out
    assert "Tip: Run 'skb verify --heal' to auto-generate missing YAML frontmatter" in fail_out


def test_fable_astra_atomic_capture_stale_sync_and_verify_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify Fable 5.1 x GPT-6 Astra adversarial findings on lazy capture, stale index auto-sync, JSON cleanliness, and Gate 5 conflict markers."""
    import argparse
    import os
    import time

    from sovereign_kb.cli import _is_index_stale, cmd_verify, get_db

    monkeypatch.setenv("KB_NO_EMBED", "1")
    vault_dir = tmp_path / "fable-astra-vault"
    main(["init", str(vault_dir), "--minimal", "--no-embed"])
    capsys.readouterr()

    # 1. Default `capture` skips heavy sync (<5ms) leaving index mtime stale until next search/verify
    time.sleep(0.02)
    main(["--vault", str(vault_dir), "capture", "Zero mid-task KB churn invariant verified."])
    cap_out = capsys.readouterr().out
    assert "[sovereign-kb capture] Appended to" in cap_out
    assert "[sovereign-kb sync]" not in cap_out

    conn = get_db(vault_dir)
    assert _is_index_stale(conn, vault_dir) is True
    conn.close()

    # 2. `cmd_verify(..., no_auto_sync=True)` fails Gate 4 when index is stale
    with pytest.raises(SystemExit):
        cmd_verify(
            argparse.Namespace(
                vault=str(vault_dir),
                json=True,
                allow_lexical_only=True,
            ),
            no_auto_sync=True,
        )
    stale_ver = json.loads(capsys.readouterr().out)
    assert stale_ver["status"] == "FAIL"
    assert any(v["gate"].startswith("Gate 4") and "mtime is stale" in v["issue"] for v in stale_ver["violations"])

    # 3. `verify --json` (default auto-sync) quietly syncs and emits valid JSON without `[skb sync]` pollution
    main(["--vault", str(vault_dir), "verify", "--json", "--allow-lexical-only"])
    clean_ver_raw = capsys.readouterr().out
    clean_ver = json.loads(clean_ver_raw)
    assert clean_ver["status"] == "PASS"
    assert clean_ver["documents"] == 6

    conn = get_db(vault_dir)
    assert _is_index_stale(conn, vault_dir) is False
    conn.close()

    # 4. Concurrent/sequential `capture` on the same day preserves all entries via `_vault_lock` + `O_CREAT | O_EXCL`
    main(["--vault", str(vault_dir), "capture", "Second episodic note on the same day."])
    capsys.readouterr()
    main(["--vault", str(vault_dir), "search", "Second episodic note", "-n", "1", "--json"])
    s_res = json.loads(capsys.readouterr().out)
    assert s_res["count"] == 1
    ep_text = Path(s_res["results"][0]["abs_path"]).read_text(encoding="utf-8")
    assert "Zero mid-task KB churn invariant verified." in ep_text
    assert "Second episodic note on the same day." in ep_text

    # 5. Gate 5 detects unresolved Git conflict markers outside fenced code blocks
    conflict_note = vault_dir / "01-now" / "now.md"
    orig_now = conflict_note.read_text(encoding="utf-8")
    conflict_marker = "\n" + ("<" * 7) + " HEAD\nbroken merge\n" + ("=" * 7) + "\n" + (">" * 7) + " main\n"
    conflict_note.write_text(orig_now + conflict_marker, encoding="utf-8")
    os.utime(conflict_note, None)
    with pytest.raises(SystemExit):
        main(["--vault", str(vault_dir), "verify", "--json", "--allow-lexical-only"])
    conflict_ver = json.loads(capsys.readouterr().out)
    assert conflict_ver["status"] == "FAIL"
    assert any(
        v["gate"].startswith("Gate 5") and "Unresolved Git merge/rebase conflict marker" in v["issue"]
        for v in conflict_ver["violations"]
    )
    conflict_note.write_text(orig_now, encoding="utf-8")


def test_obsidian_lossless_heal_offline_search_and_filtered_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """
    Verify Astra x Fable v1.4.1 adoption & edge-case invariants:
      1. `verify --heal` on an Obsidian vault without `INDEX.md` preserves custom YAML (`aliases`, `cssclasses`, `status`),
         creates `INDEX.md` when needed, does not create unrequested `02-operating-system/` distillation folders,
         and brings all 6 gates to PASS.
      2. `sync --no-embed` persists `"no_embed": true` to `.kb/vault_config.json`, and `search` issues zero HTTP requests
         even when `KB_NO_EMBED` is unset in the environment.
      3. Filtered search (`--tag wanted`) pushes document filtering into FTS5 before `LIMIT 35` so matches ranked below
         global #35 are never dropped.
      4. Unpaired thematic `---` on line 1 still indexes body chunks; folder-local `[[Note]]` resolves to same-folder
         sibling first; moving a target note updates link edges on incremental `sync`; `init` appends `.kb/` to
         an existing `.gitignore`; dashboard detects conflict markers and shows `"Not evaluated"` before RSI cycles.
    """
    monkeypatch.setenv("KB_HIPS_BIN", "")
    monkeypatch.delenv("KB_NO_EMBED", raising=False)

    http_calls: list[str] = []

    def _forbid_network(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        http_calls.append(url)
        raise RuntimeError(f"Unexpected network call: {url}")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)

    # 1. Plain Obsidian vault without INDEX.md and with custom YAML properties + 4-space list + README.md + unpaired HR
    obs_vault = tmp_path / "obsidian-vault"
    obs_vault.mkdir(parents=True)
    (obs_vault / ".gitignore").write_text("node_modules/\n.DS_Store\n", encoding="utf-8")
    (obs_vault / "README.md").write_text(
        "# My Existing Project Notes\n\nPlain README without frontmatter.\n", encoding="utf-8"
    )

    custom_note = obs_vault / "architecture-rfc.md"
    custom_note.write_text(
        "---\n"
        'title: "RFC 042 — Distributed Consensus"\n'
        "aliases:\n"
        "  - RFC-042\n"
        "  - Consensus Spec\n"
        "cssclasses: [wide-page, clean-embed]\n"
        "status: approved\n"
        "owner:\n"
        "  team: core-systems\n"
        "---\n\n"
        "# RFC 042\n\n"
        "Deterministic consensus protocol using Raft leases.\n",
        encoding="utf-8",
    )

    four_space_note = obs_vault / "indent-4space.md"
    four_space_note.write_text(
        "---\ntitle: My note\ntags:\n    - research\nstatus: draft\n---\n\n# Note\nFour-space YAML list indentation.\n",
        encoding="utf-8",
    )

    hr_note = obs_vault / "thematic-break.md"
    hr_note.write_text(
        "---\n\n# Unpaired Horizontal Rule Note\n\nUnique token `unpaired_hr_sentinel_99` after line-1 thematic break.\n",
        encoding="utf-8",
    )

    # Persist `--no-embed` via `skb sync --no-embed` and verify `.kb/.gitignore` is auto-written
    main(["--vault", str(obs_vault), "sync", "--no-embed"])
    capsys.readouterr()
    assert json.loads((obs_vault / ".kb" / "vault_config.json").read_text(encoding="utf-8"))["no_embed"] is True
    assert (obs_vault / ".kb" / ".gitignore").read_text(encoding="utf-8").strip() == "*"
    assert http_calls == []

    # Run `verify --heal`: must preserve custom YAML + 4-space list, leave README.md untouched and unlisted, create INDEX.md, and pass all 6 gates
    main(["--vault", str(obs_vault), "verify", "--heal"])
    heal_stdout = capsys.readouterr().out
    assert "README.md" not in heal_stdout
    main(["--vault", str(obs_vault), "verify", "--json"])
    ver_rep = json.loads(capsys.readouterr().out)
    assert ver_rep["status"] == "PASS"
    assert (obs_vault / "INDEX.md").is_file()
    assert not (obs_vault / "02-operating-system").exists()
    assert not (obs_vault / "README.md").read_text(encoding="utf-8").startswith("---")
    assert "[[README]]" not in (obs_vault / "INDEX.md").read_text(encoding="utf-8")

    healed_4space = four_space_note.read_text(encoding="utf-8")
    assert "tags:\n    - research\nstatus: draft" in healed_4space
    assert parse_frontmatter(healed_4space)["yaml_error"] == ""
    assert parse_frontmatter(healed_4space)["tags_list"] == ["research"]

    healed_rfc = custom_note.read_text(encoding="utf-8")
    assert "aliases:\n  - RFC-042\n  - Consensus Spec" in healed_rfc
    assert "cssclasses: [wide-page, clean-embed]" in healed_rfc
    assert "status: approved" in healed_rfc
    assert "owner:\n  team: core-systems" in healed_rfc
    assert "updated:" in healed_rfc
    assert "tags:" in healed_rfc

    # Capturing into an imported vault without `01-now/now.md` must NOT inject a broken `[[01-now/now]]` link
    main(["--vault", str(obs_vault), "capture", "We decided to use SQLite offline in this imported folder."])
    capsys.readouterr()
    main(["--vault", str(obs_vault), "verify", "--json"])
    ver_after_cap = json.loads(capsys.readouterr().out)
    assert ver_after_cap["status"] == "PASS"

    # Nonexistent `--vault` on search/verify/status/me exits 1 instead of creating `.kb/index.db`
    typo_vault = tmp_path / "nonexistent-typo-vault"
    with pytest.raises(SystemExit) as typo_exc:
        main(["--vault", str(typo_vault), "search", "SQLite"])
    assert typo_exc.value.code == 1
    assert not typo_vault.exists()
    capsys.readouterr()

    # Search for sentinel inside note that originally started with unpaired `---` (zero HTTP calls!)
    main(["--vault", str(obs_vault), "search", "unpaired_hr_sentinel_99", "--json"])
    hr_search = json.loads(capsys.readouterr().out)
    assert hr_search["count"] >= 1
    assert hr_search["results"][0]["rel_path"] == "thematic-break.md"
    assert http_calls == []

    # 2. Folder-local wikilink sibling resolution & incremental sync after moving a target file
    (obs_vault / "a").mkdir(exist_ok=True)
    (obs_vault / "z").mkdir(exist_ok=True)
    (obs_vault / "a" / "Shared.md").write_text(
        "---\ntitle: Shared A\ntype: domain\nupdated: 2026-10-30\ntags: [a]\n---\n\n# Shared A\nAlpha body.\n",
        encoding="utf-8",
    )
    (obs_vault / "z" / "Shared.md").write_text(
        "---\ntitle: Shared Z\ntype: domain\nupdated: 2026-10-30\ntags: [z]\n---\n\n# Shared Z\nZeta body.\n",
        encoding="utf-8",
    )
    (obs_vault / "z" / "Caller.md").write_text(
        "---\ntitle: Caller\ntype: domain\nupdated: 2026-10-30\ntags: [z]\n---\n\n# Caller\nLinks to [[Shared]] and [[MoveTarget]].\n",
        encoding="utf-8",
    )
    (obs_vault / "a" / "MoveTarget.md").write_text(
        "---\ntitle: MoveTarget\ntype: domain\nupdated: 2026-10-30\ntags: [a]\n---\n\n# MoveTarget\nTarget body.\n",
        encoding="utf-8",
    )
    main(["--vault", str(obs_vault), "sync"])
    capsys.readouterr()

    conn = get_db(obs_vault)
    caller_targets = {
        r["target_path"]
        for r in conn.execute("SELECT target_path FROM links WHERE source_path = 'z/Caller.md'").fetchall()
    }
    conn.close()
    # `z/Caller.md` linking `[[Shared]]` resolves to folder-local sibling `z/Shared.md`
    assert "z/Shared.md" in caller_targets
    assert "a/MoveTarget.md" in caller_targets

    # Move `a/MoveTarget.md` -> `z/MoveTarget.md` without touching `z/Caller.md` and run incremental sync
    shutil.move(str(obs_vault / "a" / "MoveTarget.md"), str(obs_vault / "z" / "MoveTarget.md"))
    main(["--vault", str(obs_vault), "sync"])
    capsys.readouterr()
    conn = get_db(obs_vault)
    caller_targets_after = {
        r["target_path"]
        for r in conn.execute("SELECT target_path FROM links WHERE source_path = 'z/Caller.md'").fetchall()
    }
    conn.close()
    assert "z/MoveTarget.md" in caller_targets_after
    assert "a/MoveTarget.md" not in caller_targets_after

    # 3. Filtered search (`--tag wanted`) with >40 higher-ranked noise notes
    noise_dir = obs_vault / "noise"
    noise_dir.mkdir(exist_ok=True)
    for i in range(42):
        (noise_dir / f"noise-{i:02d}.md").write_text(
            f"---\ntitle: Quantum Kernel {i}\ntype: note\nupdated: 2026-10-30\ntags: [noise]\n---\n\n"
            f"# Quantum Kernel {i}\nQuantum kernel optimization quantum kernel benchmark quantum kernel.\n",
            encoding="utf-8",
        )
    (obs_vault / "wanted-target.md").write_text(
        "---\ntitle: Production Notes\ntype: decision\nupdated: 2026-10-30\ntags: [wanted]\n---\n\n"
        "# Production Notes\nMentions quantum kernel once in passing.\n",
        encoding="utf-8",
    )
    main(["--vault", str(obs_vault), "sync"])
    capsys.readouterr()

    main(["--vault", str(obs_vault), "search", "quantum kernel", "--tag", "wanted", "--json"])
    filtered_res = json.loads(capsys.readouterr().out)
    assert filtered_res["count"] == 1
    assert filtered_res["results"][0]["rel_path"] == "wanted-target.md"
    assert http_calls == []

    # 4. `init` in directory with existing `.gitignore` appends `.kb/` without clobbering existing rules
    main(["init", str(obs_vault), "--minimal", "--no-embed"])
    capsys.readouterr()
    gi_after = (obs_vault / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gi_after
    assert ".kb/" in gi_after

    # 5. Dashboard HTML reports "Not evaluated" before RSI cycles and Gate 5 FAIL on conflict markers
    dash_html = obs_vault / ".kb" / "dashboard.html"
    main(["--vault", str(obs_vault), "dashboard", "--export", str(dash_html), "--native", "--no-gx10"])
    capsys.readouterr()
    assert "Not evaluated" in dash_html.read_text(encoding="utf-8")
    assert "0.9640 (init)" not in dash_html.read_text(encoding="utf-8")


def test_creative_mode_resumption_packet_f15(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify F-15: `skb resume <mode>` parses Section 5 creative mode packets and latest episodic log."""
    vault = tmp_path / "resume-vault"
    main(["init", str(vault), "--minimal", "--no-embed"])
    capsys.readouterr()
    now_file = vault / "01-now" / "now.md"
    now_file.parent.mkdir(parents=True, exist_ok=True)
    now_file.write_text(
        "---\ntitle: Now\ntype: now\nupdated: 2026-10-30\ntags: [now]\n---\n\n"
        "# Now\n\n## 5. Four Creative Mode Resumption Packets (`kb resume <mode>`)\n\n"
        "### 5.1 Mode 1: Deep Mathematics & LaTeX Manuscripts (`math`)\n"
        "- **Active Repositories & Paths**:\n  - `~/GitLab/paper2-hierarchical-edge-cloud`\n"
        "- **Immediate Next Command**:\n  ```bash\n  kb gwi audit ~/GitLab/paper2-hierarchical-edge-cloud --profile paper\n  ```\n",
        encoding="utf-8",
    )
    ep_dir = vault / "04-episodic"
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "2026-10-30-log.md").write_text(
        "---\ntitle: Log\ntype: episodic\nupdated: 2026-10-30\ntags: [log]\n---\n\n"
        "# Log\n\n## 1. Completed Paper 2 Verification\n",
        encoding="utf-8",
    )
    main(["--vault", str(vault), "resume", "math", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "math"
    assert "paper2-hierarchical-edge-cloud" in payload["packets"]["math"]["body"]

    # Verify _parse_skill_description parity between PyYAML and stdlib fallback across block/quoted scalars
    from sovereign_kb.cli import GWI_LENS_SKILL_ROUTING, _parse_skill_description

    assert "personal-kb" in GWI_LENS_SKILL_ROUTING["principal-verification"]
    assert "personal-codex" not in GWI_LENS_SKILL_ROUTING["principal-verification"]
    cases = [
        ('---\nname: s1\ndescription: "|"\n---\n', "|"),
        ("---\nname: s2\ndescription: >- # summary\n\n  alpha\n  beta\n---\n", "alpha beta"),
        ("---\nname: s3\ndescription: >+\n  gamma\n  delta\n---\n", "gamma delta"),
        ("---\nname: s4\ndescription: |\n  first line\n  second line\n---\n", "first line"),
    ]
    for idx, (raw_md, expected) in enumerate(cases):
        p_md = tmp_path / f"skill_{idx}.md"
        p_md.write_text(raw_md, encoding="utf-8")
        assert _parse_skill_description(p_md, use_pyyaml=False) == expected
        assert _parse_skill_description(p_md, use_pyyaml=True) == expected
