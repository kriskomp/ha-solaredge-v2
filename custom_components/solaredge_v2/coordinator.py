"""Polling, credit accounting and caching for one SolarEdge site.

Rules this coordinator follows:

* Only poll between sunrise and sunset (+ one final read after sunset).
* One credit per poll by default: the QUARTER_HOUR inverter telemetry gives
  power, today's energy, peak, voltage, current and frequency in one call.
* Month / year / lifetime totals cost one extra call per day.
* Every call is counted locally (the API has no monthly counter).
* The state is saved to .storage after every poll. A restart or reload shows
  the cached values immediately and the next poll happens at
  ``last poll + interval``, never right away.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Series,
    SiteMetadata,
    SolarEdgeAuthenticationError,
    SolarEdgeClient,
    SolarEdgeCreditLimitError,
    SolarEdgeError,
    SolarEdgeOAuthError,
    SolarEdgeRateLimitError,
    TokenSet,
    parse_telemetry,
    refresh_tokens,
)
from .budget import (
    MODE_ADAPTIVE,
    MODE_FIXED,
    Ledger,
    adaptive_interval,
    cycle_bounds,
    fixed_interval,
    projected_cycle_use,
)
from .const import (
    CONF_ALERTS,
    CONF_BILLING_CONFIRMED,
    CONF_BILLING_DAY,
    CONF_GRANTED_AT,
    CONF_INITIAL,
    CONF_MIN_INTERVAL,
    CONF_MONTHLY_CREDITS,
    CONF_OVERVIEW,
    CONF_POLL_MODE,
    CONF_RESERVE_PERCENT,
    CONF_SITE_ID,
    CONF_STORAGE,
    CONF_TOKEN,
    CONF_TOTALS,
    CONSENT_WARNING,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_MONTHLY_CREDITS,
    DEFAULT_RESERVE_PERCENT,
    DOMAIN,
    GRANT_MONTHS,
    ISSUE_BILLING_DAY,
    ISSUE_CONSENT,
    ISSUE_CREDITS,
    LOGGER,
    SITE_REFRESH,
    STORAGE_VERSION,
    TOKEN_KEEPALIVE,
)
from .solar import longest_window_minutes, poll_window
from .state import SiteState, summarize_inverters

ACCESS_TOKEN_MARGIN = 300  # seconds
FINAL_POLL_SLACK = timedelta(minutes=5)
MIN_DELAY = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class Options:
    """Polling options of one entry."""

    mode: str
    monthly_credits: int
    billing_day: int
    reserve_percent: float
    min_interval: float
    totals: bool
    alerts: bool
    overview: bool
    storage: bool

    @classmethod
    def from_mapping(cls, options: dict[str, Any], default_billing_day: int) -> Options:
        """Read options with defaults."""
        return cls(
            mode=options.get(CONF_POLL_MODE, MODE_FIXED),
            monthly_credits=int(options.get(CONF_MONTHLY_CREDITS, DEFAULT_MONTHLY_CREDITS)),
            billing_day=int(options.get(CONF_BILLING_DAY, default_billing_day)),
            reserve_percent=float(options.get(CONF_RESERVE_PERCENT, DEFAULT_RESERVE_PERCENT)),
            min_interval=float(options.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL)),
            totals=bool(options.get(CONF_TOTALS, True)),
            alerts=bool(options.get(CONF_ALERTS, False)),
            overview=bool(options.get(CONF_OVERVIEW, False)),
            storage=bool(options.get(CONF_STORAGE, False)),
        )

    @property
    def calls_per_poll(self) -> int:
        """Credits one daytime poll costs."""
        return 1 + int(self.overview) + int(self.storage)

    @property
    def daily_fixed_calls(self) -> float:
        """Credits spent once a day or less, averaged per day."""
        # site + devices once a week, previous-years sum once a year
        return int(self.totals) + int(self.alerts) + 2 / 7 + 1 / 365


def default_billing_day(entry: ConfigEntry) -> int:
    """The developer account's cycle usually starts on the day the app was created."""
    granted = entry.data.get(CONF_GRANTED_AT)
    if granted:
        parsed = dt_util.parse_datetime(granted)
        if parsed:
            return parsed.day
    return 1


def add_months(moment: datetime, months: int) -> datetime:
    """Same day-of-month N months later (clamped)."""
    index = moment.year * 12 + moment.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    day = moment.day
    while True:
        try:
            return moment.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1


class SolarEdgeCoordinator(DataUpdateCoordinator[SiteState]):
    """Owns the API client, the token, the credit ledger and the poll schedule."""

    config_entry: SolarEdgeConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SolarEdgeConfigEntry) -> None:
        """Prepare; nothing is fetched until async_initialize."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_SITE_ID]}",
            update_interval=None,
            always_update=True,
        )
        self.site_id = str(entry.data[CONF_SITE_ID])
        self.options_source = dict(entry.options)
        self.options = Options.from_mapping(self.options_source, default_billing_day(entry))
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}", private=True
        )
        self._token: TokenSet = TokenSet.from_dict(entry.data[CONF_TOKEN])
        self._token_lock = asyncio.Lock()
        self.client = SolarEdgeClient(
            async_get_clientsession(hass), self._access_token, on_send=self._count_call
        )
        self.state = SiteState()
        self.ledger = Ledger(cycle_start=date.today())
        self.interval_minutes: float | None = None
        self.next_poll: datetime | None = None
        self._unsub_poll: CALLBACK_TYPE | None = None
        self._unsub_midnight: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------ setup

    async def async_initialize(self) -> None:
        """Restore the cache (no API call) and start the schedule."""
        raw = await self._async_load_cache()
        stored_token = raw.get("token")
        if isinstance(stored_token, dict):
            try:
                candidate = TokenSet.from_dict(stored_token)
            except (KeyError, TypeError, ValueError) as err:
                LOGGER.warning("Ignoring the cached token (unreadable: %s)", err)
            else:
                if candidate.issued_at > self._token.issued_at:
                    self._token = candidate
        today = self.today()
        try:
            self.ledger = Ledger.from_dict(raw.get("ledger"), today, self.options.billing_day)
        except (KeyError, TypeError, ValueError) as err:
            LOGGER.warning("Credit counter in .storage unreadable (%s); starting from 0", err)
            self.ledger = Ledger.from_dict(None, today, self.options.billing_day)
        try:
            self.state = SiteState.from_dict(raw.get("state"))
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            LOGGER.warning("Cached readings in .storage unreadable (%s); starting empty", err)
            self.state = SiteState()

        initial = self.config_entry.data.get(CONF_INITIAL)
        if initial:
            if not self.state.site and initial.get("site"):
                self.state.site = initial["site"]
                self.state.site_fetched = dt_util.utcnow()
            self.ledger.used += int(initial.get("calls", 0))
            data = {k: v for k, v in self.config_entry.data.items() if k != CONF_INITIAL}
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            await self.async_save()

        if self.state.today is not None:
            self.state.roll_day(today)
        self.async_set_updated_data(self.state)
        self._check_consent()
        self._check_billing_day()
        self._schedule_midnight()
        self.schedule_next_poll()

    async def async_shutdown(self) -> None:
        """Cancel timers and save."""
        await super().async_shutdown()
        self._cancel_timers()
        await self.async_save()

    def _cancel_timers(self) -> None:
        if self._unsub_poll:
            self._unsub_poll()
            self._unsub_poll = None
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    async def _async_load_cache(self) -> dict[str, Any]:
        """Read .storage; a missing or broken file means "start without a cache"."""
        try:
            raw = await self._store.async_load()
        except Exception as err:  # never block setup on a bad cache
            LOGGER.warning("Could not read the SolarEdge cache, starting without it: %s", err)
            return {}
        return raw if isinstance(raw, dict) else {}

    def _snapshot(self) -> dict[str, Any]:
        return {
            "token": self._token.as_dict(),
            "ledger": self.ledger.as_dict(),
            "state": self.state.as_dict(),
        }

    async def async_save(self) -> None:
        """Write token, ledger and state to .storage immediately."""
        try:
            await self._store.async_save(self._snapshot())
        except Exception as err:  # a failed write must not break polling
            LOGGER.warning("Could not write the SolarEdge cache: %s", err)

    @callback
    def save_soon(self) -> None:
        """Save within a second (or when Home Assistant stops) without waiting."""
        self._store.async_delay_save(self._snapshot, 1)

    # ------------------------------------------------------------------ site

    @property
    def metadata(self) -> SiteMetadata | None:
        """Parsed /sites/{id}."""
        if not self.state.site:
            return None
        try:
            return SiteMetadata.from_api(self.state.site)
        except (KeyError, ValueError, TypeError):
            return None

    @property
    def site_tz(self) -> tzinfo:
        """The site's time zone (falls back to Home Assistant's)."""
        meta = self.metadata
        zone = dt_util.get_time_zone(meta.timezone) if meta and meta.timezone else None
        return zone or dt_util.get_default_time_zone()

    @property
    def location(self) -> tuple[float, float]:
        """Site coordinates (falls back to Home Assistant's home)."""
        meta = self.metadata
        if meta and meta.latitude is not None and meta.longitude is not None:
            return meta.latitude, meta.longitude
        return self.hass.config.latitude, self.hass.config.longitude

    def today(self) -> date:
        """Today at the site."""
        return dt_util.utcnow().astimezone(self.site_tz).date()

    def local_midnight(self, day: date) -> datetime:
        """00:00 of a day at the site, as an aware datetime."""
        return datetime.combine(day, time(0), tzinfo=self.site_tz)

    # ------------------------------------------------------------------ token

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token.valid_for() > ACCESS_TOKEN_MARGIN:
                return self._token.access_token
            await self._refresh_token_locked()
            return self._token.access_token

    async def _refresh_token_locked(self) -> None:
        data = self.config_entry.data
        try:
            new = await refresh_tokens(
                async_get_clientsession(self.hass),
                data["client_id"],
                data["client_secret"],
                self._token.refresh_token,
            )
        except SolarEdgeOAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        # The old refresh token is dead now: persist the new one before anything else.
        self._token = new
        await self.async_save()
        LOGGER.debug("SolarEdge token refreshed for site %s", self.site_id)

    async def async_keep_token_alive(self) -> None:
        """Refresh tokens die after 30 days unused (e.g. while credits are exhausted)."""
        age = dt_util.utcnow().timestamp() - self._token.issued_at
        if age < TOKEN_KEEPALIVE.total_seconds():
            return
        async with self._token_lock:
            try:
                await self._refresh_token_locked()
            except ConfigEntryAuthFailed:
                self.config_entry.async_start_reauth(self.hass)
            except SolarEdgeError as err:
                LOGGER.warning("Token keep-alive failed: %s", err)

    # ------------------------------------------------------------------ credits

    @callback
    def _count_call(self, path: str) -> None:
        self.ledger.used += 1
        self.save_soon()  # a crash mid-poll must not lose the count
        LOGGER.debug("API call %s (credits used: %s)", path, self.ledger.used)

    @property
    def credits_remaining(self) -> int:
        """Credits left in the cycle."""
        return max(0, self.options.monthly_credits - self.ledger.used)

    @property
    def cycle_end(self) -> date:
        """First day of the next billing cycle."""
        return cycle_bounds(self.today(), self.options.billing_day)[1]

    @property
    def paused(self) -> bool:
        """No credits left for another poll in this cycle."""
        return self.ledger.exhausted or self.credits_remaining < self.options.calls_per_poll

    async def async_set_credits_used(self, used: int) -> None:
        """Correct the local counter from the developer portal."""
        self.ledger.used = max(0, used)
        if self.ledger.used < self.options.monthly_credits:
            self.ledger.exhausted = False
            ir.async_delete_issue(self.hass, DOMAIN, self._issue(ISSUE_CREDITS))
        await self.async_save()
        self.schedule_next_poll()
        self.async_update_listeners()

    def _windows_until_cycle_end(self, today: date) -> list[float]:
        lat, lon = self.location
        end = self.cycle_end
        days = (end - today).days
        return [
            poll_window(lat, lon, today + timedelta(days=i)).minutes for i in range(1, days)
        ]

    def compute_interval(self, options: Options | None = None) -> float | None:
        """Minutes between daytime polls; None while paused."""
        opts = options or self.options
        if options is None and self.paused:
            return None
        lat, lon = self.location
        today = self.today()
        if opts.mode == MODE_ADAPTIVE:
            remaining_today = poll_window(lat, lon, today).remaining_minutes(dt_util.utcnow())
            return adaptive_interval(
                monthly_credits=opts.monthly_credits,
                reserve_percent=opts.reserve_percent,
                used=self.ledger.used,
                calls_per_poll=opts.calls_per_poll,
                daily_fixed_calls=opts.daily_fixed_calls,
                remaining_today_minutes=remaining_today,
                future_window_minutes=self._windows_until_cycle_end(today),
                min_interval=opts.min_interval,
            )
        return fixed_interval(
            monthly_credits=opts.monthly_credits,
            reserve_percent=opts.reserve_percent,
            calls_per_poll=opts.calls_per_poll,
            daily_fixed_calls=opts.daily_fixed_calls,
            longest_window_minutes=longest_window_minutes(lat, lon, today.year),
            min_interval=opts.min_interval,
        )

    def projected_use(self, options: Options | None = None) -> int | None:
        """Credits the cycle will end with at the current pace."""
        opts = options or self.options
        interval = self.compute_interval(options)
        if interval is None:
            return self.ledger.used
        lat, lon = self.location
        today = self.today()
        return projected_cycle_use(
            used=self.ledger.used,
            interval=interval,
            calls_per_poll=opts.calls_per_poll,
            daily_fixed_calls=opts.daily_fixed_calls,
            remaining_today_minutes=poll_window(lat, lon, today).remaining_minutes(
                dt_util.utcnow()
            ),
            future_window_minutes=self._windows_until_cycle_end(today),
        )

    # ------------------------------------------------------------------ schedule

    @callback
    def schedule_next_poll(self) -> None:
        """Arm the timer for the next poll (or for the cycle reset while paused)."""
        if self._unsub_poll:
            self._unsub_poll()
            self._unsub_poll = None
        self.interval_minutes = self.compute_interval()
        if self.interval_minutes is None:
            self.next_poll = None  # midnight handler re-arms after the cycle resets
            return
        self.next_poll = self._next_poll_time(dt_util.utcnow(), self.interval_minutes)
        self._unsub_poll = async_track_point_in_utc_time(
            self.hass, self._handle_poll_timer, self.next_poll
        )

    def _next_poll_time(self, now: datetime, interval: float) -> datetime:
        lat, lon = self.location
        today = now.astimezone(self.site_tz).date()
        window = poll_window(lat, lon, today)
        last = self.state.last_poll
        step = timedelta(minutes=interval)

        def next_day_start() -> datetime:
            for i in range(1, 370):
                start = poll_window(lat, lon, today + timedelta(days=i)).start
                if start is not None:
                    return start
            return now + timedelta(days=1)

        if self.state.today is None:
            # Fresh install: get something on screen right away (but never retry faster
            # than the interval if that first poll failed).
            when = now if last is None else max(now, last + step)
        elif window.start is None or window.end is None:
            when = next_day_start()
        elif now < window.start:
            when = window.start
        elif now <= window.end:
            due = last + step if last else now
            if due <= window.end:
                when = due
            elif last is None or last < window.end - FINAL_POLL_SLACK:
                when = window.end  # final read of the day
            else:
                when = next_day_start()
        elif last is None or last < window.end - FINAL_POLL_SLACK:
            when = now  # the final read of today was missed (e.g. HA was off)
        else:
            when = next_day_start()
        return max(when, now + MIN_DELAY)

    async def _handle_poll_timer(self, _now: datetime) -> None:
        self._unsub_poll = None
        await self.async_refresh()
        self.schedule_next_poll()
        self.async_update_listeners()

    async def async_poll_now(self) -> None:
        """Poll immediately (action ``solaredge_v2.refresh``)."""
        if self.paused:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="credits_exhausted")
        await self.async_refresh()
        self.schedule_next_poll()
        self.async_update_listeners()

    def _schedule_midnight(self) -> None:
        if self._unsub_midnight:
            self._unsub_midnight()
        midnight = self.local_midnight(self.today() + timedelta(days=1)) + timedelta(seconds=5)
        self._unsub_midnight = async_track_point_in_utc_time(
            self.hass, self._handle_midnight, midnight.astimezone(UTC)
        )

    async def _handle_midnight(self, _now: datetime) -> None:
        self._unsub_midnight = None
        today = self.today()
        if self.ledger.roll(today, self.options.billing_day):
            ir.async_delete_issue(self.hass, DOMAIN, self._issue(ISSUE_CREDITS))
        self.state.roll_day(today)
        await self.async_keep_token_alive()
        await self.async_save()
        self._check_consent()
        self._schedule_midnight()
        self.schedule_next_poll()
        self.async_set_updated_data(self.state)

    # ------------------------------------------------------------------ poll

    async def _async_update_data(self) -> SiteState:
        state = self.state
        now = dt_util.utcnow()
        today = now.astimezone(self.site_tz).date()
        self.ledger.roll(today, self.options.billing_day)
        state.roll_day(today)
        if self.ledger.exhausted:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="credits_exhausted")
        state.last_poll = now
        try:
            await self._poll(state, now, today)
            state.last_success = now
        except ConfigEntryAuthFailed:
            raise
        except SolarEdgeAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SolarEdgeCreditLimitError as err:
            self.ledger.exhausted = True
            self.ledger.used = max(self.ledger.used, self.options.monthly_credits)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue(ISSUE_CREDITS),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_CREDITS,
                translation_placeholders={"reset": self.cycle_end.strftime("%d.%m.%Y")},
            )
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="credits_exhausted"
            ) from err
        except SolarEdgeRateLimitError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="rate_limited") from err
        except SolarEdgeError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            await self.async_save()
        return state

    async def _poll(self, state: SiteState, now: datetime, today: date) -> None:
        client = self.client
        site = self.site_id
        opts = self.options
        midnight = self.local_midnight(today)

        if not state.site or not state.site_fetched or now - state.site_fetched > SITE_REFRESH:
            state.site = await client.site(site)
            devices = await client.devices(site)
            state.devices = devices if isinstance(devices, list) else []
            state.site_fetched = now

        telemetry = await client.inverter_telemetry(site, midnight, now)
        state.today = summarize_inverters(parse_telemetry(telemetry, "inverters"), today)

        if opts.overview:
            body = await client.overview(site)
            state.overview = body if isinstance(body, dict) else None
        if opts.storage:
            body = await client.storage_telemetry(site, midnight, now)
            state.storage = body if isinstance(body, dict) else None

        if opts.totals:
            totals = state.totals
            if totals.needs_baseline(today):
                jan1 = self.local_midnight(date(today.year, 1, 1))
                if midnight <= jan1:
                    totals.set_empty_baseline(today)
                else:
                    body = await client.energy(site, "MONTH", jan1, midnight)
                    totals.set_from_month_series(Series.from_api(body), today)
            if totals.needs_prior_years(today):
                await self._fetch_prior_years(state, today)

        if opts.alerts and state.alerts_fetched != today:
            body = await client.open_alerts(site)
            state.alerts_open = len(body) if isinstance(body, list) else 0
            state.alerts_fetched = today

    async def _fetch_prior_years(self, state: SiteState, today: date) -> None:
        jan1 = self.local_midnight(date(today.year, 1, 1))
        meta = self.metadata
        installed = meta.installation_date if meta else None
        if installed is None or installed >= jan1:
            state.totals.set_prior_years(0.0, today)
            return
        total = 0.0
        # resolution=YEAR is the only one without a window limit.
        body = await self.client.energy(self.site_id, "YEAR", installed, jan1)
        series = Series.from_api(body)
        for sample in series.samples:
            if sample.value is not None and sample.timestamp and sample.timestamp.year < today.year:
                total += sample.value
        state.totals.set_prior_years(total, today)

    # ------------------------------------------------------------------ issues

    def _issue(self, key: str) -> str:
        return f"{key}_{self.config_entry.entry_id}"

    @property
    def consent_expires(self) -> datetime | None:
        """When the 24-month Site Access grant runs out."""
        granted = dt_util.parse_datetime(self.config_entry.data.get(CONF_GRANTED_AT) or "")
        return add_months(granted, GRANT_MONTHS) if granted else None

    def _check_billing_day(self) -> None:
        """Entries created before the setup asked for it only have a guessed day."""
        if self.config_entry.options.get(CONF_BILLING_CONFIRMED):
            ir.async_delete_issue(self.hass, DOMAIN, self._issue(ISSUE_BILLING_DAY))
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue(ISSUE_BILLING_DAY),
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_BILLING_DAY,
            translation_placeholders={"day": str(self.options.billing_day)},
            data={"entry_id": self.config_entry.entry_id},
        )

    def _check_consent(self) -> None:
        expires = self.consent_expires
        if expires and expires - dt_util.utcnow() < CONSENT_WARNING:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue(ISSUE_CONSENT),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_CONSENT,
                translation_placeholders={
                    "date": expires.astimezone(self.site_tz).strftime("%d.%m.%Y")
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue(ISSUE_CONSENT))


def options_preview(
    coordinator: SolarEdgeCoordinator, options: dict[str, Any]
) -> dict[str, str]:
    """Numbers shown in the options flow before saving."""
    opts = Options.from_mapping(options, coordinator.options.billing_day)
    fixed = coordinator.compute_interval(replace(opts, mode=MODE_FIXED))
    adaptive = coordinator.compute_interval(replace(opts, mode=MODE_ADAPTIVE))
    projected = coordinator.projected_use(opts)
    return {
        "fixed": f"{fixed:.0f}" if fixed else "-",
        "adaptive": f"{adaptive:.0f}" if adaptive else "-",
        "calls_per_poll": str(opts.calls_per_poll),
        "daily_fixed": f"{opts.daily_fixed_calls:.1f}",
        "used": str(coordinator.ledger.used),
        "limit": str(opts.monthly_credits),
        "projected": str(projected) if projected is not None else "-",
    }


type SolarEdgeConfigEntry = ConfigEntry[SolarEdgeCoordinator]
