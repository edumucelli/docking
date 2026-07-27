#!/usr/bin/env python3
"""Launch Docking directly from a source checkout.

This small development entry point mirrors the installed ``docking`` command,
letting contributors run ``python run.py`` without installing a console script.
Application startup remains in :mod:`docking.app` so both launch paths behave
the same way.
"""

from docking.app import main

# Keep this wrapper free of startup logic; it should only delegate to the
# canonical application entry point.
if __name__ == "__main__":
    main()
