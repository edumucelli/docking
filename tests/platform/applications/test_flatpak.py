"""Launcher targets are parsed without inspecting application arguments."""

import pytest

from docking.platform.applications.flatpak import flatpak_app_id_from_exec


@pytest.mark.parametrize(
    "command,expected",
    [
        ("flatpak run org.telegram.desktop", "org.telegram.desktop"),
        (
            (
                "/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=Telegram "
                "--file-forwarding org.telegram.desktop @@u %U @@"
            ),
            "org.telegram.desktop",
        ),
        (
            "flatpak run --command org.other.Command org.telegram.desktop",
            "org.telegram.desktop",
        ),
        (
            "env FOO=bar /usr/bin/flatpak run --user org.telegram.desktop",
            "org.telegram.desktop",
        ),
        ("flatpak run app/org.telegram.desktop/x86_64/stable", "org.telegram.desktop"),
        (
            "flatpak run org.telegram.desktop//stable --env=IGNORED=value",
            "org.telegram.desktop",
        ),
        ("flatpak run -- org.telegram.desktop", "org.telegram.desktop"),
        ("flatpak run runtime/org.gnome.Platform/x86_64/47", ""),
        ("flatpak run --command org.telegram.desktop", ""),
        ("flatpak run --unknown org.telegram.desktop", ""),
        ("flatpak run --command='bad", ""),
        ("flatpak run not-an-id org.telegram.desktop", ""),
        ("sh -c 'flatpak run org.telegram.desktop'", ""),
        ("flatpak install org.telegram.desktop", ""),
        ("echo flatpak run org.telegram.desktop", ""),
        ("", ""),
    ],
)
def test_flatpak_launcher_target(command, expected):
    assert flatpak_app_id_from_exec(command) == expected
