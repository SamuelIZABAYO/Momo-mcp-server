"""Container HEALTHCHECK entry point.

Exits 0 when the server can construct its app context (config valid, store
reachable), "healthy"; exits 1 otherwise, "degraded". It does NOT
make a network call to MTN: a sandbox token fetch can fail for reasons unrelated
to container health (throttling, sandbox downtime), and we don't want a flaky
upstream to mark the container unhealthy and trigger a restart loop.

Run:  python -m momo_mcp.healthcheck
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from .config import load_settings
        from .store import Store

        settings = load_settings()
        store = Store(settings.db_path)
        # Touch the DB to confirm it is readable/writable.
        store.daily_usage()
        store.close()
    except Exception as exc:  # noqa: BLE001 - healthcheck reports any failure
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    print("healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
