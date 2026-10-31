"""Screen the live market for buy queues, sell queues and positive tick surges."""

from __future__ import annotations

import iranquant as iq


def main() -> None:
    """Run the three built-in screener filters over one MarketWatch snapshot."""

    market = iq.market_watch()
    screener = iq.MarketScreener(market)

    buy_queue = screener.filter_buy_queue()
    sell_queue = screener.filter_sell_queue()
    tick_surge = screener.filter_positive_tick_surge(min_diff_pct=1.5)

    print(f"symbols in snapshot: {len(market)}")
    print(f"buy queues:         {len(buy_queue)}")
    print(f"sell queues:        {len(sell_queue)}")
    print(f"positive surges:    {len(tick_surge)}")

    if len(buy_queue):
        print("\nLargest buy-queue candidates")
        print(buy_queue[["symbol", "close", "tmax"]].head())

    if len(tick_surge):
        print("\nPositive tick-surge candidates")
        print(tick_surge[["symbol", "close", "final_price"]].head())


if __name__ == "__main__":
    main()
