"""Canonical application IDs transferred between Docking widgets."""

APPLICATION_DRAG_MIME = "application/x-docking-application-id"
APPLICATION_DRAG_INFO = 2


def application_id_from_drag(data: bytes | None) -> str | None:
    """Decode one bounded ID; the destination must also resolve its registry."""
    if not data or len(data) > 4096:
        return None
    try:
        identifier = data.decode("utf-8")
    except UnicodeError:
        return None
    if not identifier or "\0" in identifier or identifier.strip() != identifier:
        return None
    return identifier
