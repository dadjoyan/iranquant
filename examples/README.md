# Examples

Runnable scripts that use the public `iranquant` API against live TSETMC and
Codal servers. Install the package first (`pip install -e .`), then run:

```bash
python examples/history_and_quant.py
python examples/market_screener.py
python examples/codal_monthly_sales.py
```

Each script is a normal synchronous program; no event loop setup is required.
