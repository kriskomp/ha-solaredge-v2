"""Diagnostics with every secret redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN
from .coordinator import SolarEdgeConfigEntry

TO_REDACT = {
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_TOKEN,
    "access_token",
    "refresh_token",
    "address",
    "address2",
    "latitude",
    "longitude",
    "zip",
    "name",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarEdgeConfigEntry
) -> dict[str, Any]:
    """Return the cached state, ledger and schedule."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "ledger": coordinator.ledger.as_dict(),
        "interval_minutes": coordinator.interval_minutes,
        "next_poll": coordinator.next_poll.isoformat() if coordinator.next_poll else None,
        "paused": coordinator.paused,
        "state": async_redact_data(coordinator.state.as_dict(), TO_REDACT),
    }
