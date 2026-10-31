"""Parquet cache with incremental, date-aware updates."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from iranquant.config import CACHE_DIR
from iranquant.core.dataframe import DataFrame, Engine

_SAFE_KEY = re.compile(r"[^A-Za-z0-9_.-]+")


class ParquetCache:
    """Local Parquet store rooted at ``~/.cache/iranquant`` by default.

    Frames are written as single Parquet files whose names are derived from a
    caller-supplied key.  Both Pandas and Polars frames round-trip, and
    :meth:`update` merges new rows into an existing dataset without duplicates.

    The directory can be overridden with the ``IRANQUANT_CACHE_DIR`` environment
    variable or by passing ``directory``.

    Args:
        directory: Cache directory.  Defaults to
            :data:`iranquant.config.CACHE_DIR`, i.e.
            ``~/.cache/iranquant`` or ``$IRANQUANT_CACHE_DIR``.

    Examples:
        >>> cache = ParquetCache("/tmp/iranquant-cache")
        >>> cache.save("fold-history", iq.history("فولاد"))
        PosixPath('/tmp/iranquant-cache/fold-history.parquet')
        >>> loaded = cache.load("fold-history")
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else CACHE_DIR

    def path_for(self, key: str) -> Path:
        """Return the Parquet path used for a cache key.

        Unsafe characters (anything outside ``A-Z a-z 0-9 _ . -``) are replaced
        with underscores.

        Args:
            key: Logical cache key, e.g. ``"fold-history"``.

        Returns:
            The absolute :class:`pathlib.Path` of the Parquet file.

        Examples:
            >>> ParquetCache("/tmp").path_for("فولاد/history")
            PosixPath('/tmp/_history.parquet')
        """
        safe = _SAFE_KEY.sub("_", key).strip("._") or "cache"
        return self.directory / f"{safe}.parquet"

    def exists(self, key: str) -> bool:
        """Return ``True`` when a Parquet file exists for ``key``.

        Args:
            key: Logical cache key.

        Returns:
            Whether the underlying file is present.
        """
        return self.path_for(key).exists()

    def save(self, key: str, frame: DataFrame) -> Path:
        """Write a Pandas or Polars frame to the cache.

        Args:
            key: Logical cache key.
            frame: The frame to persist.  Missing parent directories are
                created automatically.

        Returns:
            The path of the written Parquet file.

        Raises:
            OSError: If the file cannot be written.
        """
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(frame, pl.DataFrame):
            frame.write_parquet(path)
        else:
            frame.to_parquet(path, engine="pyarrow", index=False)
        return path

    def load(self, key: str, *, engine: Engine = "pandas") -> DataFrame | None:
        """Read a cached frame, if present.

        Args:
            key: Logical cache key.
            engine: Output engine, ``"pandas"`` (default) or ``"polars"``.

        Returns:
            The cached frame, or ``None`` when no file exists for ``key``.

        Raises:
            ValueError: If ``engine`` is not ``"pandas"`` or ``"polars"``.
        """
        path = self.path_for(key)
        if not path.exists():
            return None
        if engine == "polars":
            return pl.read_parquet(path)
        if engine == "pandas":
            return pd.read_parquet(path, engine="pyarrow")
        raise ValueError(f"Unsupported dataframe engine: {engine!r}")

    def update(
        self,
        key: str,
        frame: DataFrame,
        *,
        date_column: str = "date",
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Merge ``frame`` into the cached dataset, keeping the newest row per date.

        Existing rows for a date are replaced by rows from ``frame`` and the
        result is sorted ascending by ``date_column``.  This is what makes
        incremental daily updates cheap: fetch only new bars, then merge.

        Args:
            key: Logical cache key.
            frame: New rows to merge.
            date_column: Column used to detect duplicate rows.  Defaults to
                ``"date"``.
            engine: Engine used when the existing file is read and for the
                returned frame.

        Returns:
            The merged, date-sorted frame in the requested engine.

        Raises:
            ValueError: If ``engine`` is not ``"pandas"`` or ``"polars"``.
            KeyError: If ``date_column`` is missing from either frame.
        """

        existing = self.load(key, engine=engine)
        if existing is None:
            combined = frame
        elif isinstance(frame, pl.DataFrame):
            if not isinstance(existing, pl.DataFrame):
                existing = pl.from_pandas(existing)
            combined = (
                pl.concat([existing, frame], how="diagonal_relaxed")
                .unique(subset=[date_column], keep="last")
                .sort(date_column)
            )
        else:
            if not isinstance(existing, pd.DataFrame):
                existing = existing.to_pandas()
            combined = (
                pd.concat([existing, frame], ignore_index=True)
                .drop_duplicates(subset=[date_column], keep="last")
                .sort_values(date_column)
                .reset_index(drop=True)
            )
        self.save(key, combined)
        return combined

    def delete(self, key: str) -> None:
        """Delete the cached file for ``key`` if it exists.

        Args:
            key: Logical cache key.
        """
        path = self.path_for(key)
        if path.exists():
            path.unlink()

    def clear(self) -> None:
        """Delete every ``*.parquet`` file inside the cache directory.

        Files that do not match ``*.parquet`` are left untouched.
        """
        if not self.directory.exists():
            return
        for path in self.directory.glob("*.parquet"):
            path.unlink()

    def __repr__(self) -> str:
        """Return the cache directory in a concise form."""
        return f"ParquetCache(directory={self.directory!r})"

    def get(self, key: str, *, engine: Engine = "pandas") -> DataFrame | None:
        """Alias of :meth:`load`.

        Args:
            key: Logical cache key.
            engine: Output engine, ``"pandas"`` or ``"polars"``.

        Returns:
            The cached frame, or ``None`` when the key is missing.
        """
        return self.load(key, engine=engine)

    def put(self, key: str, frame: Any, *, engine: Engine = "pandas") -> Path:
        """Alias of :meth:`save` that validates the engine name first.

        Args:
            key: Logical cache key.
            frame: A Pandas or Polars DataFrame.
            engine: ``"pandas"`` or ``"polars"``; validated but otherwise unused
                because the frame type is detected directly.

        Returns:
            The path of the written Parquet file.

        Raises:
            ValueError: If ``engine`` is not ``"pandas"`` or ``"polars"``.
        """
        if engine not in ("pandas", "polars"):
            raise ValueError(f"Unsupported dataframe engine: {engine!r}")
        return self.save(key, frame)
