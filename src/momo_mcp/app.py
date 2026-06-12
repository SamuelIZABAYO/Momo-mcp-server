"""Application context: wires settings → store → provider, and reconciles
pending transactions on startup.

Both the MCP server (server.py) and the test suite build an ``AppContext`` so
the tool layer is identical in production and under test.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, load_settings
from .logging_conf import configure_logging, get_logger
from .providers.base import PaymentProvider
from .providers.mtn import MTNProvider
from .store import Store

log = get_logger("app")


@dataclass
class AppContext:
    settings: Settings
    store: Store
    provider: PaymentProvider

    async def aclose(self) -> None:
        await self.provider.aclose()
        self.store.close()


def build_app(settings: Settings | None = None) -> AppContext:
    """Construct the app context. Loads settings if not supplied."""
    configure_logging()
    settings = settings or load_settings()
    store = Store(settings.db_path)
    provider = MTNProvider(settings=settings, store=store)
    ctx = AppContext(settings=settings, store=store, provider=provider)
    _log_startup(ctx)
    return ctx


def _log_startup(ctx: AppContext) -> None:
    pending = ctx.store.pending_transactions()
    log.info(
        "app started",
        extra={
            "dry_run": ctx.settings.dry_run,
            "provisioned": ctx.settings.provisioned,
            "require_payout_approval": ctx.settings.require_payout_approval,
            "pending_to_reconcile": len(pending),
        },
    )
    if pending:
        # We do not auto-resolve on startup (that would make network calls before
        # the client is ready); we surface the worklist. check_payment_status
        # reconciles each on demand, reusing the stored reference_id.
        log.info(
            "pending transactions awaiting reconciliation",
            extra={"reference_ids": [t.reference_id for t in pending]},
        )
