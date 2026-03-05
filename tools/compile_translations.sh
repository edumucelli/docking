#!/usr/bin/env bash
# Compile gettext catalogs (.po -> .mo) for all locales.
# Usage: ./tools/compile_translations.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCALE_DIR="${ROOT_DIR}/docking/locale"

if ! command -v msgfmt >/dev/null 2>&1; then
  echo "msgfmt is required (install gettext)." >&2
  exit 1
fi

if [ ! -d "${LOCALE_DIR}" ]; then
  echo "Locale directory not found: ${LOCALE_DIR}" >&2
  exit 1
fi

count=0
while IFS= read -r -d '' po_file; do
  mo_file="${po_file%.po}.mo"
  msgfmt --check-format --verbose -o "${mo_file}" "${po_file}" >/dev/null
  count=$((count + 1))
done < <(find "${LOCALE_DIR}" -type f -name 'docking.po' -print0 | sort -z)

if [ "${count}" -eq 0 ]; then
  echo "No locale .po files found under ${LOCALE_DIR}" >&2
  exit 1
fi

echo "Compiled ${count} translation catalogs."
