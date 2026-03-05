#!/bin/bash
# Extract translatable strings from Python source into .pot template.
# Usage: ./tools/i18n-extract.sh
set -euo pipefail
cd "$(dirname "$0")/.."

xgettext \
  --language=Python \
  --keyword=_ --keyword=ngettext:1,2 \
  --from-code=UTF-8 \
  --output=docking/locale/docking.pot \
  --package-name=docking \
  --copyright-holder="Eduardo Mucelli Rezende Oliveira" \
  --msgid-bugs-address="edumucelli@gmail.com" \
  $(find docking -name '*.py' -not -path '*/test*' -not -path '*__pycache__*' | sort)

echo "Extracted $(grep -c '^msgid ' docking/locale/docking.pot) strings to docking/locale/docking.pot"
