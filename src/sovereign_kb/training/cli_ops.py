"""CLI handler for `skb train` subcommands."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .datasets import (
    build_critic_dataset,
    build_twin_dataset,
    write_nanolm_corpus,
    write_retrieval_datasets,
)
from .serve import build_semantic_index, call_critic, semantic_search, serve_models
from .trainers import train_critic_orpo, train_embedder, train_nanolm_mlx


def cmd_train(args: Any, resolve_vault_dir_fn: Callable[..., Path]) -> None:
    subcmd = getattr(args, "train_cmd", "datasets") or "datasets"
    vault_dir = resolve_vault_dir_fn(getattr(args, "vault", None))
    train_root = vault_dir / ".kb" / "training"
    data_dir = Path(getattr(args, "data_dir", None) or (train_root / "datasets")).expanduser()
    out_dir = Path(getattr(args, "out_dir", None) or train_root).expanduser()
    scope = str(getattr(args, "scope", "public-safe") or "public-safe")
    url = str(getattr(args, "url", None) or os.environ.get("SKB_MODELS_URL", "http://127.0.0.1:8012"))
    ssh_host = getattr(args, "ssh_host", None)

    if subcmd == "datasets":
        track = str(getattr(args, "track", "retrieval") or "retrieval")
        api_base = str(getattr(args, "api_base", "http://127.0.0.1:8000/v1"))
        model = str(getattr(args, "model", "qwen3.6-35b-a3b"))
        api_key = str(getattr(args, "api_key", "") or os.environ.get("OPENAI_API_KEY", ""))
        res: dict[str, Any]
        if track == "retrieval":
            res = {"track": "retrieval", "counts": write_retrieval_datasets(vault_dir, data_dir, scope=scope)}
        elif track == "nanolm":
            res = {"track": "nanolm", "stats": write_nanolm_corpus(vault_dir, data_dir, scope=scope)}
        elif track == "critic":
            res = {
                "track": "critic",
                "counts": build_critic_dataset(
                    vault_dir, data_dir, api_base=api_base, model=model, api_key=api_key, scope=scope
                ),
            }
        else:
            res = {
                "track": "twin",
                "counts": build_twin_dataset(
                    vault_dir, data_dir, api_base=api_base, model=model, api_key=api_key, scope=scope
                ),
            }
        print(json.dumps(res, indent=2))
        return

    if subcmd == "embedder":
        print(json.dumps(train_embedder(data_dir, out_dir, epochs=int(getattr(args, "epochs", 3))), indent=2))
        return

    if subcmd == "critic":
        base = str(getattr(args, "model", "Qwen/Qwen2.5-Coder-3B-Instruct"))
        print(
            json.dumps(
                train_critic_orpo(data_dir, out_dir, base_model=base, epochs=int(getattr(args, "epochs", 3))), indent=2
            )
        )
        return

    if subcmd == "nanolm":
        print(json.dumps(train_nanolm_mlx(data_dir, out_dir, steps=int(getattr(args, "steps", 1200))), indent=2))
        return

    if subcmd == "serve":
        emb_dir = out_dir / "checkpoints" / "embedder_minilm"
        lora_dir = out_dir / "checkpoints" / "critic_orpo"
        serve_models(
            emb_dir, critic_lora_dir=lora_dir if lora_dir.is_dir() else None, port=int(getattr(args, "port", 8012))
        )
        return

    if subcmd == "audit":
        target = getattr(args, "target", None)
        if target and Path(target).is_file():
            snippet = Path(target).read_text(encoding="utf-8", errors="replace")[:4500]
            prompt = f"Review `{target}` against GWI v1.4.0 and Laws L0-L10:\n\n```\n{snippet}\n```"
        elif target:
            prompt = target
        else:
            diff = subprocess.run(["git", "diff"], capture_output=True, text=True, check=False).stdout[:4500]
            prompt = (
                f"Review this git diff against GWI v1.4.0 and Laws L0-L10:\n\n```diff\n{diff}\n```"
                if diff.strip()
                else "Empty diff"
            )
        print(json.dumps(call_critic(prompt, url=url, ssh_host=ssh_host), indent=2, ensure_ascii=False))
        return

    if subcmd == "search":
        target = getattr(args, "target", None)
        if not target:
            count = build_semantic_index(vault_dir, url=url, ssh_host=ssh_host, scope="private-local")
            print(json.dumps({"indexed_chunks": count}))
            return
        hits = semantic_search(vault_dir, target, top_k=int(getattr(args, "limit", 8)), url=url, ssh_host=ssh_host)
        if getattr(args, "json", False):
            print(json.dumps(hits, indent=2, ensure_ascii=False))
        else:
            for idx, h in enumerate(hits, start=1):
                print(
                    f"{idx}. [{h['dense_score']:.3f} | rrf={h['rrf_score']:.4f}] {h['rel_path']} — {h['heading_path']}"
                )
        return

    raise ValueError(f"Unknown `skb train` subcommand: {subcmd!r}")
