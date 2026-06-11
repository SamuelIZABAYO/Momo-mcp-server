"""Log redaction: MSISDNs masked to last-4, secret-shaped strings scrubbed."""

from __future__ import annotations

from momo_mcp.logging_conf import mask_msisdn, redact


def test_mask_msisdn_last4():
    assert mask_msisdn("46733123450") == "*******3450"
    assert mask_msisdn("+250 788 123 456") == "********3456"  # 12 digits → 8 stars + 3456
    assert mask_msisdn(None) == "<none>"
    assert mask_msisdn("12") == "**"


def test_redact_bearer_token():
    out = redact("Authorization: Bearer abc.def-123_XYZ")
    assert "abc.def-123_XYZ" not in out
    assert "Bearer <redacted>" in out


def test_redact_basic_auth():
    out = redact("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in out


def test_redact_32hex_subscription_key():
    key = "0123456789abcdef0123456789abcdef"
    out = redact(f"subscription key is {key}")
    assert key not in out
    assert "<redacted-key>" in out


def test_redact_leaves_plain_text():
    assert redact("payment SUCCESSFUL for tx ref-1") == "payment SUCCESSFUL for tx ref-1"
