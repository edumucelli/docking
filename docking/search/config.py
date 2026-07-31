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
DEFAULT_GLOBAL_SEARCH_WEB_FALLBACK = True
DEFAULT_GLOBAL_SEARCH_WEB_ENGINE = "duckduckgo"
GLOBAL_SEARCH_WEB_ENGINES = ("duckduckgo", "google", "brave", "bing")


def normalize_search_providers(raw: object) -> list[str]:
    if not isinstance(raw, list | tuple):
        return list(DEFAULT_GLOBAL_SEARCH_PROVIDERS)
    supported = set(DEFAULT_GLOBAL_SEARCH_PROVIDERS)
    normalized: list[str] = []
    for value in raw:
        if isinstance(value, str) and value in supported and value not in normalized:
            normalized.append(value)
    return normalized or list(DEFAULT_GLOBAL_SEARCH_PROVIDERS)


__all__ = [
    "DEFAULT_GLOBAL_SEARCH_ENABLED",
    "DEFAULT_GLOBAL_SEARCH_MAX_RESULTS",
    "DEFAULT_GLOBAL_SEARCH_PROVIDERS",
    "DEFAULT_GLOBAL_SEARCH_SHORTCUT",
    "DEFAULT_GLOBAL_SEARCH_WEB_ENGINE",
    "DEFAULT_GLOBAL_SEARCH_WEB_FALLBACK",
    "GLOBAL_SEARCH_WEB_ENGINES",
    "normalize_search_providers",
]
