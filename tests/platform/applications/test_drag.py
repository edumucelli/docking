"""Private application transfers carry IDs, never file paths or commands."""

import pytest

from docking.platform.applications.drag import application_id_from_drag


@pytest.mark.parametrize(
    "identifier",
    ["Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", "windows:ab123", "org.example.Café"],
)
def test_application_id_round_trips_without_uri_interpretation(identifier):
    assert application_id_from_drag(identifier.encode("utf-8")) == identifier


@pytest.mark.parametrize(
    "data",
    [None, b"", b"\xff", b"tool\0other", b" leading", b"trailing\n", b"x" * 4097],
)
def test_malformed_or_oversized_transfer_is_rejected(data):
    assert application_id_from_drag(data) is None
