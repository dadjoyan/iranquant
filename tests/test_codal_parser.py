"""Tests for Codal datasource extraction, table reconstruction and search."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from iranquant.codal.parser import (
    extract_datasource_json,
    fetch_and_parse_report,
    parse_codal_tables,
)
from iranquant.codal.search import (
    get_financial_statements,
    get_monthly_reports,
    search_codal,
)
from iranquant.core.client import AsyncHTTPClient
from iranquant.exceptions import ParseError
from iranquant.models.codal import CodalLetter

SEARCH_URL = "https://search.codal.ir/api/search/v2/q"


def _datasource() -> dict[str, Any]:
    return {
        "sheets": [
            {
                "title_Fa": "گزارش فعاليت ماهانه",
                "tables": [
                    {
                        "title_Fa": "توليد و فروش",
                        "cells": [
                            {
                                "rowSequence": 1,
                                "columnSequence": 1,
                                "value": "شرح محصول",
                                "rowType": "Header",
                                "isVisible": True,
                            },
                            {
                                "rowSequence": 1,
                                "columnSequence": 2,
                                "value": "مقدار",
                                "rowType": "Header",
                                "isVisible": True,
                            },
                            {
                                "rowSequence": 1,
                                "columnSequence": 3,
                                "value": "مبلغ",
                                "rowType": "Header",
                                "isVisible": True,
                            },
                            {
                                "rowSequence": 2,
                                "columnSequence": 1,
                                "value": "فولاد",
                                "isVisible": True,
                            },
                            {
                                "rowSequence": 2,
                                "columnSequence": 2,
                                "value": "۱٬۲۵۰",
                                "isVisible": True,
                            },
                            {
                                "rowSequence": 2,
                                "columnSequence": 3,
                                "value": "(1,200)",
                                "isVisible": True,
                            },
                            {
                                "rowSequence": 3,
                                "columnSequence": 1,
                                "value": "خودرو",
                                "isVisible": False,
                            },
                            {
                                "rowSequence": 3,
                                "columnSequence": 2,
                                "value": "5",
                                "isVisible": True,
                            },
                        ],
                    }
                ],
            }
        ]
    }


def _html(datasource: dict[str, Any]) -> str:
    return (
        "<html><script>var datasource = "
        + json.dumps(datasource, ensure_ascii=False)
        + ";</script></html>"
    )


def test_extract_datasource_primary_regex() -> None:
    datasource = _datasource()
    assert extract_datasource_json(_html(datasource)) == datasource


def test_extract_datasource_fallback_with_extra_js() -> None:
    datasource = _datasource()
    html = (
        "<script>var datasource = "
        + json.dumps(datasource, ensure_ascii=False)
        + ";\nvar somethingElse = 1;\n</script>"
    )
    assert extract_datasource_json(html) == datasource


def test_extract_datasource_handles_braces_inside_strings() -> None:
    datasource = {"sheets": [{"title_Fa": "a { brace }", "tables": []}]}
    html = _html(datasource)
    assert extract_datasource_json(html) == datasource


def test_extract_datasource_missing_raises() -> None:
    with pytest.raises(ParseError):
        extract_datasource_json("<html><script>var x = 1;</script></html>")


def test_parse_codal_tables_pandas() -> None:
    tables = parse_codal_tables(_datasource())

    assert list(tables) == ["گزارش فعالیت ماهانه - تولید و فروش"]
    frame = tables["گزارش فعالیت ماهانه - تولید و فروش"]

    assert list(frame.columns) == ["شرح محصول", "مقدار", "مبلغ"]
    assert frame.iloc[0]["شرح محصول"] == "فولاد"
    assert frame.iloc[0]["مقدار"] == 1250.0
    assert frame.iloc[0]["مبلغ"] == -1200.0
    # Invisible "خودرو" is removed but the visible numeric cell remains.
    assert frame.iloc[1]["مقدار"] == 5.0


def test_parse_codal_tables_polars() -> None:
    import polars as pl

    tables = parse_codal_tables(_datasource(), engine="polars")
    frame = tables["گزارش فعالیت ماهانه - تولید و فروش"]

    assert isinstance(frame, pl.DataFrame)
    assert frame.height == 2
    assert frame["مقدار"].to_list() == [1250.0, 5.0]
    assert frame["مبلغ"].to_list() == [-1200.0, None]


@respx.mock
async def test_fetch_and_parse_report(client: AsyncHTTPClient) -> None:
    datasource = _datasource()
    respx.get("https://codal.ir/Report/123").mock(
        return_value=httpx.Response(200, text=_html(datasource))
    )

    letter = CodalLetter(Title="گزارش", Url="/Report/123")
    tables = await fetch_and_parse_report(letter, client=client)

    assert "گزارش فعالیت ماهانه - تولید و فروش" in tables
    assert tables["گزارش فعالیت ماهانه - تولید و فروش"].iloc[0]["مبلغ"] == -1200.0


@respx.mock
async def test_search_codal_normalizes_and_resolves_urls(
    client: AsyncHTTPClient,
) -> None:
    payload = {
        "Total": 1,
        "Page": 1,
        "Letters": [
            {
                "TracingNo": 12345,
                "Symbol": "فولاد",
                "CompanyName": "فولاد مباركه",
                "Title": "گزارش فعاليت ماهانه",
                "LetterCode": "58",
                "SentDateTime": "2026-10-28 10:00",
                "PublishDateTime": "2026-10-28 10:05",
                "Url": "/Report/12345",
                "HasHtml": True,
                "HasExcel": True,
                "ExcelUrl": "/Excel/12345",
                "PdfUrl": "",
                "UnderSupervision": False,
            }
        ],
    }
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))

    response = await search_codal(symbol="فولاد", client=client)

    assert response.Total == 1
    letter = response.Letters[0]
    assert letter.Symbol == "فولاد"
    assert letter.Title == "گزارش فعالیت ماهانه"
    assert letter.Url == "https://codal.ir/Report/12345"
    assert letter.ExcelUrl == "https://codal.ir/Excel/12345"
    assert letter.PdfUrl == ""

    params = route.calls[0].request.url.params
    assert params["Symbol"] == "فولاد"
    assert params["LetterType"] == "-1"
    assert params["PageNumber"] == "1"
    assert params["Audited"] == "true"
    assert params["Publisher"] == "false"


@respx.mock
async def test_monthly_and_financial_helpers_use_letter_codes(
    client: AsyncHTTPClient,
) -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"Total": 0, "Page": 1, "Letters": []})
    )

    await get_monthly_reports("فولاد", client=client)
    assert route.calls[-1].request.url.params["LetterType"] == "58"

    await get_financial_statements("فولاد", page=2, client=client)
    assert route.calls[-1].request.url.params["LetterType"] == "6"
    assert route.calls[-1].request.url.params["PageNumber"] == "2"


def test_codal_letter_normalizes_persian_text() -> None:
    letter = CodalLetter(
        Symbol="علي",
        CompanyName="شركت نمونه",
        Title="گزارش فعاليت",
        Url="/Report/1",
    )
    assert letter.Symbol == "علی"
    assert letter.CompanyName == "شرکت نمونه"
    assert letter.Title == "گزارش فعالیت"


def test_parse_codal_tables_handles_null_titles() -> None:
    datasource = {
        "sheets": [
            {
                "title_Fa": None,
                "title_En": None,
                "tables": [
                    {
                        "title_Fa": None,
                        "title_En": None,
                        "cells": [
                            {
                                "rowSequence": 1,
                                "columnSequence": 1,
                                "value": "مقدار",
                                "isVisible": True,
                            },
                            {
                                "rowSequence": 2,
                                "columnSequence": 1,
                                "value": "10",
                                "isVisible": True,
                            },
                        ],
                    }
                ],
            }
        ]
    }

    tables = parse_codal_tables(datasource)

    assert list(tables) == ["sheet_1 - table_1"]
    frame = tables["sheet_1 - table_1"]
    assert frame.shape == (2, 1)
