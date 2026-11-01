"""Launch Docking after backend-specific display setup."""

from __future__ import annotations


def prepare_display() -> bool:
    """Prepare GTK through the selected desktop adapter before importing app."""
    from docking.platform.desktop.selection import desktop_services

    return desktop_services().prepare_display()


def main() -> None:
    """Prepare the platform display, then start the dock runtime."""
    prepare_display()
    from docking.app import main as run_app
    from docking.platform.composition.selection import platform_provider

    run_app(platform_provider())


if __name__ == "__main__":
    main()
