# Quickstart

Get from zero to adjusted candles, Codal tables and live events in about five
minutes.

## 1. Install

```bash
pip install iranquant
```

For a development checkout with the docs toolchain:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,docs]"
```

## 2. Resolve a symbol and fetch history

=== "Synchronous"

    ```python
    import iranquant as iq

    code = iq.get_inscode("فولاد")
    print(code)  # "46348559193224090"

    frame = iq.history(code, adjust="all")
    print(frame.columns.tolist())
    # ['date', 'jdate', 'open', 'high', 'low', 'close',
    #  'final_price', 'prev_price', 'volume', 'value', 'trades']

    print(frame.tail(3)[["jdate", "close", "final_price", "volume"]])
    ```

=== "Asynchronous"

    ```python
    import asyncio
    import iranquant as iq

    async def main() -> None:
        async with iq.AsyncIranQuant(timeout=20.0, max_concurrency=8) as client:
            code = await client.resolve("فولاد")
            frame = await client.history(code, adjust="all")
            print(frame.tail(3)[["jdate", "close"]])

    asyncio.run(main())
    ```

The async client owns one pooled transport; reuse it for many symbols instead
of creating a new client per call.

## 3. Switch to Polars

Every function accepts `engine="polars"` and returns a `polars.DataFrame`:

```python
import iranquant as iq

frame = iq.full_history("فولاد", engine="polars")
print(frame.select(["jdate", "close", "buy_I_Volume"]).tail(3))
```

## 4. Join retail and institutional flows

```python
full = iq.full_history("فولاد")
print(full[["jdate", "buy_I_Volume", "sell_I_Volume",
            "buy_N_Volume", "sell_N_Volume"]].tail())
```

Columns prefixed with `I` are individual/retail (حقیقی) and `N` are
institutional/legal (حقوقی).

## 5. Add quant analytics

```python
import iranquant as iq

flow = iq.money_flow("فولاد")            # buyer power + smart money
power = iq.movement_power("فولاد")       # multi-timeframe score

print(flow[["jdate", "buyer_power_ratio",
            "smart_money_entry", "smart_money_exit"]].tail(5))
print(power[["jdate", "er_5", "er_20", "er_60",
             "movement_power_score"]].tail(5))
```

## 6. Read a Codal report

```python
import iranquant as iq

reports = iq.monthly_reports("فولاد")      # LetterType=58
letter = reports.Letters[0]
tables = iq.codal_report(letter)           # URLs resolved automatically

for name, table in tables.items():
    print(name, table.shape)               # "<sheet> - <table>"
```

## 7. Screen the live market

```python
import iranquant as iq

market = iq.market_watch()                 # one request for the whole market
screener = iq.MarketScreener(market)

buy_locked = screener.filter_buy_queue()
sell_locked = screener.filter_sell_queue()
surges = screener.filter_positive_tick_surge(min_diff_pct=1.5)

print(buy_locked[["symbol", "close", "tmax"]].head())
```

## 8. Watch transitions in real time

```python
import asyncio
from iranquant import MarketWatcher
from iranquant.stream import BUY_QUEUE_LOCKED, VOLUME_SPIKE

async def main() -> None:
    watcher = MarketWatcher()

    @watcher.on_event(BUY_QUEUE_LOCKED)
    def on_lock(event):
        print("buy queue locked:", event.symbol, event.data["buy_queue_volume"])

    @watcher.on_event(VOLUME_SPIKE)
    async def on_spike(event):
        print("volume spike:", event.symbol, event.data["delta"])

    async for event in watcher.watch(
        interval=3.0,
        symbols=["فولاد", "خودرو"],
        only_market_hours=True,          # Sat-Wed, 09:00-12:30 Tehran
    ):
        print(event.event_type, event.symbol)

asyncio.run(main())
```

## 9. Cache incrementally

```python
from iranquant import history
from iranquant.core.cache import ParquetCache

cache = ParquetCache()                   # ~/.cache/iranquant
combined = cache.update("fold-history", history("فولاد"))
print(combined.tail())
```

See [Caching & Polars](guide/caching-and-polars.md) for the full workflow.

## 10. Use the command line

Installing the package registers an `iranquant` command backed by Rich tables
and colour coding:

```bash
iranquant search فولاد
iranquant history فولاد --days 15 --adjust all
iranquant screener --mode buy-queue --limit 20
iranquant screener --mode tick-surge
iranquant indices
iranquant codal فولاد
iranquant codal فولاد --parse-latest
```

`codal --parse-latest` prints the report's tables and a monthly sales summary
in million Rials, including MoM and YoY growth.

---

## Proxy, VPN and split-tunneling in Iran

Iranian market hosts (`cdn.tsetmc.com`, `tsetmc.com`, `codal.ir`,
`search.codal.ir`) are domestic and are usually **fastest and most reliable
when they bypass your VPN**. `iranquant` is built around that reality.

!!! tip "Environment proxies are ignored by default"
    `AsyncHTTPClient` uses `trust_env=False` unless you pass `proxy=...`.
    This prevents `HTTP_PROXY`/`HTTPS_PROXY` exported by VPN or corporate
    clients from silently routing domestic `.ir` traffic through a remote HTTP
    port.

### Option A — explicit proxy (only when you need it)

```python
import iranquant as iq

async with iq.AsyncIranQuant(proxy="http://127.0.0.1:9090") as client:
    df = await client.history("فولاد")
```

### Option B — split tunneling (recommended for servers)

Configure your VPN client so that domestic destinations bypass the tunnel. For
example, with WireGuard `AllowedIPs = 0.0.0.0/0, ::/0` you can exclude routes
with `ip route add <domestic CIDR> via <local-gateway>`, or use your VPN app's
"split tunneling / bypass LAN and domestic sites" toggle. Add at minimum:

- `cdn.tsetmc.com`
- `tsetmc.com`
- `codal.ir` and `*.codal.ir`
- your DNS resolver, if it is queried over the tunnel

### Option C — run on an Iranian server

If your production host sits inside Iran, leave `proxy=None` and let the
library connect directly. TLS is verified first for both `*.tsetmc.com` and
`*.codal.ir`. On a certificate failure the request is retried once over an
unverified transport when `allow_tls_fallback=True`; the first fallback emits a
`UserWarning`. Set `IranQuantConfig(allow_tls_fallback=False)` or
`IRANQUANT_ALLOW_TLS_FALLBACK=false` to fail closed.

!!! warning "Market hours"
    TSETMC sessions run Saturday–Wednesday, 09:00–12:30 Tehran time.
    Use `only_market_hours=True` in `MarketWatcher.watch()` to avoid pointless
    polling outside those hours.
