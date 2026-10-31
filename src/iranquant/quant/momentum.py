"""Swing-leg detection and multi-timeframe movement power."""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd

from iranquant.core.dataframe import DataFrame
from iranquant.quant._utils import empty_like, restore, to_pandas

LEG_COLUMNS = [
    "leg_id",
    "direction",
    "start_date",
    "end_date",
    "start_price",
    "end_price",
    "leg_return_pct",
    "leg_bars",
    "leg_velocity",
    "avg_volume",
    "leg_volume_expansion",
]


def _zigzag(prices: np.ndarray, threshold: float) -> list[tuple[int, float]]:
    """Return alternating swing pivots using a percentage-reversal ZigZag."""

    length = len(prices)
    if length == 0:
        return []
    if length == 1:
        return [(0, float(prices[0]))]

    pivots: list[tuple[int, float]] = [(0, float(prices[0]))]
    direction = 0
    anchor_price = float(prices[0])
    extreme_index = 0
    extreme_price = float(prices[0])

    for index in range(1, length):
        price = float(prices[index])
        if direction == 0:
            if price >= anchor_price * (1.0 + threshold):
                direction = 1
                extreme_index, extreme_price = index, price
            elif price <= anchor_price * (1.0 - threshold):
                direction = -1
                extreme_index, extreme_price = index, price
        elif direction == 1:
            if price > extreme_price:
                extreme_index, extreme_price = index, price
            elif price <= extreme_price * (1.0 - threshold):
                pivots.append((extreme_index, extreme_price))
                direction = -1
                extreme_index, extreme_price = index, price
        else:
            if price < extreme_price:
                extreme_index, extreme_price = index, price
            elif price >= extreme_price * (1.0 + threshold):
                pivots.append((extreme_index, extreme_price))
                direction = 1
                extreme_index, extreme_price = index, price

    if direction == 0:
        if pivots[-1][0] != length - 1:
            pivots.append((length - 1, float(prices[-1])))
    else:
        if extreme_index != pivots[-1][0]:
            pivots.append((extreme_index, extreme_price))
        if pivots[-1][0] != length - 1:
            pivots.append((length - 1, float(prices[-1])))

    cleaned: list[tuple[int, float]] = []
    for index, price in pivots:
        if cleaned and index <= cleaned[-1][0]:
            continue
        cleaned.append((index, price))
    return cleaned


def detect_swing_legs(frame: DataFrame, reversal_pct: float = 5.0) -> DataFrame:
    """Detect ZigZag swing legs from the ``close`` column.

    A percentage-reversal ZigZag is run over the closing prices.  A leg is
    confirmed whenever price reverses by at least ``reversal_pct`` from the
    running extreme; the final, still-active leg is appended so the output
    always ends at the last bar.  Input and output engine types match.

    Args:
        frame: A history frame containing ``close``.  ``date`` (used for
            ``start_date``/``end_date``) and ``volume`` (used for volume
            statistics) are optional; without ``date`` positional integer
            indexes are used, and without ``volume`` the volume columns are
            ``0.0``/``NaN``.
        reversal_pct: Minimum reversal, in percent, required to confirm a new
            pivot.  Must be positive.  Defaults to ``5.0``.

    Returns:
        One row per leg (completed legs plus the active leg) with columns:

        * ``leg_id`` (``int``) — one-based leg number.
        * ``direction`` (``int``) — ``1`` for up legs, ``-1`` for down legs.
        * ``start_date`` / ``end_date`` (``Any``) — values from ``date``, or
        positional indexes when ``date`` is absent.
        * ``start_price`` / ``end_price`` (``float``) — pivot prices.
        * ``leg_return_pct`` (``float``) — percentage price change over the leg.
        * ``leg_bars`` (``int``) — number of bars spanned (end index - start).
        * ``leg_velocity`` (``float``) — ``leg_return_pct / max(leg_bars, 1)``.
        * ``avg_volume`` (``float``) — mean volume across the leg's bars.
        * ``leg_volume_expansion`` (``float``) — this leg's ``avg_volume``
        divided by the previous leg's; ``NaN`` for the first leg or when the
        previous average is zero.

        An empty frame with the same columns is returned for empty input or
        when no pivots are found.

    Raises:
        ValueError: If ``reversal_pct <= 0``.
        KeyError: If the ``close`` column is missing.

    Examples:
        >>> legs = detect_swing_legs(iq.history("فولاد"), reversal_pct=5.0)
        >>> legs[["direction", "leg_return_pct", "leg_velocity"]].tail(5)
        >>> legs[legs["leg_volume_expansion"] > 1.5]["direction"].tolist()
    """

    if reversal_pct <= 0:
        raise ValueError("reversal_pct must be positive")

    source = to_pandas(frame)
    if source.empty:
        return empty_like(frame, LEG_COLUMNS)
    if "close" not in source.columns:
        raise KeyError("Missing required column: close")

    closes = source["close"].astype(float).to_numpy()
    dates: list[Any] = (
        source["date"].tolist() if "date" in source.columns else list(range(len(source)))
    )
    volumes = (
        source["volume"].astype(float).to_numpy()
        if "volume" in source.columns
        else np.zeros(len(source), dtype=float)
    )

    pivots = _zigzag(closes, reversal_pct / 100.0)
    legs: list[dict[str, Any]] = []
    for leg_id in range(1, len(pivots)):
        start_index, start_price = pivots[leg_id - 1]
        end_index, end_price = pivots[leg_id]
        if end_index <= start_index:
            continue
        direction = 1 if end_price >= start_price else -1
        return_pct = ((end_price / start_price) - 1.0) * 100.0 if start_price else 0.0
        bars = end_index - start_index
        avg_volume = float(np.mean(volumes[start_index : end_index + 1]))
        legs.append(
            {
                "leg_id": leg_id,
                "direction": direction,
                "start_date": dates[start_index],
                "end_date": dates[end_index],
                "start_price": start_price,
                "end_price": end_price,
                "leg_return_pct": return_pct,
                "leg_bars": bars,
                "leg_velocity": return_pct / max(bars, 1),
                "avg_volume": avg_volume,
                "leg_volume_expansion": float("nan"),
            }
        )

    for index in range(1, len(legs)):
        previous_volume = float(legs[index - 1]["avg_volume"])
        if previous_volume > 0:
            legs[index]["leg_volume_expansion"] = float(legs[index]["avg_volume"]) / previous_volume

    if not legs:
        return empty_like(frame, LEG_COLUMNS)
    result = pd.DataFrame(legs, columns=LEG_COLUMNS)
    return restore(frame, result)


def _signed_efficiency_ratio(close: pd.Series, window: int) -> pd.Series:
    change = close - close.shift(window)
    path = close.diff().abs().rolling(window, min_periods=1).sum()
    ratio = (change.abs() / path.clip(lower=1e-12)).fillna(0.0)
    direction = np.sign(change.fillna(0.0).to_numpy(dtype=float))
    signed = np.nan_to_num(direction * ratio.to_numpy(), nan=0.0)
    return pd.Series(signed, index=close.index, dtype=float)


def compute_movement_power(
    frame: DataFrame,
    windows: tuple[int, int, int] = (5, 20, 60),
) -> DataFrame:
    """Compute a signed multi-timeframe Kaufman efficiency power score.

    For each window ``w`` the signed Kaufman efficiency ratio is::

        er_w = sign(close - close.shift(w))
               * abs(close - close.shift(w))
               / rolling_sum(abs(close.diff()), w)

    The score starts as a weighted sum of the signed ratios.  With exactly
    three windows the weights are ``(0.5, 0.3, 0.2)``; otherwise all windows
    are equally weighted.  The score is then multiplied by a volume factor
    (``volume / rolling_mean(volume, min(windows))`` clipped to ``[0.5, 2.0]``)
    and, when present, a buyer-power factor
    (``clip(buyer_power_ratio, 0.5, 3.0) / 1.5`` clipped to ``[0.5, 1.5]``),
    scaled by ``100`` and clipped to ``[-100, +100]``.

    Args:
        frame: A history frame containing ``close``.  ``volume`` and
            ``buyer_power_ratio`` (produced by :func:`compute_money_flow`) are
            optional and only modulate the score when present.
        windows: One or more positive lookback windows.  Defaults to
            ``(5, 20, 60)``.

    Returns:
        A frame of the same engine with all input columns plus:

        * ``er_<window>`` (``float``) — signed efficiency ratio per window
        (for example ``er_5``, ``er_20``, ``er_60``).
        * ``movement_power_score`` (``float``) — weighted, volume- and
        buyer-power-adjusted score in ``[-100, +100]``.

    Raises:
        ValueError: If ``windows`` is empty or contains a non-positive value.
        KeyError: If the ``close`` column is missing.

    Examples:
        >>> power = compute_movement_power(iq.full_history("فولاد"))
        >>> power[["jdate", "er_5", "er_20", "movement_power_score"]].tail(3)
        >>> power.sort_values("movement_power_score", ascending=False).head()
    """

    if not windows:
        raise ValueError("At least one window is required")
    if any(window < 1 for window in windows):
        raise ValueError("Windows must be positive integers")

    source = to_pandas(frame)
    if "close" not in source.columns:
        raise KeyError("Missing required column: close")
    if source.empty:
        out = source.copy()
        for window in windows:
            out[f"er_{window}"] = pd.Series(dtype=float)
        out["movement_power_score"] = pd.Series(dtype=float)
        return restore(frame, out)

    out = source.copy()
    close = cast(pd.Series, out["close"].astype(float))

    weights = [0.5, 0.3, 0.2] if len(windows) == 3 else [1.0 / len(windows)] * len(windows)

    score = pd.Series(0.0, index=out.index, dtype=float)
    for window, weight in zip(windows, weights, strict=True):
        signed = _signed_efficiency_ratio(close, window)
        out[f"er_{window}"] = signed
        score = score + (signed * weight)

    shortest = min(windows)
    if "volume" in out.columns:
        volume = cast(pd.Series, out["volume"].astype(float))
        rolling_volume = volume.rolling(shortest, min_periods=1).mean()
        volume_factor = (volume / rolling_volume.clip(lower=1e-9)).fillna(1.0)
        volume_factor = volume_factor.clip(lower=0.5, upper=2.0)
    else:
        volume_factor = pd.Series(1.0, index=out.index, dtype=float)

    if "buyer_power_ratio" in out.columns:
        buyer_power = cast(pd.Series, out["buyer_power_ratio"].astype(float))
        buyer_power = buyer_power.fillna(1.0).clip(lower=0.5, upper=3.0) / 1.5
        buyer_power = buyer_power.clip(lower=0.5, upper=1.5)
    else:
        buyer_power = pd.Series(1.0, index=out.index, dtype=float)

    power = (score * 100.0 * volume_factor * buyer_power).clip(lower=-100.0, upper=100.0)
    out["movement_power_score"] = power
    return restore(frame, out)


__all__ = ["LEG_COLUMNS", "compute_movement_power", "detect_swing_legs"]
