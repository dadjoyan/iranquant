# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-31

Initial public release.

### Market data

- `search()` / `get_inscode()` for TSETMC symbol lookup with in-process caching.
- `history()` with backward adjustment modes `none`, `all` (dividends and
  capital increases) and `capital_only`.
- `client_type()` and `full_history()` for retail/institutional (حقیقی/حقوقی)
  volume, value and trade counts.
- Live `market_watch()`, five-level `order_book()` and `intraday_trades()`.
- `indices_live()` and `index_history()` for overall, equal-weight and sector
  indices.

### Codal

- `codal_search()`, `monthly_reports()` and `financial_statements()`.
- `codal_report()` / `parse_codal_report()` reconstruct every sheet and table
  from the report's `datasource` payload.
- `analyze_monthly_sales()` and `monthly_sales_summary()` extract current-month,
  year-to-date and prior-year sales with MoM/YoY growth.

### Quant analytics

- `compute_money_flow()` / `money_flow()` with buyer power and smart-money
  entry/exit signals.
- `compute_movement_power()` / `movement_power()` with signed Kaufman efficiency
  ratios across multiple windows.
- `detect_swing_legs()` for ZigZag swing-leg extraction.
- `MarketScreener` filters for buy queues, sell queues and positive tick surges.

### Streaming

- `MarketWatcher` emits `BUY_QUEUE_LOCKED`, `BUY_QUEUE_BROKEN`,
  `SELL_QUEUE_LOCKED`, `SELL_QUEUE_BROKEN` and `VOLUME_SPIKE` events, with
  optional market-hours gating via `is_market_open()`.

### CLI

- `iranquant search`, `history`, `screener`, `indices` and `codal`, including
  `codal --parse-latest` for report tables and a monthly sales summary.

### Infrastructure

- Async-first client with a safe synchronous facade for Jupyter/Colab.
- Pandas and Polars output for every data function.
- `ParquetCache` for incremental, date-aware persistence.
- Typed exception hierarchy and PEP 561 `py.typed` marker.
- MkDocs documentation, Ruff and mypy checks, offline and live test suites, and
  a GitHub Actions release workflow.
