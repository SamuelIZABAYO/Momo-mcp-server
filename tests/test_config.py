"""Config validation: fail-fast behavior, secret redaction, sandbox-only policy."""

from __future__ import annotations

import pytest

from momo_mcp.config import ConfigError, load_settings

# A minimal valid env. Tests start from this and mutate one thing at a time.
_VALID = {
    "MOMO_COLLECTION_SUBSCRIPTION_KEY": "a" * 32,
    "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY": "b" * 32,
    "MOMO_TARGET_ENV": "sandbox",
    "MOMO_BASE_URL": "https://sandbox.momodeveloper.mtn.com",
}


def _apply(monkeypatch, env: dict[str, str], *, clear: list[str] | None = None):
    # Clear anything that might leak in from a real .env / CI env.
    for key in [
        "MOMO_COLLECTION_SUBSCRIPTION_KEY", "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY",
        "MOMO_REMITTANCE_SUBSCRIPTION_KEY", "MOMO_API_USER", "MOMO_API_KEY",
        "MOMO_TARGET_ENV", "MOMO_BASE_URL", "MOMO_CURRENCY", "DRY_RUN",
        "REQUIRE_PAYOUT_APPROVAL", "MAX_AMOUNT_PER_TX", "MAX_DAILY_TX_COUNT",
        "MAX_DAILY_TOTAL", "MSISDN_ALLOWLIST", "RATE_LIMIT_PER_SEC", "MOMO_DB_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in clear or []:
        monkeypatch.delenv(key, raising=False)


def test_valid_config_loads(monkeypatch):
    _apply(monkeypatch, _VALID)
    settings = load_settings(env_file=None)
    assert settings.target_env == "sandbox"
    assert settings.dry_run is True  # default-safe
    assert settings.require_payout_approval is True
    assert settings.provisioned is False  # no API user/key yet


def test_missing_subscription_key_fails_fast(monkeypatch):
    _apply(monkeypatch, _VALID, clear=["MOMO_COLLECTION_SUBSCRIPTION_KEY"])
    with pytest.raises(ConfigError, match="MOMO_COLLECTION_SUBSCRIPTION_KEY"):
        load_settings(env_file=None)


def test_non_sandbox_env_rejected(monkeypatch):
    _apply(monkeypatch, {**_VALID, "MOMO_TARGET_ENV": "production"})
    with pytest.raises(ConfigError, match="sandbox-only"):
        load_settings(env_file=None)


def test_non_sandbox_base_url_rejected(monkeypatch):
    _apply(monkeypatch, {**_VALID, "MOMO_BASE_URL": "https://api.momodeveloper.mtn.com"})
    with pytest.raises(ConfigError, match="sandbox"):
        load_settings(env_file=None)


def test_http_base_url_rejected(monkeypatch):
    _apply(monkeypatch, {**_VALID, "MOMO_BASE_URL": "http://sandbox.momodeveloper.mtn.com"})
    with pytest.raises(ConfigError, match="https"):
        load_settings(env_file=None)


def test_bad_currency_rejected(monkeypatch):
    _apply(monkeypatch, {**_VALID, "MOMO_CURRENCY": "USD"})
    with pytest.raises(ConfigError, match="MOMO_CURRENCY"):
        load_settings(env_file=None)


def test_bad_boolean_rejected(monkeypatch):
    _apply(monkeypatch, {**_VALID, "DRY_RUN": "maybe"})
    with pytest.raises(ConfigError, match="boolean"):
        load_settings(env_file=None)


def test_secrets_redacted_in_repr(monkeypatch):
    _apply(monkeypatch, _VALID)
    settings = load_settings(env_file=None)
    text = repr(settings)
    # The actual key value must never appear.
    assert "a" * 32 not in text
    assert "b" * 32 not in text
    assert "<set:" in text  # redaction marker present


def test_allowlist_parsed(monkeypatch):
    _apply(monkeypatch, {**_VALID, "MSISDN_ALLOWLIST": "111, 222 ,333"})
    settings = load_settings(env_file=None)
    assert settings.msisdn_allowlist == ("111", "222", "333")
