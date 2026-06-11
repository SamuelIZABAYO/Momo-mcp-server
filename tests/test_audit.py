"""Audit helper: one append-only row per call, no raw values, holds on errors."""

from __future__ import annotations

import pytest

from momo_mcp.audit import audit_call, hash_input
from momo_mcp.providers.base import GuardrailRejection
from momo_mcp.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "a.sqlite3")
    yield s
    s.close()


def test_hash_input_is_stable_and_value_free():
    h1 = hash_input({"msisdn": "46733123450", "amount": 10})
    h2 = hash_input({"amount": 10, "msisdn": "46733123450"})  # key order irrelevant
    assert h1 == h2
    assert "46733123450" not in h1  # raw value not recoverable from the digest


def test_success_records_ok(store):
    with audit_call(store, tool="request_payment", payload={"a": 1}) as scope:
        scope.reference_id = "ref-1"
    row = store.recent_audit()[0]
    assert row["tool"] == "request_payment"
    assert row["outcome"] == "ok"
    assert row["reference_id"] == "ref-1"
    assert row["latency_ms"] is not None


def test_custom_outcome(store):
    with audit_call(store, tool="check_payment_status", payload={}) as scope:
        scope.outcome = "SUCCESSFUL"
    assert store.recent_audit()[0]["outcome"] == "SUCCESSFUL"


def test_guardrail_rejection_recorded_with_reason(store):
    with pytest.raises(GuardrailRejection), audit_call(
        store, tool="send_payout", payload={"amount": 999}
    ):
        raise GuardrailRejection("nope", reason_code="amount_over_limit")
    assert store.recent_audit()[0]["outcome"] == "rejected:amount_over_limit"


def test_error_recorded(store):
    with pytest.raises(ValueError), audit_call(store, tool="get_balance", payload={}):
        raise ValueError("boom")
    assert store.recent_audit()[0]["outcome"] == "error:ValueError"


def test_one_row_per_call(store):
    for i in range(3):
        with audit_call(store, tool="t", payload={"i": i}):
            pass
    assert len(store.recent_audit()) == 3
