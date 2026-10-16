"""Exercise actual adapter request paths without requiring a compositor."""

from unittest.mock import MagicMock

import pytest

from docking.platform.backends.wayland.runtime import (
    HyprlandPreviewProtocolAdapter,
    PhocPreviewProtocolAdapter,
    PreviewProtocolAdapter,
)


@pytest.mark.parametrize(
    "adapter_type,method,args,owner,request_name,request_args",
    [
        (
            PreviewProtocolAdapter,
            "create_source",
            ("handle",),
            "_source_manager",
            "create_source",
            ("handle",),
        ),
        (
            PreviewProtocolAdapter,
            "create_session",
            ("source",),
            "_capture_manager",
            "create_session",
            ("source", 0),
        ),
        (
            PreviewProtocolAdapter,
            "create_shm_pool",
            (3, 256),
            "_shm",
            "create_pool",
            (3, 256),
        ),
        (
            HyprlandPreviewProtocolAdapter,
            "create_frame",
            ("handle",),
            "_manager",
            "capture_toplevel_with_wlr_toplevel_handle",
            (0, "handle"),
        ),
        (
            HyprlandPreviewProtocolAdapter,
            "create_shm_pool",
            (3, 256),
            "_shm",
            "create_pool",
            (3, 256),
        ),
        (
            PhocPreviewProtocolAdapter,
            "create_frame",
            ("handle", 240, 160),
            "_manager",
            "get_thumbnail",
            ("handle", 240, 160),
        ),
        (
            PhocPreviewProtocolAdapter,
            "create_shm_pool",
            (3, 256),
            "_shm",
            "create_pool",
            (3, 256),
        ),
    ],
)
def test_bound_preview_adapter_returns_protocol_resource(
    adapter_type, method, args, owner, request_name, request_args
):
    adapter = adapter_type()
    adapter.available = True
    for name in ("_source_manager", "_capture_manager", "_manager", "_shm"):
        setattr(adapter, name, MagicMock())
    proxy = getattr(adapter, owner)

    resource = getattr(adapter, method)(*args)

    getattr(proxy, request_name).assert_called_once_with(*request_args)
    assert resource is getattr(proxy, request_name).return_value
