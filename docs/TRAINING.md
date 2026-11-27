# Sovereign Vault Training & Distillation (`skb train` — F-24)

Turn any `sovereign-kb` Markdown vault into four domain-adapted local models with zero mandatory dependencies in the core package and deterministic privacy filtering:

1. **Retrieval Embedder (`--track retrieval` / `skb train embedder`)**: Mines `(query, positive, hard_negative)` triples from headings, `[[wikilink]]` edges, and `related:` metadata with stdlib TF-IDF hard negatives, then fine-tunes a bi-encoder (`all-MiniLM-L6-v2`) using InfoNCE loss.
2. **GWI / Tri-Council Critic (`--track critic` / `skb train critic`)**: Distills schema-validated GWI critique preference pairs from audit/lens/standard notes via any OpenAI-compatible teacher (`--api-base`), then trains a pure-PyTorch ORPO LoRA (`Qwen2.5-Coder-3B-Instruct`).
3. **Personal Vault Twin (`--track twin`)**: Distills vault-grounded instruction/answer pairs into `.kb/training/datasets/twin_{train,val}.jsonl` for LoRA SFT.
4. **From-Scratch Nano-LM (`--track nanolm` / `skb train nanolm`)**: Exports clean concatenated vault text (`<|endoftext|>` boundaries) and trains a ~11.9M-parameter RMSNorm + RoPE + SwiGLU causal Transformer on Apple Silicon MLX.

## Privacy & Zero-Leakage Guarantees

- **File-Level Deterministic Split**: `sha256(rel_path) % 10 == 0` assigns whole notes to `val` (~10%) or `train` (~90%), preventing chunks of the same note from leaking across splits.
- **`public-safe` Scope (default)**: Excludes `*private*`, `*secret*`, `*archive*`, and `*forensic-dossier*` paths, strips sensitive headings, and scrubs tokens, API keys, emails, IBANs, and private network IPs.

## Quickstart

```bash
# 1. Build retrieval triples & Nano-LM corpus (stdlib-only, zero GPU needed)
skb train datasets --track retrieval
skb train datasets --track nanolm

# 2. Distill Critic & Twin pairs from a local OpenAI-compatible teacher
skb train datasets --track critic --api-base http://127.0.0.1:8000/v1 --model qwen3.6-35b-a3b
skb train datasets --track twin   --api-base http://127.0.0.1:8000/v1 --model qwen3.6-35b-a3b

# 3. Train models (requires optional torch/transformers/peft or mlx)
skb train embedder --epochs 3
skb train critic   --epochs 3
skb train nanolm   --steps 1200

# 4. Serve warm & query/audit
skb train serve --port 8012
skb train audit path/to/file.py
skb train search "Goldwood Index subtractive laws"
```
