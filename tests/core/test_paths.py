"""Native path and file-URI boundaries shared by file consumers and Search."""

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
