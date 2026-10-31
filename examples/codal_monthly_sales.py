"""Parse the newest Codal monthly report and summarize its sales."""

from __future__ import annotations

import iranquant as iq


def main() -> None:
    """Print the monthly report tables and the automated sales summary."""

    reports = iq.monthly_reports("فولاد")
    print(f"monthly reports on page: {len(reports)}")
    if len(reports) == 0:
        return

    letter = reports[0]
    print(f"latest: {letter.TracingNo} — {letter.Title}")

    tables = iq.parse_codal_report(letter)
    for name, frame in tables.items():
        print(f"{name}: {frame.shape[0]} x {frame.shape[1]}")

    summary = iq.analyze_monthly_sales(tables)
    print("\nSales summary (million Rials)")
    print(f"period:              {summary['period_end']}")
    print(f"current month:       {summary['current_month_sales']}")
    print(f"year to date:        {summary['ytd_sales']}")
    print(f"prior-year YTD:      {summary['prev_ytd_sales']}")
    print(f"YoY change:          {summary['yoy_change_pct']}%")
    print(f"MoM change:          {summary['mom_change_pct']}%")
    print(summary["summary_df"].head())

    enriched = iq.monthly_sales_summary("فولاد")
    print(f"\nMoM from previous report: {enriched['mom_change_pct']}%")


if __name__ == "__main__":
    main()
