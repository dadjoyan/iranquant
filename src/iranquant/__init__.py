"""iranquant — async-first TSETMC / Codal market data client.

Synchronous module-level helpers are the easiest entry point::

    import iranquant as iq
    df = iq.history("فولاد")

Inside an async application use :class:`AsyncIranQuant`::

    async with AsyncIranQuant() as iq:
        df = await iq.history("فولاد")
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

from iranquant.codal import (
    analyze_monthly_sales,
    fetch_and_parse_report,
    get_financial_statements,
    get_monthly_reports,
    search_codal,
)
from iranquant.codal import (
    monthly_sales_summary as _monthly_sales_summary,
)
from iranquant.config import DEFAULT_TIMEOUT, MAX_CONCURRENCY, OVERALL_INDEX_CODE
from iranquant.core.client import AsyncHTTPClient
from iranquant.core.dataframe import DataFrame, Engine, to_dataframe
from iranquant.core.normalizer import normalize_persian
from iranquant.core.sync import run_sync
from iranquant.exceptions import (
    IranQuantError,
    NetworkError,
    ParseError,
    SymbolNotFoundError,
)
from iranquant.market import history as _history
from iranquant.market import index as _index
from iranquant.market import live as _live
from iranquant.market import symbol as _symbol
from iranquant.market.history import AdjustMode
from iranquant.models.codal import CodalLetter, CodalSearchResponse
from iranquant.quant import (
    MarketScreener,
    compute_money_flow,
    compute_movement_power,
    detect_swing_legs,
)
from iranquant.stream import MarketEvent, MarketWatcher, is_market_open

__version__ = "0.1.0"

T = TypeVar("T")


def _as_ins_code(value: str | int) -> str | None:
    if isinstance(value, int):
        return str(value)
    text = normalize_persian(str(value))
    return text if text.isdigit() else None


class AsyncIranQuant:
    """Async client owning a single pooled HTTP transport.

    Use this class when your application already runs an event loop.  It keeps
    one :class:`~iranquant.core.client.AsyncHTTPClient` alive for all requests,
    which reuses connections and applies a shared concurrency limit.

    Every method is a thin async wrapper around the corresponding function in
    :mod:`iranquant.market`, :mod:`iranquant.codal` or :mod:`iranquant.quant`.
    Results default to Pandas; pass ``engine="polars"`` for Polars.

    Examples:
        >>> import iranquant as iq
        >>> async with iq.AsyncIranQuant(timeout=15.0) as client:
        ...     frame = await client.history("فولاد")
        ...     market = await client.market_watch()
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_concurrency: int = MAX_CONCURRENCY,
        proxy: str | None = None,
    ) -> None:
        """Create the async client.

        Args:
            timeout: Per-request timeout in seconds.  Defaults to
                :data:`iranquant.config.DEFAULT_TIMEOUT` (30 s).
            max_concurrency: Maximum simultaneous requests.  Defaults to
                :data:`iranquant.config.MAX_CONCURRENCY` (10).
            proxy: Optional explicit proxy URL.  Environment proxies are
                ignored unless this is supplied.

        Examples:
            >>> client = AsyncIranQuant(proxy="http://127.0.0.1:9090")
        """
        self._client = AsyncHTTPClient(
            timeout=timeout,
            max_concurrency=max_concurrency,
            proxy=proxy,
        )

    @property
    def client(self) -> AsyncHTTPClient:
        """The underlying :class:`~iranquant.core.client.AsyncHTTPClient`."""
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP transports.

        Called automatically when used as an async context manager.
        """
        await self._client.aclose()

    async def __aenter__(self) -> AsyncIranQuant:
        """Return ``self`` for use with ``async with``."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the HTTP transports on context exit."""
        await self.close()

    async def resolve(self, symbol_or_inscode: str | int) -> str:
        """Resolve a symbol name or numeric instrument code to an ``insCode``.

        Numeric input (``int`` or an all-digit string, including Persian digits)
        is returned unchanged without any network request.

        Args:
            symbol_or_inscode: Persian ticker (``"فولاد"``) or a numeric
                instrument code (``46348559193224090``).

        Returns:
            The numeric ``insCode`` as a string.

        Raises:
            SymbolNotFoundError: If a symbol name cannot be matched.
            NetworkError: If the lookup request fails.
            ParseError: If the search response is malformed.

        Examples:
            >>> await client.resolve("فولاد")
            '46348559193224090'
            >>> await client.resolve(46348559193224090)
            '46348559193224090'
        """
        code = _as_ins_code(symbol_or_inscode)
        if code is not None:
            return code
        return await _symbol.get_inscode(self._client, str(symbol_or_inscode))

    async def search(self, query: str, *, engine: Engine = "pandas") -> DataFrame:
        """Search TSETMC instruments by ticker or company name.

        Args:
            query: Symbol or company-name fragment, e.g. ``"فولاد"``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            One row per match with ``ins_code``, ``symbol``, ``company_name``,
            ``isin``, ``shares``, ``flow``, ``board``, ``last_date`` and
            ``last_date_jdate``.

        Raises:
            NetworkError: If TSETMC cannot be reached.
            ParseError: If the response is malformed.

        Examples:
            >>> results = await client.search("فولاد")
            >>> results.iloc[0]["symbol"]
            'فولاد'
        """
        items = await _symbol.search_symbol(self._client, query)
        return to_dataframe(
            [_symbol.instrument_to_dict(item) for item in items],
            engine,
        )

    async def history(
        self,
        symbol_or_inscode: str | int,
        *,
        adjust: AdjustMode = "all",
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch backward-adjusted daily OHLCV history.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            adjust: ``"none"``, ``"all"`` (default) or ``"capital_only"``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Columns ``date`` (``datetime.date``), ``jdate`` (``str``),
            ``open``, ``high``, ``low``, ``close``, ``final_price``,
            ``prev_price``, ``volume``, ``value`` and ``trades`` (all
            ``float``), sorted by date.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If TSETMC cannot be reached.
            ParseError: If the payload is malformed.
            ValueError: If ``adjust`` or ``engine`` is unsupported.

        Examples:
            >>> frame = await client.history("فولاد", adjust="all")
            >>> frame[["jdate", "close", "final_price"]].tail()
        """
        ins_code = await self.resolve(symbol_or_inscode)
        return await _history.get_daily_history(
            self._client,
            ins_code,
            adjust=adjust,
            engine=engine,
        )

    async def full_history(
        self,
        symbol_or_inscode: str | int,
        *,
        adjust: AdjustMode = "all",
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch daily OHLCV merged with retail/institutional client-type data.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            adjust: ``"none"``, ``"all"`` (default) or ``"capital_only"``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            The eleven :meth:`history` columns plus ``buy_I_Volume``,
            ``buy_N_Volume``, ``buy_I_Value``, ``buy_N_Value``,
            ``buy_N_Count``, ``buy_I_Count``, ``sell_I_Volume``,
            ``sell_N_Volume``, ``sell_I_Value``, ``sell_N_Value``,
            ``sell_N_Count`` and ``sell_I_Count``, outer-joined on ``date``.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If either request fails.
            ParseError: If either payload is malformed.

        Examples:
            >>> frame = await client.full_history("فولاد")
            >>> frame[["jdate", "buy_I_Volume", "sell_N_Count"]].tail()
        """
        ins_code = await self.resolve(symbol_or_inscode)
        return await _history.get_full_history(
            self._client,
            ins_code,
            adjust=adjust,
            engine=engine,
        )

    async def client_type(
        self,
        symbol_or_inscode: str | int,
        *,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch retail/institutional (حقیقی/حقوقی) history only.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Columns ``date``, ``jdate``, ``buy_I_Volume``, ``buy_N_Volume``,
            ``buy_I_Value``, ``buy_N_Value``, ``buy_N_Count``, ``buy_I_Count``,
            ``sell_I_Volume``, ``sell_N_Volume``, ``sell_I_Value``,
            ``sell_N_Value``, ``sell_N_Count`` and ``sell_I_Count``.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If the request fails.
            ParseError: If the payload is malformed.

        Examples:
            >>> frame = await client.client_type("فولاد")
            >>> frame[["date", "buy_I_Volume", "sell_I_Volume"]].tail()
        """
        ins_code = await self.resolve(symbol_or_inscode)
        return await _history.get_client_type_history(
            self._client,
            ins_code,
            engine=engine,
        )

    async def market_watch(self, *, engine: Engine = "pandas") -> DataFrame:
        """Fetch the live market-wide snapshot in one request.

        Args:
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            One row per symbol with ``ins_code``, ``symbol``, ``company_name``,
            ``eps``, ``pe``, ``open``, ``high``, ``low``, ``close``,
            ``final_price``, ``prev_price``, ``volume``, ``value``, ``trades``,
            ``tmax``, ``tmin``, ``total_shares`` and ``order_book``.

        Raises:
            NetworkError: If the MarketWatch endpoint cannot be reached.
            ParseError: If the response body is not valid JSON.
            pydantic.ValidationError: If the payload does not match the schema.

        Examples:
            >>> market = await client.market_watch()
            >>> market[["symbol", "close", "tmax"]].head()
        """
        return await _live.get_market_watch(self._client, engine=engine)

    async def order_book(
        self,
        symbol_or_inscode: str | int,
        *,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch the best-limit order book (levels 1..5).

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Columns ``n``, ``qmd``, ``zmd``, ``pmd``, ``pmo``, ``zmo`` and
            ``qmo`` sorted by level.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If the request fails.
            ParseError: If the response body is not valid JSON.

        Examples:
            >>> book = await client.order_book("فولاد")
            >>> book[["pmd", "zmd", "pmo", "zmo"]].head()
        """
        ins_code = await self.resolve(symbol_or_inscode)
        return await _live.get_order_book(self._client, ins_code, engine=engine)

    async def intraday_trades(
        self,
        symbol_or_inscode: str | int,
        *,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch intraday OHLCV bars for the current/last session.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Columns ``time`` (``datetime.time | None``), ``open``, ``high``,
            ``low``, ``close`` and ``volume``.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If the request fails.
            ParseError: If the response body is not valid JSON.

        Examples:
            >>> bars = await client.intraday_trades("فولاد")
            >>> bars[["time", "close", "volume"]].tail()
        """
        ins_code = await self.resolve(symbol_or_inscode)
        return await _live.get_intraday_trades(self._client, ins_code, engine=engine)

    async def indices_live(self, *, engine: Engine = "pandas") -> DataFrame:
        """Fetch live overall, equal-weight and sector index snapshots.

        Args:
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Standardized columns ``index_code``, ``name``, ``index_value``,
            ``change``, ``change_pct``, ``date``, ``jdate`` and ``time``; the
            overall/equal-weight rows also carry ``market_state`` and
            ``market_state_title``.

        Raises:
            NetworkError: If no index data can be fetched.

        Examples:
            >>> indices = await client.indices_live()
            >>> indices[["name", "index_value", "change_pct"]].head()
        """
        return await _index.get_indices_live(self._client, engine=engine)

    async def index_history(
        self,
        index_code: str = OVERALL_INDEX_CODE,
        *,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch historical daily values for an index code.

        Args:
            index_code: TSETMC index code.  Defaults to the overall index
                (``OVERALL_INDEX_CODE``).
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Standardized index columns plus the raw upstream fields, sorted by
            ``date``.

        Raises:
            NetworkError: If both the B2 and B1 endpoints fail.
            ParseError: If a body is not valid JSON.

        Examples:
            >>> history = await client.index_history()
            >>> history[["date", "index_value"]].tail()
        """
        return await _index.get_index_history(
            self._client,
            index_code,
            engine=engine,
        )

    async def codal_search(
        self,
        symbol: str | None = None,
        letter_type: int = -1,
        category: int = -1,
        page: int = 1,
        audited: bool = True,
        not_audited: bool = True,
        mains: bool = True,
        childs: bool = True,
    ) -> CodalSearchResponse:
        """Search Codal announcements using this client's transport.

        Args:
            symbol: Optional Persian ticker filter.
            letter_type: Codal ``LetterType`` (``-1`` for all, ``58`` monthly
                activity, ``6`` interim financials).  Defaults to ``-1``.
            category: Codal ``Category`` filter.  Defaults to ``-1``.
            page: One-based page.  Defaults to ``1``.
            audited: Include audited reports.  Defaults to ``True``.
            not_audited: Include unaudited reports.  Defaults to ``True``.
            mains: Include parent-company reports.  Defaults to ``True``.
            childs: Include subsidiary reports.  Defaults to ``True``.

        Returns:
            A :class:`~iranquant.models.codal.CodalSearchResponse` whose
            letters have absolute URLs.

        Raises:
            NetworkError: If Codal cannot be reached.
            ParseError: If the response is malformed.

        Examples:
            >>> reports = await client.codal_search("فولاد", letter_type=58)
            >>> reports.Total
        """
        return await search_codal(
            symbol=symbol,
            letter_type=letter_type,
            category=category,
            page=page,
            audited=audited,
            not_audited=not_audited,
            mains=mains,
            childs=childs,
            client=self._client,
        )

    async def monthly_reports(
        self,
        symbol: str,
        page: int = 1,
    ) -> CodalSearchResponse:
        """Fetch monthly activity reports (``LetterType=58``).

        Args:
            symbol: Persian ticker, e.g. ``"فولاد"``.
            page: One-based page.  Defaults to ``1``.

        Returns:
            A :class:`~iranquant.models.codal.CodalSearchResponse`.

        Raises:
            NetworkError: If Codal cannot be reached.
            ParseError: If the response is malformed.

        Examples:
            >>> reports = await client.monthly_reports("فولاد")
            >>> tables = await client.codal_report(reports.Letters[0])
        """
        return await get_monthly_reports(symbol, page=page, client=self._client)

    async def financial_statements(
        self,
        symbol: str,
        page: int = 1,
    ) -> CodalSearchResponse:
        """Fetch interim financial statements (``LetterType=6``).

        Args:
            symbol: Persian ticker, e.g. ``"فولاد"``.
            page: One-based page.  Defaults to ``1``.

        Returns:
            A :class:`~iranquant.models.codal.CodalSearchResponse`.

        Raises:
            NetworkError: If Codal cannot be reached.
            ParseError: If the response is malformed.

        Examples:
            >>> statements = await client.financial_statements("فولاد")
        """
        return await get_financial_statements(symbol, page=page, client=self._client)

    async def codal_report(
        self,
        url_or_letter: str | CodalLetter,
        *,
        engine: Engine = "pandas",
    ) -> dict[str, Any]:
        """Download and parse all tables of a Codal report.

        Args:
            url_or_letter: Report URL or a ``CodalLetter`` with ``Url``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            A mapping of ``"<sheet title> - <table title>"`` to DataFrames.

        Raises:
            NetworkError: If the report page cannot be downloaded.
            ParseError: If the page has no valid datasource JSON.
            ValueError: If ``engine`` is unsupported.

        Examples:
            >>> tables = await client.codal_report("/Report/123")
            >>> for name, table in tables.items():
            ...     print(name, table.shape)
        """
        return await fetch_and_parse_report(
            url_or_letter,
            engine=engine,
            client=self._client,
        )

    async def parse_codal_report(
        self,
        url_or_letter: str | CodalLetter,
        *,
        engine: Engine = "pandas",
    ) -> dict[str, Any]:
        """Download and parse a Codal report using this client's transport.

        This is the async counterpart of the top-level
        :func:`parse_codal_report` and an alias for :meth:`codal_report`.

        Args:
            url_or_letter: Report URL or a ``CodalLetter`` with ``Url``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            A mapping of ``"<sheet title> - <table title>"`` to DataFrames.

        Raises:
            NetworkError: If the report page cannot be downloaded.
            ParseError: If the page has no valid datasource JSON.
            ValueError: If ``engine`` is unsupported.

        Examples:
            >>> tables = await client.parse_codal_report("/Report/123")
            >>> for name, table in tables.items():
            ...     print(name, table.shape)
        """
        return await fetch_and_parse_report(
            url_or_letter,
            engine=engine,
            client=self._client,
        )

    async def monthly_sales_summary(
        self,
        symbol: str,
        page: int = 1,
    ) -> dict[str, Any]:
        """Analyze a symbol's latest monthly sales report.

        Parses the newest ``LetterType=58`` report and compares its current
        month with the previous distinct monthly period.

        Args:
            symbol: Persian ticker, e.g. ``"فولاد"``.
            page: One-based Codal result page.  Defaults to ``1``.

        Returns:
            The dictionary produced by
            :func:`~iranquant.codal.monthly.analyze_monthly_sales` plus
            ``symbol``, ``tracing_no``, ``title``, ``prev_month_tracing_no``
            and a ``mom_change_pct`` computed from the previous report.

        Raises:
            ParseError: If no HTML monthly report is available.
            NetworkError: If Codal cannot be reached.
            ValueError: If the report has no usable tables.

        Examples:
            >>> summary = await client.monthly_sales_summary("فولاد")
            >>> summary["current_month_sales"]
            >>> summary["summary_df"].head()
        """
        return await _monthly_sales_summary(
            symbol,
            page=page,
            client=self._client,
        )

    async def money_flow(
        self,
        symbol_or_inscode: str | int,
        *,
        adjust: AdjustMode = "all",
        vol_window: int = 20,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch full history and compute money-flow/smart-money analytics.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            adjust: Price adjustment mode.  Defaults to ``"all"``.
            vol_window: Rolling volume window.  Defaults to ``20``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            The full-history columns plus ``real_buy_per_capita``,
            ``real_buy_per_capita_m``, ``real_sell_per_capita``,
            ``real_sell_per_capita_m``, ``buyer_power_ratio``,
            ``net_real_value``, ``ownership_change_pct``, ``close_vs_final_pct``,
            ``vol_ratio``, ``smart_money_entry`` and ``smart_money_exit``.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If a request fails.
            ParseError: If a payload is malformed.
            KeyError: If a required history column is missing.
            ValueError: If ``vol_window < 1``.

        Examples:
            >>> flow = await client.money_flow("فولاد")
            >>> flow[["jdate", "buyer_power_ratio", "smart_money_entry"]].tail()
        """
        frame = await self.full_history(
            symbol_or_inscode,
            adjust=adjust,
            engine=engine,
        )
        return compute_money_flow(frame, vol_window=vol_window)

    async def movement_power(
        self,
        symbol_or_inscode: str | int,
        *,
        windows: tuple[int, int, int] = (5, 20, 60),
        adjust: AdjustMode = "all",
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch full history and compute the movement-power score.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            windows: Lookback windows.  Defaults to ``(5, 20, 60)``.
            adjust: Price adjustment mode.  Defaults to ``"all"``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            The full-history columns plus ``er_<window>`` columns and
            ``movement_power_score`` in ``[-100, +100]``.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If a request fails.
            ParseError: If a payload is malformed.
            KeyError: If ``close`` is missing.
            ValueError: If ``windows`` is empty or non-positive.

        Examples:
            >>> power = await client.movement_power("فولاد")
            >>> power[["jdate", "movement_power_score"]].tail()
        """
        frame = await self.full_history(
            symbol_or_inscode,
            adjust=adjust,
            engine=engine,
        )
        return compute_movement_power(frame, windows=windows)

    def watch(
        self,
        interval: float = 5.0,
        symbols: list[str] | None = None,
        only_market_hours: bool = False,
    ) -> AsyncIterator[MarketEvent]:
        """Return an async iterator of live MarketWatcher events.

        The returned watcher shares this client's transport and does not close
        it when iteration ends.

        Args:
            interval: Seconds between MarketWatch polls.  Defaults to ``5.0``.
            symbols: Optional ticker/``insCode`` allow-list.
            only_market_hours: Skip polls outside Sat-Wed 09:00-12:30 Tehran
                time.  Defaults to ``False``.

        Returns:
            An async iterator of :class:`~iranquant.stream.MarketEvent`.

        Raises:
            ValueError: When iterated with ``interval <= 0``.

        Examples:
            >>> async for event in client.watch(interval=3.0, symbols=["فولاد"]):
            ...     print(event.event_type, event.symbol)
        """

        watcher = MarketWatcher(client=self._client)
        return watcher.watch(
            interval=interval,
            symbols=symbols,
            only_market_hours=only_market_hours,
        )


def _run_with_client(factory: Callable[[AsyncIranQuant], Awaitable[T]]) -> T:
    async def runner() -> T:
        async with AsyncIranQuant() as client:
            return await factory(client)

    return run_sync(runner())


def search(query: str, *, engine: Engine = "pandas") -> DataFrame:
    """Sync: search TSETMC instruments.

    A temporary client is created and closed for the call.  Safe to call inside
    an existing event loop (Jupyter/Colab).

    Args:
        query: Symbol or company-name fragment, e.g. ``"فولاد"``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        One row per match with ``ins_code``, ``symbol``, ``company_name``,
        ``isin``, ``shares``, ``flow``, ``board``, ``last_date`` and
        ``last_date_jdate``.

    Raises:
        NetworkError: If TSETMC cannot be reached.
        ParseError: If the response is malformed.

    Examples:
        >>> results = iq.search("فولاد")
        >>> results.iloc[0]["ins_code"]
        '46348559193224090'
    """

    return _run_with_client(lambda client: client.search(query, engine=engine))


def get_inscode(symbol_or_inscode: str | int) -> str:
    """Sync: resolve a symbol name or numeric code to its TSETMC ``insCode``.

    Args:
        symbol_or_inscode: Persian ticker or a numeric instrument code.

    Returns:
        The numeric ``insCode`` as a string.  Numeric input bypasses the
        network.

    Raises:
        SymbolNotFoundError: If the symbol cannot be matched.
        NetworkError: If the lookup request fails.
        ParseError: If the response is malformed.

    Examples:
        >>> iq.get_inscode("فولاد")
        '46348559193224090'
    """

    return _run_with_client(lambda client: client.resolve(symbol_or_inscode))


def history(
    symbol_or_inscode: str | int,
    *,
    adjust: AdjustMode = "all",
    engine: Engine = "pandas",
) -> DataFrame:
    """Sync: daily OHLCV history.

    Args:
        symbol_or_inscode: Persian ticker or numeric ``insCode``.
        adjust: ``"none"``, ``"all"`` (default) or ``"capital_only"``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        Columns ``date`` (``datetime.date``), ``jdate`` (``str``), ``open``,
        ``high``, ``low``, ``close``, ``final_price``, ``prev_price``,
        ``volume``, ``value`` and ``trades`` (all ``float``), sorted by date.

    Raises:
        SymbolNotFoundError: If the symbol cannot be resolved.
        NetworkError: If TSETMC cannot be reached.
        ParseError: If the payload is malformed.

    Examples:
        >>> frame = iq.history("فولاد", adjust="all")
        >>> frame.tail(3)[["jdate", "close"]]
    """

    return _run_with_client(
        lambda client: client.history(symbol_or_inscode, adjust=adjust, engine=engine)
    )


def full_history(
    symbol_or_inscode: str | int,
    *,
    adjust: AdjustMode = "all",
    engine: Engine = "pandas",
) -> DataFrame:
    """Sync: daily OHLCV merged with client-type history.

    Args:
        symbol_or_inscode: Persian ticker or numeric ``insCode``.
        adjust: ``"none"``, ``"all"`` (default) or ``"capital_only"``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        The :func:`history` columns plus ``buy_I_Volume``, ``buy_N_Volume``,
        ``buy_I_Value``, ``buy_N_Value``, ``buy_N_Count``, ``buy_I_Count``,
        ``sell_I_Volume``, ``sell_N_Volume``, ``sell_I_Value``,
        ``sell_N_Value``, ``sell_N_Count`` and ``sell_I_Count``.

    Raises:
        SymbolNotFoundError: If the symbol cannot be resolved.
        NetworkError: If a request fails.
        ParseError: If a payload is malformed.

    Examples:
        >>> frame = iq.full_history("فولاد")
        >>> frame[["jdate", "buy_I_Volume", "sell_N_Count"]].tail()
    """

    return _run_with_client(
        lambda client: client.full_history(symbol_or_inscode, adjust=adjust, engine=engine)
    )


def client_type(
    symbol_or_inscode: str | int,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Sync: retail/institutional client-type history.

    Args:
        symbol_or_inscode: Persian ticker or numeric ``insCode``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        Columns ``date``, ``jdate``, ``buy_I_Volume``, ``buy_N_Volume``,
        ``buy_I_Value``, ``buy_N_Value``, ``buy_N_Count``, ``buy_I_Count``,
        ``sell_I_Volume``, ``sell_N_Volume``, ``sell_I_Value``,
        ``sell_N_Value``, ``sell_N_Count`` and ``sell_I_Count``.

    Raises:
        SymbolNotFoundError: If the symbol cannot be resolved.
        NetworkError: If the request fails.
        ParseError: If the payload is malformed.

    Examples:
        >>> frame = iq.client_type("فولاد")
        >>> frame[["date", "buy_I_Volume", "sell_I_Volume"]].tail()
    """

    return _run_with_client(lambda client: client.client_type(symbol_or_inscode, engine=engine))


def market_watch(*, engine: Engine = "pandas") -> DataFrame:
    """Sync: live market-wide snapshot.

    Args:
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        One row per symbol with ``ins_code``, ``symbol``, ``company_name``,
        ``eps``, ``pe``, ``open``, ``high``, ``low``, ``close``,
        ``final_price``, ``prev_price``, ``volume``, ``value``, ``trades``,
        ``tmax``, ``tmin``, ``total_shares`` and ``order_book``.

    Raises:
        NetworkError: If the MarketWatch endpoint cannot be reached.
        ParseError: If the response body is not valid JSON.

    Examples:
        >>> market = iq.market_watch()
        >>> market[["symbol", "close", "tmax"]].head()
    """

    return _run_with_client(lambda client: client.market_watch(engine=engine))


def order_book(
    symbol_or_inscode: str | int,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Sync: best-limit order book.

    Args:
        symbol_or_inscode: Persian ticker or numeric ``insCode``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        Columns ``n``, ``qmd``, ``zmd``, ``pmd``, ``pmo``, ``zmo`` and
        ``qmo`` sorted by level.

    Raises:
        SymbolNotFoundError: If the symbol cannot be resolved.
        NetworkError: If the request fails.
        ParseError: If the response body is not valid JSON.

    Examples:
        >>> book = iq.order_book("فولاد")
        >>> book[["pmd", "zmd", "pmo", "zmo"]].head()
    """

    return _run_with_client(lambda client: client.order_book(symbol_or_inscode, engine=engine))


def intraday_trades(
    symbol_or_inscode: str | int,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Sync: intraday OHLCV bars for the current/last session.

    Args:
        symbol_or_inscode: Persian ticker or numeric ``insCode``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        Columns ``time`` (``datetime.time | None``), ``open``, ``high``,
        ``low``, ``close`` and ``volume``.

    Raises:
        SymbolNotFoundError: If the symbol cannot be resolved.
        NetworkError: If the request fails.
        ParseError: If the response body is not valid JSON.

    Examples:
        >>> bars = iq.intraday_trades("فولاد")
        >>> bars[["time", "close", "volume"]].tail()
    """

    return _run_with_client(lambda client: client.intraday_trades(symbol_or_inscode, engine=engine))


def indices_live(*, engine: Engine = "pandas") -> DataFrame:
    """Sync: live index snapshots.

    Args:
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        Standardized columns ``index_code``, ``name``, ``index_value``,
        ``change``, ``change_pct``, ``date``, ``jdate`` and ``time``.

    Raises:
        NetworkError: If no index data can be fetched.

    Examples:
        >>> indices = iq.indices_live()
        >>> indices[["name", "index_value", "change_pct"]].head()
    """

    return _run_with_client(lambda client: client.indices_live(engine=engine))


def index_history(
    index_code: str = OVERALL_INDEX_CODE,
    *,
    engine: Engine = "pandas",
) -> DataFrame:
    """Sync: index daily history.

    Args:
        index_code: TSETMC index code.  Defaults to the overall index.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        Standardized index columns plus raw upstream fields, sorted by date.

    Raises:
        NetworkError: If both the B2 and B1 endpoints fail.
        ParseError: If a body is not valid JSON.

    Examples:
        >>> history = iq.index_history()
        >>> history[["date", "index_value"]].tail()
    """

    return _run_with_client(lambda client: client.index_history(index_code, engine=engine))


def codal_search(
    symbol: str | None = None,
    letter_type: int = -1,
    category: int = -1,
    page: int = 1,
    audited: bool = True,
    not_audited: bool = True,
    mains: bool = True,
    childs: bool = True,
) -> CodalSearchResponse:
    """Sync: search Codal announcements.

    Args:
        symbol: Optional Persian ticker filter.
        letter_type: Codal ``LetterType`` (``-1`` all, ``58`` monthly activity,
            ``6`` interim financials).  Defaults to ``-1``.
        category: Codal ``Category`` filter.  Defaults to ``-1``.
        page: One-based page.  Defaults to ``1``.
        audited: Include audited reports.  Defaults to ``True``.
        not_audited: Include unaudited reports.  Defaults to ``True``.
        mains: Include parent-company reports.  Defaults to ``True``.
        childs: Include subsidiary reports.  Defaults to ``True``.

    Returns:
        A :class:`~iranquant.models.codal.CodalSearchResponse` with absolute
        URLs.

    Raises:
        NetworkError: If Codal cannot be reached.
        ParseError: If the response is malformed.

    Examples:
        >>> reports = iq.codal_search("فولاد", letter_type=58)
        >>> reports.Total
    """

    return _run_with_client(
        lambda client: client.codal_search(
            symbol=symbol,
            letter_type=letter_type,
            category=category,
            page=page,
            audited=audited,
            not_audited=not_audited,
            mains=mains,
            childs=childs,
        )
    )


def monthly_reports(symbol: str, page: int = 1) -> CodalSearchResponse:
    """Sync: monthly activity reports (گزارش فعالیت ماهانه).

    Args:
        symbol: Persian ticker, e.g. ``"فولاد"``.
        page: One-based page.  Defaults to ``1``.

    Returns:
        A :class:`~iranquant.models.codal.CodalSearchResponse` with
        ``LetterType=58`` results.

    Raises:
        NetworkError: If Codal cannot be reached.
        ParseError: If the response is malformed.

    Examples:
        >>> reports = iq.monthly_reports("فولاد")
        >>> tables = iq.codal_report(reports.Letters[0])
    """

    return _run_with_client(lambda client: client.monthly_reports(symbol, page=page))


def financial_statements(symbol: str, page: int = 1) -> CodalSearchResponse:
    """Sync: interim financial statements.

    Args:
        symbol: Persian ticker, e.g. ``"فولاد"``.
        page: One-based page.  Defaults to ``1``.

    Returns:
        A :class:`~iranquant.models.codal.CodalSearchResponse` with
        ``LetterType=6`` results.

    Raises:
        NetworkError: If Codal cannot be reached.
        ParseError: If the response is malformed.

    Examples:
        >>> statements = iq.financial_statements("فولاد")
        >>> statements.Letters[0].Title
    """

    return _run_with_client(lambda client: client.financial_statements(symbol, page=page))


def codal_report(
    url_or_letter: str | CodalLetter,
    *,
    engine: Engine = "pandas",
) -> dict[str, Any]:
    """Sync: download and parse all tables of a Codal report.

    Args:
        url_or_letter: Report URL or a ``CodalLetter`` with ``Url``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A mapping of ``"<sheet title> - <table title>"`` to DataFrames.

    Raises:
        NetworkError: If the report page cannot be downloaded.
        ParseError: If the page has no valid datasource JSON.
        ValueError: If ``engine`` is unsupported.

    Examples:
        >>> tables = iq.codal_report("/Report/123")
        >>> {name: table.shape for name, table in tables.items()}
    """

    return _run_with_client(lambda client: client.codal_report(url_or_letter, engine=engine))


def parse_codal_report(
    url_or_letter: str | CodalLetter,
    *,
    engine: Engine = "pandas",
) -> dict[str, Any]:
    """Sync: download and parse all tables of a Codal report.

    This is an explicit, discoverable alias for :func:`codal_report`; both
    return the same mapping.  A temporary client is created and closed for the
    call, and it is safe to use inside a running event loop.

    Args:
        url_or_letter: Report URL or a ``CodalLetter`` with ``Url``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A mapping of ``"<sheet title> - <table title>"`` to DataFrames.

    Raises:
        NetworkError: If the report page cannot be downloaded.
        ParseError: If the page has no valid datasource JSON.
        ValueError: If ``engine`` is unsupported.

    Examples:
        >>> letter = iq.monthly_reports("فولاد").Letters[0]
        >>> tables = iq.parse_codal_report(letter)
        >>> summary = iq.analyze_monthly_sales(tables)
    """

    return _run_with_client(lambda client: client.parse_codal_report(url_or_letter, engine=engine))


def monthly_sales_summary(symbol: str, page: int = 1) -> dict[str, Any]:
    """Sync: analyze a symbol's latest monthly sales report.

    Fetches the newest ``LetterType=58`` report and compares its current month
    with the previous distinct monthly period.  Values are in million Rials.

    Args:
        symbol: Persian ticker, e.g. ``"فولاد"``.
        page: One-based Codal result page.  Defaults to ``1``.

    Returns:
        The dictionary produced by
        :func:`~iranquant.codal.monthly.analyze_monthly_sales` plus
        ``symbol``, ``tracing_no``, ``title``, ``prev_month_tracing_no`` and a
        ``mom_change_pct`` computed from the previous report.

    Raises:
        ParseError: If no HTML monthly report is available.
        NetworkError: If Codal cannot be reached.
        ValueError: If the report has no usable tables.

    Examples:
        >>> summary = iq.monthly_sales_summary("فولاد")
        >>> summary["current_month_sales"]
        662639489.0
        >>> summary["yoy_change_pct"]
    """

    return _run_with_client(lambda client: client.monthly_sales_summary(symbol, page=page))


def money_flow(
    symbol_or_inscode: str | int,
    *,
    adjust: AdjustMode = "all",
    vol_window: int = 20,
    engine: Engine = "pandas",
) -> DataFrame:
    """Sync: fetch full history and compute smart-money/flow analytics.

    Args:
        symbol_or_inscode: Persian ticker or numeric ``insCode``.
        adjust: Price adjustment mode.  Defaults to ``"all"``.
        vol_window: Rolling volume window.  Defaults to ``20``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        The full-history columns plus the money-flow columns described in
        :func:`iranquant.quant.compute_money_flow`.

    Raises:
        SymbolNotFoundError: If the symbol cannot be resolved.
        NetworkError: If a request fails.
        ParseError: If a payload is malformed.
        KeyError: If a required history column is missing.
        ValueError: If ``vol_window < 1``.

    Examples:
        >>> flow = iq.money_flow("فولاد")
        >>> flow[["jdate", "buyer_power_ratio", "smart_money_entry"]].tail()
    """

    return _run_with_client(
        lambda client: client.money_flow(
            symbol_or_inscode,
            adjust=adjust,
            vol_window=vol_window,
            engine=engine,
        )
    )


def movement_power(
    symbol_or_inscode: str | int,
    *,
    windows: tuple[int, int, int] = (5, 20, 60),
    adjust: AdjustMode = "all",
    engine: Engine = "pandas",
) -> DataFrame:
    """Sync: fetch full history and compute the movement-power score.

    Args:
        symbol_or_inscode: Persian ticker or numeric ``insCode``.
        windows: Lookback windows.  Defaults to ``(5, 20, 60)``.
        adjust: Price adjustment mode.  Defaults to ``"all"``.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        The full-history columns plus ``er_<window>`` columns and
        ``movement_power_score`` in ``[-100, +100]``.

    Raises:
        SymbolNotFoundError: If the symbol cannot be resolved.
        NetworkError: If a request fails.
        ParseError: If a payload is malformed.
        KeyError: If ``close`` is missing.
        ValueError: If ``windows`` is empty or non-positive.

    Examples:
        >>> power = iq.movement_power("فولاد")
        >>> power[["jdate", "er_5", "movement_power_score"]].tail()
    """

    return _run_with_client(
        lambda client: client.movement_power(
            symbol_or_inscode,
            windows=windows,
            adjust=adjust,
            engine=engine,
        )
    )


class IranQuant:
    """Synchronous facade over :class:`AsyncIranQuant`.

    Each method delegates to the same-named module-level function, so an
    ``IranQuant`` instance can be passed around as a small client object
    without managing any HTTP transport or event loop.

    Examples:
        >>> iq_client = IranQuant()
        >>> frame = iq_client.history("فولاد")
        >>> screener = iq_client.screener(iq_client.market_watch())
    """

    def search(self, query: str, *, engine: Engine = "pandas") -> DataFrame:
        """Search TSETMC instruments by ticker or company name.

        Args:
            query: Symbol or company-name fragment, e.g. ``"فولاد"``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            One row per match with ``ins_code``, ``symbol``, ``company_name``,
            ``isin``, ``shares``, ``flow``, ``board``, ``last_date`` and
            ``last_date_jdate``.

        Raises:
            NetworkError: If TSETMC cannot be reached.
            ParseError: If the response is malformed.

        Examples:
            >>> IranQuant().search("فولاد").head()
        """
        return search(query, engine=engine)

    def get_inscode(self, symbol_or_inscode: str | int) -> str:
        """Resolve a symbol or numeric code to its TSETMC ``insCode``.

        Args:
            symbol_or_inscode: Persian ticker or numeric instrument code.

        Returns:
            The numeric ``insCode`` as a string.

        Raises:
            SymbolNotFoundError: If the symbol cannot be matched.
            NetworkError: If the lookup request fails.

        Examples:
            >>> IranQuant().get_inscode("فولاد").isdigit()
            True
        """
        return get_inscode(symbol_or_inscode)

    def history(
        self,
        symbol_or_inscode: str | int,
        *,
        adjust: AdjustMode = "all",
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch backward-adjusted daily OHLCV history.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            adjust: ``"none"``, ``"all"`` (default) or ``"capital_only"``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Columns ``date``, ``jdate``, ``open``, ``high``, ``low``,
            ``close``, ``final_price``, ``prev_price``, ``volume``, ``value``
            and ``trades``, sorted by date.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If the request fails.
            ParseError: If the payload is malformed.

        Examples:
            >>> IranQuant().history("فولاد").tail()
        """
        return history(symbol_or_inscode, adjust=adjust, engine=engine)

    def full_history(
        self,
        symbol_or_inscode: str | int,
        *,
        adjust: AdjustMode = "all",
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch daily OHLCV merged with client-type history.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            adjust: ``"none"``, ``"all"`` (default) or ``"capital_only"``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            The ``history`` columns plus the twelve ``buy_*``/``sell_*``
            client-type columns.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If a request fails.
            ParseError: If a payload is malformed.

        Examples:
            >>> IranQuant().full_history("فولاد").tail()
        """
        return full_history(symbol_or_inscode, adjust=adjust, engine=engine)

    def client_type(
        self,
        symbol_or_inscode: str | int,
        *,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch retail/institutional client-type history only.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Columns ``date``, ``jdate`` and the twelve ``buy_*``/``sell_*``
            client-type columns.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If the request fails.
            ParseError: If the payload is malformed.

        Examples:
            >>> IranQuant().client_type("فولاد").tail()
        """
        return client_type(symbol_or_inscode, engine=engine)

    def market_watch(self, *, engine: Engine = "pandas") -> DataFrame:
        """Fetch the live market-wide snapshot in one request.

        Args:
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            One row per symbol with the MarketWatch columns described in
            :func:`iranquant.market_watch`.

        Raises:
            NetworkError: If the endpoint cannot be reached.
            ParseError: If the response body is not valid JSON.

        Examples:
            >>> IranQuant().market_watch().head()
        """
        return market_watch(engine=engine)

    def order_book(
        self,
        symbol_or_inscode: str | int,
        *,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch the best-limit order book (levels 1..5).

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Columns ``n``, ``qmd``, ``zmd``, ``pmd``, ``pmo``, ``zmo`` and
            ``qmo``.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If the request fails.

        Examples:
            >>> IranQuant().order_book("فولاد").head()
        """
        return order_book(symbol_or_inscode, engine=engine)

    def intraday_trades(
        self,
        symbol_or_inscode: str | int,
        *,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch intraday OHLCV bars for the current/last session.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Columns ``time``, ``open``, ``high``, ``low``, ``close`` and
            ``volume``.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If the request fails.

        Examples:
            >>> IranQuant().intraday_trades("فولاد").tail()
        """
        return intraday_trades(symbol_or_inscode, engine=engine)

    def indices_live(self, *, engine: Engine = "pandas") -> DataFrame:
        """Fetch live overall, equal-weight and sector index snapshots.

        Args:
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Standardized index columns described in
            :func:`iranquant.indices_live`.

        Raises:
            NetworkError: If no index data can be fetched.

        Examples:
            >>> IranQuant().indices_live().head()
        """
        return indices_live(engine=engine)

    def index_history(
        self,
        index_code: str = OVERALL_INDEX_CODE,
        *,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch historical daily values for an index code.

        Args:
            index_code: TSETMC index code.  Defaults to the overall index.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            Standardized index columns plus raw upstream fields, sorted by
            date.

        Raises:
            NetworkError: If both the B2 and B1 endpoints fail.
            ParseError: If a body is not valid JSON.

        Examples:
            >>> IranQuant().index_history().tail()
        """
        return index_history(index_code, engine=engine)

    def codal_search(
        self,
        symbol: str | None = None,
        letter_type: int = -1,
        category: int = -1,
        page: int = 1,
        audited: bool = True,
        not_audited: bool = True,
        mains: bool = True,
        childs: bool = True,
    ) -> CodalSearchResponse:
        """Search Codal announcements.

        Args:
            symbol: Optional Persian ticker filter.
            letter_type: ``LetterType`` (``-1`` all, ``58`` monthly activity,
                ``6`` interim financials).  Defaults to ``-1``.
            category: ``Category`` filter.  Defaults to ``-1``.
            page: One-based page.  Defaults to ``1``.
            audited: Include audited reports.  Defaults to ``True``.
            not_audited: Include unaudited reports.  Defaults to ``True``.
            mains: Include parent-company reports.  Defaults to ``True``.
            childs: Include subsidiary reports.  Defaults to ``True``.

        Returns:
            A :class:`~iranquant.models.codal.CodalSearchResponse`.

        Raises:
            NetworkError: If Codal cannot be reached.
            ParseError: If the response is malformed.

        Examples:
            >>> IranQuant().codal_search("فولاد", letter_type=58).Total
        """
        return codal_search(
            symbol=symbol,
            letter_type=letter_type,
            category=category,
            page=page,
            audited=audited,
            not_audited=not_audited,
            mains=mains,
            childs=childs,
        )

    def monthly_reports(self, symbol: str, page: int = 1) -> CodalSearchResponse:
        """Fetch monthly activity reports (``LetterType=58``).

        Args:
            symbol: Persian ticker, e.g. ``"فولاد"``.
            page: One-based page.  Defaults to ``1``.

        Returns:
            A :class:`~iranquant.models.codal.CodalSearchResponse`.

        Raises:
            NetworkError: If Codal cannot be reached.
            ParseError: If the response is malformed.

        Examples:
            >>> IranQuant().monthly_reports("فولاد").Letters[0].Title
        """
        return monthly_reports(symbol, page=page)

    def financial_statements(self, symbol: str, page: int = 1) -> CodalSearchResponse:
        """Fetch interim financial statements (``LetterType=6``).

        Args:
            symbol: Persian ticker, e.g. ``"فولاد"``.
            page: One-based page.  Defaults to ``1``.

        Returns:
            A :class:`~iranquant.models.codal.CodalSearchResponse`.

        Raises:
            NetworkError: If Codal cannot be reached.
            ParseError: If the response is malformed.

        Examples:
            >>> IranQuant().financial_statements("فولاد").Total
        """
        return financial_statements(symbol, page=page)

    def codal_report(
        self,
        url_or_letter: str | CodalLetter,
        *,
        engine: Engine = "pandas",
    ) -> dict[str, Any]:
        """Download and parse all tables of a Codal report.

        Args:
            url_or_letter: Report URL or a ``CodalLetter`` with ``Url``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            A mapping of ``"<sheet title> - <table title>"`` to DataFrames.

        Raises:
            NetworkError: If the report page cannot be downloaded.
            ParseError: If the page has no valid datasource JSON.

        Examples:
            >>> IranQuant().codal_report("/Report/123")
        """
        return codal_report(url_or_letter, engine=engine)

    def parse_codal_report(
        self,
        url_or_letter: str | CodalLetter,
        *,
        engine: Engine = "pandas",
    ) -> dict[str, Any]:
        """Download and parse all tables of a Codal report.

        Args:
            url_or_letter: Report URL or a ``CodalLetter`` with ``Url``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            A mapping of ``"<sheet title> - <table title>"`` to DataFrames.

        Raises:
            NetworkError: If the report page cannot be downloaded.
            ParseError: If the page has no valid datasource JSON.

        Examples:
            >>> tables = IranQuant().parse_codal_report("/Report/123")
        """
        return parse_codal_report(url_or_letter, engine=engine)

    def monthly_sales_summary(self, symbol: str, page: int = 1) -> dict[str, Any]:
        """Analyze a symbol's latest monthly sales report.

        Args:
            symbol: Persian ticker, e.g. ``"فولاد"``.
            page: One-based Codal result page.  Defaults to ``1``.

        Returns:
            The monthly analyzer result plus report metadata and a
            ``mom_change_pct`` computed from the previous monthly period.

        Raises:
            ParseError: If no HTML monthly report is available.
            NetworkError: If Codal cannot be reached.

        Examples:
            >>> IranQuant().monthly_sales_summary("فولاد")["yoy_change_pct"]
        """
        return monthly_sales_summary(symbol, page=page)

    def money_flow(
        self,
        symbol_or_inscode: str | int,
        *,
        adjust: AdjustMode = "all",
        vol_window: int = 20,
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch full history and compute smart-money/flow analytics.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            adjust: Price adjustment mode.  Defaults to ``"all"``.
            vol_window: Rolling volume window.  Defaults to ``20``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            The full-history columns plus the money-flow columns described in
            :func:`iranquant.quant.compute_money_flow`.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If a request fails.
            KeyError: If a required history column is missing.
            ValueError: If ``vol_window < 1``.

        Examples:
            >>> IranQuant().money_flow("فولاد").tail()
        """
        return money_flow(
            symbol_or_inscode,
            adjust=adjust,
            vol_window=vol_window,
            engine=engine,
        )

    def movement_power(
        self,
        symbol_or_inscode: str | int,
        *,
        windows: tuple[int, int, int] = (5, 20, 60),
        adjust: AdjustMode = "all",
        engine: Engine = "pandas",
    ) -> DataFrame:
        """Fetch full history and compute the movement-power score.

        Args:
            symbol_or_inscode: Persian ticker or numeric ``insCode``.
            windows: Lookback windows.  Defaults to ``(5, 20, 60)``.
            adjust: Price adjustment mode.  Defaults to ``"all"``.
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            The full-history columns plus ``er_<window>`` and
            ``movement_power_score``.

        Raises:
            SymbolNotFoundError: If the symbol cannot be resolved.
            NetworkError: If a request fails.
            KeyError: If ``close`` is missing.
            ValueError: If ``windows`` is empty or non-positive.

        Examples:
            >>> IranQuant().movement_power("فولاد").tail()
        """
        return movement_power(
            symbol_or_inscode,
            windows=windows,
            adjust=adjust,
            engine=engine,
        )

    def screener(self, frame: DataFrame) -> MarketScreener:
        """Wrap a MarketWatch frame in a :class:`~iranquant.MarketScreener`.

        Args:
            frame: A MarketWatch (or batch) frame.

        Returns:
            A screener bound to ``frame``.

        Raises:
            TypeError: When a filter is run with a non-Pandas/Polars frame.

        Examples:
            >>> screener = IranQuant().screener(iq.market_watch())
            >>> screener.filter_buy_queue().head()
        """
        return MarketScreener(frame)


__all__ = [
    "AsyncHTTPClient",
    "AsyncIranQuant",
    "CodalLetter",
    "CodalSearchResponse",
    "DataFrame",
    "Engine",
    "IranQuant",
    "IranQuantError",
    "MarketEvent",
    "MarketScreener",
    "MarketWatcher",
    "NetworkError",
    "ParseError",
    "SymbolNotFoundError",
    "__version__",
    "analyze_monthly_sales",
    "client_type",
    "codal_report",
    "codal_search",
    "compute_money_flow",
    "compute_movement_power",
    "detect_swing_legs",
    "fetch_and_parse_report",
    "financial_statements",
    "full_history",
    "get_financial_statements",
    "get_inscode",
    "get_monthly_reports",
    "history",
    "index_history",
    "indices_live",
    "intraday_trades",
    "is_market_open",
    "market_watch",
    "money_flow",
    "monthly_reports",
    "monthly_sales_summary",
    "movement_power",
    "order_book",
    "parse_codal_report",
    "search",
    "search_codal",
]
