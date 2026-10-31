"""Tests for the Codal monthly sales analyzer."""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl
import pytest

from iranquant.codal import monthly as monthly_module
from iranquant.codal.monthly import analyze_monthly_sales, monthly_sales_summary
from iranquant.exceptions import NetworkError, ParseError
from iranquant.models.codal import CodalLetter, CodalSearchResponse

LABEL_COLUMN = "شرح - نام محصول"
PRODUCTION_TABLE = "گزارش فعالیت ماهانه - تولید و فروش"


def _monthly_frame(
    *,
    include_prev_month: bool = False,
    include_total: bool = True,
    current_month: float = 1200.0,
    prev_month: float = 1000.0,
    ytd: float = 5000.0,
    prev_ytd: float = 4000.0,
) -> pd.DataFrame:
    """Build a table shaped like the real Foolad monthly report.

    Category rows split 60/40 between domestic and export sales, so they sum to
    the final ``جمع`` row when it is present.
    """

    row_labels = [
        "فروش داخلی:",
        "محصول الف",
        "جمع فروش داخلی",
        "فروش صادراتی:",
        "محصول ب",
        "جمع فروش صادراتی",
        "جمع",
        "جمع برگشت از فروش",
    ]
    if not include_total:
        row_labels.remove("جمع")

    data: dict[str, list[Any]] = {LABEL_COLUMN: row_labels}

    def add_period(hint: str, suffix: str, total: float | None) -> None:
        data[f"{hint} - مقدار"] = [None] * len(row_labels)
        data[f"تعداد فروش{suffix}"] = [None] * len(row_labels)
        amount_column = f"مبلغ فروش (میلیون ریال){suffix}"
        data[amount_column] = []
        for label in row_labels:
            if label == "جمع فروش داخلی" and total is not None:
                data[amount_column].append(total * 0.6)
            elif label == "جمع فروش صادراتی" and total is not None:
                data[amount_column].append(total * 0.4)
            elif label == "جمع" and total is not None:
                data[amount_column].append(total)
            elif label == "جمع برگشت از فروش":
                data[amount_column].append(0.0)
            else:
                data[amount_column].append(None)

    if include_prev_month:
        add_period(
            "دوره یک ماهه منتهی به 1405/06/31",
            "",
            prev_month,
        )
    add_period("دوره یک ماهه منتهی به 1405/07/30", "_2", current_month)
    add_period("از ابتدای سال مالی تا تاریخ 1405/07/30", "_3", ytd)
    add_period("از ابتدای سال مالی تا تاریخ 1404/07/30", "_4", prev_ytd)

    return pd.DataFrame(data)


def test_analyze_monthly_sales_extracts_periods() -> None:
    result = analyze_monthly_sales({PRODUCTION_TABLE: _monthly_frame()})

    assert result["table_key"] == PRODUCTION_TABLE
    assert result["period_end"] == "1405-07-30"
    assert result["current_month_sales"] == pytest.approx(1200.0)
    assert result["prev_month_sales"] is None
    assert result["ytd_sales"] == pytest.approx(5000.0)
    assert result["prev_ytd_sales"] == pytest.approx(4000.0)
    assert result["mom_change_pct"] is None
    assert result["yoy_change_pct"] == pytest.approx(25.0)

    summary = result["summary_df"]
    assert isinstance(summary, pd.DataFrame)
    assert list(summary[LABEL_COLUMN]) == [
        "جمع فروش داخلی",
        "جمع فروش صادراتی",
        "جمع",
        "جمع برگشت از فروش",
    ]


def test_analyze_monthly_sales_detects_previous_month_block() -> None:
    frame = _monthly_frame(include_prev_month=True)

    result = analyze_monthly_sales({PRODUCTION_TABLE: frame})

    assert result["current_month_sales"] == pytest.approx(1200.0)
    assert result["prev_month_sales"] == pytest.approx(1000.0)
    assert result["mom_change_pct"] == pytest.approx(20.0)
    assert result["yoy_change_pct"] == pytest.approx(25.0)


def test_analyze_monthly_sales_aggregates_category_rows() -> None:
    frame = _monthly_frame(include_total=False)

    result = analyze_monthly_sales({PRODUCTION_TABLE: frame})

    # No final ``جمع`` row: domestic + export categories are summed instead.
    assert result["current_month_sales"] == pytest.approx(1200.0)
    assert result["ytd_sales"] == pytest.approx(5000.0)
    assert result["prev_ytd_sales"] == pytest.approx(4000.0)


def test_analyze_monthly_sales_accepts_polars() -> None:
    frame = pl.from_pandas(_monthly_frame())

    result = analyze_monthly_sales({PRODUCTION_TABLE: frame})

    assert result["current_month_sales"] == pytest.approx(1200.0)
    assert result["ytd_sales"] == pytest.approx(5000.0)
    assert isinstance(result["summary_df"], pd.DataFrame)


def test_analyze_monthly_sales_falls_back_to_most_numeric_table() -> None:
    small = pd.DataFrame({"شرح": ["الف", "ب"], "مقدار": ["1", "2"]})
    tables = {"alpha": small, "beta": _monthly_frame()}

    result = analyze_monthly_sales(tables)

    assert result["table_key"] == "beta"
    assert result["current_month_sales"] == pytest.approx(1200.0)


def test_analyze_monthly_sales_rejects_empty_tables() -> None:
    with pytest.raises(ValueError):
        analyze_monthly_sales({})


def test_analyze_monthly_sales_rejects_non_frame() -> None:
    with pytest.raises(TypeError):
        analyze_monthly_sales({"table": [[1, 2], [3, 4]]})


def _letter(tracing_no: int, period: str, *, has_html: bool = True) -> CodalLetter:
    return CodalLetter(
        TracingNo=tracing_no,
        Title=f"گزارش فعالیت ماهانه دوره 1 ماهه منتهی به {period}",
        Url=f"https://codal.ir/Report/{tracing_no}",
        HasHtml=has_html,
    )


def _reports(*letters: CodalLetter) -> CodalSearchResponse:
    return CodalSearchResponse(Total=len(letters), Page=1, Letters=list(letters))


async def test_monthly_sales_summary_enriches_previous_month(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _letter(2, "1405/07/30")
    previous = _letter(1, "1405/06/31")
    reports = _reports(latest, previous)

    async def fake_reports(
        symbol: str, page: int = 1, *, client: Any = None
    ) -> CodalSearchResponse:
        return reports

    async def fake_fetch(
        letter: CodalLetter, *, client: Any = None, engine: str = "pandas"
    ) -> dict[str, Any]:
        if letter.TracingNo == 1:
            frame = _monthly_frame(current_month=1000.0, ytd=4000.0, prev_ytd=3000.0)
        else:
            frame = _monthly_frame()
        return {PRODUCTION_TABLE: frame}

    monkeypatch.setattr(monthly_module, "get_monthly_reports", fake_reports)
    monkeypatch.setattr(monthly_module, "fetch_and_parse_report", fake_fetch)

    result = await monthly_sales_summary("فولاد")

    assert result["symbol"] == "فولاد"
    assert result["tracing_no"] == 2
    assert result["prev_month_tracing_no"] == 1
    assert result["page"] == 1
    assert result["current_month_sales"] == pytest.approx(1200.0)
    assert result["prev_month_sales"] == pytest.approx(1000.0)
    assert result["mom_change_pct"] == pytest.approx(20.0)


async def test_monthly_sales_summary_without_html_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = _reports(_letter(1, "1405/07/30", has_html=False))

    async def fake_reports(
        symbol: str, page: int = 1, *, client: Any = None
    ) -> CodalSearchResponse:
        return reports

    monkeypatch.setattr(monthly_module, "get_monthly_reports", fake_reports)

    with pytest.raises(ParseError):
        await monthly_sales_summary("فولاد")


async def test_monthly_sales_summary_survives_previous_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _letter(2, "1405/07/30")
    previous = _letter(1, "1405/06/31")
    reports = _reports(latest, previous)

    async def fake_reports(
        symbol: str, page: int = 1, *, client: Any = None
    ) -> CodalSearchResponse:
        return reports

    async def fake_fetch(
        letter: CodalLetter, *, client: Any = None, engine: str = "pandas"
    ) -> dict[str, Any]:
        if letter.TracingNo == 1:
            raise NetworkError("previous report unavailable")
        return {PRODUCTION_TABLE: _monthly_frame()}

    monkeypatch.setattr(monthly_module, "get_monthly_reports", fake_reports)
    monkeypatch.setattr(monthly_module, "fetch_and_parse_report", fake_fetch)

    result = await monthly_sales_summary("علي")

    assert result["symbol"] == "علی"
    assert result["tracing_no"] == 2
    assert "prev_month_tracing_no" not in result
    assert result["prev_month_sales"] is None
