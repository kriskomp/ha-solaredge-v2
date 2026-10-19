"""Config, re-authentication and options flows.

SolarEdge Connect does not return the OAuth ``state`` parameter, so Home
Assistant's built-in OAuth flow cannot finish. Instead the flow shows the
consent link and the user pastes the address the browser lands on
(``http://localhost/callback?code=...&site_id=...``); nothing needs to listen
on that address.
"""

from __future__ import annotations

from collections.abc import Mapping
import secrets
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import (
    DEFAULT_REDIRECT_URI,
    RedirectParseError,
    SolarEdgeClient,
    SolarEdgeConnectionError,
    SolarEdgeError,
    SolarEdgeForbiddenError,
    SolarEdgeOAuthError,
    TokenSet,
    authorize_url,
    exchange_code,
    parse_redirect,
)
from .budget import MODE_ADAPTIVE, MODE_FIXED
from .callback_view import EXTERNAL_STEP
from .const import (
    CALLBACK_PATH,
    CONF_ALERTS,
    CONF_BILLING_CONFIRMED,
    CONF_BILLING_DAY,
    CONF_GRANTED_AT,
    CONF_INITIAL,
    CONF_MIGRATE_FROM,
    CONF_MIN_INTERVAL,
    CONF_MONTHLY_CREDITS,
    CONF_OVERVIEW,
    CONF_POLL_MODE,
    CONF_REDIRECT_URI,
    CONF_RESERVE_PERCENT,
    CONF_SITE_ID,
    CONF_STORAGE,
    CONF_TOKEN,
    CONF_TOTALS,
    DOMAIN,
    LOGGER,
    MIN_INTERVAL_FLOOR,
)
from .coordinator import Options, default_billing_day, options_preview
from .migrate import core_entry_for_site, migratable_entities

CONF_REDIRECT_URL = "redirect_url"
CONF_MIGRATE = "migrate"
CONF_SITE_ID_INPUT = "site_id"

def _user_schema(redirect_default: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CLIENT_ID): TextSelector(),
            vol.Required(CONF_CLIENT_SECRET): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_REDIRECT_URI, default=redirect_default): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
        }
    )


def billing_day_schema(default: int) -> vol.Schema:
    """One field: day of the month the SolarEdge credits reset."""
    return vol.Schema(
        {
            vol.Required(CONF_BILLING_DAY, default=default): NumberSelector(
                NumberSelectorConfig(min=1, max=31, step=1, mode=NumberSelectorMode.BOX)
            )
        }
    )


def is_localhost(redirect_uri: str) -> bool:
    """The paste-the-address fallback (nothing listens on localhost)."""
    host = urlparse(redirect_uri).hostname or ""
    return host in {"localhost", "127.0.0.1"}


def _authorize_schema(need_site_id: bool) -> vol.Schema:
    fields: dict[Any, Any] = {vol.Required(CONF_REDIRECT_URL): TextSelector()}
    if need_site_id:
        fields[vol.Required(CONF_SITE_ID_INPUT)] = TextSelector()
    return vol.Schema(fields)


class SolarEdgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Site Access setup with a pasted redirect URL."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold the values between steps."""
        self._client_id = ""
        self._client_secret = ""
        self._redirect_uri = DEFAULT_REDIRECT_URI
        self._state = secrets.token_urlsafe(16)
        self._need_site_id = False
        self._title = ""
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}
        self._core_entry_id = ""
        self._callback_input: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Polling and sensor-group options."""
        return SolarEdgeOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the developer-console application credentials."""
        if user_input is not None:
            self._client_id = user_input[CONF_CLIENT_ID].strip()
            self._client_secret = user_input[CONF_CLIENT_SECRET].strip()
            self._redirect_uri = user_input[CONF_REDIRECT_URI].strip()
            return await self._async_start_consent()
        suggested = self._suggested_redirect()
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(suggested),
            description_placeholders={
                "console": "https://developer.solaredge.com",
                "callback": suggested,
            },
        )

    def _suggested_redirect(self) -> str:
        """HA's external HTTPS address + callback path, else the localhost fallback."""
        try:
            base = get_url(
                self.hass, allow_internal=False, require_ssl=True
            )
        except NoURLAvailableError:
            return DEFAULT_REDIRECT_URI
        return base.rstrip("/") + CALLBACK_PATH

    async def _async_start_consent(self) -> ConfigFlowResult:
        """Open the consent page; HA catches the redirect unless it is localhost."""
        if is_localhost(self._redirect_uri):
            return await (
                self.async_step_reauth_confirm() if self._updating else self.async_step_authorize()
            )
        return await self.async_step_external()

    @property
    def _updating(self) -> bool:
        """Re-authentication or reconfiguration of an existing entry."""
        return self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE)

    def _existing_entry(self) -> ConfigEntry:
        if self.source == SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        return self._get_reauth_entry()

    async def async_step_external(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait in the browser tab for SolarEdge to redirect to our callback view."""
        if user_input is None:
            return self.async_external_step(
                step_id=EXTERNAL_STEP,
                url=authorize_url(self._client_id, self._redirect_uri, self._state),
            )
        self._callback_input = user_input
        return self.async_external_step_done(next_step_id="finish")

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Exchange the code the callback delivered."""
        errors: dict[str, str] = {}
        if self._updating:
            self._callback_input[CONF_SITE_ID_INPUT] = self._existing_entry().data[CONF_SITE_ID]
        result = await self._exchange(self._callback_input, errors)
        if result is None:
            # Fall back to pasting, with the reason shown.
            step = "reauth_confirm" if self._updating else "authorize"
            return self.async_show_form(
                step_id=step,
                data_schema=_authorize_schema(self._need_site_id and step == "authorize"),
                errors=errors,
                description_placeholders={
                    "url": authorize_url(self._client_id, self._redirect_uri, self._state),
                    "redirect": self._redirect_uri,
                },
            )
        return await self._async_complete(result)

    async def _exchange(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> tuple[TokenSet, str, dict[str, Any], int] | None:
        """Trade the pasted code for tokens and prove access to the site."""
        try:
            code, site_id = parse_redirect(user_input[CONF_REDIRECT_URL])
        except RedirectParseError:
            errors[CONF_REDIRECT_URL] = "invalid_redirect"
            return None
        site_id = site_id or str(user_input.get(CONF_SITE_ID_INPUT) or "").strip() or None
        if not site_id:
            self._need_site_id = True
            errors["base"] = "site_id_missing"
            return None
        session = async_get_clientsession(self.hass)
        try:
            token = await exchange_code(
                session, self._client_id, self._client_secret, code, self._redirect_uri
            )
        except SolarEdgeOAuthError as err:
            LOGGER.warning("Code exchange refused: %s", err)
            errors["base"] = "invalid_code"
            return None
        except SolarEdgeConnectionError:
            errors["base"] = "cannot_connect"
            return None

        calls = 0

        def count(_path: str) -> None:
            nonlocal calls
            calls += 1

        async def token_getter() -> str:
            return token.access_token

        client = SolarEdgeClient(session, token_getter, on_send=count)
        try:
            site = await client.site(site_id)
        except SolarEdgeForbiddenError:
            errors["base"] = "no_site_access"
            return None
        except SolarEdgeError:
            errors["base"] = "cannot_connect"
            return None
        return token, str(site_id), site if isinstance(site, dict) else {}, calls

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the consent link and take the pasted redirect address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._exchange(user_input, errors)
            if result is not None:
                return await self._async_complete(result)
        return self.async_show_form(
            step_id="authorize",
            data_schema=_authorize_schema(self._need_site_id),
            errors=errors,
            description_placeholders={
                "url": authorize_url(self._client_id, self._redirect_uri, self._state),
                "redirect": self._redirect_uri,
            },
        )

    async def _async_complete(
        self, result: tuple[TokenSet, str, dict[str, Any], int]
    ) -> ConfigFlowResult:
        """Create the entry (or update it on re-authentication / reconfiguration)."""
        token, site_id, site, calls = result
        if self._updating:
            entry = self._existing_entry()
            if site_id != str(entry.data[CONF_SITE_ID]):
                return self.async_abort(reason="wrong_site")
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_CLIENT_ID: self._client_id,
                    CONF_CLIENT_SECRET: self._client_secret,
                    CONF_REDIRECT_URI: self._redirect_uri,
                    CONF_TOKEN: token.as_dict(),
                    CONF_GRANTED_AT: dt_util.utcnow().isoformat(),
                },
            )
        await self.async_set_unique_id(site_id)
        self._abort_if_unique_id_configured()
        now = dt_util.utcnow()
        self._title = str(site.get("name") or f"SolarEdge {site_id}")
        self._data = {
            CONF_CLIENT_ID: self._client_id,
            CONF_CLIENT_SECRET: self._client_secret,
            CONF_REDIRECT_URI: self._redirect_uri,
            CONF_SITE_ID: site_id,
            CONF_TOKEN: token.as_dict(),
            CONF_GRANTED_AT: now.isoformat(),
            CONF_INITIAL: {"site": site, "calls": calls},
        }
        return await self.async_step_billing()

    async def async_step_billing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the credit-cycle day: the API does not report it."""
        if user_input is not None:
            self._options = {
                CONF_BILLING_DAY: int(user_input[CONF_BILLING_DAY]),
                CONF_BILLING_CONFIRMED: True,
            }
            site_id = str(self._data[CONF_SITE_ID])
            core = core_entry_for_site(self.hass, site_id)
            if core is not None and migratable_entities(self.hass, site_id):
                self._core_entry_id = core.entry_id
                return await self.async_step_migrate()
            return self.async_create_entry(
                title=self._title, data=self._data, options=self._options
            )
        today = dt_util.now().day
        return self.async_show_form(
            step_id="billing",
            data_schema=billing_day_schema(today),
            description_placeholders={"dashboard": "https://developer.solaredge.com/dashboard"},
        )

    async def async_step_migrate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to take over the built-in integration's sensors and their history."""
        if user_input is not None:
            data = dict(self._data)
            if user_input.get(CONF_MIGRATE):
                data[CONF_MIGRATE_FROM] = self._core_entry_id
            return self.async_create_entry(title=self._title, data=data, options=self._options)
        entities = migratable_entities(self.hass, str(self._data[CONF_SITE_ID]))
        return self.async_show_form(
            step_id="migrate",
            data_schema=vol.Schema({vol.Required(CONF_MIGRATE, default=True): BooleanSelector()}),
            description_placeholders={"entities": "\n".join(f"- `{e}`" for e in entities)},
        )

    def _redirect_default(self, stored: str | None) -> str:
        """Prefer HA's own callback over a stored localhost address."""
        suggested = self._suggested_redirect()
        if not stored or (is_localhost(stored) and not is_localhost(suggested)):
            return suggested
        return stored

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The refresh token was rejected: approve access again."""
        self._client_id = entry_data[CONF_CLIENT_ID]
        self._client_secret = entry_data[CONF_CLIENT_SECRET]
        self._redirect_uri = self._redirect_default(entry_data.get(CONF_REDIRECT_URI))
        return await self.async_step_reauth_redirect()

    async def async_step_reauth_redirect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the Redirect URL (it must be registered in the SolarEdge app)."""
        if user_input is not None:
            self._redirect_uri = user_input[CONF_REDIRECT_URI].strip()
            return await self._async_start_consent()
        return self.async_show_form(
            step_id="reauth_redirect",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REDIRECT_URI, default=self._redirect_uri): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.URL)
                    )
                }
            ),
            description_placeholders={"callback": self._suggested_redirect()},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change Client ID / Secret / Redirect URL, then approve access again."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            self._client_id = user_input[CONF_CLIENT_ID].strip()
            self._client_secret = user_input[CONF_CLIENT_SECRET].strip()
            self._redirect_uri = user_input[CONF_REDIRECT_URI].strip()
            return await self._async_start_consent()
        schema = self.add_suggested_values_to_schema(
            _user_schema(self._redirect_default(entry.data.get(CONF_REDIRECT_URI))),
            {
                CONF_CLIENT_ID: entry.data[CONF_CLIENT_ID],
                CONF_CLIENT_SECRET: entry.data[CONF_CLIENT_SECRET],
            },
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={
                "console": "https://developer.solaredge.com",
                "callback": self._suggested_redirect(),
            },
        )

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste step for a localhost redirect, then update the existing entry."""
        errors: dict[str, str] = {}
        entry = self._existing_entry()
        if user_input is not None:
            user_input = {**user_input, CONF_SITE_ID_INPUT: entry.data[CONF_SITE_ID]}
            result = await self._exchange(user_input, errors)
            if result is not None:
                return await self._async_complete(result)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_authorize_schema(False),
            errors=errors,
            description_placeholders={
                "url": authorize_url(self._client_id, self._redirect_uri, self._state),
                "redirect": self._redirect_uri,
            },
        )


class SolarEdgeOptionsFlow(OptionsFlow):
    """Pick sensor groups and the polling mode, then confirm the computed interval."""

    def __init__(self) -> None:
        """Hold the pending options between the form and the summary."""
        self._pending: dict[str, Any] = {}

    def _schema(self, current: Options) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_POLL_MODE, default=current.mode): SelectSelector(
                    SelectSelectorConfig(
                        options=[MODE_FIXED, MODE_ADAPTIVE],
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_POLL_MODE,
                    )
                ),
                vol.Required(CONF_TOTALS, default=current.totals): BooleanSelector(),
                vol.Required(CONF_ALERTS, default=current.alerts): BooleanSelector(),
                vol.Required(CONF_OVERVIEW, default=current.overview): BooleanSelector(),
                vol.Required(CONF_STORAGE, default=current.storage): BooleanSelector(),
                vol.Required(
                    CONF_MONTHLY_CREDITS, default=current.monthly_credits
                ): NumberSelector(
                    NumberSelectorConfig(min=100, max=1_000_000, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_BILLING_DAY, default=current.billing_day): NumberSelector(
                    NumberSelectorConfig(min=1, max=31, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_RESERVE_PERCENT, default=current.reserve_percent
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=50, step=1, unit_of_measurement="%", mode=NumberSelectorMode.SLIDER
                    )
                ),
                vol.Required(CONF_MIN_INTERVAL, default=current.min_interval): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_INTERVAL_FLOOR,
                        max=120,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The settings form."""
        entry = self.config_entry
        current = Options.from_mapping(dict(entry.options), default_billing_day(entry))
        if user_input is not None:
            self._pending = {
                **user_input,
                CONF_MONTHLY_CREDITS: int(user_input[CONF_MONTHLY_CREDITS]),
                CONF_BILLING_DAY: int(user_input[CONF_BILLING_DAY]),
                CONF_BILLING_CONFIRMED: True,
                CONF_MIN_INTERVAL: int(user_input[CONF_MIN_INTERVAL]),
                CONF_RESERVE_PERCENT: int(user_input[CONF_RESERVE_PERCENT]),
            }
            return await self.async_step_confirm()
        coordinator = entry.runtime_data
        return self.async_show_form(
            step_id="init",
            data_schema=self._schema(current),
            description_placeholders=options_preview(coordinator, dict(entry.options)),
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what the new settings mean for the polling interval, then save."""
        if user_input is not None:
            return self.async_create_entry(data=self._pending)
        coordinator = self.config_entry.runtime_data
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=options_preview(coordinator, self._pending),
        )

