"""Async HTTP client tuned for TSETMC and Codal."""

from __future__ import annotations

import asyncio
import logging
import ssl
import warnings
from collections.abc import Mapping
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from iranquant.config import (
    CODAL_HEADERS,
    DEFAULT_TIMEOUT,
    MAX_CONCURRENCY,
    MAX_RETRIES,
    RETRY_WAIT,
    TSETMC_HEADERS,
    IranQuantConfig,
)
from iranquant.exceptions import IranQuantConnectionError, NetworkError, ParseError

logger = logging.getLogger(__name__)

_RETRYABLE_EXCEPTIONS = (
    NetworkError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _is_tls_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, httpx.ConnectError):
        text = str(exc).lower()
        return any(token in text for token in ("ssl", "tls", "certificate"))
    return False


def _should_retry(exc: BaseException) -> bool:
    return isinstance(exc, _RETRYABLE_EXCEPTIONS) and not isinstance(exc, IranQuantConnectionError)


def _is_codal_url(url: httpx.URL) -> bool:
    host = (url.host or "").lower()
    return host == "codal.ir" or host.endswith(".codal.ir")


class AsyncHTTPClient:
    """Small retrying wrapper around :class:`httpx.AsyncClient`.

    Requests use a verified transport when ``config.verify_ssl`` is true. A
    certificate failure is retried once over an unverified transport only when
    ``config.allow_tls_fallback`` is true; the first fallback emits a
    ``UserWarning`` and a log record. With fallback disabled, the failure raises
    :class:`~iranquant.exceptions.IranQuantConnectionError` without retrying.

    Environment proxy variables are ignored by default (``trust_env=False``) to
    avoid corporate/VPN HTTP proxies breaking direct connections to domestic
    Iranian hosts.  Pass ``proxy=...`` to opt into an explicit proxy.
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_concurrency: int = MAX_CONCURRENCY,
        max_retries: int = MAX_RETRIES,
        retry_wait: float = RETRY_WAIT,
        headers: Mapping[str, str] | None = None,
        proxy: str | None = None,
        trust_env: bool | None = None,
        config: IranQuantConfig | None = None,
    ) -> None:
        """Create the pooled HTTP transports.

        Args:
            timeout: Per-request timeout in seconds.  Defaults to
                :data:`iranquant.config.DEFAULT_TIMEOUT` (30 s).
            max_concurrency: Maximum number of simultaneous requests enforced
                by an internal :class:`asyncio.Semaphore`.  Defaults to
                :data:`iranquant.config.MAX_CONCURRENCY` (10).
            max_retries: Total attempts per request.  Defaults to
                :data:`iranquant.config.MAX_RETRIES` (3).
            retry_wait: Base wait used by the exponential retry backoff, in
                seconds.  Defaults to :data:`iranquant.config.RETRY_WAIT` (0.5).
            headers: Extra headers merged over the built-in TSETMC/Codal
                headers.
            proxy: Optional explicit proxy URL, e.g.
                ``"http://127.0.0.1:9090"``.  When omitted, environment proxy
                variables are ignored.
            trust_env: Whether to honour ``HTTP_PROXY`` / ``HTTPS_PROXY`` and
                other environment settings.  Defaults to ``True`` only when an
                explicit ``proxy`` is supplied, otherwise ``False``.
            config: TLS policy. Defaults to :class:`IranQuantConfig`, which
                reads ``IRANQUANT_VERIFY_SSL`` and
                ``IRANQUANT_ALLOW_TLS_FALLBACK``.

        Examples:
            >>> client = AsyncHTTPClient(timeout=10.0, max_concurrency=5)
            >>> client.trust_env
            False
            >>> client = AsyncHTTPClient(
            ...     config=IranQuantConfig(allow_tls_fallback=False)
            ... )
        """
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._headers = dict(headers or {})
        self.config = config or IranQuantConfig()
        self.proxy = proxy
        # Explicit proxies imply honouring the environment unless overridden.
        self.trust_env = (proxy is not None) if trust_env is None else trust_env
        self._tls_fallback_warned = False

        common: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": True,
            "trust_env": self.trust_env,
            "http2": False,
        }
        if proxy is not None:
            common["proxy"] = proxy

        self._client = httpx.AsyncClient(
            headers={**TSETMC_HEADERS, **self._headers},
            verify=self.config.verify_ssl,
            **common,
        )
        self._fallback_client = httpx.AsyncClient(
            headers={**TSETMC_HEADERS, **self._headers},
            verify=False,
            **common,
        )

    @staticmethod
    def _build_url(
        url: str,
        params: Mapping[str, Any] | None,
        *,
        raw: bool,
    ) -> httpx.URL:
        """Build the target URL while preserving raw brackets in query strings.

        When ``raw`` is true (or when no params are supplied) the URL string is
        handed to :class:`httpx.URL` untouched.  This keeps ``paperTypes[0]=1``
        verbatim; passing ``params=`` would encode the brackets and break the
        MarketWatch endpoint.
        """

        if params is not None and not raw:
            return httpx.URL(url, params=params)
        return httpx.URL(url)

    async def _send(
        self,
        client: httpx.AsyncClient,
        url: httpx.URL,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        async with self._semaphore:
            return await client.get(url, headers=headers)

    async def _send_with_fallback(
        self,
        url: httpx.URL,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        codal = _is_codal_url(url)
        request_headers: Mapping[str, str] | None = (
            {**CODAL_HEADERS, **self._headers} if codal else headers
        )
        try:
            return await self._send(self._client, url, request_headers)
        except (httpx.ConnectError, ssl.SSLError) as exc:
            if not _is_tls_error(exc) or not self.config.verify_ssl:
                raise
            if not self.config.allow_tls_fallback:
                raise IranQuantConnectionError(f"TLS verification failed for {url}: {exc}") from exc
            self._warn_tls_fallback(url, exc)
            return await self._send(self._fallback_client, url, request_headers)

    def _warn_tls_fallback(self, url: httpx.URL, exc: BaseException) -> None:
        if self._tls_fallback_warned:
            return
        self._tls_fallback_warned = True
        warnings.warn(
            "Upstream TLS verification failed; falling back to unverified "
            "transport because allow_tls_fallback=True.",
            UserWarning,
            stacklevel=2,
        )
        logger.warning("TLS fallback for %s: %s", url, exc)

    def _retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=self._retry_wait, min=0, max=5),
            retry=retry_if_exception(_should_retry),
            reraise=True,
        )

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        raw: bool = False,
    ) -> httpx.Response:
        """Perform a GET request with retries and TLS fallback.

        The request is retried up to ``max_retries`` times on network errors,
        and responses with status codes >= 500 are treated as retryable.
        Certificate failures use the unverified fallback transport once when
        ``allow_tls_fallback`` is true; the first fallback emits a
        ``UserWarning``. With fallback disabled, the request fails immediately
        with :class:`~iranquant.exceptions.IranQuantConnectionError`.

        Args:
            url: Absolute request URL.
            params: Optional query parameters.  Ignored for URL encoding when
                ``raw`` is true.
            headers: Optional per-request headers merged over the defaults.
            raw: When true the URL string is sent verbatim without
                percent-encoding.  Required for the MarketWatch endpoint, whose
                ``paperTypes[0]=1`` brackets must stay literal.

        Returns:
            The successful :class:`httpx.Response`.

        Raises:
            NetworkError: If the host is unreachable, a non-2xx status is
                returned, or all retries are exhausted.
            IranQuantConnectionError: If TLS verification fails and
                ``allow_tls_fallback`` is false.

        Examples:
            >>> response = await client.get("https://cdn.tsetmc.com/api/BestLimits/1")
            >>> response.status_code
            200
        """

        target = self._build_url(url, params, raw=raw)

        try:
            async for attempt in self._retrying():
                with attempt:
                    response = await self._send_with_fallback(target, headers)
                    if response.status_code >= 500:
                        raise NetworkError(f"Server error {response.status_code} for {target}")
                    response.raise_for_status()
                    return response
        except httpx.HTTPStatusError as exc:
            raise NetworkError(f"HTTP {exc.response.status_code} for {exc.request.url}") from exc
        except httpx.HTTPError as exc:
            raise NetworkError(f"Request failed for {target}: {exc}") from exc

        raise NetworkError(f"Request failed after retries: {target}")

    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        """GET a URL and decode its JSON body.

        Args:
            url: Absolute request URL.
            params: Optional query parameters.
            headers: Optional per-request headers.
            raw: When true, send the URL untouched (see :meth:`get`).

        Returns:
            The decoded JSON value (usually a ``dict`` or ``list``).

        Raises:
            NetworkError: If the request fails after retries.
            ParseError: If the response body is not valid JSON.

        Examples:
            >>> payload = await client.get_json(
            ...     "https://cdn.tsetmc.com/api/Instrument/GetInstrumentSearch/فولاد"
            ... )
            >>> "instrumentSearch" in payload
            True
        """

        response = await self.get(url, params=params, headers=headers, raw=raw)
        try:
            return response.json()
        except ValueError as exc:
            raise ParseError(f"Invalid JSON from {response.request.url}") from exc

    async def aclose(self) -> None:
        """Close the primary and fallback HTTP transports.

        Safe to call more than once.  After closing, the instance must not be
        used again.

        Examples:
            >>> client = AsyncHTTPClient()
            >>> await client.aclose()
        """
        await self._client.aclose()
        await self._fallback_client.aclose()

    async def __aenter__(self) -> AsyncHTTPClient:
        """Enter the async context manager and return ``self``."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close all transports when leaving the async context manager."""
        await self.aclose()
