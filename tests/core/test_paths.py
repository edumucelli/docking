"""Native path and file-URI boundaries shared by file consumers and Search."""

import os
from pathlib import Path

import pytest

from docking.core.paths import is_explicit_path, local_path_from_uri_or_path


@pytest.mark.parametrize("target", ["notes.txt", "https://example.com/a", "calculator"])
def test_ordinary_queries_do_not_have_explicit_path_syntax(target):
    assert not is_explicit_path(target)


@pytest.mark.parametrize(
    "target", ["/tmp/a", "~/a", "./a", "../a", "file:/tmp/a", "FILE:///tmp/a"]
)
def test_explicit_path_syntax_does_not_touch_the_filesystem(target):
    assert is_explicit_path(target)


@pytest.mark.parametrize(
    "name", ["report # résumé + 100%.txt", "brackets [1]; draft.txt"]
)
def test_file_uri_round_trip_preserves_literal_filename_characters(tmp_path, name):
    path = tmp_path / name
    assert local_path_from_uri_or_path(path.as_uri()) == path
    assert local_path_from_uri_or_path(str(path)) == path


@pytest.mark.parametrize(
    "target",
    [
        "file:notes.txt",
        "file:../notes.txt",
        "file:%2Fnotes.txt",
        "file://",
        "file://localhost",
        "file:///a%00b",
        "\0bad",
        "https://[broken",
    ],
)
def test_invalid_file_target_is_not_a_path(target):
    assert local_path_from_uri_or_path(target) is None


@pytest.mark.skipif(os.name != "nt", reason="Native Windows drive and UNC paths")
@pytest.mark.parametrize(
    "target",
    [
        r"C:\Users\Test\notes.txt",
        "C:/Users/Test/notes.txt",
        "C:notes.txt",
        r"\\server\share\notes.txt",
        r".\notes.txt",
        r"..\notes.txt",
        r"~\notes.txt",
        r"\Users\Test",
    ],
)
def test_windows_explicit_paths_are_recognized(target):
    assert is_explicit_path(target)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows drive and UNC paths")
@pytest.mark.parametrize(
    "prefix", ["file://", "file://localhost", "FILE://LOCALHOST", "file:"]
)
def test_windows_file_uri_and_drive_path_resolve_identically(tmp_path, prefix):
    path = tmp_path / "report # résumé + 100%.txt"
    uri_path = path.as_uri().removeprefix("file://")
    assert local_path_from_uri_or_path(prefix + uri_path) == path
    assert local_path_from_uri_or_path(str(path)) == path
    assert local_path_from_uri_or_path(path.as_posix()) == path


@pytest.mark.skipif(os.name != "nt", reason="Native Windows drive and UNC paths")
@pytest.mark.parametrize(
    "uri", ["file://server/share/a%20b.txt", "file:////server/share/a%20b.txt"]
)
def test_windows_unc_uri_keeps_the_network_authority(uri):
    path = local_path_from_uri_or_path(uri)
    assert path == Path(r"\\server\share\a b.txt")
    assert path.is_absolute()
    assert path.drive == r"\\server\share"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows drive and UNC paths")
@pytest.mark.parametrize(
    "uri",
    [
        "file:///C:",
        "file:///relative/path",
        "file://server",
        "file://user@server/share/a",
        "file://server:80/share/a",
        "file://%73erver/share/a",
    ],
)
def test_windows_file_uri_cannot_be_drive_relative_or_have_url_credentials(uri):
    assert local_path_from_uri_or_path(uri) is None


@pytest.mark.skipif(os.name != "nt", reason="Native Windows relative path expansion")
def test_windows_relative_and_home_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert (
        local_path_from_uri_or_path(r".\notes.txt").resolve() == tmp_path / "notes.txt"
    )
    assert local_path_from_uri_or_path(r"~\notes.txt") == Path.home() / "notes.txt"
