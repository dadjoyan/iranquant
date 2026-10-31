"""Smart-money and real/legal money-flow analytics."""

from __future__ import annotations

from typing import cast

import pandas as pd

from iranquant.core.dataframe import DataFrame
from iranquant.quant._utils import restore, to_pandas

_REQUIRED = (
    "final_price",
    "close",
    "volume",
    "buy_I_Volume",
    "sell_I_Volume",
    "buy_I_Count",
    "sell_I_Count",
)


def compute_money_flow(frame: DataFrame, vol_window: int = 20) -> DataFrame:
    """Add smart-money, per-capita and real/legal flow columns.

    Works with both Pandas and Polars inputs and returns a frame of the same
    type.  All monetary per-capita values are expressed in Rials
    (``*_m`` variants are in million Tomans).

    The required input columns are ``final_price``, ``close``, ``volume``,
    ``buy_I_Volume``, ``sell_I_Volume``, ``buy_I_Count`` and ``sell_I_Count``
    (all present in :func:`iranquant.full_history`).  Columns are computed as:

    * ``real_buy_per_capita = buy_I_Volume * final_price / max(buy_I_Count, 1)``
    * ``real_sell_per_capita = sell_I_Volume * final_price / max(sell_I_Count, 1)``
    * ``buyer_power_ratio = real_buy_per_capita / max(real_sell_per_capita, 1)``
    * ``net_real_value = (buy_I_Volume - sell_I_Volume) * final_price``
    * ``ownership_change_pct = ((buy_I_Volume - sell_I_Volume) / max(volume, 1)) * 100``
    * ``close_vs_final_pct = ((close - final_price) / max(final_price, 1)) * 100``
    * ``vol_ratio = volume / rolling_mean(volume, vol_window)``

    ``smart_money_entry`` is ``True`` when ``vol_ratio >= 1.8`` **and**
    ``buyer_power_ratio >= 1.5`` **and** ``net_real_value > 0`` **and**
    ``close >= final_price``.  ``smart_money_exit`` mirrors it with
    ``buyer_power_ratio <= 0.67``, ``net_real_value < 0`` and
    ``close <= final_price``.

    Args:
        frame: A history frame.  Any extra columns (including ``date`` and
            ``jdate``) are preserved unchanged.
        vol_window: Rolling window used for the volume-ratio baseline.  Must be
            at least ``1``.  Defaults to ``20``.

    Returns:
        A frame of the same engine with all input columns plus:

        * ``real_buy_per_capita`` (``float``) — Rials per retail buy order.
        * ``real_buy_per_capita_m`` (``float``) — same, in million Tomans.
        * ``real_sell_per_capita`` (``float``) — Rials per retail sell order.
        * ``real_sell_per_capita_m`` (``float``) — same, in million Tomans.
        * ``buyer_power_ratio`` (``float``) — real buy/sell power ratio.
        * ``net_real_value`` (``float``) — net real money flow in Rials.
        * ``ownership_change_pct`` (``float``) — net real volume / total volume.
        * ``close_vs_final_pct`` (``float``) — last price vs closing price.
        * ``vol_ratio`` (``float``) — volume relative to its rolling mean.
        * ``smart_money_entry`` (``bool``) — smart-money accumulation signal.
        * ``smart_money_exit`` (``bool``) — smart-money distribution signal.

    Raises:
        ValueError: If ``vol_window < 1``.
        KeyError: If any required input column is missing.

    Examples:
        >>> flow = compute_money_flow(iq.full_history("فولاد"))
        >>> flow[["jdate", "buyer_power_ratio", "smart_money_entry"]].tail(3)

        The Polars path preserves ``pl.DataFrame``::

        >>> import polars as pl
        >>> flow = compute_money_flow(iq.full_history("فولاد", engine="polars"))
        >>> flow.filter(pl.col("smart_money_entry")).height
    """

    if vol_window < 1:
        raise ValueError("vol_window must be >= 1")

    source = to_pandas(frame)
    missing = [column for column in _REQUIRED if column not in source.columns]
    if missing:
        raise KeyError(f"Missing required columns: {', '.join(missing)}")

    out = source.copy()
    final_price = cast(pd.Series, out["final_price"].astype(float))
    close = cast(pd.Series, out["close"].astype(float))
    volume = cast(pd.Series, out["volume"].astype(float))
    buy_i_volume = cast(pd.Series, out["buy_I_Volume"].astype(float))
    sell_i_volume = cast(pd.Series, out["sell_I_Volume"].astype(float))
    buy_i_count = cast(pd.Series, out["buy_I_Count"].astype(float))
    sell_i_count = cast(pd.Series, out["sell_I_Count"].astype(float))

    real_buy = (buy_i_volume * final_price) / buy_i_count.clip(lower=1.0)
    real_sell = (sell_i_volume * final_price) / sell_i_count.clip(lower=1.0)
    buyer_power = real_buy / real_sell.clip(lower=1.0)

    net_real_value = (buy_i_volume - sell_i_volume) * final_price
    ownership_change = ((buy_i_volume - sell_i_volume) / volume.clip(lower=1.0)) * 100.0
    close_vs_final = ((close - final_price) / final_price.clip(lower=1.0)) * 100.0

    rolling_volume = volume.rolling(vol_window, min_periods=1).mean()
    vol_ratio = (volume / rolling_volume.clip(lower=1e-9)).fillna(0.0)

    out["real_buy_per_capita"] = real_buy
    out["real_buy_per_capita_m"] = real_buy / 1e7
    out["real_sell_per_capita"] = real_sell
    out["real_sell_per_capita_m"] = real_sell / 1e7
    out["buyer_power_ratio"] = buyer_power
    out["net_real_value"] = net_real_value
    out["ownership_change_pct"] = ownership_change
    out["close_vs_final_pct"] = close_vs_final
    out["vol_ratio"] = vol_ratio
    out["smart_money_entry"] = (
        (vol_ratio >= 1.8) & (buyer_power >= 1.5) & (net_real_value > 0) & (close >= final_price)
    )
    out["smart_money_exit"] = (
        (vol_ratio >= 1.8) & (buyer_power <= 0.67) & (net_real_value < 0) & (close <= final_price)
    )
    return restore(frame, out)


__all__ = ["compute_money_flow"]
