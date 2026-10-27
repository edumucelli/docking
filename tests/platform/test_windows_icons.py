"""A native icon arriving asynchronously replaces the cached generic icon."""

from docking.platform import icons
from docking.platform.backends.windows import icons as native_icons
from tests.platform.application_fakes import application


def test_ready_native_icon_replaces_fallback_and_notifies_subscribers(monkeypatch):
    caches = []

    class Cache:
        def __init__(self, on_ready):
            self.on_ready = on_ready
            self.image = None
            self.stopped = False
            caches.append(self)

        def get(self, _info, _size):
            return self.image

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(icons, "is_windows", lambda: True)
    monkeypatch.setattr(native_icons, "WindowsIconCache", Cache)
    loader = icons.IconLoader()
    fallback = object()
    monkeypatch.setattr(loader, "_try_load_icon_without_fallback", lambda **_kw: None)
    monkeypatch.setattr(loader, "_try_load_fallback_icon", lambda **_kw: fallback)
    changes = []
    unsubscribe = loader.subscribe(changes.append)
    app = application("Example.App")
    assert loader.load_desktop_icon(app, 2) is fallback

    cache = caches[0]
    cache.image = native_icons.NativeIcon(bytes((255, 0, 0, 128)) * 4, 2, 2)
    cache.on_ready(app.desktop_id)
    pixbuf = loader.load_desktop_icon(app, 2)
    assert pixbuf.get_width() == pixbuf.get_height() == 2
    assert bytes(pixbuf.get_pixels())[:4] == bytes((255, 0, 0, 128))
    assert changes == [app.desktop_id]
    cache.image = native_icons.NativeIcon(bytes((0, 0, 255, 255)) * 4, 2, 2)
    cache.on_ready(app.desktop_id)
    updated = loader.load_desktop_icon(app, 2)
    assert bytes(updated.get_pixels())[:4] == bytes((0, 0, 255, 255))
    assert updated is not pixbuf
    unsubscribe()
    loader.stop()
    assert cache.stopped


def test_classic_application_artwork_uses_its_native_shell_item(monkeypatch):
    import threading
    from dataclasses import replace

    posted, callbacks, targets = threading.Event(), [], []
    image = native_icons.NativeIcon(bytes((255, 0, 0, 255)) * 4, 2, 2)

    def extract(target, _size):
        targets.append(target)
        return image

    def idle(callback, *args):
        callbacks.append(lambda: callback(*args))
        posted.set()
        return 1

    monkeypatch.setattr(native_icons, "extract_icon", extract)
    monkeypatch.setattr(native_icons.GLib, "idle_add", idle)
    app = replace(
        application("Native.ControlPanel"),
        executable_path=None,
        launch_target=r"shell:AppsFolder\Native.ControlPanel",
    )
    cache = native_icons.WindowsIconCache(lambda _id: None)
    try:
        assert cache.get(app, 2) is None
        assert posted.wait(2)
        assert targets == [app.launch_target]
        callbacks.pop()()
        assert cache.get(app, 2) is image
    finally:
        cache.stop()


def test_changed_shortcut_target_extracts_fresh_artwork_for_the_same_id(monkeypatch):
    import threading
    from dataclasses import replace

    posted, callbacks, targets = threading.Event(), [], []
    red = native_icons.NativeIcon(bytes((255, 0, 0, 255)), 1, 1)
    blue = native_icons.NativeIcon(bytes((0, 0, 255, 255)), 1, 1)

    def extract(target, _size):
        targets.append(target)
        return red if target == "previous.lnk" else blue

    def idle(callback, *args):
        callbacks.append(lambda: callback(*args))
        posted.set()
        return 1

    monkeypatch.setattr(native_icons, "extract_icon", extract)
    monkeypatch.setattr(native_icons.GLib, "idle_add", idle)
    previous = replace(application("Native.App"), launch_target="previous.lnk")
    current = replace(previous, launch_target="current.lnk")
    cache = native_icons.WindowsIconCache(lambda _id: None)
    try:
        assert cache.get(previous, 1) is None
        assert posted.wait(2)
        callbacks.pop()()
        assert cache.get(previous, 1) is red
        posted.clear()
        assert cache.get(current, 1) is None
        assert posted.wait(2)
        callbacks.pop()()
        assert cache.get(current, 1) is blue
        assert targets == ["previous.lnk", "current.lnk"]
        assert cache.get(previous, 1) is red
    finally:
        cache.stop()
