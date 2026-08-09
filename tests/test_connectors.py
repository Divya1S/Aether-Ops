"""Connector gateway contract: declaration, caching, rate limits, redaction."""
import unittest

from aetherops.connectors.base import RateLimitExceeded, ToolSpec
from aetherops.connectors.fakes import FakeDatadog, FakeGitHub, FakeKubernetes
from aetherops.core.types import RiskLevel
from aetherops.security.audit import AuditLog


class TestGatewayContract(unittest.TestCase):
    def test_undeclared_tools_are_rejected(self):
        with self.assertRaises(ValueError):
            FakeDatadog().call("drop_all_monitors")

    def test_write_tools_cannot_be_declared_cacheable(self):
        with self.assertRaises(ValueError):
            ToolSpec("mutate", "bad", risk=RiskLevel.HIGH, cacheable=True)

    def test_reads_are_cached_writes_are_not(self):
        github = FakeGitHub()
        first = github.call("list_recent_deploys", {"service": "checkout-service"})
        second = github.call("list_recent_deploys", {"service": "checkout-service"})
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)

        pr1 = github.call("create_revert_pr", {"sha": "c9a1f42"}, principal="executor")
        pr2 = github.call("create_revert_pr", {"sha": "c9a1f42"}, principal="executor")
        self.assertFalse(pr1.cached)
        self.assertFalse(pr2.cached)

    def test_rate_limit_raises_transient(self):
        clock = [0.0]
        k8s = FakeKubernetes(clock=lambda: clock[0])
        for _ in range(10):     # rollback_deployment: 10/min
            k8s.call("rollback_deployment", {"service": "s", "revision": "r"}, principal="executor")
        with self.assertRaises(RateLimitExceeded):
            k8s.call("rollback_deployment", {"service": "s", "revision": "r"}, principal="executor")
        clock[0] += 61.0        # window slides -> calls permitted again
        k8s.call("rollback_deployment", {"service": "s", "revision": "r"}, principal="executor")

    def test_planted_secret_is_redacted_before_entering_workflow_state(self):
        result = FakeDatadog().call("query_metrics", {"window": "incident"})
        self.assertNotIn("dd-abc123def456", str(result.data))
        self.assertIn("[REDACTED:credential-kv]", result.data["monitor_message"])

    def test_author_email_is_redacted_from_commit_data(self):
        result = FakeGitHub().call("get_commit_diff", {"sha": "c9a1f42"})
        self.assertNotIn("j.doe@example.com", str(result.data))

    def test_every_call_is_audited(self):
        audit = AuditLog()
        FakeDatadog(audit=audit).call("query_metrics", {"window": "incident"})
        actions = [record.action for record in audit.records]
        self.assertIn("tool.call", actions)
        self.assertTrue(audit.verify())


if __name__ == "__main__":
    unittest.main()
