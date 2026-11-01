"""Static configuration: endpoints, headers, timeouts, cache location and TLS policy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IranQuantConfig:
    """Transport security policy.

    Values are read from the environment when omitted, and explicit keyword
    arguments take precedence.

    Args:
        verify_ssl: Verify upstream TLS certificates. Defaults to
            ``IRANQUANT_VERIFY_SSL`` or ``True``.
        allow_tls_fallback: Retry once over an unverified transport when
            certificate verification fails. Defaults to
            ``IRANQUANT_ALLOW_TLS_FALLBACK`` or ``True``.

    Examples:
        >>> IranQuantConfig(allow_tls_fallback=False).allow_tls_fallback
        False
    """

    verify_ssl: bool = field(default_factory=lambda: _env_flag("IRANQUANT_VERIFY_SSL", True))
    allow_tls_fallback: bool = field(
        default_factory=lambda: _env_flag("IRANQUANT_ALLOW_TLS_FALLBACK", True)
    )


TSETMC_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
    "Origin": "https://tsetmc.com",
    "Referer": "https://tsetmc.com/",
}

CODAL_HEADERS: dict[str, str] = {
    **TSETMC_HEADERS,
    "Origin": "https://codal.ir",
    "Referer": "https://codal.ir/",
}

TSETMC_CDN = "https://cdn.tsetmc.com"
TSETMC_WWW = "https://tsetmc.com"
CODAL_BASE = "https://codal.ir"
CODAL_SEARCH_URL = "https://search.codal.ir/api/search/v2/q"

SYMBOL_SEARCH_URL = TSETMC_CDN + "/api/Instrument/GetInstrumentSearch/{query}"
DAILY_HISTORY_URL = TSETMC_CDN + "/api/ClosingPrice/GetClosingPriceDailyList/{ins_code}/0"
CLIENT_TYPE_HISTORY_URL = TSETMC_CDN + "/api/ClientType/GetClientTypeHistory/{ins_code}"
SHARE_CHANGE_URL = TSETMC_CDN + "/api/Instrument/GetInstrumentShareChange/{ins_code}"
DPS_URL = TSETMC_CDN + "/api/Instrument/GetInstrumentDPS/{ins_code}"
CLOSING_PRICE_INFO_URL = TSETMC_CDN + "/api/ClosingPrice/GetClosingPriceInfo/{ins_code}"
TRADE_INTRADAY_URL = TSETMC_CDN + "/api/Trade/GetTradeIntraDay/{ins_code}"
# Legacy trade-by-trade endpoint (requires a date and an isGrouped flag).
TRADE_HISTORY_URL = TSETMC_CDN + "/api/Trade/GetTradeHistory/{ins_code}/{date}/{grouped}"
BEST_LIMITS_URL = TSETMC_CDN + "/api/BestLimits/{ins_code}"
INDEX_SECTORS_LIVE_URL = TSETMC_CDN + "/api/Index/GetIndexB1LastAll/0/1"
INDEX_LIVE_DAY_URL = TSETMC_CDN + "/api/Index/GetIndexB1LastDay/{index_code}"
MARKET_OVERVIEW_URL = TSETMC_CDN + "/api/MarketData/GetMarketOverview/{flow}"
INDEX_B2_HISTORY_URL = TSETMC_CDN + "/api/Index/GetIndexB2History/{index_code}"
INDEX_B1_HISTORY_URL = TSETMC_CDN + "/api/Index/GetIndexB1History/{index_code}"

# The MarketWatch endpoint must be requested verbatim: the brackets in
# ``paperTypes[0]`` must not be percent-encoded.
MARKET_WATCH_URL = (
    TSETMC_CDN
    + "/api/ClosingPrice/GetMarketWatch"
    + "?market=0&industrialGroup="
    + "&paperTypes[0]=1&paperTypes[1]=2&paperTypes[2]=8"
    + "&showTraded=false&withBestLimits=true"
)

# Well-known TSETMC index code for the overall market index (شاخص کل).
OVERALL_INDEX_CODE = "32097828799138957"
# Equal-weight overall index (شاخص هم‌وزن).
EQUAL_WEIGHT_INDEX_CODE = "43685683301327984"

DEFAULT_TIMEOUT = 30.0
MAX_CONCURRENCY = 10
MAX_RETRIES = 3
RETRY_WAIT = 0.5

CACHE_DIR = Path(os.environ.get("IRANQUANT_CACHE_DIR", str(Path.home() / ".cache" / "iranquant")))
