"""Live sandbox suite — hits the REAL MTN sandbox. Opt-in only.

Run with:  pytest -m live
Requires a valid .env (subscription keys + provisioned API user/key). Skipped
automatically if config can't load. Exercises EVERY magic-number outcome so the
reason→status normalization is proven against reality (spec §3.5).
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
    # after our ~60s poll budget (see GOTCHAS §3). PENDING is a correct,
    # non-terminal state — not a mapping error — so accept it. What we are
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
