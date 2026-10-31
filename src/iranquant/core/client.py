"""Async HTTP client tuned for TSETMC and Codal."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Mapping
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
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
)
from iranquant.exceptions import NetworkError, ParseError

_RETRYABLE_EXCEPTIONS = (
    NetworkError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _is_codal_url(url: httpx.URL) -> bool:
    host = (url.host or "").lower()
    return host == "codal.ir" or host.endswith(".codal.ir")


def _is_tsetmc_url(url: httpx.URL) -> bool:
    host = (url.host or "").lower()
    return host == "tsetmc.com" or host.endswith(".tsetmc.com")


class AsyncHTTPClient:
    """Small retrying wrapper around :class:`httpx.AsyncClient`.

    Three transports are maintained:

    * a regular verified client used for TSETMC;
    * an insecure client used for every ``*.codal.ir`` domain, whose
      certificate chains frequently fail strict verification;
    * an insecure HTTP/1.1 fallback used once for TSETMC when the primary
      connection fails with an SSL/proxy-style error.

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

        Examples:
            >>> client = AsyncHTTPClient(timeout=10.0, max_concurrency=5)
            >>> client.trust_env
            False
            >>> client = AsyncHTTPClient(proxy="http://127.0.0.1:9090")
            >>> client.trust_env
            True
        """
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._headers = dict(headers or {})
        self.proxy = proxy
        # Explicit proxies imply honouring the environment unless overridden.
        self.trust_env = (proxy is not None) if trust_env is None else trust_env

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
            **common,
        )
        # Dedicated insecure transport for Codal (SSL chain bugs).
        self._codal_client = httpx.AsyncClient(
            headers={**CODAL_HEADERS, **self._headers},
            verify=False,
            **common,
        )
        # Last-resort HTTP/1.1 + no-verify transport for TSETMC.
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
        primary = self._codal_client if codal else self._client
        request_headers: Mapping[str, str] | None = (
            {**CODAL_HEADERS, **self._headers} if codal else headers
        )
        try:
            return await self._send(primary, url, request_headers)
        except (httpx.ConnectError, ssl.SSLError):
            # Domestic hosts sometimes negotiate against a legacy TLS
            # middlebox / proxied socket.  Retry once over plain HTTP/1.1
            # without certificate verification.
            if not codal and _is_tsetmc_url(url):
                return await self._send(self._fallback_client, url, request_headers)
            raise

    def _retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=self._retry_wait, min=0, max=5),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
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
        """Perform a GET request with retries and TLS fallbacks.

        The request is retried up to ``max_retries`` times on network errors,
        and responses with status codes >= 500 are treated as retryable.  A
        TSETMC connection that fails with ``SSLError``/``ConnectError`` is
        retried once over an insecure HTTP/1.1 transport.  Every ``*.codal.ir``
        request uses the dedicated certificate-relaxed transport.

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
        """Close the primary, Codal and fallback HTTP transports.

        Safe to call more than once.  After closing, the instance must not be
        used again.

        Examples:
            >>> client = AsyncHTTPClient()
            >>> await client.aclose()
        """
        await self._client.aclose()
        await self._codal_client.aclose()
        await self._fallback_client.aclose()

    async def __aenter__(self) -> AsyncHTTPClient:
        """Enter the async context manager and return ``self``."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close all transports when leaving the async context manager."""
        await self.aclose()
