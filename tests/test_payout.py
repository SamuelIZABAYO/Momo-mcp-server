"""Payout approval gate (§4.3) and its adversarial cases (seeds of §7.1):
no approval, forged/expired/replayed codes, amount/recipient mismatch."""

from __future__ import annotations

import httpx
import pytest
import respx

from momo_mcp.config import load_settings
from momo_mcp.providers.base import GuardrailRejection, PaymentStatus
from momo_mcp.providers.mtn import MTNProvider
from momo_mcp.store import Store

BASE = "https://sandbox.momodeveloper.mtn.com"
DISB_TOKEN = f"{BASE}/disbursement/token/"
TRANSFER = f"{BASE}/disbursement/v1_0/transfer"

_ENV = {
    "MOMO_COLLECTION_SUBSCRIPTION_KEY": "a" * 32,
    "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY": "b" * 32,
    "MOMO_BASE_URL": BASE,
    "MOMO_API_USER": "11111111-1111-1111-1111-111111111111",
    "MOMO_API_KEY": "k" * 16,
    "MSISDN_ALLOWLIST": "46733123453,46733123451",
    "DRY_RUN": "false",
    "REQUIRE_PAYOUT_APPROVAL": "true",
}


def _settings(monkeypatch, **overrides):
    for k, v in {**_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)
    return load_settings(env_file=None)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "p.sqlite3")
    yield s
    s.close()


def _token():
    return httpx.Response(200, json={"access_token": "t", "token_type": "access_token", "expires_in": 3600})


@respx.mock
async def test_payout_blocked_without_approval(monkeypatch, store):
    settings = _settings(monkeypatch)
    transfer = respx.post(TRANSFER).mock(return_value=httpx.Response(202))
    provider = MTNProvider(settings=settings, store=store)
    try:
        res = await provider.send_payout(msisdn="46733123453", amount=10, currency="EUR")
        assert res.pending_approval is True
        assert res.approval_code
        assert res.transaction_id is None
        # No transfer was sent — money did not move.
        assert transfer.call_count == 0
    finally:
        await provider.aclose()


@respx.mock
async def test_payout_executes_with_valid_code(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(DISB_TOKEN).mock(return_value=_token())
    transfer = respx.post(TRANSFER).mock(return_value=httpx.Response(202))
    provider = MTNProvider(settings=settings, store=store)
    try:
        pending = await provider.send_payout(msisdn="46733123453", amount=10, currency="EUR")
        done = await provider.confirm_payout(pending.approval_code)
        assert done.status == PaymentStatus.PENDING
        assert done.transaction_id
        assert transfer.call_count == 1
    finally:
        await provider.aclose()


@respx.mock
async def test_forged_code_rejected(monkeypatch, store):
    settings = _settings(monkeypatch)
    provider = MTNProvider(settings=settings, store=store)
    try:
        with pytest.raises(GuardrailRejection) as exc:
            await provider.send_payout(
                msisdn="46733123453", amount=10, currency="EUR",
                approval_code="DEADBEEFCAFE",
            )
        assert exc.value.reason_code == "approval_invalid"
    finally:
        await provider.aclose()


@respx.mock
async def test_replayed_code_rejected(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(DISB_TOKEN).mock(return_value=_token())
    respx.post(TRANSFER).mock(return_value=httpx.Response(202))
    provider = MTNProvider(settings=settings, store=store)
    try:
        pending = await provider.send_payout(msisdn="46733123453", amount=10, currency="EUR")
        # First use succeeds.
        await provider.confirm_payout(pending.approval_code)
        # Replaying the same code must fail (single-use, §7.1).
        with pytest.raises(GuardrailRejection) as exc:
            await provider.confirm_payout(pending.approval_code)
        assert exc.value.reason_code == "approval_invalid"
    finally:
        await provider.aclose()


@respx.mock
async def test_code_amount_mismatch_rejected(monkeypatch, store):
    settings = _settings(monkeypatch)
    provider = MTNProvider(settings=settings, store=store)
    try:
        pending = await provider.send_payout(msisdn="46733123453", amount=10, currency="EUR")
        # Same valid code, but try to push a different amount through it.
        with pytest.raises(GuardrailRejection) as exc:
            await provider.send_payout(
                msisdn="46733123453", amount=99, currency="EUR",
                approval_code=pending.approval_code,
            )
        assert exc.value.reason_code == "approval_mismatch"
    finally:
        await provider.aclose()


@respx.mock
async def test_payout_over_limit_rejected_before_approval(monkeypatch, store):
    settings = _settings(monkeypatch, MAX_AMOUNT_PER_TX="50")
    provider = MTNProvider(settings=settings, store=store)
    try:
        with pytest.raises(GuardrailRejection) as exc:
            await provider.send_payout(msisdn="46733123453", amount=100, currency="EUR")
        assert exc.value.reason_code == "amount_over_limit"
    finally:
        await provider.aclose()


@respx.mock
async def test_payout_dry_run_no_http(monkeypatch, store):
    settings = _settings(monkeypatch, DRY_RUN="true", REQUIRE_PAYOUT_APPROVAL="false")
    transfer = respx.post(TRANSFER).mock(return_value=httpx.Response(202))
    provider = MTNProvider(settings=settings, store=store)
    try:
        res = await provider.send_payout(msisdn="46733123453", amount=10, currency="EUR")
        assert res.dry_run is True
        assert transfer.call_count == 0
    finally:
        await provider.aclose()
