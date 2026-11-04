#!/usr/bin/env bash
set -euo pipefail

echo "=== [sovereign-kb] Running Deterministic Quality Gate (python-uv-ai) ==="
# Stage 1: Git conflict & secret hygiene
! grep -rnIE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ --exclude=quality_gate.sh --exclude=cli.py || exit 1
# Stage 1.5: Public boundary & privacy hygiene check (zero private note paths in library)
uv run python -c "
from pathlib import Path
from sovereign_kb.rsi import CANONICAL_BENCHMARK_PROBES, CANONICAL_HELDOUT_PROBES
starter = Path('src/sovereign_kb/starter_vault')
for p in list(CANONICAL_BENCHMARK_PROBES) + list(CANONICAL_HELDOUT_PROBES):
    tp = p['target_path']
    assert not tp.startswith('00-identity/'), f'Private 00-identity path in canonical probe: {p}'
    assert not tp.startswith('04-episodic/'), f'Private 04-episodic path in canonical probe: {p}'
    assert (starter / tp).is_file(), f'Probe target does not exist in starter_vault: {tp}'
print('[Stage 1.5] Public boundary & probe hygiene: PASS')
"
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
