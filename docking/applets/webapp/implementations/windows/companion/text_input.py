"""Decode the UTF-16 units carried by Windows Unicode packet messages."""


class PacketText:
    def __init__(self) -> None:
        self._high: int | None = None

    def reset(self) -> None:
        self._high = None

    def feed(self, unit: int) -> str:
        if not 0 <= unit <= 0xFFFF:
            raise ValueError("Invalid UTF-16 unit")
        high, self._high = self._high, None
        if 0xD800 <= unit <= 0xDBFF:
            self._high = unit
            return "\ufffd" if high is not None else ""
        if 0xDC00 <= unit <= 0xDFFF:
            return (
                chr(0x10000 + ((high - 0xD800) << 10) + unit - 0xDC00)
                if high is not None
                else "\ufffd"
            )
        return ("\ufffd" if high is not None else "") + chr(unit)
