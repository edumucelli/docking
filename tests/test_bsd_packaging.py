"""BSD staging must include runtime data without editable paths or pip tooling."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging/bsd/build.py"
spec = importlib.util.spec_from_file_location("docking_bsd_build", MODULE_PATH)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_staged_runtime_is_self_contained_without_build_tools(monkeypatch, tmp_path):
    root = tmp_path / "source"
    for folder in (
        "docking/assets",
        "packaging/shared",
        "packaging/deb/icons/hicolor/48x48/apps",
    ):
        (root / folder).mkdir(parents=True)
    (root / "docking/__init__.py").write_text('__version__="1.2.3"')
    (root / "docking/assets/fixture.svg").write_text("<svg/>")
    (root / "packaging/shared/org.docking.Docking.desktop").write_text(
        "[Desktop Entry]"
    )
    (
        root / "packaging/deb/icons/hicolor/48x48/apps/org.docking.Docking.png"
    ).write_bytes(b"icon")
    (root / "LICENSE").write_text("GPL")
    venv = tmp_path / "venv"
    site = (
        venv
        / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    site.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin/escape").write_text("escaped")
    for name in ("dependency.py", "pip.py", "__editable__.pth"):
        (site / name).write_text("runtime" if name == "dependency.py" else "build tool")

    def distribution(name, files):
        return SimpleNamespace(
            metadata={"Name": name},
            files=[Path(f) for f in files],
            locate_file=lambda item: site / item,
        )

    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder.sys, "prefix", str(venv))
    monkeypatch.setattr(
        builder.importlib.metadata,
        "distributions",
        lambda **kwargs: [
            distribution("dependency", ["dependency.py", "../../../bin/escape"]),
            distribution("pip", ["pip.py"]),
            distribution("docking", ["__editable__.pth"]),
        ],
    )
    destination = tmp_path / "stage"
    builder.stage(destination)
    private = destination / "usr/local/lib/docking/python"
    assert (private / "dependency.py").read_text() == "runtime"
    assert (private / "docking/assets/fixture.svg").is_file()
    assert not (private / "pip.py").exists()
    assert not (private / "__editable__.pth").exists()
    assert not (destination / "usr/local/bin/escape").exists()
    launcher = destination / "usr/local/bin/docking"
    assert launcher.stat().st_mode & 0o111
    assert "/usr/local/lib/docking/python" in launcher.read_text()
    assert str(venv) not in launcher.read_text()
