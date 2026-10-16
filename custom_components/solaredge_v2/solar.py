"""Sunrise/sunset without third-party packages (NOAA sunrise equation, ~1 min accuracy).

The integration only polls while the sun is up, so this decides when polling
starts and stops each day and how long the longest day of the year is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
import math

J2000 = 2451545.0
_J2000_DT = datetime(2000, 1, 1, 12, tzinfo=UTC)

# Polling starts a little after sunrise (nothing to read before) and makes one
# final read after sunset so the day's total is complete.
START_OFFSET = timedelta(minutes=10)
FINAL_OFFSET = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class SunTimes:
    """Sunrise and sunset in UTC; both None during polar night."""

    sunrise: datetime | None
    sunset: datetime | None


def _from_julian(jd: float) -> datetime:
    return _J2000_DT + timedelta(days=jd - J2000)


@lru_cache(maxsize=1024)
def sun_times(latitude: float, longitude: float, day: date) -> SunTimes:
    """Sunrise and sunset for a calendar day at a location (longitude east positive)."""
    jd_noon = day.toordinal() + 1721424.5 + 0.5
    n = round(jd_noon - J2000)
    j_star = n - longitude / 360.0
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    c = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    j_transit = J2000 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    sin_d = math.sin(lam) * math.sin(math.radians(23.4397))
    cos_d = math.cos(math.asin(sin_d))
    phi = math.radians(latitude)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * sin_d) / (math.cos(phi) * cos_d)
    if cos_w > 1:  # polar night
        return SunTimes(None, None)
    if cos_w < -1:  # midnight sun: treat the whole day as daylight
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        return SunTimes(start, start + timedelta(days=1) - timedelta(minutes=1))
    w = math.degrees(math.acos(cos_w))
    return SunTimes(_from_julian(j_transit - w / 360), _from_julian(j_transit + w / 360))


@dataclass(frozen=True, slots=True)
class PollWindow:
    """When polling is allowed on one day (UTC)."""

    start: datetime | None
    end: datetime | None

    @property
    def minutes(self) -> float:
        """Length of the window in minutes (0 during polar night)."""
        if self.start is None or self.end is None:
            return 0.0
        return max(0.0, (self.end - self.start).total_seconds() / 60)

    def contains(self, moment: datetime) -> bool:
        """Whether a moment falls inside the window."""
        return self.start is not None and self.end is not None and self.start <= moment <= self.end

    def remaining_minutes(self, moment: datetime) -> float:
        """Minutes of the window still ahead of a moment."""
        if self.start is None or self.end is None or moment >= self.end:
            return 0.0
        return (self.end - max(moment, self.start)).total_seconds() / 60


def poll_window(latitude: float, longitude: float, day: date) -> PollWindow:
    """The polling window for a calendar day."""
    times = sun_times(latitude, longitude, day)
    if times.sunrise is None or times.sunset is None:
        return PollWindow(None, None)
    return PollWindow(times.sunrise + START_OFFSET, times.sunset + FINAL_OFFSET)


@lru_cache(maxsize=16)
def longest_window_minutes(latitude: float, longitude: float, year: int) -> float:
    """The longest polling window of the year (21 June in Poland)."""
    first = date(year, 1, 1)
    return max(
        poll_window(latitude, longitude, first + timedelta(days=i)).minutes for i in range(366)
    )
