#!/usr/bin/env python3
"""Launch Docking directly from a source checkout.

This small development entry point mirrors the installed ``docking`` command,
letting contributors run ``python run.py`` without installing a console script.
Backend display setup runs in :mod:`docking.launcher` before it imports the
GTK application module, matching the installed ``docking`` command.
"""

from docking.launcher import main

# Keep this wrapper free of startup logic; the shared launcher owns it.
if __name__ == "__main__":
    main()
