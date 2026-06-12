"""MCP server: tools are discoverable and callable in-process, audited, and the
ledger resource works. Runs in DRY_RUN so no HTTP is needed."""

from __future__ import annotations

import json

import pytest

import momo_mcp.server as server
from momo_mcp.app import AppContext
from momo_mcp.config import load_settings
from momo_mcp.providers.mtn import MTNProvider
from momo_mcp.store import Store

_ENV = {
    "MOMO_COLLECTION_SUBSCRIPTION_KEY": "a" * 32,
    "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY": "b" * 32,
    "MOMO_BASE_URL": "https://sandbox.momodeveloper.mtn.com",
    "MOMO_CALLBACK_HOST": "https://callback.example",
    "MOMO_API_USER": "11111111-1111-1111-1111-111111111111",
    "MOMO_API_KEY": "k" * 16,
    "MSISDN_ALLOWLIST": "46733123453",
    "DRY_RUN": "true",
    "REQUIRE_PAYOUT_APPROVAL": "true",
}


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MOMO_DB_PATH", str(tmp_path / "srv.sqlite3"))
    settings = load_settings(env_file=None)
    store = Store(settings.db_path)
    provider = MTNProvider(settings=settings, store=store)
    context = AppContext(settings=settings, store=store, provider=provider)
    # Inject our context into the module-global the tools close over.
    monkeypatch.setattr(server, "_ctx", context)
    yield context
    store.close()


async def test_all_tools_discoverable():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "request_payment", "check_payment_status", "send_payout", "confirm_payout",
        "get_balance", "validate_account", "list_transactions", "get_provider_health",
        "list_audit",
    }
    # Each tool has a non-trivial client-facing description.
    for t in tools:
        assert t.description and len(t.description) > 40


async def test_request_payment_dry_run_and_audited(ctx):
    res = await server.request_payment(msisdn="46733123453", amount=5)
    assert res["ok"] is True
    assert res["dry_run"] is True
    assert res["transaction_id"]
    # Audited: exactly one row for this tool.
    audit = ctx.store.recent_audit()
    assert audit[0]["tool"] == "request_payment"
    assert audit[0]["reference_id"] == res["transaction_id"]


async def test_payout_gate_via_tools(ctx):
    pending = await server.send_payout(msisdn="46733123453", amount=5)
    assert pending["ok"] is True
    assert pending["pending_approval"] is True
    code = pending["approval_code"]
    done = await server.confirm_payout(approval_code=code)
    assert done["ok"] is True
    assert done["transaction_id"]


async def test_guardrail_rejection_surfaced_not_raised(ctx):
    # Off-allowlist number -> structured rejection, not an exception.
    res = await server.request_payment(msisdn="46700000999", amount=5)
    assert res["ok"] is False
    assert res["rejected"] is True
    assert res["reason_code"] == "msisdn_not_allowlisted"
    # And it was audited as a rejection.
    assert ctx.store.recent_audit()[0]["outcome"] == "rejected:msisdn_not_allowlisted"


async def test_list_transactions_tool(ctx):
    await server.request_payment(msisdn="46733123453", amount=5)
    res = await server.list_transactions()
    assert res["ok"] is True
    assert res["count"] >= 1


async def test_health_tool(ctx):
    res = await server.get_provider_health()
    assert res["ok"] is True
    assert res["provider"] == "mtn"
    assert "daily_tx_count" in res


async def test_ledger_resource(ctx):
    await server.request_payment(msisdn="46733123453", amount=5)
    payload = server.recent_ledger()
    rows = json.loads(payload)
    assert isinstance(rows, list)
    assert rows and rows[0]["kind"] == "collection"


async def test_list_audit_tool(ctx):
    await server.request_payment(msisdn="46733123453", amount=5)
    res = await server.list_audit()
    assert res["ok"] is True
    assert res["count"] >= 1
    row = res["audit"][0]
    assert row["tool"] == "request_payment"
    # Audit rows carry a hash, not raw inputs.
    assert "msisdn" not in row and "amount" not in row
    assert "input_hash" in row


async def test_audit_resource(ctx):
    await server.request_payment(msisdn="46733123453", amount=5)
    payload = server.recent_audit_resource()
    rows = json.loads(payload)
    assert isinstance(rows, list)
    assert rows and rows[0]["tool"] == "request_payment"
