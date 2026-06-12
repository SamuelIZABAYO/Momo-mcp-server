"""Live sandbox suite, hits the REAL MTN sandbox. Opt-in only.

Run with:  pytest -m live
Requires a valid .env (subscription keys + provisioned API user/key). Skipped
automatically if config can't load. Exercises EVERY magic-number outcome so the
reason→status normalization is proven against reality.
"""

from __future__ import annotations

import pytest

from momo_mcp.config import ConfigError, load_settings
from momo_mcp.providers.base import PaymentStatus
from momo_mcp.providers.mtn import MTNProvider
from momo_mcp.store import Store

from .fixtures import SANDBOX_OUTCOMES

pytestmark = pytest.mark.live


@pytest.fixture
def live_settings():
    try:
        settings = load_settings()
    except ConfigError as exc:
        pytest.skip(f"live config unavailable: {exc}")
    if not settings.provisioned:
        pytest.skip("API user/key not provisioned; run scripts/provision.py")
    # Force real calls even if .env has DRY_RUN=true.
    object.__setattr__(settings, "dry_run", False)
    return settings


@pytest.fixture
async def provider(live_settings, tmp_path):
    store = Store(tmp_path / "live.sqlite3")
    p = MTNProvider(settings=live_settings, store=store)
    yield p
    await p.aclose()
    store.close()


@pytest.mark.parametrize("msisdn,expected", [
    (m, info[2]) for m, info in SANDBOX_OUTCOMES.items()
])
async def test_live_request_and_status_all_outcomes(provider, msisdn, expected):
    req = await provider.request_payment(msisdn=msisdn, amount=5, currency="EUR")
    assert req.status == PaymentStatus.PENDING
    assert req.transaction_id

    res = await provider.check_payment_status(req.transaction_id)
    # Sandbox timing is non-deterministic: a transaction may still be PENDING
    # after our ~60s poll budget (see GOTCHAS). PENDING is a correct,
    # non-terminal state, not a mapping error, so accept it. What we are
    # really proving is that NON-pending outcomes normalize correctly: a number
    # that is meant to FAIL/REJECT/TIMEOUT must never come back SUCCESSFUL, and
    # vice versa.
    if res.status == PaymentStatus.PENDING:
        pytest.skip(
            f"{msisdn} still PENDING after poll budget (sandbox latency); "
            "mapping not contradicted"
        )
    assert res.status == PaymentStatus(expected), (
        f"{msisdn}: expected {expected}, got {res.status.value} "
        f"(raw={res.raw_status})"
    )


async def test_live_payout_approval_gate(provider):
    """send_payout must not move money without approval, then must execute with a
    valid code, verified against the real disbursement transfer endpoint."""
    pending = await provider.send_payout(msisdn="46733123451", amount=5, currency="EUR")
    assert pending.pending_approval is True
    assert pending.transaction_id is None  # nothing sent yet

    done = await provider.confirm_payout(pending.approval_code)
    assert done.transaction_id  # a transfer was issued
    res = await provider.check_payment_status(done.transaction_id)
    # 46733123451 -> APPROVAL_REJECTED -> REJECTED (or still PENDING under latency)
    assert res.status in (PaymentStatus.REJECTED, PaymentStatus.PENDING)


async def test_live_balance_handled_either_way(provider):
    """Balance is inconsistently available in sandbox (GOTCHAS): it has
    returned 500 NOT_ALLOWED, 404 RESOURCE_NOT_FOUND, and 200 on different runs.
    Whichever happens, the tool must behave sanely, either raise a clear
    ProviderError or return a structured BalanceResult, never crash. The
    deterministic blocked-path assertion lives in the mocked unit tests."""
    from momo_mcp.providers.base import BalanceResult, ProviderError

    try:
        result = await provider.get_balance("collection")
        assert isinstance(result, BalanceResult)
    except ProviderError as exc:
        assert str(exc)  # clear, non-empty message
