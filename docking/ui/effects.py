"""Animation effects — easing functions, color extraction, and timing constants."""

from __future__ import annotations

import math

from docking.core.theme import RGB

# Hover lighten: additive blending effect on the hovered icon.
#
# When the cursor hovers an icon, a white additive overlay fades in
# over HOVER_FADE_FRAMES, making the icon visibly brighter. This uses
# Cairo's ADD operator, which adds RGB values per-pixel:
#   result_rgb = icon_rgb + (1,1,1) * lighten_alpha
#
# The effect is subtle (max 20% brightness increase) to avoid
# washing out the icon. The linear fade (step = max / frames) takes
# ~150ms at 60fps, which is fast enough to feel responsive but slow
# enough to appear smooth rather than instant.
#
#   lighten=0.0        lighten=0.1        lighten=0.2
#   [normal icon]  →  [slightly bright]  →  [noticeably bright]
#
# On un-hover, the lighten value fades back to 0.0 at the same rate.
# When it reaches 0.0, the entry is removed from the dict entirely
# (no stale zero-entries accumulate).
HOVER_LIGHTEN_MAX = 0.2  # max additive brightness (0.0-1.0)
HOVER_FADE_FRAMES = 9  # ~150ms at 60fps (150/16.67)

# Click darken: a brief sine-curve pulse that dims the icon on click.
#
# When the user clicks an icon, a black ATOP overlay fades in and
# out following a sine curve over CLICK_DURATION_US microseconds:
#   darken(t) = sin(pi * t / duration) * CLICK_DARKEN_MAX
#
# The sine shape creates a smooth "flash" — starts at 0, peaks at
# the midpoint (150ms), and returns to 0 at the end (300ms):
#
#   darken ▲
#    0.5   │   ╭──╮
#          │  ╭╯  ╰╮
#    0.0   │──╯    ╰──
#          └──────────────→ time (ms)
#          0    150    300
#
# Cairo ATOP operator ensures the black only affects the icon's own
# opaque pixels — transparent areas remain transparent.
CLICK_DURATION_US = 300_000  # 300ms in microseconds
CLICK_DARKEN_MAX = 0.5  # max darkening on click (0.0-1.0)

# Launch bounce: icon hops upward when launching an application.
#
# Uses easing_bounce with n=2 (two bounces). The first bounce
# reaches full height (icon_size * 0.625 = 30px for 48px icons),
# and the second bounce is lower (~33% of first) due to the
# decay envelope. Total animation: 600ms.
#
#   y-offset ▲
#    30px    │  ╭╮
#            │ ╭╯╰╮      (second bounce ~10px)
#            │╭╯  ╰╮╭╮
#    0px     │╯    ╰╯╰─
#            └────────────→ time (ms)
#            0   200  400  600
LAUNCH_BOUNCE_DURATION_US = 600_000  # 600ms
LAUNCH_BOUNCE_HEIGHT = 0.625  # fraction of icon_size

# Urgent bounce: taller single bounce when an app demands attention.
#
# Uses easing_bounce with n=1 (single arc). The icon rises to
# icon_size * 1.66 = ~80px for 48px icons — significantly taller
# than the launch bounce to be unmissable. The single arc with
# decay envelope creates a clean rise-and-fall over 600ms.
#
#   y-offset ▲
#    80px    │  ╭──╮
#            │ ╭╯  ╰╮
#            │╭╯    ╰╮
#    0px     │╯      ╰─
#            └──────────────→ time (ms)
#            0    300    600
URGENT_BOUNCE_DURATION_US = 600_000  # 600ms
URGENT_BOUNCE_HEIGHT = 1.66  # fraction of icon_size


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
        return (0.5, 0.5, 0.5)

    pixels = pixbuf.get_pixels()  # type: ignore[attr-defined]
    n_channels = pixbuf.get_n_channels()  # type: ignore[attr-defined]
    width = pixbuf.get_width()  # type: ignore[attr-defined]
    height = pixbuf.get_height()  # type: ignore[attr-defined]
    rowstride = pixbuf.get_rowstride()  # type: ignore[attr-defined]

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
            a = pixels[offset + 3] if n_channels >= 4 else 255

            # Skip nearly-transparent pixels
            if a < 25:
                continue

            min_channel = min(r, g, b)
            max_channel = max(r, g, b)
            delta = max_channel - min_channel
            # Saturation score: 0.0 for grays, 1.0 for fully saturated
            score = (delta / max_channel) if max_channel > 0 else 0.0

            r_total += score * r / 255
            g_total += score * g / 255
            b_total += score * b / 255
            score_total += score
            count += 1

    if count == 0:
        return (0.5, 0.5, 0.5)

    if score_total > 0:
        r_avg = r_total / score_total
        g_avg = g_total / score_total
        b_avg = b_total / score_total
    else:
        # All pixels were gray (zero saturation) — fall back to neutral
        r_avg = g_avg = b_avg = 0.5

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
    # 1) abs(sin(n * pi * p)) — the bounce oscillation
    #    Using abs(sin) ensures all bounces go upward (positive).
    #    The parameter n controls the number of half-arcs within
    #    the duration. For n=2, sin completes 2 full half-arcs
    #    (two bounces); for n=1, a single arc (one bounce).
    #
    # 2) min(1.0, (1-p) * 2n / (2n-1)) — the decay envelope
    #    A linear decay from a value > 1.0 down to 0.0 over the
    #    full duration. The min(1.0, ...) clamp ensures the first
    #    bounce reaches exactly 1.0 at its peak (not higher).
    #
    #    The factor 2n/(2n-1) sets the starting value:
    #      n=1: 2/1 = 2.0 → envelope starts at 2.0, clamped to 1.0
    #      n=2: 4/3 ≈ 1.33 → envelope starts at 1.33, clamped to 1.0
    #
    #    This is tuned so the FIRST bounce peak hits exactly 1.0.
    #    Subsequent bounces are lower because the envelope has
    #    decayed past 1.0 by then.
    #
    #    For n=2 (launch bounce):
    #      First peak at p≈0.25:  envelope ≈ 1.0  → bounce = 1.0
    #      Second peak at p≈0.75: envelope ≈ 0.33 → bounce ≈ 0.33
    #
    #    For n=1 (urgent bounce):
    #      Single peak at p=0.5:  envelope = 1.0  → bounce = 1.0
    #      Decays smoothly to 0 by p=1.0
    if duration <= 0 or t >= duration:
        return 0.0
    p = t / duration
    envelope = min(1.0, (1.0 - p) * (2.0 * n) / (2.0 * n - 1.0))
    return abs(math.sin(n * math.pi * p)) * envelope
