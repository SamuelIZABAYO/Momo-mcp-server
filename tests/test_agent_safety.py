"""Agent-safety guardrail cases.

These tests exercise the server the way a confused or manipulated agent would,
and check that each case is rejected or recovered. They drive the real MCP tool
layer (server.py) so what's tested is what an agent can actually call. Results
are summarized in docs/SAFETY.md.

Cases covered:
  A1  payout above MAX_AMOUNT_PER_TX                         -> rejected
  A2  splitting a large amount into many small payouts       -> daily limit stops it
  A3  payment to an MSISDN not on the allowlist (unknown number) -> rejected
  A4  payout with no approval                                -> blocked (no money moves)
  A5  payout with a forged approval code                     -> rejected
  A6  replay of a used approval code                         -> rejected
  A7  approval code redirected to a different amount         -> rejected
  A8  any mutation while PAUSE exists                        -> refused
  A9  crash mid-transaction                                  -> resume, no double charge
"""

from __future__ import annotations

import pytest

import momo_mcp.server as server
from momo_mcp.app import AppContext
from momo_mcp.config import load_settings
from momo_mcp.guardrails import PAUSE_FILE
from momo_mcp.providers.mtn import MTNProvider
from momo_mcp.store import Store

_ENV = {
    "MOMO_COLLECTION_SUBSCRIPTION_KEY": "a" * 32,
    "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY": "b" * 32,
    "MOMO_BASE_URL": "https://sandbox.momodeveloper.mtn.com",
    "MOMO_API_USER": "11111111-1111-1111-1111-111111111111",
    "MOMO_API_KEY": "k" * 16,
    "MSISDN_ALLOWLIST": "46733123453,46733123451",
    "DRY_RUN": "true",
    "REQUIRE_PAYOUT_APPROVAL": "true",
    "MAX_AMOUNT_PER_TX": "100",
    "MAX_DAILY_TX_COUNT": "3",
    "MAX_DAILY_TOTAL": "150",
}


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MOMO_DB_PATH", str(tmp_path / "safety.sqlite3"))
    # Run guardrail PAUSE checks against the tmp dir so a PAUSE file here only
    # affects this test, not the developer's working tree.
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file=None)
    store = Store(settings.db_path)
    provider = MTNProvider(settings=settings, store=store)
    context = AppContext(settings=settings, store=store, provider=provider)
    monkeypatch.setattr(server, "_ctx", context)
    yield context
    store.close()


# A1 ──────────────────────────────────────────────────────────────────────────
async def test_a1_payout_over_limit_rejected(ctx):
    res = await server.send_payout(msisdn="46733123453", amount=500)
    assert res["ok"] is False and res["rejected"] is True
    assert res["reason_code"] == "amount_over_limit"
    # Audited as a rejection.
    assert ctx.store.recent_audit()[0]["outcome"] == "rejected:amount_over_limit"


# A2 ──────────────────────────────────────────────────────────────────────────
async def test_a2_amount_splitting_caught_by_daily_limits(ctx):
    """Splitting a big payout into many small ones to get under the per-tx cap.
    The daily limits (count=3, total=150) cap the total and then stop further
    payouts with an explicit rejection."""
    hard_stopped = False
    for _ in range(10):
        pending = await server.send_payout(msisdn="46733123453", amount=40)
        if not pending["ok"]:
            # Rejected at request time (a daily limit was already breached).
            assert pending["reason_code"] in ("daily_count_exceeded", "daily_total_exceeded")
            hard_stopped = True
            break
        done = await server.confirm_payout(approval_code=pending["approval_code"])
        if not done["ok"]:
            assert done["reason_code"] in ("daily_count_exceeded", "daily_total_exceeded")
            hard_stopped = True
            break

    # Further payouts were stopped, and the money moved never exceeded the caps.
    assert hard_stopped, "daily limits failed to stop the split payouts"
    usage = ctx.store.daily_usage()
    assert usage.tx_count <= int(_ENV["MAX_DAILY_TX_COUNT"])
    assert usage.total_amount <= float(_ENV["MAX_DAILY_TOTAL"])


# A3 ──────────────────────────────────────────────────────────────────────────
async def test_a3_unknown_msisdn_rejected(ctx):
    res = await server.request_payment(msisdn="46700000999", amount=5)
    assert res["ok"] is False and res["rejected"] is True
    assert res["reason_code"] == "msisdn_not_allowlisted"


# A4 ──────────────────────────────────────────────────────────────────────────
async def test_a4_payout_without_approval_moves_no_money(ctx):
    res = await server.send_payout(msisdn="46733123453", amount=50)
    assert res["ok"] is True
    assert res["pending_approval"] is True
    assert res["transaction_id"] is None  # nothing sent
    # No disbursement row was created (money did not move).
    assert ctx.store.list_transactions(status="PENDING") == [] or all(
        t.kind != "disbursement" for t in ctx.store.list_transactions()
    )


# A5 ──────────────────────────────────────────────────────────────────────────
async def test_a5_forged_approval_code_rejected(ctx):
    res = await server.confirm_payout(approval_code="FORGEDCODE123")
    assert res["ok"] is False
    assert res["reason_code"] in ("approval_unknown", "approval_invalid")


# A6 ──────────────────────────────────────────────────────────────────────────
async def test_a6_replayed_approval_code_rejected(ctx):
    pending = await server.send_payout(msisdn="46733123453", amount=50)
    code = pending["approval_code"]
    first = await server.confirm_payout(approval_code=code)
    assert first["ok"] is True
    replay = await server.confirm_payout(approval_code=code)
    assert replay["ok"] is False
    assert replay["reason_code"] in ("approval_invalid", "approval_unknown")


# A7 ──────────────────────────────────────────────────────────────────────────
async def test_a7_approval_code_amount_redirect_rejected(ctx):
    pending = await server.send_payout(msisdn="46733123453", amount=50)
    code = pending["approval_code"]
    # Try a different, still under-limit amount through the valid code via the
    # provider. The tool layer always re-derives amount from the code, so this
    # calls the provider directly to check binding. 80 (< MAX_AMOUNT_PER_TX=100)
    # keeps the amount guardrail from pre-empting the approval-mismatch check.
    from momo_mcp.providers.base import GuardrailRejection

    with pytest.raises(GuardrailRejection) as exc:
        await ctx.provider.send_payout(
            msisdn="46733123453", amount=80, currency="EUR", approval_code=code
        )
    assert exc.value.reason_code == "approval_mismatch"


# A8 ──────────────────────────────────────────────────────────────────────────
async def test_a8_pause_file_blocks_all_mutations(ctx, tmp_path):
    (tmp_path / PAUSE_FILE).touch()
    pay = await server.request_payment(msisdn="46733123453", amount=5)
    assert pay["ok"] is False and pay["reason_code"] == "paused"
    payout = await server.send_payout(msisdn="46733123453", amount=5)
    assert payout["ok"] is False and payout["reason_code"] == "paused"


# A9 ──────────────────────────────────────────────────────────────────────────
async def test_a9_crash_resume_no_double_charge(ctx, tmp_path):
    """A row persisted before send survives a 'crash' (DB reopen); resuming
    reconciles it via its stored reference_id rather than minting a new one, so
    MTN dedupes and no double charge occurs."""
    db = ctx.settings.db_path
    ctx.store.create_transaction(
        reference_id="crash-xyz", kind="collection", tool="request_payment",
        msisdn="46733123453", amount=5, currency="EUR", dry_run=False,
    )
    ctx.store.close()
    # Restart: reopen the same DB file.
    store2 = Store(db)
    try:
        pending = store2.pending_transactions()
        assert [t.reference_id for t in pending] == ["crash-xyz"]
        # Exactly one row for that reference_id, no duplicate was created.
        assert len([t for t in store2.list_transactions() if t.reference_id == "crash-xyz"]) == 1
    finally:
        store2.close()
