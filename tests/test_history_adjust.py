"""Tests for daily history mapping and backward price adjustment."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

from iranquant.core.client import AsyncHTTPClient
from iranquant.market.history import (
    apply_capital_adjustment,
    apply_gap_adjustment,
    get_client_type_history,
    get_daily_history,
    get_full_history,
)
from iranquant.models.tsetmc import InstrumentShareChange

DAILY_PREFIX = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/"
CLIENT_TYPE_PREFIX = "https://cdn.tsetmc.com/api/ClientType/GetClientTypeHistory/"
SHARE_CHANGE_PREFIX = "https://cdn.tsetmc.com/api/Instrument/GetInstrumentShareChange/"

INS_CODE = "46348559193224090"


@respx.mock
async def test_daily_history_standardizes_columns(
    client: AsyncHTTPClient, payloads: dict[str, dict[str, Any]]
) -> None:
    respx.get(url__startswith=DAILY_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["daily"])
    )

    frame = await get_daily_history(client, INS_CODE, adjust="none")
    assert isinstance(frame, pd.DataFrame)

    assert list(frame.columns) == [
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
    # Zero-volume placeholder row is dropped, rows are sorted ascending.
    assert len(frame) == 2
    assert list(frame["date"]) == [dt.date(2026, 10, 27), dt.date(2026, 10, 28)]

    latest = frame.iloc[-1]
    assert latest["jdate"] == "1405-08-06"
    assert latest["open"] == 4110.0  # priceFirst
    assert latest["high"] == 4180.0  # priceMax
    assert latest["low"] == 4080.0  # priceMin
    assert latest["close"] == 4150.0  # pDrCotVal
    assert latest["final_price"] == 4120.0  # pClosing
    assert latest["prev_price"] == 4100.0  # priceYesterday
    assert latest["volume"] == 98500120.0  # qTotTran5J
    assert latest["value"] == 406512000000.0  # qTotCap
    assert latest["trades"] == 4521.0  # zTotTran


@respx.mock
async def test_daily_history_drops_sentinel_dates(client: AsyncHTTPClient) -> None:
    payload = {
        "closingPriceDaily": [
            {"insCode": INS_CODE, "dEven": 1, "qTotTran5J": 10.0, "zTotTran": 1.0},
            {"insCode": INS_CODE, "dEven": 0, "qTotTran5J": 10.0, "zTotTran": 1.0},
            {
                "insCode": INS_CODE,
                "dEven": 20261028,
                "qTotTran5J": 100.0,
                "zTotTran": 1.0,
                "priceFirst": 10.0,
                "priceMax": 11.0,
                "priceMin": 9.0,
                "pDrCotVal": 10.5,
                "pClosing": 10.2,
                "priceYesterday": 10.0,
                "qTotCap": 1000.0,
            },
        ]
    }
    respx.get(url__startswith=DAILY_PREFIX).mock(return_value=httpx.Response(200, json=payload))

    frame = await get_daily_history(client, INS_CODE, adjust="none")
    assert isinstance(frame, pd.DataFrame)

    assert len(frame) == 1
    assert frame.iloc[0]["date"] == dt.date(2026, 10, 28)


def _record(
    day: int,
    *,
    final_price: float,
    prev_price: float,
    open_price: float = 100.0,
) -> dict[str, Any]:
    return {
        "date": dt.date(2026, 1, day),
        "jdate": f"1404-10-{day:02d}",
        "open": open_price,
        "high": open_price + 10,
        "low": open_price - 10,
        "close": open_price + 1,
        "final_price": final_price,
        "prev_price": prev_price,
        "volume": 1000.0,
        "value": 100000.0,
        "trades": 10.0,
    }


def test_gap_adjustment_matches_spec_formula() -> None:
    records = [
        _record(1, final_price=100.0, prev_price=100.0),
        _record(2, final_price=100.0, prev_price=90.0),  # r = 90 / 100
        _record(3, final_price=100.0, prev_price=100.0),  # r = 100 / 100
    ]

    adjusted = apply_gap_adjustment(records)

    # F_2 = 1, F_1 = 1, F_0 = F_1 * r_1 = 0.9
    assert adjusted[2]["open"] == pytest.approx(100.0)
    assert adjusted[1]["open"] == pytest.approx(100.0)
    assert adjusted[0]["open"] == pytest.approx(90.0)
    assert adjusted[0]["final_price"] == pytest.approx(90.0)
    assert adjusted[0]["prev_price"] == pytest.approx(90.0)


def test_gap_adjustment_does_not_mutate_input() -> None:
    records = [_record(1, final_price=100.0, prev_price=100.0)]
    adjusted = apply_gap_adjustment(records)

    assert records[0]["open"] == 100.0
    assert adjusted is not records


def test_capital_adjustment_applies_old_over_new_before_event() -> None:
    records = [
        _record(1, final_price=100.0, prev_price=100.0),
        _record(2, final_price=100.0, prev_price=100.0),
        _record(3, final_price=100.0, prev_price=100.0),
    ]
    event = InstrumentShareChange(dEven=20260103, numberOfShareOld=100.0, numberOfShareNew=200.0)

    adjusted = apply_capital_adjustment(records, [event])

    assert adjusted[0]["open"] == pytest.approx(50.0)
    assert adjusted[1]["open"] == pytest.approx(50.0)
    assert adjusted[2]["open"] == pytest.approx(100.0)


@respx.mock
async def test_capital_only_adjustment_fetches_share_changes(
    client: AsyncHTTPClient, payloads: dict[str, dict[str, Any]]
) -> None:
    respx.get(url__startswith=DAILY_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["daily"])
    )
    respx.get(url__startswith=SHARE_CHANGE_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["share_change"])
    )

    frame = await get_daily_history(client, INS_CODE, adjust="capital_only")
    assert isinstance(frame, pd.DataFrame)

    # Event is on 2026-10-28; only the earlier day is adjusted by 0.5.
    assert frame.iloc[0]["open"] == pytest.approx(4090.0 * 0.5)
    assert frame.iloc[1]["open"] == pytest.approx(4110.0)


@respx.mock
async def test_client_type_history(
    client: AsyncHTTPClient, payloads: dict[str, dict[str, Any]]
) -> None:
    respx.get(url__startswith=CLIENT_TYPE_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["client_type"])
    )

    frame = await get_client_type_history(client, INS_CODE)
    assert isinstance(frame, pd.DataFrame)

    assert list(frame["date"]) == [dt.date(2026, 10, 27), dt.date(2026, 10, 28)]
    assert "buy_I_Volume" in frame.columns
    assert "sell_N_Count" in frame.columns
    assert frame.iloc[-1]["buy_I_Volume"] == 65000000.0
    assert frame.iloc[-1]["sell_I_Count"] == 3100


@respx.mock
async def test_full_history_merges_ohlcv_and_client_type(
    client: AsyncHTTPClient, payloads: dict[str, dict[str, Any]]
) -> None:
    respx.get(url__startswith=DAILY_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["daily"])
    )
    respx.get(url__startswith=CLIENT_TYPE_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["client_type"])
    )

    frame = await get_full_history(client, INS_CODE, adjust="none")

    assert len(frame) == 2
    assert {"open", "final_price", "buy_I_Volume", "sell_I_Count"} <= set(frame.columns)
    assert "jdate" in frame.columns
    assert "jdate_ct" not in frame.columns


@respx.mock
async def test_daily_history_supports_polars(
    client: AsyncHTTPClient, payloads: dict[str, dict[str, Any]]
) -> None:
    respx.get(url__startswith=DAILY_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["daily"])
    )

    frame = await get_daily_history(client, INS_CODE, adjust="none", engine="polars")

    import polars as pl

    assert isinstance(frame, pl.DataFrame)
    assert frame.height == 2
