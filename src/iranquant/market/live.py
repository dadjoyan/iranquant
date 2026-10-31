"""Live market data: MarketWatch, order book and intraday trades."""

from __future__ import annotations

from typing import Any

from iranquant.config import (
    BEST_LIMITS_URL,
    MARKET_WATCH_URL,
    TRADE_INTRADAY_URL,
)
from iranquant.core.client import AsyncHTTPClient
from iranquant.core.dataframe import DataFrame, Engine, to_dataframe
from iranquant.core.normalizer import int_to_time_optional, normalize_persian
from iranquant.models.tsetmc import (
    BestLimitResponse,
    MarketWatchResponse,
    TradeIntraDayResponse,
)


def _first_value(*values: float | None) -> float:
    """Return the first value that is not ``None`` (older/newer field names)."""

    for value in values:
        if value is not None:
            return value
    return 0.0


async def get_market_watch(
    client: AsyncHTTPClient,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Fetch the entire market snapshot in a single request.

    The request is sent verbatim (``raw=True``) so the bracketed
    ``paperTypes[0]=1`` query is never percent-encoded.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        One row per symbol with the following columns:

        =================  ==========================  =======================
        Column             Type                        Description
        =================  ==========================  =======================
        ``ins_code``       ``str``                     TSETMC instrument code.
        ``symbol``         ``str``                     Ticker (``lva``).
        ``company_name``   ``str``                     Company name (``lvc``).
        ``eps``            ``float | None``            Earnings per share.
        ``pe``             ``float | None``            Price-to-earnings ratio.
        ``open``           ``float``                   Opening price (``pf``).
        ``high``           ``float``                   Daily high (``pmx``).
        ``low``            ``float``                   Daily low (``pmn``).
        ``close``          ``float``                   Last trade (``pdv``).
        ``final_price``    ``float``                   Closing price (``pcl``).
        ``prev_price``     ``float``                   Previous close (``py``).
        ``volume``         ``float``                   Volume (``qtj``/``qtt``).
        ``value``          ``float``                   Value in Rials (``qtc``).
        ``trades``         ``float``                   Trade count (``ztt``).
        ``tmax``           ``float``                   High limit (``pMax``/``ps``).
        ``tmin``           ``float``                   Low limit (``pMin``/``pi``).
        ``total_shares``   ``float``                   Listed shares (``ztd``).
        ``order_book``     ``list[dict[str, float]]``  Best-limit levels.
        =================  ==========================  =======================

        ``order_book`` dictionaries expose ``n``, ``qmd``, ``zmd``, ``pmd``,
        ``pmo``, ``zmo`` and ``qmo`` for levels 1..5, sorted by ``n``.

    Raises:
        NetworkError: If the MarketWatch endpoint cannot be reached.
        ParseError: If the response body is not valid JSON.
        pydantic.ValidationError: If the payload does not match the schema.

    Examples:
        >>> market = await get_market_watch(client)
        >>> market[["symbol", "close", "tmax"]].head()
        >>> book = market.iloc[0]["order_book"]
        >>> book[0]["zmd"]  # best bid volume
    """

    payload = await client.get_json(MARKET_WATCH_URL, raw=True)
    response = MarketWatchResponse.model_validate(payload)

    records: list[dict[str, Any]] = []
    for item in response.marketwatch:
        order_book = sorted(item.blDs, key=lambda level: level.n)
        records.append(
            {
                "ins_code": item.insCode,
                "symbol": normalize_persian(item.lva),
                "company_name": normalize_persian(item.lvc),
                "eps": item.eps,
                "pe": item.pe,
                "open": item.pf,
                "high": item.pmx,
                "low": item.pmn,
                "close": item.pdv,
                "final_price": item.pcl,
                "prev_price": item.py,
                # Current servers use qtj/pMax/pMin; fall back to legacy names.
                "volume": _first_value(item.qtj, item.qtt),
                "value": item.qtc,
                "trades": item.ztt,
                "tmax": _first_value(item.pMax, item.ps),
                "tmin": _first_value(item.pMin, item.pi),
                "total_shares": item.ztd,
                "order_book": [level.model_dump() for level in order_book],
            }
        )
    return to_dataframe(records, engine)


async def get_order_book(
    client: AsyncHTTPClient,
    ins_code: str,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Fetch the best-limit order book (levels 1..5) for an instrument.

    Rows are sorted by level number.  The ``d``/``o`` suffixes mean *demand*
    (buy) and *offer* (sell) respectively.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        ins_code: TSETMC instrument code.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A frame with one row per visible level and these columns:

        * ``n`` (``int``) — level number (1 is the best level).
        * ``qmd`` (``float``) — bid order count.
        * ``zmd`` (``float``) — bid volume.
        * ``pmd`` (``float``) — bid price.
        * ``pmo`` (``float``) — ask price.
        * ``zmo`` (``float``) — ask volume.
        * ``qmo`` (``float``) — ask order count.

    Raises:
        NetworkError: If the endpoint cannot be reached.
        ParseError: If the response body is not valid JSON.
        pydantic.ValidationError: If the payload does not match the schema.

    Examples:
        >>> book = await get_order_book(client, "46348559193224090")
        >>> book.iloc[0][["pmd", "zmd", "pmo", "zmo"]]
    """

    payload = await client.get_json(BEST_LIMITS_URL.format(ins_code=ins_code))
    response = BestLimitResponse.model_validate(payload)

    records = [
        {
            "n": level.number,
            "qmd": level.qTitMeDem,
            "zmd": level.zOrdMeDem,
            "pmd": level.pMeDem,
            "pmo": level.pMeOf,
            "zmo": level.zOrdMeOf,
            "qmo": level.qTitMeOf,
        }
        for level in sorted(response.bestLimits, key=lambda level: level.number)
    ]
    return to_dataframe(records, engine)


async def get_intraday_trades(
    client: AsyncHTTPClient,
    ins_code: str,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Fetch intraday OHLCV bars for the current/last trading session.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        ins_code: TSETMC instrument code.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        One row per intraday bar, in server order, with these columns:

        * ``time`` (``datetime.time | None``) — bar time from ``hEven``.
        * ``open`` (``float``) — first price in the bar.
        * ``high`` (``float``) — highest price in the bar.
        * ``low`` (``float``) — lowest price in the bar.
        * ``close`` (``float``) — last price in the bar.
        * ``volume`` (``float``) — traded volume in the bar.

    Raises:
        NetworkError: If the endpoint cannot be reached.
        ParseError: If the response body is not valid JSON.
        pydantic.ValidationError: If the payload does not match the schema.

    Examples:
        >>> bars = await get_intraday_trades(client, "46348559193224090")
        >>> bars[["time", "close", "volume"]].tail()
    """

    payload = await client.get_json(TRADE_INTRADAY_URL.format(ins_code=ins_code))
    response = TradeIntraDayResponse.model_validate(payload)

    records = [
        {
            "time": int_to_time_optional(bar.hEven),
            "open": bar.openPrice,
            "high": bar.maxPrice,
            "low": bar.minPrice,
            "close": bar.closePrice,
            "volume": bar.volume,
        }
        for bar in response.tradeIntraDay
    ]
    return to_dataframe(records, engine)
