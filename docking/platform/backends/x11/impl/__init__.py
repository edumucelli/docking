"""Private X11 implementation helpers used by service adapters.

These modules may import X11-specific libraries and expose implementation
objects such as Wnck trackers, Xlib strut helpers, and pointer barriers. Code
outside the X11 backend should use service adapters instead.
"""
