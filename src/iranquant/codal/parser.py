"""Extract and reconstruct Codal ``var datasource`` report tables."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, cast

import pandas as pd
import polars as pl

from iranquant.codal.search import resolve_codal_url
from iranquant.core.client import AsyncHTTPClient
from iranquant.core.normalizer import normalize_persian
from iranquant.exceptions import ParseError
from iranquant.models.codal import CodalDatasource, CodalLetter

Engine = Literal["pandas", "polars"]

_DATASOURCE_PATTERN = re.compile(
    r"var\s+datasource\s*=\s*(\{.*?\})\s*;\s*</script>",
    re.DOTALL,
)
_DATASOURCE_MARKER = re.compile(r"var\s+datasource\s*=", re.DOTALL)
_HEADER_HINTS = ("header", "هدر", "سرستون", "سر ستون", "عنوان", "title")
_NUMBER_PATTERN = re.compile(r"(?:\d+(?:\.\d+)?|\.\d+)")


def _extract_balanced_object(html: str) -> str | None:
    marker = _DATASOURCE_MARKER.search(html)
    if marker is None:
        return None
    start = html.find("{", marker.end())
    if start == -1:
        return None

    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in ('"', "'"):
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start : index + 1]
    return None


def extract_datasource_json(html: str) -> dict[str, Any]:
    """Extract the ``var datasource = {...}`` JSON object from a report page.

    A fast regex is tried first; if the JSON contains nested braces inside
    strings or is followed by more JavaScript, a brace-balanced scanner is used
    as a fallback.  Braces inside quoted strings are handled correctly.

    Args:
        html: The full HTML source of a Codal report page.

    Returns:
        The decoded datasource object, usually containing a ``"sheets"`` list.

    Raises:
        ParseError: If no ``var datasource`` block is found, the block is not
            valid JSON, or it decodes to something other than an object.

    Examples:
        >>> html = '<script>var datasource = {"sheets": []};</script>'
        >>> extract_datasource_json(html)
        {'sheets': []}
    """

    match = _DATASOURCE_PATTERN.search(html)
    raw = match.group(1) if match is not None else _extract_balanced_object(html)
    if raw is None:
        raise ParseError("No 'var datasource' object found in Codal report")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError("Malformed Codal datasource JSON") from exc
    if not isinstance(parsed, dict):
        raise ParseError("Codal datasource is not a JSON object")
    return cast(dict[str, Any], parsed)


def _special_to_ascii(text: str) -> str:
    return (
        normalize_persian(text)
        .replace("٬", "")
        .replace(",", "")
        .replace("٫", ".")
        .replace("\u2212", "-")
        .strip()
    )


def _parse_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    text = _special_to_ascii(value)
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()

    sign = -1.0 if negative else 1.0
    if text.startswith("+") or text.startswith("-"):
        if text[0] == "-":
            sign *= -1.0
        text = text[1:].strip()

    if not _NUMBER_PATTERN.fullmatch(text):
        return None
    return sign * float(text)


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = normalize_persian(value).strip()
        if not normalized:
            return None
        parsed = _parse_number(normalized)
        return parsed if parsed is not None else normalized
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _label(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _dedupe_columns(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    output: list[str] = []
    for index, label in enumerate(labels):
        base = label.strip() or f"column_{index + 1}"
        if base not in seen:
            seen[base] = 1
            output.append(base)
        else:
            seen[base] += 1
            output.append(f"{base}_{seen[base]}")
    return output


def _normalize_column(column: list[Any]) -> list[Any]:
    has_number = any(_is_number(value) for value in column)
    has_text = any(isinstance(value, str) for value in column)
    if has_number and has_text:
        return [None if value is None else _label(value) for value in column]
    return column


def _row_looks_like_header(values: list[Any]) -> bool:
    non_empty = [value for value in values if value not in (None, "")]
    if len(non_empty) < 2:
        return False
    return not any(_is_number(value) for value in non_empty)


def _reconstruct_table(table: Any) -> tuple[list[str], list[list[Any]]]:
    raw_rows: dict[int, dict[int, Any]] = {}
    row_hints: dict[int, set[str]] = {}

    for cell in table.cells:
        if not cell.isVisible:
            continue
        row = int(cell.rowSequence or 0)
        column = int(cell.columnSequence or 0)
        raw_rows.setdefault(row, {})[column] = _clean_value(cell.value)
        for hint in (cell.rowType, cell.cellGroupName):
            if isinstance(hint, str):
                row_hints.setdefault(row, set()).add(normalize_persian(hint).lower())

    if not raw_rows:
        return [], []

    column_indexes = sorted({column for row in raw_rows.values() for column in row})
    if not column_indexes:
        return [], []

    ordered_rows: list[tuple[int, list[Any]]] = []
    for sequence in sorted(raw_rows):
        values = [raw_rows[sequence].get(column) for column in column_indexes]
        if all(value is None for value in values):
            continue
        ordered_rows.append((sequence, values))

    if not ordered_rows:
        return [], []

    header_positions: list[int] = []
    for position, (sequence, values) in enumerate(ordered_rows):
        hints = row_hints.get(sequence, set())
        hint_is_header = any(any(token in hint for token in _HEADER_HINTS) for hint in hints)
        if hint_is_header or (position == 0 and _row_looks_like_header(values)):
            header_positions.append(position)
        elif header_positions:
            # Only contiguous leading header rows are treated as headers.
            break

    header_rows = [ordered_rows[position][1] for position in header_positions]
    header_position_set = set(header_positions)
    if header_rows:
        labels: list[str] = []
        for column_index in range(len(column_indexes)):
            parts: list[str] = []
            for header_values in header_rows:
                label = _label(header_values[column_index])
                if label and (not parts or parts[-1] != label):
                    parts.append(label)
            labels.append(" - ".join(parts) if parts else f"column_{column_index + 1}")
        columns = _dedupe_columns(labels)
        data_rows = [
            values
            for position, (_, values) in enumerate(ordered_rows)
            if position not in header_position_set
        ]
    else:
        columns = _dedupe_columns([f"column_{index + 1}" for index in range(len(column_indexes))])
        data_rows = [values for _, values in ordered_rows]

    # Drop rows that became completely empty after cleaning.
    data_rows = [values for values in data_rows if any(value is not None for value in values)]
    if not data_rows:
        return columns, []

    normalized_columns = _normalize_all_columns(data_rows, len(columns))
    normalized_rows = [
        [normalized_columns[column][row] for column in range(len(columns))]
        for row in range(len(data_rows))
    ]
    return columns, normalized_rows


def _normalize_all_columns(
    rows: list[list[Any]],
    width: int,
) -> list[list[Any]]:
    return [_normalize_column([row[index] for row in rows]) for index in range(width)]


def _build_frame(
    columns: list[str],
    rows: list[list[Any]],
    engine: Engine,
) -> Any:
    if engine == "polars":
        if not rows:
            return pl.DataFrame({column: [] for column in columns})
        return pl.DataFrame(
            {column: [row[index] for row in rows] for index, column in enumerate(columns)}
        )
    if engine == "pandas":
        return pd.DataFrame(rows, columns=columns)
    raise ValueError(f"Unsupported dataframe engine: {engine!r}")


def parse_codal_tables(
    datasource: dict[str, Any],
    engine: Engine = "pandas",
) -> dict[str, Any]:
    """Reconstruct every visible table in a Codal ``datasource`` object.

    Cells are grouped by ``rowSequence``/``columnSequence``, invisible cells are
    dropped, header rows are detected (from ``rowType``/``cellGroupName`` hints
    or a text-only first row), and multi-row headers are joined with ``" - "``.
    Persian/Arabic digits, thousands separators, ``٫`` decimals, unicode minus
    and accounting parentheses (``(1,200)`` -> ``-1200.0``) are normalized.
    Duplicate column labels get ``_2``, ``_3`` suffixes.

    Args:
        datasource: A decoded datasource object, e.g. from
            :func:`extract_datasource_json`.
        engine: ``"pandas"`` (default) or ``"polars"``.

    Returns:
        A dictionary mapping ``"<sheet title> - <table title>"`` to a clean
        DataFrame.  Persistently duplicated keys are suffixed with `` (2)``,
        `` (3)``, and so on.  Tables with no visible cells are omitted.  Values
        are numeric (``float``) when parseable, otherwise ``str``; missing
        cells are ``None``/``NaN``.

    Raises:
        ParseError: If the datasource does not match the expected structure.
        ValueError: If ``engine`` is unsupported.

    Examples:
        >>> datasource = extract_datasource_json(html)
        >>> tables = parse_codal_tables(datasource, engine="pandas")
        >>> next(iter(tables))
        'گزارش فعالیت ماهانه - تولید و فروش'
    """

    if engine not in ("pandas", "polars"):
        raise ValueError(f"Unsupported dataframe engine: {engine!r}")

    try:
        model = CodalDatasource.model_validate(datasource)
    except Exception as exc:
        raise ParseError("Invalid Codal datasource structure") from exc

    output: dict[str, Any] = {}
    for sheet_index, sheet in enumerate(model.sheets):
        sheet_title = (
            normalize_persian(sheet.title_Fa or sheet.title_En or "") or f"sheet_{sheet_index + 1}"
        )
        for table_index, table in enumerate(sheet.tables):
            table_title = (
                normalize_persian(table.title_Fa or table.title_En or "")
                or f"table_{table_index + 1}"
            )
            columns, rows = _reconstruct_table(table)
            if not columns:
                continue
            key = f"{sheet_title} - {table_title}"
            if key in output:
                suffix = 2
                while f"{key} ({suffix})" in output:
                    suffix += 1
                key = f"{key} ({suffix})"
            output[key] = _build_frame(columns, rows, engine)
    return output


async def _fetch_and_parse(
    client: AsyncHTTPClient,
    url: str,
    engine: Engine,
) -> dict[str, Any]:
    response = await client.get(url)
    datasource = extract_datasource_json(response.text)
    return parse_codal_tables(datasource, engine=engine)


async def fetch_and_parse_report(
    url_or_letter: str | CodalLetter,
    engine: Engine = "pandas",
    *,
    client: AsyncHTTPClient | None = None,
) -> dict[str, Any]:
    """Download a Codal report page and parse all of its datasource tables.

    Relative URLs (including those on :class:`~iranquant.models.codal.CodalLetter`)
    are resolved with :func:`~iranquant.codal.search.resolve_codal_url` before
    the request is made.

    Args:
        url_or_letter: Either an absolute/relative report URL or a
            ``CodalLetter`` whose ``Url`` is used.
        engine: ``"pandas"`` (default) or ``"polars"``.
        client: Optional shared :class:`~iranquant.core.client.AsyncHTTPClient`.
            When omitted a temporary client is used and closed.

    Returns:
        The dictionary produced by :func:`parse_codal_tables`, keyed by
        ``"<sheet title> - <table title>"``.

    Raises:
        NetworkError: If the report page cannot be downloaded.
        ParseError: If the page has no valid ``var datasource`` JSON or the
            datasource structure is invalid.
        ValueError: If ``engine`` is unsupported.

    Examples:
        >>> letter = reports.Letters[0]
        >>> tables = await fetch_and_parse_report(letter)
        >>> for name, table in tables.items():
        ...     print(name, table.shape)
    """

    url = url_or_letter.Url if isinstance(url_or_letter, CodalLetter) else url_or_letter
    url = resolve_codal_url(url)

    if client is not None:
        return await _fetch_and_parse(client, url, engine)

    http = AsyncHTTPClient()
    try:
        return await _fetch_and_parse(http, url, engine)
    finally:
        await http.aclose()


__all__ = [
    "Engine",
    "extract_datasource_json",
    "fetch_and_parse_report",
    "parse_codal_tables",
]
