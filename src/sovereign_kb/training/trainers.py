"""Optional ML training backends (PyTorch/PEFT on CUDA/CPU & MLX on Apple Silicon)."""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any


def available_ram_gib() -> float:
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return 999.0
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / (1024.0 * 1024.0)
    return 0.0


def train_embedder(
    data_dir: Path,
    out_dir: Path,
    base_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    epochs: int = 3,
    batch_size: int = 32,
    lr: float = 2e-5,
    temperature: float = 0.05,
) -> dict[str, Any]:
    """Fine-tune a bi-encoder with InfoNCE + explicit hard negatives."""
    import torch
    import torch.nn.functional as F
    from torch.utils.data import DataLoader
    from transformers import AutoModel, AutoTokenizer

    torch.manual_seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    model = AutoModel.from_pretrained(base_model).to(device)

    train_rows = [
        json.loads(ln)
        for ln in (data_dir / "retrieval_train.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    val_rows = [
        json.loads(ln)
        for ln in (data_dir / "retrieval_val.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]

    def _mean_pool(hidden: Any, mask: Any) -> Any:
        m = mask.unsqueeze(-1).expand(hidden.size()).float()
        return F.normalize(torch.sum(hidden * m, dim=1) / torch.clamp(m.sum(dim=1), min=1e-9), p=2, dim=-1)

    @torch.no_grad()
    def _eval_retrieval() -> dict[str, Any]:
        if not val_rows:
            return {"queries": 0, "mrr@10": 0.0, "recall@1": 0.0, "recall@5": 0.0, "ndcg@10": 0.0}
        passages: list[str] = []
        p2id: dict[str, int] = {}
        queries: list[str] = []
        gold_ids: list[int] = []
        for r in val_rows:
            for k in ("positive", "hard_negative"):
                if r[k] not in p2id:
                    p2id[r[k]] = len(passages)
                    passages.append(r[k])
            queries.append(r["query"])
            gold_ids.append(p2id[r["positive"]])

        def _enc(texts: list[str]) -> Any:
            model.eval()
            vecs = []
            for i in range(0, len(texts), 64):
                enc = tokenizer(
                    texts[i : i + 64], padding=True, truncation=True, max_length=256, return_tensors="pt"
                ).to(device)
                vecs.append(_mean_pool(model(**enc).last_hidden_state, enc["attention_mask"]).cpu())
            return torch.cat(vecs, dim=0)

        sims = _enc(queries) @ _enc(passages).T
        _, top_idx = torch.topk(sims, k=min(10, len(passages)), dim=-1)
        mrr10 = r1 = r5 = ndcg10 = 0.0
        for qi, gold in enumerate(gold_ids):
            ranked = top_idx[qi].tolist()
            if gold in ranked:
                rank = ranked.index(gold) + 1
                mrr10 += 1.0 / rank
                ndcg10 += 1.0 / math.log2(rank + 1)
                r1 += float(rank == 1)
                r5 += float(rank <= 5)
        n = len(gold_ids)
        return {
            "queries": n,
            "mrr@10": round(mrr10 / n, 4),
            "recall@1": round(r1 / n, 4),
            "recall@5": round(r5 / n, 4),
            "ndcg@10": round(ndcg10 / n, 4),
        }

    baseline = _eval_retrieval()

    def _collate(batch: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return (
            dict(
                tokenizer(
                    [b["query"] for b in batch], padding=True, truncation=True, max_length=256, return_tensors="pt"
                )
            ),
            dict(
                tokenizer(
                    [b["positive"] for b in batch], padding=True, truncation=True, max_length=256, return_tensors="pt"
                )
            ),
            dict(
                tokenizer(
                    [b["hard_negative"] for b in batch],
                    padding=True,
                    truncation=True,
                    max_length=256,
                    return_tensors="pt",
                )
            ),
        )

    loader = DataLoader(train_rows, batch_size=batch_size, shuffle=True, collate_fn=_collate)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    t0 = time.time()
    epoch_losses: list[float] = []
    for _ in range(epochs):
        model.train()
        tot = 0.0
        steps = 0
        for q_e, p_e, n_e in loader:
            q_e = {k: v.to(device) for k, v in q_e.items()}
            p_e = {k: v.to(device) for k, v in p_e.items()}
            n_e = {k: v.to(device) for k, v in n_e.items()}
            with torch.amp.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                qv = _mean_pool(model(**q_e).last_hidden_state, q_e["attention_mask"])
                pv = _mean_pool(model(**p_e).last_hidden_state, p_e["attention_mask"])
                nv = _mean_pool(model(**n_e).last_hidden_state, n_e["attention_mask"])
                cand = torch.cat([pv, nv], dim=0)
                loss = F.cross_entropy((qv @ cand.T) / temperature, torch.arange(qv.size(0), device=device))
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += float(loss.item())
            steps += 1
        epoch_losses.append(round(tot / max(1, steps), 4))

    ckpt_dir = out_dir / "checkpoints" / "embedder_minilm"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(ckpt_dir)
    tokenizer.save_pretrained(ckpt_dir)
    report: dict[str, Any] = {
        "track": "embedder",
        "base_model": base_model,
        "train_triples": len(train_rows),
        "epoch_losses": epoch_losses,
        "train_seconds": round(time.time() - t0, 2),
        "baseline": baseline,
        "finetuned": _eval_retrieval(),
        "checkpoint_dir": str(ckpt_dir),
    }
    res_dir = out_dir / "results"
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "track1_embedder.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def train_critic_orpo(
    data_dir: Path,
    out_dir: Path,
    base_model: str = "Qwen/Qwen2.5-Coder-3B-Instruct",
    epochs: int = 3,
    grad_accum: int = 8,
    lr: float = 1e-4,
    beta: float = 0.25,
    max_steps: int = 160,
    min_free_gib: float = 12.0,
) -> dict[str, Any]:
    """Train a GWI / Tri-Council Critic LoRA with pure-PyTorch ORPO."""
    import torch
    import torch.nn.functional as F
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    avail = available_ram_gib()
    if avail < min_free_gib:
        raise RuntimeError(f"Only {avail:.2f} GiB RAM available (< {min_free_gib:.1f} GiB)")

    torch.manual_seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tok = AutoTokenizer.from_pretrained(base_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        dtype=torch.bfloat16,
        device_map="auto" if device.type == "cuda" else None,
    )
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = get_peft_model(
        model,
        LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        ),
    )

    train_rows = [
        json.loads(ln)
        for ln in (data_dir / "critic_train.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    val_rows = [
        json.loads(ln) for ln in (data_dir / "critic_val.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()
    ]

    def _tok_pair(row: dict[str, Any], key: str) -> dict[str, Any]:
        msgs = [{"role": "system", "content": row["system"]}, {"role": "user", "content": row["prompt"]}]
        prompt = (
            tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            if getattr(tok, "chat_template", None)
            else f"System: {row['system']}\n\nUser: {row['prompt']}\n\nAssistant:\n"
        )
        p_ids = tok(prompt, add_special_tokens=False, truncation=True, max_length=576)["input_ids"]
        t_ids = tok(row[key], add_special_tokens=False, truncation=True, max_length=640 - len(p_ids) - 1)["input_ids"]
        if tok.eos_token_id is not None:
            t_ids = [*t_ids, tok.eos_token_id]
        ids = (p_ids + t_ids)[:640]
        labels = ([-100] * len(p_ids) + t_ids)[:640]
        return {
            "input_ids": torch.tensor([ids], dtype=torch.long, device=device),
            "labels": torch.tensor([labels], dtype=torch.long, device=device),
            "attention_mask": torch.ones((1, len(ids)), dtype=torch.long, device=device),
        }

    def _mean_logp(batch: dict[str, Any]) -> tuple[Any, Any]:
        logits = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits[:, :-1, :].float()
        labels = batch["labels"][:, 1:]
        mask = labels != -100
        tok_lp = torch.gather(F.log_softmax(logits, dim=-1), -1, labels.masked_fill(~mask, 0).unsqueeze(-1)).squeeze(-1)
        mean_lp = (tok_lp * mask).sum() / mask.sum().clamp(min=1)
        return -mean_lp, mean_lp

    @torch.no_grad()
    def _eval() -> dict[str, Any]:
        model.eval()
        wins = 0
        losses: list[float] = []
        margins: list[float] = []
        for row in val_rows:
            with torch.amp.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                sft_c, lpc = _mean_logp(_tok_pair(row, "chosen"))
                _, lpr = _mean_logp(_tok_pair(row, "rejected"))
            lo_c = lpc - torch.log1p(-torch.exp(lpc.clamp(max=-1e-5)))
            lo_r = lpr - torch.log1p(-torch.exp(lpr.clamp(max=-1e-5)))
            losses.append(float(sft_c.item()))
            margins.append(float((lo_c - lo_r).item()))
            wins += int(float(lpc.item()) > float(lpr.item()))
        n = max(1, len(val_rows))
        ml = sum(losses) / n
        return {
            "val_pairs": len(val_rows),
            "val_sft_loss": round(ml, 4),
            "val_ppl": round(math.exp(min(20.0, ml)), 2),
            "preference_win_rate": round(wins / n, 4),
            "mean_log_odds_margin": round(sum(margins) / n, 4),
        }

    baseline = _eval()
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=0.01)
    t0 = time.time()
    step = 0
    opt.zero_grad(set_to_none=True)
    model.train()
    for _ in range(epochs):
        for idx, row in enumerate(train_rows):
            if step >= max_steps:
                break
            with torch.amp.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                sft_c, lpc = _mean_logp(_tok_pair(row, "chosen"))
                _, lpr = _mean_logp(_tok_pair(row, "rejected"))
                lo_c = lpc - torch.log1p(-torch.exp(lpc.clamp(max=-1e-5)))
                lo_r = lpr - torch.log1p(-torch.exp(lpr.clamp(max=-1e-5)))
                loss = (sft_c + beta * (-F.logsigmoid(lo_c - lo_r))) / grad_accum
            loss.backward()
            if (idx + 1) % grad_accum == 0 or (idx + 1) == len(train_rows):
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
        if step >= max_steps:
            break

    ckpt_dir = out_dir / "checkpoints" / "critic_orpo"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(ckpt_dir)
    tok.save_pretrained(ckpt_dir)
    report: dict[str, Any] = {
        "track": "critic_orpo",
        "base_model": base_model,
        "optimizer_steps": step,
        "train_seconds": round(time.time() - t0, 2),
        "baseline": baseline,
        "finetuned": _eval(),
        "checkpoint_dir": str(ckpt_dir),
    }
    res_dir = out_dir / "results"
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "track2_critic_orpo.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def train_nanolm_mlx(
    data_dir: Path,
    out_dir: Path,
    steps: int = 1200,
    batch_size: int = 16,
    ctx_len: int = 256,
    vocab_size: int = 512,
    lr: float = 5e-4,
) -> dict[str, Any]:
    """Train a from-scratch ~11.9M-param RMSNorm+RoPE+SwiGLU Nano-LM on Apple Silicon MLX."""
    import re

    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten

    mx.random.seed(42)
    train_text = (data_dir / "nanolm_train.txt").read_text(encoding="utf-8")
    val_text = (data_dir / "nanolm_val.txt").read_text(encoding="utf-8")

    # Fast word-cached byte-level BPE
    word_freqs: Counter[tuple[int, ...]] = Counter()
    for w in train_text.split()[:20000]:
        b = tuple((" " + w).encode("utf-8"))
        if len(b) >= 2:
            word_freqs[b] += 1
    merges: list[tuple[int, int]] = []
    for i in range(max(0, vocab_size - 256)):
        pair_counts: Counter[tuple[int, int]] = Counter()
        for seq, freq in word_freqs.items():
            for j in range(len(seq) - 1):
                pair_counts[(seq[j], seq[j + 1])] += freq
        if not pair_counts:
            break
        best_pair, best_cnt = pair_counts.most_common(1)[0]
        if best_cnt < 2:
            break
        merges.append(best_pair)
        nid = 256 + i
        a_tok, b_tok = best_pair
        new_freqs: Counter[tuple[int, ...]] = Counter()
        for seq, freq in word_freqs.items():
            out_s: list[int] = []
            idx = 0
            while idx < len(seq):
                if idx + 1 < len(seq) and seq[idx] == a_tok and seq[idx + 1] == b_tok:
                    out_s.append(nid)
                    idx += 2
                else:
                    out_s.append(seq[idx])
                    idx += 1
            new_freqs[tuple(out_s)] += freq
        word_freqs = new_freqs

    ranks = {p: 256 + i for i, p in enumerate(merges)}

    def _encode(text: str) -> list[int]:
        cache: dict[bytes, list[int]] = {}
        ids: list[int] = []
        for tok_str in re.findall(r"\s*\S+|\s+", text):
            raw = tok_str.encode("utf-8")
            if raw not in cache:
                seq = list(raw)
                while len(seq) >= 2:
                    best_r, best_p = None, None
                    for j in range(len(seq) - 1):
                        r = ranks.get((seq[j], seq[j + 1]))
                        if r is not None and (best_r is None or r < best_r):
                            best_r, best_p = r, (seq[j], seq[j + 1])
                    if best_p is None or best_r is None:
                        break
                    a_tok, b_tok = best_p
                    nxt: list[int] = []
                    k = 0
                    while k < len(seq):
                        if k + 1 < len(seq) and seq[k] == a_tok and seq[k + 1] == b_tok:
                            nxt.append(best_r)
                            k += 2
                        else:
                            nxt.append(seq[k])
                            k += 1
                    seq = nxt
                cache[raw] = seq
            ids.extend(cache[raw])
        return ids

    train_tokens = mx.array(_encode(train_text), dtype=mx.int32)
    val_tokens = mx.array(_encode(val_text), dtype=mx.int32)
    eff_vocab = 256 + len(merges)

    class _Block(nn.Module):
        def __init__(self, d: int = 384, h: int = 6) -> None:
            super().__init__()
            self.n1 = nn.RMSNorm(d)
            self.n2 = nn.RMSNorm(d)
            self.h = h
            self.hd = d // h
            self.q = nn.Linear(d, d, bias=False)
            self.k = nn.Linear(d, d, bias=False)
            self.v = nn.Linear(d, d, bias=False)
            self.o = nn.Linear(d, d, bias=False)
            self.rope = nn.RoPE(self.hd, traditional=False)
            self.w1 = nn.Linear(d, d * 3, bias=False)
            self.w2 = nn.Linear(d * 3, d, bias=False)
            self.w3 = nn.Linear(d, d * 3, bias=False)

        def __call__(self, x: Any, mask: Any) -> Any:
            bsz, seqlen, dim = x.shape
            nx = self.n1(x)
            q = self.rope(self.q(nx).reshape(bsz, seqlen, self.h, self.hd).transpose(0, 2, 1, 3))
            k = self.rope(self.k(nx).reshape(bsz, seqlen, self.h, self.hd).transpose(0, 2, 1, 3))
            v = self.v(nx).reshape(bsz, seqlen, self.h, self.hd).transpose(0, 2, 1, 3)
            att = mx.fast.scaled_dot_product_attention(q, k, v, scale=1.0 / math.sqrt(self.hd), mask=mask)
            x = x + self.o(att.transpose(0, 2, 1, 3).reshape(bsz, seqlen, dim))
            nx2 = self.n2(x)
            return x + self.w2(nn.silu(self.w1(nx2)) * self.w3(nx2))

    class _NanoLM(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.emb = nn.Embedding(eff_vocab, 384)
            self.blocks = [_Block() for _ in range(6)]
            self.nf = nn.RMSNorm(384)
            self.head = nn.Linear(384, eff_vocab, bias=False)

        def __call__(self, idx: Any) -> Any:
            x = self.emb(idx)
            mask = nn.MultiHeadAttention.create_additive_causal_mask(idx.shape[1]).astype(x.dtype)
            for blk in self.blocks:
                x = blk(x, mask)
            return self.head(self.nf(x))

    model = _NanoLM()
    mx.eval(model.parameters())
    param_count = sum(v.size for _, v in tree_flatten(model.parameters()))

    def _batch(tok_arr: Any) -> tuple[Any, Any]:
        starts = mx.random.randint(0, tok_arr.shape[0] - ctx_len - 1, shape=(batch_size,))
        idx = starts[:, None] + mx.arange(ctx_len)[None, :]
        return tok_arr[idx], tok_arr[idx + 1]

    def _eval(n_batches: int = 16) -> float:
        tot = 0.0
        for _ in range(n_batches):
            xb, yb = _batch(val_tokens)
            ls = nn.losses.cross_entropy(model(xb), yb, reduction="mean")
            mx.eval(ls)
            tot += float(ls.item())
        return tot / n_batches

    init_vl = _eval(10)
    opt = optim.AdamW(learning_rate=optim.cosine_decay(lr, steps, end=lr * 0.1), weight_decay=0.01)
    lfn = nn.value_and_grad(model, lambda m, x, y: nn.losses.cross_entropy(m(x), y, reduction="mean"))
    t0 = time.time()
    for _ in range(steps):
        xb, yb = _batch(train_tokens)
        ls, grads = lfn(model, xb, yb)
        opt.update(model, grads)
        mx.eval(model.parameters(), opt.state, ls)
    train_secs = round(time.time() - t0, 2)
    final_vl = _eval(20)

    ckpt_dir = out_dir / "checkpoints" / "nanolm_mlx"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    model.save_weights(str(ckpt_dir / "weights.safetensors"))
    (ckpt_dir / "tokenizer.json").write_text(json.dumps({"vocab_size": eff_vocab, "merges": merges}), encoding="utf-8")

    report: dict[str, Any] = {
        "track": "nanolm_mlx",
        "vocab_size": eff_vocab,
        "param_count": param_count,
        "steps": steps,
        "train_seconds": train_secs,
        "init_val_loss": round(init_vl, 4),
        "final_val_loss": round(final_vl, 4),
        "final_val_ppl": round(math.exp(min(20.0, final_vl)), 2),
        "checkpoint_dir": str(ckpt_dir),
    }
    res_dir = out_dir / "results"
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "track4_nanolm.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
