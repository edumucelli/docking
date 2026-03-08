#!/usr/bin/env bash
# Run the GUI/integration-oriented Docking test slice under Xvfb and an isolated
# D-Bus session.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if ! command -v xvfb-run >/dev/null 2>&1; then
  echo "[gui-tests] xvfb-run is required." >&2
  exit 1
fi

if ! command -v dbus-run-session >/dev/null 2>&1; then
  echo "[gui-tests] dbus-run-session is required." >&2
  exit 1
fi

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "[gui-tests] python3 is required." >&2
  exit 1
fi

DEFAULT_TARGETS=(
  tests/ui/test_pointer_scenarios.py
  tests/ui/test_edges.py
  tests/ui/test_menu_integration.py
  tests/ui/test_preview_popup_integration.py
  tests/ui/test_dock_window_integration.py
  tests/ui/test_interaction.py
  tests/ui/test_dnd_integration.py
  tests/ui/test_renderer_integration.py
)

if [ "$#" -gt 0 ]; then
  TARGETS=("$@")
else
  TARGETS=("${DEFAULT_TARGETS[@]}")
fi

export GSETTINGS_BACKEND="${GSETTINGS_BACKEND:-memory}"
export GTK_THEME="${GTK_THEME:-Adwaita}"

exec xvfb-run -a dbus-run-session \
  "${PYTHON_BIN}" -m pytest "${TARGETS[@]}"
