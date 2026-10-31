"""Shared helpers for quant functions that accept Pandas or Polars frames."""

from __future__ import annotations

from typing import cast

import pandas as pd
import polars as pl

from iranquant.core.dataframe import DataFrame


def to_pandas(frame: DataFrame) -> pd.DataFrame:
    if isinstance(frame, pl.DataFrame):
        return cast(pd.DataFrame, frame.to_pandas())
    if isinstance(frame, pd.DataFrame):
        return frame
    raise TypeError(f"Expected a pandas or polars DataFrame, got {type(frame)!r}")


def restore(original: DataFrame, result: pd.DataFrame) -> DataFrame:
    if isinstance(original, pl.DataFrame):
        return pl.from_pandas(result)
    return result


def empty_like(original: DataFrame, columns: list[str]) -> DataFrame:
    if isinstance(original, pl.DataFrame):
        return pl.DataFrame({column: [] for column in columns})
    return pd.DataFrame(columns=columns)
