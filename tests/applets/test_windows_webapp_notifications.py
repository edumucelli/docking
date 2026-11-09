"""Native toast races never call released browser notification interfaces."""

import ctypes
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree

import pytest

from docking.applets.webapp.implementations.windows import notifications as mod
from docking.platform.native.windows.toasts import toast_xml
from tests.applets.deferred_worker import DeferredWorker


@pytest.fixture
def notifier(monkeypatch, tmp_path):
    queue = DeferredWorker()
    worker = SimpleNamespace(run=queue.worker.run, stop=Mock())
    monkeypatch.setattr(mod, "SerialWorker", lambda **_kwargs: worker)
    publisher = Mock()
    monkeypatch.setattr(mod, "ToastPublisher", lambda *_args: publisher)
    native_calls, released = [], []
    monkeypatch.setattr(
        mod,
        "method",
        lambda _pointer, index, *_args: (
            lambda *_values: native_calls.append(index) or 0
        ),
    )
    activate, shown = Mock(), Mock()
    instance = mod.DesktopNotifier(
        service_name="Generated",
        profile_directory=tmp_path,
        on_activate=activate,
        on_shown=shown,
    )
    scope = ExitStack()
    scope.callback(released.append, "released")
    instance._notifications["owned"] = mod._Notification(
        scope, ctypes.c_void_p(41), "tag"
    )
    yield instance, queue, publisher, native_calls, released, activate, shown, worker
    instance.stop()
    queue.drain()


def test_stopped_publisher_reply_cannot_touch_released_browser(notifier):
    instance, queue, publisher, calls, released, _activate, shown, worker = notifier
    instance.stop()
    instance._published("owned", True)
    assert released == ["released"] and calls == [7]
    shown.assert_not_called()
    queue.drain()
    publisher.hide.assert_called_once_with("owned")
    worker.stop.assert_called_once()


def test_duplicate_com_and_winrt_activation_only_click_once(notifier):
    instance, queue, publisher, calls, released, activate, _shown, _worker = notifier
    instance._event("clicked", "owned")
    instance._event("clicked", "owned")
    assert calls == [6] and released == []
    activate.assert_called_once()
    queue.drain()
    assert calls == [6, 7] and released == ["released"]
    publisher.hide.assert_called_once_with("owned")


def test_web_close_does_not_report_a_second_dom_close(notifier):
    instance, queue, publisher, calls, released, activate, shown, _worker = notifier
    instance._event("web-closed", "owned")
    instance._published("owned", True)
    assert calls == [] and released == []
    activate.assert_not_called()
    shown.assert_not_called()
    queue.drain()
    assert released == ["released"]
    publisher.hide.assert_called_once_with("owned")


def test_rejected_toast_closes_dom_and_does_not_count_activity(notifier):
    instance, queue, _publisher, calls, _released, _activate, shown, _worker = notifier
    instance._published("owned", False)
    queue.drain()
    assert calls == [7]
    shown.assert_not_called()


def test_xml_keeps_untrusted_notification_text_as_text():
    root = ElementTree.fromstring(
        toast_xml('owned"token', '<>&"', "café\0\ud800<script/>", True)
    )
    assert root.attrib == {"launch": 'owned"token'}
    assert [node.text for node in root.findall(".//text")] == ['<>&"', "café<script/>"]
    assert root.find("audio").attrib == {"silent": "true"}
    assert root.find(".//script") is None


def test_xml_bounds_native_notification_text():
    root = ElementTree.fromstring(toast_xml("owned", "x" * 10000, "y" * 20000, False))
    assert [len(node.text) for node in root.findall(".//text")] == [256, 2048]
    assert root.find("audio") is None


def test_native_identity_is_stable_and_separate_for_each_profile():
    from docking.platform.native.windows.toasts import ToastPublisher

    one = ToastPublisher("Generated", r"C:\Owned\One\webview2", lambda *_args: None)
    restarted = ToastPublisher(
        "Generated", r"c:\owned\one\WEBVIEW2", lambda *_args: None
    )
    separate = ToastPublisher(
        "Generated", r"C:\Owned\Two\webview2", lambda *_args: None
    )
    assert one.identity == restarted.identity != separate.identity
    assert bytes(one._class_id) == bytes(restarted._class_id)


@pytest.mark.parametrize("hide_failed", [False, True])
def test_native_removal_clears_only_its_exclusive_identity_after_last_toast(
    monkeypatch, hide_failed
):
    from contextlib import contextmanager

    from docking.platform.native.windows import toasts

    @contextmanager
    def string(text):
        yield text

    calls, released = [], []
    publisher = toasts.ToastPublisher("Generated", "owned-profile", lambda *_args: None)
    publisher._runtime = SimpleNamespace(string=string)
    publisher._history = ctypes.c_void_p(70)
    for token in ("one", "two"):
        scope = ExitStack()
        scope.callback(released.append, token)
        publisher._toasts[token] = (scope, ctypes.c_void_p(80), "native-" + token)

    def method(_pointer, index, *_args):
        def invoke(_this, *values):
            calls.append((index, values))
            if index == 7 and hide_failed:
                raise OSError("Banner is unavailable")
            return 0

        return invoke

    monkeypatch.setattr(toasts, "method", method)
    with pytest.raises(OSError) if hide_failed else nullcontext():
        publisher.hide("one")
    assert [index for index, _values in calls] == [7, 8]
    assert calls[0][1][0].value == 80
    assert calls[1] == (8, ("native-one", "Docking.WebApp", publisher.identity))
    assert released == ["one"] and "two" in publisher._toasts
    with pytest.raises(OSError) if hide_failed else nullcontext():
        publisher.hide("two")
    assert [index for index, _values in calls] == [7, 8, 7, 8, 12]
    assert calls[3:] == [
        (8, ("native-two", "Docking.WebApp", publisher.identity)),
        (12, (publisher.identity,)),
    ]
    assert released == ["one", "two"]


def test_replacement_retains_the_new_native_notification_and_completes_each_deferral(
    notifier, monkeypatch
):
    instance, queue, publisher, _calls, _released, _activate, _shown, _worker = notifier
    instance.clear()
    queue.drain()
    released, shown, completed, methods = [], [], [], []

    def object_result(scope, args, index):
        pointer = ctypes.c_void_p(args.value if index == 4 else args.value + 1000)
        scope.callback(released.append, pointer.value)
        return pointer

    def method(pointer, index, *_types):
        def invoke(*args):
            methods.append((pointer.value, index))
            if index == 3 and pointer.value < 1000:
                args[-1]._obj.value = pointer.value
            elif index == 3:
                completed.append(pointer.value)
            elif index == 5:
                shown.append(pointer.value)
            return 0

        return invoke

    monkeypatch.setattr(mod, "object_result", object_result)
    monkeypatch.setattr(mod, "method", method)
    monkeypatch.setattr(mod, "value", lambda *_args: SimpleNamespace(value=0))
    monkeypatch.setattr(
        mod,
        "ComCallback",
        lambda *_args, **_kwargs: SimpleNamespace(
            pointer=ctypes.c_void_p(999), close=Mock()
        ),
    )
    engine = SimpleNamespace(
        text=lambda _pointer, index, **_kwargs: {
            13: "Generated",
            8: "Owned",
            11: "same-tag",
        }[index],
        put=Mock(),
    )
    publisher.show.return_value = True
    assert instance.show((engine, ctypes.c_void_p(51)))
    queue.drain()
    assert instance.show((engine, ctypes.c_void_p(52)))
    queue.drain()
    assert shown == [51, 52]
    assert (51, 4) not in methods
    assert completed == [1051, 1052]
    assert released.count(1051) == released.count(1052) == 1
    assert [item.pointer.value for item in instance._notifications.values()] == [52]
    assert 51 not in released and 52 not in released
    active = next(iter(instance._notifications.values()))
    assert [item.pointer.value for item in active.superseded] == [51]
    stale = next(iter(instance._notifications))
    for pointer in (53, 54):
        assert instance.show((engine, ctypes.c_void_p(pointer)))
    instance._event("web-closed", stale)
    queue.drain()
    assert shown == [51, 52, 54]
    active = next(iter(instance._notifications.values()))
    assert active.pointer.value == 54
    assert [item.pointer.value for item in active.superseded] == [51, 52, 53]
    instance.clear()
    queue.drain()
    for pointer in (51, 52, 53, 54):
        assert released.count(pointer) == released.count(pointer + 1000) == 1
        assert methods.count((pointer, 4)) == 1


def test_large_replacement_chain_releases_every_scope_without_recursive_cleanup():
    released = []
    notifications = []
    for index in range(1500):
        scope = ExitStack()
        scope.callback(released.append, index)
        notifications.append(mod._Notification(scope, ctypes.c_void_p(index), "tag"))
    active = notifications[-1]
    active.superseded = notifications[:-1]
    active.close()
    assert len(released) == len(set(released)) == 1500
    assert not active.superseded
