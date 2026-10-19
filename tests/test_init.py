"""Setup, restart without API calls, and the first poll."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.solaredge_v2.api.models import Series, parse_telemetry
from custom_components.solaredge_v2.const import DOMAIN
from custom_components.solaredge_v2.state import SiteState, Totals, summarize_inverters
from tests.helpers import load

API = "https://monitoringapi.solaredge.com/v2/sites/1234567"
NOW = datetime.fromisoformat("2026-10-16T12:34:00+02:00")
TODAY = date(2026, 10, 16)


def _entry(options: dict[str, Any] | None = None, **extra: Any) -> MockConfigEntry:
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
        options=options or {"billing_day": 16, "billing_day_confirmed": True},
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
    # The newest interval (12:30-12:45) was still filling at the cached poll (NOW - 5 min),
    # so the data reaches up to that poll, not to the interval start.
    as_of = registry.async_get_entity_id("sensor", DOMAIN, "1234567_data_time")
    state = hass.states.get(as_of)
    assert dt_util.parse_datetime(state.state) == min(
        datetime.fromisoformat("2026-10-16T12:45:00+02:00"), last_poll
    )
    assert state.attributes["interval_start"] == "2026-10-16T12:30:00+02:00"


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


async def test_takes_over_builtin_sensors_with_their_entity_ids(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    core = MockConfigEntry(domain="solaredge", data={"site_id": "1234567", "api_key": "x"})
    core.add_to_hass(hass)
    registry = er.async_get(hass)
    for key in ("current_power", "lifetime_energy"):
        registry.async_get_or_create(
            "sensor",
            "solaredge",
            f"1234567_{key}",
            config_entry=core,
            suggested_object_id=f"solaredge_{key}",
            unit_of_measurement="W" if key == "current_power" else "Wh",
        )
    entry = _entry(migrate_from=core.entry_id)
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": _cached_state(NOW - timedelta(minutes=5)),
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    lifetime = registry.async_get("sensor.solaredge_lifetime_energy")
    assert lifetime.platform == DOMAIN
    assert lifetime.config_entry_id == entry.entry_id
    assert registry.async_get("sensor.solaredge_current_power").platform == DOMAIN
    # Same entity_id, still in Wh, value from the new integration.
    state = hass.states.get("sensor.solaredge_lifetime_energy")
    assert state.attributes["unit_of_measurement"] == "Wh"
    assert float(state.state) == 19_711_733.0 + 4_943_860.0 + 8_843.0
    assert hass.states.get("sensor.solaredge_current_power").state == "2427.0"
    assert hass.config_entries.async_get_entry(core.entry_id).disabled_by is not None
    assert "migrate_from" not in entry.data
    assert aioclient_mock.call_count == 0


async def test_broken_cache_does_not_block_setup(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    entry = _entry()
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "token": "garbage",
            "ledger": {"cycle_start": "not-a-date", "used": 5},
            "state": {"today": {"day": "nope"}},
        },
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.ledger.used == 0
    assert coordinator.state.today is None
    assert coordinator._token.refresh_token == "rt"  # from the entry, not the broken cache
    assert aioclient_mock.call_count == 0


async def test_request_is_counted_before_the_answer(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    aioclient_mock.get(f"{API}/inverters/telemetry", exc=TimeoutError())
    entry = _entry(initial={"site": load("site"), "calls": 1})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_poll_now()
    await hass.async_block_till_done()
    # The timed-out call may have reached SolarEdge, so it costs a credit too.
    assert coordinator.ledger.used == 2
    assert not coordinator.last_update_success
    saved = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert saved["ledger"]["used"] == 2


async def test_update_entity_does_not_spend_credits(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    entry = _entry()
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": _cached_state(NOW - timedelta(minutes=5)),
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert await async_setup_component(hass, "homeassistant", {})
    await hass.async_block_till_done()
    power = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "1234567_current_power")
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": power}, blocking=True
    )
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 0
    assert entry.runtime_data.ledger.used == 42


async def test_unconfirmed_billing_day_is_fixed_in_repairs(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Entries made before the setup asked for the day get a fixable issue."""
    freezer.move_to(NOW)
    assert await async_setup_component(hass, "repairs", {})
    entry = _entry(options={"billing_day": 18})
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": _cached_state(NOW - timedelta(minutes=5)),
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue_id = f"billing_day_unconfirmed_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders == {"day": "18"}

    client = await hass_client()
    resp = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert resp.status == 200
    flow = await resp.json()
    assert flow["step_id"] == "init"
    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={"billing_day": 16})
    assert resp.status == 200
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()

    assert entry.options["billing_day"] == 16
    assert entry.options["billing_day_confirmed"] is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert aioclient_mock.call_count == 0
