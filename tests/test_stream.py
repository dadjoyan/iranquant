"""Tests for MarketWatcher state transitions, callbacks and market hours."""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import respx

from iranquant.stream import (
    BUY_QUEUE_BROKEN,
    BUY_QUEUE_LOCKED,
    SELL_QUEUE_BROKEN,
    SELL_QUEUE_LOCKED,
    VOLUME_SPIKE,
    MarketEvent,
    MarketWatcher,
    is_market_open,
)

MARKET_WATCH_PREFIX = "https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch"


def _snapshot(
    close: float,
    *,
    volume: float,
    buy_volume: float = 0.0,
    sell_volume: float = 0.0,
    tmax: float = 500.0,
    tmin: float = 450.0,
    symbol: str = "A",
    ins_code: str = "1",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ins_code": ins_code,
                "symbol": symbol,
                "close": close,
                "tmax": tmax,
                "tmin": tmin,
                "volume": volume,
                "order_book": [{"zmd": buy_volume, "zmo": sell_volume}],
            }
        ]
    )


def test_buy_queue_locked_then_broken() -> None:
    watcher = MarketWatcher()

    first = watcher.process_snapshot(_snapshot(500.0, volume=1000.0, buy_volume=100.0))
    second = watcher.process_snapshot(_snapshot(490.0, volume=2000.0, buy_volume=0.0))

    assert [event.event_type for event in first] == [BUY_QUEUE_LOCKED]
    assert [event.event_type for event in second] == [BUY_QUEUE_BROKEN]
    assert second[0].ins_code == "1"
    assert second[0].symbol == "A"
    assert second[0].data["close"] == 490.0


def test_sell_queue_locked_then_broken() -> None:
    watcher = MarketWatcher()

    first = watcher.process_snapshot(_snapshot(450.0, volume=1000.0, sell_volume=100.0))
    second = watcher.process_snapshot(_snapshot(460.0, volume=2000.0, sell_volume=0.0))

    assert [event.event_type for event in first] == [SELL_QUEUE_LOCKED]
    assert [event.event_type for event in second] == [SELL_QUEUE_BROKEN]


def test_volume_spike_after_baseline() -> None:
    watcher = MarketWatcher()

    watcher.process_snapshot(_snapshot(490.0, volume=1000.0))
    watcher.process_snapshot(_snapshot(490.0, volume=2000.0))
    events = watcher.process_snapshot(_snapshot(490.0, volume=6000.0))

    assert [event.event_type for event in events] == [VOLUME_SPIKE]
    assert events[0].data["delta"] == 4000.0


def test_symbols_filter() -> None:
    watcher = MarketWatcher()
    snapshot = pd.concat(
        [
            _snapshot(500.0, volume=1000.0, buy_volume=100.0, symbol="A"),
            _snapshot(
                500.0,
                volume=1000.0,
                buy_volume=100.0,
                symbol="B",
                ins_code="2",
            ),
        ],
        ignore_index=True,
    )

    events = watcher.process_snapshot(snapshot, symbols=["B"])

    assert len(events) == 1
    assert events[0].symbol == "B"


async def test_sync_and_async_callbacks() -> None:
    watcher = MarketWatcher()
    sync_seen: list[MarketEvent] = []
    async_seen: list[MarketEvent] = []

    @watcher.on_event(BUY_QUEUE_LOCKED)
    def sync_handler(event: MarketEvent) -> None:
        sync_seen.append(event)

    @watcher.on_event("*")
    async def async_handler(event: MarketEvent) -> None:
        async_seen.append(event)

    events = watcher.process_snapshot(_snapshot(500.0, volume=1000.0, buy_volume=100.0))
    await watcher.dispatch(events)

    assert [event.event_type for event in sync_seen] == [BUY_QUEUE_LOCKED]
    assert [event.event_type for event in async_seen] == [BUY_QUEUE_LOCKED]


@respx.mock
async def test_watch_streams_transitions_from_mocked_snapshots() -> None:
    def payload(close: float, volume: float, buy_volume: float) -> dict[str, Any]:
        return {
            "marketwatch": [
                {
                    "insCode": "1",
                    "lva": "A",
                    "lvc": "Company A",
                    "pcl": close,
                    "pdv": close,
                    "pf": close,
                    "pmx": close,
                    "pmn": close,
                    "pMax": 500.0,
                    "pMin": 450.0,
                    "qtj": volume,
                    "ztt": 10.0,
                    "qtc": volume * close,
                    "blDs": [
                        {"n": 1, "zmd": buy_volume, "zmo": 0.0},
                    ],
                }
            ]
        }

    respx.get(url__startswith=MARKET_WATCH_PREFIX).mock(
        side_effect=[
            httpx.Response(200, json=payload(500.0, 1000.0, 100.0)),
            httpx.Response(200, json=payload(490.0, 2000.0, 0.0)),
        ]
    )

    watcher = MarketWatcher()
    generator = watcher.watch(interval=0.01)
    events: list[MarketEvent] = []
    try:
        async for event in generator:
            events.append(event)
            if len(events) >= 2:
                break
    finally:
        await generator.aclose()

    assert [event.event_type for event in events] == [
        BUY_QUEUE_LOCKED,
        BUY_QUEUE_BROKEN,
    ]


def test_market_hours() -> None:
    tehran = ZoneInfo("Asia/Tehran")

    # 2026-10-31 is a Saturday.
    assert is_market_open(dt.datetime(2026, 10, 31, 10, 0, tzinfo=tehran)) is True
    assert is_market_open(dt.datetime(2026, 10, 31, 8, 0, tzinfo=tehran)) is False
    # 2026-10-30 is a Friday.
    assert is_market_open(dt.datetime(2026, 10, 30, 10, 0, tzinfo=tehran)) is False


def test_reset_clears_state() -> None:
    watcher = MarketWatcher()
    watcher.process_snapshot(_snapshot(500.0, volume=1000.0, buy_volume=100.0))
    assert watcher.process_snapshot(_snapshot(500.0, volume=1000.0, buy_volume=100.0)) == []

    watcher.reset()
    events = watcher.process_snapshot(_snapshot(500.0, volume=1000.0, buy_volume=100.0))
    assert [event.event_type for event in events] == [BUY_QUEUE_LOCKED]
