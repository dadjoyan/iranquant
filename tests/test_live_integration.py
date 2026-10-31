"""Live integration tests against the real TSETMC and Codal servers.

Enable explicitly::

    IRANQUANT_LIVE=1 pytest tests/test_live_integration.py -m live

Each test skips (rather than fails) when the environment has no network access.
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from iranquant import AsyncIranQuant
from iranquant.codal import (
    fetch_and_parse_report,
    get_monthly_reports,
    monthly_sales_summary,
)
from iranquant.exceptions import NetworkError
from iranquant.quant import MarketScreener

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("IRANQUANT_LIVE") != "1",
        reason="set IRANQUANT_LIVE=1 to run live integration tests",
    ),
]

FOLD_INS_CODE = "46348559193224090"


async def test_live_history_client_type_and_quant() -> None:
    async with AsyncIranQuant(timeout=30.0) as client:
        try:
            history = await client.history("فولاد", adjust="none")
            client_type = await client.client_type("فولاد")
            full = await client.full_history("فولاد", adjust="all")
            flow = await client.money_flow("فولاد")
            power = await client.movement_power("فولاد")
        except NetworkError as exc:
            pytest.skip(f"TSETMC is unreachable from this environment: {exc}")

    assert isinstance(history, pd.DataFrame)
    assert isinstance(client_type, pd.DataFrame)
    assert isinstance(full, pd.DataFrame)
    assert isinstance(flow, pd.DataFrame)
    assert isinstance(power, pd.DataFrame)
    assert not history.empty
    assert {"date", "jdate", "open", "high", "low", "close", "final_price"} <= set(history.columns)
    assert history["date"].is_monotonic_increasing
    assert not client_type.empty
    assert "buy_I_Volume" in client_type.columns
    assert "buyer_power_ratio" in flow.columns
    assert "movement_power_score" in power.columns


async def test_live_search_and_resolve() -> None:
    async with AsyncIranQuant(timeout=20.0) as client:
        try:
            results = await client.search("فولاد")
            code = await client.resolve("فولاد")
        except NetworkError as exc:
            pytest.skip(f"TSETMC is unreachable from this environment: {exc}")

    assert isinstance(results, pd.DataFrame)
    assert not results.empty
    assert {"ins_code", "symbol", "last_date"} <= set(results.columns)
    assert code == FOLD_INS_CODE


async def test_live_market_watch_and_screener() -> None:
    async with AsyncIranQuant(timeout=30.0) as client:
        try:
            market = await client.market_watch()
        except NetworkError as exc:
            pytest.skip(f"TSETMC is unreachable from this environment: {exc}")

    assert isinstance(market, pd.DataFrame)
    assert not market.empty
    assert {"close", "final_price", "volume", "tmax", "tmin", "order_book"} <= set(market.columns)
    assert (market["tmax"] > 0).any()

    screener = MarketScreener(market)
    buy_queue = screener.filter_buy_queue()
    sell_queue = screener.filter_sell_queue()
    assert isinstance(buy_queue, pd.DataFrame)
    assert isinstance(sell_queue, pd.DataFrame)
    assert not buy_queue.empty
    assert not sell_queue.empty


async def test_live_order_book_and_intraday() -> None:
    async with AsyncIranQuant(timeout=30.0) as client:
        try:
            book = await client.order_book("فولاد")
            trades = await client.intraday_trades("فولاد")
        except NetworkError as exc:
            pytest.skip(f"TSETMC is unreachable from this environment: {exc}")

    assert isinstance(book, pd.DataFrame)
    assert isinstance(trades, pd.DataFrame)
    assert list(book.columns) == ["n", "qmd", "zmd", "pmd", "pmo", "zmo", "qmo"]
    assert not book.empty
    assert list(trades.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert not trades.empty


async def test_live_indices() -> None:
    async with AsyncIranQuant(timeout=30.0) as client:
        try:
            live = await client.indices_live()
            history = await client.index_history()
        except NetworkError as exc:
            pytest.skip(f"TSETMC is unreachable from this environment: {exc}")

    assert isinstance(live, pd.DataFrame)
    assert isinstance(history, pd.DataFrame)
    assert {"index_code", "index_value"} <= set(live.columns)
    assert "32097828799138957" in set(live["index_code"])
    assert {"date", "jdate", "index_value"} <= set(history.columns)
    assert not history.empty
    assert history["date"].is_monotonic_increasing


async def test_live_codal_monthly_report() -> None:
    async with AsyncIranQuant(timeout=60.0) as client:
        try:
            reports = await get_monthly_reports("فولاد", client=client.client)
            letter = next(
                (item for item in reports.Letters if item.HasHtml and item.Url),
                None,
            )
            if letter is None:
                pytest.skip("No HTML monthly report is currently available")
            tables = await fetch_and_parse_report(letter, client=client.client)
        except NetworkError as exc:
            pytest.skip(f"Codal is unreachable from this environment: {exc}")

    assert reports.Total > 0
    assert tables
    assert all(hasattr(frame, "shape") for frame in tables.values())


async def test_live_monthly_sales_summary() -> None:
    try:
        summary = await monthly_sales_summary("فولاد")
    except NetworkError as exc:
        pytest.skip(f"Codal is unreachable from this environment: {exc}")

    assert summary["symbol"] == "فولاد"
    assert summary["current_month_sales"] is not None
    assert summary["current_month_sales"] >= 0
    assert summary["ytd_sales"] is not None
    assert "summary_df" in summary
    assert isinstance(summary["summary_df"], pd.DataFrame)
