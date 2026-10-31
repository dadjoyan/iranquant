"""Release-readiness checks for the installed package surface."""

from __future__ import annotations

import importlib
import importlib.metadata
from pathlib import Path

import pytest

import iranquant as iq

REQUIRED_EXPORTS = {
    "AsyncIranQuant",
    "IranQuant",
    "MarketScreener",
    "MarketWatcher",
    "NetworkError",
    "ParseError",
    "SymbolNotFoundError",
    "analyze_monthly_sales",
    "client_type",
    "compute_money_flow",
    "compute_movement_power",
    "detect_swing_legs",
    "financial_statements",
    "full_history",
    "get_inscode",
    "history",
    "index_history",
    "indices_live",
    "intraday_trades",
    "market_watch",
    "monthly_reports",
    "monthly_sales_summary",
    "order_book",
    "parse_codal_report",
    "search",
    "search_codal",
}

SUBPACKAGES = ("core", "models", "market", "codal", "quant", "stream")


def test_all_exports_resolve() -> None:
    missing = [name for name in iq.__all__ if not hasattr(iq, name)]
    assert missing == []


def test_required_public_exports_are_present() -> None:
    assert set(iq.__all__) >= REQUIRED_EXPORTS
    assert "IranQuantError" in iq.__all__


def test_version_matches_distribution_metadata() -> None:
    assert iq.__version__ == importlib.metadata.version("iranquant")


def test_py_typed_marker_ships_with_package() -> None:
    assert iq.__file__ is not None
    marker = Path(iq.__file__).parent / "py.typed"
    assert marker.is_file()


@pytest.mark.parametrize("subpackage", SUBPACKAGES)
def test_subpackages_have_public_exports(subpackage: str) -> None:
    module = importlib.import_module(f"iranquant.{subpackage}")
    assert module.__all__
