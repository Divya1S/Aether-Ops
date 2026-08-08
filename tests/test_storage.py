"""SQLite persistence: memory and audit ledger survive process restart;
the hash chain still detects tampering after a round-trip (docs/17 #15)."""
import os
import sqlite3
import tempfile
import unittest

from aetherops.storage.sqlite import SqliteAuditLog, SqliteEpisodicMemory


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
