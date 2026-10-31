# Market Data

Everything in this page is available both synchronously (`iq.history(...)`) and
asynchronously (`await client.history(...)`).

## Symbol search and `insCode` resolution

```python
import iranquant as iq

results = iq.search("فولاد")
print(results)

code = iq.get_inscode("فولاد")      # "46348559193224090"
```

Search output columns:

| Column | Type | Description |
| --- | --- | --- |
| `ins_code` | `str` | TSETMC instrument code |
| `symbol` | `str` | Ticker (`lVal18AFC`) |
| `company_name` | `str` | Full company name |
| `isin` | `str \| None` | International securities code |
| `shares` | `float` | Total listed shares |
| `flow` | `int` | Flow id (`1`, `2`, `4` are tradable) |
| `board` | `str` | Market board code |
| `last_date` | `datetime.date \| None` | Last trade date |
| `last_date_jdate` | `str \| None` | Jalali last trade date |

Resolution details:

- Text is normalized for Arabic yeh/kaf variants, Persian digits and
  zero-width characters.
- Instruments with an active `flow` (`1`, `2`, `4`) win; ties go to the newest
  `lastDate`.
- A numeric `insCode` (or all-digit string) bypasses the network entirely:
  `iq.get_inscode(46348559193224090)` returns immediately.
- Both the search and resolution caches are process-local.

```python
from iranquant.market.symbol import clear_cache
clear_cache()   # useful in long-running workers and tests
```

## Daily OHLCV history

```python
daily = iq.history("فولاد", adjust="all")
```

Output columns (sorted ascending by `date`):

| Column | Type | TSETMC source | Notes |
| --- | --- | --- | --- |
| `date` | `datetime.date` | `dEven` | Gregorian |
| `jdate` | `str` | `dEven` | Jalali `YYYY-MM-DD` |
| `open` | `float` | `priceFirst` | |
| `high` | `float` | `priceMax` | |
| `low` | `float` | `priceMin` | |
| `close` | `float` | `pDrCotVal` | Last traded price |
| `final_price` | `float` | `pClosing` | Official closing price |
| `prev_price` | `float` | `priceYesterday` | Unadjusted previous close |
| `volume` | `float` | `qTotTran5J` | Shares |
| `value` | `float` | `qTotCap` | Rials |
| `trades` | `float` | `zTotTran` | Trade count |

Sentinel dates (`dEven < 19000101`) and zero-volume/zero-trade placeholder rows
are dropped automatically.

## Client-type (retail vs institutional) history

```python
clients = iq.client_type("فولاد")
```

| Column | Type | Description |
| --- | --- | --- |
| `date` | `datetime.date` | Trading date |
| `jdate` | `str` | Jalali date |
| `buy_I_Volume` | `float` | Retail buy volume |
| `buy_N_Volume` | `float` | Institutional buy volume |
| `buy_I_Value` | `float` | Retail buy value (Rials) |
| `buy_N_Value` | `float` | Institutional buy value (Rials) |
| `buy_N_Count` | `int` | Institutional buy orders |
| `buy_I_Count` | `int` | Retail buy orders |
| `sell_I_Volume` | `float` | Retail sell volume |
| `sell_N_Volume` | `float` | Institutional sell volume |
| `sell_I_Value` | `float` | Retail sell value (Rials) |
| `sell_N_Value` | `float` | Institutional sell value (Rials) |
| `sell_N_Count` | `int` | Institutional sell orders |
| `sell_I_Count` | `int` | Retail sell orders |

`iq.full_history("فولاد")` performs an **outer join on `date`** between the
OHLCV frame and this client-type frame, so the result contains all eleven OHLCV
columns followed by the twelve client-type columns above.

## Backward price adjustment

Price gaps occur on ex-dividend and capital-increase days: the raw `final_price`
collapses while `prev_price` on the next day already reflects the event. The
library supports three modes:

| `adjust` | Behavior |
| --- | --- |
| `"none"` | Raw TSETMC prices |
| `"all"` | Backward gap-ratio adjustment for dividends **and** capital increases |
| `"capital_only"` | Backward adjustment for capital increases only |

### `adjust="all"` — gap ratio

For each day `i`, using the newest day as the anchor:

```text
r[i] = prev_price[i+1] / final_price[i]
F[n-1] = 1
F[i]   = F[i+1] * r[i]
```

Every price column (`open`, `high`, `low`, `close`, `final_price`,
`prev_price`) is multiplied by `F[i]`, so the newest bar stays untouched and all
older prices are pulled back onto today's scale.

```python
raw = iq.history("فولاد", adjust="none")
adj = iq.history("فولاد", adjust="all")
```

### `adjust="capital_only"` — ex-rights ratio

For every capital increase from `numberOfShareOld` to `numberOfShareNew`, rows
strictly **before** the event date are multiplied by:

```text
factor = numberOfShareOld / numberOfShareNew
```

Rows on or after the event date are unchanged. This mode fetches the instrument's
share-change history in a second request.

```python
capital_adj = iq.history("فولاد", adjust="capital_only")
```

!!! note
    `apply_gap_adjustment` and `apply_capital_adjustment` are pure functions and
    never mutate their input, so they can be tested in isolation or reused on
    custom records.

## Live MarketWatch

One request returns the entire market:

```python
market = iq.market_watch()
```

| Column | Type | Description |
| --- | --- | --- |
| `ins_code` | `str` | Instrument code |
| `symbol` | `str` | Ticker |
| `company_name` | `str` | Company name |
| `eps` | `float \| None` | Earnings per share |
| `pe` | `float \| None` | Price-to-earnings ratio |
| `open` / `high` / `low` | `float` | Session prices |
| `close` | `float` | Last traded price |
| `final_price` | `float` | Closing price |
| `prev_price` | `float` | Previous close |
| `volume` / `value` / `trades` | `float` | Session activity |
| `tmax` / `tmin` | `float` | Daily high/low limit |
| `total_shares` | `float` | Listed shares |
| `order_book` | `list[dict]` | Levels with `n`, `qmd`, `zmd`, `pmd`, `pmo`, `zmo`, `qmo` |

The endpoint is requested verbatim (brackets in `paperTypes[0]=1` are never
percent-encoded). Current servers send `qtj`/`pMax`/`pMin`; older payloads used
`qtt`/`ps`/`pi` and are still supported.

## Order book (`BestLimits`)

```python
book = iq.order_book("فولاد")
```

| Column | Type | Meaning |
| --- | --- | --- |
| `n` | `int` | Level (1 = best) |
| `qmd` | `float` | Bid order count |
| `zmd` | `float` | Bid volume |
| `pmd` | `float` | Bid price |
| `pmo` | `float` | Ask price |
| `zmo` | `float` | Ask volume |
| `qmo` | `float` | Ask order count |

## Intraday candles

```python
bars = iq.intraday_trades("فولاد")
```

| Column | Type | Description |
| --- | --- | --- |
| `time` | `datetime.time \| None` | Bar time from `hEven` |
| `open` / `high` / `low` / `close` | `float` | OHLC |
| `volume` | `float` | Volume in the bar |

## Indices

```python
live = iq.indices_live()
overall = iq.index_history()                       # شاخص کل
equal = iq.index_history("43685683301327984")      # شاخص هم‌وزن
```

Standardized columns:

| Column | Type | Description |
| --- | --- | --- |
| `index_code` | `str` | TSETMC index code |
| `name` | `str` | Index name |
| `index_value` | `float \| None` | Current value |
| `change` | `float \| None` | Absolute change |
| `change_pct` | `float \| None` | Percentage change |
| `date` / `jdate` | date/str or `None` | Reported date |
| `time` | `datetime.time \| None` | Last update |

Overall and equal-weight rows also expose `market_state` and
`market_state_title`. Sector rows retain every raw upstream field alongside the
standardized ones. Live snapshots run the overview and sector requests
concurrently; if only one succeeds, the partial result is returned.

## Error handling

```python
import iranquant as iq

try:
    iq.history("نماد_ناموجود")
except iq.SymbolNotFoundError:
    ...
except iq.NetworkError:
    ...
except iq.ParseError:
    ...
```

All exceptions inherit from `iranquant.IranQuantError`.
