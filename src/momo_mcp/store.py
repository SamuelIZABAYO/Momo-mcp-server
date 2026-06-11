"""SQLite persistence: transaction ledger, idempotency keys, audit log.

Integrity guarantees this module is responsible for (spec §4.1, §4.2):

  * **Persisted-before-send idempotency** — :meth:`Store.create_transaction`
    writes a row in state ``PENDING`` with a unique ``reference_id`` *before*
    any HTTP call is made. A crash between write and send leaves a recoverable
    ``PENDING`` row; :meth:`Store.pending_transactions` lists them for
    startup reconciliation. The provider reuses the stored ``reference_id`` on
    retry, so MTN dedupes the request and no double charge occurs.

  * **Append-only audit** — :meth:`Store.record_audit` only ever inserts. Every
    tool call lands one row. No raw amounts or MSISDNs are stored in the audit
    table; the input is hashed and the MSISDN masked to last-4 (§4.2).

  * **Daily counters** — :meth:`Store.daily_usage` aggregates non-dry-run,
    non-rejected mutations for the current UTC day, backing the spend limits in
    §4.7. Counters "reset" implicitly by keying on UTC date.

The schema is intentionally small and readable so a skeptical engineer can audit
it (Hard Rule #6).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Terminal states never transition again (used by reconciliation + replay guards).
TERMINAL_STATES = frozenset({"SUCCESSFUL", "FAILED", "TIMEOUT", "REJECTED"})
ALL_STATES = TERMINAL_STATES | {"PENDING"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    reference_id   TEXT PRIMARY KEY,          -- uuid4; the MTN X-Reference-Id
    kind           TEXT NOT NULL,             -- 'collection' | 'disbursement'
    tool           TEXT NOT NULL,             -- originating tool name
    msisdn         TEXT NOT NULL,             -- payer/payee (stored for ledger queries)
    amount         REAL NOT NULL,
    currency       TEXT NOT NULL,
    status         TEXT NOT NULL,             -- PENDING/SUCCESSFUL/FAILED/TIMEOUT/REJECTED
    dry_run        INTEGER NOT NULL DEFAULT 0,
    external_ref   TEXT,                       -- caller-supplied externalId, if any
    note           TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_status  ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_tx_msisdn  ON transactions(msisdn);

-- Append-only audit log. No raw amounts/MSISDNs (§4.2): input is hashed,
-- amounts/MSISDNs are not stored here at all.
CREATE TABLE IF NOT EXISTS audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    tool         TEXT NOT NULL,
    input_hash   TEXT NOT NULL,
    reference_id TEXT,
    outcome      TEXT NOT NULL,               -- e.g. ok / rejected:<reason> / error
    latency_ms   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);

-- One-time payout approval codes (§4.3). Single-use; replay is rejected.
CREATE TABLE IF NOT EXISTS approvals (
    code         TEXT PRIMARY KEY,
    msisdn       TEXT NOT NULL,
    amount       REAL NOT NULL,
    currency     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT                          -- NULL until used; set once, never reused
);

-- Human-triggered daily-limit resets (§4.7). daily_usage only counts
-- transactions created at/after the most recent reset, so a human running
-- scripts/reset_limits.py clears a hard-stop without deleting ledger history.
CREATE TABLE IF NOT EXISTS limit_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    reset_at   TEXT NOT NULL,
    note       TEXT
);

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
"""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _utc_date() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass(frozen=True)
class Transaction:
    reference_id: str
    kind: str
    tool: str
    msisdn: str
    amount: float
    currency: str
    status: str
    dry_run: bool
    external_ref: str | None
    note: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Transaction:
        return cls(
            reference_id=row["reference_id"],
            kind=row["kind"],
            tool=row["tool"],
            msisdn=row["msisdn"],
            amount=row["amount"],
            currency=row["currency"],
            status=row["status"],
            dry_run=bool(row["dry_run"]),
            external_ref=row["external_ref"],
            note=row["note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class DailyUsage:
    date: str
    tx_count: int
    total_amount: float


class Store:
    """Thin, explicit wrapper over a SQLite connection. Not thread-shared:
    instantiate one per process; the server is single-process (spec §4.8)."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if self.db_path.parent and str(self.db_path.parent) not in ("", "."):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ── transactions / idempotency ───────────────────────────────────────────
    def create_transaction(
        self,
        *,
        reference_id: str,
        kind: str,
        tool: str,
        msisdn: str,
        amount: float,
        currency: str,
        dry_run: bool,
        external_ref: str | None = None,
        note: str | None = None,
    ) -> Transaction:
        """Insert a PENDING transaction BEFORE any HTTP call (§4.1).

        Raises :class:`sqlite3.IntegrityError` if ``reference_id`` already
        exists — that is the idempotency guarantee surfacing, and callers should
        treat it as "already recorded, do not resend".
        """
        if kind not in ("collection", "disbursement"):
            raise ValueError(f"kind must be collection|disbursement, got {kind!r}")
        now = _utcnow()
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO transactions
                   (reference_id, kind, tool, msisdn, amount, currency, status,
                    dry_run, external_ref, note, created_at, updated_at)
                   VALUES (?,?,?,?,?,?, 'PENDING', ?,?,?,?,?)""",
                (
                    reference_id, kind, tool, msisdn, amount, currency,
                    int(dry_run), external_ref, note, now, now,
                ),
            )
        return self.get_transaction(reference_id)  # type: ignore[return-value]

    def update_status(self, reference_id: str, status: str) -> None:
        if status not in ALL_STATES:
            raise ValueError(f"unknown status {status!r}; expected one of {sorted(ALL_STATES)}")
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE transactions SET status=?, updated_at=? WHERE reference_id=?",
                (status, _utcnow(), reference_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no transaction with reference_id={reference_id!r}")

    def get_transaction(self, reference_id: str) -> Transaction | None:
        row = self._conn.execute(
            "SELECT * FROM transactions WHERE reference_id=?", (reference_id,)
        ).fetchone()
        return Transaction.from_row(row) if row else None

    def pending_transactions(self) -> list[Transaction]:
        """All PENDING rows — the startup reconciliation worklist (§4.1)."""
        rows = self._conn.execute(
            "SELECT * FROM transactions WHERE status='PENDING' ORDER BY created_at"
        ).fetchall()
        return [Transaction.from_row(r) for r in rows]

    def list_transactions(
        self,
        *,
        status: str | None = None,
        msisdn: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[Transaction]:
        """Ledger query — local store only, never the API (spec §2)."""
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if msisdn:
            clauses.append("msisdn=?")
            params.append(msisdn)
        if since:
            clauses.append("created_at>=?")
            params.append(since)
        if until:
            clauses.append("created_at<=?")
            params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM transactions{where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [Transaction.from_row(r) for r in rows]

    # ── daily usage + limit resets (spend limits, §4.7) ──────────────────────
    def latest_reset_today(self, day: str | None = None) -> str | None:
        """The most recent limit-reset timestamp for the given UTC day, if any."""
        d = day or _utc_date()
        row = self._conn.execute(
            "SELECT MAX(reset_at) AS r FROM limit_resets WHERE substr(reset_at,1,10)=?",
            (d,),
        ).fetchone()
        return row["r"] if row and row["r"] else None

    def daily_usage(
        self, date: str | None = None, *, include_dry_run: bool = True
    ) -> DailyUsage:
        """Count + sum of non-rejected mutations for a UTC day.

        ``include_dry_run`` defaults to True because the spend LIMITS are about
        agent *behavior*, not settlement: a guardrail must stop an oversized or
        runaway spree even in DRY_RUN (the safe demo mode), so the safety
        scorecard is truthful. Set it False to report real money actually moved.

        Only counts transactions created at/after the most recent limit reset for
        that day, so a human running scripts/reset_limits.py clears a hard-stop
        without erasing ledger history (§4.7)."""
        day = date or _utc_date()
        floor = self.latest_reset_today(day) or ""
        dry_clause = "" if include_dry_run else "AND dry_run=0"
        row = self._conn.execute(
            f"""SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS total
               FROM transactions
               WHERE status != 'REJECTED'
                 {dry_clause}
                 AND substr(created_at,1,10)=?
                 AND created_at >= ?""",
            (day, floor),
        ).fetchone()
        return DailyUsage(date=day, tx_count=row["n"], total_amount=row["total"])

    def reset_limits(self, note: str | None = None) -> str:
        """Record a limit reset now; subsequent daily_usage counts from here."""
        ts = _utcnow()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO limit_resets (reset_at, note) VALUES (?,?)", (ts, note)
            )
        return ts

    # ── audit (append-only, §4.2) ────────────────────────────────────────────
    def record_audit(
        self,
        *,
        tool: str,
        input_hash: str,
        outcome: str,
        reference_id: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO audit (ts, tool, input_hash, reference_id, outcome, latency_ms)
                   VALUES (?,?,?,?,?,?)""",
                (_utcnow(), tool, input_hash, reference_id, outcome, latency_ms),
            )

    def recent_audit(self, limit: int = 100) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # ── approvals (one-time payout codes, §4.3) ──────────────────────────────
    def create_approval(
        self,
        *,
        code: str,
        msisdn: str,
        amount: float,
        currency: str,
        expires_at: str,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO approvals (code, msisdn, amount, currency, created_at, expires_at)
                   VALUES (?,?,?,?,?,?)""",
                (code, msisdn, amount, currency, _utcnow(), expires_at),
            )

    def consume_approval(self, code: str) -> sqlite3.Row | None:
        """Atomically mark an approval consumed. Returns the row on success.

        Returns ``None`` if the code is unknown, already consumed (replay), or
        expired — the caller maps that to a rejection (§7.1). The single UPDATE
        with ``consumed_at IS NULL`` in the WHERE clause makes replay a no-op
        even under concurrency.
        """
        now = _utcnow()
        with self._tx() as conn:
            cur = conn.execute(
                """UPDATE approvals SET consumed_at=?
                   WHERE code=? AND consumed_at IS NULL AND expires_at > ?""",
                (now, code, now),
            )
            if cur.rowcount == 0:
                return None
            return conn.execute(
                "SELECT * FROM approvals WHERE code=?", (code,)
            ).fetchone()
