#!/usr/bin/env python3
"""Accountant-friendly ledger export (spec §7.4).

Exports the transaction ledger to CSV (opens directly in Excel/Sheets) with a
human-readable reconciliation status column. This is the small thing that turns
"developer tool" into "thing a business's accountant signs off on".

Usage:
    python scripts/export_ledger.py                 # -> ledger_export.csv
    python scripts/export_ledger.py out.csv         # custom path
    python scripts/export_ledger.py --status SUCCESSFUL
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from momo_mcp.config import ConfigError, load_settings  # noqa: E402
from momo_mcp.store import TERMINAL_STATES, Store  # noqa: E402

# Reconciliation status for an accountant: is this row settled, still open, or
# did it fail? Derived from the canonical transaction status.
_RECON = {
    "SUCCESSFUL": "RECONCILED (settled)",
    "FAILED": "CLOSED (failed, no settlement)",
    "REJECTED": "CLOSED (rejected by payer)",
    "TIMEOUT": "CLOSED (expired, no settlement)",
    "PENDING": "OPEN (awaiting settlement)",
}

_COLUMNS = [
    "reference_id", "kind", "tool", "msisdn", "amount", "currency",
    "status", "reconciliation", "dry_run", "external_ref", "note",
    "created_at", "updated_at",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the MoMo ledger to CSV.")
    parser.add_argument("output", nargs="?", default="ledger_export.csv")
    parser.add_argument("--status", choices=sorted(TERMINAL_STATES | {"PENDING"}))
    parser.add_argument("--limit", type=int, default=100000)
    args = parser.parse_args()

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error:\n  {exc}", file=sys.stderr)
        return 2

    store = Store(settings.db_path)
    try:
        rows = store.list_transactions(status=args.status, limit=args.limit)
    finally:
        store.close()

    out = Path(args.output)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
        writer.writeheader()
        for tx in rows:
            writer.writerow(
                {
                    "reference_id": tx.reference_id,
                    "kind": tx.kind,
                    "tool": tx.tool,
                    "msisdn": tx.msisdn,
                    "amount": tx.amount,
                    "currency": tx.currency,
                    "status": tx.status,
                    "reconciliation": _RECON.get(tx.status, "UNKNOWN"),
                    "dry_run": "yes" if tx.dry_run else "no",
                    "external_ref": tx.external_ref or "",
                    "note": tx.note or "",
                    "created_at": tx.created_at,
                    "updated_at": tx.updated_at,
                }
            )

    print(f"Exported {len(rows)} transaction(s) to {out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
