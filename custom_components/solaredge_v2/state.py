"""Everything the integration knows about a site, serializable to .storage.

Restoring this after a restart lets every sensor show its last value without
calling the API (no credits spent on restarts or reloads).

Energy totals are built so that only ONE extra call per day is needed:

* today        = sum of the 15-minute inverter energy since local midnight
* this month   = month baseline (up to last midnight) + today
* this year    = year baseline (up to last midnight) + today
* lifetime     = previous years + year baseline + today

Baselines come from ``/energy?resolution=MONTH`` (1 Jan -> last midnight) once
a day; previous years from ``resolution=YEAR`` once a year. At midnight the
finished day is added to the baselines locally so the sensors stay correct
even before the next API call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .api.models import Series, Telemetry, parse_datetime, to_watt_hours, to_watts

INVERTER_METRICS = ("power", "energy", "voltage", "current", "frequency")


@dataclass(slots=True)
class InverterReading:
    """Latest 15-minute values of one inverter."""

    power_w: float | None = None
    energy_today_wh: float | None = None
    voltage: float | None = None
    current: float | None = None
    frequency: float | None = None
    at: datetime | None = None


@dataclass(slots=True)
class DayReading:
    """Today's production as read from the inverter telemetry."""

    day: date
    energy_wh: float = 0.0
    power_w: float | None = None
    power_at: datetime | None = None
    peak_w: float | None = None
    peak_at: datetime | None = None
    inverters: dict[str, InverterReading] = field(default_factory=dict)


def _latest(series: Series | None) -> tuple[float | None, datetime | None]:
    if series is None:
        return None, None
    for sample in reversed(series.samples):
        if sample.value is not None:
            return sample.value, sample.timestamp
    return None, None


def summarize_inverters(telemetry: Telemetry, day: date) -> DayReading:
    """Turn one QUARTER_HOUR inverter telemetry body into today's figures."""
    reading = DayReading(day=day)
    power_by_slot: dict[datetime, float] = {}
    energy_total = 0.0
    for serial, metrics in telemetry.items():
        inv = InverterReading()
        power = metrics.get("power")
        energy = metrics.get("energy")
        if power is not None:
            value, at = _latest(power)
            inv.power_w = to_watts(value, power.unit)
            inv.at = at
            for sample in power.samples:
                if sample.value is None or sample.timestamp is None:
                    continue
                watts = to_watts(sample.value, power.unit) or 0.0
                power_by_slot[sample.timestamp] = power_by_slot.get(sample.timestamp, 0.0) + watts
        if energy is not None:
            inv.energy_today_wh = energy.total_watt_hours or 0.0
            energy_total += inv.energy_today_wh
        for metric, attr in (("voltage", "voltage"), ("current", "current"), ("frequency", "frequency")):
            value, _ = _latest(metrics.get(metric))
            setattr(inv, attr, value)
        reading.inverters[serial] = inv
    reading.energy_wh = energy_total
    if power_by_slot:
        last = max(power_by_slot)
        reading.power_w = power_by_slot[last]
        reading.power_at = last
        peak_at = max(power_by_slot, key=lambda k: power_by_slot[k])
        reading.peak_w = power_by_slot[peak_at]
        reading.peak_at = peak_at
    return reading


@dataclass(slots=True)
class Totals:
    """Baselines for month, year and lifetime energy (Wh)."""

    base_day: date | None = None  # baselines are valid up to 00:00 of this day
    month_wh: float | None = None
    year_wh: float | None = None
    prior_years_wh: float | None = None
    prior_years_for: int | None = None  # calendar year the prior sum was computed for

    def needs_baseline(self, today: date) -> bool:
        """True when the month/year baselines must be fetched today."""
        return self.base_day != today

    def needs_prior_years(self, today: date) -> bool:
        """True when the sum of earlier years must be fetched."""
        return self.prior_years_for != today.year

    def set_from_month_series(self, series: Series, today: date) -> None:
        """Apply ``/energy?resolution=MONTH`` from 1 Jan to today's midnight."""
        month = 0.0
        year = 0.0
        for sample in series.samples:
            wh = to_watt_hours(sample.value, series.unit)
            if wh is None or sample.timestamp is None:
                continue
            year += wh
            if sample.timestamp.year == today.year and sample.timestamp.month == today.month:
                month += wh
        self.month_wh = month
        self.year_wh = year
        self.base_day = today

    def set_empty_baseline(self, today: date) -> None:
        """1 January: nothing produced yet this year before midnight."""
        self.month_wh = 0.0
        self.year_wh = 0.0
        self.base_day = today

    def set_prior_years(self, total_wh: float, today: date) -> None:
        """Apply the sum of every year before the current one."""
        self.prior_years_wh = total_wh
        self.prior_years_for = today.year

    def roll(self, finished_day: date, finished_wh: float, new_day: date) -> None:
        """Move the baselines past midnight using the finished day's energy."""
        if self.base_day != finished_day or (new_day - finished_day).days != 1:
            # A gap (HA was off): keep the values but let the next poll resync.
            return
        if self.month_wh is not None and self.year_wh is not None:
            if new_day.year != finished_day.year:
                if self.prior_years_wh is not None:
                    self.prior_years_wh += self.year_wh + finished_wh
                    self.prior_years_for = new_day.year
                self.month_wh = 0.0
                self.year_wh = 0.0
            elif new_day.month != finished_day.month:
                self.month_wh = 0.0
                self.year_wh += finished_wh
            else:
                self.month_wh += finished_wh
                self.year_wh += finished_wh
            self.base_day = new_day

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "base_day": self.base_day.isoformat() if self.base_day else None,
            "month_wh": self.month_wh,
            "year_wh": self.year_wh,
            "prior_years_wh": self.prior_years_wh,
            "prior_years_for": self.prior_years_for,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Totals:
        """Restore from storage."""
        data = data or {}
        base = data.get("base_day")
        return cls(
            base_day=date.fromisoformat(base) if base else None,
            month_wh=data.get("month_wh"),
            year_wh=data.get("year_wh"),
            prior_years_wh=data.get("prior_years_wh"),
            prior_years_for=data.get("prior_years_for"),
        )


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _day_as_dict(day: DayReading) -> dict[str, Any]:
    return {
        "day": day.day.isoformat(),
        "energy_wh": day.energy_wh,
        "power_w": day.power_w,
        "power_at": _dt(day.power_at),
        "peak_w": day.peak_w,
        "peak_at": _dt(day.peak_at),
        "inverters": {
            serial: {
                "power_w": inv.power_w,
                "energy_today_wh": inv.energy_today_wh,
                "voltage": inv.voltage,
                "current": inv.current,
                "frequency": inv.frequency,
                "at": _dt(inv.at),
            }
            for serial, inv in day.inverters.items()
        },
    }


def _day_from_dict(data: dict[str, Any]) -> DayReading:
    return DayReading(
        day=date.fromisoformat(data["day"]),
        energy_wh=float(data.get("energy_wh") or 0.0),
        power_w=data.get("power_w"),
        power_at=parse_datetime(data.get("power_at")),
        peak_w=data.get("peak_w"),
        peak_at=parse_datetime(data.get("peak_at")),
        inverters={
            serial: InverterReading(
                power_w=v.get("power_w"),
                energy_today_wh=v.get("energy_today_wh"),
                voltage=v.get("voltage"),
                current=v.get("current"),
                frequency=v.get("frequency"),
                at=parse_datetime(v.get("at")),
            )
            for serial, v in (data.get("inverters") or {}).items()
        },
    )


@dataclass(slots=True)
class SiteState:
    """The cached state of one site."""

    site: dict[str, Any] = field(default_factory=dict)  # raw /sites/{id}
    devices: list[dict[str, Any]] = field(default_factory=list)  # raw /devices
    site_fetched: datetime | None = None
    today: DayReading | None = None
    totals: Totals = field(default_factory=Totals)
    alerts_open: int | None = None
    alerts_fetched: date | None = None
    overview: dict[str, Any] | None = None  # raw /overview (optional group)
    storage: dict[str, Any] | None = None  # raw storage telemetry (optional group)
    last_poll: datetime | None = None
    last_success: datetime | None = None

    def roll_day(self, new_day: date) -> bool:
        """Midnight: fold the finished day into the baselines and start a new one."""
        if self.today is None or self.today.day >= new_day:
            return False
        self.totals.roll(self.today.day, self.today.energy_wh, new_day)
        self.today = DayReading(day=new_day, energy_wh=0.0, power_w=0.0)
        self.overview = None
        self.storage = None
        return True

    @property
    def energy_today_wh(self) -> float | None:
        """Today's production."""
        return self.today.energy_wh if self.today else None

    def _with_today(self, base: float | None, today: date) -> float | None:
        if base is None or self.totals.base_day != today:
            return None
        return base + (self.today.energy_wh if self.today and self.today.day == today else 0.0)

    def month_wh(self, today: date) -> float | None:
        """This month's production."""
        return self._with_today(self.totals.month_wh, today)

    def year_wh(self, today: date) -> float | None:
        """This year's production."""
        return self._with_today(self.totals.year_wh, today)

    def lifetime_wh(self, today: date) -> float | None:
        """Production since installation."""
        year = self.year_wh(today)
        if year is None or self.totals.prior_years_wh is None:
            return None
        return self.totals.prior_years_wh + year

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "site": self.site,
            "devices": self.devices,
            "site_fetched": _dt(self.site_fetched),
            "today": _day_as_dict(self.today) if self.today else None,
            "totals": self.totals.as_dict(),
            "alerts_open": self.alerts_open,
            "alerts_fetched": self.alerts_fetched.isoformat() if self.alerts_fetched else None,
            "overview": self.overview,
            "storage": self.storage,
            "last_poll": _dt(self.last_poll),
            "last_success": _dt(self.last_success),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SiteState:
        """Restore from storage."""
        data = data or {}
        alerts_day = data.get("alerts_fetched")
        return cls(
            site=data.get("site") or {},
            devices=data.get("devices") or [],
            site_fetched=parse_datetime(data.get("site_fetched")),
            today=_day_from_dict(data["today"]) if data.get("today") else None,
            totals=Totals.from_dict(data.get("totals")),
            alerts_open=data.get("alerts_open"),
            alerts_fetched=date.fromisoformat(alerts_day) if alerts_day else None,
            overview=data.get("overview"),
            storage=data.get("storage"),
            last_poll=parse_datetime(data.get("last_poll")),
            last_success=parse_datetime(data.get("last_success")),
        )
