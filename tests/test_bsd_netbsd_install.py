"""Exercise X11 download verification without extracting into the host system."""

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or Path("/usr/X11R7/bin/Xvfb").exists()
    or Path("/usr/pkg/bin").exists(),
    reason="isolates the NetBSD installer on Linux without pkgsrc/X11R7 paths",
)


@pytest.mark.parametrize("manifest", ["valid", "missing", "mismatch"])
def test_x11_sets_are_verified_before_any_extraction(tmp_path, manifest):
    script = tmp_path / "install-netbsd.sh"
    shutil.copy2(ROOT / "tools/bsd/visual/install-netbsd.sh", script)
    commands = tmp_path / "bin"
    commands.mkdir()
    receipt = tmp_path / "extracted"

    def command(name, source):
        path = commands / name
        path.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(sys.executable)
            + " -c "
            + shlex.quote(source)
            + ' "$@"\n'
        )
        path.chmod(0o755)

    command(
        "uname",
        "import sys; print({'-s':'NetBSD','-r':'11.0','-m':'amd64'}[sys.argv[1]])",
    )
    command(
        "ftp",
        "import sys; from pathlib import Path; Path(sys.argv[2]).write_bytes(b'archive')",
    )
    command(
        "cksum",
        "import sys,hashlib; from pathlib import Path; print(hashlib.sha512(Path(sys.argv[-1]).read_bytes()).hexdigest())",
    )
    command(
        "tar",
        "import os; from pathlib import Path; p=Path(os.environ['INSTALL_RECEIPT']); p.write_text(p.read_text()+'extract\\n' if p.exists() else 'extract\\n')",
    )
    command("pkg_add", "pass")
    if manifest != "missing":
        directory = tmp_path / "checksums"
        directory.mkdir()
        digest = hashlib.sha512(
            b"archive" if manifest == "valid" else b"tampered"
        ).hexdigest()
        (directory / "netbsd-11.0-amd64.sha512").write_text(
            "".join(
                f"SHA512 ({name}.tar.xz) = {digest}\n"
                for name in ("xbase", "xetc", "xfont", "xserver")
            )
        )
    result = subprocess.run(
        ["sh", str(script)],
        env={
            **os.environ,
            "PATH": str(commands) + os.pathsep + os.environ["PATH"],
            "INSTALL_RECEIPT": str(receipt),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    if manifest == "valid":
        assert result.returncode == 0, result.stderr
        assert receipt.read_text().splitlines() == ["extract"] * 4
    else:
        assert result.returncode != 0
        assert not receipt.exists()
        assert (
            "SHA512 verification failed" in result.stderr
            or "No trusted X11 checksums" in result.stderr
        )
