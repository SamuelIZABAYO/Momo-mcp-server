"""Environment loading and validation — fail fast, with clear, actionable errors.

Design rules (spec §4.4, Hard Rules #2 & #3):
  * Secrets come only from the environment / .env, never from code.
  * The process refuses to start if configuration is incoherent, rather than
    failing deep inside an API call with a cryptic message.
  * ``Settings`` carries the secret values but its ``repr``/``str`` redact them,
    so a stray ``log.info(settings)`` can never leak a key (§4.4).
  * Sandbox only — a non-sandbox target environment is rejected here so no
    production code path can ever run from this repo (Hard Rule #3).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

# Fields that hold secrets — never rendered in repr/str, never logged.
_SECRET_FIELDS = frozenset(
    {
        "collection_subscription_key",
        "disbursement_subscription_key",
        "remittance_subscription_key",
        "api_user",
        "api_key",
    }
)

# Currencies we accept. Sandbox is EUR-only; RWF is reserved for go-live (§3.6).
_ALLOWED_CURRENCIES = frozenset({"EUR", "RWF"})


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid. Message is user-facing."""


def _redact(value: str | None) -> str:
    """Render a secret as a length hint only — never the value itself."""
    if not value:
        return "<unset>"
    return f"<set:{len(value)} chars>"


# Env vars whose values must NOT have inline-comment stripping applied (a secret
# could, in principle, contain a '#'). Everything else is a simple scalar where a
# trailing " # comment" is never intended — and `docker run --env-file` does not
# strip those, so we do it defensively here.
_SECRET_ENV = frozenset(
    {
        "MOMO_COLLECTION_SUBSCRIPTION_KEY",
        "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY",
        "MOMO_REMITTANCE_SUBSCRIPTION_KEY",
        "MOMO_API_USER",
        "MOMO_API_KEY",
    }
)


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ' # comment' (whitespace before #). Leaves leading-# and
    mid-token # untouched."""
    idx = value.find(" #")
    return value[:idx] if idx != -1 else value


def _get(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name, default)
    if raw is None:
        return None
    if name not in _SECRET_ENV:
        raw = _strip_inline_comment(raw)
    raw = raw.strip()
    return raw or None


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name)
    if raw is None:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(
        f"{name} must be a boolean (true/false), got {raw!r}. "
        f"Set it to 'true' or 'false' in your .env."
    )


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc
    if value < 0:
        raise ConfigError(f"{name} must be >= 0, got {value}.")
    return value


@dataclass(frozen=True)
class Settings:
    """Validated, immutable configuration. Secrets redacted in repr/str."""

    # Subscription keys
    collection_subscription_key: str
    disbursement_subscription_key: str
    remittance_subscription_key: str | None

    # Provisioned API credentials (may be unset before scripts/provision.py runs)
    api_user: str | None
    api_key: str | None

    # Environment / endpoints
    target_env: str
    base_url: str
    callback_host: str
    currency: str

    # Guardrails (§4.7)
    dry_run: bool
    require_payout_approval: bool
    max_amount_per_tx: int
    max_daily_tx_count: int
    max_daily_total: int
    msisdn_allowlist: tuple[str, ...]

    # Store + rate limit
    db_path: Path
    rate_limit_per_sec: int

    # ── secret-safe rendering ────────────────────────────────────────────────
    def _safe_dict(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = _redact(value) if f.name in _SECRET_FIELDS else value
        return out

    def __repr__(self) -> str:  # pragma: no cover - trivial formatting
        inner = ", ".join(f"{k}={v!r}" for k, v in self._safe_dict().items())
        return f"Settings({inner})"

    __str__ = __repr__

    @property
    def provisioned(self) -> bool:
        """True once an API user/key pair is available (post-provisioning)."""
        return bool(self.api_user and self.api_key)


# Field where a guardrail default lives, so error messages can point at the env var.
_ENV_KEYS = {
    "collection_subscription_key": "MOMO_COLLECTION_SUBSCRIPTION_KEY",
    "disbursement_subscription_key": "MOMO_DISBURSEMENT_SUBSCRIPTION_KEY",
}


def load_settings(env_file: str | os.PathLike[str] | None = ".env") -> Settings:
    """Load and validate settings. Raises :class:`ConfigError` with a clear message.

    ``env_file`` is loaded if present; real environment variables always win,
    which is what CI and container ``--env-file`` injection rely on.
    """
    if env_file is not None and Path(env_file).is_file():
        load_dotenv(env_file, override=False)

    # ── required subscription keys ───────────────────────────────────────────
    missing = [
        env for field_name, env in _ENV_KEYS.items() if not _get(env)
    ]
    if missing:
        raise ConfigError(
            "Missing required subscription key(s): "
            + ", ".join(missing)
            + ".\nSubscribe to Collections and Disbursements at "
            "momodeveloper.mtn.com, then copy each product's primary key into "
            "your .env (see .env.example)."
        )

    # ── sandbox-only enforcement (Hard Rule #3) ──────────────────────────────
    target_env = (_get("MOMO_TARGET_ENV") or "sandbox").lower()
    if target_env != "sandbox":
        raise ConfigError(
            f"MOMO_TARGET_ENV={target_env!r} is not allowed. This repo is "
            "sandbox-only by policy (Hard Rule #3); production targets are out "
            "of scope. Set MOMO_TARGET_ENV=sandbox."
        )

    base_url = (_get("MOMO_BASE_URL") or "https://sandbox.momodeveloper.mtn.com").rstrip("/")
    if not base_url.startswith("https://"):
        raise ConfigError(f"MOMO_BASE_URL must be an https:// URL, got {base_url!r}.")
    if "sandbox" not in base_url:
        raise ConfigError(
            f"MOMO_BASE_URL={base_url!r} does not look like a sandbox host. "
            "Only the sandbox base URL is permitted in this repo (Hard Rule #3)."
        )

    currency = (_get("MOMO_CURRENCY") or "EUR").upper()
    if currency not in _ALLOWED_CURRENCIES:
        raise ConfigError(
            f"MOMO_CURRENCY={currency!r} is not supported. Allowed: "
            f"{sorted(_ALLOWED_CURRENCIES)}. Sandbox uses EUR (see GOTCHAS.md)."
        )

    # ── guardrails ───────────────────────────────────────────────────────────
    max_amount = _get_int("MAX_AMOUNT_PER_TX", 100)
    if max_amount <= 0:
        raise ConfigError("MAX_AMOUNT_PER_TX must be > 0.")
    max_daily_count = _get_int("MAX_DAILY_TX_COUNT", 50)
    max_daily_total = _get_int("MAX_DAILY_TOTAL", 1000)
    rate_limit = _get_int("RATE_LIMIT_PER_SEC", 5)
    if rate_limit <= 0:
        raise ConfigError("RATE_LIMIT_PER_SEC must be > 0.")

    raw_allowlist = _get("MSISDN_ALLOWLIST") or ""
    allowlist = tuple(
        part.strip() for part in raw_allowlist.split(",") if part.strip()
    )

    db_path = Path(_get("MOMO_DB_PATH") or "./data/momo.sqlite3")

    return Settings(
        collection_subscription_key=_get("MOMO_COLLECTION_SUBSCRIPTION_KEY"),  # type: ignore[arg-type]
        disbursement_subscription_key=_get("MOMO_DISBURSEMENT_SUBSCRIPTION_KEY"),  # type: ignore[arg-type]
        remittance_subscription_key=_get("MOMO_REMITTANCE_SUBSCRIPTION_KEY"),
        api_user=_get("MOMO_API_USER"),
        api_key=_get("MOMO_API_KEY"),
        target_env=target_env,
        base_url=base_url,
        callback_host=(_get("MOMO_CALLBACK_HOST") or "https://example.com"),
        currency=currency,
        dry_run=_get_bool("DRY_RUN", True),
        require_payout_approval=_get_bool("REQUIRE_PAYOUT_APPROVAL", True),
        max_amount_per_tx=max_amount,
        max_daily_tx_count=max_daily_count,
        max_daily_total=max_daily_total,
        msisdn_allowlist=allowlist,
        db_path=db_path,
        rate_limit_per_sec=rate_limit,
    )
