"""Recent-info records with observable filesystem checks for GTK contracts."""

from dataclasses import dataclass


@dataclass
class RecentInfo:
    name: str
    modified: int
    available: bool = True
    checks: int = 0

    def get_display_name(self) -> str:
        return self.name

    def get_uri(self) -> str:
        return f"file:///{self.name}"

    def get_modified(self) -> int:
        return self.modified

    def exists(self) -> bool:
        self.checks += 1
        return self.available
