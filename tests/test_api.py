"""API helpers that need no network."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from custom_components.solaredge_v2.api import instant, parse_redirect, window
from custom_components.solaredge_v2.api.oauth import RedirectParseError, authorize_url


def test_instant_is_utc_with_z() -> None:
    # The live API rejects offset-free local time with HTTP 400.
    warsaw = timezone(timedelta(hours=2))
    assert instant(datetime(2026, 10, 16, 0, 0, tzinfo=warsaw)) == "2026-10-15T22:00:00Z"
    assert window(
        datetime(2026, 10, 16, 0, 0, tzinfo=UTC), datetime(2026, 10, 16, 1, 0, tzinfo=UTC)
    ) == [("from", "2026-10-16T00:00:00Z"), ("to", "2026-10-16T01:00:00Z")]
    with pytest.raises(ValueError):
        instant(datetime(2026, 10, 16))


def test_parse_redirect_variants() -> None:
    assert parse_redirect("http://localhost/callback?code=abc&site_id=123") == ("abc", "123")
    assert parse_redirect("  http://localhost/callback?code=abc  ") == ("abc", None)
    assert parse_redirect("code=abc&site_id=9") == ("abc", "9")
    assert parse_redirect("abc") == ("abc", None)
    with pytest.raises(RedirectParseError):
        parse_redirect("http://localhost/callback?error=access_denied")
    with pytest.raises(RedirectParseError):
        parse_redirect("")


def test_authorize_url_has_scopes_and_duration() -> None:
    url = authorize_url("cid", "http://localhost/callback", "st")
    assert url.startswith("https://connect.solaredge.com/authorize?")
    assert "scope=SITE_DATA+DEVICE_DATA" in url
    assert "access_duration=24" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%2Fcallback" in url
