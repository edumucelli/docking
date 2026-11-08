"""Install Docking's pinned Microsoft WebView2 loader; the Runtime is separate.

The official SDK archive is verified before extracting just the x64 loader and
its redistribution license. No dependency is downloaded by the running applet.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.0.4314.35"
SHA256 = "90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4"
URL = (
    "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/"
    + VERSION
    + "/microsoft.web.webview2."
    + VERSION
    + ".nupkg"
)


def install(archive: bytes, destination: Path) -> None:
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise ValueError("WebView2 SDK archive does not match its pinned checksum")
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        loader = package.read("build/native/x64/WebView2Loader.dll")
        license_text = package.read("LICENSE.txt")
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in (("WebView2Loader.dll", loader), ("LICENSE.txt", license_text)):
        pending = destination / (name + ".tmp")
        pending.write_bytes(data)
        pending.replace(destination / name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package", type=Path, help="Use a previously downloaded SDK archive"
    )
    parser.add_argument(
        "--output", type=Path, help="Override the per-user loader directory"
    )
    args = parser.parse_args()
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        parser.error("Requires the Windows x64 Python build")
    # Avoid importing GTK or application modules into the installer.
    data = Path(
        os.environ.get("XDG_DATA_HOME")
        or Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        / "Data"
    )
    destination = (
        args.output
        if args.output is not None
        else data / "docking" / "runtimes" / "webview2"
    )
    if args.package is not None:
        archive = args.package.read_bytes()
    else:
        with urllib.request.urlopen(URL, timeout=30) as response:
            archive = response.read(20 * 1024 * 1024 + 1)
    install(archive, destination)
    print(
        f"Installed Microsoft WebView2 loader {VERSION}; "
        "the WebView2 Runtime must also be installed."
    )


if __name__ == "__main__":
    main()
