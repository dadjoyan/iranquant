"""Exception hierarchy for iranquant."""

from __future__ import annotations


class IranQuantError(Exception):
    """Base class for every exception raised by iranquant.

    Catching this single type covers every library-specific failure while
    still allowing narrower handlers for specific cases.

    Examples:
        >>> try:
        ...     iq.history("نماد_ناموجود")
        ... except iq.IranQuantError as exc:
        ...     print(type(exc).__name__)
        SymbolNotFoundError
    """


class NetworkError(IranQuantError):
    """Raised when a remote endpoint cannot be reached or returns an error.

    Wraps connection failures, timeouts, HTTP error statuses and exhausted
    retries from TSETMC or Codal.
    """


class IranQuantConnectionError(NetworkError):
    """Raised when TLS verification fails and fallback is disabled.

    Inherits from :class:`NetworkError`, so existing handlers keep working,
    but is excluded from retry loops because the failure is deterministic.
    """


class SymbolNotFoundError(IranQuantError):
    """Raised when a symbol cannot be resolved to a TSETMC instrument code.

    Examples:
        >>> iq.get_inscode("نماد_ناموجود")
        Traceback (most recent call last):
        ...
        iranquant.exceptions.SymbolNotFoundError: Symbol not found: 'نماد_ناموجود'
    """


class ParseError(IranQuantError):
    """Raised when a remote payload cannot be parsed into the expected shape.

    Covers invalid JSON, unexpected TSETMC/Codal schemas, malformed ``dEven``
    or ``hEven`` values, and missing ``var datasource`` blocks.
    """
