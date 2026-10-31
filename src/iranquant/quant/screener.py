"""Vectorized market-wide screening helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from iranquant.core.dataframe import DataFrame
from iranquant.quant._utils import restore, to_pandas


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _side_volume(order_book: Any, side: str) -> float:
    if order_book is None:
        return 0.0
    if isinstance(order_book, dict):
        levels: list[Any] = [order_book]
    elif isinstance(order_book, str):
        return 0.0
    else:
        levels = list(order_book)

    key = "zmd" if side == "buy" else "zmo"
    total = 0.0
    for level in levels:
        if isinstance(level, dict):
            total += _to_float(level.get(key, 0.0))
        else:
            total += _to_float(getattr(level, key, 0.0))
    return total


class MarketScreener:
    """Vectorized filters over a MarketWatch (or batch) DataFrame.

    Every filter accepts an optional alternative frame; when omitted the frame
    passed to the constructor is used.  Results preserve the input engine type
    (Pandas in, Pandas out; Polars in, Polars out) and are reset to a clean
    index.

    Args:
        frame: An optional MarketWatch frame from
            :func:`iranquant.market_watch` (or any frame with the same
            columns).  Filters may instead receive the frame directly, so both
            ``MarketScreener(frame).filter_buy_queue()`` and
            ``MarketScreener().filter_buy_queue(frame)`` are equivalent.

    Raises:
        ValueError: If a filter runs without a bound or call-time frame.
        TypeError: If the selected frame is neither a Pandas nor a Polars
            DataFrame (raised lazily when a filter runs).

    Examples:
        >>> screener = MarketScreener(iq.market_watch())
        >>> screener.filter_buy_queue()[["symbol", "close", "tmax"]]
        >>> MarketScreener().filter_buy_queue(iq.market_watch()).head()
    """

    def __init__(self, frame: DataFrame | None = None) -> None:
        self._frame = frame

    def _source(self, frame: DataFrame | None) -> tuple[DataFrame, pd.DataFrame]:
        selected = self._frame if frame is None else frame
        if selected is None:
            raise ValueError("No frame supplied: pass one to MarketScreener(...) or to the filter")
        return selected, to_pandas(selected)

    @staticmethod
    def _restore(original: DataFrame, filtered: pd.DataFrame) -> DataFrame:
        return restore(original, filtered.reset_index(drop=True))

    @staticmethod
    def _queue_volume(frame: pd.DataFrame, side: str) -> pd.Series:
        if "order_book" not in frame.columns:
            return pd.Series(0.0, index=frame.index, dtype=float)
        return frame["order_book"].apply(lambda book: _side_volume(book, side))

    def filter_buy_queue(self, frame: DataFrame | None = None) -> DataFrame:
        """Symbols locked at ``tmax`` with a positive buy-queue volume.

        The mask is ``(tmax > 0) & (close >= tmax) & (sum(zmd) > 0)``.  The
        buy-queue volume is the sum of ``zmd`` (bid volume) across all order-book
        levels.

        Args:
            frame: Optional alternative frame; defaults to the constructor
                frame.

        Returns:
            The matching rows in the same engine as the input.

        Raises:
            KeyError: If ``close`` or ``tmax`` is missing.

        Examples:
            >>> locked = screener.filter_buy_queue()
            >>> locked[["symbol", "close", "tmax"]].head()
        """

        original, source = self._source(frame)
        for column in ("close", "tmax"):
            if column not in source.columns:
                raise KeyError(f"Missing required column: {column}")
        close = source["close"].astype(float)
        tmax = source["tmax"].astype(float)
        buy_volume = self._queue_volume(source, "buy")
        mask = (tmax > 0) & (close >= tmax) & (buy_volume > 0)
        return self._restore(original, source.loc[mask])

    def filter_sell_queue(self, frame: DataFrame | None = None) -> DataFrame:
        """Symbols locked at ``tmin`` with a positive sell-queue volume.

        The mask is ``(tmin > 0) & (close <= tmin) & (sum(zmo) > 0)``.  The
        sell-queue volume is the sum of ``zmo`` (ask volume) across all
        order-book levels.

        Args:
            frame: Optional alternative frame; defaults to the constructor
                frame.

        Returns:
            The matching rows in the same engine as the input.

        Raises:
            KeyError: If ``close`` or ``tmin`` is missing.

        Examples:
            >>> locked = screener.filter_sell_queue()
            >>> locked[["symbol", "close", "tmin"]].head()
        """

        original, source = self._source(frame)
        for column in ("close", "tmin"):
            if column not in source.columns:
                raise KeyError(f"Missing required column: {column}")
        close = source["close"].astype(float)
        tmin = source["tmin"].astype(float)
        sell_volume = self._queue_volume(source, "sell")
        mask = (tmin > 0) & (close <= tmin) & (sell_volume > 0)
        return self._restore(original, source.loc[mask])

    def filter_positive_tick_surge(
        self,
        frame: DataFrame | None = None,
        min_diff_pct: float = 1.5,
    ) -> DataFrame:
        """Symbols whose last price exceeds the final price by ``min_diff_pct``.

        The mask is
        ``((close - final_price) / max(final_price, 1)) * 100 >= min_diff_pct``.

        Args:
            frame: Optional alternative frame; defaults to the constructor
                frame.
            min_diff_pct: Minimum percentage gap between the last traded price
                and the closing price.  Defaults to ``1.5``.

        Returns:
            The matching rows in the same engine as the input.

        Raises:
            KeyError: If ``close`` or ``final_price`` is missing.

        Examples:
            >>> surges = screener.filter_positive_tick_surge(min_diff_pct=1.5)
            >>> surges[["symbol", "close", "final_price"]].head()
        """

        original, source = self._source(frame)
        for column in ("close", "final_price"):
            if column not in source.columns:
                raise KeyError(f"Missing required column: {column}")
        close = source["close"].astype(float)
        final_price = source["final_price"].astype(float)
        diff_pct = ((close - final_price) / final_price.clip(lower=1.0)) * 100.0
        mask = diff_pct >= min_diff_pct
        return self._restore(original, source.loc[mask])

    def scan_market(
        self,
        custom_predicate: Callable[[pd.DataFrame], pd.DataFrame | pd.Series],
        frame: DataFrame | None = None,
    ) -> DataFrame:
        """Apply a custom Pandas predicate/mask and restore the engine type.

        Args:
            custom_predicate: A callable that receives the frame as a
                :class:`pandas.DataFrame` and returns either a filtered
                DataFrame or a boolean Series mask.  ``NaN`` mask values are
                treated as ``False``.
            frame: Optional alternative frame; defaults to the constructor
                frame.

        Returns:
            The filtered rows converted back to the input engine.

        Raises:
            TypeError: If ``custom_predicate`` returns neither a DataFrame nor
                a Series.

        Examples:
            >>> high_volume = screener.scan_market(
            ...     lambda df: df["volume"] > df["volume"].median()
            ... )
            >>> high_volume[["symbol", "volume"]].sort_values("volume").tail()
        """

        original, source = self._source(frame)
        result = custom_predicate(source)
        if isinstance(result, pd.DataFrame):
            filtered = result
        elif isinstance(result, pd.Series):
            mask = result.fillna(False).astype(bool)
            filtered = source.loc[mask]
        else:
            raise TypeError("custom_predicate must return a pandas DataFrame or boolean Series")
        return self._restore(original, filtered)


__all__ = ["MarketScreener"]
