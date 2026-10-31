"""Persian/Arabic text normalization and TSETMC date/time decoding."""

from __future__ import annotations

import datetime as dt

import jdatetime

from iranquant.exceptions import ParseError

# Arabic letter variants that are visually identical to Persian letters.
_CHAR_MAP: dict[int, str] = {
    0x064A: "\u06cc",  # ي (Arabic yeh) -> ی (Persian yeh)
    0x0649: "\u06cc",  # ى (Alef maksura) -> ی
    0x0643: "\u06a9",  # ك (Arabic kaf) -> ک (Persian kaf)
    0x0629: "\u0647",  # ة (Teh marbuta) -> ه (heh)
}

# Arabic-Indic digits ٠..٩ and extended Persian digits ۰..۹ -> ASCII.
for _offset in range(10):
    _CHAR_MAP[0x0660 + _offset] = str(_offset)
    _CHAR_MAP[0x06F0 + _offset] = str(_offset)

# Zero-width and bidirectional control characters that break equality checks.
_ZERO_WIDTH = dict.fromkeys(
    [
        0x200B,  # zero-width space
        0x200C,  # zero-width non-joiner (ZWNJ)
        0x200D,  # zero-width joiner (ZWJ)
        0x200E,  # left-to-right mark
        0x200F,  # right-to-left mark
        0xFEFF,  # BOM
        0x0640,  # Arabic tatweel (kashida)
    ]
)


def normalize_persian(text: str) -> str:
    """Normalize Persian/Arabic text for reliable comparison.

    Converts Arabic yeh/kaf/teh-marbuta to their Persian equivalents, converts
    Arabic-Indic and Persian digits to ASCII digits, removes zero-width and
    directional control characters, and collapses whitespace.

    This is what makes ``"فولاد"`` written with an Arabic yeh and the same
    symbol written with a Persian yeh resolve to the same TSETMC instrument.

    Args:
        text: Any string, including Persian, Arabic-Indic or mixed text.

    Returns:
        The normalized string with single spaces and ASCII digits.

    Examples:
        >>> normalize_persian("۱۲۳")
        '123'
        >>> normalize_persian("  علي  ")
        'علی'
    """

    translated = text.translate(_CHAR_MAP).translate(_ZERO_WIDTH)
    return " ".join(translated.split())


# TSETMC uses sentinel integers (0, 1, ...) for missing/inactive dates.
MIN_EVEN_DATE = 19000101
MAX_EVEN_DATE = 21001231


def _even_digits(value: int, width: int, label: str) -> str:
    raw = str(int(value))
    if len(raw) > width:
        raise ParseError(f"Invalid {label} value: {value!r}")
    return raw.zfill(width)


def int_to_date(d_even: int) -> dt.date:
    """Convert a TSETMC ``dEven`` integer (e.g. ``20261028``) to a date.

    Args:
        d_even: An eight-digit Gregorian date encoded as an integer.

    Returns:
        The corresponding :class:`datetime.date`.

    Raises:
        ParseError: If the value is longer than eight digits or is not a valid
            calendar date.

    Examples:
        >>> int_to_date(20261028)
        datetime.date(2026, 10, 28)
    """

    raw = _even_digits(d_even, 8, "dEven")
    year, month, day = int(raw[:4]), int(raw[4:6]), int(raw[6:8])
    try:
        return dt.date(year, month, day)
    except ValueError as exc:
        raise ParseError(f"Invalid TSETMC date: {d_even!r}") from exc


def int_to_date_optional(d_even: int | None) -> dt.date | None:
    """Convert a TSETMC ``dEven`` to a date, returning ``None`` for sentinels.

    Values such as ``0`` and ``1`` are emitted for inactive/expired instruments
    and placeholder rows.  Anything outside ``[19000101, 21001231]`` — or not a
    valid calendar date — yields ``None`` instead of raising.

    Args:
        d_even: An eight-digit Gregorian date encoded as an integer, or ``None``.

    Returns:
        The decoded :class:`datetime.date`, or ``None`` for sentinel or invalid
        values.

    Examples:
        >>> int_to_date_optional(20261028)
        datetime.date(2026, 10, 28)
        >>> int_to_date_optional(0) is None
        True
    """

    if d_even is None:
        return None
    try:
        value = int(d_even)
    except (TypeError, ValueError):
        return None
    if not MIN_EVEN_DATE <= value <= MAX_EVEN_DATE:
        return None
    try:
        return int_to_date(value)
    except ParseError:
        return None


def int_to_jdate(d_even: int) -> str:
    """Convert a TSETMC ``dEven`` integer to a Jalali ``YYYY-MM-DD`` string.

    Args:
        d_even: An eight-digit Gregorian date encoded as an integer.

    Returns:
        The Jalali (Solar Hijri) date as a zero-padded ``"YYYY-MM-DD"`` string.

    Raises:
        ParseError: If ``d_even`` is not a valid Gregorian date.

    Examples:
        >>> int_to_jdate(20261028)
        '1405-08-06'
    """

    gregorian = int_to_date(d_even)
    jalali = jdatetime.date.fromgregorian(date=gregorian)
    return str(jalali.strftime("%Y-%m-%d"))


def int_to_jdate_optional(d_even: int | None) -> str | None:
    """Jalali counterpart of :func:`int_to_date_optional`.

    Args:
        d_even: An eight-digit Gregorian date encoded as an integer, or ``None``.

    Returns:
        The Jalali date as ``"YYYY-MM-DD"``, or ``None`` for sentinel values.

    Examples:
        >>> int_to_jdate_optional(1) is None
        True
    """

    gregorian = int_to_date_optional(d_even)
    if gregorian is None:
        return None
    jalali = jdatetime.date.fromgregorian(date=gregorian)
    return str(jalali.strftime("%Y-%m-%d"))


def int_to_time(h_even: int) -> dt.time:
    """Convert a TSETMC ``hEven`` integer (e.g. ``123000``) to a time.

    Args:
        h_even: A six-digit ``HHMMSS`` time encoded as an integer.

    Returns:
        The corresponding :class:`datetime.time`.

    Raises:
        ParseError: If the value is longer than six digits or is not a valid
            time of day.

    Examples:
        >>> int_to_time(123000)
        datetime.time(12, 30)
    """

    raw = _even_digits(h_even, 6, "hEven")
    hour, minute, second = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
    try:
        return dt.time(hour, minute, second)
    except ValueError as exc:
        raise ParseError(f"Invalid TSETMC time: {h_even!r}") from exc


def int_to_time_optional(h_even: int | None) -> dt.time | None:
    """Convert a TSETMC ``hEven`` to a time, returning ``None`` when invalid.

    Args:
        h_even: A ``HHMMSS`` time encoded as an integer, or ``None``.

    Returns:
        The decoded :class:`datetime.time`, or ``None`` when the value is not a
        valid time between ``00:00:00`` and ``23:59:59``.

    Examples:
        >>> int_to_time_optional(91500)
        datetime.time(9, 15)
        >>> int_to_time_optional(999999) is None
        True
    """

    if h_even is None:
        return None
    try:
        value = int(h_even)
    except (TypeError, ValueError):
        return None
    if not 0 <= value <= 235959:
        return None
    try:
        return int_to_time(value)
    except ParseError:
        return None
