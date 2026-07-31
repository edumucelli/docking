"""Static D-Bus contract for Global Search activation."""

SEARCH_OBJECT_PATH = "/org/docking/Docking/Search"
SEARCH_INTERFACE = "org.docking.Docking.Search1"
UNKNOWN_METHOD_ERROR = "org.freedesktop.DBus.Error.UnknownMethod"

SEARCH_INTROSPECTION_XML = f"""
<node>
  <interface name="{SEARCH_INTERFACE}">
    <method name="Show">
      <arg name="initial_query" type="s" direction="in"/>
      <arg name="activation_context" type="a{{sv}}" direction="in"/>
    </method>
    <method name="Hide"/>
    <method name="Toggle">
      <arg name="activation_context" type="a{{sv}}" direction="in"/>
    </method>
  </interface>
</node>
""".strip()

__all__ = [
    "SEARCH_INTERFACE",
    "SEARCH_INTROSPECTION_XML",
    "SEARCH_OBJECT_PATH",
    "UNKNOWN_METHOD_ERROR",
]
