"""SolarEdge Monitoring API v2 (Site Access) for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import UpdateFailed
import voluptuous as vol

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_USED,
    CONF_SITE_ID,
    DOMAIN,
    MANUFACTURER,
    SERVICE_REFRESH,
    SERVICE_SET_CREDITS,
    STORAGE_VERSION,
)
from .callback_view import SolarEdgeCallbackView
from .coordinator import SolarEdgeConfigEntry, SolarEdgeCoordinator
from .migrate import async_migrate_from_core

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions and the consent callback view."""
    hass.http.register_view(SolarEdgeCallbackView())

    def _coordinator(call: ServiceCall) -> SolarEdgeCoordinator:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entries = [
            e
            for e in hass.config_entries.async_loaded_entries(DOMAIN)
            if entry_id is None or e.entry_id == entry_id
        ]
        if len(entries) != 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_found"
            )
        return entries[0].runtime_data

    async def set_credits(call: ServiceCall) -> None:
        await _coordinator(call).async_set_credits_used(int(call.data[ATTR_USED]))

    async def refresh(call: ServiceCall) -> None:
        try:
            await _coordinator(call).async_poll_now()
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CREDITS,
        set_credits,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
                vol.Required(ATTR_USED): vol.All(vol.Coerce(int), vol.Range(min=0)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH,
        refresh,
        schema=vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> bool:
    """Set up one site from the cache; the first API call waits for its slot."""
    coordinator = SolarEdgeCoordinator(hass, entry)
    await coordinator.async_initialize()
    entry.runtime_data = coordinator

    meta = coordinator.metadata
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, str(entry.data[CONF_SITE_ID]))},
        manufacturer=MANUFACTURER,
        name=meta.name if meta else f"SolarEdge {entry.data[CONF_SITE_ID]}",
        model="Monitoring site (API v2)",
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url="https://monitoring.solaredge.com/",
    )
    # Successor of the built-in integration: take over its sensors (same entity_id,
    # same history) before our sensors are created.
    await async_migrate_from_core(hass, entry, device.id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> None:
    """Reload only when the options changed (token or data updates must not reload)."""
    coordinator = entry.runtime_data
    if dict(entry.options) != coordinator.options_source:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> bool:
    """Unload; the cache stays so the next load costs no credits."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> None:
    """Delete the cache file when the entry is removed."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
