"""Take over the entities of the built-in ``solaredge`` integration.

The built-in integration names its sensors ``{site_id}_{key}`` and so does this
one, with the same keys. Moving the registry entries to this integration keeps
the entity_id, name, area, unit settings and the whole history/statistics
(which Home Assistant stores per entity_id), so dashboards, automations and the
Energy dashboard carry on without a gap.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import CONF_MIGRATE_FROM, CONF_SITE_ID, CORE_DOMAIN, DOMAIN, ISSUE_MIGRATED, LOGGER

# Sensors with the same meaning, unit (W / Wh) and state class in both integrations.
MIGRATE_KEYS = (
    "current_power",
    "energy_today",
    "energy_this_month",
    "energy_this_year",
    "lifetime_energy",
)


def core_entry_for_site(hass: HomeAssistant, site_id: str) -> ConfigEntry | None:
    """The built-in integration's entry for the same site, if any."""
    for entry in hass.config_entries.async_entries(CORE_DOMAIN):
        if str(entry.data.get(CONF_SITE_ID, "")) == str(site_id):
            return entry
    return None


def migratable_entities(hass: HomeAssistant, site_id: str) -> list[str]:
    """entity_ids of the built-in sensors this integration can take over."""
    registry = er.async_get(hass)
    found = []
    for key in MIGRATE_KEYS:
        entity_id = registry.async_get_entity_id("sensor", CORE_DOMAIN, f"{site_id}_{key}")
        if entity_id:
            found.append(entity_id)
    return found


async def async_migrate_from_core(
    hass: HomeAssistant, entry: ConfigEntry, site_device_id: str
) -> list[str]:
    """Disable the built-in entry and move its matching sensors here."""
    core_id = entry.data.get(CONF_MIGRATE_FROM)
    if not core_id:
        return []
    site_id = str(entry.data[CONF_SITE_ID])

    core = hass.config_entries.async_get_entry(core_id)
    if core is not None and core.disabled_by is None:
        # Unloads it, so its entities are no longer "loaded" and can be moved.
        await hass.config_entries.async_set_disabled_by(core_id, ConfigEntryDisabler.USER)

    registry = er.async_get(hass)
    moved: list[str] = []
    for key in MIGRATE_KEYS:
        unique_id = f"{site_id}_{key}"
        if registry.async_get_entity_id("sensor", DOMAIN, unique_id):
            continue  # already ours
        old_id = registry.async_get_entity_id("sensor", CORE_DOMAIN, unique_id)
        if old_id is None:
            continue
        old = registry.async_get(old_id)
        try:
            # unique_id is identical in both integrations, so it is not passed
            # (passing it makes the registry see a clash with the entry itself).
            registry.async_update_entity_platform(
                old_id,
                DOMAIN,
                new_config_entry_id=entry.entry_id,
                new_device_id=site_device_id,
            )
        except ValueError as err:
            LOGGER.warning("Could not take over %s: %s", old_id, err)
            continue
        if old and old.disabled_by is er.RegistryEntryDisabler.CONFIG_ENTRY:
            # Disabled only because we disabled the old entry: switch it back on.
            registry.async_update_entity(old_id, disabled_by=None)
        moved.append(old_id)

    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != CONF_MIGRATE_FROM}
    )
    if moved:
        LOGGER.info("Took over %s from the built-in SolarEdge integration", ", ".join(moved))
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_MIGRATED}_{entry.entry_id}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MIGRATED,
            translation_placeholders={"entities": ", ".join(moved)},
        )
    return moved
