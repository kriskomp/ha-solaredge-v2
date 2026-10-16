"""Setup, restart without API calls, and the first poll."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.solaredge_v2.api.models import Series, parse_telemetry
from custom_components.solaredge_v2.const import DOMAIN
from custom_components.solaredge_v2.state import SiteState, Totals, summarize_inverters
from tests.helpers import load

API = "https://monitoringapi.solaredge.com/v2/sites/1234567"
NOW = datetime.fromisoformat("2026-10-16T12:34:00+02:00")
TODAY = date(2026, 10, 16)


def _entry(**extra: Any) -> MockConfigEntry:
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
            **extra,
        },
        options={"billing_day": 16},
    )


def _cached_state(last_poll: datetime) -> dict[str, Any]:
    totals = Totals()
    totals.set_from_month_series(Series.from_api(load("energy_month")), TODAY)
    totals.set_prior_years(19_711_733.0, TODAY)
    state = SiteState(
        site=load("site"),
        devices=load("devices"),
        site_fetched=last_poll,
        today=summarize_inverters(parse_telemetry(load("inverters_telemetry"), "inverters"), TODAY),
        totals=totals,
        last_poll=last_poll,
        last_success=last_poll,
    )
    return {
        "token": {"access_token": "at", "refresh_token": "rt", "expires_at": 0, "issued_at": 0},
        "ledger": {"cycle_start": "2026-10-16", "used": 42, "exhausted": False},
        "state": state.as_dict(),
    }


async def test_restart_uses_cache_and_waits_for_the_next_slot(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    entry = _entry()
    entry.add_to_hass(hass)
    last_poll = NOW - timedelta(minutes=5)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": _cached_state(last_poll),
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # No API call at all on start-up.
    assert aioclient_mock.call_count == 0
    registry = er.async_get(hass)
    power = registry.async_get_entity_id("sensor", DOMAIN, "1234567_current_power")
    energy = registry.async_get_entity_id("sensor", DOMAIN, "1234567_energy_today")
    month = registry.async_get_entity_id("sensor", DOMAIN, "1234567_energy_this_month")
    assert hass.states.get(power).state == "2427.0"
    assert float(hass.states.get(energy).state) == 8.843  # shown in kWh
    assert round(float(hass.states.get(month).state), 3) == round((184688 + 8843) / 1000, 3)

    coordinator = entry.runtime_data
    assert coordinator.ledger.used == 42
    assert coordinator.next_poll == last_poll + timedelta(minutes=coordinator.interval_minutes)
    voltage = registry.async_get_entity_id("sensor", DOMAIN, "7E1234AB-01_ac_voltage")
    assert round(float(hass.states.get(voltage).state), 1) == 243.4


async def test_first_poll_after_install(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW)
    aioclient_mock.get(f"{API}/inverters/telemetry", json=load("inverters_telemetry"))
    aioclient_mock.get(f"{API}/energy", json=load("energy_month"))
    entry = _entry(initial={"site": load("site"), "calls": 1})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    # Fresh install: the first poll is due immediately.
    await coordinator.async_poll_now()
    await hass.async_block_till_done()
    paths = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert any("/inverters/telemetry" in p and "QUARTER_HOUR" in p for p in paths)
    assert all("Z" in p for p in paths if "from=" in p)  # Instants, not local time
    assert coordinator.state.today.energy_wh == 8843.0
    # setup call + telemetry + month baseline + previous years
    assert coordinator.ledger.used >= 4
    assert coordinator.state.totals.base_day == TODAY
