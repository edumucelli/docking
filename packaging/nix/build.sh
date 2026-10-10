#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_LINK="${ROOT_DIR}/result-nix"
ARTIFACTS_DIR="${ROOT_DIR}/artifacts"

bash "${ROOT_DIR}/tools/i18n.sh" --compile

mkdir -p "${ARTIFACTS_DIR}"
rm -f "${OUT_LINK}"

# download-attempts defaults to 5, but connect-timeout defaults to 0, i.e. no
# limit at all -- so a black-holed connection to cache.nixos.org hangs forever
# and the retry logic never gets a chance to fire.  Passed on the command line
# rather than via NIX_CONFIG so nothing else can clobber it.
nix-build --option connect-timeout 15 \
    "${ROOT_DIR}/packaging/nix/default.nix" -o "${OUT_LINK}"

OUT_PATH="$(readlink -f "${OUT_LINK}")"
if [ -z "${OUT_PATH}" ] || [ ! -d "${OUT_PATH}" ]; then
  echo "Failed to resolve Nix build output path"
  exit 1
fi

tar -C "${OUT_PATH}" -czf "${ARTIFACTS_DIR}/docking-nix-output.tar.gz" .
printf '%s\n' "${OUT_PATH}" > "${ARTIFACTS_DIR}/docking-nix-store-path.txt"

echo "Built Nix package output:"
ls -lh "${ARTIFACTS_DIR}/docking-nix-output.tar.gz" \
  "${ARTIFACTS_DIR}/docking-nix-store-path.txt"
