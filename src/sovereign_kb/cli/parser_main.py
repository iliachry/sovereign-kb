"""Top-level `skb` argparse builder, alias normalizer, dispatcher, and specialized gate subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .. import install_check
from .config_db import resolve_vault_dir
from .ops import cmd_certify, cmd_git_sync, cmd_hips, cmd_rsi
from .retrieval_notes import cmd_ask, cmd_capture, cmd_me, cmd_resume, cmd_search, cmd_status
from .scaffold import ARCHETYPES, GWI_LENS_SKILL_ROUTING, cmd_scaffold
from .skills import cmd_skills
from .sync_hips import cmd_init, cmd_sync
from .usage_dash import cmd_dashboard, cmd_usage
from .verify import cmd_verify


def _add_opt_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str | None = None,
) -> argparse.ArgumentParser:
    return sub.add_parser(name, help=help_text) if help_text else sub.add_parser(name)


class _VersionAction(argparse.Action):
    """`--version`: the package version, then whether this installed copy matches its source checkout."""

    def __init__(self, option_strings: Sequence[str], dest: str = argparse.SUPPRESS, **kwargs: Any) -> None:
        kwargs.setdefault("default", argparse.SUPPRESS)
        super().__init__(option_strings=option_strings, dest=dest, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        from sovereign_kb import __version__

        sys.stdout.write(f"{parser.prog} {__version__}\n{install_check.version_line()}\n")
        parser.exit()


def build_parser(show_all_aliases: bool = False) -> argparse.ArgumentParser:
    epilog = (
        (
            "Command Groups:\n"
            "  Everyday Memory : init, capture, search, ask, sync, verify (doctor), dashboard (ui), status, me\n"
            "  Project & Gate  : scaffold (new), gwi (audit, goldwood)\n"
            "  Advanced & Ops  : rsi (evolve, tune, consolidate), usage (finops), sessions (chats, ps),\n"
            "                    soar, jepa, train, hips, certify, skills, git-sync (pull, push)\n"
        )
        if show_all_aliases
        else "Run 'skb --help-all' to also display project scaffolding, quality gates, and advanced tooling."
    )
    parser = argparse.ArgumentParser(
        prog="sovereign-kb",
        description="Sovereign-KB (skb): Searchable, local-first Markdown memory for developers and AI coding agents.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V",
        "--version",
        action=_VersionAction,
        help="Show the version and whether this installed copy matches its source checkout, then exit",
    )
    parser.add_argument("--vault", help="Explicit path to vault directory (overrides KB_VAULT_DIR)")
    parser.add_argument(
        "--help-all",
        action="store_true",
        help="Show full help including advanced commands and aliases",
    )
    sub = parser.add_subparsers(
        dest="cmd",
        metavar="{init,capture,search,ask,sync,verify,dashboard,status,me}" if not show_all_aliases else None,
    )

    p_init = sub.add_parser("init", help="Initialize a new 6-Gate verified Sovereign Knowledge Base vault")
    p_init.add_argument("target_dir", nargs="?", help="Target vault directory (default: ~/sovereign-kb-vault)")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing starter files")
    p_init.add_argument("--no-embed", action="store_true", help="Skip vector embedding during initial sync (persisted)")
    p_init.add_argument(
        "--minimal",
        action="store_true",
        help="Create a minimal 5-file starter vault (default)",
    )
    p_init.add_argument(
        "--full",
        action="store_true",
        help="Include the full 28-file operating-system starter blueprint library",
    )
    p_init.add_argument(
        "--save-default", action="store_true", help="Save target_dir as default in ~/.config/sovereign-kb/config.json"
    )

    p_sync = sub.add_parser("sync", help="Index markdown files, compute embeddings & build HIPS Block-Max WAND index")
    p_sync.add_argument("--force", action="store_true", help="Force re-index and re-embed all files")
    p_sync.add_argument(
        "--no-embed", action="store_true", help="Index FTS5 & graph only without calling embedding endpoint"
    )

    p_search = sub.add_parser("search", help="Search across your Markdown vault (BM25 + HIPS WAND + Vector + Graph)")
    p_search.add_argument("query", nargs="+", help="Search query")
    p_search.add_argument("-n", "--limit", type=int, default=5, help="Max results")
    p_search.add_argument(
        "--type",
        help="Filter by frontmatter type (e.g. episodic, identity, now, operating-system, domain, decision, template)",
    )
    p_search.add_argument("--tag", help="Filter by frontmatter tag substring")
    p_search.add_argument(
        "--engine",
        choices=("auto", "hips", "fts5"),
        default="auto",
        help="Retrieval engine mode: 'auto' (4-way HIPS+FTS5+Vector+Graph), 'hips' (pure HIPS Block-Max WAND), or 'fts5'",
    )
    p_search.add_argument(
        "--explain",
        action="store_true",
        help="Show detailed RRF, HIPS WAND, vector cosine, and FTS5 rank telemetry per match",
    )
    p_search.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON search results for AI coding agents",
    )
    p_search.add_argument(
        "--jepa",
        action="store_true",
        help="Fuse Vault JEPA latent predictions as a 5th retrieval channel",
    )

    p_hips = (
        sub.add_parser("hips", help="Direct interface to the `hips` Block-Max WAND engine")
        if show_all_aliases
        else sub.add_parser("hips")
    )
    p_hips.add_argument(
        "hips_cmd",
        nargs="?",
        choices=("status", "index", "search", "bench", "serve", "mcp"),
        default="status",
        help="HIPS subcommand (status, index, search, bench, serve, mcp)",
    )
    p_hips.add_argument("query", nargs="*", help="Query string (for `skb hips search` or `skb hips bench`)")
    p_hips.add_argument("-n", "--limit", type=int, default=8, help="Max results for `skb hips search`")
    p_hips.add_argument("--addr", default="127.0.0.1:18080", help="Listen address for `skb hips serve`")
    p_hips.add_argument("--json", action="store_true", help="Emit JSON output for `skb hips search`")

    p_ask = sub.add_parser("ask", help="Ask a question synthesized by local OpenAI/Ollama LLM")
    p_ask.add_argument("question", nargs="+", help="Question to ask the vault")

    p_cap = sub.add_parser("capture", help="Capture a timestamped note or decision into 04-episodic/")
    p_cap.add_argument("-s", "--sync", action="store_true", help="Immediately re-index after capture")
    p_cap.add_argument("text", nargs="+", help="Note content")

    for v_alias in ("verify", "doctor", "lint"):
        v_help = (
            "Run 6-Gate deterministic verification on vault & index (aliases: doctor, lint)"
            if v_alias == "verify" or show_all_aliases
            else None
        )
        p_ver = _add_opt_parser(sub, v_alias, v_help)
        p_ver.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
        p_ver.add_argument(
            "--heal",
            action="store_true",
            help="Self-heal missing frontmatter (Gate 1) and orphan notes (Gate 3) before verifying",
        )
        p_ver.add_argument(
            "--allow-lexical-only",
            action="store_true",
            help="Allow Gate 4 to pass when running in lexical-only (no-embed) environments",
        )
        p_ver.add_argument(
            "--require-embeddings",
            action="store_true",
            help="Require 100%% vector embedding coverage even if Ollama is offline",
        )

    for s_alias in ("scaffold", "new"):
        s_help = (
            "Scaffold a new project or paper from the 7 deterministic archetypes (alias: new)"
            if show_all_aliases
            else None
        )
        p_scaf = _add_opt_parser(sub, s_alias, s_help)
        p_scaf.add_argument(
            "archetype",
            nargs="?",
            help="Archetype ID (web-ts-3d-gis, python-uv-ai, rust-systems, spatial-xr-ble, paper-ieee-arxiv, game-sim-3d, metatopia-chameleon)",
        )
        p_scaf.add_argument("target_dir", nargs="?", help="Target project directory")
        p_scaf.add_argument("--name", help="Custom project name")
        p_scaf.add_argument("--author", help="Author name for scientific paper archetype")
        p_scaf.add_argument("--list", action="store_true", help="List available project archetypes")
        p_scaf.add_argument("--force", action="store_true", help="Overwrite existing AGENTS.md / quality_gate.sh")

    p_cert = (
        sub.add_parser("certify", help="Run deterministic compiler-verified domain certification (`RUST-CERT-01..20`)")
        if show_all_aliases
        else sub.add_parser("certify")
    )
    p_cert.add_argument(
        "domain", nargs="?", choices=("rust",), default="rust", help="Certification domain (default: rust)"
    )
    p_cert.add_argument("--json", action="store_true", help="Output machine-readable JSON certification manifest")
    p_cert.add_argument(
        "--output-dir", help="Optional directory to write rust_certification_report.json and rust_cert_suite.rs"
    )
    p_cert.add_argument("--no-embed", action="store_true", help="Skip vector embedding calls if syncing vault")

    for r_alias in ("rsi", "evolve", "tune", "consolidate"):
        r_help = (
            "Run Monotonically-Gated Recursive Self-Improvement (aliases: evolve, tune, consolidate)"
            if show_all_aliases
            else None
        )
        p_rsi = _add_opt_parser(sub, r_alias, r_help)
        if r_alias in ("rsi", "evolve"):
            p_rsi.add_argument(
                "rsi_cmd",
                nargs="?",
                choices=(
                    "cycle",
                    "tune",
                    "consolidate",
                    "status",
                    "ledger",
                    "feedback",
                    "compile-prompts",
                    "sleep-consolidate",
                ),
                default="cycle",
                help="RSI subcommand (default: cycle)",
            )
            p_rsi.add_argument("--query", help="Query string for `rsi feedback`")
            p_rsi.add_argument("--target", help="Expected target rel_path for `rsi feedback`")
            p_rsi.add_argument("--heading", default="", help="Optional expected heading for `rsi feedback`")
            p_rsi.add_argument(
                "--outcome",
                choices=("miss", "success"),
                default="miss",
                help="Outcome label for `rsi feedback` (default: miss)",
            )
        p_rsi.add_argument("--json", action="store_true", help="Output machine-readable JSON telemetry")
        p_rsi.add_argument("--use-llm", action="store_true", help="Synthesize episodic distillations via local LLM")
        p_rsi.add_argument(
            "--verify-rust", action="store_true", help="Include 20-domain `rustc -D warnings` certification in cycle"
        )
        p_rsi.add_argument("--no-embed", action="store_true", help="Skip vector embedding calls during re-sync")

    p_gsync = (
        sub.add_parser("git-sync", help="Pull, re-index, run RSI, commit & push vault changes to Git remote")
        if show_all_aliases
        else sub.add_parser("git-sync")
    )
    p_gsync.add_argument("-m", "--message", help="Custom commit message")
    p_gsync.add_argument("--no-rsi", action="store_true", help="Skip automatic RSI cycle before committing")

    if show_all_aliases:
        sub.add_parser("pull", help="Git pull & re-index vault")
    else:
        sub.add_parser("pull")
    p_push = (
        sub.add_parser("push", help="Run RSI, commit, push & sync vault to Git remote")
        if show_all_aliases
        else sub.add_parser("push")
    )
    p_push.add_argument("-m", "--message", help="Custom commit message")
    p_push.add_argument("--no-rsi", action="store_true", help="Skip automatic RSI cycle before committing")

    sub.add_parser("me", help="Print Tier-0 identity & current focus")
    p_resume = sub.add_parser(
        "resume",
        help="Print zero-context-tax Creative Mode Resumption Packet(s) (all, math, compute, spatial, writing)",
    )
    p_resume.add_argument(
        "mode",
        nargs="?",
        choices=("all", "math", "compute", "spatial", "writing"),
        default="all",
        help="Creative mode packet to display (default: all)",
    )
    p_resume.add_argument("--json", action="store_true", help="Emit JSON output")
    sub.add_parser("status", help="Show vault and index telemetry")
    for u_alias in ("usage", "finops"):
        u_help = (
            "Multi-agent FinOps, token telemetry & prompt-cache auditor (alias: finops)" if show_all_aliases else None
        )
        p_use = _add_opt_parser(sub, u_alias, u_help)
        p_use.add_argument("--today", action="store_true", help="Show only today's credit and token usage")
        p_use.add_argument(
            "-d", "--days", type=int, default=7, help="Number of recent active days to include (default: 7)"
        )
        p_use.add_argument("--offline", action="store_true", help="Use cached pricing data without network lookup")
        p_use.add_argument("--native", action="store_true", help="Force zero-dependency stdlib SQLite/JSONL scanner")
        p_use.add_argument("--no-gx10", action="store_true", help="Skip querying remote GPU node status")
        p_use.add_argument("--json", action="store_true", help="Output machine-readable JSON FinOps report")

    for d_alias in ("dashboard", "ui"):
        d_help = (
            "Zero-dependency local observability & Knowledge Graph dashboard (alias: ui)"
            if d_alias == "dashboard" or show_all_aliases
            else None
        )
        p_dash = _add_opt_parser(sub, d_alias, d_help)
        p_dash.add_argument(
            "--host", default="127.0.0.1", help="Bind host for live dashboard server (default: 127.0.0.1)"
        )
        p_dash.add_argument(
            "-p", "--port", type=int, default=8765, help="Bind port for live dashboard server (default: 8765)"
        )
        p_dash.add_argument(
            "-d", "--days", type=int, default=7, help="Number of recent FinOps active days to include (default: 7)"
        )
        p_dash.add_argument(
            "--export",
            nargs="?",
            const="__DEFAULT__",
            help="Export self-contained static HTML dashboard to path (default: <vault>/.kb/dashboard.html)",
        )
        p_dash.add_argument("--open", action="store_true", help="Automatically open the dashboard in default browser")
        p_dash.add_argument("--native", action="store_true", help="Force zero-dependency stdlib FinOps scanner")
        p_dash.add_argument("--no-gx10", action="store_true", help="Skip querying remote GPU node status")
        p_dash.add_argument("--json", action="store_true", help="Output combined dashboard telemetry snapshot as JSON")

    p_skills = (
        sub.add_parser(
            "skills",
            help="Inventory and route Sovereign Agent Skills & Zero-Auth Plugins across GWI profiles, lenses & archetypes",
        )
        if show_all_aliases
        else sub.add_parser("skills")
    )
    p_skills.add_argument(
        "query", nargs="*", help="Optional search keyword across top-level skills and reference modules"
    )
    p_skills.add_argument(
        "--profile",
        choices=("app", "sim", "video", "paper"),
        help="Filter skills routed for a specific GWI profile (app, sim, video, paper)",
    )
    p_skills.add_argument(
        "--lens",
        choices=tuple(GWI_LENS_SKILL_ROUTING.keys()),
        help="Filter skills routed for a specific Bi-Polar GWI Craft Lens (4 Verga + 4 Chrysos)",
    )
    p_skills.add_argument(
        "--archetype",
        choices=tuple(ARCHETYPES.keys()),
        help="Filter skills routed for a specific kb scaffold archetype",
    )
    p_skills.add_argument(
        "--inject",
        metavar="REPO_DIR",
        help="Auto-detect archetype/GWI profile for REPO_DIR and inject Activated Sovereign Agent Skills table into its AGENTS.md",
    )
    p_skills.add_argument(
        "--inject-all",
        action="store_true",
        help="Auto-inject Activated Sovereign Agent Skills table into all repositories with AGENTS.md under ~/GitLab and ~/Projects",
    )
    p_skills.add_argument("--json", action="store_true", help="Emit machine-readable JSON skill & plugin inventory")
    p_skills.add_argument(
        "--verify",
        action="store_true",
        help="Strictly validate YAML frontmatter, schema, and syntax of all installed skills & plugins",
    )

    from sovereign_kb.chain import register_chain_subparser
    from sovereign_kb.elenchus import register_elenchus_subparser
    from sovereign_kb.evr import register_evr_subparser
    from sovereign_kb.gwi import register_gwi_subparser
    from sovereign_kb.sessions import register_sessions_subparser
    from sovereign_kb.soar import register_soar_subparser
    from sovereign_kb.video_playback import register_video_subparser

    register_gwi_subparser(sub, show_all_aliases=show_all_aliases)
    register_elenchus_subparser(sub, show_all_aliases=show_all_aliases)
    register_soar_subparser(sub, show_all_aliases=show_all_aliases)
    register_sessions_subparser(sub, show_all_aliases=show_all_aliases)
    register_chain_subparser(sub, show_all_aliases=show_all_aliases)
    register_video_subparser(sub, show_all_aliases=show_all_aliases)
    register_evr_subparser(sub, show_all_aliases=show_all_aliases)

    p_cgraph = (
        sub.add_parser(
            "code-graph",
            help="Build polyglot AST/SCIP symbol & call graph and slice 2-hop dependency subgraphs",
        )
        if show_all_aliases
        else sub.add_parser("code-graph")
    )
    p_cgraph.add_argument("target_dir", nargs="?", default=".", help="Target repository directory (default: .)")
    p_cgraph.add_argument("-q", "--query", help="Optional symbol/feature query to slice a k-hop dependency subgraph")
    p_cgraph.add_argument("--hops", type=int, default=2, help="Caller/callee expansion hops (default: 2)")
    p_cgraph.add_argument("-n", "--limit", type=int, default=8, help="Max seed symbols for subgraph slice")
    p_cgraph.add_argument("--json", action="store_true", help="Output JSON")

    p_falsify = (
        sub.add_parser(
            "falsify",
            help="Run AST mutation kill-ratio probe, property-fuzz check (`GWI-SCI-06`) & formal proof verifier (`GWI-SCI-07`)",
        )
        if show_all_aliases
        else sub.add_parser("falsify")
    )
    p_falsify.add_argument("target_dir", nargs="?", default=".", help="Target repository directory (default: .)")
    p_falsify.add_argument("--target-file", help="Optional relative Python file path to mutate")
    p_falsify.add_argument("--max-mutants", type=int, default=4, help="Maximum AST operator mutants to test")
    p_falsify.add_argument("--run-mutations", action="store_true", help="Execute live pytest AST mutation probe")
    p_falsify.add_argument(
        "--execute-symbolic",
        action="store_true",
        help="Execute SymPy/Z3 symbolic verification scripts",
    )
    p_falsify.add_argument("--json", action="store_true", help="Output JSON")

    p_splat = (
        sub.add_parser(
            "splat",
            help="Audit 3D Gaussian Splatting (`.ply`, `.splat`, `.ksplat`, `.spz`) assets for WebXR/Quest 3 budgets",
        )
        if show_all_aliases
        else sub.add_parser("splat")
    )
    p_splat.add_argument("target", nargs="?", default=".", help="Target directory or `.ply`/`.splat` file")
    p_splat.add_argument("--json", action="store_true", help="Output JSON")

    p_jepa = (
        sub.add_parser(
            "jepa",
            help="Unified Vault JEPA: latent world-model training, rollouts, gap discovery & surprise detection",
        )
        if show_all_aliases
        else sub.add_parser("jepa")
    )
    p_jepa.add_argument(
        "jepa_cmd",
        nargs="?",
        choices=(
            "train",
            "predict",
            "plan",
            "search",
            "gaps",
            "surprise",
            "bench",
            "hjepa",
            "soar",
            "heal",
            "code",
            "status",
        ),
        default="status",
        help="JEPA subcommand (train, predict, plan, search, gaps, surprise, bench, hjepa, soar, heal, code, status)",
    )
    p_jepa.add_argument("target", nargs="?", help="Query, start note, note rel_path, or repo dir")
    p_jepa.add_argument("goal", nargs="?", help="Goal note/query (`plan`) or seed symbol (`code`)")
    p_jepa.add_argument(
        "--action",
        choices=("WIKILINK", "NEXT_SECTION", "EPISODIC_NEXT", "EPISODIC_TO_SEMANTIC"),
        default=None,
        help="Transition action type",
    )
    p_jepa.add_argument("--steps", type=int, default=1, help="Rollout steps for `skb jepa predict` / `plan`")
    p_jepa.add_argument("--beam", type=int, default=4, help="Beam width for `skb jepa plan` (default: 4)")
    p_jepa.add_argument("-n", "--limit", type=int, default=5, help="Max results to return")
    p_jepa.add_argument("--epochs", type=int, default=25, help="Training epochs (default: 25)")
    p_jepa.add_argument("--latent-dim", type=int, default=48, help="Latent dimension d (default: 48)")
    p_jepa.add_argument("--proj-dim", type=int, default=128, help="Input projection dim before encoder (0 = none)")
    p_jepa.add_argument(
        "--backend",
        choices=("stdlib", "torch", "auto"),
        default="stdlib",
        help="Compute backend (default: stdlib zero-dependency)",
    )
    p_jepa.add_argument("--seed", type=int, default=42, help="Deterministic RNG seed (default: 42)")
    p_jepa.add_argument("--evr", help="Optional output path to seal JEPA checkpoint into an .evr capsule")
    p_jepa.add_argument("--apply", action="store_true", help="Apply Lyapunov-gated changes in `skb jepa heal`")
    p_jepa.add_argument("--persist-chunks", action="store_true", help="Persist compiled Soar chunks in `skb jepa soar`")
    p_jepa.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    p_train = (
        sub.add_parser("train", help="Build datasets, train models, serve & audit/search from your vault (F-24)")
        if show_all_aliases
        else sub.add_parser("train")
    )
    p_train.add_argument(
        "train_cmd",
        nargs="?",
        choices=("datasets", "embedder", "critic", "nanolm", "serve", "audit", "search"),
        default="datasets",
        help="Training subcommand (datasets, embedder, critic, nanolm, serve, audit, search)",
    )
    p_train.add_argument("target", nargs="?", help="File/prompt for `audit` or query for `search`")
    p_train.add_argument(
        "--track",
        choices=("retrieval", "critic", "twin", "nanolm"),
        default="retrieval",
        help="Dataset track for `skb train datasets`",
    )
    p_train.add_argument("--scope", choices=("public-safe", "private-local"), default="public-safe")
    p_train.add_argument("--data-dir", help="Dataset directory (default: .kb/training/datasets)")
    p_train.add_argument("--out-dir", help="Output directory for checkpoints & results (default: .kb/training)")
    p_train.add_argument("--api-base", default="http://127.0.0.1:8000/v1", help="OpenAI-compatible teacher URL")
    p_train.add_argument("--model", default="qwen3.6-35b-a3b", help="Teacher or base model ID")
    p_train.add_argument("--api-key", default="", help="Optional teacher API key")
    p_train.add_argument("--epochs", type=int, default=3)
    p_train.add_argument("--steps", type=int, default=1200)
    p_train.add_argument("--port", type=int, default=8012)
    p_train.add_argument("--url", help="Model server URL (default: http://127.0.0.1:8012)")
    p_train.add_argument("--ssh-host", help="Optional SSH host to tunnel :8012 when remote")
    p_train.add_argument("-n", "--limit", type=int, default=8, help="Max results for `skb train search`")
    p_train.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    return parser


def _normalize_cli_aliases(raw_argv: list[str]) -> list[str]:
    out = list(raw_argv)
    idx = 0
    while idx < len(out):
        tok = out[idx]
        if tok == "--vault":
            idx += 2
            continue
        if tok.startswith("-"):
            idx += 1
            continue
        if tok == "challenge":
            out[idx] = "elenchus"
        break
    return out


def main(argv: list[str] | None = None) -> None:
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    if argv is None and not {"-V", "--version"} & set(raw_argv):  # --version reports the install state itself
        install_check.warn_if_stale()
    if "--help-all" in raw_argv:
        parser = build_parser(show_all_aliases=True)
        parser.print_help()
        return
    norm_argv = _normalize_cli_aliases(raw_argv)
    parser = build_parser(show_all_aliases=False)
    args = parser.parse_args(norm_argv)
    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "sync":
        if getattr(args, "no_embed", False):
            args.persist_no_embed = True
        cmd_sync(args)
    elif args.cmd == "search":
        cmd_search(args)
    elif args.cmd == "hips":
        cmd_hips(args)
    elif args.cmd == "ask":
        cmd_ask(args)
    elif args.cmd == "capture":
        cmd_capture(args)
    elif args.cmd in ("verify", "doctor", "lint"):
        cmd_verify(args)
    elif args.cmd in ("scaffold", "new"):
        cmd_scaffold(args)
    elif args.cmd == "skills":
        cmd_skills(args)
    elif args.cmd in ("sessions", "chats", "ps"):
        from sovereign_kb.sessions import cmd_sessions

        cmd_sessions(args)
    elif args.cmd == "certify":
        cmd_certify(args)
    elif args.cmd in ("rsi", "evolve", "tune", "consolidate"):
        cmd_rsi(args)
    elif args.cmd in ("usage", "finops"):
        cmd_usage(args)
    elif args.cmd in ("dashboard", "ui"):
        cmd_dashboard(args)
    elif args.cmd in ("gwi", "goldwood", "audit"):
        from sovereign_kb.gwi import cmd_gwi

        cmd_gwi(args)
    elif args.cmd in ("elenchus", "challenge"):
        from sovereign_kb.elenchus import cmd_elenchus

        cmd_elenchus(args)
    elif args.cmd == "soar":
        from sovereign_kb.soar import cmd_soar

        cmd_soar(args, resolve_vault_dir)
    elif args.cmd == "video":
        if hasattr(args, "func"):
            args.func(args)
        else:
            parser.parse_args(["video", "--help"])
    elif args.cmd in ("chain", "pistis", "blockchain"):
        from sovereign_kb.chain import cmd_chain

        cmd_chain(args)
    elif args.cmd in ("evr", "everything"):
        from sovereign_kb.evr import cmd_evr

        cmd_evr(args)
    elif args.cmd == "git-sync":
        args.mode = "sync"
        cmd_git_sync(args)
    elif args.cmd == "pull":
        args.mode = "pull"
        cmd_git_sync(args)
    elif args.cmd == "push":
        args.mode = "push"
        cmd_git_sync(args)
    elif args.cmd == "me":
        cmd_me(args)
    elif args.cmd == "resume":
        cmd_resume(args)
    elif args.cmd == "code-graph":
        cmd_code_graph(args)
    elif args.cmd == "falsify":
        cmd_falsify(args)
    elif args.cmd == "splat":
        cmd_splat(args)
    elif args.cmd == "jepa":
        from sovereign_kb.jepa import cmd_jepa

        cmd_jepa(args, resolve_vault_dir)
    elif args.cmd == "train":
        from sovereign_kb.training import cmd_train

        cmd_train(args, resolve_vault_dir)
    else:
        cmd_status(args)


def cmd_code_graph(args) -> None:
    from sovereign_kb.frontier_organon import CodeGraphIndexer, slice_symbol_subgraph

    target_dir = Path(getattr(args, "target_dir", ".")).expanduser().resolve()
    query = getattr(args, "query", None)
    if query:
        hops = max(0, int(getattr(args, "hops", 2)))
        limit = max(1, int(getattr(args, "limit", 8)))
        res = slice_symbol_subgraph(
            target_dir,
            query,
            hops=hops,
            top_k=limit,
            persist=True,
        )
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2))
        else:
            print(f"=== Sovereign-KB Polyglot AST Subgraph Slice (`{target_dir.name}`, hops={res['hops']}) ===")
            print(f"  Query          : {res['query']}")
            print(f"  Affected Files : {', '.join(res['affected_files'])}")
            for s in res["seed_symbols"]:
                print(
                    f"  • [{s['language']}:{s['kind']}] {s['name']} ({s['file_path']}:{s['start_line']}) in_deg={s['in_degree']}"
                )
        return

    indexer = CodeGraphIndexer(target_dir)
    rep = indexer.build_graph(persist=True, export_hips=True)
    rep_out = {k: v for k, v in rep.items() if k not in ("nodes", "edges")}
    if getattr(args, "json", False):
        print(json.dumps(rep_out, indent=2))
    else:
        print(f"=== Sovereign-KB Polyglot AST & Call-Graph Index (`{target_dir.name}`) ===")
        print(
            f"  Files Scanned : {rep_out['files_scanned']} | Symbols: {rep_out['total_symbols']} "
            f"| Resolved Call Edges: {rep_out['resolved_call_edges']}"
        )
        print(f"  Languages     : {rep_out['languages']}")
        print(f"  SQLite Graph  : {rep_out['db_path']}")
        print(f"  HIPS Corpus   : {rep_out['hips_corpus_path']}")
        for top in rep_out["top_central_symbols"][:5]:
            print(
                f"  • {top['name']:36s} ({top['file_path']}) in_degree={top['in_degree']} centrality={top['centrality']:.3f}"
            )


def cmd_falsify(args) -> None:
    from sovereign_kb.frontier_organon import (
        check_formal_proofs,
        check_property_and_mutation_rigor,
        run_ast_mutation_probe,
    )

    target_dir = Path(getattr(args, "target_dir", ".")).expanduser().resolve()
    f6, p6, metrics = check_property_and_mutation_rigor(target_dir)
    formal = check_formal_proofs(
        target_dir,
        execute_symbolic=bool(getattr(args, "execute_symbolic", False)),
    )
    mut_res = None
    if getattr(args, "run_mutations", False) or getattr(args, "target_file", None):
        mut_res = run_ast_mutation_probe(
            target_dir,
            target_rel_path=getattr(args, "target_file", None),
            max_mutants=int(getattr(args, "max_mutants", 4)),
        )
    all_findings = f6 + formal.get("findings", [])
    overall_passed = (
        bool(formal.get("passed", False))
        and not any(f.get("blocker") for f in all_findings)
        and (mut_res is None or bool(mut_res.get("passed", False)))
    )
    payload = {
        "target_dir": str(target_dir),
        "passed": overall_passed,
        "property_and_assertion_metrics": metrics,
        "formal_proof_certificate": formal,
        "ast_mutation_probe": mut_res,
        "findings": all_findings,
        "passes": p6 + formal.get("passes", []),
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(f"=== Sovereign-KB Falsification & Formal Verification Gate (`{target_dir.name}`) ===")
        print(
            f"  Test Assertions : {metrics['total_assertions']} across {metrics['total_test_functions']} tests "
            f"({metrics['assertions_per_test']} assertions/test, property_fuzz={metrics['has_property_fuzzing']})"
        )
        print(
            f"  Formal Proofs   : cert_sha256={formal['certificate_sha256']} | passed={formal['passed']} "
            f"(lean={len(formal['lean_theorems'])}, sympy/z3={len(formal['symbolic_scripts'])}, "
            f"tex_theorems={formal['tex_theorem_count']})"
        )
        if mut_res:
            print(
                f"  AST Mutation    : {mut_res['mutants_killed']}/{mut_res['mutants_tested']} killed "
                f"(M_kill={mut_res['mutation_kill_ratio']:.2%}) [{mut_res['status']}]"
            )
    if not overall_passed:
        sys.exit(1)


def cmd_splat(args) -> None:
    from sovereign_kb.frontier_organon import audit_and_package_3dgs

    target = Path(getattr(args, "target", ".")).expanduser().resolve()
    rep = audit_and_package_3dgs(target)
    if getattr(args, "json", False):
        print(json.dumps(rep, indent=2))
    else:
        print(f"=== Sovereign-KB 3D Gaussian Splatting (`3DGS`) WebXR Auditor (`{target.name}`) ===")
        print(
            f"  Assets Scanned : {rep['asset_count']} | Total Gaussians: {rep['total_gaussians']:,} "
            f"| Passed: {rep['passed']}"
        )
        for a in rep["assets"]:
            print(
                f"  • {Path(a['path']).name:28s} : {a['gaussian_count']:,} splats, "
                f"SH_deg={a['sh_degree']}, {a['size_mb']:.2f} MB (Quest3_90fps={a['quest3_90fps_budget_ok']})"
            )
    if not rep.get("passed", False):
        sys.exit(1)
