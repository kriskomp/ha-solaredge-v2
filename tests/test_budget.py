"""Credit budget and interval planning."""

from datetime import date

from custom_components.solaredge_v2.budget import (
    MAX_INTERVAL_MINUTES,
    Ledger,
    adaptive_interval,
    cycle_bounds,
    fixed_interval,
    polls_in,
    projected_cycle_use,
)


def test_cycle_bounds_mid_month() -> None:
    assert cycle_bounds(date(2026, 10, 16), 16) == (date(2026, 10, 16), date(2026, 11, 16))
    assert cycle_bounds(date(2026, 10, 15), 16) == (date(2026, 9, 16), date(2026, 10, 16))
    assert cycle_bounds(date(2026, 12, 20), 16) == (date(2026, 12, 16), date(2027, 1, 16))


def test_cycle_bounds_short_months() -> None:
    assert cycle_bounds(date(2027, 2, 28), 31) == (date(2027, 2, 28), date(2027, 3, 31))
    assert cycle_bounds(date(2027, 2, 27), 31) == (date(2027, 1, 31), date(2027, 2, 28))


def test_ledger_rolls_and_restores() -> None:
    ledger = Ledger.from_dict(None, date(2026, 10, 16), 16)
    ledger.used = 40
    ledger.exhausted = True
    data = ledger.as_dict()
    same = Ledger.from_dict(data, date(2026, 11, 15), 16)
    assert same.used == 40 and same.exhausted
    new = Ledger.from_dict(data, date(2026, 11, 16), 16)
    assert new.used == 0 and not new.exhausted and new.cycle_start == date(2026, 11, 16)


def test_fixed_interval_free_tier_poland() -> None:
    minutes = fixed_interval(
        monthly_credits=2000,
        reserve_percent=10,
        calls_per_poll=1,
        daily_fixed_calls=1.3,
        longest_window_minutes=1026,
        min_interval=10,
    )
    assert 18 < minutes < 19.5
    # Two calls per poll halves the frequency.
    doubled = fixed_interval(
        monthly_credits=2000,
        reserve_percent=10,
        calls_per_poll=2,
        daily_fixed_calls=1.3,
        longest_window_minutes=1026,
        min_interval=10,
    )
    assert 36 < doubled < 40


def test_fixed_interval_respects_minimum_and_tiny_budget() -> None:
    assert (
        fixed_interval(
            monthly_credits=100000,
            reserve_percent=0,
            calls_per_poll=1,
            daily_fixed_calls=1,
            longest_window_minutes=1000,
            min_interval=10,
        )
        == 10
    )
    assert (
        fixed_interval(
            monthly_credits=30,
            reserve_percent=0,
            calls_per_poll=1,
            daily_fixed_calls=1,
            longest_window_minutes=1000,
            min_interval=10,
        )
        == MAX_INTERVAL_MINUTES
    )


def test_adaptive_interval_october() -> None:
    minutes = adaptive_interval(
        monthly_credits=2000,
        reserve_percent=10,
        used=50,
        calls_per_poll=1,
        daily_fixed_calls=1.3,
        remaining_today_minutes=300,
        future_window_minutes=[640.0] * 30,
        min_interval=10,
    )
    assert 10 < minutes < 13
    assert (
        adaptive_interval(
            monthly_credits=2000,
            reserve_percent=10,
            used=1795,
            calls_per_poll=1,
            daily_fixed_calls=1.3,
            remaining_today_minutes=300,
            future_window_minutes=[640.0] * 3,
            min_interval=10,
        )
        == MAX_INTERVAL_MINUTES
    )


def test_projection() -> None:
    assert polls_in(0, 20) == 0
    assert polls_in(100, 20) == 6
    assert (
        projected_cycle_use(
            used=10,
            interval=20,
            calls_per_poll=1,
            daily_fixed_calls=1,
            remaining_today_minutes=100,
            future_window_minutes=[100, 100],
        )
        == 10 + 18 + 2
    )
