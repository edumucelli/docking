"""Stateful catalogs and desktop integrations owned by global search.

Services isolate lifecycle, monitoring, background loading, and platform APIs
from pure recognizers and mostly stateless providers. Catalogs publish
immutable snapshots, while shortcut services translate portal or X11 events
into small callbacks. The controller owns every service instance and is
responsible for starting and stopping it.
"""
