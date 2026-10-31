"""Codal announcement search."""

from __future__ import annotations

from typing import Any

from iranquant.config import CODAL_BASE, CODAL_SEARCH_URL
from iranquant.core.client import AsyncHTTPClient
from iranquant.exceptions import ParseError
from iranquant.models.codal import CodalLetter, CodalSearchResponse

MONTHLY_ACTIVITY_LETTER = 58
INTERIM_FINANCIAL_LETTER = 6


def resolve_codal_url(url: str) -> str:
    """Make a Codal link absolute (``/foo`` -> ``https://codal.ir/foo``).

    Protocol-relative links (``//cdn...``) become HTTPS, root-relative links
    are prefixed with :data:`iranquant.config.CODAL_BASE`, and absolute URLs
    are returned unchanged.  Empty strings stay empty.

    Args:
        url: A raw Codal URL as returned by the search API.

    Returns:
        An absolute URL suitable for :class:`httpx.AsyncClient`.

    Examples:
        >>> resolve_codal_url("/Report/123")
        'https://codal.ir/Report/123'
        >>> resolve_codal_url("//cdn.codal.ir/a.pdf")
        'https://cdn.codal.ir/a.pdf'
    """

    cleaned = url.strip()
    if not cleaned:
        return cleaned
    if cleaned.startswith("//"):
        return f"https:{cleaned}"
    if cleaned.startswith("/"):
        return f"{CODAL_BASE}{cleaned}"
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return f"{CODAL_BASE}/{cleaned.lstrip('/')}"


def _resolve_letter_urls(letter: CodalLetter) -> CodalLetter:
    letter.Url = resolve_codal_url(letter.Url)
    if letter.ExcelUrl:
        letter.ExcelUrl = resolve_codal_url(letter.ExcelUrl)
    if letter.PdfUrl:
        letter.PdfUrl = resolve_codal_url(letter.PdfUrl)
    return letter


async def _search(
    client: AsyncHTTPClient,
    *,
    symbol: str | None,
    letter_type: int,
    category: int,
    page: int,
    audited: bool,
    not_audited: bool,
    mains: bool,
    childs: bool,
) -> CodalSearchResponse:
    params: dict[str, Any] = {
        "Symbol": symbol or "",
        "LetterType": letter_type,
        "Category": category,
        "CompanyState": -1,
        "CompanyType": -1,
        "PageNumber": page,
        "Audited": str(audited).lower(),
        "NotAudited": str(not_audited).lower(),
        "Mains": str(mains).lower(),
        "Childs": str(childs).lower(),
        "Consolidatable": "true",
        "NotConsolidatable": "true",
        "Publisher": "false",
        "search": "true",
    }
    payload = await client.get_json(CODAL_SEARCH_URL, params=params)
    try:
        response = CodalSearchResponse.model_validate(payload)
    except Exception as exc:  # pydantic.ValidationError
        raise ParseError("Unexpected Codal search payload") from exc

    response.Letters = [_resolve_letter_urls(letter) for letter in response.Letters]
    return response


async def search_codal(
    symbol: str | None = None,
    letter_type: int = -1,
    category: int = -1,
    page: int = 1,
    audited: bool = True,
    not_audited: bool = True,
    mains: bool = True,
    childs: bool = True,
    *,
    client: AsyncHTTPClient | None = None,
) -> CodalSearchResponse:
    """Search Codal announcements.

    Parameters mirror the ``search.codal.ir`` query API.  A temporary client is
    created and closed when ``client`` is not supplied.  Every returned letter
    has its ``Url``, ``ExcelUrl`` and ``PdfUrl`` resolved to absolute URLs.

    Args:
        symbol: Optional Persian ticker to filter by, e.g. ``"فولاد"``.
        letter_type: Codal ``LetterType`` filter.  ``-1`` means all types;
            ``58`` selects monthly activity reports and ``6`` selects interim
            financial statements.  Defaults to ``-1``.
        category: Codal ``Category`` filter; ``-1`` means all.  Defaults to
            ``-1``.
        page: One-based result page.  Defaults to ``1``.
        audited: Include audited reports.  Defaults to ``True``.
        not_audited: Include unaudited reports.  Defaults to ``True``.
        mains: Include parent-company reports.  Defaults to ``True``.
        childs: Include subsidiary reports.  Defaults to ``True``.
        client: Optional shared :class:`~iranquant.core.client.AsyncHTTPClient`.
            When omitted a temporary client is used and closed.

    Returns:
        A :class:`~iranquant.models.codal.CodalSearchResponse` with ``Total``,
        ``Page`` and a list of :class:`~iranquant.models.codal.CodalLetter`.

    Raises:
        NetworkError: If ``search.codal.ir`` cannot be reached.
        ParseError: If the response is not valid JSON or does not match the
            expected schema.

    Examples:
        >>> response = await search_codal("فولاد", letter_type=58)
        >>> response.Total
        12
        >>> response.Letters[0].Url.startswith("https://codal.ir")
        True
    """

    if client is not None:
        return await _search(
            client,
            symbol=symbol,
            letter_type=letter_type,
            category=category,
            page=page,
            audited=audited,
            not_audited=not_audited,
            mains=mains,
            childs=childs,
        )

    http = AsyncHTTPClient()
    try:
        return await _search(
            http,
            symbol=symbol,
            letter_type=letter_type,
            category=category,
            page=page,
            audited=audited,
            not_audited=not_audited,
            mains=mains,
            childs=childs,
        )
    finally:
        await http.aclose()


async def get_monthly_reports(
    symbol: str,
    page: int = 1,
    *,
    client: AsyncHTTPClient | None = None,
) -> CodalSearchResponse:
    """Monthly activity reports (گزارش فعالیت ماهانه, ``LetterType=58``).

    Args:
        symbol: Persian ticker, e.g. ``"فولاد"``.
        page: One-based result page.  Defaults to ``1``.
        client: Optional shared :class:`~iranquant.core.client.AsyncHTTPClient`.

    Returns:
        A :class:`~iranquant.models.codal.CodalSearchResponse`.  Pass any
        ``Letters`` entry to :func:`fetch_and_parse_report` to reconstruct its
        tables.

    Raises:
        NetworkError: If Codal cannot be reached.
        ParseError: If the response is malformed.

    Examples:
        >>> reports = await get_monthly_reports("فولاد")
        >>> tables = await fetch_and_parse_report(reports.Letters[0])
    """

    return await search_codal(
        symbol=symbol,
        letter_type=MONTHLY_ACTIVITY_LETTER,
        page=page,
        client=client,
    )


async def get_financial_statements(
    symbol: str,
    page: int = 1,
    *,
    client: AsyncHTTPClient | None = None,
) -> CodalSearchResponse:
    """Interim financial statements (``LetterType=6``).

    Args:
        symbol: Persian ticker, e.g. ``"فولاد"``.
        page: One-based result page.  Defaults to ``1``.
        client: Optional shared :class:`~iranquant.core.client.AsyncHTTPClient`.

    Returns:
        A :class:`~iranquant.models.codal.CodalSearchResponse` with interim
        financial-statement letters.

    Raises:
        NetworkError: If Codal cannot be reached.
        ParseError: If the response is malformed.

    Examples:
        >>> statements = await get_financial_statements("فولاد")
        >>> statements.Letters[0].Title
        'صورت‌های مالی ۳ ماهه'
    """

    return await search_codal(
        symbol=symbol,
        letter_type=INTERIM_FINANCIAL_LETTER,
        page=page,
        client=client,
    )


__all__ = [
    "INTERIM_FINANCIAL_LETTER",
    "MONTHLY_ACTIVITY_LETTER",
    "get_financial_statements",
    "get_monthly_reports",
    "resolve_codal_url",
    "search_codal",
]
