#!/usr/bin/env bash
set -euo pipefail

# Always run from the repository root, whatever the caller's cwd.
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "=== [sovereign-kb] Running Deterministic Quality Gate (python-uv-ai) ==="
# Optional: regenerate dashboard browser evidence first, so every later stage sees fresh evidence.
if [ "${QG_BROWSER:-0}" = "1" ]; then
  uv run --with playwright==1.58.0 python scripts/audit_dashboard.py >/dev/null
  echo "[Stage -1] Dashboard browser evidence regenerated"
fi
# Stage 0: Lockfile reproducibility (uv.lock must match pyproject.toml)
uv lock --check

# Stage 1: Git conflict-marker hygiene. grep exit codes: 0 = markers found (fail), 1 = clean, >1 = grep error (fail).
set +e
grep -rnIE '^(<<<<<<<|=======|>>>>>>>)' . --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ \
  --exclude=quality_gate.sh --exclude=scaffold.py
grep_rc=$?
set -e
if [ "$grep_rc" -eq 0 ]; then
  echo "[Stage 1] FAIL: git conflict markers found" >&2
  exit 1
elif [ "$grep_rc" -gt 1 ]; then
  echo "[Stage 1] FAIL: grep error (exit $grep_rc)" >&2
  exit 1
fi
echo "[Stage 1] Conflict-marker hygiene: PASS"

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
# Stage 2: Ruff lint, format & mypy type check
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev mypy src/sovereign_kb
# Stage 3: Deterministic unit & property tests (pinned seed)
PYTHONHASHSEED=42 uv run --extra dev pytest -q
# Stage 4: Smoke & CLI/packaging contract verification
uv run python -m compileall -q src tests
for entry in sovereign-kb skb gwi organon evr; do
  uv run "$entry" --help >/dev/null
done
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT
uv build --quiet --out-dir "$build_dir"
ls "$build_dir"/*.whl "$build_dir"/*.tar.gz >/dev/null
echo "[Stage 4] CLI entrypoints (5) & sdist/wheel build: PASS"
# Stage 5: Goldwood Index G(S) >= 9.0 self-audit gate
uv run python -m sovereign_kb.gwi audit . --strict
# Stage 6: Strict craft lenses against real dashboard browser evidence.
# Evidence comes from scripts/audit_dashboard.py (gitignored under evidence/); QG_BROWSER=1 regenerates it at the start.
report="evidence/playwright/report.json"
if [ -f "$report" ]; then
  if [ src/sovereign_kb/dashboard.py -nt "$report" ]; then
    echo "[Stage 6] FAIL: $report is older than src/sovereign_kb/dashboard.py." >&2
    echo "          Re-run: uv run --with playwright==1.58.0 python scripts/audit_dashboard.py" >&2
    exit 1
  fi
  uv run python -m sovereign_kb.gwi audit . --strict --strict-lenses
else
  echo "[Stage 6] SKIPPED: no $report (run scripts/audit_dashboard.py or QG_BROWSER=1 for strict-lens certification)"
fi
echo "=== [sovereign-kb] ALL QUALITY GATES PASSED ==="
