"""Animation effects -- easing functions and color extraction.

Timing constants (click duration, bounce heights, hover lighten, etc.)
are loaded from the theme JSON via Theme.load(). Only pure functions
remain here.
"""

from __future__ import annotations

import math

from docking.core.theme import RGB

NEUTRAL_GRAY_RGB: RGB = (0.5, 0.5, 0.5)
DOMINANT_COLOR_ALPHA_THRESHOLD = 25
COLOR_CHANNEL_MAX = 255.0


def average_icon_color(
    pixbuf: object,
) -> RGB:
    """Compute the saturation-weighted average color of an icon pixbuf.

    Returns (r, g, b) in 0.0-1.0 range, or (0.5, 0.5, 0.5) for
    missing/empty/gray icons.
    """
    # The goal is to extract the icon's "dominant color" for use in
    # the active glow effect. A naive average of all pixels would
    # produce muddy browns/grays because most icon backgrounds
    # contain desaturated pixels (grays, whites, near-blacks).
    #
    # Instead, we use saturation-weighted averaging. Each pixel gets a
    # "score" based on how colorful (saturated) it is. Vibrant pixels
    # contribute heavily to the average; gray pixels contribute nothing.
    #
    # The score formula uses HSV-like saturation:
    #   score = (max_channel - min_channel) / max_channel
    #
    # For a pure red pixel (255, 0, 0):  score = 255/255 = 1.0 (max weight)
    # For a gray pixel (128, 128, 128):  score = 0/128   = 0.0 (ignored)
    # For a muted blue (100, 100, 180):  score = 80/180  = 0.44 (moderate)
    #
    # The weighted sum is:
    #   r_avg = sum(score_i * r_i) / sum(score_i)
    #
    # This naturally selects the icon's most visually prominent hue.
    # For example, a Firefox icon with a blue globe and gray background
    # will average to blue because the blue pixels have high saturation
    # scores while the gray pixels have scores near zero.
    #
    # Transparent pixels (alpha < 25) are skipped entirely since they
    # have no visual contribution. If ALL pixels are gray (score_total=0),
    # we fall back to neutral gray (0.5, 0.5, 0.5).
    if pixbuf is None:
        return NEUTRAL_GRAY_RGB

    pixels = pixbuf.get_pixels()
    n_channels = pixbuf.get_n_channels()
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    rowstride = pixbuf.get_rowstride()
    r_total = 0.0
    g_total = 0.0
    b_total = 0.0
    score_total = 0.0
    count = 0

    for y in range(height):
        for x in range(width):
            offset = y * rowstride + x * n_channels
            r = pixels[offset]
            g = pixels[offset + 1]
            b = pixels[offset + 2]
            a = pixels[offset + 3] if n_channels >= 4 else int(COLOR_CHANNEL_MAX)

            # Skip nearly-transparent pixels
            if a < DOMINANT_COLOR_ALPHA_THRESHOLD:
                continue

            min_channel = min(r, g, b)
            max_channel = max(r, g, b)
            delta = max_channel - min_channel
            # Saturation score: 0.0 for grays, 1.0 for fully saturated
            score = (delta / max_channel) if max_channel > 0 else 0.0

            r_total += score * r / COLOR_CHANNEL_MAX
            g_total += score * g / COLOR_CHANNEL_MAX
            b_total += score * b / COLOR_CHANNEL_MAX
            score_total += score
            count += 1

    if count == 0:
        return NEUTRAL_GRAY_RGB

    if score_total > 0:
        r_avg = r_total / score_total
        g_avg = g_total / score_total
        b_avg = b_total / score_total
    else:
        # All pixels were gray (zero saturation) -- fall back to neutral
        r_avg, g_avg, b_avg = NEUTRAL_GRAY_RGB

    # Clamp: ensure no channel exceeds 1.0 (can happen with
    # rounding in heavily saturated icons)
    max_channel = max(r_avg, g_avg, b_avg)
    if max_channel > 1.0:
        r_avg /= max_channel
        g_avg /= max_channel
        b_avg /= max_channel

    return (r_avg, g_avg, b_avg)


def easing_bounce(t: float, duration: float, n: float = 1.0) -> float:
    """Sinusoidal bounce easing with momentum decay.

    Simulates a ball bouncing n times with decreasing height.
    """
    # The bounce is the product of two components:
    #
    # 1) abs(sin(n * pi * p)) -- the bounce oscillation
    #    Using abs(sin) ensures all bounces go upward (positive).
    #    The parameter n controls the number of half-arcs within
    #    the duration. For n=2, sin completes 2 full half-arcs
    #    (two bounces); for n=1, a single arc (one bounce).
    #
    # 2) min(1.0, (1-p) * 2n / (2n-1)) -- the decay envelope
    #    A linear decay from a value > 1.0 down to 0.0 over the
    #    full duration. The min(1.0, ...) clamp ensures the first
    #    bounce reaches exactly 1.0 at its peak (not higher).
    #
    #    The factor 2n/(2n-1) sets the starting value:
    #      n=1: 2/1 = 2.0 -> envelope starts at 2.0, clamped to 1.0
    #      n=2: 4/3 ~ 1.33 -> envelope starts at 1.33, clamped to 1.0
    #
    #    This is tuned so the first bounce peak hits exactly 1.0.
    #    Subsequent bounces are lower because the envelope has
    #    decayed past 1.0 by then.
    #
    #    For n=2 (launch bounce):
    #      First peak at p~0.25:  envelope ~ 1.0  -> bounce = 1.0
    #      Second peak at p~0.75: envelope ~ 0.33 -> bounce ~ 0.33
    #
    #    For n=1 (urgent bounce):
    #      Single peak at p=0.5:  envelope = 1.0  -> bounce = 1.0
    #      Decays smoothly to 0 by p=1.0
    if duration <= 0 or t >= duration:
        return 0.0
    p = t / duration
    envelope = min(1.0, (1.0 - p) * (2.0 * n) / (2.0 * n - 1.0))
    return abs(math.sin(n * math.pi * p)) * envelope
