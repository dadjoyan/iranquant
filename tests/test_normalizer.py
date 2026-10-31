"""Tests for Persian normalization and TSETMC date/time decoding."""

from __future__ import annotations

import datetime as dt

import pytest

from iranquant.core.normalizer import (
    int_to_date,
    int_to_date_optional,
    int_to_jdate,
    int_to_jdate_optional,
    int_to_time,
    int_to_time_optional,
    normalize_persian,
)
from iranquant.exceptions import ParseError


def test_normalizes_arabic_letters_to_persian() -> None:
    assert normalize_persian("علي") == "علی"
    assert normalize_persian("شركت") == "شرکت"
    assert normalize_persian("مباركة") == "مبارکه"


def test_converts_arabic_and_persian_digits() -> None:
    assert normalize_persian("۱۲۳٤٥٦") == "123456"
    assert normalize_persian("٠٩") == "09"


def test_strips_zero_width_and_collapses_whitespace() -> None:
    assert normalize_persian("فولاد\u200cمبارکه") == "فولادمبارکه"
    assert normalize_persian("  فولاد   مبارکه  ") == "فولاد مبارکه"
    assert normalize_persian("\ufeffفولاد") == "فولاد"


def test_int_to_date() -> None:
    assert int_to_date(20261028) == dt.date(2026, 10, 28)
    assert int_to_date(20260101) == dt.date(2026, 1, 1)


def test_int_to_jdate() -> None:
    assert int_to_jdate(20261028) == "1405-08-06"
    assert int_to_jdate(20260321) == "1405-01-01"


def test_int_to_date_optional_valid() -> None:
    assert int_to_date_optional(20261028) == dt.date(2026, 10, 28)
    assert int_to_date_optional(None) is None


@pytest.mark.parametrize("value", [0, 1, 18991231, 21010101, 20260230, 20261340])
def test_int_to_date_optional_rejects_sentinels_and_invalid(value: int) -> None:
    assert int_to_date_optional(value) is None


def test_int_to_jdate_optional() -> None:
    assert int_to_jdate_optional(20261028) == "1405-08-06"
    assert int_to_jdate_optional(1) is None
    assert int_to_jdate_optional(None) is None


def test_int_to_time() -> None:
    assert int_to_time(123000) == dt.time(12, 30, 0)
    assert int_to_time(93000) == dt.time(9, 30, 0)
    assert int_to_time(0) == dt.time(0, 0, 0)


def test_int_to_time_optional() -> None:
    assert int_to_time_optional(123000) == dt.time(12, 30, 0)
    assert int_to_time_optional(None) is None
    assert int_to_time_optional(250000) is None
    assert int_to_time_optional(-1) is None


@pytest.mark.parametrize("value", [20261340, 20260230, 123456789])
def test_invalid_dates_raise(value: int) -> None:
    with pytest.raises(ParseError):
        int_to_date(value)


@pytest.mark.parametrize("value", [250000, 126000, 999999])
def test_invalid_times_raise(value: int) -> None:
    with pytest.raises(ParseError):
        int_to_time(value)
