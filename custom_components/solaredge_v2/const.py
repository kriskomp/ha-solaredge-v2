"""Constants for the SolarEdge Monitoring API v2 integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "solaredge_v2"
LOGGER = logging.getLogger(__package__)
MANUFACTURER: Final = "SolarEdge"

STORAGE_VERSION: Final = 1

# Entry data
CONF_SITE_ID: Final = "site_id"
CONF_REDIRECT_URI: Final = "redirect_uri"
CONF_TOKEN: Final = "token"
CONF_GRANTED_AT: Final = "granted_at"
CONF_INITIAL: Final = "initial"  # site body + calls spent by the setup flow
CONF_MIGRATE_FROM: Final = "migrate_from"  # built-in solaredge entry to take over
CORE_DOMAIN: Final = "solaredge"

# Options
CONF_POLL_MODE: Final = "poll_mode"
CONF_MONTHLY_CREDITS: Final = "monthly_credits"
CONF_BILLING_DAY: Final = "billing_day"
CONF_RESERVE_PERCENT: Final = "reserve_percent"
CONF_MIN_INTERVAL: Final = "min_interval"
CONF_TOTALS: Final = "totals"
CONF_ALERTS: Final = "alerts"
CONF_OVERVIEW: Final = "overview"
CONF_STORAGE: Final = "storage"

DEFAULT_MONTHLY_CREDITS: Final = 2000
DEFAULT_RESERVE_PERCENT: Final = 10
DEFAULT_MIN_INTERVAL: Final = 10
MIN_INTERVAL_FLOOR: Final = 5

GRANT_MONTHS: Final = 24
CONSENT_WARNING: Final = timedelta(days=30)
TOKEN_KEEPALIVE: Final = timedelta(days=20)  # refresh tokens die after 30 days unused
SITE_REFRESH: Final = timedelta(days=7)
POWER_STALE: Final = timedelta(minutes=60)

ISSUE_CREDITS: Final = "credits_exhausted"
ISSUE_CONSENT: Final = "consent_expiring"
ISSUE_MIGRATED: Final = "core_migrated"

SERVICE_SET_CREDITS: Final = "set_credits_used"
SERVICE_REFRESH: Final = "refresh"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_USED: Final = "used"
