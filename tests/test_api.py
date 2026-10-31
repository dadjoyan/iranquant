"""Tests for the synchronous wrapper and top-level API."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pandas as pd
import respx

import iranquant
from iranquant import AsyncIranQuant
from iranquant.core.sync import run_sync

SEARCH_PREFIX = "https://cdn.tsetmc.com/api/Instrument/GetInstrumentSearch/"
DAILY_PREFIX = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/"
INS_CODE = "46348559193224090"


def test_run_sync_outside_event_loop() -> None:
    async def coro() -> int:
        return 42

    assert run_sync(coro()) == 42


async def test_run_sync_inside_running_event_loop() -> None:
    async def coro() -> str:
        return "ok"

    assert run_sync(coro()) == "ok"


@respx.mock
async def test_async_client_history_accepts_ins_code(
    payloads: dict[str, dict[str, Any]],
) -> None:
    respx.get(url__startswith=DAILY_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["daily"])
    )

    async with AsyncIranQuant() as client:
        frame = await client.history(INS_CODE, adjust="none")
    assert isinstance(frame, pd.DataFrame)

    assert len(frame) == 2
    assert frame.iloc[-1]["date"] == dt.date(2026, 10, 28)


@respx.mock
async def test_async_resolve_reuses_search(
    payloads: dict[str, dict[str, Any]],
) -> None:
    search_route = respx.get(url__startswith=SEARCH_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["search"])
    )
    respx.get(url__startswith=DAILY_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["daily"])
    )

    async with AsyncIranQuant() as client:
        await client.history("فولاد", adjust="none")
        await client.history("فولاد", adjust="none")

    assert search_route.call_count == 1


@respx.mock
async def test_sync_api_works_inside_running_loop(
    payloads: dict[str, dict[str, Any]],
) -> None:
    respx.get(url__startswith=SEARCH_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["search"])
    )

    frame = iranquant.search("فولاد")
    assert isinstance(frame, pd.DataFrame)

    assert len(frame) == 1
    assert frame.iloc[0]["ins_code"] == INS_CODE


@respx.mock
async def test_sync_market_watch(
    payloads: dict[str, dict[str, Any]],
) -> None:
    respx.get(url__startswith="https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch").mock(
        return_value=httpx.Response(200, json=payloads["market_watch"])
    )

    frame = iranquant.market_watch()
    assert isinstance(frame, pd.DataFrame)

    assert len(frame) == 1
    assert frame.iloc[0]["symbol"] == "فولاد"


@respx.mock
async def test_sync_get_inscode(payloads: dict[str, dict[str, Any]]) -> None:
    respx.get(url__startswith=SEARCH_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["search"])
    )

    assert iranquant.get_inscode("فولاد") == INS_CODE
    assert iranquant.IranQuant().get_inscode(INS_CODE) == INS_CODE


def test_numeric_input_bypasses_network() -> None:
    async def coro() -> str:
        async with AsyncIranQuant() as client:
            return await client.resolve(INS_CODE)

    assert run_sync(coro()) == INS_CODE
