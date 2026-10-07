"""SQLite helpers. Every function opens its own short-lived connection."""
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    ip              TEXT    NOT NULL,
    device_type     TEXT    NOT NULL DEFAULT 'server',
    snmp_enabled    INTEGER NOT NULL DEFAULT 0,
    snmp_port       INTEGER NOT NULL DEFAULT 161,
    community       TEXT    NOT NULL DEFAULT 'public',
    if_index        INTEGER,
    if_name         TEXT,
    status          TEXT    NOT NULL DEFAULT 'UNKNOWN',
    down_reason     TEXT,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    latency_ms      REAL,
    packet_loss     REAL,
    cpu_percent     REAL,
    ram_percent     REAL,
    in_bps          REAL,
    out_bps         REAL,
    if_status       TEXT,
    last_in_octets  INTEGER,
    last_out_octets INTEGER,
    last_counter_ts REAL,
    last_cpu_idle   INTEGER,
    last_cpu_total  INTEGER,
    last_checked    REAL,
    created_at      REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts          REAL    NOT NULL,
    status      TEXT,
    latency_ms  REAL,
    packet_loss REAL,
    cpu_percent REAL,
    ram_percent REAL,
    in_bps      REAL,
    out_bps     REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics_device_ts ON metrics(device_id, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER,
    device_name TEXT,
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

-- remembers which alert conditions are currently active (so we alert once, not every poll)
CREATE TABLE IF NOT EXISTS alert_state (
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    kind      TEXT    NOT NULL,
    active    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, kind)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    """Usage:  with db() as conn: conn.execute(...)   (auto commit / rollback)"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
