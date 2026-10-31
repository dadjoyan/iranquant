"""Live and historical index data (overall, equal-weight and sector indices)."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import polars as pl

from iranquant.config import (
    EQUAL_WEIGHT_INDEX_CODE,
    INDEX_B1_HISTORY_URL,
    INDEX_B2_HISTORY_URL,
    INDEX_SECTORS_LIVE_URL,
    MARKET_OVERVIEW_URL,
    OVERALL_INDEX_CODE,
)
from iranquant.core.client import AsyncHTTPClient
from iranquant.core.dataframe import DataFrame, Engine, to_dataframe
from iranquant.core.normalizer import (
    int_to_date_optional,
    int_to_jdate_optional,
    int_to_time_optional,
    normalize_persian,
)
from iranquant.exceptions import NetworkError


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_items(payload: Any, preferred_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_index(item: dict[str, Any]) -> dict[str, Any]:
    """Standardize an index row from either the live or history endpoints."""

    record = dict(item)
    even_date = record.get("dEven")
    parsed_date = int_to_date_optional(even_date) if isinstance(even_date, int) else None
    record["index_code"] = str(record.get("insCode") or "")
    record["name"] = normalize_persian(str(record.get("lVal30") or ""))
    record["index_value"] = _to_float(record.get("xDrNivJIdx004"))
    if record["index_value"] is None:
        record["index_value"] = _to_float(record.get("xNivInuClMresIbs"))
    record["change"] = _to_float(record.get("indexChange"))
    record["change_pct"] = _to_float(record.get("xVarIdxJRfV"))
    record["date"] = parsed_date
    record["jdate"] = int_to_jdate_optional(even_date) if parsed_date else None
    record["time"] = int_to_time_optional(record.get("hEven"))
    return record


async def get_indices_live(
    client: AsyncHTTPClient,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Fetch live overall, equal-weight and sector index snapshots.

    Uses ``MarketData/GetMarketOverview/1`` for the overall and equal-weight
    indices and ``Index/GetIndexB1LastAll/0/1`` for sector indices.  The two
    requests run concurrently; if one fails but the other returns data, the
    partial result is returned.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A frame with at least the following standardized columns:

        * ``index_code`` (``str``) — TSETMC index code.
        * ``name`` (``str``) — index name (Persian).
        * ``index_value`` (``float | None``) — current index value.
        * ``change`` (``float | None``) — absolute change.
        * ``change_pct`` (``float | None``) — percentage change.
        * ``date`` (``datetime.date | None``) — date, when reported.
        * ``jdate`` (``str | None``) — Jalali date, when reported.
        * ``time`` (``datetime.time | None``) — last update time.

        Rows for the overall (``OVERALL_INDEX_CODE``) and equal-weight
        (``EQUAL_WEIGHT_INDEX_CODE``) indices additionally contain
        ``market_state`` and ``market_state_title``.  Sector rows keep every
        extra raw TSETMC field alongside the standardized columns.

    Raises:
        NetworkError: If both requests fail and no rows can be produced.

    Examples:
        >>> indices = await get_indices_live(client)
        >>> indices[["index_code", "name", "index_value"]].head()
    """

    results = await asyncio.gather(
        client.get_json(MARKET_OVERVIEW_URL.format(flow=1)),
        client.get_json(INDEX_SECTORS_LIVE_URL),
        return_exceptions=True,
    )
    overview_result: Any = results[0]
    sectors_result: Any = results[1]

    records: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    if isinstance(overview_result, BaseException):
        errors.append(overview_result)
    else:
        overview = overview_result.get("marketOverview")
        if isinstance(overview, dict):
            overview_rows = (
                (
                    OVERALL_INDEX_CODE,
                    "شاخص کل",
                    "indexLastValue",
                    "indexChange",
                ),
                (
                    EQUAL_WEIGHT_INDEX_CODE,
                    "شاخص هم‌وزن",
                    "indexEqualWeightedLastValue",
                    "indexEqualWeightedChange",
                ),
            )
            for code, name, value_key, change_key in overview_rows:
                value = _to_float(overview.get(value_key))
                if value is None:
                    continue
                records.append(
                    {
                        "index_code": code,
                        "name": name,
                        "index_value": value,
                        "change": _to_float(overview.get(change_key)) or 0.0,
                        "change_pct": None,
                        "date": None,
                        "jdate": None,
                        "time": None,
                        "market_state": overview.get("marketState"),
                        "market_state_title": normalize_persian(
                            str(overview.get("marketStateTitle") or "")
                        ),
                    }
                )

    if isinstance(sectors_result, BaseException):
        errors.append(sectors_result)
    else:
        records.extend(
            _normalize_index(item) for item in _extract_items(sectors_result, ("indexB1",))
        )

    if not records and errors:
        raise NetworkError("Unable to fetch any live index data") from errors[0]

    return to_dataframe(records, engine)


async def get_index_history(
    client: AsyncHTTPClient,
    index_code: str = OVERALL_INDEX_CODE,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Fetch historical daily values for an index code.

    Falls back to the B1 endpoint when the B2 endpoint is unavailable.  Rows
    are sorted ascending by ``date``; rows without a parseable date sort first.

    Args:
        client: An open :class:`~iranquant.core.client.AsyncHTTPClient`.
        index_code: TSETMC index code.  Defaults to
            :data:`iranquant.config.OVERALL_INDEX_CODE` (شاخص کل).
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A frame with the same standardized columns as
        :func:`get_indices_live` (``index_code``, ``name``, ``index_value``,
        ``change``, ``change_pct``, ``date``, ``jdate``, ``time``) plus the raw
        upstream fields (for example ``xNivInuClMresIbs`` and ``hEven``).

    Raises:
        NetworkError: If both the B2 and B1 endpoints fail.
        ParseError: If a returned body is not valid JSON.
        ValueError: If ``engine`` is unsupported.

    Examples:
        >>> overall = await get_index_history(client)
        >>> overall[["date", "index_value"]].tail()
        >>> equal = await get_index_history(
        ...     client, EQUAL_WEIGHT_INDEX_CODE, engine="polars"
        ... )
    """

    b2_url = INDEX_B2_HISTORY_URL.format(index_code=index_code)
    try:
        payload = await client.get_json(b2_url)
    except NetworkError:
        payload = await client.get_json(INDEX_B1_HISTORY_URL.format(index_code=index_code))

    items = _extract_items(payload, ("indexB2", "indexB1"))
    records = [_normalize_index(item) for item in items]
    records.sort(key=lambda row: row["date"] or dt.date.min)

    if engine == "polars":
        return pl.DataFrame(records)
    if engine == "pandas":
        return to_dataframe(records, "pandas")
    raise ValueError(f"Unsupported dataframe engine: {engine!r}")
