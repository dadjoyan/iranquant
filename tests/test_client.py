"""Tests for the async HTTP client, retries and raw URL handling."""

from __future__ import annotations

import warnings

import httpx
import pytest
import respx

from iranquant.config import MARKET_WATCH_URL, IranQuantConfig
from iranquant.core.client import AsyncHTTPClient
from iranquant.exceptions import IranQuantConnectionError, NetworkError, ParseError


@respx.mock
async def test_get_json(client: AsyncHTTPClient) -> None:
    respx.get("https://example.test/data").mock(return_value=httpx.Response(200, json={"ok": True}))

    assert await client.get_json("https://example.test/data") == {"ok": True}


async def test_trust_env_is_disabled_by_default() -> None:
    client = AsyncHTTPClient()
    try:
        assert client.trust_env is False
        assert client.proxy is None
    finally:
        await client.aclose()


async def test_explicit_proxy_enables_trust_env() -> None:
    client = AsyncHTTPClient(proxy="http://127.0.0.1:9090")
    try:
        assert client.trust_env is True
        assert client.proxy == "http://127.0.0.1:9090"
    finally:
        await client.aclose()


@respx.mock
async def test_tsetmc_connect_error_uses_insecure_http1_fallback(
    client: AsyncHTTPClient,
) -> None:
    route = respx.get("https://cdn.tsetmc.com/api/test").mock(
        side_effect=[
            httpx.ConnectError("SSL: WRONG_VERSION_NUMBER"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    assert await client.get_json("https://cdn.tsetmc.com/api/test") == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_tls_failure_without_fallback_raises_connection_error() -> None:
    client = AsyncHTTPClient(
        max_retries=3,
        retry_wait=0,
        config=IranQuantConfig(verify_ssl=True, allow_tls_fallback=False),
    )
    try:
        route = respx.get("https://cdn.tsetmc.com/api/strict").mock(
            side_effect=httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED")
        )

        with pytest.raises(IranQuantConnectionError):
            await client.get_json("https://cdn.tsetmc.com/api/strict")
        assert route.call_count == 1
    finally:
        await client.aclose()


@respx.mock
async def test_tls_fallback_warning_is_emitted_once() -> None:
    client = AsyncHTTPClient(
        max_retries=1,
        retry_wait=0,
        config=IranQuantConfig(verify_ssl=True, allow_tls_fallback=True),
    )
    try:
        route = respx.get("https://cdn.tsetmc.com/api/warn").mock(
            side_effect=[
                httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED"),
                httpx.Response(200, json={"ok": True}),
                httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED"),
                httpx.Response(200, json={"ok": True}),
            ]
        )

        with pytest.warns(UserWarning, match="falling back to unverified transport"):
            assert await client.get_json("https://cdn.tsetmc.com/api/warn") == {"ok": True}

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            assert await client.get_json("https://cdn.tsetmc.com/api/warn") == {"ok": True}

        assert captured == []
        assert route.call_count == 4
    finally:
        await client.aclose()


@respx.mock
async def test_raw_url_does_not_encode_brackets(client: AsyncHTTPClient) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"marketwatch": []})

    respx.get(url__startswith="https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch").mock(
        side_effect=handler
    )

    await client.get_json(MARKET_WATCH_URL, raw=True)

    assert "paperTypes[0]=1" in captured["url"]
    assert "paperTypes[1]=2" in captured["url"]
    assert "paperTypes[2]=8" in captured["url"]
    assert "%5B" not in captured["url"]


@respx.mock
async def test_retries_transient_connection_errors(client: AsyncHTTPClient) -> None:
    route = respx.get("https://example.test/flaky").mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    assert await client.get_json("https://example.test/flaky") == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_raises_network_error_after_server_errors(client: AsyncHTTPClient) -> None:
    route = respx.get("https://example.test/broken").mock(
        return_value=httpx.Response(503, json={"error": "unavailable"})
    )

    with pytest.raises(NetworkError):
        await client.get_json("https://example.test/broken")
    assert route.call_count == 2


@respx.mock
async def test_client_side_errors_are_not_retried(client: AsyncHTTPClient) -> None:
    route = respx.get("https://example.test/missing").mock(
        return_value=httpx.Response(404, json={"error": "missing"})
    )

    with pytest.raises(NetworkError):
        await client.get_json("https://example.test/missing")
    assert route.call_count == 1


@respx.mock
async def test_invalid_json_raises_parse_error(client: AsyncHTTPClient) -> None:
    respx.get("https://example.test/html").mock(
        return_value=httpx.Response(200, text="<html>nope</html>")
    )

    with pytest.raises(ParseError) as exc_info:
        await client.get_json("https://example.test/html")
    assert "Invalid JSON" in str(exc_info.value)
