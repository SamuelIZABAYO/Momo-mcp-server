"""MTN provider behavior with mocked HTTP (respx): status normalization,
persisted-before-send idempotency, dry-run, 401-retry-once, and the empty-202
contract."""

from __future__ import annotations

import httpx
import pytest
import respx

from momo_mcp.config import load_settings
from momo_mcp.providers.base import GuardrailRejection, PaymentStatus, ProviderError
from momo_mcp.providers.mtn import MTNProvider, normalize_status
from momo_mcp.store import Store

BASE = "https://sandbox.momodeveloper.mtn.com"
TOKEN_URL = f"{BASE}/collection/token/"
RTP_URL = f"{BASE}/collection/v1_0/requesttopay"

_ENV = {
    "MOMO_COLLECTION_SUBSCRIPTION_KEY": "a" * 32,
    "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY": "b" * 32,
    "MOMO_BASE_URL": BASE,
    "MOMO_API_USER": "11111111-1111-1111-1111-111111111111",
    "MOMO_API_KEY": "k" * 16,
    "MSISDN_ALLOWLIST": "46733123453,46733123450,46733123451,46733123452",
    "DRY_RUN": "false",
}


def _settings(monkeypatch, **overrides):
    for k, v in {**_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)
    return load_settings(env_file=None)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "m.sqlite3")
    yield s
    s.close()


def _token():
    return httpx.Response(200, json={"access_token": "t", "token_type": "access_token", "expires_in": 3600})


# ── normalization (the reason→status logic, GOTCHAS) ──────────────────────
@pytest.mark.parametrize(
    "raw,reason,expected",
    [
        ("SUCCESSFUL", None, PaymentStatus.SUCCESSFUL),
        ("PENDING", None, PaymentStatus.PENDING),
        ("FAILED", "APPROVAL_REJECTED", PaymentStatus.REJECTED),
        ("FAILED", "EXPIRED", PaymentStatus.TIMEOUT),
        ("FAILED", "INTERNAL_PROCESSING_ERROR", PaymentStatus.FAILED),
        ("FAILED", None, PaymentStatus.FAILED),
        ("WEIRD", None, PaymentStatus.FAILED),
    ],
)
def test_normalize_status(raw, reason, expected):
    assert normalize_status(raw, reason) == expected


# ── request_payment ──────────────────────────────────────────────────────────
@respx.mock
async def test_request_payment_persists_before_send_and_accepts_202(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(TOKEN_URL).mock(return_value=_token())
    route = respx.post(RTP_URL).mock(return_value=httpx.Response(202))  # empty body
    provider = MTNProvider(settings=settings, store=store)
    try:
        result = await provider.request_payment(
            msisdn="46733123453", amount=5, currency="EUR"
        )
        assert result.status == PaymentStatus.PENDING
        # The ledger row exists (persisted before send) with the same id.
        tx = store.get_transaction(result.transaction_id)
        assert tx is not None and tx.status == "PENDING"
        # The X-Reference-Id header equals the transaction id (idempotency key).
        sent = route.calls.last.request
        assert sent.headers["X-Reference-Id"] == result.transaction_id
    finally:
        await provider.aclose()


@respx.mock
async def test_request_payment_guardrail_blocks_unknown_msisdn(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(TOKEN_URL).mock(return_value=_token())
    rtp = respx.post(RTP_URL).mock(return_value=httpx.Response(202))
    provider = MTNProvider(settings=settings, store=store)
    try:
        with pytest.raises(GuardrailRejection):
            await provider.request_payment(msisdn="46700000999", amount=5, currency="EUR")
        # Guardrail fired BEFORE any HTTP call, no requesttopay sent, no ledger row.
        assert rtp.call_count == 0
        assert store.list_transactions() == []
    finally:
        await provider.aclose()


@respx.mock
async def test_request_payment_dry_run_makes_no_http_call(monkeypatch, store):
    settings = _settings(monkeypatch, DRY_RUN="true")
    rtp = respx.post(RTP_URL).mock(return_value=httpx.Response(202))
    provider = MTNProvider(settings=settings, store=store)
    try:
        result = await provider.request_payment(msisdn="46733123453", amount=5, currency="EUR")
        assert result.dry_run is True
        assert rtp.call_count == 0
        tx = store.get_transaction(result.transaction_id)
        assert tx is not None and tx.dry_run is True
    finally:
        await provider.aclose()


@respx.mock
async def test_request_payment_500_raises_but_keeps_ledger_row(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(TOKEN_URL).mock(return_value=_token())
    respx.post(RTP_URL).mock(return_value=httpx.Response(500, text="upstream"))
    provider = MTNProvider(settings=settings, store=store)
    try:
        with pytest.raises(ProviderError):
            await provider.request_payment(msisdn="46733123453", amount=5, currency="EUR")
        # Row persisted before send is still there for reconciliation.
        assert len(store.list_transactions(status="PENDING")) == 1
    finally:
        await provider.aclose()


# ── check_payment_status ─────────────────────────────────────────────────────
@respx.mock
async def test_check_status_maps_rejected(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(TOKEN_URL).mock(return_value=_token())
    respx.post(RTP_URL).mock(return_value=httpx.Response(202))
    provider = MTNProvider(settings=settings, store=store)
    try:
        req = await provider.request_payment(msisdn="46733123451", amount=5, currency="EUR")
        respx.get(f"{BASE}/collection/v1_0/requesttopay/{req.transaction_id}").mock(
            return_value=httpx.Response(200, json={"status": "FAILED", "reason": "APPROVAL_REJECTED"})
        )
        res = await provider.check_payment_status(req.transaction_id)
        assert res.status == PaymentStatus.REJECTED
        # Ledger updated to terminal state.
        assert store.get_transaction(req.transaction_id).status == "REJECTED"
    finally:
        await provider.aclose()


@respx.mock
async def test_check_status_401_refreshes_once_and_succeeds(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(TOKEN_URL).mock(
        side_effect=[_token(), _token()]  # initial + one forced refresh
    )
    respx.post(RTP_URL).mock(return_value=httpx.Response(202))
    provider = MTNProvider(settings=settings, store=store)
    try:
        req = await provider.request_payment(msisdn="46733123453", amount=5, currency="EUR")
        status_url = f"{BASE}/collection/v1_0/requesttopay/{req.transaction_id}"
        respx.get(status_url).mock(
            side_effect=[
                httpx.Response(401),  # triggers one refresh + retry
                httpx.Response(200, json={"status": "SUCCESSFUL"}),
            ]
        )
        res = await provider.check_payment_status(req.transaction_id)
        assert res.status == PaymentStatus.SUCCESSFUL
    finally:
        await provider.aclose()


async def test_check_status_unknown_id_raises(monkeypatch, store):
    settings = _settings(monkeypatch)
    provider = MTNProvider(settings=settings, store=store)
    try:
        with pytest.raises(ProviderError, match="No transaction"):
            await provider.check_payment_status("not-a-real-id")
    finally:
        await provider.aclose()


@respx.mock
async def test_crash_resume_no_double_charge(monkeypatch, tmp_path):
    """ acceptance: a crash mid-call leaves a recoverable PENDING row, and
    resuming reuses the SAME reference_id, MTN dedupes, so no double charge.

    We simulate the crash by persisting the row then 'restarting' (reopening the
    DB) and asserting the pending row is the reconciliation worklist, and that a
    status check reuses the stored reference_id rather than minting a new one.
    """
    settings = _settings(monkeypatch)
    db = tmp_path / "crash.sqlite3"

    # Pre-crash process: row persisted before send (no HTTP yet).
    store1 = Store(db)
    store1.create_transaction(
        reference_id="fixed-ref-123", kind="collection", tool="request_payment",
        msisdn="46733123453", amount=5, currency="EUR", dry_run=False,
    )
    store1.close()  # crash before the 202 came back

    # Post-restart process: reconcile the PENDING row via its stored reference_id.
    respx.post(TOKEN_URL).mock(return_value=_token())
    store2 = Store(db)
    pending = store2.pending_transactions()
    assert [t.reference_id for t in pending] == ["fixed-ref-123"]

    provider = MTNProvider(settings=settings, store=store2)
    try:
        status_route = respx.get(
            f"{BASE}/collection/v1_0/requesttopay/fixed-ref-123"
        ).mock(return_value=httpx.Response(200, json={"status": "SUCCESSFUL"}))
        res = await provider.check_payment_status("fixed-ref-123")
        assert res.status == PaymentStatus.SUCCESSFUL
        # The status check used the stored reference_id (idempotency key reuse).
        assert "fixed-ref-123" in str(status_route.calls.last.request.url)
    finally:
        await provider.aclose()
        store2.close()


@respx.mock
async def test_dry_run_status_resolves_successful(monkeypatch, store):
    settings = _settings(monkeypatch, DRY_RUN="true")
    provider = MTNProvider(settings=settings, store=store)
    try:
        req = await provider.request_payment(msisdn="46733123453", amount=5, currency="EUR")
        res = await provider.check_payment_status(req.transaction_id)
        assert res.status == PaymentStatus.SUCCESSFUL
        assert res.dry_run is True
    finally:
        await provider.aclose()


# ── get_balance / validate_account ───────────────────────────────────────────
async def test_get_balance_dry_run(monkeypatch, store):
    settings = _settings(monkeypatch, DRY_RUN="true")
    provider = MTNProvider(settings=settings, store=store)
    try:
        bal = await provider.get_balance("collection")
        assert bal.dry_run is True
        assert bal.currency == "EUR"
    finally:
        await provider.aclose()


@respx.mock
async def test_get_balance_surfaces_sandbox_block(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(TOKEN_URL).mock(return_value=_token())
    respx.get(f"{BASE}/collection/v1_0/account/balance").mock(
        return_value=httpx.Response(500, json={"code": "NOT_ALLOWED_TARGET_ENVIRONMENT"})
    )
    provider = MTNProvider(settings=settings, store=store)
    try:
        with pytest.raises(ProviderError, match="not permitted in the sandbox"):
            await provider.get_balance("collection")
    finally:
        await provider.aclose()


async def test_validate_account_dry_run(monkeypatch, store):
    settings = _settings(monkeypatch, DRY_RUN="true")
    provider = MTNProvider(settings=settings, store=store)
    try:
        v = await provider.validate_account("46733123453")
        assert v.is_active is True
        assert v.dry_run is True
        assert "3453" in v.msisdn_masked  # last-4 preserved, rest masked
    finally:
        await provider.aclose()


@respx.mock
async def test_validate_account_404_is_honest(monkeypatch, store):
    settings = _settings(monkeypatch)
    respx.post(TOKEN_URL).mock(return_value=_token())
    respx.get(f"{BASE}/collection/v1_0/accountholder/msisdn/46733123450/active").mock(
        return_value=httpx.Response(404, json={"code": "RESOURCE_NOT_FOUND"})
    )
    provider = MTNProvider(settings=settings, store=store)
    try:
        v = await provider.validate_account("46733123450")
        assert v.is_active is False
        assert "unreliable" in v.message.lower()
    finally:
        await provider.aclose()
