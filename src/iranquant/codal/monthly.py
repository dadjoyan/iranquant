"""Monthly Codal sales analysis (``LetterType=58``).

The analyzer understands the standard ``گزارش فعالیت ماهانه - تولید و فروش``
table: a product section followed by summary rows such as ``جمع فروش داخلی``,
``جمع فروش صادراتی``, ``جمع درآمد ارائه خدمات`` and a final ``جمع`` row.  It
returns the current-month, year-to-date and prior-year values in million Rials
(the unit used by Codal) together with Month-over-Month and Year-over-Year
growth percentages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
import polars as pl

from iranquant.codal.parser import fetch_and_parse_report
from iranquant.codal.search import get_monthly_reports
from iranquant.core.client import AsyncHTTPClient
from iranquant.core.normalizer import normalize_persian
from iranquant.exceptions import IranQuantError, ParseError

_DATE_PATTERN = re.compile(r"(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})")
_MONTH_HINTS = ("دوره یک ماهه", "دوره ۱ ماهه", "دوره ماه", "ماهه منتهی")
_YTD_HINTS = ("از ابتدای سال مالی", "ازابتدای سال مالی", "ابتدای سال مالی")
_PREV_MONTH_HINTS = ("ماه قبل", "دوره قبل", "ماه گذشته")
_PRIOR_YEAR_HINTS = ("سال قبل", "سال مالی قبل", "دوره سال قبل")
_SUMMARY_TOKENS = ("جمع", "مجموع")
_TOTAL_LABELS = {"جمع", "جمع کل", "مجموع", "مجموع کل", "جمع کل فروش"}
_CATEGORY_LABELS = (
    "جمع فروش داخلی",
    "جمع فروش صادراتی",
    "جمع درآمد ارائه خدمات",
)
_EXCLUDED_SUMMARY_HINTS = ("برگشت", "تخفیف")


@dataclass(slots=True)
class _Block:
    """A contiguous group of columns describing one reporting period."""

    start: int
    end: int
    kind: str
    date: tuple[int, int, int] | None
    corrected: bool
    label: str
    amount_index: int


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _label(value: Any) -> str:
    if _is_missing(value):
        return ""
    return normalize_persian(str(value)).strip()


def _to_number(value: Any) -> float | None:
    if _is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    text = (
        normalize_persian(value)
        .replace("٬", "")
        .replace(",", "")
        .replace("٫", ".")
        .replace("\u2212", "-")
        .strip()
    )
    if not text or text in {"-", "—", "_"}:
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    sign = -1.0 if negative else 1.0
    if text.startswith("+") or text.startswith("-"):
        if text[0] == "-":
            sign *= -1.0
        text = text[1:].strip()
    try:
        return sign * float(text)
    except ValueError:
        return None


def _as_pandas(frame: Any) -> pd.DataFrame:
    if isinstance(frame, pl.DataFrame):
        return frame.to_pandas()
    if isinstance(frame, pd.DataFrame):
        return frame
    raise TypeError(f"Expected a pandas or polars DataFrame, got {type(frame)!r}")


def _numeric_cells(frame: pd.DataFrame) -> int:
    total = 0
    for column in frame.columns[1:]:
        values = pd.to_numeric(frame[column], errors="coerce")
        total += int(values.notna().sum())
    return total


def _select_table(tables: dict[str, Any]) -> tuple[str, pd.DataFrame]:
    """Pick the production & sales table, falling back to the most numeric one."""

    candidates: list[tuple[float, str, pd.DataFrame]] = []
    for key, raw_frame in tables.items():
        frame = _as_pandas(raw_frame)
        if frame.empty or frame.shape[1] == 0:
            continue
        name = normalize_persian(str(key))
        score = float(_numeric_cells(frame))
        if "تولید و فروش" in name:
            score += 1_000_000.0
        elif "فروش" in name:
            score += 1_000.0
        candidates.append((score, str(key), frame))

    if not candidates:
        raise ValueError("No usable Codal tables were supplied")

    candidates.sort(key=lambda item: item[0], reverse=True)
    best = candidates[0]
    return best[1], best[2]


def _parse_date(label: str) -> tuple[int, int, int] | None:
    match = _DATE_PATTERN.search(label)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _build_blocks(frame: pd.DataFrame) -> list[_Block]:
    starts: list[tuple[int, str, tuple[int, int, int] | None, bool, str]] = []
    for index, column in enumerate(frame.columns):
        label = _label(column)
        kind: str | None = None
        if any(hint in label for hint in _MONTH_HINTS) or ("دوره" in label and "ماه" in label):
            kind = "month"
        elif any(hint in label for hint in _YTD_HINTS):
            kind = "ytd"
        elif "اصلاحات" in label:
            kind = "adjustment"
        if kind is None:
            continue
        corrected = "اصلاح شده" in label or "اصلاح‌شده" in label
        starts.append((index, kind, _parse_date(label), corrected, label))

    blocks: list[_Block] = []
    for position, (start, kind, date, corrected, label) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(frame.columns)
        amount_index = end - 1
        for column_index in range(start, end):
            column_label = _label(frame.columns[column_index])
            if "مبلغ" in column_label:
                amount_index = column_index
                if "فروش" in column_label or "درآمد" in column_label:
                    break
        blocks.append(
            _Block(
                start=start,
                end=end,
                kind=kind,
                date=date,
                corrected=corrected,
                label=label,
                amount_index=amount_index,
            )
        )
    return blocks


def _pick_period_blocks(
    blocks: list[_Block],
) -> tuple[_Block | None, _Block | None, _Block | None, _Block | None]:
    """Return ``(current_month, previous_month, current_ytd, previous_ytd)``."""

    month_blocks = [block for block in blocks if block.kind == "month"]
    ytd_blocks = [block for block in blocks if block.kind == "ytd"]

    current_month: _Block | None = None
    if month_blocks:
        current_month = max(
            month_blocks,
            key=lambda block: (block.date or (0, 0, 0), block.start),
        )

    previous_month: _Block | None = None
    previous_hint = next(
        (block for block in month_blocks if any(hint in block.label for hint in _PREV_MONTH_HINTS)),
        None,
    )
    if previous_hint is not None:
        previous_month = previous_hint
    elif current_month is not None and current_month.date is not None:
        earlier = [
            block
            for block in month_blocks
            if block.date is not None and block.date < current_month.date
        ]
        if earlier:
            previous_month = max(earlier, key=lambda block: block.date or (0, 0, 0))

    report_date = max(
        (block.date for block in blocks if block.date is not None),
        default=None,
    )

    current_ytd: _Block | None = None
    previous_ytd: _Block | None = None
    if report_date is not None:
        same_period = [block for block in ytd_blocks if block.date == report_date]
        if same_period:
            current_ytd = max(
                same_period,
                key=lambda block: (not block.corrected, block.start),
            )
        earlier_year = [
            block
            for block in ytd_blocks
            if block.date is not None and block.date[0] < report_date[0]
        ]
        if earlier_year:
            previous_ytd = max(
                earlier_year,
                key=lambda block: (block.date or (0, 0, 0), block.start),
            )
    else:
        usable = [block for block in ytd_blocks if not block.corrected] or ytd_blocks
        if usable:
            current_ytd = max(usable, key=lambda block: block.start)
        previous_ytd = next(
            (
                block
                for block in ytd_blocks
                if any(hint in block.label for hint in _PRIOR_YEAR_HINTS)
            ),
            None,
        )

    return current_month, previous_month, current_ytd, previous_ytd


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def _row_value(frame: pd.DataFrame, row: int, column: int) -> float | None:
    if row < 0 or column < 0 or row >= frame.shape[0] or column >= frame.shape[1]:
        return None
    return _to_number(frame.iat[row, column])


def _pick_total_row(labels: pd.Series) -> int | None:
    exact = labels[labels.isin(_TOTAL_LABELS)]
    if not exact.empty:
        index = int(str(exact.index[0]))
        return index

    candidates = [
        int(str(index))
        for index, label in labels.items()
        if any(token in label for token in _SUMMARY_TOKENS)
        and label not in _CATEGORY_LABELS
        and not any(hint in label for hint in _EXCLUDED_SUMMARY_HINTS)
    ]
    return candidates[-1] if candidates else None


def _aggregate(
    frame: pd.DataFrame,
    total_row: int | None,
    category_rows: list[int],
    column: int,
) -> float | None:
    if total_row is not None:
        value = _row_value(frame, total_row, column)
        if value is not None:
            return value
    values = [
        value
        for value in (_row_value(frame, row, column) for row in category_rows)
        if value is not None
    ]
    return float(sum(values)) if values else None


def analyze_monthly_sales(tables: dict[str, Any]) -> dict[str, Any]:
    """Summarize a monthly activity report's production & sales table.

    The main table is the one whose key contains ``"تولید و فروش"``; if no such
    key exists, the table with the most numeric cells is used.  Summary rows are
    detected by normalized ``جمع``/``مجموع`` labels.  A final ``جمع`` row is
    preferred; when it is absent, the ``جمع فروش داخلی``, ``جمع فروش صادراتی``
    and ``جمع درآمد ارائه خدمات`` rows are added together.

    Values are reported in **million Rials**, the unit printed in the source
    table.  ``prev_month_sales`` is only populated when the same table exposes a
    separate previous-month period; :func:`monthly_sales_summary` fills it by
    parsing the previous monthly letter.

    Args:
        tables: The mapping returned by
            :func:`~iranquant.codal.parser.fetch_and_parse_report` (or
            :func:`~iranquant.codal.parser.parse_codal_tables`), with Pandas or
            Polars DataFrames as values.

    Returns:
        A dictionary with:

        * ``table_key`` (``str``) — key of the selected table.
        * ``period_end`` (``str | None``) — latest reporting date in the table.
        * ``current_month_sales`` (``float | None``) — one-month sales amount.
        * ``prev_month_sales`` (``float | None``) — previous standalone month,
            when present in the same table.
        * ``ytd_sales`` (``float | None``) — year-to-date sales amount.
        * ``prev_ytd_sales`` (``float | None``) — prior-year YTD sales amount.
        * ``mom_change_pct`` (``float | None``) — month-over-month percentage.
        * ``yoy_change_pct`` (``float | None``) — year-over-year percentage.
        * ``summary_df`` (``pandas.DataFrame``) — filtered summary rows.

    Raises:
        ValueError: If ``tables`` is empty or contains no usable table.
        TypeError: If a table value is not a Pandas or Polars DataFrame.

    Examples:
        >>> letter = iq.monthly_reports("فولاد").Letters[0]
        >>> tables = iq.parse_codal_report(letter)
        >>> summary = iq.analyze_monthly_sales(tables)
        >>> summary["current_month_sales"]
        662639489.0
        >>> summary["summary_df"][["شرح - نام محصول"]]
    """

    table_key, frame = _select_table(tables)
    label_column = frame.columns[0]
    labels = frame[label_column].map(_label)

    summary_mask = labels.str.contains("جمع|مجموع", regex=True, na=False)
    summary_df = frame.loc[summary_mask].copy().reset_index(drop=True)

    total_row = _pick_total_row(labels)
    category_rows = [int(str(index)) for index in labels[labels.isin(_CATEGORY_LABELS)].index]

    blocks = _build_blocks(frame)
    current_month, previous_month, current_ytd, previous_ytd = _pick_period_blocks(blocks)

    current_month_sales = (
        _aggregate(frame, total_row, category_rows, current_month.amount_index)
        if current_month is not None
        else None
    )
    prev_month_sales = (
        _aggregate(frame, total_row, category_rows, previous_month.amount_index)
        if previous_month is not None
        else None
    )
    ytd_sales = (
        _aggregate(frame, total_row, category_rows, current_ytd.amount_index)
        if current_ytd is not None
        else None
    )
    prev_ytd_sales = (
        _aggregate(frame, total_row, category_rows, previous_ytd.amount_index)
        if previous_ytd is not None
        else None
    )

    period_end = None
    if current_month is not None and current_month.date is not None:
        period_end = "{:04d}-{:02d}-{:02d}".format(*current_month.date)
    elif current_ytd is not None and current_ytd.date is not None:
        period_end = "{:04d}-{:02d}-{:02d}".format(*current_ytd.date)

    return {
        "table_key": table_key,
        "period_end": period_end,
        "current_month_sales": current_month_sales,
        "prev_month_sales": prev_month_sales,
        "ytd_sales": ytd_sales,
        "prev_ytd_sales": prev_ytd_sales,
        "mom_change_pct": _pct_change(current_month_sales, prev_month_sales),
        "yoy_change_pct": _pct_change(ytd_sales, prev_ytd_sales),
        "summary_df": summary_df,
    }


def _letter_period(letter: Any) -> tuple[int, int, int] | None:
    return _parse_date(normalize_persian(str(getattr(letter, "Title", "") or "")))


async def monthly_sales_summary(
    symbol: str,
    page: int = 1,
    *,
    client: AsyncHTTPClient | None = None,
) -> dict[str, Any]:
    """Fetch and analyze a symbol's latest monthly sales report.

    The newest report on ``page`` is parsed, and its previous distinct monthly
    period is parsed too so a true Month-over-Month comparison can be computed.

    Args:
        symbol: Persian ticker, e.g. ``"فولاد"``.
        page: One-based Codal result page.  Defaults to ``1``.
        client: Optional shared :class:`~iranquant.core.client.AsyncHTTPClient`.

    Returns:
        The dictionary from :func:`analyze_monthly_sales` plus
        ``symbol``, ``tracing_no``, ``title``, ``prev_month_tracing_no`` and a
        ``mom_change_pct`` derived from the previous month's report.

    Raises:
        ParseError: If Codal returns no HTML monthly reports.
        NetworkError: If Codal or the report page cannot be fetched.
        ValueError: If the latest report has no usable tables.

    Examples:
        >>> summary = await monthly_sales_summary("فولاد")
        >>> summary["current_month_sales"]
        >>> summary["mom_change_pct"]
    """

    reports = await get_monthly_reports(symbol, page=page, client=client)
    letters = [letter for letter in reports.Letters if letter.HasHtml and letter.Url]
    if not letters:
        raise ParseError(f"No HTML monthly report found for {symbol!r}")

    def sort_key(letter: Any) -> tuple[tuple[int, int, int], int]:
        period = _letter_period(letter) or (0, 0, 0)
        return period, int(getattr(letter, "TracingNo", 0) or 0)

    ordered = sorted(letters, key=sort_key, reverse=True)
    latest = ordered[0]
    latest_period = _letter_period(latest)

    tables = await fetch_and_parse_report(latest, client=client)
    result = analyze_monthly_sales(tables)
    result.update(
        {
            "symbol": normalize_persian(symbol),
            "tracing_no": latest.TracingNo,
            "title": latest.Title,
            "page": page,
        }
    )

    previous = next(
        (
            letter
            for letter in ordered[1:]
            if latest_period is None or (_letter_period(letter) or (0, 0, 0)) < latest_period
        ),
        None,
    )
    if previous is None:
        return result

    try:
        previous_tables = await fetch_and_parse_report(previous, client=client)
        previous_result = analyze_monthly_sales(previous_tables)
    except (IranQuantError, ValueError, TypeError):
        return result

    previous_sales = previous_result.get("current_month_sales")
    if isinstance(previous_sales, (int, float)):
        result["prev_month_sales"] = float(previous_sales)
        result["prev_month_tracing_no"] = previous.TracingNo
        current_sales = result.get("current_month_sales")
        result["mom_change_pct"] = _pct_change(
            float(current_sales) if isinstance(current_sales, (int, float)) else None,
            float(previous_sales),
        )
    return result


__all__ = ["analyze_monthly_sales", "monthly_sales_summary"]
