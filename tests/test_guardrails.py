"""Guardrail checks exercised directly, the seeds of the safety suite."""

from __future__ import annotations

import pytest

from momo_mcp.config import load_settings
from momo_mcp.guardrails import (
    PAUSE_FILE,
    check_allowlist,
    check_amount,
    check_daily_limits,
    enforce_mutation,
)
from momo_mcp.providers.base import GuardrailRejection
from momo_mcp.store import Store

_ENV = {
    "MOMO_COLLECTION_SUBSCRIPTION_KEY": "a" * 32,
    "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY": "b" * 32,
    "MOMO_BASE_URL": "https://sandbox.momodeveloper.mtn.com",
    "MAX_AMOUNT_PER_TX": "100",
    "MAX_DAILY_TX_COUNT": "3",
    "MAX_DAILY_TOTAL": "150",
    "MSISDN_ALLOWLIST": "46733123453,46733123450",
}


@pytest.fixture
def settings(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    return load_settings(env_file=None)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "g.sqlite3")
    yield s
    s.close()


def test_allowlist_blocks_unknown(settings):
    with pytest.raises(GuardrailRejection) as exc:
        check_allowlist("46700000999", settings)
    assert exc.value.reason_code == "msisdn_not_allowlisted"
    assert "do not retry" in str(exc.value).lower()


def test_allowlist_permits_known(settings):
    check_allowlist("46733123453", settings)  # no raise


def test_amount_over_limit_rejected_not_split(settings):
    with pytest.raises(GuardrailRejection) as exc:
        check_amount(150, settings)
    assert exc.value.reason_code == "amount_over_limit"
    assert "not split" in str(exc.value).lower()


def test_daily_count_limit(settings, store):
    for i in range(3):
        store.create_transaction(
            reference_id=f"r{i}", kind="collection", tool="t",
            msisdn="46733123453", amount=10, currency="EUR", dry_run=False,
        )
    with pytest.raises(GuardrailRejection) as exc:
        check_daily_limits(10, settings, store)
    assert exc.value.reason_code == "daily_count_exceeded"


def test_daily_total_limit(settings, store):
    store.create_transaction(
        reference_id="big", kind="collection", tool="t",
        msisdn="46733123453", amount=145, currency="EUR", dry_run=False,
    )
    with pytest.raises(GuardrailRejection) as exc:
        check_daily_limits(10, settings, store)  # 145 + 10 > 150
    assert exc.value.reason_code == "daily_total_exceeded"


def test_pause_file_blocks_all(settings, store, tmp_path):
    (tmp_path / PAUSE_FILE).touch()
    with pytest.raises(GuardrailRejection) as exc:
        enforce_mutation(
            msisdn="46733123453", amount=10, settings=settings,
            store=store, workdir=tmp_path,
        )
    assert exc.value.reason_code == "paused"


def test_enforce_mutation_happy_path(settings, store, tmp_path):
    # On the allowlist, under limits, no PAUSE -> no raise.
    enforce_mutation(
        msisdn="46733123453", amount=10, settings=settings,
        store=store, workdir=tmp_path,
    )
