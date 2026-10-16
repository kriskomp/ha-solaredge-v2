"""Monthly credit budget: billing cycle, local counter and poll interval planning.

The API exposes no monthly usage counter (only a per-minute one), so the
integration counts the calls it makes itself and can be corrected with the
``solaredge_v2.set_credits_used`` action from the number the developer portal
shows.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

MODE_FIXED = "fixed"
MODE_ADAPTIVE = "adaptive"
MAX_INTERVAL_MINUTES = 240.0


def _billing_date(year: int, month: int, billing_day: int) -> date:
    return date(year, month, min(billing_day, calendar.monthrange(year, month)[1]))


def _add_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def cycle_bounds(today: date, billing_day: int) -> tuple[date, date]:
    """(first day of the current cycle, first day of the next one)."""
    this = _billing_date(today.year, today.month, billing_day)
    if today >= this:
        start = this
    else:
        y, m = _add_month(today.year, today.month, -1)
        start = _billing_date(y, m, billing_day)
    y, m = _add_month(start.year, start.month, 1)
    return start, _billing_date(y, m, billing_day)


@dataclass(slots=True)
class Ledger:
    """Credits used in the current billing cycle."""

    cycle_start: date
    used: int = 0
    exhausted: bool = False

    def roll(self, today: date, billing_day: int) -> bool:
        """Start a new cycle when the billing day has passed. True when it reset."""
        start, _ = cycle_bounds(today, billing_day)
        if start != self.cycle_start:
            self.cycle_start = start
            self.used = 0
            self.exhausted = False
            return True
        return False

    def as_dict(self) -> dict:
        """Serialize for storage."""
        return {
            "cycle_start": self.cycle_start.isoformat(),
            "used": self.used,
            "exhausted": self.exhausted,
        }

    @classmethod
    def from_dict(cls, data: dict | None, today: date, billing_day: int) -> Ledger:
        """Restore from storage, or start a fresh cycle."""
        start, _ = cycle_bounds(today, billing_day)
        if not data:
            return cls(cycle_start=start)
        ledger = cls(
            cycle_start=date.fromisoformat(data["cycle_start"]),
            used=int(data.get("used", 0)),
            exhausted=bool(data.get("exhausted", False)),
        )
        ledger.roll(today, billing_day)
        return ledger


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fixed_interval(
    *,
    monthly_credits: int,
    reserve_percent: float,
    calls_per_poll: int,
    daily_fixed_calls: float,
    longest_window_minutes: float,
    min_interval: float,
) -> float:
    """One interval for the whole year, sized so the longest day still fits."""
    budget = monthly_credits * (1 - reserve_percent / 100)
    polls = (budget / 31 - daily_fixed_calls) / max(calls_per_poll, 1)
    if polls < 2:
        return MAX_INTERVAL_MINUTES
    return _clamp(longest_window_minutes / (polls - 1), min_interval, MAX_INTERVAL_MINUTES)


def adaptive_interval(
    *,
    monthly_credits: int,
    reserve_percent: float,
    used: int,
    calls_per_poll: int,
    daily_fixed_calls: float,
    remaining_today_minutes: float,
    future_window_minutes: list[float],
    min_interval: float,
) -> float:
    """Spread the credits left in this cycle over the daylight left in it."""
    remaining = monthly_credits * (1 - reserve_percent / 100) - used
    days = 1 + len(future_window_minutes)
    polls = (remaining - daily_fixed_calls * days) / max(calls_per_poll, 1)
    daylight = remaining_today_minutes + sum(future_window_minutes)
    if polls < 1 or daylight <= 0:
        return MAX_INTERVAL_MINUTES
    return _clamp(daylight / polls, min_interval, MAX_INTERVAL_MINUTES)


def polls_in(window_minutes: float, interval: float) -> int:
    """How many polls fit in a window (one at the start, one at the end)."""
    if window_minutes <= 0 or interval <= 0:
        return 0
    return int(window_minutes // interval) + 1


def projected_cycle_use(
    *,
    used: int,
    interval: float,
    calls_per_poll: int,
    daily_fixed_calls: float,
    remaining_today_minutes: float,
    future_window_minutes: list[float],
) -> int:
    """Credits the cycle will have used at its end if nothing changes."""
    polls = polls_in(remaining_today_minutes, interval) + sum(
        polls_in(m, interval) for m in future_window_minutes
    )
    return round(used + polls * calls_per_poll + daily_fixed_calls * len(future_window_minutes))
