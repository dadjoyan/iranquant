"""Rich terminal interface for the ``iranquant`` command.

The CLI is a thin presentation layer over the public synchronous API.  It never
implements market logic itself, which keeps its output consistent with the
library::

    iranquant search فولاد
    iranquant history فولاد --days 15
    iranquant screener --mode buy-queue --limit 20
    iranquant indices
    iranquant codal فولاد --parse-latest
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any, cast

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import iranquant as iq
from iranquant.quant import MarketScreener


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _format_number(value: Any, digits: int = 0) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{number:,.{digits}f}"


def _format_pct(value: Any, digits: int = 2) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{number:+.{digits}f}%"


def _signed_text(value: Any, suffix: str = "") -> Text:
    number = _number(value)
    if number is None:
        return Text("-", style="dim")
    style = "green" if number > 0 else "red" if number < 0 else "dim"
    return Text(f"{number:+,.2f}{suffix}", style=style)


def _queue_volume(order_book: Any, side: str) -> float:
    if order_book is None or isinstance(order_book, str):
        return 0.0
    levels = [order_book] if isinstance(order_book, dict) else list(order_book)
    key = "zmd" if side == "buy" else "zmo"
    total = 0.0
    for level in levels:
        value = level.get(key, 0.0) if isinstance(level, dict) else getattr(level, key, 0.0)
        total += _number(value) or 0.0
    return total


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iranquant",
        description="TSETMC / Codal market data from the terminal.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {iq.__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    search_parser = subparsers.add_parser("search", help="search symbols by ticker or company name")
    search_parser.add_argument("query", help="symbol or company-name fragment")
    search_parser.set_defaults(handler=_command_search)

    history_parser = subparsers.add_parser(
        "history", help="show recent price, money-flow and movement-power data"
    )
    history_parser.add_argument("symbol", help="Persian ticker or insCode")
    history_parser.add_argument(
        "--days", type=_positive_int, default=15, help="number of rows (default: 15)"
    )
    history_parser.add_argument(
        "--adjust",
        choices=("none", "all", "capital_only"),
        default="all",
        help="price adjustment mode (default: all)",
    )
    history_parser.set_defaults(handler=_command_history)

    screener_parser = subparsers.add_parser(
        "screener", help="scan the live market using a predefined filter"
    )
    screener_parser.add_argument(
        "--mode",
        choices=("buy-queue", "sell-queue", "tick-surge"),
        default="buy-queue",
        help="filter to apply (default: buy-queue)",
    )
    screener_parser.add_argument(
        "--limit", type=_positive_int, default=20, help="maximum rows (default: 20)"
    )
    screener_parser.set_defaults(handler=_command_screener)

    indices_parser = subparsers.add_parser("indices", help="show live market indices")
    indices_parser.set_defaults(handler=_command_indices)

    codal_parser = subparsers.add_parser(
        "codal", help="list monthly Codal reports and optionally parse the latest"
    )
    codal_parser.add_argument("symbol", help="Persian ticker")
    codal_parser.add_argument(
        "--parse-latest",
        action="store_true",
        help="parse the newest report and print its tables plus sales summary",
    )
    codal_parser.set_defaults(handler=_command_codal)

    return parser


def _command_search(args: argparse.Namespace, console: Console) -> int:
    frame = cast(pd.DataFrame, iq.search(args.query))
    if frame.empty:
        console.print(f"[yellow]No symbols matched {args.query!r}.[/]")
        return 0

    table = Table(title=f"Search results for {args.query}", show_lines=False)
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Company")
    table.add_column("insCode", justify="right")
    table.add_column("Flow", justify="right")
    for _, row in frame.iterrows():
        table.add_row(
            str(row["symbol"]),
            str(row["company_name"]),
            str(row["ins_code"]),
            str(row["flow"]),
        )
    console.print(table)
    return 0


def _command_history(args: argparse.Namespace, console: Console) -> int:
    full = cast(pd.DataFrame, iq.full_history(args.symbol, adjust=args.adjust))
    flow = cast(pd.DataFrame, iq.compute_money_flow(full))
    enriched = cast(pd.DataFrame, iq.compute_movement_power(flow))

    if enriched.empty:
        console.print(f"[yellow]No history returned for {args.symbol!r}.[/]")
        return 0

    enriched = enriched.copy()
    enriched["daily_return_pct"] = enriched["close"].pct_change() * 100.0
    shown = enriched.tail(args.days)

    table = Table(
        title=f"{args.symbol} — last {len(shown)} sessions (adjust={args.adjust})",
        show_lines=False,
    )
    table.add_column("Date", style="dim")
    table.add_column("Close", justify="right")
    table.add_column("Return", justify="right")
    table.add_column("Buyer Power", justify="right")
    table.add_column("Smart Money", justify="center")
    table.add_column("Power", justify="right")

    for _, row in shown.iterrows():
        buyer_power = _number(row.get("buyer_power_ratio"))
        if buyer_power is None:
            buyer_style = "dim"
        elif buyer_power >= 1.5:
            buyer_style = "bold green"
        elif buyer_power <= 0.67:
            buyer_style = "red"
        else:
            buyer_style = ""

        if bool(row.get("smart_money_entry", False)):
            smart = Text("ENTRY", style="bold green")
        elif bool(row.get("smart_money_exit", False)):
            smart = Text("EXIT", style="bold red")
        else:
            smart = Text("-", style="dim")

        table.add_row(
            str(row.get("jdate", row.get("date", "-"))),
            _format_number(row.get("close")),
            _signed_text(row.get("daily_return_pct"), "%"),
            Text(_format_number(buyer_power, 2), style=buyer_style),
            smart,
            _signed_text(row.get("movement_power_score")),
        )

    console.print(table)
    return 0


def _command_screener(args: argparse.Namespace, console: Console) -> int:
    market = cast(pd.DataFrame, iq.market_watch())
    screener = MarketScreener(market)

    if args.mode == "buy-queue":
        filtered = cast(pd.DataFrame, screener.filter_buy_queue()).copy()
        filtered["_metric"] = filtered["order_book"].apply(lambda book: _queue_volume(book, "buy"))
        metric_title = "Buy Queue Vol"
    elif args.mode == "sell-queue":
        filtered = cast(pd.DataFrame, screener.filter_sell_queue()).copy()
        filtered["_metric"] = filtered["order_book"].apply(lambda book: _queue_volume(book, "sell"))
        metric_title = "Sell Queue Vol"
    else:
        filtered = cast(pd.DataFrame, screener.filter_positive_tick_surge()).copy()
        close = filtered["close"].astype(float)
        final = filtered["final_price"].astype(float).clip(lower=1.0)
        filtered["_metric"] = ((close - final) / final) * 100.0
        metric_title = "Tick Surge"

    if filtered.empty:
        console.print(f"[yellow]No symbols matched mode {args.mode!r}.[/]")
        return 0

    filtered = filtered.sort_values("_metric", ascending=False).head(args.limit)

    table = Table(title=f"Live screener — {args.mode} (top {len(filtered)})")
    table.add_column("Symbol", style="bold cyan")
    table.add_column("Company")
    table.add_column("Close", justify="right")
    table.add_column("Final", justify="right")
    table.add_column(metric_title, justify="right")
    table.add_column("Volume", justify="right")

    for _, row in filtered.iterrows():
        if args.mode == "tick-surge":
            metric = _signed_text(row["_metric"], "%")
        else:
            metric = Text(_format_number(row["_metric"]))

        table.add_row(
            str(row.get("symbol", "")),
            str(row.get("company_name", "")),
            _format_number(row.get("close")),
            _format_number(row.get("final_price")),
            metric,
            _format_number(row.get("volume")),
        )

    console.print(table)
    return 0


def _command_indices(args: argparse.Namespace, console: Console) -> int:
    frame = cast(pd.DataFrame, iq.indices_live())
    if frame.empty:
        console.print("[yellow]No index data returned.[/]")
        return 0

    table = Table(title="Live market indices")
    table.add_column("Index", style="bold")
    table.add_column("Code", style="dim", justify="right")
    table.add_column("Value", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Change %", justify="right")
    table.add_column("Time", justify="right")

    for _, row in frame.iterrows():
        table.add_row(
            str(row.get("name", "")),
            str(row.get("index_code", "")),
            _format_number(row.get("index_value"), 2),
            _signed_text(row.get("change")),
            _signed_text(row.get("change_pct"), "%"),
            str(row.get("time") or "-"),
        )

    console.print(table)
    return 0


def _command_codal(args: argparse.Namespace, console: Console) -> int:
    reports = iq.monthly_reports(args.symbol)
    if len(reports) == 0:
        console.print(f"[yellow]No monthly reports found for {args.symbol!r}.[/]")
        return 0

    letters = Table(title=f"Monthly Codal reports — {args.symbol}")
    letters.add_column("TracingNo", justify="right")
    letters.add_column("Title")
    letters.add_column("Published", style="dim")
    for letter in reports:
        letters.add_row(
            str(letter.TracingNo or "-"),
            letter.Title,
            letter.PublishDateTime or letter.SentDateTime,
        )
    console.print(letters)

    if not args.parse_latest:
        return 0

    letter = reports[0]
    tables = iq.parse_codal_report(letter)

    table_list = Table(title=f"Tables — {letter.Title}")
    table_list.add_column("Table", style="bold")
    table_list.add_column("Shape", justify="right")
    for name, frame in tables.items():
        table_list.add_row(str(name), f"{frame.shape[0]} × {frame.shape[1]}")
    console.print(table_list)

    try:
        summary = iq.analyze_monthly_sales(tables)
    except (ValueError, TypeError) as exc:
        console.print(Panel(str(exc), title="Could not analyze sales", border_style="yellow"))
        return 0

    lines = [
        f"[bold]Period:[/] {summary.get('period_end') or '-'}",
        f"[bold]Current month sales:[/] {_format_number(summary.get('current_month_sales'))}",
        f"[bold]YTD sales:[/] {_format_number(summary.get('ytd_sales'))}",
        f"[bold]Previous-year YTD:[/] {_format_number(summary.get('prev_ytd_sales'))}",
        f"[bold]YoY:[/] {_format_pct(summary.get('yoy_change_pct'))}",
        f"[bold]MoM (previous report):[/] {_format_pct(summary.get('mom_change_pct'))}",
    ]
    console.print(
        Panel(
            "\n".join(lines),
            title="Monthly sales summary (million Rials)",
            border_style="cyan",
        )
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    console: Console | None = None,
) -> int:
    """Run the CLI.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).
        console: Optional Rich console, useful for tests and embedding.

    Returns:
        Process exit code: ``0`` on success, ``1`` on a handled data/network
        error, and ``130`` when interrupted with Ctrl-C.
    """

    parser = _build_parser()
    args = parser.parse_args(argv)
    output = console or Console()

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    try:
        return int(handler(args, output))
    except iq.IranQuantError as exc:
        output.print(Panel(str(exc), title="iranquant error", border_style="red"))
        return 1
    except (KeyError, TypeError, ValueError) as exc:
        output.print(Panel(str(exc), title="iranquant error", border_style="red"))
        return 1
    except KeyboardInterrupt:
        output.print("[yellow]Interrupted.[/]")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
