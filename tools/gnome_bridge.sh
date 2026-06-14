#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_DIR="${ROOT_DIR}/docking/platform/backends/gnome/extension"
UUID="docking-bridge@docking.org"
ZIP="${ROOT_DIR}/${UUID}.shell-extension.zip"

usage() {
  cat <<EOF
Usage: tools/gnome_bridge.sh COMMAND

Commands:
  pack      Build a local GNOME Shell extension bundle
  install   Pack and install the extension for the current user
  enable    Enable the installed extension
  status    Show extension status and bridge D-Bus availability
  query     Call the bridge ListWindows method
EOF
}

pack() {
  rm -f "${ZIP}"
  (cd "${ROOT_DIR}" && gnome-extensions pack --force "${EXT_DIR}")
}

install_extension() {
  pack
  gnome-extensions install --force "${ZIP}"
  rm -f "${ZIP}"
}

enable_extension() {
  gnome-extensions enable "${UUID}"
}

status() {
  echo "GNOME Shell: $(gnome-shell --version)"
  echo
  if gnome-extensions info "${UUID}"; then
    true
  else
    echo "Extension is not visible to gnome-extensions yet."
  fi
  echo
  if gdbus call --session \
    --dest org.docking.Docking.GnomeShellBridge \
    --object-path /org/docking/Docking/GnomeShellBridge \
    --method org.docking.Docking.GnomeShellBridge1.ListWorkspaces >/dev/null; then
    echo "Bridge D-Bus API: available"
  else
    echo "Bridge D-Bus API: unavailable"
  fi
}

query() {
  gdbus call --session \
    --dest org.docking.Docking.GnomeShellBridge \
    --object-path /org/docking/Docking/GnomeShellBridge \
    --method org.docking.Docking.GnomeShellBridge1.ListWindows
}

case "${1:-}" in
  pack)
    pack
    ;;
  install)
    install_extension
    ;;
  enable)
    enable_extension
    ;;
  status)
    status
    ;;
  query)
    query
    ;;
  *)
    usage
    exit 2
    ;;
esac

