"""Repair: confirm the day the SolarEdge credits reset."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .config_flow import billing_day_schema
from .const import CONF_BILLING_CONFIRMED, CONF_BILLING_DAY, ISSUE_BILLING_DAY


class BillingDayRepairFlow(RepairsFlow):
    """Ask for the billing day and store it as confirmed."""

    def __init__(self, entry_id: str) -> None:
        """Remember which entry to update."""
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """The only step."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_not_found")
        if user_input is not None:
            # Saving the options reloads the entry, which clears this issue.
            self.hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    CONF_BILLING_DAY: int(user_input[CONF_BILLING_DAY]),
                    CONF_BILLING_CONFIRMED: True,
                },
            )
            return self.async_create_entry(data={})
        current = int(entry.options.get(CONF_BILLING_DAY, 1))
        return self.async_show_form(
            step_id="init",
            data_schema=billing_day_schema(current),
            description_placeholders={"dashboard": "https://developer.solaredge.com/dashboard"},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for a repair issue."""
    assert issue_id.startswith(ISSUE_BILLING_DAY)
    return BillingDayRepairFlow(str((data or {})["entry_id"]))
