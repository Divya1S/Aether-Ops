"""Redaction patterns and audit hash-chain integrity."""
import unittest

from aetherops.security.audit import AuditLog
from aetherops.security.redaction import redact_text, redact_value


class TestRedaction(unittest.TestCase):
    def test_common_secret_shapes_are_redacted(self):
        cases = {
            "contact j.doe@example.com": "email",
            "key AKIAIOSFODNN7EXAMPLE ok": "aws-access-key",
            "Authorization: Bearer abcdef1234567890abcdef": "bearer-token",
            "xoxb-1234567890-abcdefghij": "slack-token",
            "password=hunter2s3cret": "credential-kv",
            "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----": "private-key",
        }
        for text, label in cases.items():
            clean, findings = redact_text(text)
            self.assertIn(label, findings, text)
            self.assertIn(f"[REDACTED:{label}]", clean)

    def test_clean_text_untouched(self):
        clean, findings = redact_text("p99 latency rose to 2400ms at 14:05")
        self.assertEqual(findings, [])
        self.assertEqual(clean, "p99 latency rose to 2400ms at 14:05")

    def test_nested_structures_are_redacted(self):
        value = {"series": [{"note": "api_key=abc123secret"}],
                 "owner": "j.doe@example.com"}
        clean, findings = redact_value(value)
        self.assertIn("credential-kv", findings)
        self.assertIn("email", findings)
        self.assertNotIn("abc123secret", str(clean))
        self.assertNotIn("j.doe@example.com", str(clean))


class TestAuditChain(unittest.TestCase):
    def test_chain_verifies_and_links(self):
        log = AuditLog()
        first = log.append(actor="test", action="one", payload={"k": 1})
        second = log.append(actor="test", action="two", payload={"k": 2})
        self.assertEqual(second.prev_hash, first.hash)
        self.assertTrue(log.verify())

    def test_tampering_breaks_verification(self):
        log = AuditLog()
        log.append(actor="test", action="one", payload={"amount": 10})
        log.append(actor="test", action="two", payload={"amount": 20})
        object.__setattr__(log._records[0], "payload", {"amount": 999})
        self.assertFalse(log.verify())


if __name__ == "__main__":
    unittest.main()
