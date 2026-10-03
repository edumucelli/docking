"""Measure native output corners in parent pixels; do not infer rendered size."""


def marker_colors(index=0):
    offset = (index % 10) * 16
    return [
        (255, 0, 80 + offset),
        (0, 255, 80 + offset),
        (80 + offset, 0, 255),
        (255, 255, offset),
    ]


def measure_output(pixels, stride, channels, rect, colors=None):
    def find_marker(color):
        needle = bytes(color)
        found, offset = [], 0
        while True:
            offset = pixels.find(needle, offset)
            if offset < 0:
                break
            y, byte_x = divmod(offset, stride)
            x = byte_x // channels
            if (
                byte_x % channels == 0
                and rect["x"] <= x < rect["x"] + rect["width"]
                and rect["y"] <= y < rect["y"] + rect["height"]
            ):
                found.append((x, y))
            offset += 1
        if len(found) < 4:
            raise RuntimeError(
                "native output corner marker not visible in parent capture"
            )
        return (
            min(x for x, y in found),
            min(y for x, y in found),
            max(x for x, y in found),
            max(y for x, y in found),
        )

    corners = [find_marker(color) for color in colors or marker_colors()]
    left_edge, top_edge = corners[0][:2]
    right_edge, bottom_edge = corners[3][2:]
    if not (
        corners[1][2] == right_edge
        and corners[1][1] == top_edge
        and corners[2][0] == left_edge
        and corners[2][3] == bottom_edge
    ):
        raise RuntimeError("native output corner markers do not form a rectangle")
    return {
        "x": left_edge,
        "y": top_edge,
        "width": right_edge - left_edge + 1,
        "height": bottom_edge - top_edge + 1,
    }


def remove_markers(
    pixels, stride, channels, rect, background=(20, 46, 71), colors=None
):
    """Remove only calibration colours from the measured output's corner pixels."""
    result = bytearray(pixels)
    colors = colors or marker_colors()
    for corner, color in enumerate(colors):
        x0 = rect["x"] if corner % 2 == 0 else rect["x"] + rect["width"] - 16
        y0 = rect["y"] if corner < 2 else rect["y"] + rect["height"] - 16
        vector = [a - b for a, b in zip(color, background, strict=True)]
        length = sum(v * v for v in vector)
        for y in range(y0, y0 + 16):
            for x in range(x0, x0 + 16):
                offset = y * stride + x * channels
                rgb = pixels[offset : offset + 3]
                delta = [a - b for a, b in zip(rgb, background, strict=True)]
                fraction = (
                    sum(a * b for a, b in zip(delta, vector, strict=True)) / length
                )
                error = max(
                    abs(a - fraction * b) for a, b in zip(delta, vector, strict=True)
                )
                if 0 <= fraction <= 1.01 and error <= 4:
                    result[offset : offset + 3] = bytes(background)
    return bytes(result)
