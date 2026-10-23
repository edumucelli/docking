#!/bin/sh
# Let the VM action copy evidence back, then fail the job in the host-side gate.
# Returning a failed test's status directly makes vmactions skip its copy-back.
set -u

output=${1:?artifact directory required}
setup_log=$(mktemp /tmp/docking-bsd-install.XXXXXX)
status=0
sh tools/bsd_visual/install-python.sh >"$setup_log" 2>&1 || status=$?
if [ "$status" -eq 0 ]; then
    .venv-bsd/bin/python -m tools.bsd_visual.run --pytest --output "$output" || status=$?
fi
mkdir -p "$output"
cp "$setup_log" "$output/install.log"
rm -f "$setup_log"
printf '%s\n' "$status" >"$output/exit-status"
if [ ! -f "$output/results.json" ]; then
    python3 - "$output" "$status" <<'PY'
import platform
import sys
from pathlib import Path
from tools.bsd_visual.run import report

report(Path(sys.argv[1]), {"os": platform.platform()}, [{
    "case": "runtime-installation", "status": "fail",
    "detail": f"Runtime setup exited with status {sys.argv[2]}; inspect install.log",
}])
PY
fi
exit 0
