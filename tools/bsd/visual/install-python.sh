#!/bin/sh
# Keep native GI/Cairo/psutil bindings while installing the real runtime deps.
set -eu

python3 -m venv --system-site-packages --without-pip .venv-bsd
python3 -m pip --python .venv-bsd/bin/python install --upgrade pip
PIP_UPLOADED_PRIOR_TO=$(.venv-bsd/bin/python -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat())')
export PIP_UPLOADED_PRIOR_TO
.venv-bsd/bin/python -m pip install -e .
