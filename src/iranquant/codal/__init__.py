"""Codal search and report-table parsing."""

from __future__ import annotations

from .monthly import analyze_monthly_sales, monthly_sales_summary
from .parser import (
    Engine,
    extract_datasource_json,
    fetch_and_parse_report,
    parse_codal_tables,
)
from .search import (
    INTERIM_FINANCIAL_LETTER,
    MONTHLY_ACTIVITY_LETTER,
    get_financial_statements,
    get_monthly_reports,
    resolve_codal_url,
    search_codal,
)

__all__ = [
    "Engine",
    "INTERIM_FINANCIAL_LETTER",
    "MONTHLY_ACTIVITY_LETTER",
    "analyze_monthly_sales",
    "extract_datasource_json",
    "fetch_and_parse_report",
    "get_financial_statements",
    "get_monthly_reports",
    "monthly_sales_summary",
    "parse_codal_tables",
    "resolve_codal_url",
    "search_codal",
]
