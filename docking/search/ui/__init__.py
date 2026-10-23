"""GTK presentation and input helpers owned by global search.

This package is the only search layer allowed to construct widgets or decode
images for display. It consumes immutable snapshots and reports user intent
through callbacks. Provider invocation, ranking, and catalog ownership remain
in the controller and toolkit-free layers.
"""
