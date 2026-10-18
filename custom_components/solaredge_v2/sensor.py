"""Sensors for the SolarEdge Monitoring API v2 integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import SiteOverview, parse_telemetry
from .const import DOMAIN, MANUFACTURER, POWER_STALE
from .coordinator import Options, SolarEdgeConfigEntry, SolarEdgeCoordinator
from .state import InverterReading

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SiteSensorDescription(SensorEntityDescription):
    """A sensor on the site device."""

    value_fn: Callable[[SolarEdgeCoordinator], Any]
    attrs_fn: Callable[[SolarEdgeCoordinator], dict[str, Any] | None] | None = None
    enabled_fn: Callable[[Options], bool] = lambda _: True
    monotonic: bool = False  # ignore small downward revisions (they look like resets)


@dataclass(frozen=True, kw_only=True)
class InverterSensorDescription(SensorEntityDescription):
    """A sensor on one inverter device."""

    value_fn: Callable[[InverterReading], float | None]


def _today(c: SolarEdgeCoordinator) -> date:
    return c.today()


def _power(c: SolarEdgeCoordinator) -> float | None:
    reading = c.state.today
    if reading is None:
        return None
    if reading.day != _today(c) or reading.power_at is None or reading.power_w is None:
        return 0.0
    if dt_util.utcnow() - reading.power_at > POWER_STALE:
        return 0.0
    return round(reading.power_w, 1)


def _power_attrs(c: SolarEdgeCoordinator) -> dict[str, Any]:
    reading = c.state.today
    return {
        "data_time": reading.power_at.isoformat() if reading and reading.power_at else None,
        "note": "15-minute average reported by SolarEdge",
    }


def _energy_today(c: SolarEdgeCoordinator) -> float | None:
    reading = c.state.today
    if reading is None:
        return None
    return reading.energy_wh if reading.day == _today(c) else 0.0


def _peak(c: SolarEdgeCoordinator) -> float | None:
    reading = c.state.today
    if reading is None or reading.day != _today(c) or reading.peak_w is None:
        return None
    return round(reading.peak_w, 1)


def _peak_attrs(c: SolarEdgeCoordinator) -> dict[str, Any]:
    reading = c.state.today
    at = reading.peak_at if reading and reading.day == _today(c) else None
    return {"peak_time": at.isoformat() if at else None}


def _overview(c: SolarEdgeCoordinator) -> SiteOverview | None:
    return SiteOverview.from_api(c.state.overview) if c.state.overview else None


def _overview_value(attr: str) -> Callable[[SolarEdgeCoordinator], float | None]:
    def value(c: SolarEdgeCoordinator) -> float | None:
        overview = _overview(c)
        return getattr(overview, attr) if overview else None

    return value


def _storage_value(metric: str) -> Callable[[SolarEdgeCoordinator], float | None]:
    def value(c: SolarEdgeCoordinator) -> float | None:
        if not c.state.storage:
            return None
        for metrics in parse_telemetry(c.state.storage, "storage").values():
            series = metrics.get(metric)
            if series is not None and series.latest is not None:
                return round(series.latest, 2)
        return None

    return value


def _date_of(moment: datetime | date | None) -> date | None:
    if isinstance(moment, datetime):
        return moment.date()
    return moment


ENERGY = {
    "native_unit_of_measurement": UnitOfEnergy.WATT_HOUR,
    "suggested_unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
    "suggested_display_precision": 2,
    "device_class": SensorDeviceClass.ENERGY,
    "state_class": SensorStateClass.TOTAL_INCREASING,
}
SITE_ENERGY = {**ENERGY, "monotonic": True}

SITE_SENSORS: tuple[SiteSensorDescription, ...] = (
    SiteSensorDescription(
        key="current_power",
        translation_key="current_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_power,
        attrs_fn=_power_attrs,
    ),
    SiteSensorDescription(key="energy_today", translation_key="energy_today", value_fn=_energy_today, **SITE_ENERGY),
    SiteSensorDescription(
        key="peak_power_today",
        translation_key="peak_power_today",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        value_fn=_peak,
        attrs_fn=_peak_attrs,
    ),
    SiteSensorDescription(
        key="energy_this_month",
        translation_key="energy_this_month",
        value_fn=lambda c: c.state.month_wh(c.today()),
        enabled_fn=lambda o: o.totals,
        **SITE_ENERGY,
    ),
    SiteSensorDescription(
        key="energy_this_year",
        translation_key="energy_this_year",
        value_fn=lambda c: c.state.year_wh(c.today()),
        enabled_fn=lambda o: o.totals,
        **SITE_ENERGY,
    ),
    SiteSensorDescription(
        key="lifetime_energy",
        translation_key="lifetime_energy",
        value_fn=lambda c: c.state.lifetime_wh(c.today()),
        enabled_fn=lambda o: o.totals,
        **SITE_ENERGY,
    ),
    SiteSensorDescription(
        key="open_alerts",
        translation_key="open_alerts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.state.alerts_open,
        enabled_fn=lambda o: o.alerts,
    ),
    # Optional groups (one extra credit per poll each)
    SiteSensorDescription(
        key="consumption_today",
        translation_key="consumption_today",
        value_fn=_overview_value("consumption"),
        enabled_fn=lambda o: o.overview,
        **SITE_ENERGY,
    ),
    SiteSensorDescription(
        key="self_consumption_today",
        translation_key="self_consumption_today",
        value_fn=_overview_value("production_to_self_consumption"),
        enabled_fn=lambda o: o.overview,
        **SITE_ENERGY,
    ),
    SiteSensorDescription(
        key="export_today",
        translation_key="export_today",
        value_fn=_overview_value("production_to_grid"),
        enabled_fn=lambda o: o.overview,
        **SITE_ENERGY,
    ),
    SiteSensorDescription(
        key="import_today",
        translation_key="import_today",
        value_fn=_overview_value("consumption_from_grid"),
        enabled_fn=lambda o: o.overview,
        **SITE_ENERGY,
    ),
    SiteSensorDescription(
        key="battery_state_of_energy",
        translation_key="battery_state_of_energy",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_storage_value("stateOfEnergy"),
        enabled_fn=lambda o: o.storage,
    ),
    SiteSensorDescription(
        key="battery_charge_power",
        translation_key="battery_charge_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_storage_value("chargePower"),
        enabled_fn=lambda o: o.storage,
    ),
    SiteSensorDescription(
        key="battery_discharge_power",
        translation_key="battery_discharge_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_storage_value("dischargePower"),
        enabled_fn=lambda o: o.storage,
    ),
    # Diagnostics: credits and schedule
    SiteSensorDescription(
        key="credits_used",
        translation_key="credits_used",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.ledger.used,
        attrs_fn=lambda c: {
            "limit": c.options.monthly_credits,
            "cycle_start": c.ledger.cycle_start.isoformat(),
        },
    ),
    SiteSensorDescription(
        key="credits_remaining",
        translation_key="credits_remaining",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.credits_remaining,
    ),
    SiteSensorDescription(
        key="credits_projected",
        translation_key="credits_projected",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.projected_use(),
    ),
    SiteSensorDescription(
        key="credit_cycle_end",
        translation_key="credit_cycle_end",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c: c.cycle_end,
    ),
    SiteSensorDescription(
        key="poll_interval",
        translation_key="poll_interval",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
        value_fn=lambda c: round(c.interval_minutes, 1) if c.interval_minutes else None,
        attrs_fn=lambda c: {"mode": c.options.mode, "paused": c.paused},
    ),
    SiteSensorDescription(
        key="last_poll",
        translation_key="last_poll",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.state.last_poll,
    ),
    SiteSensorDescription(
        key="next_poll",
        translation_key="next_poll",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.next_poll,
    ),
    SiteSensorDescription(
        key="data_time",
        translation_key="data_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: c.state.today.power_at if c.state.today else None,
    ),
    SiteSensorDescription(
        key="consent_expires",
        translation_key="consent_expires",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c: _date_of(c.consent_expires),
    ),
)

INVERTER_SENSORS: tuple[InverterSensorDescription, ...] = (
    InverterSensorDescription(
        key="ac_voltage",
        translation_key="ac_voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.voltage,
    ),
    InverterSensorDescription(
        key="ac_current",
        translation_key="ac_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: r.current,
    ),
    InverterSensorDescription(
        key="ac_frequency",
        translation_key="ac_frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: r.frequency,
    ),
    InverterSensorDescription(
        key="inverter_power",
        translation_key="inverter_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.power_w,
    ),
    InverterSensorDescription(
        key="inverter_energy_today",
        translation_key="inverter_energy_today",
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.energy_today_wh,
        **ENERGY,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarEdgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the site sensors now and inverter sensors as inverters appear."""
    coordinator = entry.runtime_data
    async_add_entities(
        SiteSensor(coordinator, d) for d in SITE_SENSORS if d.enabled_fn(coordinator.options)
    )

    known: set[str] = set()

    @callback
    def add_inverters() -> None:
        serials = set(coordinator.state.today.inverters) if coordinator.state.today else set()
        serials |= {
            str(d["serialNumber"])
            for d in coordinator.state.devices
            if isinstance(d, dict) and d.get("type") == "INVERTER" and d.get("serialNumber")
        }
        new = serials - known
        if new:
            known.update(new)
            async_add_entities(
                InverterSensor(coordinator, d, serial) for serial in sorted(new) for d in INVERTER_SENSORS
            )

    add_inverters()
    entry.async_on_unload(coordinator.async_add_listener(add_inverters))


class SiteSensor(CoordinatorEntity[SolarEdgeCoordinator], SensorEntity):
    """A sensor on the site device."""

    _attr_has_entity_name = True
    entity_description: SiteSensorDescription

    def __init__(self, coordinator: SolarEdgeCoordinator, description: SiteSensorDescription) -> None:
        """Attach to the site device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.site_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.site_id)})
        self._last: float | None = None

    async def async_update(self) -> None:
        """``homeassistant.update_entity`` must not spend API credits.

        The schedule decides when SolarEdge is read; use the ``solaredge_v2.refresh``
        action to poll on demand.
        """

    @property
    def available(self) -> bool:
        """Cached values stay visible even when the last poll failed."""
        return True

    @property
    def native_value(self) -> Any:
        """Value from the cached state."""
        value = self.entity_description.value_fn(self.coordinator)
        if self.entity_description.monotonic and isinstance(value, int | float):
            last = self._last
            if last is not None and last * 0.99 <= value < last:
                return last  # tiny revision, not a reset
            self._last = value
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Optional attributes."""
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator) if fn else None


class InverterSensor(CoordinatorEntity[SolarEdgeCoordinator], SensorEntity):
    """A sensor on one inverter."""

    _attr_has_entity_name = True
    entity_description: InverterSensorDescription

    def __init__(
        self, coordinator: SolarEdgeCoordinator, description: InverterSensorDescription, serial: str
    ) -> None:
        """Attach to the inverter device."""
        super().__init__(coordinator)
        self.entity_description = description
        self.serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        info = next(
            (
                d
                for d in coordinator.state.devices
                if isinstance(d, dict) and str(d.get("serialNumber")) == serial
            ),
            {},
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=info.get("manufacturer") or MANUFACTURER,
            model=info.get("partNumber"),
            name=info.get("name") or f"Inverter {serial}",
            serial_number=serial,
            sw_version=info.get("firmwareVersion"),
            via_device_id=dr.async_get_device_id_by_identifier(
                coordinator.hass,
                (DOMAIN, coordinator.site_id),
                config_entry_id=coordinator.config_entry.entry_id,
            ),
        )

    async def async_update(self) -> None:
        """``homeassistant.update_entity`` must not spend API credits.

        The schedule decides when SolarEdge is read; use the ``solaredge_v2.refresh``
        action to poll on demand.
        """

    @property
    def available(self) -> bool:
        """Cached values stay visible even when the last poll failed."""
        return True

    @property
    def native_value(self) -> float | None:
        """Latest 15-minute value of this inverter (today only)."""
        reading = self.coordinator.state.today
        if reading is None or reading.day != self.coordinator.today():
            return None
        inverter = reading.inverters.get(self.serial)
        if inverter is None:
            return None
        value = self.entity_description.value_fn(inverter)
        return round(value, 3) if value is not None else None
