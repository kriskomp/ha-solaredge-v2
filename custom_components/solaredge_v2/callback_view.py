"""Receive the SolarEdge consent redirect inside Home Assistant.

SolarEdge Connect redirects the browser to the registered Redirect URL with
``code`` and ``site_id`` but WITHOUT the OAuth ``state`` parameter, so Home
Assistant's shared OAuth callback (and my.home-assistant.io) cannot route it.
This view does the routing itself: it hands the query to the setup or
re-authentication flow of this integration that is waiting in its external
step, then shows a page the user can close.

The redirect is followed by the user's browser, not by SolarEdge's servers, so
the address only needs to be reachable from that browser. SolarEdge accepts
HTTPS addresses on a registered domain (or ``http://localhost``).
"""

from __future__ import annotations

from html import escape
from http import HTTPStatus

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView

from .const import CALLBACK_PATH, DOMAIN, LOGGER

EXTERNAL_STEP = "external"

_PAGE = """<!doctype html>
<html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SolarEdge</title>
<style>
body{{font-family:system-ui,sans-serif;display:flex;min-height:100vh;margin:0;
align-items:center;justify-content:center;background:#f5f5f5;color:#222}}
main{{max-width:28rem;padding:2rem;background:#fff;border-radius:12px;
box-shadow:0 2px 12px rgba(0,0,0,.08);text-align:center}}
h1{{font-size:1.3rem}} p{{line-height:1.5}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}}main{{background:#1c1c1c}}}}
</style></head>
<body><main><h1>{title}</h1><p>{text}</p></main>
<script>{script}</script></body></html>"""


def _page(title: str, text: str, *, close: bool) -> web.Response:
    script = "setTimeout(function(){window.close();},1500);" if close else ""
    return web.Response(
        text=_PAGE.format(title=escape(title), text=escape(text), script=script),
        content_type="text/html",
        status=HTTPStatus.OK,
    )


class SolarEdgeCallbackView(HomeAssistantView):
    """GET /api/solaredge_v2/callback?code=...&site_id=..."""

    url = CALLBACK_PATH
    name = "api:solaredge_v2:callback"
    requires_auth = False  # the browser arrives from SolarEdge without an HA session

    async def get(self, request: web.Request) -> web.Response:
        """Pass the code to the waiting flow."""
        hass: HomeAssistant = request.app[KEY_HASS]
        query = request.query
        if "error" in query:
            return _page(
                "Dostęp nie został przyznany",
                f"SolarEdge zwrócił błąd: {query.get('error')}. Wróć do Home Assistant.",
                close=False,
            )
        if "code" not in query:
            return _page("Brak kodu", "W adresie brakuje kodu autoryzacji.", close=False)

        waiting = [
            flow
            for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            if flow.get("step_id") == EXTERNAL_STEP
        ]
        if not waiting:
            return _page(
                "Brak oczekującej konfiguracji",
                "Nie znaleziono konfiguracji SolarEdge czekającej na zgodę. "
                "Rozpocznij dodawanie integracji w Home Assistant jeszcze raz.",
                close=False,
            )
        # No `state` from SolarEdge: hand the code to the most recent waiting flow.
        flow_id = waiting[-1]["flow_id"]
        try:
            await hass.config_entries.flow.async_configure(
                flow_id, {"redirect_url": request.query_string}
            )
        except UnknownFlow:
            LOGGER.debug("Flow %s vanished before the SolarEdge callback", flow_id)
            return _page("Konfiguracja wygasła", "Spróbuj jeszcze raz.", close=False)
        return _page(
            "Gotowe",
            "Zgoda przekazana do Home Assistant. To okno możesz zamknąć.",
            close=True,
        )
