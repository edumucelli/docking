"""Unix XEmbed tray presentation."""

from docking.applets.systemtray.presentation import TrayPresentation

from .xembed import XEmbedTrayHost


class UnixTrayPresentation(XEmbedTrayHost, TrayPresentation):
    """Preserve XEmbed ownership and popup positioning."""
