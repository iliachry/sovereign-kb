---
title: Sovereign Compute & Cluster Architecture
type: domain
updated: 2026-10-22
tags: [infrastructure, compute, ollama, sglang, cluster]
related:
  - "[[01-now/now]]"
  - "[[00-identity/cognitive-and-working-style]]"
  - "[[03-life-domains/ventures-and-engineering]]"
---

# Sovereign Compute & Cluster Architecture

Document your local workstations, GPU inference servers, and embedding endpoints here.

---

## 1. Local & Cluster AI Endpoints
- **Embedding Endpoint (`KB_EMBED_URL`)**: Defaults to local Ollama (`http://127.0.0.1:11434/api/embeddings` with `nomic-embed-text:latest`).
- **Synthesis Endpoint (`KB_CHAT_URL`)**: Defaults to local OpenAI-compatible server (`http://127.0.0.1:8000/v1/chat/completions`) with automatic fallback to local Ollama (`http://127.0.0.1:11434/v1/chat/completions`).

---

## 2. Central Agent Credentials Standard
- Keep API tokens and environment credentials strictly outside Git repositories in `~/.config/agents/secrets.env` (`chmod 600`), sourced via `~/.zshenv`.
