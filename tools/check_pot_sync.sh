#!/usr/bin/env bash
# Validate that docking/locale/docking.pot matches source strings.
# Normalizes dynamic POT metadata before comparison.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POT_FILE="${ROOT_DIR}/docking/locale/docking.pot"

for cmd in msgcat diff mktemp sed; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[i18n] ${cmd} is required." >&2
    exit 1
  fi
done

if [ ! -f "${POT_FILE}" ]; then
  echo "[i18n] Missing template file: ${POT_FILE}" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

cp "${POT_FILE}" "${tmp_dir}/current.pot"
bash "${ROOT_DIR}/tools/i18n-extract.sh" "${tmp_dir}/generated.pot" >/dev/null

normalize_pot() {
  local src="$1"
  local dst="$2"
  msgcat --sort-output --no-location --no-wrap "${src}" \
    | sed -E 's/^"POT-Creation-Date: .*\\n"$/\"POT-Creation-Date: NORMALIZED\\n\"/' \
    > "${dst}"
}

normalize_pot "${tmp_dir}/current.pot" "${tmp_dir}/current.norm"
normalize_pot "${tmp_dir}/generated.pot" "${tmp_dir}/generated.norm"

if ! diff -u "${tmp_dir}/current.norm" "${tmp_dir}/generated.norm" >"${tmp_dir}/pot.diff"; then
  echo "[i18n] docking/locale/docking.pot is out of date with source strings." >&2
  echo "[i18n] Run: ./tools/i18n-extract.sh" >&2
  sed -n '1,80p' "${tmp_dir}/pot.diff" >&2
  exit 1
fi

echo "[i18n] POT template is in sync with source strings."
