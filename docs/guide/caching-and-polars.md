# Caching & Polars

## Pandas or Polars, same API

Every public data function accepts `engine="pandas"` (default) or
`engine="polars"` and returns that engine type:

```python
import iranquant as iq

pandas_frame = iq.history("فولاد")                     # pandas.DataFrame
polars_frame = iq.history("فولاد", engine="polars")    # polars.DataFrame
```

The quant functions preserve the input engine:

```python
import polars as pl

flow = iq.money_flow("فولاد", engine="polars")
assert isinstance(flow, pl.DataFrame)

entries = flow.filter(pl.col("smart_money_entry"))
```

This also applies to Codal parsing:

```python
tables = iq.codal_report(letter, engine="polars")
```

## Local Parquet cache

`ParquetCache` stores one Parquet file per logical key under
`~/.cache/iranquant/` by default. Override the location with the
`IRANQUANT_CACHE_DIR` environment variable or by passing a directory.

```python
from iranquant.core.cache import ParquetCache

cache = ParquetCache()                     # ~/.cache/iranquant
print(cache.directory)
```

### Save and load

```python
import iranquant as iq

frame = iq.history("فولاد")
path = cache.save("fold-history", frame)
print(path)                                # .../fold-history.parquet

loaded = cache.load("fold-history")        # pandas
loaded_pl = cache.load("fold-history", engine="polars")
```

Unsafe characters in a key are replaced with underscores:

```python
cache.path_for("فولاد/history")
# PosixPath('~/.cache/iranquant/_history.parquet')
```

### Incremental updates

`update()` merges new rows into the cached dataset, keeping the newest row per
date and sorting the result. This is the recommended daily workflow:

```python
import iranquant as iq
from iranquant.core.cache import ParquetCache

cache = ParquetCache()

# First run: fetch and persist everything.
full = cache.update("fold-history", iq.history("فولاد", adjust="all"))

# Later runs: fetch again (TSETMC returns the full series) and merge.
# `update` replaces duplicate dates and keeps the series sorted.
combined = cache.update("fold-history", iq.history("فولاد", adjust="all"))
print(combined.tail())
```

If your upstream call can return only new bars, `update()` still works: it
deduplicates on `date_column` (default `"date"`) and keeps the last row.

```python
cache.update(
    "fold-history",
    new_bars,
    date_column="date",
    engine="polars",
)
```

### Manage the cache

```python
cache.exists("fold-history")     # bool
cache.delete("fold-history")     # remove one file
cache.clear()                    # remove every *.parquet in the directory
```

`get()` and `put()` are aliases of `load()` and `save()`.

### Errors

| Condition | Exception |
| --- | --- |
| Unknown `engine` | `ValueError` |
| Missing `date_column` during `update` | `KeyError` |
| Unwritable path | `OSError` |

!!! note
    The market functions do **not** write to the cache automatically. `ParquetCache`
    is a standalone building block so you stay in control of keys, retention and
    which symbols are persisted.

## End-to-end example

```python
import iranquant as iq
from iranquant.core.cache import ParquetCache

cache = ParquetCache()

def load_or_update(symbol: str) -> object:
    key = f"{symbol}-history"
    fresh = iq.history(symbol, adjust="all", engine="polars")
    return cache.update(key, fresh, engine="polars")

history = load_or_update("فولاد")
flow = iq.compute_money_flow(history, vol_window=20)
print(flow.select(["jdate", "buyer_power_ratio"]).tail())
```
