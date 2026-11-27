"""Portable warm model server, SSH-tunnel resolver, GWI Critic client, and semantic RRF search."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .corpus import extract_json_object, load_chunks, rrf_fuse

DEFAULT_CRITIC_SYSTEM = (
    "You are the Sovereign Tri-Council & Goldwood Index (GWI v1.4.0) Technical Critic. "
    "Evaluate artifacts strictly against Laws L0-L10 and GWI gates (G >= 9.0, P0/P1/P2 defects). "
    "Return valid JSON with keys: verdict ('APPROVED'|'CHANGES_REQUESTED'), "
    "gwi_score_est (float), p0_findings (list[str]), p1_findings (list[str]), "
    "violated_laws (list[str]), and concise_remediation (str)."
)


def resolve_models_url(
    preferred_url: str = "http://127.0.0.1:8012",
    ssh_host: str | None = None,
    local_port: int = 18012,
) -> str:
    """Return a reachable model-server URL, optionally opening an SSH tunnel to `ssh_host`."""

    def _healthy(base_url: str, timeout: float = 1.0) -> bool:
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + "/health", timeout=timeout) as resp:
                return bool(resp.status == 200)
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    if _healthy(preferred_url, timeout=1.0):
        return preferred_url.rstrip("/")
    if not ssh_host:
        return preferred_url.rstrip("/")

    local_url = f"http://127.0.0.1:{local_port}"
    if _healthy(local_url, timeout=0.6):
        return local_url

    subprocess.run(
        [
            "ssh",
            "-fN",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ConnectTimeout=5",
            "-L",
            f"{local_port}:127.0.0.1:8012",
            ssh_host,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    for _ in range(10):
        if _healthy(local_url, timeout=0.5):
            return local_url
        time.sleep(0.2)
    return preferred_url.rstrip("/")


def call_critic(
    prompt: str,
    url: str = "http://127.0.0.1:8012",
    ssh_host: str | None = None,
    max_new_tokens: int = 260,
) -> dict[str, Any]:
    """Call POST /critic on the warm model server."""
    active_url = resolve_models_url(url, ssh_host=ssh_host)
    payload = json.dumps({"prompt": prompt, "max_new_tokens": max_new_tokens}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("SKB_MODELS_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(active_url + "/critic", data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return dict(data) if isinstance(data, dict) else {"ok": False, "raw": str(data)}


def _embed_remote(texts: list[str], url: str) -> Any:
    import numpy as np

    payload = json.dumps({"texts": texts}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("SKB_MODELS_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url.rstrip("/") + "/embed", data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return np.asarray(data["embeddings"], dtype=np.float16)


def build_semantic_index(
    vault_dir: Path,
    url: str = "http://127.0.0.1:8012",
    ssh_host: str | None = None,
    scope: str = "private-local",
) -> int:
    """Embed vault chunks via POST /embed and save `.kb/training/minilm_index.npz`."""
    import numpy as np

    active_url = resolve_models_url(url, ssh_host=ssh_host)
    chunks = load_chunks(vault_dir, scope=scope, max_words=380, overlap_words=40)
    texts = [f"{c.heading_path}\n{c.text}" for c in chunks]
    vecs = [_embed_remote(texts[i : i + 64], active_url) for i in range(0, len(texts), 64)]
    matrix = np.vstack(vecs) if vecs else np.zeros((0, 384), dtype=np.float16)

    train_dir = vault_dir / ".kb" / "training"
    train_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(train_dir / "minilm_index.npz", embeddings=matrix)
    meta = [
        {
            "chunk_id": c.chunk_id,
            "rel_path": c.rel_path,
            "heading_path": c.heading_path,
            "snippet": " ".join(c.text.split()[:36]),
        }
        for c in chunks
    ]
    (train_dir / "minilm_meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return len(meta)


def semantic_search(
    vault_dir: Path,
    query: str,
    top_k: int = 8,
    url: str = "http://127.0.0.1:8012",
    ssh_host: str | None = None,
    use_hips: bool = True,
) -> list[dict[str, Any]]:
    """Query `.kb/training/minilm_index.npz` and optionally fuse with `hips` BM25 via RRF."""
    import numpy as np

    active_url = resolve_models_url(url, ssh_host=ssh_host)
    train_dir = vault_dir / ".kb" / "training"
    idx_path = train_dir / "minilm_index.npz"
    meta_path = train_dir / "minilm_meta.json"
    if not idx_path.is_file() or not meta_path.is_file():
        build_semantic_index(vault_dir, url=active_url)

    matrix = np.load(idx_path)["embeddings"].astype(np.float32)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    q_vec = _embed_remote([query], active_url).astype(np.float32)[0]
    sims = matrix @ q_vec
    top_chunk_idx = np.argsort(-sims)[: max(top_k * 4, 24)]

    dense_paths: list[str] = []
    best_by_path: dict[str, dict[str, Any]] = {}
    for idx in top_chunk_idx:
        item = meta[int(idx)]
        rp = str(item["rel_path"])
        if rp not in best_by_path:
            best_by_path[rp] = {**item, "dense_score": round(float(sims[int(idx)]), 4)}
            dense_paths.append(rp)

    bm25_paths: list[str] = []
    hips_bin = shutil.which("hips") or str(Path.home() / ".local" / "bin" / "hips")
    hips_idx = vault_dir / ".kb" / "hips-index"
    if use_hips and Path(hips_bin).is_file() and hips_idx.is_dir():
        proc = subprocess.run(
            [hips_bin, "--index", str(hips_idx), "search", query, "--limit", "15", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                parsed = json.loads(proc.stdout)
                hits = parsed if isinstance(parsed, list) else parsed.get("hits", [])
                for h in hits:
                    rp = str(h.get("rel_path") or h.get("path") or "")
                    if rp and rp not in bm25_paths:
                        bm25_paths.append(rp)
            except json.JSONDecodeError:
                pass

    fused = rrf_fuse([dense_paths, bm25_paths] if bm25_paths else [dense_paths])
    out: list[dict[str, Any]] = []
    for rp, rrf_score in fused[:top_k]:
        info = best_by_path.get(
            rp, {"rel_path": rp, "heading_path": rp, "snippet": "(matched via hips BM25)", "dense_score": 0.0}
        )
        out.append({**info, "rrf_score": round(rrf_score, 5), "in_hips": rp in bm25_paths})
    return out


def serve_models(
    embedder_dir: Path,
    critic_base: str = "Qwen/Qwen2.5-Coder-3B-Instruct",
    critic_lora_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8012,
) -> None:
    """Start a warm ThreadingHTTPServer exposing /health, /embed, and /critic."""
    import torch
    import torch.nn.functional as F
    from peft import PeftModel
    from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

    lock = threading.Lock()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    emb_tok = AutoTokenizer.from_pretrained(embedder_dir, local_files_only=True)
    emb_model = AutoModel.from_pretrained(embedder_dir, local_files_only=True).to(device)
    emb_model.eval()

    critic_tok = None
    critic_model = None
    if critic_lora_dir is not None and critic_lora_dir.is_dir():
        critic_tok = AutoTokenizer.from_pretrained(critic_lora_dir, local_files_only=True)
        if critic_tok.pad_token is None:
            critic_tok.pad_token = critic_tok.eos_token
        base_lm = AutoModelForCausalLM.from_pretrained(
            critic_base,
            local_files_only=True,
            dtype=torch.bfloat16,
            device_map="auto" if device.type == "cuda" else None,
        )
        critic_model = PeftModel.from_pretrained(base_lm, critic_lora_dir)
        critic_model.eval()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, code: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/health":
                models = ["embedder"] + (["critic"] if critic_model is not None else [])
                self._send(200, {"status": "ok", "models": models})
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads((self.rfile.read(length) if length > 0 else b"{}").decode("utf-8"))
            if self.path == "/embed":
                texts = [str(t) for t in data.get("texts", [])]
                vecs: list[Any] = []
                with lock, torch.no_grad():
                    for i in range(0, len(texts), 64):
                        enc = emb_tok(
                            texts[i : i + 64], padding=True, truncation=True, max_length=256, return_tensors="pt"
                        ).to(device)
                        hid = emb_model(**enc).last_hidden_state
                        mask = enc["attention_mask"].unsqueeze(-1).expand(hid.size()).float()
                        vecs.append(
                            F.normalize(torch.sum(hid * mask, 1) / mask.sum(1).clamp(min=1e-9), p=2, dim=-1).cpu()
                        )
                embs = torch.cat(vecs, dim=0).tolist() if vecs else []
                self._send(200, {"embeddings": embs, "dim": len(embs[0]) if embs else 384})
                return
            if self.path == "/critic" and critic_model is not None and critic_tok is not None:
                prompt = str(data.get("prompt") or "")
                system = str(data.get("system") or DEFAULT_CRITIC_SYSTEM)
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
                rendered = (
                    critic_tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True) + '{"verdict": "'
                )
                with lock, torch.no_grad():
                    enc = critic_tok(rendered, return_tensors="pt", truncation=True, max_length=1536).to(
                        critic_model.device
                    )
                    out = critic_model.generate(
                        **enc, max_new_tokens=int(data.get("max_new_tokens") or 260), do_sample=False
                    )
                raw = '{"verdict": "' + critic_tok.decode(out[0][enc["input_ids"].shape[1] :], skip_special_tokens=True)
                parsed = extract_json_object(raw)
                self._send(200, {"ok": parsed is not None, "verdict": parsed, "raw": raw})
                return
            self._send(404, {"error": "not_found"})

    ThreadingHTTPServer((host, port), _Handler).serve_forever()
