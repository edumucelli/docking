#!/usr/bin/env bash
# Install a local Git pre-commit hook for this repository.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
hook_path="$(git rev-parse --path-format=absolute --git-path hooks/pre-commit)"

if [[ "$hook_path" -ef .githooks/pre-commit ]]; then
  echo "Docking pre-commit hook is already active at $hook_path"
  exit 0
fi

local_hook="$(git rev-parse --path-format=absolute --git-common-dir)/hooks/pre-commit"
if [[ "$hook_path" != "$local_hook" || -L "${hook_path%/*}" || -L "$hook_path" ]]; then
  echo "Refusing to install over a custom pre-commit hook at $hook_path" >&2
  exit 1
fi

mkdir -p "${hook_path%/*}"
cat >"$hook_path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec "$(git rev-parse --show-toplevel)/.githooks/pre-commit" "$@"
EOF
chmod +x "$hook_path"
echo "Installed Docking pre-commit hook at $hook_path"
