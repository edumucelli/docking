from __future__ import annotations

import sys

import pytest

import docking.scaffold as scaffold


def test_to_class_name_and_display_helpers():
    assert scaffold._to_class_name("myapplet") == "MyappletApplet"
    assert scaffold._to_display("my_applet") == "My Applet"


def test_main_creates_applet_package_and_test(tmp_path, monkeypatch, capsys):
    applets_dir = tmp_path / "docking" / "applets"
    tests_dir = tmp_path / "tests" / "applets"
    applets_dir.mkdir(parents=True)
    monkeypatch.setattr(scaffold, "_ROOT", tmp_path / "docking")
    monkeypatch.setattr(scaffold, "_APPLETS_DIR", applets_dir)
    monkeypatch.setattr(scaffold, "_TESTS_DIR", tests_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        ["python", "weather_note", "--category", "WELLNESS"],
    )

    scaffold.main()

    pkg_dir = applets_dir / "weathernote"
    assert pkg_dir.is_dir()
    assert (pkg_dir / "__init__.py").read_text() == scaffold._INIT_PY.format(
        aid="weathernote",
        display="Weathernote",
        class_name="WeathernoteApplet",
        state_class="WeathernoteState",
        category="WELLNESS",
        icon_name="application-x-executable",
    )
    assert (pkg_dir / "state.py").read_text() == scaffold._STATE_PY.format(
        aid="weathernote",
        display="Weathernote",
        class_name="WeathernoteApplet",
        state_class="WeathernoteState",
        category="WELLNESS",
        icon_name="application-x-executable",
    )
    assert (pkg_dir / "render.py").read_text() == scaffold._RENDER_PY.format(
        aid="weathernote",
        display="Weathernote",
        class_name="WeathernoteApplet",
        state_class="WeathernoteState",
        category="WELLNESS",
        icon_name="application-x-executable",
    )
    assert (pkg_dir / "applet.py").read_text() == scaffold._APPLET_PY.format(
        aid="weathernote",
        display="Weathernote",
        class_name="WeathernoteApplet",
        state_class="WeathernoteState",
        category="WELLNESS",
        icon_name="application-x-executable",
    )
    assert (tests_dir / "test_weathernote.py").read_text() == scaffold._TEST_PY.format(
        aid="weathernote",
        display="Weathernote",
        class_name="WeathernoteApplet",
        state_class="WeathernoteState",
        category="WELLNESS",
        icon_name="application-x-executable",
    )

    out = capsys.readouterr().out
    assert "Created applet 'weathernote'" in out
    assert "python -m pytest tests/applets/test_weathernote.py -v" in out


def test_main_exits_when_target_package_already_exists(tmp_path, monkeypatch, capsys):
    applets_dir = tmp_path / "docking" / "applets"
    tests_dir = tmp_path / "tests" / "applets"
    existing_pkg = applets_dir / "myapplet"
    existing_pkg.mkdir(parents=True)
    monkeypatch.setattr(scaffold, "_ROOT", tmp_path / "docking")
    monkeypatch.setattr(scaffold, "_APPLETS_DIR", applets_dir)
    monkeypatch.setattr(scaffold, "_TESTS_DIR", tests_dir)
    monkeypatch.setattr(sys, "argv", ["python", "my_applet"])

    with pytest.raises(SystemExit) as excinfo:
        scaffold.main()

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "already exists" in err
    assert not (tests_dir / "test_myapplet.py").exists()


@pytest.mark.parametrize(
    "name",
    [
        "my applet",  # space would land in an import path
        "2fa",  # a leading digit is not an identifier
        "class",  # a keyword cannot be a package name
        "my.applet",  # a dot would be read as package separation
        "",
    ],
)
def test_main_rejects_names_that_are_not_importable(
    name, tmp_path, monkeypatch, capsys
):
    """A name the generator cannot turn into valid Python must fail up front.

    Every generated file embeds the id in an import path and a class name, so
    an unusable id used to write out modules that raise SyntaxError on import
    (and a test module that breaks collection for the whole suite).
    """
    applets_dir = tmp_path / "docking" / "applets"
    applets_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests" / "applets"
    monkeypatch.setattr(scaffold, "_ROOT", tmp_path / "docking")
    monkeypatch.setattr(scaffold, "_APPLETS_DIR", applets_dir)
    monkeypatch.setattr(scaffold, "_TESTS_DIR", tests_dir)
    monkeypatch.setattr(sys, "argv", ["python", name])

    with pytest.raises(SystemExit) as excinfo:
        scaffold.main()

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "not a usable applet id" in err
    # Nothing may be written: a half-created package is worse than no package.
    assert list(applets_dir.iterdir()) == []
    assert not tests_dir.exists()


@pytest.mark.parametrize("name", ["my_applet", "my-applet", "MyApplet"])
def test_main_accepts_names_that_normalize_to_an_identifier(
    name, tmp_path, monkeypatch
):
    applets_dir = tmp_path / "docking" / "applets"
    applets_dir.mkdir(parents=True)
    monkeypatch.setattr(scaffold, "_ROOT", tmp_path / "docking")
    monkeypatch.setattr(scaffold, "_APPLETS_DIR", applets_dir)
    monkeypatch.setattr(scaffold, "_TESTS_DIR", tmp_path / "tests" / "applets")
    monkeypatch.setattr(sys, "argv", ["python", name])

    scaffold.main()

    pkg_dir = applets_dir / "myapplet"
    assert pkg_dir.is_dir()
    # The whole point of the guard: everything written must be importable.
    for generated in pkg_dir.glob("*.py"):
        compile(generated.read_text(), str(generated), "exec")
