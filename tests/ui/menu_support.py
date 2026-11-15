"""Menu image doubles with explicit scaling observations."""


class FakePixbuf:
    def __init__(self, width: int, height: int, scaled=None) -> None:
        self.width = width
        self.height = height
        self.scaled = scaled
        self.scale_calls: list[tuple[int, int, object]] = []

    def get_width(self) -> int:
        return self.width

    def get_height(self) -> int:
        return self.height

    def scale_simple(self, width: int, height: int, interp):
        self.scale_calls.append((width, height, interp))
        return self.scaled
