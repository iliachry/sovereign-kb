"""CLI subparser registration and `evr` subcommands (`inspect`, `verify`, `search`, `sql`, `tensor`, `prove`, `extract`, `pack`, `seal`)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .builder import EvrBuilder
from .capsules import (
    create_showcase_evr_capsule,
    seal_gwi_audit_capsule,
    seal_paper_reproducibility_capsule,
)
from .constants import EVR_ALIGNMENT, EVR_SPEC_VERSION, EvrKind
from .packers import (
    pack_digital_twin_snapshot,
    pack_poetics_show_capsule,
    pack_vault_knowledge_cartridge,
)
from .reader import EvrReader
from .rust_interop import verify_rust_evr_interop


def register_evr_subparser(subparsers: Any, show_all_aliases: bool = False) -> None:
    aliases = ("evr", "everything") if show_all_aliases else ("evr",)
    for idx, name in enumerate(aliases):
        help_txt = (
            "The `.evr` ('Everything' v1.0) universal zero-copy polyglot file format engine"
            if idx == 0 or show_all_aliases
            else None
        )
        p = subparsers.add_parser(name, **({"help": help_txt} if help_txt else {}))
        p.add_argument(
            "action",
            nargs="?",
            default="status",
            choices=(
                "status",
                "pack",
                "inspect",
                "verify",
                "search",
                "sql",
                "tensor",
                "extract",
                "prove",
                "demo",
                "bench",
                "seal-gwi",
                "seal-paper",
                "pack-vault",
                "pack-show",
                "pack-twin",
            ),
            help="EVR action (pack, inspect, verify, search, sql, tensor, extract, prove, demo, bench, seal-gwi, seal-paper, pack-vault, pack-show, pack-twin, status)",
        )
        p.add_argument("target", nargs="?", default="", help="Path to .evr file or input directory/file")
        p.add_argument("extra", nargs="*", help="Additional arguments (query, SQL statement, or input paths)")
        p.add_argument("-o", "--output", default="", help="Output .evr file or destination directory")
        p.add_argument("--title", default="Everything Capsule (.evr)", help="Capsule title")
        p.add_argument("--author", default="Sovereign Organon", help="Capsule author")
        p.add_argument(
            "--polyglot",
            action="store_true",
            help="Emit triple-polyglot (Shell + HTML5/3D + 64B Binary) capsule",
        )
        p.add_argument("--sign-key", default="", help="HMAC-SHA256 signing/verification key")
        p.add_argument(
            "--include-internal",
            action="store_true",
            help="Include __evr__/ internal streams when extracting",
        )
        p.add_argument("--rust", action="store_true", help="Also run native Rust (`rustc`) cross-language verification")
        p.add_argument(
            "--require-elenchus",
            action="store_true",
            help="Require a fresh, cross-lineage adjudicated Elenchus challenge receipt before sealing `.evr` (`seal-gwi`)",
        )
        p.add_argument("-k", "--top-k", type=int, default=5, help="Top-K results for `evr search`")
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def cmd_evr(args: argparse.Namespace) -> None:
    action = getattr(args, "action", "status") or "status"
    target = getattr(args, "target", "") or ""
    extra: list[str] = list(getattr(args, "extra", []) or [])
    as_json = bool(getattr(args, "json", False))
    sign_key = getattr(args, "sign_key", "") or None

    if action == "status":
        payload: dict[str, Any] = {
            "format": ".evr (Everything Container Format)",
            "spec_version": EVR_SPEC_VERSION,
            "magic_start": "EVR\\x01 (0x45565201)",
            "magic_end": "1RVE (0x31525645)",
            "alignment_bytes": EVR_ALIGNMENT,
            "modalities": [k.name for k in EvrKind],
            "features": [
                "64-byte cacheline/SIMD/mmap zero-copy chunk alignment",
                "BLAKE2b-256 content-addressed deduplication + stored/raw IEEE CRC32",
                "Dual-layer TOC (128-byte binary entries + JSON manifest with full parity check)",
                "Embedded BM25 + 64-d feature-hashed cosine search (__evr__/index.json)",
                "Embedded SQLite3 relational & FTS5 catalog with read-only authorizer (__evr__/catalog.sqlite)",
                "RFC-6962-shaped domain-separated binary Merkle tree & inclusion proofs",
                "Triple-Polyglot executable shell + HTML5/3D Studio + binary container",
                "Cross-language Python <-> Rust bit-exact wire compatibility",
            ],
        }
        if as_json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"=== .evr ('Everything' v{EVR_SPEC_VERSION}) Universal File Format Engine ===")
            print(
                f"  Magic Envelope : {payload['magic_start']} ... {payload['magic_end']} (64B Superblock + 64B Footer)"
            )
            print(f"  SIMD Alignment : {EVR_ALIGNMENT} bytes (zero-copy mmap ready)")
            print(f"  12 Modalities  : {', '.join(payload['modalities'])}")
            for feat in payload["features"]:
                print(f"  - {feat}")
        return

    if action == "demo":
        out_file = Path(args.output or (target if target else "everything_showcase.evr")).expanduser().resolve()
        summ = create_showcase_evr_capsule(out_file, polyglot=True, sign_key=sign_key or "sovereign-evr-v1-key")
        if getattr(args, "rust", False):
            summ["rust_interop"] = verify_rust_evr_interop(out_file)
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(f"=== Created Showcase .evr Capsule: {out_file} ({summ['file_size_bytes']} bytes) ===")
            print(f"  Polyglot HTML5/Shell : {summ['polyglot']} (64B Superblock @ offset {summ['superblock_offset']})")
            print(f"  Chunks & Modalities  : {summ['chunk_count']} chunks across {', '.join(summ['modalities'])}")
            print(f"  RFC 6962 Merkle Root : {summ['merkle_root']}")
            print(f"  6-Gate Verification  : {'PASS' if summ['verification']['valid'] else 'FAIL'}")
        return

    if action == "seal-gwi":
        target_p = Path(target or ".").expanduser().resolve()
        out_p = Path(args.output).expanduser().resolve() if args.output else None
        try:
            summ = seal_gwi_audit_capsule(
                target_p,
                output_path=out_p,
                sign_key=sign_key or "sovereign-evr-v1-key",
                require_elenchus=bool(getattr(args, "require_elenchus", False)),
            )
        except ValueError as exc:
            if as_json:
                print(json.dumps({"error": str(exc)}, indent=2))
            else:
                print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(f"=== Sealed GWI Audit Capsule: {summ['path']} ({summ['file_size_bytes']} bytes) ===")
            print(f"  Goldwood Index G(S)  : {summ['goldwood_g']:.2f} / 10.00 ({summ['gwi_status']})")
            print(f"  RFC 6962 Merkle Root : {summ['merkle_root']}")
            if "elenchus" in summ:
                el = summ["elenchus"]
                ver_tag = (
                    "[VERIFIED]" if el.get("gate_passed") else f"[UNVERIFIED:{el.get('gate_reason_code', 'UNKNOWN')}]"
                )
                print(
                    f"  Elenchus Gate        : {el['seal_status']} {ver_tag} ({el['panel_mode']}, N_eff={el['n_eff']:.2f})"
                )
            if "pistis_chain_event" in summ:
                print(
                    f"  Pistis Chain Event   : seq=#{summ['pistis_chain_event']['seq']} ({summ['pistis_chain_event']['event_hash'][:16]}...)"
                )
        return

    if action == "seal-paper":
        target_p = Path(target or ".").expanduser().resolve()
        out_p = Path(args.output).expanduser().resolve() if args.output else None
        summ = seal_paper_reproducibility_capsule(
            target_p, output_path=out_p, sign_key=sign_key or "sovereign-evr-v1-key"
        )
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(
                f"=== Sealed Scientific Reproducibility Capsule: {summ['path']} ({summ['file_size_bytes']} bytes) ==="
            )
            print(f"  RFC 6962 Merkle Root : {summ['merkle_root']}")
            print(f"\n{summ['latex_reproducibility_stamp'].rstrip()}")
        return

    if action == "pack-vault":
        target_p = Path(target or ".").expanduser().resolve()
        out_p = Path(args.output).expanduser().resolve() if args.output else None
        summ = pack_vault_knowledge_cartridge(target_p, output_path=out_p, sign_key=sign_key or "sovereign-evr-v1-key")
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(f"=== Packed Sovereign Knowledge Cartridge: {summ['path']} ({summ['file_size_bytes']} bytes) ===")
            print(
                f"  Notes & Embeddings   : {summ['notes_packed']} notes -> 64B-aligned tensor {summ['embedding_tensor_shape']}"
            )
            print(f"  RFC 6962 Merkle Root : {summ['merkle_root']}")
        return

    if action == "pack-show":
        out_p = Path(args.output or target or "poetics_show_capsule.evr").expanduser().resolve()
        summ = pack_poetics_show_capsule(output_path=out_p, sign_key=sign_key or "sovereign-evr-v1-key")
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(
                f"=== Packed Poetics Show & Law L10 Session Capsule: {summ['path']} ({summ['file_size_bytes']} bytes) ==="
            )
            print(f"  Modalities           : {', '.join(summ['modalities'])}")
            print(f"  RFC 6962 Merkle Root : {summ['merkle_root']}")
        return

    if action == "pack-twin":
        out_p = Path(args.output or target or "digital_twin_snapshot.evr").expanduser().resolve()
        summ = pack_digital_twin_snapshot(output_path=out_p, sign_key=sign_key or "sovereign-evr-v1-key")
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(
                f"=== Packed 3D Digital Twin & 2D SWE Flood Snapshot: {summ['path']} ({summ['file_size_bytes']} bytes) ==="
            )
            print(f"  Modalities           : {', '.join(summ['modalities'])}")
            print(f"  RFC 6962 Merkle Root : {summ['merkle_root']}")
        return

    if action == "pack":
        inputs = ([target] if target else []) + extra
        if not inputs:
            print("Error: `evr pack` requires at least one input file or directory.", file=sys.stderr)
            sys.exit(1)
        out_path = Path(args.output or "bundle.evr").expanduser().resolve()
        builder = EvrBuilder(
            title=args.title,
            author=args.author,
            polyglot=bool(args.polyglot),
            sign_key=sign_key,
        )
        for inp in inputs:
            p = Path(inp).expanduser().resolve()
            if not p.exists():
                print(f"Error: input path '{inp}' does not exist.", file=sys.stderr)
                sys.exit(1)
            builder.add_path(p, base_dir=p if p.is_dir() else p.parent)
        written = builder.write(out_path)
        with EvrReader(written) as reader:
            summ = reader.summary()
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(
                f"Packed {summ['chunk_count']} chunks ({summ['total_raw_bytes']} raw bytes -> {summ['file_size_bytes']} file bytes) into {written}"
            )
            print(f"  Modalities  : {', '.join(summ['modalities'])}")
            print(f"  Merkle Root : {summ['merkle_root']}")
        return

    if action == "bench":
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmpdir:
            cap_path = Path(tmpdir) / "bench.evr"
            create_showcase_evr_capsule(cap_path, polyglot=True)
            t_pack = (time.perf_counter() - t0) * 1000.0

            with EvrReader(cap_path) as reader:
                t1 = time.perf_counter()
                mv = reader.mmap_slice("tensors/attention_projection.tensor")
                _ = len(mv)
                mv.release()
                t_mmap_us = (time.perf_counter() - t1) * 1_000_000.0

                # Warm index cache then measure search
                _ = reader.search("zero-copy", top_k=1)
                t2 = time.perf_counter()
                hits = reader.search("zero-copy 64-byte alignment SIMD", top_k=3)
                t_search_us = (time.perf_counter() - t2) * 1_000_000.0

                t3 = time.perf_counter()
                ver = reader.verify(sign_key="sovereign-evr-v1-key")
                t_verify_us = (time.perf_counter() - t3) * 1_000_000.0

            rust_res = verify_rust_evr_interop(cap_path)

        bench_out = {
            "pack_12_modalities_ms": round(t_pack, 3),
            "zero_copy_tensor_mmap_us": round(t_mmap_us, 2),
            "embedded_hybrid_search_us": round(t_search_us, 2),
            "rfc6962_6gate_verify_us": round(t_verify_us, 2),
            "top_search_hit": hits[0]["path"] if hits else None,
            "verified": ver["valid"],
            "rust_interop": rust_res,
        }
        if as_json:
            print(json.dumps(bench_out, indent=2))
        else:
            print("=== .evr ('Everything' v1.0) Performance Benchmark ===")
            print(f"  Pack 12-Modality Polyglot Capsule : {bench_out['pack_12_modalities_ms']:.2f} ms")
            print(f"  Zero-Copy 64B Tensor mmap Slice   : {bench_out['zero_copy_tensor_mmap_us']:.2f} us")
            print(f"  Embedded Hybrid BM25+Vec64 Search : {bench_out['embedded_hybrid_search_us']:.2f} us")
            print(f"  RFC 6962 6-Gate Merkle Verify     : {bench_out['rfc6962_6gate_verify_us']:.2f} us")
            print(f"  Rust <-> Python Wire Interop      : {'PASS' if rust_res.get('valid') else 'FAIL'}")
        return

    if not target:
        print(f"Error: `evr {action}` requires a target `.evr` file.", file=sys.stderr)
        sys.exit(1)

    evr_path = Path(target).expanduser().resolve()
    if action == "inspect":
        with EvrReader(evr_path) as reader:
            summ = reader.summary()
        if as_json:
            print(json.dumps(summ, indent=2))
        else:
            print(f"=== .evr Capsule: {summ['title']} ({summ['file_size_bytes']} bytes) ===")
            print(
                f"  Version / Polyglot : v{summ['evr_version']} · polyglot={summ['polyglot']} · alignment={summ['alignment']}B"
            )
            print(
                f"  Offsets (64B)      : superblock={summ['superblock_offset']} · toc={summ['toc_offset']} · manifest={summ['manifest_offset']}"
            )
            print(f"  Modalities ({len(summ['modalities'])})    : {', '.join(summ['modalities'])}")
            print(f"  RFC 6962 Root      : {summ['merkle_root']}")
            print("\n  Idx  Kind      Codec  Offset(64B)  Raw(B)  Stored(B)  Path")
            print("  ---  --------  -----  -----------  ------  ---------  ----")
            for c in summ["chunks"]:
                print(
                    f"  {c['index']:3d}  {c['kind']:8s}  {c['codec']:5s}  {c['data_offset']:11d}  {c['raw_len']:6d}  {c['stored_len']:9d}  {c['path']}"
                )
        return

    if action == "verify":
        with EvrReader(evr_path) as reader:
            ver = reader.verify(sign_key=sign_key)
        if getattr(args, "rust", False):
            ver["rust_interop"] = verify_rust_evr_interop(evr_path)
            if not ver["rust_interop"].get("valid", False):
                ver["valid"] = False
        if as_json:
            print(json.dumps(ver, indent=2))
        else:
            status_str = "PASS" if ver["valid"] else "FAIL"
            print(f"=== .evr 6-Gate Verification: {status_str} ({evr_path.name}) ===")
            print(f"  64B Alignment Invariant : {ver['all_chunks_64b_aligned']}")
            print(f"  Chunks Verified         : {ver['chunk_count']} ({', '.join(ver['modalities'])})")
            print(f"  RFC 6962 Merkle Root    : {ver['merkle_root']}")
            if ver.get("signature_verified") is not None:
                print(f"  HMAC-SHA256 Signature   : {ver['signature_verified']}")
            if "rust_interop" in ver:
                print(f"  Native Rust Kernel      : {'PASS' if ver['rust_interop'].get('valid') else 'FAIL'}")
            for err in ver["errors"]:
                print(f"  [ERROR] {err}")
        if not ver["valid"]:
            sys.exit(1)
        return

    if action == "search":
        query = " ".join(extra).strip()
        if not query:
            print("Error: `evr search <file.evr> <query>` requires a search query.", file=sys.stderr)
            sys.exit(1)
        with EvrReader(evr_path) as reader:
            hits = reader.search(query, top_k=int(args.top_k))
        if as_json:
            print(json.dumps({"query": query, "hits": hits}, indent=2))
        else:
            print(f"=== .evr Embedded Hybrid Search ({len(hits)} hits for '{query}') ===")
            for i, h in enumerate(hits, 1):
                print(
                    f"  {i}. [{h['kind']}] {h['path']} (score={h['score']}, bm25={h['bm25']}, cos={h['semantic_cosine']})"
                )
                if h["snippet"]:
                    print(f"     {h['snippet'][:120]}")
        return

    if action == "sql":
        sql_q = (
            " ".join(extra).strip() or "SELECT chunk_index, path, kind, raw_len FROM evr_chunks ORDER BY chunk_index;"
        )
        with EvrReader(evr_path) as reader:
            rows = reader.sql(sql_q)
        if as_json:
            print(json.dumps({"sql": sql_q, "rows": rows}, indent=2))
        else:
            for r in rows:
                print(json.dumps(r, ensure_ascii=False))
        return

    if action == "tensor":
        tensor_path = extra[0] if extra else ""
        with EvrReader(evr_path) as reader:
            if not tensor_path:
                t_chunks = [c["path"] for c in reader.chunks if c["kind"] == "TENSOR"]
                if not t_chunks:
                    print("Error: no TENSOR chunks found in capsule.", file=sys.stderr)
                    sys.exit(1)
                tensor_path = t_chunks[0]
            t_info = reader.read_tensor(tensor_path)
        if as_json:
            print(json.dumps(t_info, indent=2))
        else:
            print(f"=== Zero-Copy 64B-Aligned Tensor: {t_info['path']} ===")
            print(f"  Offset : {t_info['data_offset']} (aligned_64={t_info['aligned_64']})")
            print(f"  Shape  : {t_info['shape']} ({t_info['dtype']}, numel={t_info['numel']})")
            print(
                f"  Stats  : min={t_info['min']} · max={t_info['max']} · mean={t_info['mean']} · L2={t_info['l2_norm']}"
            )
            print(f"  Values : {t_info['values'][:16]}")
        return

    if action == "prove":
        chunk_id: str | int = extra[0] if extra else 0
        if isinstance(chunk_id, str) and chunk_id.isdigit():
            chunk_id = int(chunk_id)
        with EvrReader(evr_path) as reader:
            proof = reader.prove_chunk(chunk_id)
        if as_json:
            print(json.dumps(proof, indent=2))
        else:
            print(f"=== RFC 6962 Merkle Inclusion Proof: {proof['path']} (#{proof['chunk_index']}) ===")
            print(f"  Merkle Root : {proof['merkle_root']}")
            print(f"  Audit Steps : {proof['proof_length']} · Verified={proof['verified']}")
        return

    if action == "extract":
        dest = Path(args.output or "evr_extracted").expanduser().resolve()
        with EvrReader(evr_path) as reader:
            files = reader.extract_all(dest, include_internal=bool(args.include_internal))
        if as_json:
            print(
                json.dumps(
                    {"dest": str(dest), "extracted_count": len(files), "files": [str(f) for f in files]}, indent=2
                )
            )
        else:
            print(f"Extracted {len(files)} files from {evr_path.name} -> {dest}")
        return


def main_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="evr",
        description="The `.evr` ('Everything' v1.0) Universal Zero-Copy Polyglot File Format CLI",
    )
    sub = parser.add_subparsers(dest="cmd")
    register_evr_subparser(sub, show_all_aliases=False)
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0] not in ("evr", "everything"):
        raw = ["evr", *raw]
    args = parser.parse_args(raw)
    cmd_evr(args)
