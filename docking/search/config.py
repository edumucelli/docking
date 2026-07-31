"""Defaults and normalization owned by Global Search."""

DEFAULT_GLOBAL_SEARCH_ENABLED = True
DEFAULT_GLOBAL_SEARCH_SHORTCUT = "CTRL+LOGO+space"
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
__all__ = [
    "DEFAULT_GLOBAL_SEARCH_ENABLED",
    "DEFAULT_GLOBAL_SEARCH_MAX_RESULTS",
    "DEFAULT_GLOBAL_SEARCH_PROVIDERS",
    "DEFAULT_GLOBAL_SEARCH_SHORTCUT",
    "DEFAULT_GLOBAL_SEARCH_WEB_ENGINE",
    "GLOBAL_SEARCH_WEB_ENGINES",
]
