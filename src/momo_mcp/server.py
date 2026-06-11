"""MCP server entry point.

Phase 1 provides only a runnable ``main()`` that validates configuration and
reports readiness — full FastMCP tool registration lands in Phase 4 (spec §5).
Keeping the entry point real from day one means the ``momo-mcp-server`` console
script and the container ``CMD`` are wired and testable before the tools exist.
"""

from __future__ import annotations

import sys

from .config import ConfigError, load_settings
from .logging_conf import configure_logging


def main() -> int:
    log = configure_logging()
    try:
        settings = load_settings()
    except ConfigError as exc:
        # stderr so MCP stdio (stdout) stays clean.
        print(f"Configuration error:\n  {exc}", file=sys.stderr)
        return 2

    log.info(
        "config loaded",
        extra={
            "target_env": settings.target_env,
            "dry_run": settings.dry_run,
            "provisioned": settings.provisioned,
            "currency": settings.currency,
        },
    )
    # Phase 4 will start the FastMCP stdio server here.
    print(
        "momo-mcp-server: configuration valid. "
        f"(dry_run={settings.dry_run}, provisioned={settings.provisioned}). "
        "Tool serving arrives in Phase 4.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
