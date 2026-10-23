"""Choose immutable stable assets and a genuine preceding release for upgrades."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "packaging/cloudsmith/verification_assets.py"
)
spec = importlib.util.spec_from_file_location(
    "docking_apt_verification_assets", MODULE_PATH
)
assert spec and spec.loader
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


@pytest.mark.parametrize("tag", ["v2.13.9-rc1", "2.13.9", "v2.13", "../v2.13.9"])
def test_rejects_nonstable_tags(tag):
    with pytest.raises(ValueError):
        assets.version(tag)


def test_downloads_latest_older_stable_asset(monkeypatch, tmp_path):
    calls = []
    releases = [
        {"tagName": "v2.13.10", "isDraft": False, "isPrerelease": False},
        {"tagName": "v2.13.8", "isDraft": False, "isPrerelease": False},
        {"tagName": "v2.13.9", "isDraft": False, "isPrerelease": False},
        {"tagName": "v2.13.99", "isDraft": True, "isPrerelease": False},
        {"tagName": "v2.13.9-rc1", "isDraft": False, "isPrerelease": True},
    ]

    def gh_json(*args):
        if args[1] == "list":
            return releases
        if args[-1] == "assets":
            return {"assets": [{"name": "docking-2.13.9-linux-aarch64.deb"}]}
        return {"isDraft": False, "isPrerelease": False}

    monkeypatch.setattr(assets, "gh_json", gh_json)
    monkeypatch.setattr(assets, "download", lambda *args: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--release-tag",
            "v2.13.10",
            "--arch",
            "arm64",
            "--directory",
            str(tmp_path),
        ],
    )
    assets.main()
    assert calls == [
        ("v2.13.10", "docking-2.13.10-linux-aarch64.deb", tmp_path / "current"),
        ("v2.13.9", "docking-2.13.9-linux-aarch64.deb", tmp_path / "previous"),
    ]


def test_first_arm64_release_reports_missing_predecessor(monkeypatch, tmp_path, capsys):
    def gh_json(*args):
        if args[1] == "list":
            return [{"tagName": "v2.13.7", "isDraft": False, "isPrerelease": False}]
        if args[-1] == "assets":
            return {"assets": [{"name": "docking-2.13.7-linux-x86_64.deb"}]}
        return {"isDraft": False, "isPrerelease": False}

    calls = []
    monkeypatch.setattr(assets, "gh_json", gh_json)
    monkeypatch.setattr(assets, "download", lambda *args: calls.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--release-tag",
            "v2.13.8",
            "--arch",
            "arm64",
            "--directory",
            str(tmp_path),
        ],
    )
    assets.main()
    assert len(calls) == 1
    assert "fresh install only" in capsys.readouterr().out


def test_missing_predecessor_asset_fails_after_initial_release(monkeypatch, tmp_path):
    def gh_json(*args):
        if args[1] == "list":
            return [{"tagName": "v2.13.8", "isDraft": False, "isPrerelease": False}]
        if args[-1] == "assets":
            return {"assets": []}
        return {"isDraft": False, "isPrerelease": False}

    monkeypatch.setattr(assets, "gh_json", gh_json)
    monkeypatch.setattr(assets, "download", lambda *args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--release-tag",
            "v2.13.9",
            "--arch",
            "arm64",
            "--directory",
            str(tmp_path),
        ],
    )
    with pytest.raises(SystemExit, match="cannot verify upgrade"):
        assets.main()
