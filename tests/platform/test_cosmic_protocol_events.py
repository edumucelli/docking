"""Decode real wire arguments before invoking COSMIC's event callbacks."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pywayland")
from pywayland import ffi
from pywayland.protocol.wayland import WlOutput

from docking.platform.backends.wayland.cosmic import CosmicToplevelAdapter
from docking.platform.backends.wayland.protocols.cosmic_toplevel_info_v1 import (
    ZcosmicToplevelHandleV1,
)
from docking.platform.backends.wayland.protocols.cosmic_workspace_v1 import (
    ZcosmicWorkspaceHandleV1,
)
from docking.platform.backends.wayland.protocols.ext_workspace_v1 import (
    ExtWorkspaceHandleV1,
)


class WireObject:
    """Keep an object alive in PyWayland's weak proxy registry."""


@pytest.mark.parametrize(
    ("event", "interface", "integers"),
    [
        ("output_enter", WlOutput, ()),
        ("output_leave", WlOutput, ()),
        ("geometry", WlOutput, (10, 20, 640, 480)),
        ("workspace_enter", ZcosmicWorkspaceHandleV1, ()),
        ("workspace_leave", ZcosmicWorkspaceHandleV1, ()),
        ("ext_workspace_enter", ExtWorkspaceHandleV1, ()),
        ("ext_workspace_leave", ExtWorkspaceHandleV1, ()),
    ],
)
def test_cosmic_event_preserves_object_argument(event, interface, integers):
    # Exercise the decoder, rather than supplying Python arguments that hide
    # missing protocol metadata. No compositor/display connection is required.
    storage = ffi.new("char[]", 1)
    pointer = ffi.cast("struct wl_proxy *", storage)
    obj = WireObject()
    interface.registry[pointer] = obj
    args = ffi.new("union wl_argument[]", 1 + len(integers))
    args[0].o = ffi.cast("struct wl_object *", pointer)
    for index, value in enumerate(integers, 1):
        args[index].i = value
    message = next(m for m in ZcosmicToplevelHandleV1.events if m.name == event)
    try:
        decoded = message.c_to_arguments(args)
        assert decoded == [obj, *integers]

        if event.startswith("workspace_"):
            return  # Deprecated events are decoded but not used by the adapter.
        proxy = WireObject()
        proxy.dispatcher = {}
        adapter = CosmicToplevelAdapter()
        service = MagicMock()
        adapter._service = service
        adapter._toplevel_info = SimpleNamespace(get_cosmic_toplevel=lambda _: proxy)
        toplevel = WireObject()
        adapter._request_cosmic_info(toplevel)
        proxy.dispatcher[event](proxy, *decoded)
        if event == "output_enter":
            service.output_entered.assert_called_once_with(toplevel, obj)
        elif event == "output_leave":
            service.output_left.assert_called_once_with(toplevel, obj)
        elif event == "geometry":
            assert adapter._pending_data[toplevel]["geometry"] == (obj, *integers)
        elif event == "ext_workspace_enter":
            assert adapter._pending_data[toplevel]["ext_workspace"] is obj
        else:
            adapter._pending_data[toplevel] = {"ext_workspace": obj}
            proxy.dispatcher[event](proxy, *decoded)
            assert "ext_workspace" not in adapter._pending_data[toplevel]
    finally:
        interface.registry.pop(pointer, None)
