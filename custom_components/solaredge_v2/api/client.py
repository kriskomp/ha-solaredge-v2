"""Async client for the SolarEdge Monitoring API v2.

Facts verified against the live API on 2026-10-16:

* ``from``/``to`` must be an Instant (``2026-10-16T05:53:19Z`` or with an
  offset). Offset-free local time is rejected with HTTP 400.
* QUARTER_HOUR data for a whole day (more than 12 h) is accepted for
  ``/power`` and ``/inverters/telemetry``.
* ``resolution=MONTH`` accepts at most 3 years; ``YEAR`` is unlimited.
* The only rate-limit headers are ``x-ratelimit-limit-minute`` (10 on the Free
  tier) and ``x-ratelimit-remaining-minute``. There is no monthly counter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession

from .exceptions import (
    SolarEdgeAuthenticationError,
    SolarEdgeConnectionError,
    SolarEdgeCreditLimitError,
    SolarEdgeForbiddenError,
    SolarEdgeRateLimitError,
    SolarEdgeResponseError,
)

BASE_URL = "https://monitoringapi.solaredge.com/v2"
DEFAULT_TIMEOUT = 30.0

TokenGetter = Callable[[], Awaitable[str]]
CallListener = Callable[[str, int], None]


def instant(value: datetime) -> str:
    """Format an aware datetime as the UTC Instant the API expects."""
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def window(start: datetime, end: datetime) -> list[tuple[str, str]]:
    """The from/to query pair."""
    return [("from", instant(start)), ("to", instant(end))]


class SolarEdgeClient:
    """One authenticated view of the V2 API; every call that reaches it costs a credit."""

    def __init__(
        self,
        session: ClientSession,
        token_getter: TokenGetter,
        *,
        on_call: CallListener | None = None,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Use the caller's session; the client never creates or closes one."""
        self._session = session
        self._token_getter = token_getter
        self._on_call = on_call
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self.remaining_minute: int | None = None

    async def request(self, path: str, params: Sequence[tuple[str, str]] | None = None) -> Any:
        """GET a path and return the decoded JSON body."""
        token = await self._token_getter()
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._session.get(
                    f"{self._base_url}{path}", params=list(params or ()), headers=headers
                )
                async with response:
                    remaining = response.headers.get("x-ratelimit-remaining-minute")
                    if remaining is not None and remaining.strip().isdigit():
                        self.remaining_minute = int(remaining)
                    if self._on_call is not None:
                        self._on_call(path, response.status)
                    if response.status >= 400:
                        raise await _error_for(response)
                    return await response.json(content_type=None)
        except TimeoutError as err:
            raise SolarEdgeConnectionError(f"Timeout calling {path}") from err
        except ClientError as err:
            raise SolarEdgeConnectionError(f"Error calling {path}: {err}") from err
        except ValueError as err:
            raise SolarEdgeResponseError(200, f"Invalid JSON from {path}") from err

    async def site(self, site_id: int | str) -> Any:
        """GET /sites/{id}."""
        return await self.request(f"/sites/{site_id}")

    async def devices(self, site_id: int | str) -> Any:
        """GET /sites/{id}/devices for inverters, meters and batteries."""
        return await self.request(
            f"/sites/{site_id}/devices",
            [("types", "INVERTER"), ("types", "METER"), ("types", "BATTERY")],
        )

    async def inverter_telemetry(self, site_id: int | str, start: datetime, end: datetime) -> Any:
        """GET /sites/{id}/inverters/telemetry at QUARTER_HOUR.

        One call returns power, energy, voltage, current and frequency per inverter.
        """
        return await self.request(
            f"/sites/{site_id}/inverters/telemetry",
            [("resolution", "QUARTER_HOUR"), *window(start, end)],
        )

    async def storage_telemetry(self, site_id: int | str, start: datetime, end: datetime) -> Any:
        """GET /sites/{id}/storage/telemetry at QUARTER_HOUR."""
        return await self.request(
            f"/sites/{site_id}/storage/telemetry",
            [("resolution", "QUARTER_HOUR"), *window(start, end)],
        )

    async def overview(self, site_id: int | str) -> Any:
        """GET /sites/{id}/overview (midnight to now, site time)."""
        return await self.request(f"/sites/{site_id}/overview")

    async def energy(
        self, site_id: int | str, resolution: str, start: datetime, end: datetime
    ) -> Any:
        """GET /sites/{id}/energy in Wh."""
        return await self.request(
            f"/sites/{site_id}/energy",
            [("resolution", resolution), ("unit", "WH"), *window(start, end)],
        )

    async def open_alerts(self, site_id: int | str) -> Any:
        """GET /sites/{id}/alerts, open alerts only."""
        return await self.request(
            f"/sites/{site_id}/alerts", [("only-open", "true"), ("alerts-in-page", "100")]
        )


async def _error_for(response: ClientResponse) -> SolarEdgeResponseError:
    status = response.status
    try:
        body = await response.json(content_type=None)
    except (ClientError, ValueError):
        body = None
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("detail") or body.get("message") or body.get("title") or "")
    detail = detail or _phrase(status)
    if status == 401:
        return SolarEdgeAuthenticationError(status, detail)
    if status == 403:
        return SolarEdgeForbiddenError(status, detail)
    if status == 429:
        remaining = response.headers.get("x-ratelimit-remaining-minute")
        retry_after = response.headers.get("retry-after")
        # Per-minute window: remaining is 0 and retry-after is sent.
        # Monthly quota: remaining is above 0 and no retry-after.
        if (
            remaining is not None
            and remaining.strip() not in ("", "0")
            and not retry_after
        ):
            return SolarEdgeCreditLimitError(status, detail)
        return SolarEdgeRateLimitError(
            status, detail, int(retry_after) if retry_after and retry_after.isdigit() else None
        )
    return SolarEdgeResponseError(status, detail)


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "error"
