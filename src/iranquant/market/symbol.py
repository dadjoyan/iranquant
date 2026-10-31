"""Symbol search and instrument-code resolution with in-memory caching."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from iranquant.config import SYMBOL_SEARCH_URL
from iranquant.core.client import AsyncHTTPClient
from iranquant.core.normalizer import (
    int_to_date_optional,
    int_to_jdate_optional,
    normalize_persian,
)
from iranquant.exceptions import ParseError, SymbolNotFoundError
from iranquant.models.tsetmc import InstrumentSearchItem, InstrumentSearchResponse

# TSETMC "flow" values that represent tradable instruments.
ACTIVE_FLOWS = frozenset({1, 2, 4})

# Module-level caches. Symbol lookups are cheap and safe to memoize for the
# lifetime of the process.
_search_cache: dict[str, list[InstrumentSearchItem]] = {}
_inscode_cache: dict[str, str] = {}


def instrument_to_dict(item: InstrumentSearchItem) -> dict[str, Any]:
    """Standardize a search result into a user-facing record.

    Args:
        item: A raw :class:`~iranquant.models.tsetmc.InstrumentSearchItem` from
            the TSETMC search payload.

    Returns:
        A dictionary with the following keys:

        * ``ins_code`` (``str``) — TSETMC instrument code.
        * ``symbol`` (``str``) — ``lVal18AFC`` ticker, e.g. ``"فولاد"``.
        * ``company_name`` (``str``) — full company name.
        * ``isin`` (``str | None``) — international securities code.
        * ``shares`` (``float``) — total listed shares.
        * ``flow`` (``int``) — TSETMC flow id (1, 2 and 4 are tradable).
        * ``board`` (``str``) — market board code (``cgrValCot``).
        * ``last_date`` (``datetime.date | None``) — last trade date.
        * ``last_date_jdate`` (``str | None``) — Jalali last trade date.

    Examples:
        >>> record = instrument_to_dict(response.instrumentSearch[0])
        >>> record["symbol"]
        'فولاد'
    """

    return {
        "ins_code": item.insCode,
        "symbol": normalize_persian(item.lVal18AFC),
        "company_name": normalize_persian(item.lVal30),
        "isin": item.cIsin,
        "shares": item.zTitad,
        "flow": item.flow,
        "board": item.cgrValCot,
        "last_date": int_to_date_optional(item.lastDate),
        "last_date_jdate": int_to_jdate_optional(item.lastDate),
    }


async def search_symbol(
    client: AsyncHTTPClient,
    query: str,
) -> list[InstrumentSearchItem]:
    """Search TSETMC instruments by symbol or company name.

    The query is Persian-normalized before it is sent.  Results are memoized
    in-process by normalized query, so repeated searches do not hit the
    network.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        query: Symbol or company-name fragment, e.g. ``"فولاد"`` or ``"خودرو"``.

    Returns:
        Matching instruments.  An empty query returns an empty list without a
        request.

    Raises:
        NetworkError: If ``cdn.tsetmc.com`` cannot be reached.
        ParseError: If the response does not match the expected schema.

    Examples:
        >>> items = await search_symbol(client, "فولاد")
        >>> items[0].lVal18AFC
        'فولاد'
    """

    normalized = normalize_persian(query)
    if not normalized:
        return []
    if normalized in _search_cache:
        return list(_search_cache[normalized])

    url = SYMBOL_SEARCH_URL.format(query=quote(normalized, safe=""))
    payload = await client.get_json(url)
    try:
        response = InstrumentSearchResponse.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError
        raise ParseError(f"Unexpected instrument search payload for {query!r}") from exc

    items = response.instrumentSearch
    _search_cache[normalized] = items
    return list(items)


def _is_active(item: InstrumentSearchItem) -> bool:
    return item.flow in ACTIVE_FLOWS


async def get_inscode(client: AsyncHTTPClient, symbol: str) -> str:
    """Resolve a symbol to its TSETMC instrument code (``insCode``).

    Matching is done on normalized ``lVal18AFC`` values.  Instruments with an
    active ``flow`` (1, 2 or 4) are preferred; ties are broken by the newest
    ``lastDate``.  Results are cached in-process.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        symbol: Persian ticker, e.g. ``"فولاد"``.  Arabic yeh/kaf variants and
            Persian digits are normalized automatically.

    Returns:
        The numeric ``insCode`` as a string, e.g. ``"46348559193224090"``.

    Raises:
        SymbolNotFoundError: If the symbol is empty or no exact normalized
            match exists.
        NetworkError: If the search request fails.
        ParseError: If the search response is malformed.

    Examples:
        >>> code = await get_inscode(client, "فولاد")
        >>> code.isdigit()
        True
    """

    normalized = normalize_persian(symbol)
    if not normalized:
        raise SymbolNotFoundError("Empty symbol")

    cached = _inscode_cache.get(normalized)
    if cached is not None:
        return cached

    items = await search_symbol(client, normalized)
    matches = [item for item in items if normalize_persian(item.lVal18AFC) == normalized]
    if not matches:
        raise SymbolNotFoundError(f"Symbol not found: {symbol!r}")

    matches.sort(key=lambda item: (_is_active(item), item.lastDate or 0), reverse=True)
    code = matches[0].insCode
    _inscode_cache[normalized] = code
    return code


def clear_cache() -> None:
    """Clear the in-memory symbol caches (mainly useful in tests).

    Clears both the search-results cache and the ``insCode`` resolution cache
    for the current process.

    Examples:
        >>> clear_cache()
    """

    _search_cache.clear()
    _inscode_cache.clear()
