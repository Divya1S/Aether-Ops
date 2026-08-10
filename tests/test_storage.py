"""SQLite persistence: memory and audit ledger survive process restart;
the hash chain still detects tampering after a round-trip (docs/17 #15)."""
import os
import sqlite3
import tempfile
import threading
import unittest

from aetherops.security.audit import AuditLog
from aetherops.storage.sqlite import SqliteAuditLog, SqliteEpisodicMemory


def _hammer(fn, threads=16, per_thread=8):
    def work():
        for i in range(per_thread):
            fn(i)
    workers = [threading.Thread(target=work) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return threads * per_thread


class TestConcurrency(unittest.TestCase):
    def test_in_memory_audit_append_is_atomic(self):
        # audit F13: without a lock, concurrent appends fork the chain and
        # verify() would fail on a log nobody tampered with.
        log = AuditLog()
        total = _hammer(lambda i: log.append(actor="t", action="a",
                                             payload={"i": i}))
        self.assertEqual(len(log), total)
        self.assertEqual(len({r.seq for r in log.records}), total)   # no dups
        self.assertTrue(log.verify())

    def test_sqlite_audit_is_thread_safe_and_durable(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "audit.db")
            log = SqliteAuditLog(db)
            total = _hammer(lambda i: log.append(actor="t", action="a",
                                                 payload={"i": i}))
            self.assertEqual(len(log), total)
            self.assertTrue(log.verify())
            reopened = SqliteAuditLog(db)          # survives restart
            self.assertEqual(len(reopened), total)
            self.assertTrue(reopened.verify())

    def test_sqlite_memory_is_thread_safe(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "mem.db")
            memory = SqliteEpisodicMemory(db)
            total = _hammer(lambda i: memory.add(
                {"service": "s", "failure_class": "x", "summary": str(i)}))
            self.assertEqual(len(memory), total)
            self.assertEqual(len(SqliteEpisodicMemory(db)), total)


class TestSqlitePersistence(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "aetherops.db")

    def tearDown(self):
        self.dir.cleanup()

    def test_memory_survives_restart(self):
        memory = SqliteEpisodicMemory(self.db)
        memory.add({"service": "checkout-service",
                    "failure_class": "deploy-regression/memory",
                    "summary": "pool increase caused OOMKilled cascade"})
        self.assertEqual(len(memory), 1)

        reopened = SqliteEpisodicMemory(self.db)     # simulated restart
        self.assertEqual(len(reopened), 1)
        hits = reopened.search("OOMKilled pool")
        self.assertEqual(hits[0]["service"], "checkout-service")

    def test_audit_chain_survives_restart_and_verifies(self):
        audit = SqliteAuditLog(self.db)
        audit.append(actor="t", action="one", payload={"n": 1})
        audit.append(actor="t", action="two", payload={"n": 2})

        reopened = SqliteAuditLog(self.db)
        self.assertEqual(len(reopened), 2)
        self.assertTrue(reopened.verify())

        reopened.append(actor="t", action="three", payload={"n": 3})
        self.assertTrue(SqliteAuditLog(self.db).verify())   # chain continues

    def test_database_tampering_breaks_verification(self):
        audit = SqliteAuditLog(self.db)
        audit.append(actor="t", action="one", payload={"amount": 10})
        audit.append(actor="t", action="two", payload={"amount": 20})

        conn = sqlite3.connect(self.db)              # attacker edits the DB
        conn.execute("UPDATE audit SET payload = ? WHERE seq = 0",
                     ('{"amount": 999}',))
        conn.commit()
        conn.close()

        self.assertFalse(SqliteAuditLog(self.db).verify())


if __name__ == "__main__":
    unittest.main()
