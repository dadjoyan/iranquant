# Quant Analytics

All functions accept either a Pandas or Polars frame and return the **same
engine type**. They do not mutate their input.

```python
import iranquant as iq
from iranquant import (
    compute_money_flow,
    compute_movement_power,
    detect_swing_legs,
    MarketScreener,
)
```

## Conventions and limitations

### Units

TSETMC prices and traded values are in IRR (Rials); `volume` and the client-type `*_Volume` columns are share counts. Codal financial and monthly figures are in Million IRR unless the table header states otherwise. The `*_per_capita_m` columns divide Rial per-capita values by `1e7`, so they are Million Tomans.

### Calendars

Historical and client-type frames include Gregorian `date` and Jalali `jdate` (`YYYY-MM-DD`). Conversion uses `Asia/Tehran`; `date` carries no timezone.

### Price adjustment

`history(adjust=...)` compounds one ratio per bar backwards from the newest
bar (`F[n-1] = 1`, `F[i] = F[i+1] * r[i]`, `price[i] *= F[i]`). The nominal
ratios are:

| Adjustment | Ratio `r` |
| --- | --- |
| `all` | `pDrCotVal / pc` |
| `cash` | `(pc - dps) / pc` |
| `capital` | `numberOfShareOld / numberOfShareNew` |

`pDrCotVal` is the last traded price, `pc` the official closing price and `dps`
the dividend per share. The public `adjust` values are `all`, `capital_only`
and `none`. `all` uses the equivalent next-bar gap
`prev_price[t+1] / final_price[t]`; `capital_only` applies the capital ratio.

### Heuristic status

`smart_money_entry`, `smart_money_exit` and `movement_power_score` are
deterministic rule-based screening heuristics. Entry requires
`vol_ratio >= 1.8`, `buyer_power_ratio >= 1.5`, `net_real_value > 0` and
`close >= final_price`; exit mirrors those thresholds. The score is a bounded
efficiency-ratio aggregate. None of these outputs is a backtested alpha signal,
a forecast, or financial advice.

### Polling and TLS

`MarketWatcher` compares periodic HTTP MarketWatch snapshots; it is not a
WebSocket tick feed, and transitions between polls are not observable. TLS is
verified first for TSETMC and Codal. Pass
`IranQuantConfig(allow_tls_fallback=False)` to fail closed instead of using the
warned unverified fallback.

## Money flow and smart money

=== "One call"

    ```python
    flow = iq.money_flow("فولاد")          # fetches full_history + computes
    ```

=== "Lower level"

    ```python
    full = iq.full_history("فولاد")
    flow = compute_money_flow(full, vol_window=20)
    ```

### Required input columns

`compute_money_flow` expects `final_price`, `close`, `volume`,
`buy_I_Volume`, `sell_I_Volume`, `buy_I_Count` and `sell_I_Count` — all present
in `full_history`.

### Exact formulas

```text
real_buy_per_capita  = buy_I_Volume  * final_price / max(buy_I_Count, 1)
real_sell_per_capita = sell_I_Volume * final_price / max(sell_I_Count, 1)
buyer_power_ratio    = real_buy_per_capita / max(real_sell_per_capita, 1)

net_real_value       = (buy_I_Volume - sell_I_Volume) * final_price
ownership_change_pct = ((buy_I_Volume - sell_I_Volume) / max(volume, 1)) * 100
close_vs_final_pct   = ((close - final_price) / max(final_price, 1)) * 100
vol_ratio            = volume / rolling_mean(volume, vol_window)   # min_periods=1
```

`*_m` columns are the per-capita values expressed in **million Tomans**
(divided by `1e7`); the base `*_per_capita` columns are in Rials.

### Signals

```text
smart_money_entry = vol_ratio >= 1.8
                    and buyer_power_ratio >= 1.5
                    and net_real_value > 0
                    and close >= final_price

smart_money_exit  = vol_ratio >= 1.8
                    and buyer_power_ratio <= 0.67
                    and net_real_value < 0
                    and close <= final_price
```

### Output columns

| Column | Type | Meaning |
| --- | --- | --- |
| `real_buy_per_capita` | `float` | Rials per retail buy order |
| `real_buy_per_capita_m` | `float` | Million Tomans per retail buy order |
| `real_sell_per_capita` | `float` | Rials per retail sell order |
| `real_sell_per_capita_m` | `float` | Million Tomans per retail sell order |
| `buyer_power_ratio` | `float` | Real buy/sell power |
| `net_real_value` | `float` | Net real money flow (Rials) |
| `ownership_change_pct` | `float` | Net real volume / total volume |
| `close_vs_final_pct` | `float` | Last price vs closing price |
| `vol_ratio` | `float` | Volume vs rolling mean |
| `smart_money_entry` | `bool` | Accumulation signal |
| `smart_money_exit` | `bool` | Distribution signal |

```python
import polars as pl

flow = iq.money_flow("فولاد", engine="polars")
entries = flow.filter(pl.col("smart_money_entry"))
print(entries.select(["jdate", "buyer_power_ratio", "net_real_value"]))
```

## Multi-timeframe movement power

```python
power = iq.movement_power("فولاد", windows=(5, 20, 60))
# or
power = compute_movement_power(iq.full_history("فولاد"), windows=(5, 20, 60))
```

### Formula

For each window `w`:

```text
er_w = sign(close - close.shift(w))
       * abs(close - close.shift(w))
       / rolling_sum(abs(close.diff()), w)
```

With exactly three windows the weights are `(0.5, 0.3, 0.2)`; otherwise the
window ratios are equally weighted. The weighted score is modulated by:

```text
volume_factor = clip(volume / rolling_mean(volume, min(windows)), 0.5, 2.0)
buyer_factor  = clip(clip(buyer_power_ratio, 0.5, 3.0) / 1.5, 0.5, 1.5)

movement_power_score = clip(weighted_score * 100 * volume_factor * buyer_factor,
                            -100, +100)
```

`buyer_power_ratio` is optional; when absent the buyer factor is `1.0`. The
result is therefore negative for sustained down moves and positive for
sustained up moves, with more efficient (straighter) moves scoring higher.

### Output columns

- `er_<window>` — signed efficiency ratio per window (for example `er_5`,
  `er_20`, `er_60`).
- `movement_power_score` — final score in `[-100, +100]`.

```python
print(power[["jdate", "er_5", "er_20", "er_60",
             "movement_power_score"]].tail())
```

## ZigZag swing legs

```python
legs = detect_swing_legs(iq.history("فولاد"), reversal_pct=5.0)
```

A swing leg is confirmed when price reverses by at least `reversal_pct` from
the running extreme. The final, still-active leg is included, so the output
always ends at the latest bar.

### Output columns

| Column | Type | Meaning |
| --- | --- | --- |
| `leg_id` | `int` | One-based leg number |
| `direction` | `int` | `1` up, `-1` down |
| `start_date` / `end_date` | from `date` (or positional int) | Leg bounds |
| `start_price` / `end_price` | `float` | Pivot prices |
| `leg_return_pct` | `float` | Percentage move |
| `leg_bars` | `int` | Bars spanned |
| `leg_velocity` | `float` | `leg_return_pct / max(leg_bars, 1)` |
| `avg_volume` | `float` | Mean volume over the leg |
| `leg_volume_expansion` | `float` | Leg average / previous leg average |

`leg_volume_expansion` is `NaN` for the first leg and when the previous leg's
average volume is zero.

```python
fast_up_legs = legs[
    (legs["direction"] == 1)
    & (legs["leg_velocity"] > 0)
    & (legs["leg_volume_expansion"] > 1.5)
]
print(fast_up_legs)
```

## Market screener

```python
market = iq.market_watch()
screener = MarketScreener(market)       # or iq.IranQuant().screener(market)
```

| Filter | Criteria |
| --- | --- |
| `filter_buy_queue()` | `tmax > 0` and `close >= tmax` and buy queue volume (`sum(zmd)`) `> 0` |
| `filter_sell_queue()` | `tmin > 0` and `close <= tmin` and sell queue volume (`sum(zmo)`) `> 0` |
| `filter_positive_tick_surge(min_diff_pct=1.5)` | `(close - final_price) / max(final_price, 1) * 100 >= min_diff_pct` |
| `scan_market(predicate)` | Your own Pandas DataFrame/Series predicate |

```python
buy_locked = screener.filter_buy_queue()
sell_locked = screener.filter_sell_queue()
surges = screener.filter_positive_tick_surge(min_diff_pct=1.5)

high_volume = screener.scan_market(
    lambda df: df["volume"] > df["volume"].median()
)
```

Every filter returns the **same engine type** as the input. Each also accepts
an optional alternative frame as its first argument, so one screener can be
reused across several snapshots:

```python
screener.filter_buy_queue(other_snapshot)
```

## Errors

| Condition | Exception |
| --- | --- |
| `vol_window < 1` | `ValueError` |
| `reversal_pct <= 0` | `ValueError` |
| Empty/negative `windows` | `ValueError` |
| Required column missing | `KeyError` |
| Predicate returns neither DataFrame nor Series | `TypeError` |
