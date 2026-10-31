"""Tests for ergonomic APIs, normalization and flexible signatures."""

from __future__ import annotations

from typing import Any, cast

import httpx
import pandas as pd
import polars as pl
import pytest
import respx

import iranquant as iq
from iranquant.core.client import AsyncHTTPClient
from iranquant.market.index import get_indices_live
from iranquant.market.live import get_market_watch
from iranquant.market.symbol import instrument_to_dict
from iranquant.models.codal import CodalLetter, CodalSearchResponse
from iranquant.models.tsetmc import InstrumentSearchItem
from iranquant.quant import MarketScreener

SYMBOL = "فولاد"


def _letter(tracing_no: int, symbol: str = SYMBOL) -> CodalLetter:
    return CodalLetter(
        TracingNo=tracing_no,
        Symbol=symbol,
        Title="گزارش فعالیت ماهانه",
        Url=f"https://codal.ir/Report/{tracing_no}",
        HasHtml=True,
    )


def test_codal_search_response_container_ergonomics() -> None:
    first = _letter(1)
    second = _letter(2)
    response = CodalSearchResponse(Total=7, Page=2, Letters=[first, second])

    assert len(response) == 2
    assert list(response) == [first, second]
    assert response[0] is first
    assert response[1:] == [second]
    assert response.total == 7
    assert response.page == 2
    assert response.letters == [first, second]


def test_codal_search_response_to_dataframe_pandas() -> None:
    response = CodalSearchResponse(Letters=[_letter(1), _letter(2)])

    frame = response.to_dataframe()

    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == list(CodalLetter.model_fields)
    assert list(frame["TracingNo"]) == [1, 2]
    assert list(frame["Symbol"]) == [SYMBOL, SYMBOL]


def test_codal_search_response_to_dataframe_polars() -> None:
    response = CodalSearchResponse(Letters=[_letter(1)])

    frame = response.to_dataframe("polars")

    assert isinstance(frame, pl.DataFrame)
    assert frame.height == 1
    assert frame["TracingNo"].to_list() == [1]

    empty = CodalSearchResponse().to_dataframe("polars")
    assert empty.height == 0
    assert list(empty.columns) == list(CodalLetter.model_fields)


def test_codal_search_response_rejects_unknown_engine() -> None:
    with pytest.raises(ValueError):
        CodalSearchResponse().to_dataframe("numpy")  # type: ignore[arg-type]


def _market_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ins_code": "1",
                "symbol": "A",
                "close": 500.0,
                "final_price": 490.0,
                "tmax": 500.0,
                "tmin": 450.0,
                "order_book": [{"zmd": 1000.0, "zmo": 0.0}],
            },
            {
                "ins_code": "2",
                "symbol": "B",
                "close": 450.0,
                "final_price": 460.0,
                "tmax": 500.0,
                "tmin": 450.0,
                "order_book": [{"zmd": 0.0, "zmo": 2000.0}],
            },
        ]
    )


def test_market_screener_accepts_constructor_or_filter_frame() -> None:
    frame = _market_frame()

    bound = cast(pd.DataFrame, MarketScreener(frame).filter_buy_queue())
    passed = cast(pd.DataFrame, MarketScreener().filter_buy_queue(frame))

    pd.testing.assert_frame_equal(bound.reset_index(drop=True), passed.reset_index(drop=True))
    assert list(passed["symbol"]) == ["A"]


def test_market_screener_requires_a_frame() -> None:
    with pytest.raises(ValueError):
        MarketScreener().filter_buy_queue()


def test_tsetmc_models_normalize_persian_text() -> None:
    item = InstrumentSearchItem(
        insCode="1",
        lVal18AFC="علي",
        lVal30="شركت نمونه",
    )

    assert item.lVal18AFC == "علی"
    assert item.lVal30 == "شرکت نمونه"

    record = instrument_to_dict(item)
    assert record["symbol"] == "علی"
    assert record["company_name"] == "شرکت نمونه"


@respx.mock
async def test_market_watch_normalizes_symbol_and_company(
    client: AsyncHTTPClient,
) -> None:
    respx.get(url__startswith="https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch").mock(
        return_value=httpx.Response(
            200,
            json={"marketwatch": [{"insCode": "1", "lva": "علي", "lvc": "شركت"}]},
        )
    )

    frame = cast(pd.DataFrame, await get_market_watch(client))

    assert frame.iloc[0]["symbol"] == "علی"
    assert frame.iloc[0]["company_name"] == "شرکت"


@respx.mock
async def test_indices_live_normalizes_names(client: AsyncHTTPClient) -> None:
    respx.get(url__startswith="https://cdn.tsetmc.com/api/MarketData/GetMarketOverview/").mock(
        return_value=httpx.Response(
            200,
            json={
                "marketOverview": {
                    "indexLastValue": 1.0,
                    "indexChange": 0.0,
                    "marketState": "F",
                    "marketStateTitle": "بسته",
                }
            },
        )
    )
    respx.get(url__startswith="https://cdn.tsetmc.com/api/Index/GetIndexB1LastAll/").mock(
        return_value=httpx.Response(
            200,
            json={
                "indexB1": [
                    {
                        "insCode": "9",
                        "dEven": 0,
                        "hEven": 123000,
                        "xDrNivJIdx004": 2.0,
                        "xVarIdxJRfV": 0.0,
                        "lVal30": "شركت نمونه",
                    }
                ]
            },
        )
    )

    frame = cast(pd.DataFrame, await get_indices_live(client))

    sector = frame.loc[frame["index_code"] == "9"].iloc[0]
    assert sector["name"] == "شرکت نمونه"


def test_top_level_exports_and_facades() -> None:
    for name in (
        "analyze_monthly_sales",
        "monthly_sales_summary",
        "parse_codal_report",
    ):
        assert callable(getattr(iq, name))

    for name in (
        "parse_codal_report",
        "monthly_sales_summary",
    ):
        assert hasattr(iq.IranQuant, name)
        assert hasattr(iq.AsyncIranQuant, name)


def test_sync_parse_codal_report_uses_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_fetch(
        url_or_letter: str | CodalLetter,
        *,
        engine: str = "pandas",
        client: Any = None,
    ) -> dict[str, Any]:
        captured["url"] = url_or_letter
        captured["engine"] = engine
        captured["client"] = client
        return {"table": pd.DataFrame({"a": [1]})}

    monkeypatch.setattr(iq, "fetch_and_parse_report", fake_fetch)

    tables = iq.parse_codal_report("https://codal.ir/Report/1", engine="polars")

    assert captured["url"] == "https://codal.ir/Report/1"
    assert captured["engine"] == "polars"
    assert captured["client"] is not None
    assert "table" in tables


def test_sync_monthly_sales_summary_uses_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_summary(
        symbol: str,
        page: int = 1,
        *,
        client: Any = None,
    ) -> dict[str, Any]:
        captured["symbol"] = symbol
        captured["page"] = page
        captured["client"] = client
        return {"symbol": symbol, "page": page}

    monkeypatch.setattr(iq, "_monthly_sales_summary", fake_summary)

    result = iq.monthly_sales_summary("فولاد", page=2)

    assert captured["symbol"] == "فولاد"
    assert captured["page"] == 2
    assert captured["client"] is not None
    assert result == {"symbol": "فولاد", "page": 2}
