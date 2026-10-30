#!/usr/bin/env bash
set -euo pipefail

echo "=== [sovereign-kb] Running Deterministic Quality Gate (python-uv-ai) ==="
# Stage 1: Git conflict & secret hygiene
! grep -rnIE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ --exclude=quality_gate.sh --exclude=cli.py || exit 1
# Stage 2: Ruff lint & format check
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
# Stage 3: Deterministic unit & property tests (pinned seed)
PYTHONHASHSEED=42 uv run --extra dev pytest -q
# Stage 4: Smoke & CLI/API contract verification
uv run python -m compileall -q src tests
# Stage 5: Goldwood Index G(S) >= 9.0 self-audit gate
uv run python -m sovereign_kb.gwi audit . --strict
echo "=== [sovereign-kb] ALL QUALITY GATES PASSED ==="
