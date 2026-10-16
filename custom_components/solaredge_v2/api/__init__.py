"""SolarEdge Monitoring API v2 client (no Home Assistant imports)."""

from .client import BASE_URL, SolarEdgeClient, instant, window
from .exceptions import (
    SolarEdgeAuthenticationError,
    SolarEdgeConnectionError,
    SolarEdgeCreditLimitError,
    SolarEdgeError,
    SolarEdgeForbiddenError,
    SolarEdgeOAuthError,
    SolarEdgeRateLimitError,
    SolarEdgeResponseError,
)
from .models import (
    Alert,
    Device,
    Sample,
    Series,
    SiteMetadata,
    SiteOverview,
    Telemetry,
    parse_telemetry,
)
from .oauth import (
    DEFAULT_REDIRECT_URI,
    RedirectParseError,
    TokenSet,
    authorize_url,
    exchange_code,
    parse_redirect,
    refresh_tokens,
)

__all__ = [
    "BASE_URL",
    "DEFAULT_REDIRECT_URI",
    "Alert",
    "Device",
    "RedirectParseError",
    "Sample",
    "Series",
    "SiteMetadata",
    "SiteOverview",
    "SolarEdgeAuthenticationError",
    "SolarEdgeClient",
    "SolarEdgeConnectionError",
    "SolarEdgeCreditLimitError",
    "SolarEdgeError",
    "SolarEdgeForbiddenError",
    "SolarEdgeOAuthError",
    "SolarEdgeRateLimitError",
    "SolarEdgeResponseError",
    "Telemetry",
    "TokenSet",
    "authorize_url",
    "exchange_code",
    "instant",
    "parse_redirect",
    "parse_telemetry",
    "refresh_tokens",
    "window",
]
