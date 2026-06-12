"""Ledger integrity: idempotency, reconciliation worklist, daily counters,
append-only audit, and one-time approval consumption."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from momo_mcp.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    yield s
    s.close()


def _create(store, ref="ref-1", *, amount=10.0, dry_run=False, kind="collection"):
    return store.create_transaction(
        reference_id=ref, kind=kind, tool="request_payment",
        msisdn="46733123450", amount=amount, currency="EUR", dry_run=dry_run,
    )


def test_create_and_get(store):
    tx = _create(store)
    assert tx.status == "PENDING"
    fetched = store.get_transaction("ref-1")
    assert fetched is not None
    assert fetched.amount == 10.0


def test_duplicate_reference_id_rejected(store):
    """The idempotency guarantee: a reused reference_id cannot create a 2nd row."""
    _create(store, "dup")
    with pytest.raises(sqlite3.IntegrityError):
        _create(store, "dup")


def test_pending_reconciliation_worklist(store):
    _create(store, "p1")
    _create(store, "p2")
    store.update_status("p1", "SUCCESSFUL")
    pending = store.pending_transactions()
    assert [t.reference_id for t in pending] == ["p2"]


def test_update_status_unknown_ref_raises(store):
    with pytest.raises(KeyError):
        store.update_status("nope", "SUCCESSFUL")


def test_update_status_validates_state(store):
    _create(store, "s1")
    with pytest.raises(ValueError, match="unknown status"):
        store.update_status("s1", "BOGUS")


def test_daily_usage_counts_dry_run_for_limits_by_default(store):
    """Limits must apply even in DRY_RUN, so the default counts dry-run rows;
    rejected rows are always excluded."""
    _create(store, "real1", amount=10.0, dry_run=False)
    _create(store, "real2", amount=15.0, dry_run=False)
    _create(store, "dry1", amount=99.0, dry_run=True)       # counted (limits)
    _create(store, "rej1", amount=50.0, dry_run=False)
    store.update_status("rej1", "REJECTED")                  # excluded: rejected
    usage = store.daily_usage()
    assert usage.tx_count == 3
    assert usage.total_amount == 124.0


def test_daily_usage_real_money_only_excludes_dry_run(store):
    """Reporting of money actually moved excludes dry-run rows."""
    _create(store, "real1", amount=10.0, dry_run=False)
    _create(store, "dry1", amount=99.0, dry_run=True)
    usage = store.daily_usage(include_dry_run=False)
    assert usage.tx_count == 1
    assert usage.total_amount == 10.0


def test_audit_is_append_only_and_ordered(store):
    store.record_audit(tool="request_payment", input_hash="h1", outcome="ok", latency_ms=12)
    store.record_audit(tool="send_payout", input_hash="h2", outcome="rejected:limit")
    rows = store.recent_audit()
    assert len(rows) == 2
    assert rows[0]["tool"] == "send_payout"  # most recent first
    assert rows[1]["outcome"] == "ok"


def test_list_transactions_filters(store):
    _create(store, "a", amount=5.0)
    _create(store, "b", amount=7.0)
    store.update_status("b", "SUCCESSFUL")
    assert len(store.list_transactions(status="SUCCESSFUL")) == 1
    assert len(store.list_transactions(msisdn="46733123450")) == 2
    assert len(store.list_transactions(status="PENDING")) == 1


def _future(minutes=10):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _past(minutes=10):
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def test_approval_single_use(store):
    store.create_approval(code="C1", msisdn="46733123450", amount=20.0,
                          currency="EUR", expires_at=_future())
    first = store.consume_approval("C1")
    assert first is not None
    # Replay must fail.
    assert store.consume_approval("C1") is None


def test_get_approval_is_read_only(store):
    store.create_approval(code="PEEK", msisdn="46733123450", amount=20.0,
                          currency="EUR", expires_at=_future())
    row = store.get_approval("PEEK")
    assert row is not None and row["msisdn"] == "46733123450"
    assert row["consumed_at"] is None
    # Peeking must NOT consume, the code is still usable afterwards.
    assert store.consume_approval("PEEK") is not None
    assert store.get_approval("missing") is None


def test_approval_expired_rejected(store):
    store.create_approval(code="C2", msisdn="46733123450", amount=20.0,
                          currency="EUR", expires_at=_past())
    assert store.consume_approval("C2") is None


def test_approval_unknown_rejected(store):
    assert store.consume_approval("does-not-exist") is None


def test_reset_limits_clears_count_but_keeps_history(store):
    for i in range(3):
        store.create_transaction(
            reference_id=f"r{i}", kind="collection", tool="t",
            msisdn="46733123450", amount=10, currency="EUR", dry_run=False,
        )
    assert store.daily_usage().tx_count == 3
    store.reset_limits(note="manual reset")
    # Usage counts only post-reset transactions, so the counter clears...
    assert store.daily_usage().tx_count == 0
    # ...but the ledger history is preserved (audit/reconciliation intact).
    assert len(store.list_transactions()) == 3


def test_idempotency_survives_reopen(tmp_path):
    """Persisted-before-send survives a process restart: reopen the DB file and
    the PENDING row is still there for reconciliation."""
    path = tmp_path / "persist.sqlite3"
    s1 = Store(path)
    _create(s1, "crash-ref")
    s1.close()  # simulate crash/restart, no status update happened
    s2 = Store(path)
    pending = s2.pending_transactions()
    assert [t.reference_id for t in pending] == ["crash-ref"]
    s2.close()
