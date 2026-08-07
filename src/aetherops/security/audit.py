"""Hash-chained, append-only audit ledger (docs/05-security.md §8).

Every orchestrator transition, tool call, model call, policy decision, and
approval lands here. Each record's hash covers the previous record's hash, so
any tampering breaks verification from that point forward. Production ships
the chain to WORM object storage; the reference implementation keeps it
in-memory with optional JSONL persistence.
"""
from __future__ import annotations

import hashlib
import json
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

    def append(self, *, actor: str, action: str, payload: dict | None = None) -> AuditRecord:
        payload = payload or {}
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
        prev = GENESIS
        for record in self._records:
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
