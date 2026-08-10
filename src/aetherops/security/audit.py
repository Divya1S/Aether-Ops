"""Hash-chained, append-only audit ledger (docs/05-security.md §8).

Every orchestrator transition, tool call, model call, policy decision, and
approval lands here. Each record's hash covers the previous record's hash, so
a naive in-place edit or reordering breaks verification from that point on
(proven in tests). Honest threat model: this detects accidental corruption
and casual tampering; it is NOT proof against an adversary with write access
to the log itself, who could re-chain every following record, and chain-only
verify() cannot detect trailing truncation. Production closes both gaps by
HMAC-signing records with an out-of-band key and anchoring the tip (hash +
count) in WORM storage. The reference implementation keeps the chain
in-memory with optional JSONL persistence and makes it reachable for
verification via GET /v1/incidents/{id}/audit.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass


GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: float
    actor: str
    action: str
    payload: dict
    prev_hash: str
    hash: str


def _body(seq: int, ts: float, actor: str, action: str, payload: dict) -> str:
    return json.dumps(
        {"seq": seq, "ts": ts, "actor": actor, "action": action, "payload": payload},
        sort_keys=True, default=str,
    )


class AuditLog:
    def __init__(self, path: str | None = None):
        self._records: list[AuditRecord] = []
        self._path = path
        self._lock = threading.Lock()

    def attach_path(self, path: str) -> None:
        """Turn on JSONL persistence after construction (the API builds the
        env, learns the incident id, then attaches a per-incident path).
        Flushes any already-recorded records so the file is complete."""
        with self._lock:
            self._path = path
            with open(path, "a", encoding="utf-8") as fh:
                for record in self._records:
                    fh.write(json.dumps(record.__dict__, default=str) + "\n")

    @classmethod
    def load(cls, path: str) -> "AuditLog":
        """Reload a chain from its JSONL file (read-only: path stays None so a
        loaded log never re-appends). verify() then re-checks the reloaded
        chain — governance survives a restart (Phase M)."""
        log = cls(path=None)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                data = json.loads(line)
                log._records.append(AuditRecord(
                    seq=data["seq"], ts=data["ts"], actor=data["actor"],
                    action=data["action"], payload=data["payload"],
                    prev_hash=data["prev_hash"], hash=data["hash"]))
        return log

    def append(self, *, actor: str, action: str, payload: dict | None = None) -> AuditRecord:
        payload = payload or {}
        # Atomic seq + prev-hash + append (audit F13): without the lock, two
        # threads appending to a shared log read the same seq and prev-hash
        # and FORK the chain — silently voiding tamper-evidence (verify()
        # would then return False on a log nobody tampered with).
        with self._lock:
            seq = len(self._records)
            ts = time.time()
            prev = self._records[-1].hash if self._records else GENESIS
            digest = hashlib.sha256(
                (prev + _body(seq, ts, actor, action, payload)).encode()
            ).hexdigest()
            record = AuditRecord(seq, ts, actor, action, payload, prev, digest)
            self._records.append(record)
            if self._path:
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record.__dict__, default=str) + "\n")
            return record

    @property
    def records(self) -> list[AuditRecord]:
        return list(self._records)

    def verify(self) -> bool:
        with self._lock:                        # snapshot; don't race appends
            records = list(self._records)
        prev = GENESIS
        for record in records:
            expected = hashlib.sha256(
                (prev + _body(record.seq, record.ts, record.actor,
                              record.action, record.payload)).encode()
            ).hexdigest()
            if record.prev_hash != prev or record.hash != expected:
                return False
            prev = record.hash
        return True

    def __len__(self) -> int:
        return len(self._records)
