r"""Offline astronomical moon phase calculation - no network needed.

When briancasey.org is unreachable, this module computes moon phase and
illumination from the date alone using a synodic period model. This file
is a self-contained astronomical calculator with no external dependencies.


The Moon's Orbit and Why It Has Phases
======================================

The Moon orbits the Earth roughly once every 29.5 days. As it does, the Sun
illuminates different portions of the lunar surface as seen from Earth. These
are the "phases" we observe - they are not caused by Earth's shadow (that is
an eclipse), but by the changing geometric angle between Sun, Moon, and Earth.

At new moon, the Moon is between the Earth and the Sun. The sunlit side
faces away from us - we see only the dark side. At full moon, the Earth
is between the Sun and the Moon - we see the entire sunlit face.

The cycle from one new moon to the next is called a synodic month. Its
mean duration is 29.53058770576 days (29 days, 12 hours, 44 minutes, and
2.9 seconds). This value has been measured with extreme precision over
centuries of observation.


The Synodic Period Model
========================

The simplest way to compute the current moon phase is:

    1. Pick a known new moon date (an "epoch").
    2. Count how many days have elapsed since that epoch.
    3. Divide by the synodic month length.
    4. The fractional part is the phase (0.0 = new, 0.5 = full, 1.0 = next new).

This is called the mean synodic period model. It assumes the Moon orbits
at a constant rate, which is not quite true - the Moon's orbit is elliptical
and perturbed by the Sun's gravity - but the error is small: typically within
1 day of the true phase, which is more than adequate for a dock icon.

Our epoch is January 29, 2025, a known new moon. We express this as a
Julian Day Number (see below) for easy arithmetic.


Phase to Illumination
=====================

The fraction of the Moon's disc that appears lit (the "illumination") follows
a cosine curve over the phase:

    illumination = (1 - cos(2 * pi * phase)) / 2

This gives:

    phase = 0.00 (new moon)      -> illumination = 0.00  (dark)
    phase = 0.25 (first quarter)  -> illumination = 0.50  (half lit)
    phase = 0.50 (full moon)      -> illumination = 1.00  (fully lit)
    phase = 0.75 (last quarter)   -> illumination = 0.50  (half lit)

The cosine is exact for a circular orbit viewed from infinite distance. For
the real Moon (slightly elliptical orbit, finite observer distance), the error
in illumination is less than 2% - invisible at dock icon resolution.


Waxing vs. Waning
=================

When ``phase < 0.5``, the Moon is waxing (growing brighter night to night):
  - 0.00–0.25: Waxing Crescent (thin sliver on the right in Northern Hemisphere)
  - 0.25:       First Quarter (right half lit)
  - 0.25–0.50: Waxing Gibbous (more than half lit, growing)

When ``phase > 0.5``, the Moon is waning (dimming night to night):
  - 0.50–0.75: Waning Gibbous (more than half lit, shrinking)
  - 0.75:       Last Quarter / Third Quarter (left half lit)
  - 0.75–1.00: Waning Crescent (thin sliver on the left)

In the Southern Hemisphere, left and right are swapped. The original
Cairo-Dock Moon applet handled this by swapping image suffixes ("a" for
Northern, "b" for Southern). Our Cairo renderer could do the same by
flipping the ``waning`` flag.


Julian Day Numbers
==================

Astronomers use Julian Day Numbers (JDN) to count days without worrying about
months, leap years, or calendar reforms. JDN 0 is January 1, 4713 BC in the
proleptic Julian calendar - a date chosen by Joseph Scaliger in 1583 to
predate all recorded history.

The conversion from a Gregorian calendar date to JDN uses the algorithm
published by the US Naval Observatory:

    a = (14 - month) / 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    JDN = day + (153 * m + 2) / 5 + 365 * y + y / 4 - y / 400 - 32045

This gives the JDN at noon UT on the given date. The magic numbers (153, 4800,
32045) encode the irregular month lengths and the offset between the Julian
epoch and the Gregorian calendar.

For example:
    March 4, 2026  -> JDN 2,461,108
    January 1, 2000 -> JDN 2,451,545

The difference between two JDNs is the number of days between two dates -
exactly what we need for the synodic calculation.


Accuracy and Limitations
========================

This model's error budget:

- Synodic period variation: The actual time between consecutive new moons
  varies from ~29.27 to ~29.83 days due to orbital eccentricity. Our mean
  value (29.53) accumulates ~0.5 day of error per year. Re-anchoring the
  epoch to a recent new moon (Jan 29, 2025) keeps the error small for years
  in either direction.

- Illumination approximation: The cosine model assumes the observer is
  infinitely far from the Moon–Sun system. The real observer (on Earth's
  surface) sees very slightly different illumination due to parallax. The
  error is under 1% and completely invisible in a 48-pixel icon.

- Time-of-day: We ignore the time of day (treating each date as noon UT).
  This adds up to ~0.5 day of phase error, which shifts the illumination by
  at most ~3%. Again, invisible at icon scale.

For a dock applet, this is more than sufficient. Professional ephemeris
software (like JPL's HORIZONS) uses Keplerian orbital elements with
perturbation terms from dozens of solar system bodies - overkill for our
purpose by many orders of magnitude.


Historical Note
===============

Humans have tracked lunar phases for at least 30,000 years. The Lebombo
bone (a baboon fibula from ~35,000 BC found in Swaziland) has 29 notch
marks - possibly the earliest known lunar calendar. The Babylonians
formalized the synodic month around 500 BC, and their value (29.53 days)
matches our modern measurement to within seconds.

This module carries on that tradition with a few lines of Python.
"""

from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING

from docking.i18n import _

if TYPE_CHECKING:
    from docking.applets.moon.state import MoonData

# Mean synodic month (new moon to new moon)
SYNODIC_MONTH = 29.53058770576

# Known new moon: Jan 29, 2025 (Julian Day Number 2460739)
_NEW_MOON_EPOCH_JD = 2460739.0

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _format_date(d: date) -> str:
    """Locale-independent date format (e.g. 'Mar 4, 2026')."""
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _julian_day(d: date) -> float:
    """Convert a date to Julian Day number (noon UT)."""
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return float(d.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 400 - 32045)


def moon_phase_from_date(d: date | None = None) -> float:
    """Compute moon phase as 0.0–1.0 from date alone.

    0.0 = new moon, 0.5 = full moon, 1.0 = next new moon.
    """
    if d is None:
        d = date.today()
    jd = _julian_day(d=d)
    days_since = jd - _NEW_MOON_EPOCH_JD
    return (days_since % SYNODIC_MONTH) / SYNODIC_MONTH


def illumination_from_phase(phase: float) -> float:
    """Approximate illumination fraction from phase (0–1)."""
    return (1.0 - math.cos(2.0 * math.pi * phase)) / 2.0


def phase_from_illumination(illumination: float) -> str:
    """Map illumination to phase name without description context."""
    if illumination < 0.02:
        return _("New")
    if illumination > 0.98:
        return _("Full")
    if illumination < 0.5:
        return _("Crescent")
    return _("Gibbous")


def fetch_moon_offline(d: date | None = None) -> MoonData:
    """Compute moon data from astronomical calculation - no network needed."""
    from docking.applets.moon.state import MoonData

    if d is None:
        d = date.today()
    phase = moon_phase_from_date(d=d)
    illum = illumination_from_phase(phase=phase)
    waning = phase > 0.5
    if phase < 0.03 or phase > 0.97:
        desc = _("new moon")
    elif 0.23 < phase < 0.27:
        desc = _("first quarter")
    elif 0.47 < phase < 0.53:
        desc = _("full moon")
    elif 0.73 < phase < 0.77:
        desc = _("last quarter")
    elif phase < 0.5:
        desc = _("{days} days after new moon").format(
            days=f"{phase * SYNODIC_MONTH:.1f}"
        )
    else:
        days_after = (phase - 0.5) * SYNODIC_MONTH
        desc = _("{days} days after full moon").format(days=f"{days_after:.1f}")
    return MoonData(
        image_name=f"moon{int(illum * 10):02d}{'b' if waning else 'a'}",
        illumination=round(illum, 3),
        description=desc,
        date_label=_format_date(d=d),
    )
