"""SolarEdge Site Access (OAuth 2.0) helpers.

Facts verified against the live API on 2026-10-16:

* SolarEdge Connect does NOT echo the ``state`` parameter on the redirect; it
  returns only ``code`` and ``site_id``. Home Assistant's built-in OAuth flow
  therefore cannot be used; the user pastes the redirect URL instead.
* The token endpoint accepts a JSON body with ``client_id``/``client_secret``.
* Access tokens live 7200 s. Every refresh returns a NEW refresh token and the
  old one is rejected immediately (``invalid_request: Invalid refresh_token``),
  so the new one must be persisted before anything else happens.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from aiohttp import ClientError, ClientSession

from .exceptions import SolarEdgeConnectionError, SolarEdgeOAuthError

AUTHORIZE_URL = "https://connect.solaredge.com/authorize"
TOKEN_URL = "https://monitoringapi.solaredge.com/v2/oauth2/token"
SCOPES = "SITE_DATA DEVICE_DATA"
ACCESS_DURATION_MONTHS = 24
DEFAULT_REDIRECT_URI = "http://localhost/callback"
TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class TokenSet:
    """One access/refresh token pair and when it was issued."""

    access_token: str
    refresh_token: str
    expires_at: float
    issued_at: float

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "issued_at": self.issued_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet:
        """Restore from storage."""
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=float(data.get("expires_at", 0)),
            issued_at=float(data.get("issued_at", 0)),
        )

    def valid_for(self, now: float | None = None) -> float:
        """Seconds the access token stays valid."""
        return self.expires_at - (time.time() if now is None else now)


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Build the consent URL the user opens in a browser."""
    return f"{AUTHORIZE_URL}?" + urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "access_duration": str(ACCESS_DURATION_MONTHS),
            "state": state,
        }
    )


class RedirectParseError(ValueError):
    """The pasted text holds no authorization code."""


def parse_redirect(text: str) -> tuple[str, str | None]:
    """Return (code, site_id) from a pasted redirect URL or a bare code."""
    text = text.strip()
    if not text:
        raise RedirectParseError("empty")
    if "://" not in text and "code=" not in text:
        return text, None
    query = urlparse(text).query if "://" in text else text.lstrip("?")
    params = parse_qs(query)
    if "error" in params:
        raise RedirectParseError(params["error"][0])
    code = (params.get("code") or [None])[0]
    if not code:
        raise RedirectParseError("no code")
    site_id = (params.get("site_id") or [None])[0]
    return code, site_id


async def _token_request(session: ClientSession, payload: dict[str, str]) -> TokenSet:
    try:
        async with asyncio.timeout(TIMEOUT):
            response = await session.post(
                TOKEN_URL, json=payload, headers={"Accept": "application/json"}
            )
            async with response:
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = None
                status = response.status
    except TimeoutError as err:
        raise SolarEdgeConnectionError("Timeout calling the token endpoint") from err
    except ClientError as err:
        raise SolarEdgeConnectionError(f"Error calling the token endpoint: {err}") from err

    body = body if isinstance(body, dict) else {}
    if status >= 500 or status == 429:
        raise SolarEdgeConnectionError(f"Token endpoint answered HTTP {status}")
    if status != 200 or not body.get("access_token") or not body.get("refresh_token"):
        raise SolarEdgeOAuthError(
            status,
            str(body.get("error") or "invalid_response"),
            str(body.get("error_description") or ""),
        )
    now = time.time()
    return TokenSet(
        access_token=str(body["access_token"]),
        refresh_token=str(body["refresh_token"]),
        expires_at=now + float(body.get("expires_in") or 7200),
        issued_at=now,
    )


async def exchange_code(
    session: ClientSession, client_id: str, client_secret: str, code: str, redirect_uri: str
) -> TokenSet:
    """Trade a one-time authorization code for tokens."""
    return await _token_request(
        session,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )


async def refresh_tokens(
    session: ClientSession, client_id: str, client_secret: str, refresh_token: str
) -> TokenSet:
    """Use (and thereby invalidate) a refresh token."""
    return await _token_request(
        session,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
