"""Static D-Bus contract for Docking's remote item-control surface.

The D-Bus API is intentionally versioned and kept small:

- one well-known bus name for the dock process,
- one object path,
- one versioned interface dedicated to item control,
- only typed methods in v1.

Keeping the XML in a dedicated module makes the contract easy to test and keeps
the service implementation focused on dispatch and lifecycle rather than string
assembly.
"""

from __future__ import annotations

BUS_NAME = "org.docking.Docking"
OBJECT_PATH = "/org/docking/Docking"
ITEMS_INTERFACE = "org.docking.Docking.Items1"
UNKNOWN_METHOD_ERROR = "org.freedesktop.DBus.Error.UnknownMethod"

ITEMS_INTROSPECTION_XML = f"""
<node>
  <interface name="{ITEMS_INTERFACE}">
    <method name="GetCount">
      <arg name="count" type="i" direction="out"/>
    </method>
    <method name="ListPinnedIds">
      <arg name="desktop_ids" type="as" direction="out"/>
    </method>
    <method name="ListTransientIds">
      <arg name="desktop_ids" type="as" direction="out"/>
    </method>
    <method name="Pin">
      <arg name="desktop_id" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="Unpin">
      <arg name="desktop_id" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="Remove">
      <arg name="desktop_id" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="GetHoverAnchor">
      <arg name="desktop_id" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
      <arg name="x" type="i" direction="out"/>
      <arg name="y" type="i" direction="out"/>
      <arg name="position" type="s" direction="out"/>
    </method>
    <signal name="Changed"/>
  </interface>
</node>
""".strip()
