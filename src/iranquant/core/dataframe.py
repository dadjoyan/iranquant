"""Unified Pandas / Polars output conversion."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, cast

import pandas as pd
import polars as pl

Engine = Literal["pandas", "polars"]
DataFrame: TypeAlias = pd.DataFrame | pl.DataFrame


def to_dataframe(records: list[dict[str, Any]], engine: Engine = "pandas") -> DataFrame:
    """Convert a list of record dictionaries to a Pandas or Polars DataFrame.

    An empty ``records`` list still produces a valid (empty) DataFrame for both
    engines.  Column order follows the key insertion order of the first record.

    Args:
        records: Row-oriented records, typically produced by the market and
            Codal modules.  Every record should share the same keys.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A :class:`pandas.DataFrame` or :class:`polars.DataFrame` matching
        ``engine``.

    Raises:
        ValueError: If ``engine`` is neither ``"pandas"`` nor ``"polars"``.

    Examples:
        >>> to_dataframe([{"symbol": "فولاد", "close": 4150.0}])
           symbol   close
        0   فولاد  4150.0
        >>> to_dataframe([], engine="polars").height
        0
    """

    if engine == "polars":
        return pl.DataFrame(records)
    if engine == "pandas":
        return cast(pd.DataFrame, pd.DataFrame(records))
    raise ValueError(f"Unsupported dataframe engine: {engine!r}")
