#!/usr/bin/env bash
set -euo pipefail
# Serialize LXDE's first-run libfm initialization in the private profile.
for _ in $(seq 200); do
    if xdotool search --onlyvisible --name '^panel$' >/dev/null 2>&1; then
        exec pcmanfm --desktop --profile LXDE
    fi
    sleep 0.1
done
echo 'LXDE panel never mapped before desktop startup' >&2
exit 1
