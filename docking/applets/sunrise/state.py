# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Pure state and solar calculations for the Sunrise applet."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any, NamedTuple

from docking.applets.cities import CityEntry, load_cities
from docking.i18n import _

REFRESH_INTERVAL_S = 60

DAY_MINUTES = 24 * 60
ZENITH_SUNRISE = 90.833
ZENITH_CIVIL = 96.0
ZENITH_NAUTICAL = 102.0
ZENITH_ASTRONOMICAL = 108.0


class CityPref(NamedTuple):
    """A persisted city entry."""

    city_display: str
    lat: float
    lng: float


class SolarPhase(str, Enum):
    NIGHT = "night"
    ASTRONOMICAL = "astronomical"
    NAUTICAL = "nautical"
    CIVIL = "civil"
    DAYLIGHT = "daylight"


class LabelMode(str, Enum):
    NEXT_EVENT = "next_event"
    PHASE = "phase"
    SUNRISE_SUNSET = "sunrise_sunset"


@dataclass(frozen=True, slots=True)
class SunrisePrefs:
    """Persisted Sunrise applet preferences."""

    cities: tuple[CityPref, ...] = ()
    active_index: int = 0
    label_mode: LabelMode = LabelMode.NEXT_EVENT


@dataclass(frozen=True, slots=True)
class SolarEvent:
    """One named solar event."""

    key: str
    label: str
    when: dt.datetime | None


@dataclass(frozen=True, slots=True)
class SolarDay:
    """Solar event snapshot for one local date."""

    date: dt.date
    events: tuple[SolarEvent, ...]
    polar_day: bool = False
    polar_night: bool = False

    def event(self, key: str) -> dt.datetime | None:
        for event in self.events:
            if event.key == key:
                return event.when
        return None


@dataclass(frozen=True, slots=True)
class SolarSnapshot:
    """Current solar state for rendering and tooltip text."""

    city: CityPref | None
    now: dt.datetime
    today: SolarDay | None
    phase: SolarPhase
    next_event: SolarEvent | None
    label_mode: LabelMode = LabelMode.NEXT_EVENT


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> SunrisePrefs:
    """Build preferences from persisted values."""
    if not prefs:
        return SunrisePrefs()

    cities: tuple[CityPref, ...] = ()
    raw_cities = prefs.get("cities")
    if isinstance(raw_cities, list | tuple):
        cities = tuple(
            CityPref(
                city_display=str(city.get("city_display", "")),
                lat=float(city.get("lat", 0.0)),
                lng=float(city.get("lng", 0.0)),
            )
            for city in raw_cities
            if isinstance(city, Mapping)
        )
    else:
        city_display = str(prefs.get("city_display", ""))
        if city_display:
            cities = (
                CityPref(
                    city_display=city_display,
                    lat=float(prefs.get("lat", 0.0)),
                    lng=float(prefs.get("lng", 0.0)),
                ),
            )

    active_index = int(prefs.get("active_index", 0))
    active_index = max(0, min(active_index, len(cities) - 1)) if cities else 0
    return SunrisePrefs(
        cities=cities,
        active_index=active_index,
        label_mode=normalize_label_mode(prefs.get("label_mode")),
    )


def prefs_payload(
    *,
    cities: tuple[CityPref, ...],
    active_index: int,
    label_mode: LabelMode,
) -> dict[str, object]:
    """Build payload used by save_prefs()."""
    return {
        "cities": [
            {"city_display": c.city_display, "lat": c.lat, "lng": c.lng} for c in cities
        ],
        "active_index": active_index,
        "label_mode": normalize_label_mode(label_mode).value,
    }


def normalize_label_mode(value: object) -> LabelMode:
    """Return a known label mode."""
    if isinstance(value, LabelMode):
        return value
    try:
        return LabelMode(str(value))
    except ValueError:
        return LabelMode.NEXT_EVENT


def cycle_active_index(*, count: int, current: int, direction_up: bool) -> int:
    """Cycle through configured cities."""
    if count <= 1:
        return 0
    delta = -1 if direction_up else 1
    return (current + delta) % count


@lru_cache(maxsize=1)
def cached_cities() -> tuple[CityEntry, ...]:
    """Load city database on first access."""
    return tuple(load_cities())


def build_snapshot(
    *,
    city: CityPref | None,
    now: dt.datetime | None = None,
    label_mode: LabelMode = LabelMode.NEXT_EVENT,
) -> SolarSnapshot:
    """Build the current solar snapshot."""
    current = now or dt.datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    if city is None:
        return SolarSnapshot(
            city=None,
            now=current,
            today=None,
            phase=SolarPhase.NIGHT,
            next_event=None,
            label_mode=label_mode,
        )

    today = solar_day(
        date=current.date(),
        latitude=city.lat,
        longitude=city.lng,
        tz=current.tzinfo or dt.datetime.now().astimezone().tzinfo,
    )
    next_event = find_next_event(
        city=city,
        now=current,
    )
    return SolarSnapshot(
        city=city,
        now=current,
        today=today,
        phase=phase_at(now=current, day=today),
        next_event=next_event,
        label_mode=label_mode,
    )


def solar_day(
    *,
    date: dt.date,
    latitude: float,
    longitude: float,
    tz: dt.tzinfo | None,
) -> SolarDay:
    """Calculate solar events for one local date.

    The implementation follows the compact NOAA sunrise/sunset equations and
    converts the resulting UTC minutes into the user's display timezone.
    """
    tzinfo = tz or dt.timezone.utc
    event_specs = _event_specs()
    solar_noon_utc = _solar_noon_utc_minutes(date=date, longitude=longitude)
    utc_midnight = dt.datetime.combine(date, dt.time(), tzinfo=dt.timezone.utc)
    events: list[SolarEvent] = []
    polar_day = False
    polar_night = False

    for key, label, zenith, rising in event_specs:
        if zenith is None:
            minutes = solar_noon_utc
        else:
            result = _event_utc_minutes(
                date=date,
                latitude=latitude,
                longitude=longitude,
                zenith=zenith,
                rising=rising,
            )
            if result == _PolarState.ALWAYS_UP:
                polar_day = True
                minutes = None
            elif result == _PolarState.ALWAYS_DOWN:
                polar_night = True
                minutes = None
            else:
                minutes = result
        when = (
            (utc_midnight + dt.timedelta(minutes=float(minutes))).astimezone(tzinfo)
            if isinstance(minutes, float)
            else None
        )
        events.append(SolarEvent(key=key, label=label, when=when))

    return SolarDay(
        date=date,
        events=tuple(events),
        polar_day=polar_day and not polar_night,
        polar_night=polar_night and not polar_day,
    )


def phase_at(*, now: dt.datetime, day: SolarDay) -> SolarPhase:
    """Classify the current solar phase."""
    if day.polar_day:
        return SolarPhase.DAYLIGHT
    if day.polar_night:
        return SolarPhase.NIGHT

    ordered = (
        ("astronomical_dawn", SolarPhase.ASTRONOMICAL),
        ("nautical_dawn", SolarPhase.NAUTICAL),
        ("civil_dawn", SolarPhase.CIVIL),
        ("sunrise", SolarPhase.DAYLIGHT),
        ("sunset", SolarPhase.CIVIL),
        ("civil_dusk", SolarPhase.NAUTICAL),
        ("nautical_dusk", SolarPhase.ASTRONOMICAL),
        ("astronomical_dusk", SolarPhase.NIGHT),
    )
    current = SolarPhase.NIGHT
    for key, phase in ordered:
        when = day.event(key)
        if when is not None and now >= when:
            current = phase
    return current


def find_next_event(*, city: CityPref, now: dt.datetime) -> SolarEvent | None:
    """Return the next solar event from today or tomorrow."""
    tzinfo = now.tzinfo or dt.datetime.now().astimezone().tzinfo
    days = (
        solar_day(
            date=now.date(),
            latitude=city.lat,
            longitude=city.lng,
            tz=tzinfo,
        ),
        solar_day(
            date=now.date() + dt.timedelta(days=1),
            latitude=city.lat,
            longitude=city.lng,
            tz=tzinfo,
        ),
    )
    for event in _chronological_events(days):
        if event.when is not None and event.when > now:
            return event
    return None


def tooltip_text(snapshot: SolarSnapshot) -> str:
    """Build tooltip string."""
    if snapshot.city is None:
        return _("Sunrise: no city selected")
    lines = [
        snapshot.city.city_display,
        _("{phase} now").format(phase=phase_label(snapshot.phase)),
    ]
    if snapshot.next_event is not None and snapshot.next_event.when is not None:
        lines.append(
            _("{event} in {duration}").format(
                event=snapshot.next_event.label,
                duration=format_duration(snapshot.next_event.when - snapshot.now),
            )
        )
    if snapshot.today is not None:
        for event in snapshot.today.events:
            if event.when is None:
                continue
            lines.append(f"{event.label}: {event.when.strftime('%H:%M')}")
    return "\n".join(lines)


def icon_label(snapshot: SolarSnapshot) -> str:
    """Return compact icon label text for the selected mode."""
    if snapshot.city is None:
        return ""
    if snapshot.label_mode == LabelMode.PHASE:
        return phase_short_label(snapshot.phase)
    if snapshot.label_mode == LabelMode.SUNRISE_SUNSET and snapshot.today is not None:
        sunrise = snapshot.today.event("sunrise")
        sunset = snapshot.today.event("sunset")
        if sunrise is not None and sunset is not None:
            return f"{sunrise.strftime('%H:%M')}/{sunset.strftime('%H:%M')}"
    if snapshot.next_event is None or snapshot.next_event.when is None:
        return phase_short_label(snapshot.phase)
    return format_duration(snapshot.next_event.when - snapshot.now)


def phase_label(phase: SolarPhase) -> str:
    """Human label for a solar phase."""
    return {
        SolarPhase.NIGHT: _("Night"),
        SolarPhase.ASTRONOMICAL: _("Astronomical twilight"),
        SolarPhase.NAUTICAL: _("Nautical twilight"),
        SolarPhase.CIVIL: _("Civil twilight"),
        SolarPhase.DAYLIGHT: _("Daylight"),
    }[phase]


def phase_short_label(phase: SolarPhase) -> str:
    """Compact phase label for the dock icon."""
    return {
        SolarPhase.NIGHT: _("Night"),
        SolarPhase.ASTRONOMICAL: _("Astro"),
        SolarPhase.NAUTICAL: _("Naut"),
        SolarPhase.CIVIL: _("Civil"),
        SolarPhase.DAYLIGHT: _("Day"),
    }[phase]


def label_mode_label(mode: LabelMode) -> str:
    """Human label for menu choices."""
    return {
        LabelMode.NEXT_EVENT: _("Next Event"),
        LabelMode.PHASE: _("Current Phase"),
        LabelMode.SUNRISE_SUNSET: _("Sunrise/Sunset"),
    }[mode]


def menu_header_label(snapshot: SolarSnapshot) -> str:
    """Return the menu header label."""
    if snapshot.city is None:
        return _("No city selected")
    if snapshot.next_event is None or snapshot.next_event.when is None:
        return _("{city}: {phase}").format(
            city=snapshot.city.city_display,
            phase=phase_label(snapshot.phase),
        )
    return _("{city}: {event} in {duration}").format(
        city=snapshot.city.city_display,
        event=snapshot.next_event.label,
        duration=format_duration(snapshot.next_event.when - snapshot.now),
    )


def format_duration(delta: dt.timedelta) -> str:
    """Format a future duration compactly."""
    total_minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return _("{hours}h {minutes}m").format(hours=hours, minutes=minutes)
    return _("{minutes}m").format(minutes=minutes)


def _event_specs() -> tuple[tuple[str, str, float | None, bool], ...]:
    """Return the solar events displayed by the applet.

    These labels are intentionally user-facing, but the longer explanations live
    here in code so future changes preserve the meaning of each displayed event.
    The dawn events are ordered from darkest to brightest as the sun approaches
    the horizon. The dusk events mirror them in reverse as the sun moves below
    the horizon after sunset.

    Astronomical dawn is the first morning twilight boundary shown by the
    applet. The sun's center is 18 degrees below the true horizon. At this point
    full night starts to soften, but the sky is still dark enough for most
    astronomical observing.

    Nautical dawn begins when the sun's center is 12 degrees below the morning
    horizon. The name comes from marine navigation: the horizon becomes easier
    to distinguish at sea, while the sky is still too dim for ordinary daylight
    activity.

    Civil dawn begins when the sun's center is 6 degrees below the morning
    horizon. There is usually enough natural light for most outdoor activity
    before the sun itself appears.

    Sunrise is when the visible upper edge of the sun appears above the horizon.
    The applet uses the standard 90.833 degree zenith for this rather than a
    purely geometric 90 degree horizon. The extra 0.833 degrees accounts for
    atmospheric refraction and the apparent radius of the solar disc.

    Solar noon is when the sun crosses the local meridian and reaches its
    highest point in the sky for the day. It is local solar noon, not clock
    noon, so it usually does not happen at 12:00 in the user's timezone.

    Sunset mirrors sunrise: it is when the visible upper edge of the sun
    disappears below the horizon, using the same refraction and solar-disc
    correction as sunrise.

    Civil dusk is the evening boundary where the sun's center is 6 degrees below
    the horizon. After this point, ordinary outdoor activity usually needs
    artificial light.

    Nautical dusk is the evening boundary where the sun's center is 12 degrees
    below the horizon. The sea horizon becomes hard to distinguish, and the sky
    is well into night-like twilight.

    Astronomical dusk is the final evening twilight boundary shown by the
    applet. The sun's center is 18 degrees below the horizon. After this point
    the sky is considered fully dark for most astronomical observing.
    """
    return (
        (
            "astronomical_dawn",
            _("Astronomical dawn"),
            ZENITH_ASTRONOMICAL,
            True,
        ),
        (
            "nautical_dawn",
            _("Nautical dawn"),
            ZENITH_NAUTICAL,
            True,
        ),
        (
            "civil_dawn",
            _("Civil dawn"),
            ZENITH_CIVIL,
            True,
        ),
        (
            "sunrise",
            _("Sunrise"),
            ZENITH_SUNRISE,
            True,
        ),
        (
            "solar_noon",
            _("Solar noon"),
            None,
            True,
        ),
        (
            "sunset",
            _("Sunset"),
            ZENITH_SUNRISE,
            False,
        ),
        (
            "civil_dusk",
            _("Civil dusk"),
            ZENITH_CIVIL,
            False,
        ),
        (
            "nautical_dusk",
            _("Nautical dusk"),
            ZENITH_NAUTICAL,
            False,
        ),
        (
            "astronomical_dusk",
            _("Astronomical dusk"),
            ZENITH_ASTRONOMICAL,
            False,
        ),
    )


def _chronological_events(days: Iterable[SolarDay]) -> tuple[SolarEvent, ...]:
    events = [event for day in days for event in day.events if event.when is not None]
    events.sort(
        key=lambda event: event.when or dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    )
    return tuple(events)


class _PolarState(str, Enum):
    ALWAYS_UP = "always_up"
    ALWAYS_DOWN = "always_down"


def _event_utc_minutes(
    *,
    date: dt.date,
    latitude: float,
    longitude: float,
    zenith: float,
    rising: bool,
) -> float | _PolarState:
    """Return a rise/set time as UTC minutes after midnight.

    This is the core horizon-crossing calculation. The applet asks the same
    question for several horizons:

    - 90.833 degrees: sunrise and sunset. The extra 0.833 degrees accounts
      for the apparent solar radius and atmospheric refraction, so the event
      matches the visually observed upper edge of the sun.
    - 96, 102, and 108 degrees: civil, nautical, and astronomical twilight.
      These are expressed as "zenith" angles, where 90 degrees means the sun
      is exactly on the geometric horizon and larger values mean the sun is
      farther below it.

    The calculation starts with a solar-noon estimate for the date and
    longitude. From that estimated time, _solar_terms returns two values:
    the equation of time, which tells us how apparent solar time differs from
    mean clock time, and solar declination, the sun's latitude-like position in
    the sky. Combining declination with the observer latitude and requested
    horizon gives cosine_hour_angle: how far the Earth must rotate from
    solar noon before the sun reaches that horizon. One degree of rotation is
    four minutes of time, so the hour angle in degrees becomes a minute offset
    from solar noon.

    At high latitudes the sun may never reach a requested horizon on a given
    date. In that case the cosine falls outside the valid acos range [-1, 1].
    Values above 1 mean the sun stays below that horizon all day; values below
    -1 mean it stays above it all day.
    """
    solar_noon_utc = _solar_noon_utc_minutes(date=date, longitude=longitude)
    result = _event_utc_minutes_for_terms(
        date=date,
        latitude=latitude,
        longitude=longitude,
        zenith=zenith,
        rising=rising,
        utc_minutes=solar_noon_utc,
    )
    if isinstance(result, _PolarState):
        return result

    # Refine once using the approximate event time. Declination and the
    # equation of time drift slightly during the day; recomputing them at the
    # event rather than at noon removes most of the remaining approximation
    # without adding a visible performance cost.
    return _event_utc_minutes_for_terms(
        date=date,
        latitude=latitude,
        longitude=longitude,
        zenith=zenith,
        rising=rising,
        utc_minutes=result,
    )


def _event_utc_minutes_for_terms(
    *,
    date: dt.date,
    latitude: float,
    longitude: float,
    zenith: float,
    rising: bool,
    utc_minutes: float,
) -> float | _PolarState:
    """Return one horizon crossing using solar terms for a specific UTC minute."""
    equation_of_time, solar_declination = _solar_terms(
        date=date,
        utc_minutes=utc_minutes,
    )
    latitude_radians = math.radians(latitude)
    zenith_radians = math.radians(zenith)
    cosine_hour_angle = math.cos(zenith_radians) / (
        math.cos(latitude_radians) * math.cos(solar_declination)
    ) - math.tan(latitude_radians) * math.tan(solar_declination)
    if cosine_hour_angle > 1.0:
        return _PolarState.ALWAYS_DOWN
    if cosine_hour_angle < -1.0:
        return _PolarState.ALWAYS_UP
    hour_angle_minutes = math.degrees(math.acos(cosine_hour_angle)) * 4.0
    solar_noon_utc = 720.0 - (4.0 * longitude) - equation_of_time
    if rising:
        return solar_noon_utc - hour_angle_minutes
    return solar_noon_utc + hour_angle_minutes


def _solar_noon_utc_minutes(*, date: dt.date, longitude: float) -> float:
    """Return solar noon as UTC minutes after midnight.

    Clock noon is fixed by time zones, but solar noon is local: it is the moment
    when the sun crosses the observer's meridian and reaches its highest point
    for the day. The baseline is 720 minutes, i.e. 12:00 UTC at longitude 0.

    Longitude shifts that moment by four minutes per degree because Earth
    rotates 360 degrees in 24 hours. East longitudes see the sun earlier than
    Greenwich, so they subtract minutes from the UTC noon baseline; west
    longitudes add minutes.

    The final correction is the "equation of time". Earth's orbit is
    elliptical and its axis is tilted, so apparent solar time and mean clock
    time drift by several minutes through the year. _solar_terms computes
    that correction from Julian centuries and Meeus/NOAA solar elements. A
    second pass evaluates the correction at the estimated noon time, which is
    more accurate than evaluating every date at fixed clock noon.
    """
    equation_of_time, _declination = _solar_terms(date=date, utc_minutes=720.0)
    estimated_noon = 720.0 - (4.0 * longitude) - equation_of_time
    equation_of_time, _declination = _solar_terms(
        date=date,
        utc_minutes=estimated_noon,
    )
    return 720.0 - (4.0 * longitude) - equation_of_time


def _solar_terms(*, date: dt.date, utc_minutes: float) -> tuple[float, float]:
    """Return equation of time in minutes and solar declination in radians.

    This is the higher-precision NOAA/Meeus-style model used by the improved
    sunrise/sunset calculator. The input date plus UTC minute is first
    converted to a Julian day, then to Julian centuries since J2000.0. That
    time scale is the natural input for the solar orbital elements below.

    The intermediate names are intentionally explicit:

    - geometric mean longitude: where the sun would appear in a simplified,
      perfectly regular orbit.
    - geometric mean anomaly: how far Earth is through its elliptical orbit.
    - eccentricity: how non-circular Earth's orbit is at this epoch.
    - equation of center: the correction from the mean orbit to the true orbit.
    - apparent longitude: the sun's true longitude corrected for small nutation
      effects in Earth's axis.
    - obliquity correction: Earth's axial tilt for this epoch.

    Declination is the solar equivalent of latitude. It tells us how far north
    or south of the celestial equator the sun appears. The equation of time
    tells us how many minutes apparent solar time differs from mean clock time.
    Those two values are the only solar terms needed for sunrise, sunset, and
    twilight hour-angle calculations.
    """
    julian_century = _julian_century(_julian_day(date=date, utc_minutes=utc_minutes))
    geometric_mean_longitude = (
        280.46646 + julian_century * (36000.76983 + julian_century * 0.0003032)
    ) % 360.0
    geometric_mean_anomaly = 357.52911 + julian_century * (
        35999.05029 - 0.0001537 * julian_century
    )
    eccentricity = 0.016708634 - julian_century * (
        0.000042037 + 0.0000001267 * julian_century
    )
    anomaly_radians = math.radians(geometric_mean_anomaly)
    equation_of_center = (
        math.sin(anomaly_radians)
        * (1.914602 - julian_century * (0.004817 + 0.000014 * julian_century))
        + math.sin(2.0 * anomaly_radians) * (0.019993 - 0.000101 * julian_century)
        + math.sin(3.0 * anomaly_radians) * 0.000289
    )
    true_longitude = geometric_mean_longitude + equation_of_center
    omega = 125.04 - 1934.136 * julian_century
    apparent_longitude = (
        true_longitude - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    )

    mean_obliquity_seconds = 21.448 - julian_century * (
        46.815 + julian_century * (0.00059 - julian_century * 0.001813)
    )
    mean_obliquity = 23.0 + (26.0 + (mean_obliquity_seconds / 60.0)) / 60.0
    obliquity_correction = mean_obliquity + 0.00256 * math.cos(math.radians(omega))

    obliquity_radians = math.radians(obliquity_correction)
    apparent_longitude_radians = math.radians(apparent_longitude)
    solar_declination = math.asin(
        math.sin(obliquity_radians) * math.sin(apparent_longitude_radians)
    )

    y = math.tan(obliquity_radians / 2.0)
    y *= y
    longitude_radians = math.radians(geometric_mean_longitude)
    equation_of_time = 4.0 * math.degrees(
        y * math.sin(2.0 * longitude_radians)
        - 2.0 * eccentricity * math.sin(anomaly_radians)
        + 4.0
        * eccentricity
        * y
        * math.sin(anomaly_radians)
        * math.cos(2.0 * longitude_radians)
        - 0.5 * y * y * math.sin(4.0 * longitude_radians)
        - 1.25 * eccentricity * eccentricity * math.sin(2.0 * anomaly_radians)
    )
    return equation_of_time, solar_declination


def _julian_day(*, date: dt.date, utc_minutes: float) -> float:
    """Return the Julian day for a Gregorian date and UTC minutes.

    Julian days count continuous days from an ancient astronomical epoch. They
    are useful here because solar formulae need a smooth time value rather than
    calendar fields with month lengths and leap years. The .5 offset is
    part of the astronomical convention: Julian days begin at noon UTC, so
    midnight sits at a half-day boundary.
    """
    year = date.year
    month = date.month
    day = date.day
    if month <= 2:
        year -= 1
        month += 12
    century = math.floor(year / 100)
    gregorian_correction = 2 - century + math.floor(century / 4)
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + gregorian_correction
        - 1524.5
        + (utc_minutes / DAY_MINUTES)
    )


def _julian_century(julian_day: float) -> float:
    """Return Julian centuries since J2000.0.

    J2000.0 is Julian day 2451545.0, noon UTC on 2000-01-01. Dividing days
    since that epoch by 36525 expresses time in Julian centuries, which is the
    unit used by the NOAA/Meeus polynomial coefficients above.
    """
    return (julian_day - 2451545.0) / 36525.0
