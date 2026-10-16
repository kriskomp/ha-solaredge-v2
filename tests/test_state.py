"""Turning recorded API responses into sensor values."""

from datetime import date

from custom_components.solaredge_v2.api.models import Series, parse_telemetry
from custom_components.solaredge_v2.state import SiteState, Totals, summarize_inverters
from tests.helpers import load

TODAY = date(2026, 10, 16)


def _reading():
    return summarize_inverters(parse_telemetry(load("inverters_telemetry"), "inverters"), TODAY)


def test_summarize_real_inverter_telemetry() -> None:
    reading = _reading()
    # Sum of the 15-minute energy buckets 00:00-12:30 recorded on 16.10.2026.
    assert reading.energy_wh == 8843.0
    assert reading.power_w == 2427.0
    assert reading.power_at.isoformat() == "2026-10-16T12:30:00+02:00"
    assert round(reading.peak_w, 1) == 3221.7
    assert reading.peak_at.isoformat() == "2026-10-16T10:45:00+02:00"
    inv = reading.inverters["7E1234AB-01"]
    assert round(inv.voltage, 2) == 243.41
    assert round(inv.current, 2) == 9.97
    assert round(inv.frequency, 2) == 49.97


def test_month_baseline_and_lifetime() -> None:
    totals = Totals()
    totals.set_from_month_series(Series.from_api(load("energy_month")), TODAY)
    assert totals.month_wh == 184688.0
    assert totals.year_wh == 4943860.0
    years = Series.from_api(load("energy_year"))
    prior = sum(s.value for s in years.samples if s.timestamp.year < 2026)
    totals.set_prior_years(prior, TODAY)
    state = SiteState(totals=totals, today=_reading())
    assert state.month_wh(TODAY) == 184688.0 + 8843.0
    assert state.year_wh(TODAY) == 4943860.0 + 8843.0
    assert state.lifetime_wh(TODAY) == prior + 4943860.0 + 8843.0
    # Stale baseline -> unknown instead of wrong.
    assert state.month_wh(date(2026, 10, 17)) is None


def test_midnight_rollover_within_month_and_across_month_and_year() -> None:
    totals = Totals(base_day=date(2026, 10, 16), month_wh=100.0, year_wh=1000.0,
                    prior_years_wh=5000.0, prior_years_for=2026)
    totals.roll(date(2026, 10, 16), 10.0, date(2026, 10, 17))
    assert (totals.month_wh, totals.year_wh, totals.base_day) == (110.0, 1010.0, date(2026, 10, 17))

    totals = Totals(base_day=date(2026, 10, 31), month_wh=100.0, year_wh=1000.0)
    totals.roll(date(2026, 10, 31), 10.0, date(2026, 11, 1))
    assert (totals.month_wh, totals.year_wh) == (0.0, 1010.0)

    totals = Totals(base_day=date(2026, 12, 31), month_wh=100.0, year_wh=1000.0,
                    prior_years_wh=5000.0, prior_years_for=2026)
    totals.roll(date(2026, 12, 31), 10.0, date(2027, 1, 1))
    assert (totals.month_wh, totals.year_wh, totals.prior_years_wh, totals.prior_years_for) == (
        0.0, 0.0, 6010.0, 2027)


def test_rollover_gap_keeps_values_and_forces_resync() -> None:
    totals = Totals(base_day=date(2026, 10, 14), month_wh=100.0, year_wh=1000.0)
    totals.roll(date(2026, 10, 14), 10.0, date(2026, 10, 16))
    assert totals.base_day == date(2026, 10, 14)
    assert totals.needs_baseline(date(2026, 10, 16))


def test_state_roundtrip_and_day_roll() -> None:
    totals = Totals(base_day=TODAY, month_wh=1.0, year_wh=2.0, prior_years_wh=3.0,
                    prior_years_for=2026)
    state = SiteState(site=load("site"), totals=totals, today=_reading(), alerts_open=0,
                      alerts_fetched=TODAY)
    copy = SiteState.from_dict(state.as_dict())
    assert copy.as_dict() == state.as_dict()
    assert copy.roll_day(date(2026, 10, 17))
    assert copy.today.energy_wh == 0.0 and copy.today.power_w == 0.0
    assert copy.totals.month_wh == 1.0 + 8843.0
    assert not copy.roll_day(date(2026, 10, 17))
