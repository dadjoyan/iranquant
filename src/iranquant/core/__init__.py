"""Core utilities: HTTP client, cache, normalizer, dataframe and sync helpers."""

from __future__ import annotations

from .cache import ParquetCache
from .client import AsyncHTTPClient
from .dataframe import Engine, to_dataframe
from .normalizer import (
    int_to_date,
    int_to_date_optional,
    int_to_jdate,
    int_to_jdate_optional,
    int_to_time,
    int_to_time_optional,
    normalize_persian,
)
from .sync import run_sync

__all__ = [
    "AsyncHTTPClient",
    "Engine",
    "ParquetCache",
    "int_to_date",
    "int_to_date_optional",
    "int_to_jdate",
    "int_to_jdate_optional",
    "int_to_time",
    "int_to_time_optional",
    "normalize_persian",
    "run_sync",
    "to_dataframe",
]
