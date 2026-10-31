"""Tests for quant money-flow, momentum and screener functions."""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl
import pytest

from iranquant.core.dataframe import DataFrame
from iranquant.quant import (
    MarketScreener,
    compute_money_flow,
    compute_movement_power,
    detect_swing_legs,
)


def _frame(records: list[dict[str, Any]], engine: str) -> DataFrame:
    if engine == "polars":
        return pl.DataFrame(records)
    return pd.DataFrame(records)


def _as_pandas(frame: DataFrame) -> pd.DataFrame:
    return frame.to_pandas() if isinstance(frame, pl.DataFrame) else frame


def _money_flow_rows() -> list[dict[str, Any]]:
    base = {
        "final_price": 1000.0,
        "close": 1010.0,
        "buy_I_Volume": 6000.0,
        "sell_I_Volume": 4000.0,
        "buy_I_Count": 10,
        "sell_I_Count": 20,
        "buy_N_Volume": 500.0,
        "sell_N_Volume": 500.0,
    }
    return [
        {**base, "volume": 100.0},
        {**base, "volume": 100.0},
        {**base, "volume": 1000.0},
    ]


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_compute_money_flow(engine: str) -> None:
    frame = _frame(_money_flow_rows(), engine)
    result = compute_money_flow(frame, vol_window=2)

    if engine == "polars":
        assert isinstance(result, pl.DataFrame)
    else:
        assert isinstance(result, pd.DataFrame)

    data = _as_pandas(result)
    last = data.iloc[-1]

    assert last["real_buy_per_capita"] == pytest.approx(600_000.0)
    assert last["real_buy_per_capita_m"] == pytest.approx(0.06)
    assert last["real_sell_per_capita"] == pytest.approx(200_000.0)
    assert last["real_sell_per_capita_m"] == pytest.approx(0.02)
    assert last["buyer_power_ratio"] == pytest.approx(3.0)
    assert last["net_real_value"] == pytest.approx(2_000_000.0)
    assert last["ownership_change_pct"] == pytest.approx(200.0)
    assert last["close_vs_final_pct"] == pytest.approx(1.0)
    assert last["vol_ratio"] == pytest.approx(1000.0 / 550.0)
    assert bool(last["smart_money_entry"]) is True
    assert bool(last["smart_money_exit"]) is False


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_compute_money_flow_exit_signal(engine: str) -> None:
    rows = _money_flow_rows()
    for row in rows:
        row["buy_I_Volume"] = 1000.0
        row["sell_I_Volume"] = 6000.0
        row["close"] = 990.0
    frame = _frame(rows, engine)

    data = _as_pandas(compute_money_flow(frame, vol_window=2))
    assert bool(data.iloc[-1]["smart_money_exit"]) is True
    assert bool(data.iloc[-1]["smart_money_entry"]) is False


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_detect_swing_legs(engine: str) -> None:
    rows = [
        {
            "date": f"2026-01-0{index + 1}",
            "close": close,
            "volume": volume,
        }
        for index, (close, volume) in enumerate(
            zip([100.0, 110.0, 105.0, 120.0, 90.0, 95.0], [10, 20, 30, 40, 50, 60], strict=True)
        )
    ]
    frame = _frame(rows, engine)

    result = detect_swing_legs(frame, reversal_pct=5.0)
    if engine == "polars":
        assert isinstance(result, pl.DataFrame)
        assert result.height == 3
    else:
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 3

    legs = _as_pandas(result)
    assert list(legs["direction"]) == [1, -1, 1]
    assert legs.iloc[0]["leg_return_pct"] == pytest.approx(20.0)
    assert legs.iloc[1]["leg_return_pct"] == pytest.approx(-25.0)
    assert legs.iloc[1]["leg_velocity"] == pytest.approx(-25.0)
    assert legs.iloc[1]["leg_volume_expansion"] == pytest.approx(45.0 / 25.0)


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_compute_movement_power_direction(engine: str) -> None:
    rising = [{"close": float(value), "volume": 1000.0} for value in range(1, 21)]
    rising_frame = _frame(rising, engine)
    rising_result = compute_movement_power(rising_frame)
    rising_data = _as_pandas(rising_result)

    assert {"er_5", "er_20", "er_60", "movement_power_score"} <= set(rising_data.columns)
    assert rising_data.iloc[-1]["movement_power_score"] > 0
    assert rising_data.iloc[-1]["movement_power_score"] <= 100.0

    falling = [{"close": float(value), "volume": 1000.0} for value in range(20, 0, -1)]
    falling_result = compute_movement_power(_frame(falling, engine))
    falling_data = _as_pandas(falling_result)
    assert falling_data.iloc[-1]["movement_power_score"] < 0
    assert falling_data.iloc[-1]["movement_power_score"] >= -100.0

    if engine == "polars":
        assert isinstance(rising_result, pl.DataFrame)
        assert isinstance(falling_result, pl.DataFrame)


def _market_rows() -> list[dict[str, Any]]:
    return [
        {
            "ins_code": "1",
            "symbol": "A",
            "close": 500.0,
            "final_price": 490.0,
            "tmax": 500.0,
            "tmin": 450.0,
            "order_book": [{"zmd": 1000.0, "zmo": 0.0}],
        },
        {
            "ins_code": "2",
            "symbol": "B",
            "close": 450.0,
            "final_price": 460.0,
            "tmax": 500.0,
            "tmin": 450.0,
            "order_book": [{"zmd": 0.0, "zmo": 2000.0}],
        },
        {
            "ins_code": "3",
            "symbol": "C",
            "close": 100.0,
            "final_price": 100.0,
            "tmax": 120.0,
            "tmin": 80.0,
            "order_book": [{"zmd": 0.0, "zmo": 0.0}],
        },
    ]


@pytest.mark.parametrize("engine", ["pandas", "polars"])
def test_market_screener(engine: str) -> None:
    frame = _frame(_market_rows(), engine)
    screener = MarketScreener(frame)

    buy = screener.filter_buy_queue()
    sell = screener.filter_sell_queue()
    surge = screener.filter_positive_tick_surge(min_diff_pct=1.5)

    assert isinstance(buy, type(frame))
    assert list(_as_pandas(buy)["symbol"]) == ["A"]
    assert list(_as_pandas(sell)["symbol"]) == ["B"]
    assert list(_as_pandas(surge)["symbol"]) == ["A"]

    scanned = screener.scan_market(lambda data: data[data["symbol"].isin(["B", "C"])])
    assert list(_as_pandas(scanned)["symbol"]) == ["B", "C"]


def test_market_screener_requires_columns() -> None:
    screener = MarketScreener(pd.DataFrame({"symbol": ["A"]}))
    with pytest.raises(KeyError):
        screener.filter_buy_queue()
