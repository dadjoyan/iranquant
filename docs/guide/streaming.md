# Streaming

`MarketWatcher` polls the full MarketWatch snapshot on an interval and emits
structured events when a symbol's state changes. It is deliberately simple:
one request per poll, a synchronous state machine, and no hidden background
tasks.

```python
import asyncio
from iranquant import MarketWatcher
from iranquant.stream import (
    BUY_QUEUE_LOCKED,
    BUY_QUEUE_BROKEN,
    SELL_QUEUE_LOCKED,
    SELL_QUEUE_BROKEN,
    VOLUME_SPIKE,
)

async def main() -> None:
    watcher = MarketWatcher()

    @watcher.on_event(BUY_QUEUE_LOCKED)
    def on_buy_lock(event):
        print("buy queue locked:", event.symbol, event.data["buy_queue_volume"])

    @watcher.on_event(VOLUME_SPIKE)
    async def on_spike(event):
        print("spike:", event.symbol, event.data["delta"])

    @watcher.on_event("*")            # wildcard: every event
    def log(event):
        print(event.event_type, event.symbol)

    async for event in watcher.watch(
        interval=3.0,
        symbols=["فولاد", "خودرو"],
        only_market_hours=True,
    ):
        print("yielded:", event.event_type, event.symbol)

asyncio.run(main())
```

## Events

| Constant | Trigger | Reset when |
| --- | --- | --- |
| `BUY_QUEUE_LOCKED` | `close >= tmax` **and** buy queue volume `> 0` | — |
| `BUY_QUEUE_BROKEN` | A previously locked buy queue disappears | After a `BUY_QUEUE_LOCKED` |
| `SELL_QUEUE_LOCKED` | `close <= tmin` **and** sell queue volume `> 0` | — |
| `SELL_QUEUE_BROKEN` | A previously locked sell queue disappears | After a `SELL_QUEUE_LOCKED` |
| `VOLUME_SPIKE` | Per-poll volume increase `>= 1.8x` the running average increase | — |

Queue volume is the sum of `zmd` (bid) or `zmo` (ask) across all order-book
levels.

## `MarketEvent`

| Attribute | Type | Description |
| --- | --- | --- |
| `event_type` | `str` | One of the constants above |
| `ins_code` | `str` | TSETMC instrument code |
| `symbol` | `str` | Persian ticker |
| `timestamp` | `datetime.datetime` | Detection time (UTC unless supplied) |
| `data` | `dict` | Event-specific payload |

Queue events carry `close`, `tmax`, `tmin`, `buy_queue_volume`,
`sell_queue_volume` and `volume`. `VOLUME_SPIKE` carries `volume`, `delta`,
`avg_delta` and `close`.

## Callbacks

Register callbacks with the `on_event` decorator. Callbacks may be plain
functions or `async def`; awaitable results are awaited automatically.
Type-specific listeners run before wildcard (`"*"`) listeners.

```python
@watcher.on_event(VOLUME_SPIKE)
async def on_spike(event):
    await notify(event.symbol, event.data["delta"])
```

You can also dispatch manually:

```python
events = watcher.process_snapshot(snapshot, symbols=["فولاد"])
await watcher.dispatch(events)
```

## Backtesting without the network

`process_snapshot` is synchronous and stateful, so you can feed it historical
snapshots to reproduce events deterministically:

```python
watcher = MarketWatcher()

for snapshot in historical_snapshots:          # each a MarketWatch-style frame
    events = watcher.process_snapshot(snapshot)
    for event in events:
        print(event.timestamp, event.event_type, event.symbol)

watcher.reset()                                # start a new session
```

`reset()` forgets per-symbol state but keeps registered listeners.

## Using a shared client

`MarketWatcher` accepts an existing `AsyncHTTPClient`, which it will **not**
close. This is what `AsyncIranQuant.watch()` does:

```python
import asyncio
import iranquant as iq

async def main() -> None:
    async with iq.AsyncIranQuant() as client:
        async for event in client.watch(interval=5.0, symbols=["فولاد"]):
            print(event.event_type, event.symbol)

asyncio.run(main())
```

## Market hours

`is_market_open()` returns `True` on Saturday–Wednesday, 09:00–12:30 Tehran
time (inclusive), using `Asia/Tehran` with a fixed +03:30 fallback when tzdata
is unavailable.

```python
from iranquant import is_market_open

if not is_market_open():
    print("market closed")
```

With `only_market_hours=True`, the watcher sleeps through closed periods
instead of polling.

## Error behavior

- Network and parse errors during a poll are swallowed; the watcher sleeps and
  retries on the next interval.
- `interval <= 0` raises `ValueError` when iteration starts.
- Cancelling the async iterator closes a watcher-owned client automatically.
