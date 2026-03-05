#!/usr/bin/env bash
# Validate gettext translation completeness for all docking.po catalogs.
# Fails when:
# - a locale catalog is out of sync with docking.pot
# - msgfmt reports format/syntax errors
#
# Strict mode is enabled by default:
# - fails on untranslated/fuzzy entries
# - set I18N_REQUIRE_COMPLETE=0 to temporarily allow partial catalogs
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCALE_DIR="${ROOT_DIR}/docking/locale"
POT_FILE="${LOCALE_DIR}/docking.pot"

for cmd in msgfmt msgcmp; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[i18n] ${cmd} is required (install gettext)." >&2
    exit 1
  fi
done

if [ ! -f "${POT_FILE}" ]; then
  echo "[i18n] Missing template file: ${POT_FILE}" >&2
  exit 1
fi

mapfile -d '' PO_FILES < <(find "${LOCALE_DIR}" -type f -name 'docking.po' -print0 | sort -z)
if [ "${#PO_FILES[@]}" -eq 0 ]; then
  echo "[i18n] No docking.po files found under ${LOCALE_DIR}" >&2
  exit 1
fi

status=0
require_complete="${I18N_REQUIRE_COMPLETE:-1}"
for po_file in "${PO_FILES[@]}"; do
  rel="${po_file#${ROOT_DIR}/}"

  if ! msgcmp --use-untranslated --use-fuzzy "${po_file}" "${POT_FILE}" >/tmp/docking-msgcmp.log 2>&1; then
    echo "[i18n] ${rel} is out of sync with docking.pot" >&2
    sed -n '1,6p' /tmp/docking-msgcmp.log >&2
    status=1
  fi

  if ! stats="$(msgfmt --check-format --statistics -o /dev/null "${po_file}" 2>&1)"; then
    echo "[i18n] ${rel} has msgfmt errors:" >&2
    printf '%s\n' "${stats}" >&2
    status=1
    continue
  fi

  untranslated="$(
    printf '%s\n' "${stats}" \
      | grep -oE '[0-9]+ untranslated message(s)?' \
      | awk '{print $1}' \
      | head -n1 || true
  )"
  fuzzy="$(
    printf '%s\n' "${stats}" \
      | grep -oE '[0-9]+ fuzzy translation(s)?' \
      | awk '{print $1}' \
      | head -n1 || true
  )"

  untranslated="${untranslated:-0}"
  fuzzy="${fuzzy:-0}"

  if [ "${require_complete}" = "1" ] && { [ "${untranslated}" -gt 0 ] || [ "${fuzzy}" -gt 0 ]; }; then
    echo "[i18n] ${rel} has untranslated/fuzzy entries: untranslated=${untranslated}, fuzzy=${fuzzy}" >&2
    status=1
  fi
done

if [ "${status}" -ne 0 ]; then
  echo "[i18n] Translation catalog check failed." >&2
  exit 1
fi

echo "[i18n] Translation catalog check passed for ${#PO_FILES[@]} catalogs."
