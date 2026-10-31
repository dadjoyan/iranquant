"""Tests for the Rich terminal CLI."""

from __future__ import annotations

from io import StringIO
from typing import Any

import pandas as pd
import pytest
from rich.console import Console

import iranquant as iq_module
import iranquant.cli as cli
from iranquant.exceptions import NetworkError
from iranquant.models.codal import CodalLetter, CodalSearchResponse


def _console() -> tuple[Console, StringIO]:
    buffer = StringIO()
    return Console(file=buffer, force_terminal=False, width=120), buffer


def _history_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "jdate": "1405-07-28",
                "close": 4100.0,
                "buyer_power_ratio": 1.8,
                "smart_money_entry": True,
                "smart_money_exit": False,
                "movement_power_score": 42.0,
            },
            {
                "jdate": "1405-07-29",
                "close": 4150.0,
                "buyer_power_ratio": 0.5,
                "smart_money_entry": False,
                "smart_money_exit": True,
                "movement_power_score": -12.0,
            },
            {
                "jdate": "1405-07-30",
                "close": 4200.0,
                "buyer_power_ratio": 1.0,
                "smart_money_entry": False,
                "smart_money_exit": False,
                "movement_power_score": 5.0,
            },
        ]
    )


def _market_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "فولاد",
                "company_name": "فولاد مبارکه",
                "close": 500.0,
                "final_price": 490.0,
                "tmax": 500.0,
                "tmin": 450.0,
                "volume": 1000.0,
                "order_book": [{"zmd": 1000.0, "zmo": 0.0}],
            },
            {
                "symbol": "خودرو",
                "company_name": "ایران خودرو",
                "close": 450.0,
                "final_price": 460.0,
                "tmax": 500.0,
                "tmin": 450.0,
                "volume": 2000.0,
                "order_book": [{"zmd": 0.0, "zmo": 2000.0}],
            },
        ]
    )


def _indices_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "name": "شاخص کل",
                "index_code": "32097828799138957",
                "index_value": 7967550.61,
                "change": 40220.04,
                "change_pct": 0.51,
                "time": "12:30:00",
            }
        ]
    )


def _summary() -> dict[str, Any]:
    return {
        "period_end": "1405-07-30",
        "current_month_sales": 662639489.0,
        "ytd_sales": 3172061674.0,
        "prev_ytd_sales": 1892095932.0,
        "mom_change_pct": 3.63,
        "yoy_change_pct": 67.65,
    }


def test_search_command(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "فولاد",
                "company_name": "فولاد مبارکه",
                "ins_code": "46348559193224090",
                "flow": 1,
            }
        ]
    )
    monkeypatch.setattr(iq_module, "search", lambda query, **kwargs: frame)

    console, buffer = _console()
    code = cli.main(["search", "فولاد"], console=console)

    output = buffer.getvalue()
    assert code == 0
    assert "فولاد" in output
    assert "46348559193224090" in output
    assert "Search results" in output


def test_search_command_with_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        iq_module, "search", lambda query, **kwargs: pd.DataFrame(columns=["symbol"])
    )

    console, buffer = _console()
    assert cli.main(["search", "هیچ"], console=console) == 0
    assert "No symbols matched" in buffer.getvalue()


def test_history_command(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _history_frame()
    monkeypatch.setattr(iq_module, "full_history", lambda symbol, **kwargs: frame.copy())
    monkeypatch.setattr(iq_module, "compute_money_flow", lambda data, **kwargs: data)
    monkeypatch.setattr(iq_module, "compute_movement_power", lambda data, **kwargs: data)

    console, buffer = _console()
    code = cli.main(
        ["history", "فولاد", "--days", "3", "--adjust", "none"],
        console=console,
    )

    output = buffer.getvalue()
    assert code == 0
    assert "last 3 sessions" in output
    assert "ENTRY" in output
    assert "EXIT" in output


def test_screener_buy_queue_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iq_module, "market_watch", lambda **kwargs: _market_frame())

    console, buffer = _console()
    code = cli.main(["screener", "--mode", "buy-queue"], console=console)

    output = buffer.getvalue()
    assert code == 0
    assert "Live screener" in output
    assert "فولاد" in output
    assert "خودرو" not in output


def test_screener_sell_queue_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iq_module, "market_watch", lambda **kwargs: _market_frame())

    console, buffer = _console()
    code = cli.main(["screener", "--mode", "sell-queue"], console=console)

    output = buffer.getvalue()
    assert code == 0
    assert "خودرو" in output
    assert "فولاد" not in output


def test_indices_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iq_module, "indices_live", lambda **kwargs: _indices_frame())

    console, buffer = _console()
    code = cli.main(["indices"], console=console)

    output = buffer.getvalue()
    assert code == 0
    assert "شاخص کل" in output
    assert "7,967,550.61" in output


def _reports() -> CodalSearchResponse:
    return CodalSearchResponse(
        Total=1,
        Page=1,
        Letters=[
            CodalLetter(
                TracingNo=1621524,
                Title="گزارش فعالیت ماهانه",
                PublishDateTime="2026-10-28",
                Url="https://codal.ir/Report/1621524",
                HasHtml=True,
            )
        ],
    )


def test_codal_command_lists_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iq_module, "monthly_reports", lambda symbol, **kwargs: _reports())

    console, buffer = _console()
    code = cli.main(["codal", "فولاد"], console=console)

    output = buffer.getvalue()
    assert code == 0
    assert "1621524" in output
    assert "Monthly Codal reports" in output


def test_codal_command_parses_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iq_module, "monthly_reports", lambda symbol, **kwargs: _reports())
    monkeypatch.setattr(
        iq_module,
        "parse_codal_report",
        lambda letter, **kwargs: {
            "گزارش فعالیت ماهانه - تولید و فروش": pd.DataFrame({"شرح": ["جمع"]})
        },
    )
    monkeypatch.setattr(iq_module, "analyze_monthly_sales", lambda tables, **kwargs: _summary())

    console, buffer = _console()
    code = cli.main(["codal", "فولاد", "--parse-latest"], console=console)

    output = buffer.getvalue()
    assert code == 0
    assert "Monthly sales summary" in output
    assert "662,639,489" in output
    assert "+67.65%" in output


def test_codal_command_handles_unanalyzable_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(iq_module, "monthly_reports", lambda symbol, **kwargs: _reports())
    monkeypatch.setattr(
        iq_module, "parse_codal_report", lambda letter, **kwargs: {"t": pd.DataFrame()}
    )

    def fail(tables: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        raise ValueError("no usable tables")

    monkeypatch.setattr(iq_module, "analyze_monthly_sales", fail)

    console, buffer = _console()
    code = cli.main(["codal", "فولاد", "--parse-latest"], console=console)

    assert code == 0
    assert "Could not analyze sales" in buffer.getvalue()


def test_codal_command_with_no_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        iq_module,
        "monthly_reports",
        lambda symbol, **kwargs: CodalSearchResponse(),
    )

    console, buffer = _console()
    assert cli.main(["codal", "فولاد"], console=console) == 0
    assert "No monthly reports found" in buffer.getvalue()


def test_main_handles_iranquant_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(query: str, **kwargs: Any) -> pd.DataFrame:
        raise NetworkError("upstream unavailable")

    monkeypatch.setattr(iq_module, "search", fail)

    console, buffer = _console()
    code = cli.main(["search", "فولاد"], console=console)

    output = buffer.getvalue()
    assert code == 1
    assert "iranquant error" in output
    assert "upstream unavailable" in output


def test_main_without_command_prints_help() -> None:
    assert cli.main([]) == 0


def test_positive_int_rejects_invalid_values() -> None:
    with pytest.raises(SystemExit):
        cli.main(["history", "فولاد", "--days", "0"])
