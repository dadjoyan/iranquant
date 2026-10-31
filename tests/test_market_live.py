"""Tests for live MarketWatch, order book, trades and index endpoints."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pandas as pd
import respx

from iranquant.config import MARKET_WATCH_URL
from iranquant.core.client import AsyncHTTPClient
from iranquant.market.index import get_index_history, get_indices_live
from iranquant.market.live import get_intraday_trades, get_market_watch, get_order_book

INS_CODE = "46348559193224090"


@respx.mock
async def test_market_watch_maps_fields(
    client: AsyncHTTPClient, payloads: dict[str, dict[str, Any]]
) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=payloads["market_watch"])

    respx.get(url__startswith="https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch").mock(
        side_effect=handler
    )

    frame = await get_market_watch(client)
    assert isinstance(frame, pd.DataFrame)

    assert "paperTypes[0]=1" in captured["url"]
    assert "%5B" not in captured["url"]

    row = frame.iloc[0]
    assert row["ins_code"] == INS_CODE
    assert row["symbol"] == "فولاد"
    assert row["company_name"] == "فولاد مبارکه اصفهان"
    assert row["open"] == 4110.0  # pf
    assert row["high"] == 4180.0  # pmx
    assert row["low"] == 4080.0  # pmn
    assert row["close"] == 4150.0  # pdv
    assert row["final_price"] == 4120.0  # pcl
    assert row["prev_price"] == 4100.0  # py
    assert row["volume"] == 98500120.0  # qtj
    assert row["value"] == 406512000000.0  # qtc
    assert row["trades"] == 4521.0  # ztt
    assert row["tmax"] == 4428.0  # pMax
    assert row["tmin"] == 3772.0  # pMin
    assert row["total_shares"] == 1130000000000.0  # ztd

    book = row["order_book"]
    assert len(book) == 1
    assert book[0]["pmd"] == 4140.0
    assert book[0]["pmo"] == 4150.0


@respx.mock
async def test_order_book(client: AsyncHTTPClient) -> None:
    payload = {
        "bestLimits": [
            {
                "number": 2,
                "qTitMeDem": 5,
                "zOrdMeDem": 100000,
                "pMeDem": 4130.0,
                "pMeOf": 4160.0,
                "zOrdMeOf": 200000,
                "qTitMeOf": 8,
            },
            {
                "number": 1,
                "qTitMeDem": 15,
                "zOrdMeDem": 500000,
                "pMeDem": 4140.0,
                "pMeOf": 4150.0,
                "zOrdMeOf": 300000,
                "qTitMeOf": 10,
            },
        ]
    }
    respx.get(url__startswith="https://cdn.tsetmc.com/api/BestLimits/").mock(
        return_value=httpx.Response(200, json=payload)
    )

    frame = await get_order_book(client, INS_CODE)
    assert isinstance(frame, pd.DataFrame)

    assert list(frame.columns) == ["n", "qmd", "zmd", "pmd", "pmo", "zmo", "qmo"]
    assert list(frame["n"]) == [1, 2]
    assert frame.iloc[0]["pmd"] == 4140.0
    assert frame.iloc[0]["qmo"] == 10


@respx.mock
async def test_intraday_trades_adds_time(client: AsyncHTTPClient) -> None:
    payload = {
        "tradeIntraDay": [
            {
                "hEven": 93000,
                "minPrice": 4140.0,
                "maxPrice": 4160.0,
                "openPrice": 4150.0,
                "closePrice": 4155.0,
                "volume": 100.0,
            },
            {
                "hEven": 123000,
                "minPrice": 4150.0,
                "maxPrice": 4170.0,
                "openPrice": 4155.0,
                "closePrice": 4165.0,
                "volume": 200.0,
            },
        ]
    }
    respx.get(url__startswith="https://cdn.tsetmc.com/api/Trade/GetTradeIntraDay/").mock(
        return_value=httpx.Response(200, json=payload)
    )

    frame = await get_intraday_trades(client, INS_CODE)
    assert isinstance(frame, pd.DataFrame)

    assert list(frame.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert frame.iloc[0]["time"] == dt.time(9, 30, 0)
    assert frame.iloc[1]["time"] == dt.time(12, 30, 0)
    assert frame.iloc[0]["open"] == 4150.0
    assert frame.iloc[0]["high"] == 4160.0
    assert frame.iloc[0]["low"] == 4140.0
    assert frame.iloc[0]["close"] == 4155.0
    assert frame.iloc[0]["volume"] == 100.0


@respx.mock
async def test_index_history(client: AsyncHTTPClient) -> None:
    payload = {
        "indexB2": [
            {
                "insCode": 32097828799138957,
                "dEven": 20261028,
                "xNivInuClMresIbs": 2400000.0,
                "xNivInuPbMresIbs": 2390000.0,
                "xNivInuPhMresIbs": 2410000.0,
            },
            {
                "insCode": 32097828799138957,
                "dEven": 20261027,
                "xNivInuClMresIbs": 2380000.0,
                "xNivInuPbMresIbs": 2370000.0,
                "xNivInuPhMresIbs": 2390000.0,
            },
        ]
    }
    respx.get(url__startswith="https://cdn.tsetmc.com/api/Index/GetIndexB2History/").mock(
        return_value=httpx.Response(200, json=payload)
    )

    frame = await get_index_history(client, "32097828799138957")
    assert isinstance(frame, pd.DataFrame)

    assert list(frame["date"]) == [dt.date(2026, 10, 27), dt.date(2026, 10, 28)]
    assert frame.iloc[-1]["jdate"] == "1405-08-06"
    assert frame.iloc[-1]["index_value"] == 2400000.0
    assert frame.iloc[-1]["index_code"] == "32097828799138957"


@respx.mock
async def test_indices_live_combines_overview_and_sectors(
    client: AsyncHTTPClient,
) -> None:
    respx.get(url__startswith="https://cdn.tsetmc.com/api/MarketData/GetMarketOverview/").mock(
        return_value=httpx.Response(
            200,
            json={
                "marketOverview": {
                    "indexLastValue": 7967550.61,
                    "indexChange": 40220.04,
                    "indexEqualWeightedLastValue": 2209746.17,
                    "indexEqualWeightedChange": 7001.38,
                    "marketState": "F",
                    "marketStateTitle": "بسته",
                }
            },
        )
    )
    respx.get(url__startswith="https://cdn.tsetmc.com/api/Index/GetIndexB1LastAll/").mock(
        return_value=httpx.Response(
            200,
            json={
                "indexB1": [
                    {
                        "insCode": "34408080767216529",
                        "dEven": 0,
                        "hEven": 192857,
                        "xDrNivJIdx004": 1544484.55,
                        "xVarIdxJRfV": -1.1028,
                        "indexChange": -17222.59,
                        "lVal30": "01-زراعت",
                    }
                ]
            },
        )
    )

    frame = await get_indices_live(client)
    assert isinstance(frame, pd.DataFrame)

    assert len(frame) == 3
    assert set(frame["index_code"]) == {
        "32097828799138957",
        "43685683301327984",
        "34408080767216529",
    }
    overall = frame.loc[frame["index_code"] == "32097828799138957"].iloc[0]
    assert overall["index_value"] == 7967550.61
    assert overall["market_state"] == "F"
    sector = frame.loc[frame["index_code"] == "34408080767216529"].iloc[0]
    assert sector["name"] == "01-زراعت"
    assert sector["index_value"] == 1544484.55
    assert sector["change_pct"] == -1.1028


@respx.mock
async def test_market_watch_empty(client: AsyncHTTPClient) -> None:
    respx.get(url__startswith="https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch").mock(
        return_value=httpx.Response(200, json={"marketwatch": []})
    )

    frame = await get_market_watch(client)
    assert isinstance(frame, pd.DataFrame)
    assert frame.empty


def test_market_watch_url_is_verbatim() -> None:
    assert "paperTypes[0]=1" in MARKET_WATCH_URL
    assert "paperTypes[1]=2" in MARKET_WATCH_URL
    assert "paperTypes[2]=8" in MARKET_WATCH_URL
    assert "%5B" not in MARKET_WATCH_URL


@respx.mock
async def test_market_watch_handles_placeholder_pe_and_legacy_fields(
    client: AsyncHTTPClient,
) -> None:
    payload = {
        "marketwatch": [
            {
                "insCode": "1",
                "lva": "A",
                "pe": "-",
                "qtj": None,
                "qtt": 123.0,
                "pMax": None,
                "ps": 10.0,
                "pMin": None,
                "pi": 9.0,
            }
        ]
    }
    respx.get(url__startswith="https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch").mock(
        return_value=httpx.Response(200, json=payload)
    )

    frame = await get_market_watch(client)
    assert isinstance(frame, pd.DataFrame)

    row = frame.iloc[0]
    assert pd.isna(row["pe"])
    assert row["volume"] == 123.0
    assert row["tmax"] == 10.0
    assert row["tmin"] == 9.0
