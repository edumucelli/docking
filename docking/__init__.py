# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Top-level package metadata for Docking.

What this module is for

This package root is intentionally small. It marks ``docking`` as a Python
package, exposes the release version, and gives readers one sentence about what
kind of application they are looking at.

Why it stays lightweight

The rest of the codebase is split into clearer runtime boundaries:

- ``docking.core`` owns configuration, item data, layout math, and theme data.
- ``docking.platform`` owns desktop-environment and X11-facing integration.
- ``docking.ui`` owns GTK windows, rendering, hover, menus, and interaction.
- ``docking.applets`` owns optional dock-resident tools and widgets.

Keeping the package root light matters for two reasons:

1. importing ``docking`` should not drag GTK or X11 side effects into callers,
2. version lookups from packaging, tests, and external tooling should stay
   simple and reliable.

In other words, this module is a package marker and metadata boundary, not an
alternate application entrypoint.
"""

__version__ = "2.4.1"
