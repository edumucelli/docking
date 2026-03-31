#!/usr/bin/env bash
# Docking Translation Guide + Unified i18n Tool
#
# -----------------------------------------------------------------------------
# 1) Translation model in gettext: what each file is for
# -----------------------------------------------------------------------------
# Source code marks user-visible strings with _() / ngettext().
#
# POT file (template):
#   - Path: docking/locale/docking.pot
#   - Role: canonical list of translatable msgid strings extracted from source.
#   - Contains no language translations, only source strings and metadata.
#
# PO files (per language catalogs):
#   - Path pattern: docking/locale/<lang>/LC_MESSAGES/docking.po
#   - Role: translation catalogs where msgid (source) maps to msgstr (localized).
#   - May include fuzzy entries (needs review) and untranslated entries.
#
# MO files (compiled runtime catalogs):
#   - Path pattern: docking/locale/<lang>/LC_MESSAGES/docking.mo
#   - Role: binary catalogs used at runtime by gettext for fast lookup.
#   - Generated artifacts from PO files; should not be edited manually.
#
# -----------------------------------------------------------------------------
# 2) Normal workflow in this project
# -----------------------------------------------------------------------------
# A) Developer changes or adds translatable UI strings in Python source.
# B) Regenerate POT from source:
#      ./tools/i18n.sh --extract
# C) Update each PO with msgmerge (translator workflow), then translate new msgids.
# D) Validate catalogs:
#      strict gate:
#        ./tools/i18n.sh --check-catalogs --require-complete
#      backlog-friendly validation:
#        ./tools/i18n.sh --check-catalogs --allow-incomplete
# E) Compile catalogs for runtime/distribution:
#      ./tools/i18n.sh --compile
# F) Optional single-shot local quality command:
#      ./tools/i18n.sh --check-pot-sync --check-catalogs --compile
#
# -----------------------------------------------------------------------------
# 3) How this script integrates with Docking automation
# -----------------------------------------------------------------------------
# Local hooks:
#   - .pre-commit-config.yaml uses:
#       --check-pot-sync
#       --check-catalogs --require-complete
#   - tools/install_precommit_hook.sh installs a strict .git/hooks/pre-commit
#     that calls this script.
#
# CI:
#   - .github/workflows/ci.yml (quality job) uses:
#       --check-pot-sync
#       --check-catalogs --require-complete
#       --compile
#   - This prevents stale templates and incomplete catalogs from landing.
#
# Packaging:
#   - deb/rpm/flatpak/snap/appimage/arch/nix build scripts invoke:
#       --compile
#   - Ensures distributed artifacts include compiled translations.
#
# -----------------------------------------------------------------------------
# 4) Why one script
# -----------------------------------------------------------------------------
# Centralizing i18n operations here avoids drift between multiple small scripts,
# keeps CI/hooks/packaging behavior consistent, and makes maintenance simpler.
#
# Quick examples:
#   ./tools/i18n.sh --extract
#   ./tools/i18n.sh --check-pot-sync
#   ./tools/i18n.sh --check-catalogs --require-complete
#   ./tools/i18n.sh --compile
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCALE_DIR="${ROOT_DIR}/docking/locale"
POT_FILE="${LOCALE_DIR}/docking.pot"

usage() {
  cat <<'EOF'
Docking i18n utility

Operations (you can combine multiple):
  --extract                 Regenerate POT template from Python source.
  --check-pot-sync          Fail if docking.pot is out of date.
  --check-catalogs          Validate all locale PO catalogs.
  --compile                 Compile all PO catalogs into MO binaries.
  --strip-obsolete          Remove obsolete (#~) entries from all PO catalogs.

Options:
  --output PATH             Output file for --extract (default: docking/locale/docking.pot)
  --require-complete        With --check-catalogs, fail on untranslated/fuzzy entries.
  --allow-incomplete        With --check-catalogs, allow untranslated/fuzzy entries.
  -h, --help                Show this help.

Environment:
  I18N_REQUIRE_COMPLETE     Default mode for --check-catalogs when no explicit
                            completeness flag is provided.
                            1 = require complete (default), 0 = allow incomplete.
EOF
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[i18n] ${cmd} is required." >&2
    exit 1
  fi
}

collect_po_files() {
  mapfile -d '' PO_FILES < <(find "${LOCALE_DIR}" -type f -name 'docking.po' -print0 | sort -z)
  if [ "${#PO_FILES[@]}" -eq 0 ]; then
    echo "[i18n] No docking.po files found under ${LOCALE_DIR}" >&2
    exit 1
  fi
}

extract_pot() {
  local output_path="$1"
  require_cmd xgettext
  require_cmd find
  require_cmd grep

  (
    cd "${ROOT_DIR}"
    xgettext \
      --language=Python \
      --keyword=_ \
      --keyword=ngettext:1,2 \
      --from-code=UTF-8 \
      --output="${output_path}" \
      --package-name=docking \
      --copyright-holder="Eduardo Mucelli Rezende Oliveira" \
      --msgid-bugs-address="edumucelli@gmail.com" \
      $(find docking -name '*.py' -not -path '*/test*' -not -path '*__pycache__*' | sort)
  )

  echo "[i18n] Extracted $(grep -c '^msgid ' "${output_path}") strings to ${output_path}"
}

normalize_pot() {
  local src="$1"
  local dst="$2"
  msgcat --sort-output --no-location --no-wrap "${src}" \
    | sed -E 's/^"POT-Creation-Date: .*\\n"$/\"POT-Creation-Date: NORMALIZED\\n\"/' \
    > "${dst}"
}

check_pot_sync() {
  require_cmd msgcat
  require_cmd diff
  require_cmd mktemp
  require_cmd sed
  require_cmd cp

  if [ ! -f "${POT_FILE}" ]; then
    echo "[i18n] Missing template file: ${POT_FILE}" >&2
    exit 1
  fi

  local tmp_dir
  tmp_dir="$(mktemp -d)"

  cp "${POT_FILE}" "${tmp_dir}/current.pot"
  extract_pot "${tmp_dir}/generated.pot" >/dev/null

  normalize_pot "${tmp_dir}/current.pot" "${tmp_dir}/current.norm"
  normalize_pot "${tmp_dir}/generated.pot" "${tmp_dir}/generated.norm"

  if ! diff -u "${tmp_dir}/current.norm" "${tmp_dir}/generated.norm" >"${tmp_dir}/pot.diff"; then
    echo "[i18n] docking/locale/docking.pot is out of date with source strings." >&2
    echo "[i18n] Run: ./tools/i18n.sh --extract" >&2
    sed -n '1,80p' "${tmp_dir}/pot.diff" >&2
    rm -rf "${tmp_dir}"
    exit 1
  fi

  rm -rf "${tmp_dir}"
  echo "[i18n] POT template is in sync with source strings."
}

check_catalogs() {
  local require_complete="$1"
  require_cmd msgfmt
  require_cmd msgcmp
  collect_po_files

  if [ ! -f "${POT_FILE}" ]; then
    echo "[i18n] Missing template file: ${POT_FILE}" >&2
    exit 1
  fi

  local status=0
  local msgcmp_log
  msgcmp_log="$(mktemp)"

  for po_file in "${PO_FILES[@]}"; do
    local rel
    rel="${po_file#${ROOT_DIR}/}"

    if ! msgcmp --use-untranslated --use-fuzzy "${po_file}" "${POT_FILE}" >"${msgcmp_log}" 2>&1; then
      echo "[i18n] ${rel} is out of sync with docking.pot" >&2
      sed -n '1,6p' "${msgcmp_log}" >&2
      status=1
    fi

    local stats
    if ! stats="$(msgfmt --check-format --statistics -o /dev/null "${po_file}" 2>&1)"; then
      echo "[i18n] ${rel} has msgfmt errors:" >&2
      printf '%s\n' "${stats}" >&2
      status=1
      continue
    fi

    local untranslated fuzzy
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
    rm -f "${msgcmp_log}"
    echo "[i18n] Translation catalog check failed." >&2
    exit 1
  fi

  rm -f "${msgcmp_log}"
  echo "[i18n] Translation catalog check passed for ${#PO_FILES[@]} catalogs."
}

strip_obsolete() {
  require_cmd msgattrib
  collect_po_files

  local count=0
  for po_file in "${PO_FILES[@]}"; do
    if grep -q '^#~' "${po_file}"; then
      msgattrib --no-obsolete -o "${po_file}" "${po_file}"
      count=$((count + 1))
    fi
  done

  echo "[i18n] Stripped obsolete entries from ${count} catalogs."
}

compile_catalogs() {
  require_cmd msgfmt
  collect_po_files

  local count=0
  local po_file mo_file
  for po_file in "${PO_FILES[@]}"; do
    mo_file="${po_file%.po}.mo"
    msgfmt --check-format --verbose -o "${mo_file}" "${po_file}" >/dev/null
    count=$((count + 1))
  done

  echo "[i18n] Compiled ${count} translation catalogs."
}

do_extract=0
do_check_pot_sync=0
do_check_catalogs=0
do_strip_obsolete=0
do_compile=0
extract_output="${POT_FILE}"
require_complete="${I18N_REQUIRE_COMPLETE:-1}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --extract)
      do_extract=1
      shift
      ;;
    --check-pot-sync)
      do_check_pot_sync=1
      shift
      ;;
    --check-catalogs)
      do_check_catalogs=1
      shift
      ;;
    --compile)
      do_compile=1
      shift
      ;;
    --strip-obsolete)
      do_strip_obsolete=1
      shift
      ;;
    --output)
      if [ "$#" -lt 2 ]; then
        echo "[i18n] --output requires a path argument." >&2
        exit 1
      fi
      extract_output="$2"
      shift 2
      ;;
    --require-complete)
      require_complete=1
      shift
      ;;
    --allow-incomplete)
      require_complete=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[i18n] Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ "${do_extract}" -eq 0 ] && [ "${do_check_pot_sync}" -eq 0 ] && [ "${do_check_catalogs}" -eq 0 ] && [ "${do_strip_obsolete}" -eq 0 ] && [ "${do_compile}" -eq 0 ]; then
  echo "[i18n] No operation selected." >&2
  usage >&2
  exit 1
fi

if [ "${do_extract}" -eq 0 ] && [ "${extract_output}" != "${POT_FILE}" ]; then
  echo "[i18n] --output can only be used together with --extract." >&2
  exit 1
fi

if [ "${do_extract}" -eq 1 ]; then
  extract_pot "${extract_output}"
fi

if [ "${do_check_pot_sync}" -eq 1 ]; then
  check_pot_sync
fi

if [ "${do_strip_obsolete}" -eq 1 ]; then
  strip_obsolete
fi

if [ "${do_check_catalogs}" -eq 1 ]; then
  check_catalogs "${require_complete}"
fi

if [ "${do_compile}" -eq 1 ]; then
  compile_catalogs
fi
