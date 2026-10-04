"""SQLite access: WAL mode, short-lived connections, append-only events table."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    task           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    config_hash    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    -- Lease: set when a worker claims the run, renewed while it works. An expired lease on a
    -- running/delivering run means the worker died (see Tracer.recover_orphans).
    claimed_by       TEXT,
    lease_expires_at TEXT
);

CREATE TABLE IF NOT EXISTS spans (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(id),
    parent_id     TEXT,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'running',
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    duration_ms   REAL,
    input         TEXT,
    output        TEXT,
    error         TEXT,
    traceback     TEXT,
    callsite_file TEXT,
    callsite_line INTEGER,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    cost_usd      REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_spans_run ON spans(run_id);

-- Append-only log. The autoincrement id doubles as the SSE Last-Event-ID.
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL,
    span_id TEXT,
    type    TEXT NOT NULL,
    ts      TEXT NOT NULL,
    data    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);

-- Eval harness: one row per eval invocation, one row per (case, trial).
-- Each trial links to a normal run (run_id), so its full trace is inspectable.
CREATE TABLE IF NOT EXISTS eval_runs (
    id            TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    suite         TEXT NOT NULL,
    suite_hash    TEXT,
    config_hash   TEXT,
    model         TEXT,
    trials        INTEGER NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    truncated     INTEGER NOT NULL DEFAULT 0,
    summary       TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_label ON eval_runs(label);

CREATE TABLE IF NOT EXISTS eval_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_run_id TEXT NOT NULL REFERENCES eval_runs(id),
    case_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    trial       INTEGER NOT NULL,
    run_id      TEXT,
    passed      INTEGER NOT NULL,
    grades      TEXT NOT NULL,
    metrics     TEXT NOT NULL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(eval_run_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
"""


def connect(path: str | Path) -> sqlite3.Connection:
    p = str(path)
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: autocommit, transactions are managed explicitly.
    conn = sqlite3.connect(p, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the first release: (table, column, definition). CREATE TABLE IF NOT EXISTS
# does not alter an existing table, so databases created earlier get them via ALTER TABLE.
# (At the Postgres cutover this list becomes real numbered migrations.)
_ADDED_COLUMNS = [
    ("runs", "claimed_by", "TEXT"),
    ("runs", "lease_expires_at", "TEXT"),
    ("spans", "cache_read_tokens", "INTEGER"),
    ("spans", "cache_write_tokens", "INTEGER"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(path: str | Path) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
    finally:
        conn.close()


@contextmanager
def write_tx(path: str | Path) -> Iterator[sqlite3.Connection]:
    """One writer transaction. BEGIN IMMEDIATE takes the write lock up front."""
    conn = connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
