#!/bin/sh
# Let the VM action copy evidence back, then fail the job in the host-side gate.
# Returning a failed test's status directly makes vmactions skip its copy-back.
set -u

output=${1:?artifact directory required}
setup_log=$(mktemp /tmp/docking-bsd-install.XXXXXX)
status=0
sh tools/bsd/visual/install-python.sh >"$setup_log" 2>&1 || status=$?
package_log=$(mktemp /tmp/docking-bsd-package.XXXXXX)
if [ "$status" -eq 0 ]; then
    package_os=$(uname -s | tr '[:upper:]' '[:lower:]')
    .venv-bsd/bin/python packaging/bsd/build.py --output "artifacts/bsd-packages/$package_os" >"$package_log" 2>&1 || status=$?
fi
if [ "$status" -eq 0 ]; then
    export DOCKING_BSD_VM_TEST=1
    .venv-bsd/bin/python -m tools.bsd.visual.run --pytest --dock-command /usr/local/bin/docking --output "$output" || status=$?
fi
mkdir -p "$output"
cp "$setup_log" "$output/install.log"
cp "$package_log" "$output/package.log"
rm -f "$setup_log"
rm -f "$package_log"
printf '%s\n' "$status" >"$output/exit-status"
if [ ! -f "$output/results.json" ]; then
    python3 - "$output" "$status" <<'PY'
import platform
import sys
from pathlib import Path
from tools.bsd.visual.run import report

report(Path(sys.argv[1]), {"os": platform.platform()}, [{
    "case": "runtime-installation", "status": "fail",
    "detail": f"Runtime/package setup exited with status {sys.argv[2]}; inspect install.log and package.log",
}])
PY
fi
exit 0
