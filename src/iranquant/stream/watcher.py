"""Async market-wide polling streamer with state-transition events."""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from iranquant.core.client import AsyncHTTPClient
from iranquant.core.dataframe import DataFrame
from iranquant.core.normalizer import normalize_persian
from iranquant.exceptions import IranQuantError
from iranquant.market.live import get_market_watch
from iranquant.quant._utils import to_pandas

BUY_QUEUE_LOCKED = "BUY_QUEUE_LOCKED"
BUY_QUEUE_BROKEN = "BUY_QUEUE_BROKEN"
SELL_QUEUE_LOCKED = "SELL_QUEUE_LOCKED"
SELL_QUEUE_BROKEN = "SELL_QUEUE_BROKEN"
VOLUME_SPIKE = "VOLUME_SPIKE"

EventCallback = Callable[["MarketEvent"], Awaitable[None] | None]


@dataclass(slots=True)
class MarketEvent:
    """A structured market state-change event.

    Attributes:
        event_type: One of ``BUY_QUEUE_LOCKED``, ``BUY_QUEUE_BROKEN``,
            ``SELL_QUEUE_LOCKED``, ``SELL_QUEUE_BROKEN`` or ``VOLUME_SPIKE``.
        ins_code: TSETMC instrument code (empty when the snapshot lacks it).
        symbol: Persian ticker from MarketWatch.
        timestamp: When the event was detected (UTC unless supplied).
        data: Event-specific payload.  Queue events include ``close``,
            ``tmax``, ``tmin``, ``buy_queue_volume``, ``sell_queue_volume`` and
            ``volume``; ``VOLUME_SPIKE`` includes ``volume``, ``delta``,
            ``avg_delta`` and ``close``.

    Examples:
        >>> event = MarketEvent(
        ...     event_type=BUY_QUEUE_LOCKED,
        ...     ins_code="46348559193224090",
        ...     symbol="فولاد",
        ...     timestamp=dt.datetime.now(dt.timezone.utc),
        ...     data={"close": 4150.0, "tmax": 4150.0},
        ... )
        >>> event.symbol
        'فولاد'
    """

    event_type: str
    ins_code: str
    symbol: str
    timestamp: dt.datetime
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class _SymbolState:
    buy_locked: bool = False
    sell_locked: bool = False
    last_volume: float | None = None
    avg_delta: float = 0.0
    delta_count: int = 0
    last_close: float = 0.0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _book_volume(order_book: Any, side: str) -> float:
    if order_book is None or isinstance(order_book, str):
        return 0.0
    levels = [order_book] if isinstance(order_book, dict) else list(order_book)
    key = "zmd" if side == "buy" else "zmo"
    total = 0.0
    for level in levels:
        if isinstance(level, dict):
            total += _to_float(level.get(key, 0.0))
        else:
            total += _to_float(getattr(level, key, 0.0))
    return total


def _tehran_timezone() -> dt.tzinfo:
    try:
        return ZoneInfo("Asia/Tehran")
    except ZoneInfoNotFoundError:  # pragma: no cover - tzdata normally present
        return dt.timezone(dt.timedelta(hours=3, minutes=30), "IRST")


def is_market_open(now: dt.datetime | None = None) -> bool:
    """Return True during Saturday-Wednesday TSETMC sessions (09:00-12:30 IRST).

    Python weekday numbering is Monday=0, so the active days are 5, 6, 0, 1
    and 2.  The time is interpreted in ``Asia/Tehran`` (with a fixed +03:30
    fallback when tzdata is unavailable).

    Args:
        now: Optional aware or naive datetime.  Naive values are assumed to be
            Tehran local time.  Defaults to the current instant.

    Returns:
        ``True`` when the current weekday is Saturday to Wednesday and the
        local time is between 09:00 and 12:30 inclusive, otherwise ``False``.

    Examples:
        >>> import datetime as dt
        >>> is_market_open(dt.datetime(2026, 10, 31, 10, 0))  # Saturday
        True
        >>> is_market_open(dt.datetime(2026, 10, 30, 13, 0))  # Friday
        False
    """

    timezone = _tehran_timezone()
    current = now or dt.datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    current = current.astimezone(timezone)
    if current.weekday() not in {5, 6, 0, 1, 2}:
        return False
    current_time = current.time()
    return dt.time(9, 0) <= current_time <= dt.time(12, 30)


class MarketWatcher:
    """Poll MarketWatch and emit queue/volume transitions.

    ``process_snapshot`` is synchronous and stateful so it can also be used in
    backtests or tests without any network access.  The state machine emits:

    * ``BUY_QUEUE_LOCKED`` / ``BUY_QUEUE_BROKEN`` when ``close >= tmax`` with a
      positive buy queue starts or stops.
    * ``SELL_QUEUE_LOCKED`` / ``SELL_QUEUE_BROKEN`` when ``close <= tmin`` with
      a positive sell queue starts or stops.
    * ``VOLUME_SPIKE`` when the per-poll volume increase is at least ``1.8x``
      the running average increase.

    Args:
        client: Optional shared :class:`~iranquant.core.client.AsyncHTTPClient`.
            When omitted, :meth:`watch` creates and owns a client itself, or
            :meth:`__aenter__` creates one for the context manager.

    Examples:
        >>> watcher = MarketWatcher()
        >>> @watcher.on_event(BUY_QUEUE_LOCKED)
        ... def on_lock(event: MarketEvent) -> None:
        ...     print(event.symbol, event.data["buy_queue_volume"])
        >>> async for event in watcher.watch(interval=5.0, symbols=["فولاد"]):
        ...     print(event.event_type)
    """

    def __init__(self, client: AsyncHTTPClient | None = None) -> None:
        self._client = client
        self._owns_client = False
        self._states: dict[str, _SymbolState] = {}
        self._listeners: dict[str, list[EventCallback]] = {}

    def on_event(
        self,
        event_type: str,
    ) -> Callable[[EventCallback], EventCallback]:
        """Register a sync or async callback for an event type (``"*"`` = all).

        Args:
            event_type: A specific event constant or ``"*"`` to receive every
                event.

        Returns:
            A decorator that registers the callback and returns it unchanged,
            so the method can be used as ``@watcher.on_event(...)``.

        Examples:
            >>> @watcher.on_event("*")
            ... async def log(event: MarketEvent) -> None:
            ...     print(event.event_type, event.symbol)
        """

        def decorator(callback: EventCallback) -> EventCallback:
            self._listeners.setdefault(event_type, []).append(callback)
            return callback

        return decorator

    async def dispatch(self, events: Iterable[MarketEvent]) -> None:
        """Invoke every matching listener for each event.

        Sync callbacks are called directly; awaitable results are awaited.
        Listeners registered for ``"*"`` run after type-specific listeners.

        Args:
            events: Events to deliver, typically returned by
                :meth:`process_snapshot`.

        Examples:
            >>> await watcher.dispatch(watcher.process_snapshot(snapshot))
        """
        for event in events:
            callbacks = [
                *self._listeners.get(event.event_type, []),
                *self._listeners.get("*", []),
            ]
            for callback in callbacks:
                result = callback(event)
                if inspect.isawaitable(result):
                    await cast(Awaitable[Any], result)

    def reset(self) -> None:
        """Forget all per-symbol state so the next snapshot starts fresh.

        Does not remove registered listeners.
        """
        self._states.clear()

    def process_snapshot(
        self,
        snapshot: DataFrame,
        *,
        timestamp: dt.datetime | None = None,
        symbols: list[str] | None = None,
    ) -> list[MarketEvent]:
        """Update internal state from a MarketWatch frame and return events.

        No I/O is performed; the result is deterministic for a given snapshot
        sequence, so this is safe to use in backtests and unit tests.

        Args:
            snapshot: A MarketWatch frame (from
                :func:`iranquant.market_watch`) with at least ``close``,
                ``tmax``, ``tmin`` and ``volume``.  ``ins_code``, ``symbol``
                and ``order_book`` are used when present.
            timestamp: Timestamp attached to emitted events.  Defaults to the
                current UTC time.
            symbols: Optional allow-list.  Numeric strings are matched against
                ``ins_code``; other strings are normalized and matched against
                ``symbol``.

        Returns:
            The newly triggered events in snapshot order.  An empty frame
            returns an empty list without mutating state.

        Examples:
            >>> events = watcher.process_snapshot(iq.market_watch())
            >>> [event.event_type for event in events]
            ['BUY_QUEUE_LOCKED']
        """

        frame = to_pandas(snapshot)
        if frame.empty:
            return []

        wanted_codes = {str(symbol) for symbol in symbols or [] if str(symbol).isdigit()}
        wanted_symbols = {
            normalize_persian(symbol) for symbol in symbols or [] if not str(symbol).isdigit()
        }
        moment = timestamp or dt.datetime.now(dt.timezone.utc)

        events: list[MarketEvent] = []
        for _, row in frame.iterrows():
            ins_code = str(row.get("ins_code", "") or "")
            symbol = str(row.get("symbol", "") or "")
            if symbols is not None:
                matches = ins_code in wanted_codes or normalize_persian(symbol) in wanted_symbols
                if not matches:
                    continue

            key = ins_code or symbol
            if not key:
                continue

            close = _to_float(row.get("close"))
            tmax = _to_float(row.get("tmax"))
            tmin = _to_float(row.get("tmin"))
            volume = _to_float(row.get("volume"))
            buy_volume = _book_volume(row.get("order_book"), "buy")
            sell_volume = _book_volume(row.get("order_book"), "sell")

            buy_locked = tmax > 0 and close >= tmax and buy_volume > 0
            sell_locked = tmin > 0 and close <= tmin and sell_volume > 0

            state = self._states.get(key)
            is_new = state is None
            if state is None:
                state = _SymbolState()
                self._states[key] = state

            base_data: dict[str, Any] = {
                "close": close,
                "tmax": tmax,
                "tmin": tmin,
                "buy_queue_volume": buy_volume,
                "sell_queue_volume": sell_volume,
                "volume": volume,
            }

            if buy_locked and not state.buy_locked:
                events.append(
                    MarketEvent(BUY_QUEUE_LOCKED, ins_code, symbol, moment, dict(base_data))
                )
            elif state.buy_locked and not buy_locked:
                events.append(
                    MarketEvent(BUY_QUEUE_BROKEN, ins_code, symbol, moment, dict(base_data))
                )

            if sell_locked and not state.sell_locked:
                events.append(
                    MarketEvent(SELL_QUEUE_LOCKED, ins_code, symbol, moment, dict(base_data))
                )
            elif state.sell_locked and not sell_locked:
                events.append(
                    MarketEvent(SELL_QUEUE_BROKEN, ins_code, symbol, moment, dict(base_data))
                )

            if not is_new and state.last_volume is not None:
                delta = volume - state.last_volume
                if delta < 0:
                    # A new trading day/reset: restart the spike baseline.
                    state.avg_delta = 0.0
                    state.delta_count = 0
                elif delta > 0:
                    previous_average = state.avg_delta
                    if state.delta_count > 0 and delta >= 1.8 * previous_average:
                        events.append(
                            MarketEvent(
                                VOLUME_SPIKE,
                                ins_code,
                                symbol,
                                moment,
                                {
                                    "volume": volume,
                                    "delta": delta,
                                    "avg_delta": previous_average,
                                    "close": close,
                                },
                            )
                        )
                    state.delta_count += 1
                    state.avg_delta = (
                        (previous_average * (state.delta_count - 1)) + delta
                    ) / state.delta_count

            state.buy_locked = buy_locked
            state.sell_locked = sell_locked
            state.last_volume = volume
            state.last_close = close

        return events

    async def watch(
        self,
        interval: float = 5.0,
        symbols: list[str] | None = None,
        only_market_hours: bool = False,
    ) -> AsyncGenerator[MarketEvent, None]:
        """Poll MarketWatch forever, yielding state-change events.

        Each iteration fetches a full market snapshot, updates the state
        machine, dispatches callbacks, yields every new event, then sleeps.
        IranQuant errors during a poll are swallowed and retried on the next
        interval.

        Args:
            interval: Seconds between full MarketWatch polls.  Must be positive.
                Defaults to ``5.0``.
            symbols: Optional allow-list passed to :meth:`process_snapshot`.
            only_market_hours: When ``True``, skip polling outside
                Saturday-Wednesday 09:00-12:30 Tehran time.  Defaults to
                ``False``.

        Yields:
            :class:`MarketEvent` objects as state transitions occur.

        Raises:
            ValueError: If ``interval <= 0``.

        Examples:
            >>> async for event in watcher.watch(interval=3.0, symbols=["فولاد"]):
            ...     print(event.event_type, event.symbol)
        """

        if interval <= 0:
            raise ValueError("interval must be positive")

        owns_client = self._client is None
        if self._client is None:
            self._client = AsyncHTTPClient()
        client = self._client

        try:
            while True:
                if only_market_hours and not is_market_open():
                    await asyncio.sleep(interval)
                    continue
                try:
                    snapshot = await get_market_watch(client)
                except IranQuantError:
                    await asyncio.sleep(interval)
                    continue

                events = self.process_snapshot(snapshot, symbols=symbols)
                await self.dispatch(events)
                for event in events:
                    yield event
                await asyncio.sleep(interval)
        finally:
            if owns_client:
                await client.aclose()
                self._client = None

    async def __aenter__(self) -> MarketWatcher:
        """Enter the async context manager, creating a client if needed.

        Returns:
            ``self`` so the watcher can be used with ``async with``.
        """
        if self._client is None:
            self._client = AsyncHTTPClient()
            self._owns_client = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the owned HTTP client when leaving the context manager."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False


__all__ = [
    "BUY_QUEUE_BROKEN",
    "BUY_QUEUE_LOCKED",
    "MarketEvent",
    "MarketWatcher",
    "SELL_QUEUE_BROKEN",
    "SELL_QUEUE_LOCKED",
    "VOLUME_SPIKE",
    "is_market_open",
]
