"""Shared fixtures for the iranquant test suite."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from iranquant.core.client import AsyncHTTPClient
from iranquant.market import symbol as symbol_module


@pytest.fixture(autouse=True)
def _clear_symbol_caches() -> Iterator[None]:
    symbol_module.clear_cache()
    yield
    symbol_module.clear_cache()


@pytest.fixture
async def client() -> AsyncIterator[AsyncHTTPClient]:
    http = AsyncHTTPClient(timeout=5.0, max_retries=2, retry_wait=0)
    try:
        yield http
    finally:
        await http.aclose()


def search_payload() -> dict[str, Any]:
    return {
        "instrumentSearch": [
            {
                "insCode": "46348559193224090",
                "lVal18AFC": "فولاد",
                "lVal30": "فولاد مباركه اصفهان",
                "cIsin": "IRO1FOLD0001",
                "zTitad": 1130000000000.0,
                "flow": 1,
                "cgrValCot": "N1",
                "lastDate": 20261028,
                "faraDesc": "",
            }
        ]
    }


def daily_payload() -> dict[str, Any]:
    return {
        "closingPriceDaily": [
            {
                "insCode": "46348559193224090",
                "dEven": 20261027,
                "hEven": 123000,
                "pClosing": 4100.0,
                "iClose": False,
                "yClose": False,
                "pDrCotVal": 4105.0,
                "zTotTran": 3000.0,
                "qTotTran5J": 50000000.0,
                "qTotCap": 200000000000.0,
                "priceChange": 20.0,
                "priceMin": 4050.0,
                "priceMax": 4120.0,
                "priceYesterday": 4080.0,
                "priceFirst": 4090.0,
                "last": False,
                "id": 2,
            },
            {
                "insCode": "46348559193224090",
                "dEven": 20261028,
                "hEven": 123000,
                "pClosing": 4120.0,
                "iClose": False,
                "yClose": False,
                "pDrCotVal": 4150.0,
                "zTotTran": 4521.0,
                "qTotTran5J": 98500120.0,
                "qTotCap": 406512000000.0,
                "priceChange": 50.0,
                "priceMin": 4080.0,
                "priceMax": 4180.0,
                "priceYesterday": 4100.0,
                "priceFirst": 4110.0,
                "last": False,
                "id": 1,
            },
            {
                # Non-trading placeholder that must be filtered out.
                "insCode": "46348559193224090",
                "dEven": 20261029,
                "hEven": 123000,
                "pClosing": 0.0,
                "iClose": True,
                "yClose": True,
                "pDrCotVal": 0.0,
                "zTotTran": 0.0,
                "qTotTran5J": 0.0,
                "qTotCap": 0.0,
                "priceChange": 0.0,
                "priceMin": 0.0,
                "priceMax": 0.0,
                "priceYesterday": 0.0,
                "priceFirst": 0.0,
                "last": True,
                "id": 0,
            },
        ]
    }


def client_type_payload() -> dict[str, Any]:
    return {
        "clientType": [
            {
                "recDate": 20261027,
                "insCode": "46348559193224090",
                "buy_I_Volume": 60000000.0,
                "buy_N_Volume": 33500120.0,
                "buy_I_Value": 268000000000.0,
                "buy_N_Value": 138512000000.0,
                "buy_N_Count": 12,
                "buy_I_Count": 2450,
                "sell_I_Volume": 40000000.0,
                "sell_N_Volume": 53500120.0,
                "sell_I_Value": 185000000000.0,
                "sell_N_Value": 221512000000.0,
                "sell_N_Count": 8,
                "sell_I_Count": 3100,
            },
            {
                "recDate": 20261028,
                "insCode": "46348559193224090",
                "buy_I_Volume": 65000000.0,
                "buy_N_Volume": 33500120.0,
                "buy_I_Value": 268000000000.0,
                "buy_N_Value": 138512000000.0,
                "buy_N_Count": 12,
                "buy_I_Count": 2450,
                "sell_I_Volume": 45000000.0,
                "sell_N_Volume": 53500120.0,
                "sell_I_Value": 185000000000.0,
                "sell_N_Value": 221512000000.0,
                "sell_N_Count": 8,
                "sell_I_Count": 3100,
            },
        ]
    }


def market_watch_payload() -> dict[str, Any]:
    return {
        "marketwatch": [
            {
                "insCode": "46348559193224090",
                "lva": "فولاد",
                "lvc": "فولاد مباركه اصفهان",
                "eps": 650.0,
                "pe": "6.3",
                "pmd": 4110.0,
                "pmo": 4120.0,
                "pcl": 4120.0,
                "pdv": 4150.0,
                "pvc": None,
                "py": 4100.0,
                "pmn": 4080.0,
                "pmx": 4180.0,
                "pf": 4110.0,
                "ztt": 4521.0,
                "qtt": None,
                "qtj": 98500120.0,
                "qtc": 406512000000.0,
                "bv": 100000.0,
                "ps": None,
                "pi": None,
                "pMax": 4428.0,
                "pMin": 3772.0,
                "ztd": 1130000000000.0,
                "blDs": [
                    {
                        "n": 1,
                        "qmd": 15,
                        "zmd": 500000,
                        "pmd": 4140.0,
                        "pmo": 4150.0,
                        "zmo": 300000,
                        "qmo": 10,
                    }
                ],
            }
        ]
    }


def share_change_payload() -> dict[str, Any]:
    return {
        "instrumentShareChange": [
            {
                "dEven": 20261028,
                "numberOfShareNew": 2000000000000.0,
                "numberOfShareOld": 1000000000000.0,
            }
        ]
    }


@pytest.fixture
def payloads() -> dict[str, dict[str, Any]]:
    return {
        "search": copy.deepcopy(search_payload()),
        "daily": copy.deepcopy(daily_payload()),
        "client_type": copy.deepcopy(client_type_payload()),
        "market_watch": copy.deepcopy(market_watch_payload()),
        "share_change": copy.deepcopy(share_change_payload()),
    }
