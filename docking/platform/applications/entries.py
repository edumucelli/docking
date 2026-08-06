"""Desktop-entry parsing primitives and constants.

Canonical home for XDG desktop-entry constants.  ``GNOME_APP_PREFIX``
lives here alongside ``DESKTOP_SUFFIX`` so the matcher can import both
from one leaf module.
"""

from __future__ import annotations

from docking.platform.desktop_entries import (  # noqa: F401
    DESKTOP_SUFFIX,
    FALLBACK_ICON,
    GENERATED_DESKTOP_PREFIX,
    GENERATED_MARKER_KEY,
    GENERATED_SOURCE_KEY,
    HOST_FILESYSTEM_ROOT,
    HOST_XDG_DATA_DIRS,
    SNAP_XDG_DATA_DIR,
    DesktopAction,
    DesktopAppListing,
    DesktopInfo,
    GeneratedDesktopEntry,
    ResolvedAppInfo,
    ResolvedDesktopLaunch,
    all_desktop_app_listings,
    appimage_path_needing_executable_permission,
    create_desktop_entry_for_executable,
    desktop_dirs,
    desktop_entry_bool,
    desktop_entry_locale_string,
    desktop_entry_string,
    desktop_file_action_exec,
    desktop_file_actions,
    desktop_id_from_uri_or_path,
    desktop_info_from_app_info,
    desktop_info_from_file,
    desktop_listing_from_app_info,
    desktop_listing_from_file,
    desktop_match_aliases,
    executable_path_from_exec_line,
    find_desktop_file,
    generated_desktop_id_for_path,
    is_host_desktop_file,
    load_desktop_key_file,
    local_path_from_uri_or_path,
    make_user_executable,
    normalized_exec_basename,
    resolve_app_info,
    resolve_desktop_launch,
    user_applications_dir,
    wine_executable_aliases,
    wm_class_for_app_info,
)

GNOME_APP_PREFIX = "org.gnome."
