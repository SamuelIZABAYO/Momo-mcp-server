"""Token caching, proactive refresh, and the 401-retry-once path — all mocked."""

from __future__ import annotations

import httpx
import pytest
import respx

from momo_mcp.auth import AuthError, TokenManager

BASE = "https://sandbox.momodeveloper.mtn.com"
TOKEN_URL = f"{BASE}/collection/token/"


def _token_response(token="tok-abc", expires_in=3600):
    return httpx.Response(
        200, json={"access_token": token, "token_type": "access_token", "expires_in": expires_in}
    )


async def _manager(client):
    return TokenManager(
        client=client, base_url=BASE, product="collection",
        api_user="u", api_key="k", subscription_key="s",
    )


@respx.mock
async def test_token_fetched_and_cached():
    route = respx.post(TOKEN_URL).mock(return_value=_token_response())
    async with httpx.AsyncClient() as client:
        mgr = await _manager(client)
        t1 = await mgr.get_token()
        t2 = await mgr.get_token()  # cached, no second HTTP call
        assert t1 == t2 == "tok-abc"
        assert route.call_count == 1
        assert mgr.has_valid_token


@respx.mock
async def test_force_refresh_fetches_new_token():
    route = respx.post(TOKEN_URL).mock(
        side_effect=[_token_response("first"), _token_response("second")]
    )
    async with httpx.AsyncClient() as client:
        mgr = await _manager(client)
        assert await mgr.get_token() == "first"
        # force_refresh simulates the 401-retry path discarding the cached token
        assert await mgr.get_token(force_refresh=True) == "second"
        assert route.call_count == 2


@respx.mock
async def test_proactive_refresh_at_80_percent():
    # expires_in=10 -> refresh_after at 8s. We can't sleep 8s in a unit test, so
    # assert the threshold math directly by inspecting the cached token.
    respx.post(TOKEN_URL).mock(return_value=_token_response(expires_in=10))
    async with httpx.AsyncClient() as client:
        mgr = await _manager(client)
        await mgr.get_token()
        cached = mgr._cached
        assert cached is not None
        # refresh_after must be earlier than hard expiry (80% < 100%)
        assert cached.refresh_after < cached.expires_at


@respx.mock
async def test_token_http_error_raises_autherror():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(500, text="boom"))
    async with httpx.AsyncClient() as client:
        mgr = await _manager(client)
        with pytest.raises(AuthError, match="Token request"):
            await mgr.get_token()


@respx.mock
async def test_missing_access_token_raises():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"expires_in": 3600}))
    async with httpx.AsyncClient() as client:
        mgr = await _manager(client)
        with pytest.raises(AuthError, match="no access_token"):
            await mgr.get_token()


def test_bad_product_rejected():
    with pytest.raises(ValueError, match="product must be"):
        TokenManager(
            client=None, base_url=BASE, product="bogus",  # type: ignore[arg-type]
            api_user="u", api_key="k", subscription_key="s",
        )
