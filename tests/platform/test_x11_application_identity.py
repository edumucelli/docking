"""Regression fixtures for the distinct failures captured in issue #364."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.types import MatchMethod
from docking.platform.backends.diagnostics import IdentityHintStatus, WindowIdentityHint
from docking.platform.backends.x11.impl.window_tracker import WindowMatcher


@pytest.fixture
def identity_fixture(tmp_path):
    user, system, proc = tmp_path / "user", tmp_path / "system", tmp_path / "proc"
    user.mkdir()
    system.mkdir()
    entries = [
        (
            system,
            "io.gitlab.news_flash.NewsFlash.desktop",
            "Newsflash",
            "flatpak run io.gitlab.news_flash.NewsFlash",
            "",
        ),
        (
            system,
            "org.telegram.desktop.desktop",
            "Telegram",
            "flatpak run org.telegram.desktop",
            "StartupWMClass=TelegramDesktop\n",
        ),
        (
            user,
            "org.telegram.desktop.desktop",
            "Telegram Desktop",
            "flatpak run org.telegram.desktop",
            "Hidden=true\nStartupWMClass=TelegramDesktop\n",
        ),
        (
            user,
            "alacarte-made-test.desktop",
            "Telegram Desktop",
            "flatpak run --command=Telegram org.telegram.desktop",
            "",
        ),
        (
            system,
            "app.grayjay.Grayjay.desktop",
            "Grayjay",
            "flatpak run app.grayjay.Grayjay",
            "StartupWMClass=cef\n",
        ),
        (system, "gufw.desktop", "Firewall", "gufw", ""),
    ]
    for directory, desktop_id, name, command, extra in entries:
        (directory / desktop_id).write_text(
            f"[Desktop Entry]\nType=Application\nName={name}\nExec={command}\n{extra}"
        )
    registry = ApplicationRegistry(
        application_source=lambda: (), desktop_directories_source=lambda: (user, system)
    )
    registry._desktop_app_info_for_id = lambda _id: None
    registry._desktop_app_info_from_filename = lambda _path: None
    registry.refresh()
    grayjay = proc / "12" / "root"
    grayjay.mkdir(parents=True)
    (grayjay / ".flatpak-info").write_text("[Application]\nname=app.grayjay.Grayjay\n")
    gufw = proc / "13"
    gufw.mkdir()
    (gufw / "cmdline").write_bytes(
        b"/usr/bin/python3\0/usr/share/gufw/gufw.py\0PRIVATE\0"
    )
    processes = ProcessIdentityService(
        LaunchProvenanceStore(), proc_root=proc, executable_resolver=lambda _pid: None
    )
    return registry, processes


@pytest.mark.parametrize(
    "class_name,instance,pid,app_id,desktop_id,method",
    [
        (
            "news-flash",
            "news-flash",
            10,
            "io.gitlab.news_flash.NewsFlash",
            "io.gitlab.news_flash.NewsFlash.desktop",
            MatchMethod.APPLICATION_ID,
        ),
        (
            "TelegramDesktop",
            "Telegram",
            11,
            "org.telegram.desktop",
            "alacarte-made-test.desktop",
            MatchMethod.APPLICATION_ID,
        ),
        ("", "", 12, "", "app.grayjay.Grayjay.desktop", MatchMethod.SANDBOX_ID),
        ("Gufw.py", "gufw.py", 13, "", "gufw.desktop", MatchMethod.SCRIPT_NAME),
    ],
)
def test_reported_identities_match_known_launchers(
    identity_fixture,
    class_name,
    instance,
    pid,
    app_id,
    desktop_id,
    method,
):
    registry, processes = identity_fixture
    matcher = WindowMatcher(
        application_registry=registry, process_identity_service=processes
    )
    window = SimpleNamespace(
        get_class_group_name=lambda: class_name,
        get_class_instance_name=lambda: instance,
        get_pid=lambda: pid,
    )
    hints = (
        (WindowIdentityHint("_GTK_APPLICATION_ID", IdentityHintStatus.PRESENT, app_id),)
        if app_id
        else ()
    )
    result = matcher.match_result(window, identity_hints=hints)
    assert result.desktop_id == desktop_id
    assert result.evidence.method is method
    assert matcher.last_diagnostic.outcome == "matched"
    assert matcher.last_diagnostic.identity_hints == hints
    assert "PRIVATE" not in repr(matcher.last_diagnostic)
    assert registry.get("org.telegram.desktop.desktop") is None


def test_explicit_id_wins_over_generic_class_and_declined_id_falls_back(
    identity_fixture,
):
    registry, processes = identity_fixture
    matcher = WindowMatcher(
        application_registry=registry, process_identity_service=processes
    )
    window = SimpleNamespace(
        get_class_group_name=lambda: "cef",
        get_class_instance_name=lambda: "cef",
        get_pid=lambda: 10,
    )
    hint = WindowIdentityHint(
        "_KDE_NET_WM_DESKTOP_FILE",
        IdentityHintStatus.PRESENT,
        "io.gitlab.news_flash.NewsFlash",
    )
    assert (
        matcher.match_result(window, identity_hints=(hint,)).desktop_id
        == "io.gitlab.news_flash.NewsFlash.desktop"
    )
    bad = replace(hint, status=IdentityHintStatus.MALFORMED)
    assert (
        matcher.match_result(window, identity_hints=(bad,)).desktop_id
        == "app.grayjay.Grayjay.desktop"
    )
    unknown = replace(hint, value="org.unknown.App")
    assert (
        matcher.match_result(window, identity_hints=(unknown,)).desktop_id
        == "app.grayjay.Grayjay.desktop"
    )


def test_missing_process_metadata_and_window_identity_stay_unmatched(identity_fixture):
    registry, processes = identity_fixture
    matcher = WindowMatcher(
        application_registry=registry, process_identity_service=processes
    )
    blank = SimpleNamespace(
        get_class_group_name=lambda: "",
        get_class_instance_name=lambda: "",
        get_pid=lambda: 99,
    )
    assert matcher.match_result(blank) is None
    gufw = SimpleNamespace(
        get_class_group_name=lambda: "Gufw.py",
        get_class_instance_name=lambda: "gufw.py",
        get_pid=lambda: 99,
    )
    assert matcher.match_result(gufw) is None


def test_hidden_launcher_is_not_revived_and_ambiguous_replacements_are_declined(
    identity_fixture,
):
    registry, processes = identity_fixture
    matcher = WindowMatcher(
        application_registry=registry, process_identity_service=processes
    )
    window = SimpleNamespace(
        get_class_group_name=lambda: "TelegramDesktop",
        get_class_instance_name=lambda: "Telegram",
        get_pid=lambda: 11,
    )
    hint = WindowIdentityHint(
        "_GTK_APPLICATION_ID", IdentityHintStatus.PRESENT, "org.telegram.desktop"
    )
    directory = registry.get("alacarte-made-test.desktop").desktop_file.parent
    replacement = directory / "alacarte-made-test.desktop"
    duplicate = directory / "duplicate.desktop"
    duplicate.write_text(replacement.read_text())
    registry.refresh()
    assert matcher.match_result(window, identity_hints=(hint,)) is None
    duplicate.unlink()
    replacement.unlink()
    registry.refresh()
    assert matcher.match_result(window, identity_hints=(hint,)) is None
    assert registry.get("org.telegram.desktop.desktop") is None


def test_application_id_ending_in_desktop_resolves_canonical_double_suffix(
    identity_fixture,
):
    registry, processes = identity_fixture
    user = registry.get("alacarte-made-test.desktop").desktop_file.parent
    (user / "org.telegram.desktop.desktop").unlink()
    registry.refresh()
    matcher = WindowMatcher(
        application_registry=registry, process_identity_service=processes
    )
    window = SimpleNamespace(
        get_class_group_name=lambda: "",
        get_class_instance_name=lambda: "",
        get_pid=lambda: 11,
    )
    hint = WindowIdentityHint(
        "_KDE_NET_WM_DESKTOP_FILE", IdentityHintStatus.PRESENT, "org.telegram.desktop"
    )
    result = matcher.match_result(window, identity_hints=(hint,))
    assert result.desktop_id == "org.telegram.desktop.desktop"
