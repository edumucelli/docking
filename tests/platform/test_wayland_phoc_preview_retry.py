"""A phoc thumbnail request that is never answered must not wedge the preview.

phoc can drop a request without ever sending `failed`. The service issues one
request per (window, size) key and only a terminal event clears it, so without
an expiry that key stays pending for the session and the preview never arrives
however long a caller waits — which is what the Wayland (phoc) CI probe hit.
"""

from __future__ import annotations

from docking.platform.backends.base import DisplayServer, WindowId
from docking.platform.backends.wayland import previews

WINDOW_ID = WindowId(DisplayServer.WAYLAND, 1)
KEY = (WINDOW_ID, 240, 160)

# Far past any plausible request budget, so this also fails loudly against a
# build that has no expiry at all rather than passing by luck.
_SILENCE_S = 60.0


class _FakeFrame:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}
        self.destroyed = 0

    def destroy(self) -> None:
        self.destroyed += 1


class _FakeProtocol:
    def __init__(self) -> None:
        self.frames: list[_FakeFrame] = []

    def create_frame(self, handle, width, height):
        frame = _FakeFrame()
        self.frames.append(frame)
        return frame

    def flush(self) -> None:
        pass


class _FakeWindows:
    def protocol_handle_for_window_id(self, window_id):
        return object()


def _service():
    protocol = _FakeProtocol()
    service = previews.PhocPreviewService(protocol=protocol, windows=_FakeWindows())
    return protocol, service


def _capture(service):
    return service.capture(WINDOW_ID, width=240, height=160)


def test_unanswered_request_is_retried_after_prolonged_silence():
    protocol, service = _service()

    assert _capture(service) is None
    assert len(protocol.frames) == 1

    # Still waiting on the first request: polling must not pile on more.
    service._pending[KEY].progress_at -= 0.1
    assert _capture(service) is None
    assert len(protocol.frames) == 1

    # phoc never answered and nothing timed it out, so the key would stay
    # pending for the session. A later poll has to try again.
    service._pending[KEY].progress_at -= _SILENCE_S
    assert _capture(service) is None

    assert len(protocol.frames) == 2, (
        "a request phoc never answered must be retried, not waited on forever"
    )
    assert protocol.frames[0].destroyed == 1


def test_progress_from_the_compositor_defers_expiry():
    protocol, service = _service()

    assert _capture(service) is None
    request = service._pending[KEY]

    # phoc went quiet for a long time, but then answered. A request that is
    # still making progress may be mid-copy, so it must not be expired.
    request.progress_at -= _SILENCE_S
    service._on_flags(request, 0)

    assert _capture(service) is None
    assert len(protocol.frames) == 1, "a capture in progress must not be destroyed"
