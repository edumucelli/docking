#!/bin/bash
# Extract translatable strings from Python source into .pot template.
# Usage: ./tools/i18n-extract.sh [output_path]
set -euo pipefail
cd "$(dirname "$0")/.."

OUTPUT_PATH="${1:-docking/locale/docking.pot}"

xgettext \
  --language=Python \
  --keyword=_ --keyword=ngettext:1,2 \
  --from-code=UTF-8 \
  --output="${OUTPUT_PATH}" \
  --package-name=docking \
  --copyright-holder="Eduardo Mucelli Rezende Oliveira" \
  --msgid-bugs-address="edumucelli@gmail.com" \
  $(find docking -name '*.py' -not -path '*/test*' -not -path '*__pycache__*' | sort)

echo "Extracted $(grep -c '^msgid ' "${OUTPUT_PATH}") strings to ${OUTPUT_PATH}"
