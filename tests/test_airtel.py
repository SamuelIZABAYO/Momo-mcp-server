"""Airtel stub: implements the interface; every method reports not-implemented."""

from __future__ import annotations

import pytest

from momo_mcp.providers.airtel import AirtelProvider
from momo_mcp.providers.base import PaymentProvider


def test_airtel_implements_interface():
    assert issubclass(AirtelProvider, PaymentProvider)


async def test_methods_raise_not_implemented():
    p = AirtelProvider()
    for call in (
        p.request_payment(msisdn="1", amount=1, currency="EUR"),
        p.check_payment_status("x"),
        p.get_balance("collection"),
        p.validate_account("1"),
        p.send_payout(msisdn="1", amount=1, currency="EUR"),
        p.confirm_payout("code"),
    ):
        with pytest.raises(NotImplementedError, match="not implemented"):
            await call


async def test_health_reports_not_implemented_without_raising():
    p = AirtelProvider()
    health = await p.health()
    assert health.provider == "airtel"
    assert health.token_valid is False
    assert health.details["status"] == "not_implemented"
