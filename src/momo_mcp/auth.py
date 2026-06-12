"""OAuth token acquisition + caching for MTN MoMo.

Verified against the live sandbox (2026-06-11):
    POST {base}/{product}/token/
    headers: Authorization: Basic base64(apiUser:apiKey), Ocp-Apim-Subscription-Key
    -> 200 {"access_token": "...", "token_type": "access_token", "expires_in": 3600}

Behavior contract:
  * Token cached in memory with its expiry; refreshed **proactively at 80% of
    lifetime** so a request never races a hard expiry.
  * On a 401 from a business call, the caller asks for a forced refresh and
    retries **once**, never a loop. The retry budget lives in the
    provider; this module just hands out tokens and supports forced refresh.
  * ``token_type`` from MTN is the literal string ``"access_token"``; we always
    send ``Authorization: Bearer <token>`` regardless (see GOTCHAS).
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import httpx

from .logging_conf import get_logger

log = get_logger("auth")

# Refresh when this fraction of the token's lifetime has elapsed.
_REFRESH_AT = 0.80


@dataclass
class _CachedToken:
    value: str
    expires_at: float  # monotonic deadline
    refresh_after: float  # monotonic; proactive refresh threshold (80% lifetime)


class TokenManager:
    """Caches a bearer token for one product ('collection' or 'disbursement').

    Not goroutine-safe by design, the server is single-process and calls are
    serialized through the provider. An ``httpx.AsyncClient`` is injected so the
    same client (and its connection pool / rate limiter) is reused.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        product: str,
        api_user: str,
        api_key: str,
        subscription_key: str,
    ):
        if product not in ("collection", "disbursement"):
            raise ValueError(f"product must be collection|disbursement, got {product!r}")
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._product = product
        self._sub_key = subscription_key
        self._basic = base64.b64encode(f"{api_user}:{api_key}".encode()).decode()
        self._cached: _CachedToken | None = None

    @property
    def product(self) -> str:
        return self._product

    def _is_fresh(self, now: float) -> bool:
        return self._cached is not None and now < self._cached.refresh_after

    @property
    def has_valid_token(self) -> bool:
        """True if a token is cached and not past hard expiry (for health checks)."""
        return self._cached is not None and time.monotonic() < self._cached.expires_at

    async def get_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid bearer token, refreshing if stale or forced.

        ``force_refresh=True`` is used by the 401-retry-once path: it discards the
        cached token and fetches a new one exactly once before the caller retries.
        """
        now = time.monotonic()
        if not force_refresh and self._is_fresh(now):
            return self._cached.value  # type: ignore[union-attr]
        return await self._refresh()

    async def _refresh(self) -> str:
        url = f"{self._base_url}/{self._product}/token/"
        headers = {
            "Authorization": f"Basic {self._basic}",
            "Ocp-Apim-Subscription-Key": self._sub_key,
        }
        resp = await self._client.post(url, headers=headers)
        if resp.status_code != 200:
            # Point at the likely cause; the message is safe to return.
            raise AuthError(
                f"Token request for {self._product} failed (HTTP {resp.status_code}). "
                "Check the subscription key and that the API user/key were provisioned "
                "(run scripts/provision.py). This is not retryable without fixing config."
            )
        data = resp.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not token:
            raise AuthError(f"Token response for {self._product} had no access_token.")
        now = time.monotonic()
        self._cached = _CachedToken(
            value=token,
            expires_at=now + expires_in,
            refresh_after=now + expires_in * _REFRESH_AT,
        )
        log.info("token refreshed", extra={"product": self._product, "expires_in": expires_in})
        return token


class AuthError(RuntimeError):
    """Token acquisition failed. Message is safe to surface to the client."""
