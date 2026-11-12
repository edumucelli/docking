"""Bootstrap a verified pip before resolving dependencies with a UTC cooldown."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

COOLDOWN_DAYS = 7
PIP_VERSION = "26.3"
PIP_FILENAME = f"pip-{PIP_VERSION}-py3-none-any.whl"
PIP_SHA256 = "f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13"


def cutoff() -> datetime:
    """Reuse the job cutoff, refusing an inherited value that relaxes policy."""
    latest = datetime.now(timezone.utc) - timedelta(days=COOLDOWN_DAYS)
    value = os.environ.get("PIP_UPLOADED_PRIOR_TO")
    if value is None:
        return latest
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result > latest:
        raise ValueError(
            "PIP_UPLOADED_PRIOR_TO must be UTC-aware and at least seven days ago"
        )
    return result.astimezone(timezone.utc)


def validate_wheel(metadata: dict, before: datetime) -> str:
    """Only accept the reviewed wheel, with matching hash and upload age."""
    for wheel in metadata["urls"]:
        if wheel["filename"] != PIP_FILENAME:
            continue
        uploaded = datetime.fromisoformat(
            wheel["upload_time_iso_8601"].replace("Z", "+00:00")
        )
        if uploaded.tzinfo is None or uploaded >= before:
            raise ValueError(
                f"{PIP_FILENAME} is not eligible until "
                f"{uploaded + timedelta(days=COOLDOWN_DAYS)}"
            )
        if wheel["yanked"] or wheel["digests"]["sha256"] != PIP_SHA256:
            raise ValueError(
                "Bootstrap wheel is yanked or its SHA256 differs from the reviewed pin"
            )
        url = wheel["url"]
        if not url.startswith("https://files.pythonhosted.org/packages/"):
            raise ValueError("Bootstrap wheel must come from files.pythonhosted.org")
        return url
    raise ValueError(f"Missing bootstrap wheel {PIP_FILENAME}")


def bootstrap(before: datetime) -> None:
    with urlopen(
        f"https://pypi.org/pypi/pip/{PIP_VERSION}/json", timeout=30
    ) as response:
        url = validate_wheel(json.load(response), before)
    with urlopen(url, timeout=30) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != PIP_SHA256:
        raise ValueError("Downloaded bootstrap wheel failed SHA256 verification")
    with tempfile.TemporaryDirectory() as directory:
        wheel = Path(directory) / PIP_FILENAME
        wheel.write_bytes(payload)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--upgrade",
                str(wheel),
            ],
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("cutoff", "bootstrap"))
    args = parser.parse_args()
    before = cutoff()
    value = before.isoformat(timespec="seconds").replace("+00:00", "Z")
    if args.command == "cutoff":
        print(value)
        return
    os.environ["PIP_UPLOADED_PRIOR_TO"] = value
    bootstrap(before)
    if "GITHUB_ENV" in os.environ:
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as output:
            output.write(f"PIP_UPLOADED_PRIOR_TO={value}\n")


if __name__ == "__main__":
    main()
