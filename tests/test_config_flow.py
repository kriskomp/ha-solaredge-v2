"""Setup flow with a pasted redirect URL."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.solaredge_v2.const import (
    CONF_INITIAL,
    CONF_MIGRATE_FROM,
    CONF_SITE_ID,
    CONF_TOKEN,
    DOMAIN,
)
from tests.helpers import load

TOKEN_URL = "https://monitoringapi.solaredge.com/v2/oauth2/token"
SITE_URL = "https://monitoringapi.solaredge.com/v2/sites/1234567"


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"client_id": "cid", "client_secret": "secret", "redirect_uri": "http://localhost/callback"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authorize"
    assert "connect.solaredge.com/authorize" in result["description_placeholders"]["url"]
    return result["flow_id"]


async def test_full_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        TOKEN_URL,
        json={"access_token": "at", "refresh_token": "rt", "expires_in": 7200, "token_type": "bearer"},
    )
    aioclient_mock.get(SITE_URL, json=load("site"))
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"redirect_url": "http://localhost/callback?code=CODE&site_id=1234567"}
    )
    # The API does not report the credit cycle, so the setup asks for it.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "billing"
    result = await hass.config_entries.flow.async_configure(flow_id, {"billing_day": 16})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {"billing_day": 16, "billing_day_confirmed": True}
    assert result["title"] == "Example site"
    data = result["data"]
    assert data[CONF_SITE_ID] == "1234567"
    assert data[CONF_TOKEN]["refresh_token"] == "rt"
    assert data[CONF_INITIAL]["calls"] == 1
    # JSON body, as verified against the live API
    _, _, body, _ = aioclient_mock.mock_calls[0]
    assert body["grant_type"] == "authorization_code"
    assert body["client_secret"] == "secret"


async def test_bad_paste_and_rejected_code(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        TOKEN_URL, status=400, json={"error": "invalid_grant", "error_description": "expired"}
    )
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"redirect_url": "http://localhost/callback?error=access_denied"}
    )
    assert result["errors"] == {"redirect_url": "invalid_redirect"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"redirect_url": "http://localhost/callback?code=OLD&site_id=1234567"}
    )
    assert result["errors"] == {"base": "invalid_code"}


async def test_flow_offers_to_take_over_builtin_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    core = MockConfigEntry(domain="solaredge", data={"site_id": "1234567", "api_key": "x"})
    core.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        "solaredge",
        "1234567_lifetime_energy",
        config_entry=core,
        suggested_object_id="solaredge_lifetime_energy",
    )
    aioclient_mock.post(
        TOKEN_URL, json={"access_token": "at", "refresh_token": "rt", "expires_in": 7200}
    )
    aioclient_mock.get(SITE_URL, json=load("site"))
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"redirect_url": "http://localhost/callback?code=CODE&site_id=1234567"}
    )
    assert result["step_id"] == "billing"
    result = await hass.config_entries.flow.async_configure(flow_id, {"billing_day": 16})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "migrate"
    assert "sensor.solaredge_lifetime_energy" in result["description_placeholders"]["entities"]
    with patch("custom_components.solaredge_v2.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(flow_id, {"migrate": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MIGRATE_FROM] == core.entry_id


async def test_external_flow_catches_the_redirect(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    aioclient_mock.post(
        TOKEN_URL, json={"access_token": "at", "refresh_token": "rt", "expires_in": 7200}
    )
    aioclient_mock.get(SITE_URL, json=load("site"))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "client_id": "cid",
            "client_secret": "secret",
            "redirect_uri": "https://ha.example.com/api/solaredge_v2/callback",
        },
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert "redirect_uri=https%3A%2F%2Fha.example.com" in result["url"]

    client = await hass_client_no_auth()
    resp = await client.get("/api/solaredge_v2/callback?code=CODE&site_id=1234567")
    assert resp.status == 200
    assert "Gotowe" in await resp.text()

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["step_id"] == "billing"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"billing_day": 16})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["redirect_uri"] == "https://ha.example.com/api/solaredge_v2/callback"
    _, _, body, _ = aioclient_mock.mock_calls[0]
    assert body["code"] == "CODE"
    assert body["redirect_uri"] == "https://ha.example.com/api/solaredge_v2/callback"


async def test_callback_without_waiting_flow(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_client_no_auth()
    resp = await client.get("/api/solaredge_v2/callback?code=CODE&site_id=1")
    assert resp.status == 200
    assert "Brak oczekującej konfiguracji" in await resp.text()


async def test_reconfigure_switches_to_the_ha_callback(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1234567",
        data={
            "client_id": "cid",
            "client_secret": "secret",
            "redirect_uri": "http://localhost/callback",
            "site_id": "1234567",
            "token": {"access_token": "old", "refresh_token": "old", "expires_at": 0, "issued_at": 0},
            "granted_at": "2026-10-16T06:51:00+00:00",
        },
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(
        TOKEN_URL, json={"access_token": "at", "refresh_token": "rt", "expires_in": 7200}
    )
    aioclient_mock.get(SITE_URL, json=load("site"))
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    new_uri = "https://ha.example.com/api/solaredge_v2/callback"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"client_id": "cid", "client_secret": "secret", "redirect_uri": new_uri},
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    client = await hass_client_no_auth()
    resp = await client.get("/api/solaredge_v2/callback?code=CODE&site_id=1234567")
    assert resp.status == 200
    with patch("custom_components.solaredge_v2.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["redirect_uri"] == new_uri
    assert entry.data["token"]["refresh_token"] == "rt"


async def test_reauth_asks_for_the_redirect_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1234567",
        data={
            "client_id": "cid",
            "client_secret": "secret",
            "redirect_uri": "http://localhost/callback",
            "site_id": "1234567",
            "token": {"access_token": "old", "refresh_token": "old", "expires_at": 0, "issued_at": 0},
            "granted_at": "2026-10-16T06:51:00+00:00",
        },
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(
        TOKEN_URL, json={"access_token": "at", "refresh_token": "rt", "expires_in": 7200}
    )
    aioclient_mock.get(SITE_URL, json=load("site"))
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_redirect"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_uri": "http://localhost/callback"}
    )
    assert result["step_id"] == "reauth_confirm"
    with patch("custom_components.solaredge_v2.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": "http://localhost/callback?code=C&site_id=1234567"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"]["refresh_token"] == "rt"
