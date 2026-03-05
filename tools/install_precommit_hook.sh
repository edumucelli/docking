#!/usr/bin/env bash
# Install a strict local Git pre-commit hook for this repository.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_PATH="${ROOT_DIR}/.git/hooks/pre-commit"

cat >"${HOOK_PATH}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "Running ruff format..."
.venv/bin/ruff format --check docking/ tests/ || {
  echo "Run: ruff format docking/ tests/"
  exit 1
}
echo "Running ruff check..."
.venv/bin/ruff check docking/ tests/
echo "Running ty..."
.venv/bin/ty check docking/
echo "Checking i18n template sync..."
bash tools/i18n.sh --check-pot-sync
echo "Checking i18n completeness..."
bash tools/i18n.sh --check-catalogs --require-complete
echo "Running tests..."
.venv/bin/python -m pytest tests/ -q
echo "All checks passed."
EOF

chmod +x "${HOOK_PATH}"
echo "Installed strict pre-commit hook at ${HOOK_PATH}"
