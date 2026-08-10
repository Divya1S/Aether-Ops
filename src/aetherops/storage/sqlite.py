"""SQLite-backed episodic memory and audit ledger (docs/17 acceptance #15).

Same interfaces as the in-memory implementations — they subclass them and
add durability, so every consumer (agents, workflows, postmortems) is
untouched. In-memory remains the default: demos and evals stay byte-stable;
persistence is an explicit choice (production: Postgres per docs/12).

The audit ledger's hash chain survives the round-trip: records are stored
as JSON-native primitives, reloaded on open, and `verify()` recomputes the
chain — tampering with the database breaks verification exactly as
tampering with memory does.
"""
from __future__ import annotations

import json
import sqlite3
import threading

from aetherops.memory.store import EpisodicMemory
from aetherops.security.audit import AuditLog, AuditRecord


def _connect(db_path: str) -> sqlite3.Connection:
    """A connection safe to share across the API's worker threads (audit F12):
    check_same_thread=False lets any pool thread use it, WAL improves
    concurrency, and busy_timeout waits on a lock instead of raising
    'database is locked'. Callers still serialize writes with a lock, since a
    single sqlite3 connection is not safe for concurrent use."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class SqliteEpisodicMemory(EpisodicMemory):
    def __init__(self, db_path: str):
        super().__init__()
        self._db_lock = threading.Lock()
        self._conn = _connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS episodes ("
            "id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self._conn.commit()
        for (body,) in self._conn.execute(
                "SELECT body FROM episodes ORDER BY rowid"):
            self._episodes.append(json.loads(body))

    def add(self, episode: dict) -> str:
        with self._db_lock:
            episode_id = super().add(episode)
            self._conn.execute(
                "INSERT OR REPLACE INTO episodes (id, body) VALUES (?, ?)",
                (episode_id, json.dumps(self._episodes[-1], default=str)))
            self._conn.commit()
            return episode_id


class SqliteAuditLog(AuditLog):
    def __init__(self, db_path: str):
        super().__init__()
        self._db_lock = threading.Lock()
        self._conn = _connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS audit ("
            "seq INTEGER PRIMARY KEY, ts REAL, actor TEXT, action TEXT, "
            "payload TEXT, prev_hash TEXT, hash TEXT)")
        self._conn.commit()
        for row in self._conn.execute(
                "SELECT seq, ts, actor, action, payload, prev_hash, hash "
                "FROM audit ORDER BY seq"):
            self._records.append(AuditRecord(
                seq=row[0], ts=row[1], actor=row[2], action=row[3],
                payload=json.loads(row[4]), prev_hash=row[5], hash=row[6]))

    def append(self, *, actor: str, action: str,
               payload: dict | None = None) -> AuditRecord:
        # super().append() assigns seq/prev-hash atomically under the base
        # lock; the DB write is then serialized on _db_lock (one sqlite
        # connection is not safe for concurrent use). seq is the PRIMARY KEY,
        # so reload-by-seq reconstructs the chain regardless of insert order.
        record = super().append(actor=actor, action=action, payload=payload)
        with self._db_lock:
            self._conn.execute(
                "INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record.seq, record.ts, record.actor, record.action,
                 json.dumps(record.payload, default=str),
                 record.prev_hash, record.hash))
            self._conn.commit()
        return record
