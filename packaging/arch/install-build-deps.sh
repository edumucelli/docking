#!/usr/bin/env bash
# Install the Arch build toolchain, hardened against mirror flakiness.
#
# This runs as root inside a throwaway archlinux container (the build-arch and
# build-arch-arm64 jobs) and as the documented local recipe:
#
#   docker run --rm -v "$PWD:/src" -w /src archlinux:latest \
#       bash packaging/arch/install-build-deps.sh
#
# Safe to re-run.  NOTE: it unconditionally removes pacman's lock file, so it
# must only ever be used where it is the sole pacman consumer (an ephemeral,
# single-purpose container).  Do not lift it into a shared-VM context.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../tools/retry.sh
. "${SCRIPT_DIR}/../../tools/retry.sh"

pacman_flags=(--noconfirm)
# --disable-download-timeout is pacman 6+; the ARM job runs an unofficial
# :latest image, so probe rather than assume.  A slow Arch mirror tripping the
# default low-speed timeout is a plausible cause of the ARM job needing three
# attempts to get through this step.
if pacman --help 2>&1 | grep -q -- --disable-download-timeout; then
    pacman_flags+=(--disable-download-timeout)
fi

do_install() {
    # A previous attempt killed mid-transaction leaves a stale lock file, and
    # every later pacman call then fails with "unable to lock database".  That
    # turns a transient mirror error into a permanent one, which is exactly the
    # shape of a job that fails attempt 1 *and* attempt 2.  Nothing else can be
    # running pacman inside this container.
    rm -f /var/lib/pacman/db.lck

    # Retried as one unit: this order is load-bearing (-Sy refreshes the
    # keyring, -Syu then upgrades against it), and a half-applied upgrade only
    # converges if the whole sequence reruns.
    pacman -Sy "${pacman_flags[@]}" archlinux-keyring
    pacman -Syu "${pacman_flags[@]}"
    pacman -S "${pacman_flags[@]}" --needed base-devel git python python-pip gettext
}

# Re-exec so the retried unit is a real executable rather than a shell function:
# timeout(1) cannot wrap a function, so this is what lets one hung pacman
# download be bounded instead of stalling the job.
if [ "${1:-}" = --do-install ]; then
    do_install
    exit 0
fi

retry -a 3 -d 15 -m 60 -l pacman -- timeout 600 bash "$0" --do-install
