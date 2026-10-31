"""Tests for symbol search and insCode resolution."""

from __future__ import annotations

import httpx
import pytest
import respx

from iranquant.core.client import AsyncHTTPClient
from iranquant.exceptions import SymbolNotFoundError
from iranquant.market.symbol import get_inscode, instrument_to_dict, search_symbol
from iranquant.models.tsetmc import InstrumentSearchItem

SEARCH_PREFIX = "https://cdn.tsetmc.com/api/Instrument/GetInstrumentSearch/"


@respx.mock
async def test_search_symbol(
    client: AsyncHTTPClient, payloads: dict[str, dict[str, object]]
) -> None:
    respx.get(url__startswith=SEARCH_PREFIX).mock(
        return_value=httpx.Response(200, json=payloads["search"])
    )

    items = await search_symbol(client, "فولاد")

    assert len(items) == 1
    assert items[0].insCode == "46348559193224090"
    assert items[0].lVal18AFC == "فولاد"
    assert items[0].zTitad == 1130000000000.0


@pytest.mark.parametrize("last_date", [None, 0, 1, 18991231])
def test_instrument_to_dict_handles_sentinel_last_date(
    last_date: int | None,
) -> None:
    item = InstrumentSearchItem(
        insCode="777",
        lVal18AFC="فولاد",
        lastDate=last_date,
    )

    record = instrument_to_dict(item)

    assert record["last_date"] is None
    assert record["last_date_jdate"] is None


def test_instrument_to_dict_converts_valid_last_date() -> None:
    item = InstrumentSearchItem(
        insCode="777",
        lVal18AFC="فولاد",
        lastDate=20261028,
    )

    record = instrument_to_dict(item)

    assert record["last_date"].isoformat() == "2026-10-28"
    assert record["last_date_jdate"] == "1405-08-06"


@respx.mock
async def test_get_inscode_prefers_active_flow(client: AsyncHTTPClient) -> None:
    payload = {
        "instrumentSearch": [
            {
                "insCode": "999",
                "lVal18AFC": "فولاد",
                "flow": 5,
                "cgrValCot": "N1",
                "lastDate": 20261030,
            },
            {
                "insCode": "111",
                "lVal18AFC": "فولاد",
                "flow": 1,
                "cgrValCot": "N1",
                "lastDate": 20261020,
            },
        ]
    }
    respx.get(url__startswith=SEARCH_PREFIX).mock(return_value=httpx.Response(200, json=payload))

    assert await get_inscode(client, "فولاد") == "111"


@respx.mock
async def test_get_inscode_breaks_ties_by_last_date(client: AsyncHTTPClient) -> None:
    payload = {
        "instrumentSearch": [
            {
                "insCode": "222",
                "lVal18AFC": "فولاد",
                "flow": 1,
                "cgrValCot": "N1",
                "lastDate": 20261020,
            },
            {
                "insCode": "333",
                "lVal18AFC": "فولاد",
                "flow": 2,
                "cgrValCot": "N2",
                "lastDate": 20261029,
            },
        ]
    }
    respx.get(url__startswith=SEARCH_PREFIX).mock(return_value=httpx.Response(200, json=payload))

    assert await get_inscode(client, "فولاد") == "333"


@respx.mock
async def test_get_inscode_matches_normalized_names(client: AsyncHTTPClient) -> None:
    payload = {
        "instrumentSearch": [
            {
                "insCode": "444",
                "lVal18AFC": "علي",  # Arabic yeh
                "flow": 1,
                "cgrValCot": "N1",
                "lastDate": 20261028,
            }
        ]
    }
    respx.get(url__startswith=SEARCH_PREFIX).mock(return_value=httpx.Response(200, json=payload))

    assert await get_inscode(client, "علی") == "444"


@respx.mock
async def test_get_inscode_is_cached(client: AsyncHTTPClient) -> None:
    route = respx.get(url__startswith=SEARCH_PREFIX).mock(
        return_value=httpx.Response(
            200,
            json={
                "instrumentSearch": [
                    {
                        "insCode": "555",
                        "lVal18AFC": "فولاد",
                        "flow": 1,
                        "cgrValCot": "N1",
                        "lastDate": 20261028,
                    }
                ]
            },
        )
    )

    assert await get_inscode(client, "فولاد") == "555"
    assert await get_inscode(client, "فولاد") == "555"
    assert route.call_count == 1


@respx.mock
async def test_get_inscode_raises_when_missing(client: AsyncHTTPClient) -> None:
    respx.get(url__startswith=SEARCH_PREFIX).mock(
        return_value=httpx.Response(
            200,
            json={"instrumentSearch": [{"insCode": "1", "lVal18AFC": "خودرو", "flow": 1}]},
        )
    )

    with pytest.raises(SymbolNotFoundError):
        await get_inscode(client, "فولاد")
