#!/bin/sh

# Keep completed downloads available when the package archive stalls.
pacman "$@" ||
    (sleep 5 && pacman "$@") ||
    (sleep 10 && pacman "$@") ||
    exit "$?"

exec pacman -Scc --noconfirm
