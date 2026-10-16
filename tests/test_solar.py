"""Sunrise/sunset maths."""

from datetime import UTC, date, datetime

from custom_components.solaredge_v2.solar import (
    longest_window_minutes,
    poll_window,
    sun_times,
)

WARSAW = (52.23, 21.01)


def _hm(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%M")


def test_warsaw_summer_solstice_within_two_minutes() -> None:
    times = sun_times(*WARSAW, date(2026, 6, 21))
    # Reference (timeanddate.com): sunrise 04:14, sunset 21:01 CEST
    assert _hm(times.sunrise) in {"02:13", "02:14", "02:15", "02:16"}
    assert _hm(times.sunset) in {"18:59", "19:00", "19:01", "19:02"}


def test_warsaw_october() -> None:
    times = sun_times(*WARSAW, date(2026, 10, 16))
    # about 07:02 / 17:41 CEST
    assert _hm(times.sunrise) in {"05:00", "05:01", "05:02", "05:03", "05:04"}
    assert _hm(times.sunset) in {"15:39", "15:40", "15:41", "15:42"}


def test_poll_window_adds_offsets() -> None:
    times = sun_times(*WARSAW, date(2026, 10, 16))
    window = poll_window(*WARSAW, date(2026, 10, 16))
    assert (window.start - times.sunrise).total_seconds() == 600
    assert (window.end - times.sunset).total_seconds() == 1800
    assert window.contains(datetime(2026, 10, 16, 10, 0, tzinfo=UTC))
    assert not window.contains(datetime(2026, 10, 16, 20, 0, tzinfo=UTC))
    assert window.remaining_minutes(datetime(2026, 10, 16, 23, 0, tzinfo=UTC)) == 0
    assert window.remaining_minutes(datetime(2026, 10, 16, 0, 0, tzinfo=UTC)) == window.minutes


def test_longest_window_is_june() -> None:
    longest = longest_window_minutes(*WARSAW, 2026)
    assert 1010 < longest < 1040
    assert longest == poll_window(*WARSAW, date(2026, 6, 21)).minutes or longest > poll_window(
        *WARSAW, date(2026, 12, 21)
    ).minutes


def test_polar_cases() -> None:
    svalbard = (78.2, 15.6)
    assert sun_times(*svalbard, date(2026, 12, 21)).sunrise is None
    assert poll_window(*svalbard, date(2026, 12, 21)).minutes == 0
    day = sun_times(*svalbard, date(2026, 6, 21))
    assert (day.sunset - day.sunrise).total_seconds() > 23 * 3600
