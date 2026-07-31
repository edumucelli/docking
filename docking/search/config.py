"""Built-in policy values owned exclusively by global search.

These constants keep search defaults and fixed product policy close to the
implementation. Enabled state, shortcut, and web engine seed the remaining
supported preferences. Provider availability and result count are internal
policy rather than configuration, which avoids expanding Docking's main model
for values users should not need to tune. The ordinary provider tuple excludes
recognizer-only utilities and web search: intent routing selects utility
providers for structured queries and the controller appends web search as a
bounded fallback.
"""

DEFAULT_GLOBAL_SEARCH_ENABLED = True
DEFAULT_GLOBAL_SEARCH_SHORTCUT = "CTRL+ALT+space"
DEFAULT_GLOBAL_SEARCH_PROVIDERS: tuple[str, ...] = (
    "applications",
    "dock",
    "windows",
    "calculator",
    "recent-files",
    "path",
)
DEFAULT_GLOBAL_SEARCH_MAX_RESULTS = 12
DEFAULT_GLOBAL_SEARCH_WEB_ENGINE = "duckduckgo"
GLOBAL_SEARCH_WEB_ENGINES = ("duckduckgo", "google", "brave", "bing")

# Export the small policy surface explicitly. This makes accidental coupling
# from unrelated Docking packages visible during review.
__all__ = [
    "DEFAULT_GLOBAL_SEARCH_ENABLED",
    "DEFAULT_GLOBAL_SEARCH_MAX_RESULTS",
    "DEFAULT_GLOBAL_SEARCH_PROVIDERS",
    "DEFAULT_GLOBAL_SEARCH_SHORTCUT",
    "DEFAULT_GLOBAL_SEARCH_WEB_ENGINE",
    "GLOBAL_SEARCH_WEB_ENGINES",
]
