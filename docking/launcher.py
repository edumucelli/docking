"""Launch Docking after backend-specific display setup."""

from __future__ import annotations


def prepare_display() -> bool:
    """Let a backend prepare GTK before the application module loads."""
    from docking.platform.backends.cinnamon.startup import prepare_cinnamon_display

    return prepare_cinnamon_display()


def main() -> None:
    """Prepare the platform display, then start the dock runtime."""
    prepare_display()
    from docking.app import main as run_app

    run_app()


if __name__ == "__main__":
    main()
