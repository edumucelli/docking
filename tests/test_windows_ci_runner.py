"""Keep native test-runner cleanup safe for long paths under MSYS2."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from docking import launcher
from tools.windows import ci
from tools.windows.bundle import filesystem_path


@pytest.mark.skipif(
    sys.platform != "win32" or os.name != "nt", reason="Native Windows long-path APIs"
)
@pytest.mark.parametrize("status", [0, 7])
def test_runner_removes_long_path_stage_and_preserves_child_status(
    tmp_path, monkeypatch, status
):
    temporary = tmp_path / "runner-temp"
    temporary.mkdir()
    stages = []
    monkeypatch.setattr(ci.tempfile, "gettempdir", lambda: str(temporary))
    monkeypatch.setattr(launcher, "prepare_display", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["windows-ci", "--stage", "tests", "--output", str(tmp_path / "reports")],
    )

    def run(command, *, cwd):
        assert cwd == Path(ci.__file__).resolve().parents[2]
        base = next(
            value.removeprefix("--basetemp=")
            for value in command
            if value.startswith("--basetemp=")
        )
        # pytest receives an ordinary path; only filesystem operations retain
        # the extended prefix needed by MSYS2's long-path cleanup.
        assert not base.startswith("\\\\?\\")
        assert "/" not in base
        root = filesystem_path(Path(base))
        stage = root.parent
        assert stage.name.startswith("docking-windows-ci-")
        owned = root / "runtime/lib" / ("long-directory/" * 20 + "owned.txt")
        owned.parent.mkdir(parents=True)
        owned.write_bytes(b"owned subprocess fixture")
        assert len(str(owned)) > 260
        assert owned.read_bytes() == b"owned subprocess fixture"
        stages.append(stage)
        return subprocess.CompletedProcess(command, status)

    monkeypatch.setattr(ci.subprocess, "run", run)
    with pytest.raises(SystemExit) as result:
        ci.main()
    assert result.value.code == status
    assert len(stages) == 1
    assert not stages[0].exists()
    assert list(temporary.iterdir()) == []
