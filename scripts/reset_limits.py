#!/usr/bin/env python3
"""Human-triggered daily spend-limit reset (spec §4.7).

When a daily limit (count or total) is breached, mutating tools hard-stop until a
human runs this script. It records a reset marker; daily_usage then counts only
transactions created after the reset, so the hard-stop clears WITHOUT deleting any
ledger history (the audit trail and reconciliation stay intact).

Usage:
    python scripts/reset_limits.py ["optional note explaining why"]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from momo_mcp.config import ConfigError, load_settings  # noqa: E402
from momo_mcp.store import Store  # noqa: E402


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error:\n  {exc}", file=sys.stderr)
        return 2

    note = " ".join(sys.argv[1:]) or None
    store = Store(settings.db_path)
    try:
        before = store.daily_usage()
        ts = store.reset_limits(note=note)
        after = store.daily_usage()
    finally:
        store.close()

    print(
        f"Daily limits reset at {ts}.\n"
        f"  before: {before.tx_count} tx, total {before.total_amount}\n"
        f"  after:  {after.tx_count} tx, total {after.total_amount}\n"
        + (f"  note:   {note}\n" if note else "")
        + "Mutating tools may proceed again (history preserved)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
