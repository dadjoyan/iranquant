"""Adjusted history, money flow, movement power and swing legs for one symbol."""

from __future__ import annotations

import iranquant as iq


def main() -> None:
    """Print recent analytics for Foolad (فولاد)."""

    daily = iq.history("فولاد", adjust="all")
    print("Recent daily history")
    print(daily.tail(3)[["jdate", "close", "final_price", "volume"]])

    flow = iq.money_flow("فولاد")
    print("\nSmart-money flow")
    print(flow.tail(3)[["jdate", "buyer_power_ratio", "smart_money_entry", "smart_money_exit"]])

    power = iq.movement_power("فولاد")
    print("\nMovement power")
    print(power.tail(3)[["jdate", "er_5", "er_20", "er_60", "movement_power_score"]])

    legs = iq.detect_swing_legs(iq.full_history("فولاد"), reversal_pct=5.0)
    print("\nSwing legs")
    print(legs.tail(3)[["direction", "start_date", "end_date", "leg_return_pct"]])


if __name__ == "__main__":
    main()
