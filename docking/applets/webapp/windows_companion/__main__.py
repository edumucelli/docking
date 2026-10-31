"""Bootstrap the isolated Python runtime from the installed source location."""

import importlib.util
import sys
from pathlib import Path

# Load this exact Docking package without adding the GTK interpreter's entire
# site-packages directory to a different CPython runtime's dependency search.
package = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "docking", package / "__init__.py", submodule_search_locations=[str(package)]
)
if spec is None or spec.loader is None:
    raise RuntimeError("Browser package is unavailable")
module = importlib.util.module_from_spec(spec)
sys.modules["docking"] = module
spec.loader.exec_module(module)

from docking.applets.webapp.windows_companion.runtime import main

if __name__ == "__main__":
    main()
