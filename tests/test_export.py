"""The export_energy action: daily energy from the API into one file, resumed on repeat."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.solaredge_v2.const import DOMAIN
from tests.helpers import load

API = "https://monitoringapi.solaredge.com/v2/sites/1234567"
NOW = datetime.fromisoformat("2026-10-16T12:34:00+02:00")


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="1234567",
        title="Example site",
        data={
            "client_id": "cid",
            "client_secret": "secret",
            "redirect_uri": "http://localhost/callback",
            "site_id": "1234567",
            "token": {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_at": NOW.timestamp() + 7000,
                "issued_at": NOW.timestamp() - 200,
            },
            "granted_at": "2026-10-16T06:51:00+00:00",
            "initial": {"site": load("site"), "calls": 1},
        },
        options={"billing_day": 16, "billing_day_confirmed": True},
    )


def _days(first: date, count: int, wh: float = 1000.0) -> dict[str, Any]:
    """A DAY answer of the energy endpoint (summer time, +02:00)."""
    return {
        "unit": "WH",
        "resolution": "DAY",
        "values": [
            {
                "timestamp": f"{first + timedelta(days=i)}T00:00:00+02:00",
                "value": wh + i,
            }
            for i in range(count)
        ],
    }


async def _setup(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, tmp_path: Path
) -> MockConfigEntry:
    freezer.move_to(NOW)
    hass.config.config_dir = str(tmp_path)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _export(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    return await hass.services.async_call(
        DOMAIN, "export_energy", data, blocking=True, return_response=True
    )


async def test_export_returns_daily_values_and_counts_the_call(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
) -> None:
    entry = await _setup(hass, freezer, tmp_path)
    aioclient_mock.get(f"{API}/energy", json=_days(date(2026, 5, 19), 3))
    used_before = entry.runtime_data.ledger.used

    result = await _export(hass, start_date="2026-05-19", end_date="2026-05-21")

    assert result["unit"] == "Wh"
    assert result["calls"] == 1
    assert result["errors"] == []
    assert result["values"] == [
        {"date": "2026-05-19", "wh": 1000.0},
        {"date": "2026-05-20", "wh": 1001.0},
        {"date": "2026-05-21", "wh": 1002.0},
    ]
    assert result["total_wh"] == 3003.0
    saved = json.loads((tmp_path / "solaredge_v2_export.json").read_text())
    assert saved["site_id"] == "1234567" and saved["unit"] == "Wh"
    assert saved["values"] == result["values"]
    assert entry.runtime_data.ledger.used == used_before + 1
    assert result["credits_used"] == used_before + 1
    query = aioclient_mock.mock_calls[-1][1].query
    assert query["resolution"] == "DAY" and query["unit"] == "WH"
    # Local midnights sent as Instants: 19 May 00:00 +02:00 to 22 May 00:00 +02:00.
    assert query["from"] == "2026-05-18T22:00:00Z"
    assert query["to"] == "2026-05-21T22:00:00Z"


async def test_export_splits_long_ranges_and_drops_overlap(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
) -> None:
    entry = await _setup(hass, freezer, tmp_path)
    # Every call answers with all 40 days; each block keeps only its own days.
    aioclient_mock.get(f"{API}/energy", json=_days(date(2026, 5, 1), 40))
    used_before = entry.runtime_data.ledger.used

    result = await _export(hass, start_date="2026-05-01", end_date="2026-06-09", days_per_call=31)

    assert result["calls"] == 2
    assert len(result["values"]) == 40
    assert [v["date"] for v in result["values"]][:2] == ["2026-05-01", "2026-05-02"]
    assert result["values"][-1]["date"] == "2026-06-09"
    assert entry.runtime_data.ledger.used == used_before + 2


async def test_export_refuses_when_credits_are_short(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
) -> None:
    entry = await _setup(hass, freezer, tmp_path)
    entry.runtime_data.ledger.used = entry.runtime_data.options.monthly_credits - 1
    with pytest.raises(ServiceValidationError):
        await _export(hass, start_date="2026-01-01", end_date="2026-06-30", days_per_call=31)
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-06-02", "2026-06-01"), ("2026-10-10", "2026-10-17"), ("2010-01-01", "2026-10-16")],
)
async def test_export_rejects_bad_ranges(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
    start: str,
    end: str,
) -> None:
    await _setup(hass, freezer, tmp_path)
    with pytest.raises(ServiceValidationError):
        await _export(hass, start_date=start, end_date=end)
    assert aioclient_mock.call_count == 0


async def test_export_failure_is_reported_and_still_counted(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
) -> None:
    entry = await _setup(hass, freezer, tmp_path)
    aioclient_mock.get(
        f"{API}/energy", status=400, json={"detail": "Window too long for DAY resolution"}
    )
    used_before = entry.runtime_data.ledger.used

    with pytest.raises(HomeAssistantError, match="Window too long"):
        await _export(hass, start_date="2026-05-01", end_date="2026-06-09", days_per_call=31)

    # Stops at the first failed block instead of spending a credit on every one.
    assert aioclient_mock.call_count == 1
    assert entry.runtime_data.ledger.used == used_before + 1
    assert not (tmp_path / "solaredge_v2_export.json").exists()


async def test_export_resumes_from_the_file_and_appends(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
) -> None:
    entry = await _setup(hass, freezer, tmp_path)
    aioclient_mock.get(f"{API}/energy", json=_days(date(2026, 5, 1), 31))
    await _export(hass, start_date="2026-05-01", end_date="2026-05-31")
    assert aioclient_mock.call_count == 1

    # Same range again: everything is in the file, no call and no credit.
    used = entry.runtime_data.ledger.used
    again = await _export(hass, start_date="2026-05-01", end_date="2026-05-31")
    assert aioclient_mock.call_count == 1
    assert again["calls"] == 0 and again["days_missing"] == 0
    assert len(again["values"]) == 31
    assert entry.runtime_data.ledger.used == used

    # A longer range asks only for the days that are not in the file yet.
    aioclient_mock.get(f"{API}/energy", json=_days(date(2026, 6, 1), 9))
    longer = await _export(hass, start_date="2026-05-01", end_date="2026-06-09")
    assert aioclient_mock.call_count == 2
    assert longer["calls"] == 1 and longer["days_fetched"] == 9
    assert aioclient_mock.mock_calls[-1][1].query["from"] == "2026-05-31T22:00:00Z"
    saved = json.loads((tmp_path / "solaredge_v2_export.json").read_text())
    assert len(saved["values"]) == 40
    assert saved["values"][0]["date"] == "2026-05-01"


async def test_export_never_stores_today(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
) -> None:
    await _setup(hass, freezer, tmp_path)
    aioclient_mock.get(f"{API}/energy", json=_days(date(2026, 10, 14), 3))

    result = await _export(hass, start_date="2026-10-14", end_date="2026-10-16")

    assert result["end"] == "2026-10-15"
    assert [v["date"] for v in result["values"]] == ["2026-10-14", "2026-10-15"]


async def test_export_refuses_a_file_of_another_site(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    tmp_path: Path,
) -> None:
    await _setup(hass, freezer, tmp_path)
    (tmp_path / "solaredge_v2_export.json").write_text(json.dumps({"site_id": "999", "values": []}))
    with pytest.raises(ServiceValidationError):
        await _export(hass, start_date="2026-05-01", end_date="2026-05-02")
    assert aioclient_mock.call_count == 0
