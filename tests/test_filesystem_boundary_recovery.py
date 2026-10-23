"""Filesystem bytes must survive config and mount discovery."""

import json
import os
from pathlib import Path

import pytest

from docking.applets.trash import backend as trash_backend
from docking.core.config import Config
from docking.core.items import FOLDER_KIND


def test_legacy_pinned_folder_keeps_non_utf8_filename(tmp_path):
    folder = tmp_path / os.fsdecode(b"folder-\xff")
    folder.mkdir()
    config_file = tmp_path / "dock.json"
    config_file.write_text(json.dumps({"pinned": [folder.as_uri()]}))

    config = Config.load(config_file)

    assert config.pinned[0].kind == FOLDER_KIND
    assert config.pinned[0].target == folder.as_uri()


@pytest.mark.parametrize(
    "name", [os.fsdecode(b"volume-\xff"), "volume-\u00a0disk", "volume-\u2028disk"]
)
def test_mount_discovery_keeps_filesystem_mount_name(tmp_path, monkeypatch, name):
    mount = tmp_path / name
    mount.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_bytes(b"1 0 0:1 / " + os.fsencode(mount) + b" rw - tmpfs none rw\n")
    open_file = Path.open

    def read_mountinfo(path, *args, **kwargs):
        target = mountinfo if path == Path("/proc/self/mountinfo") else path
        return open_file(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read_mountinfo)

    assert trash_backend._mount_points() == (mount,)
