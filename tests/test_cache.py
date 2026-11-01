"""Tests for the local Parquet cache."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import polars as pl

from iranquant.core.cache import ParquetCache


def _pandas_frame(values: list[tuple[dt.date, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [value[0] for value in values],
            "close": [value[1] for value in values],
        }
    )


def test_save_and_load_pandas(tmp_path: Path) -> None:
    cache = ParquetCache(tmp_path)
    frame = _pandas_frame([(dt.date(2026, 1, 1), 100.0)])

    cache.save("fold", frame)
    loaded = cache.load("fold")

    assert isinstance(loaded, pd.DataFrame)
    assert loaded is not None
    assert loaded.iloc[0]["close"] == 100.0


def test_update_deduplicates_by_date(tmp_path: Path) -> None:
    cache = ParquetCache(tmp_path)
    cache.save(
        "fold",
        _pandas_frame([(dt.date(2026, 1, 1), 100.0), (dt.date(2026, 1, 2), 101.0)]),
    )

    combined = cache.update(
        "fold",
        _pandas_frame([(dt.date(2026, 1, 2), 999.0), (dt.date(2026, 1, 3), 102.0)]),
    )

    assert isinstance(combined, pd.DataFrame)
    assert len(combined) == 3
    assert list(combined["close"]) == [100.0, 999.0, 102.0]


def test_polars_roundtrip_and_update(tmp_path: Path) -> None:
    cache = ParquetCache(tmp_path)
    frame = pl.DataFrame(
        {
            "date": [dt.date(2026, 1, 1), dt.date(2026, 1, 2)],
            "close": [100.0, 101.0],
        }
    )
    cache.save("fold", frame)

    loaded = cache.load("fold", engine="polars")
    assert isinstance(loaded, pl.DataFrame)
    assert loaded.height == 2

    combined = cache.update(
        "fold",
        pl.DataFrame({"date": [dt.date(2026, 1, 3)], "close": [102.0]}),
        engine="polars",
    )
    assert isinstance(combined, pl.DataFrame)
    assert combined.height == 3


def test_missing_key_returns_none(tmp_path: Path) -> None:
    cache = ParquetCache(tmp_path)
    assert cache.load("nope") is None
    assert not cache.exists("nope")


def test_corrupted_parquet_is_evicted(tmp_path: Path) -> None:
    cache = ParquetCache(tmp_path)
    path = cache.path_for("broken")
    path.write_bytes(b"not a parquet file")

    assert cache.load("broken") is None
    assert not path.exists()


def test_key_is_sanitized(tmp_path: Path) -> None:
    cache = ParquetCache(tmp_path)
    path = cache.path_for("../../evil key")
    assert path.parent == tmp_path
    assert ".." not in path.name


def test_delete(tmp_path: Path) -> None:
    cache = ParquetCache(tmp_path)
    cache.save("fold", _pandas_frame([(dt.date(2026, 1, 1), 100.0)]))
    assert cache.exists("fold")
    cache.delete("fold")
    assert not cache.exists("fold")
