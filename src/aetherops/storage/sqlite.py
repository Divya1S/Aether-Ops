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

    def update(self, episode_id: str, fields: dict) -> bool:
        with self._db_lock:
            if not super().update(episode_id, fields):
                return False
            body = next(e for e in self._episodes
                        if e.get("id") == episode_id)
            self._conn.execute(
                "UPDATE episodes SET body = ? WHERE id = ?",
                (json.dumps(body, default=str), episode_id))
            self._conn.commit()
            return True

    def close(self) -> None:
        with self._db_lock:
            self._conn.close()


class SqliteIncidentArchive:
    """Durable incident history (docs/12 §5.1 `incidents`, reduced to one
    JSON snapshot per incident). The API writes an incident's rendered view
    here on every state change, so the list and each detail page survive a
    restart alongside organizational memory and the audit chains.

    What is archived is the *record*, not the live workflow: a run paused at
    its approval gate holds in-process state that does not survive the
    process, so an archived PAUSED incident is readable but not resumable
    (production resumes from Temporal history — docs/03 §7)."""

    def __init__(self, db_path: str):
        self._db_lock = threading.Lock()
        self._conn = _connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS incidents ("
            "id TEXT PRIMARY KEY, created_at REAL NOT NULL, "
            "body TEXT NOT NULL)")
        self._conn.commit()

    def save(self, incident_id: str, created_at: float, view: dict) -> None:
        with self._db_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO incidents (id, created_at, body) "
                "VALUES (?, ?, ?)",
                (incident_id, created_at, json.dumps(view, default=str)))
            self._conn.commit()

    def get(self, incident_id: str) -> dict | None:
        with self._db_lock:
            row = self._conn.execute(
                "SELECT body FROM incidents WHERE id = ?",
                (incident_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, limit: int = 200) -> list[dict]:
        with self._db_lock:
            rows = self._conn.execute(
                "SELECT body FROM incidents ORDER BY created_at DESC "
                "LIMIT ?", (limit,)).fetchall()
        return [json.loads(body) for (body,) in rows]

    def __len__(self) -> int:
        with self._db_lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM incidents").fetchone()[0]

    def close(self) -> None:
        with self._db_lock:
            self._conn.close()


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

    def close(self) -> None:
        with self._db_lock:
            self._conn.close()
