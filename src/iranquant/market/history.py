"""Daily OHLCV, client-type history and backward price adjustment."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, cast

import pandas as pd
import polars as pl

from iranquant.config import (
    CLIENT_TYPE_HISTORY_URL,
    DAILY_HISTORY_URL,
    DPS_URL,
    SHARE_CHANGE_URL,
)
from iranquant.core.client import AsyncHTTPClient
from iranquant.core.dataframe import DataFrame, Engine, to_dataframe
from iranquant.core.normalizer import (
    MIN_EVEN_DATE,
    int_to_date,
    int_to_date_optional,
    int_to_jdate,
)
from iranquant.exceptions import ParseError
from iranquant.models.tsetmc import (
    ClientTypeResponse,
    ClosingPriceDailyResponse,
    InstrumentDPS,
    InstrumentDPSResponse,
    InstrumentShareChange,
    InstrumentShareChangeResponse,
)

AdjustMode = Literal["none", "all", "capital_only"]

PRICE_COLUMNS = ("open", "high", "low", "close", "final_price", "prev_price")
HISTORY_COLUMNS = [
    "date",
    "jdate",
    "open",
    "high",
    "low",
    "close",
    "final_price",
    "prev_price",
    "volume",
    "value",
    "trades",
]
CLIENT_TYPE_COLUMNS = [
    "date",
    "jdate",
    "buy_I_Volume",
    "buy_N_Volume",
    "buy_I_Value",
    "buy_N_Value",
    "buy_N_Count",
    "buy_I_Count",
    "sell_I_Volume",
    "sell_N_Volume",
    "sell_I_Value",
    "sell_N_Value",
    "sell_N_Count",
    "sell_I_Count",
]


def _validate(model: type[Any], payload: Any, label: str) -> Any:
    try:
        return model.model_validate(payload)
    except Exception as exc:  # pydantic.ValidationError
        raise ParseError(f"Unexpected {label} payload") from exc


def apply_gap_adjustment(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backward-adjust prices using the ``priceYesterday`` gap ratio.

    ``priceYesterday`` on day *t* already reflects any dividend or capital
    increase applied between *t-1* and *t*, while ``final_price`` on day *t-1*
    is unadjusted.  The cumulative factor is therefore computed backwards from
    the newest day (factor 1.0) and applied to all price columns.

    For ``i = n-2 ... 0`` the per-day ratio is
    ``r[i] = prev_price[i+1] / final_price[i]`` and the cumulative factor is
    ``F[i] = F[i+1] * r[i]``.  Every column in ``PRICE_COLUMNS``
    (``open``, ``high``, ``low``, ``close``, ``final_price``, ``prev_price``)
    is multiplied by ``F[i]``.  The input list is never mutated.

    Args:
        records: Chronologically ascending daily records produced by
            :func:`_daily_records`.  Each record must contain the keys in
            ``PRICE_COLUMNS``.

    Returns:
        A new list of copied records with adjusted float prices.  Records with
        fewer than two rows are returned as shallow copies.

    Examples:
        >>> rows = [
        ...     {"final_price": 100.0, "prev_price": 100.0, "open": 100.0,
        ...      "high": 100.0, "low": 100.0, "close": 100.0},
        ...     {"final_price": 100.0, "prev_price": 90.0, "open": 100.0,
        ...      "high": 100.0, "low": 100.0, "close": 100.0},
        ... ]
        >>> apply_gap_adjustment(rows)[0]["final_price"]
        90.0
    """

    if len(records) < 2:
        return [dict(record) for record in records]

    factors = [1.0] * len(records)
    for index in range(len(records) - 2, -1, -1):
        previous_final = float(records[index]["final_price"])
        current_prev = float(records[index + 1]["prev_price"])
        ratio = current_prev / previous_final if previous_final > 0 and current_prev > 0 else 1.0
        factors[index] = factors[index + 1] * ratio

    adjusted: list[dict[str, Any]] = []
    for factor, record in zip(factors, records, strict=True):
        row = dict(record)
        for column in PRICE_COLUMNS:
            row[column] = float(record[column]) * factor
        adjusted.append(row)
    return adjusted


def apply_capital_adjustment(
    records: list[dict[str, Any]],
    share_changes: list[InstrumentShareChange],
) -> list[dict[str, Any]]:
    """Adjust prices backwards for capital increases only.

    For an increase from ``numberOfShareOld`` to ``numberOfShareNew`` the
    historical prices preceding the event are multiplied by
    ``old / new`` (the theoretical ex-rights ratio).

    Events with non-positive share counts or an invalid ``dEven`` are ignored.
    A record exactly on the event date is *not* adjusted; only strictly earlier
    records are.  The input list is never mutated.

    Args:
        records: Chronologically ascending daily records with a
            ``datetime.date`` under the ``"date"`` key.
        share_changes: Corporate-action events from
            :func:`get_share_changes`.

    Returns:
        A new list of copied records whose ``PRICE_COLUMNS`` are scaled by the
        cumulative ``old / new`` factors of all later events.

    Examples:
        >>> event = InstrumentShareChange(
        ...     dEven=20261028, numberOfShareOld=100.0, numberOfShareNew=200.0
        ... )
        >>> apply_capital_adjustment(records, [event])[0]["open"]
        50.0
    """

    events: list[tuple[dt.date, InstrumentShareChange]] = []
    for event in share_changes:
        if event.numberOfShareOld <= 0 or event.numberOfShareNew <= 0:
            continue
        event_date = int_to_date_optional(event.dEven)
        if event_date is None:
            continue
        events.append((event_date, event))
    events.sort(key=lambda pair: pair[0])

    adjusted: list[dict[str, Any]] = []
    for record in records:
        row_date = cast(dt.date, record["date"])
        factor = 1.0
        for event_date, event in events:
            if row_date < event_date:
                factor *= event.numberOfShareOld / event.numberOfShareNew
        row = dict(record)
        for column in PRICE_COLUMNS:
            row[column] = float(record[column]) * factor
        adjusted.append(row)
    return adjusted


async def get_share_changes(
    client: AsyncHTTPClient,
    ins_code: str,
) -> list[InstrumentShareChange]:
    """Fetch the capital-increase history of an instrument.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        ins_code: TSETMC instrument code.

    Returns:
        Share-change events, each exposing ``dEven``, ``numberOfShareOld`` and
        ``numberOfShareNew``.

    Raises:
        NetworkError: If the endpoint cannot be reached.
        ParseError: If the payload does not match the expected schema.

    Examples:
        >>> events = await get_share_changes(client, "46348559193224090")
        >>> events[0].numberOfShareNew
        2000000000000.0
    """
    payload = await client.get_json(SHARE_CHANGE_URL.format(ins_code=ins_code))
    response = cast(
        InstrumentShareChangeResponse,
        _validate(InstrumentShareChangeResponse, payload, "share change"),
    )
    return response.instrumentShareChange


async def get_dps(
    client: AsyncHTTPClient,
    ins_code: str,
) -> list[InstrumentDPS]:
    """Fetch dividend-per-share (DPS) events for an instrument.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        ins_code: TSETMC instrument code.

    Returns:
        DPS events with ``dEven`` and ``priceAfterTax`` fields.

    Raises:
        NetworkError: If the endpoint cannot be reached.
        ParseError: If the payload does not match the expected schema.

    Examples:
        >>> events = await get_dps(client, "46348559193224090")
        >>> events[0].priceAfterTax
        350.0
    """
    payload = await client.get_json(DPS_URL.format(ins_code=ins_code))
    response = cast(InstrumentDPSResponse, _validate(InstrumentDPSResponse, payload, "DPS"))
    return response.instrumentDPS


async def _daily_records(
    client: AsyncHTTPClient,
    ins_code: str,
    *,
    adjust: AdjustMode,
) -> list[dict[str, Any]]:
    if adjust not in ("none", "all", "capital_only"):
        raise ValueError(f"Unsupported adjust mode: {adjust!r}")

    payload = await client.get_json(DAILY_HISTORY_URL.format(ins_code=ins_code))
    response = cast(
        ClosingPriceDailyResponse,
        _validate(ClosingPriceDailyResponse, payload, "daily history"),
    )

    records: list[dict[str, Any]] = []
    for item in response.closingPriceDaily:
        # Drop sentinel dates emitted for expired/placeholder instruments.
        if item.dEven < MIN_EVEN_DATE:
            continue
        # Drop non-trading placeholder rows.
        if item.qTotTran5J == 0 and item.zTotTran == 0:
            continue
        records.append(
            {
                "date": int_to_date(item.dEven),
                "jdate": int_to_jdate(item.dEven),
                "open": item.priceFirst,
                "high": item.priceMax,
                "low": item.priceMin,
                "close": item.pDrCotVal,
                "final_price": item.pClosing,
                "prev_price": item.priceYesterday,
                "volume": item.qTotTran5J,
                "value": item.qTotCap,
                "trades": item.zTotTran,
            }
        )

    records.sort(key=lambda row: cast(dt.date, row["date"]))

    if adjust == "all":
        return apply_gap_adjustment(records)
    if adjust == "capital_only":
        changes = await get_share_changes(client, ins_code)
        return apply_capital_adjustment(records, changes)
    return records


def _empty_frame(columns: list[str], engine: Engine) -> DataFrame:
    if engine == "polars":
        return pl.DataFrame({column: [] for column in columns})
    if engine == "pandas":
        return pd.DataFrame(columns=columns)
    raise ValueError(f"Unsupported dataframe engine: {engine!r}")


def _client_type_records(
    response: ClientTypeResponse,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in response.clientType:
        if item.recDate < MIN_EVEN_DATE:
            continue
        records.append(
            {
                "date": int_to_date(item.recDate),
                "jdate": int_to_jdate(item.recDate),
                "buy_I_Volume": item.buy_I_Volume,
                "buy_N_Volume": item.buy_N_Volume,
                "buy_I_Value": item.buy_I_Value,
                "buy_N_Value": item.buy_N_Value,
                "buy_N_Count": item.buy_N_Count,
                "buy_I_Count": item.buy_I_Count,
                "sell_I_Volume": item.sell_I_Volume,
                "sell_N_Volume": item.sell_N_Volume,
                "sell_I_Value": item.sell_I_Value,
                "sell_N_Value": item.sell_N_Value,
                "sell_N_Count": item.sell_N_Count,
                "sell_I_Count": item.sell_I_Count,
            }
        )
    records.sort(key=lambda row: cast(dt.date, row["date"]))
    return records


async def get_daily_history(
    client: AsyncHTTPClient,
    ins_code: str,
    *,
    adjust: AdjustMode = "all",
    engine: Engine = "pandas",
) -> DataFrame:
    """Fetch standardized daily OHLCV history for an instrument.

    Sentinel dates (``dEven`` below ``19000101``) and placeholder rows with no
    volume and no trades are dropped, then rows are sorted by ``date``.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        ins_code: TSETMC instrument code.
        adjust: Price adjustment mode.

            * ``"none"`` — raw prices exactly as TSETMC reports them.
            * ``"all"`` (default) — backward-adjusted for dividends and capital
              increases using :func:`apply_gap_adjustment`.
            * ``"capital_only"`` — backward-adjusted for capital increases only
              using :func:`apply_capital_adjustment` and the instrument's share
              changes.

        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A frame with one row per trading day and the following columns:

        ==============  =================  =====================================
        Column          Type               Description
        ==============  =================  =====================================
        ``date``        ``datetime.date``  Gregorian trading date.
        ``jdate``       ``str``            Jalali date, ``YYYY-MM-DD``.
        ``open``        ``float``          First traded price (``priceFirst``).
        ``high``        ``float``          Daily maximum (``priceMax``).
        ``low``         ``float``          Daily minimum (``priceMin``).
        ``close``       ``float``          Last traded price (``pDrCotVal``).
        ``final_price`` ``float``          Closing price (``pClosing``).
        ``prev_price``  ``float``          Previous-day close (``priceYesterday``).
        ``volume``      ``float``          Traded shares (``qTotTran5J``).
        ``value``       ``float``          Traded value in Rials (``qTotCap``).
        ``trades``      ``float``          Number of trades (``zTotTran``).
        ==============  =================  =====================================

        An empty frame with the same columns is returned when no valid rows
        remain.

    Raises:
        ValueError: If ``adjust`` is not one of the three supported modes or
            ``engine`` is unsupported.
        NetworkError: If TSETMC cannot be reached.
        ParseError: If the payload does not match the expected schema.

    Examples:
        >>> frame = await get_daily_history(client, "46348559193224090")
        >>> frame.columns.tolist()[:5]
        ['date', 'jdate', 'open', 'high', 'low']
        >>> frame = await get_daily_history(
        ...     client, "46348559193224090", adjust="none", engine="polars"
        ... )
    """

    records = await _daily_records(client, ins_code, adjust=adjust)
    if not records:
        return _empty_frame(HISTORY_COLUMNS, engine)
    return to_dataframe(records, engine)


async def get_client_type_history(
    client: AsyncHTTPClient,
    ins_code: str,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Fetch retail/institutional (حقیقی/حقوقی) client-type history.

    ``I`` columns describe individual/retail (حقیقی) activity and ``N`` columns
    describe institutional/legal (حقوقی) activity.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        ins_code: TSETMC instrument code.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A frame sorted by ``date`` with the following columns:

        =================  =================  ==================================
        Column             Type               Description
        =================  =================  ==================================
        ``date``           ``datetime.date``  Trading date.
        ``jdate``          ``str``            Jalali date, ``YYYY-MM-DD``.
        ``buy_I_Volume``   ``float``          Retail buy volume.
        ``buy_N_Volume``   ``float``          Institutional buy volume.
        ``buy_I_Value``    ``float``          Retail buy value (Rials).
        ``buy_N_Value``    ``float``          Institutional buy value (Rials).
        ``buy_N_Count``    ``int``            Institutional buy order count.
        ``buy_I_Count``    ``int``            Retail buy order count.
        ``sell_I_Volume``  ``float``          Retail sell volume.
        ``sell_N_Volume``  ``float``          Institutional sell volume.
        ``sell_I_Value``   ``float``          Retail sell value (Rials).
        ``sell_N_Value``   ``float``          Institutional sell value (Rials).
        ``sell_N_Count``   ``int``            Institutional sell order count.
        ``sell_I_Count``   ``int``            Retail sell order count.
        =================  =================  ==================================

    Raises:
        NetworkError: If TSETMC cannot be reached.
        ParseError: If the payload does not match the expected schema.
        ValueError: If ``engine`` is unsupported.

    Examples:
        >>> frame = await get_client_type_history(client, "46348559193224090")
        >>> frame[["date", "buy_I_Volume", "sell_N_Count"]].tail(1)
    """

    payload = await client.get_json(CLIENT_TYPE_HISTORY_URL.format(ins_code=ins_code))
    response = cast(
        ClientTypeResponse,
        _validate(ClientTypeResponse, payload, "client type history"),
    )
    records = _client_type_records(response)
    if not records:
        return _empty_frame(CLIENT_TYPE_COLUMNS, engine)
    return to_dataframe(records, engine)


async def get_full_history(
    client: AsyncHTTPClient,
    ins_code: str,
    *,
    adjust: AdjustMode = "all",
    engine: Engine = "pandas",
) -> DataFrame:
    """Merge daily OHLCV with client-type history on the trading date.

    The two requests are performed sequentially and merged with an outer join
    on ``date`` (so dates missing from either source are retained), then sorted
    ascending.  The client-type ``jdate`` column is dropped, leaving a single
    ``jdate`` from the OHLCV frame.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        ins_code: TSETMC instrument code.
        adjust: Price adjustment mode, identical to
            :func:`get_daily_history` (``"none"``, ``"all"`` or
            ``"capital_only"``).
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A frame with the eleven :func:`get_daily_history` columns followed by
        the twelve client-type columns below (``date`` is shared):

        * ``buy_I_Volume``, ``buy_N_Volume`` (``float``)
        * ``buy_I_Value``, ``buy_N_Value`` (``float``)
        * ``buy_N_Count``, ``buy_I_Count`` (``int``)
        * ``sell_I_Volume``, ``sell_N_Volume`` (``float``)
        * ``sell_I_Value``, ``sell_N_Value`` (``float``)
        * ``sell_N_Count``, ``sell_I_Count`` (``int``)

        When both sources are empty an empty frame is returned.

    Raises:
        ValueError: If ``adjust`` or ``engine`` is unsupported.
        NetworkError: If either request fails.
        ParseError: If either payload is malformed.

    Examples:
        >>> frame = await get_full_history(client, "46348559193224090")
        >>> {"open", "final_price", "buy_I_Volume"} <= set(frame.columns)
        True
    """

    daily = await _daily_records(client, ins_code, adjust=adjust)
    payload = await client.get_json(CLIENT_TYPE_HISTORY_URL.format(ins_code=ins_code))
    response = cast(
        ClientTypeResponse,
        _validate(ClientTypeResponse, payload, "client type history"),
    )
    clients = _client_type_records(response)

    daily_frame = pd.DataFrame(daily) if daily else pd.DataFrame(columns=HISTORY_COLUMNS)
    client_frame = pd.DataFrame(clients)
    if daily_frame.empty or client_frame.empty:
        merged = daily_frame
    else:
        client_frame = client_frame.drop(columns=["jdate"], errors="ignore")
        merged = daily_frame.merge(client_frame, on="date", how="outer")

    merged = merged.sort_values("date").reset_index(drop=True)

    if engine == "polars":
        return pl.from_pandas(merged)
    if engine == "pandas":
        return merged
    raise ValueError(f"Unsupported dataframe engine: {engine!r}")
