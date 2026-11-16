"""Native tray ABI cases shared by acceptance and release validation."""

from dataclasses import dataclass

_SHAPES = (
    ("hwnd", 0, "full"),
    ("hwnd", 3, "full"),
    ("hwnd", 4, "full"),
    ("guid", 0, "full"),
    ("guid", 3, "full"),
    ("guid", 4, "full"),
    ("hwnd", 0, "v1"),
    ("hwnd", 3, "v2"),
    ("hwnd", 4, "v2"),
    ("guid", 4, "v3"),
)


@dataclass(frozen=True)
class ProviderCase:
    bits: int = 64
    encoding: str = "unicode"
    identity: str = "guid"
    version: int = 4
    size: str = "full"

    def __post_init__(self):
        if self.bits not in (32, 64) or self.encoding not in ("unicode", "ansi"):
            raise ValueError("Invalid native tray architecture/encoding")
        if self.identity not in ("guid", "hwnd") or self.version not in (0, 3, 4):
            raise ValueError("Invalid tray identity/protocol version")
        if self.size not in ("v1", "v2", "v3", "full"):
            raise ValueError("Invalid NOTIFYICONDATA size")
        if self.size == "v1" and self.version != 0:
            raise ValueError("V1 does not contain uVersion")
        if self.identity == "guid" and self.size in ("v1", "v2"):
            raise ValueError("GUID identity requires a V3/full structure")

    @property
    def name(self) -> str:
        return (
            f"{self.bits}-{self.encoding}-{self.identity}-v{self.version}-{self.size}"
        )

    @property
    def native_size(self) -> int:
        # SDK FIELD_OFFSET macros describe native structures, not the shell's
        # canonical 32-bit-handle Unicode wire format decoded by Docking.
        wide = self.encoding == "unicode"
        if self.size == "v1":
            return (40 if self.bits == 64 else 24) + (128 if wide else 64)
        base = (
            (952 if self.bits == 64 else 936)
            if wide
            else (504 if self.bits == 64 else 488)
        )
        return (
            base + {"v2": 0, "v3": 16, "full": 24 if self.bits == 64 else 20}[self.size]
        )

    def configuration(self, label: str, guid: str, *, seconds: int = 120) -> dict:
        if not 1 <= seconds <= 600:
            raise ValueError("Provider lifetime must be between 1 and 600 seconds")
        return {
            "bits": self.bits,
            "label": label,
            "guid": guid,
            "identity": self.identity,
            "uid": 731 if self.identity == "hwnd" or label == "A" else 732,
            "version": self.version,
            "encoding": self.encoding,
            "size": self.size,
            "show_tip": False,
            "rich_popup": True,
            "seconds": seconds,
        }


def provider_cases() -> tuple[ProviderCase, ...]:
    return tuple(
        ProviderCase(bits, encoding, identity, version, size)
        for bits in (64, 32)
        for encoding in ("unicode", "ansi")
        for identity, version, size in _SHAPES
    )
