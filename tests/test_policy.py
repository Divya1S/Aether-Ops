"""Policy engine rules and plan admission."""
import unittest

from aetherops.core.types import RiskLevel
from aetherops.policy.engine import PolicyEngine


class TestPolicyRules(unittest.TestCase):
    def setUp(self):
        self.engine = PolicyEngine()

    def test_critical_in_prod_is_denied(self):
        decision = self.engine.evaluate(action="delete_namespace",
                                        risk=RiskLevel.CRITICAL,
                                        environment="prod", confidence=0.99)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.rule_id, "R1-critical-prod")

    def test_noncompensable_high_needs_tier3(self):
        decision = self.engine.evaluate(action="flush_cache",
                                        risk=RiskLevel.HIGH,
                                        environment="staging",
                                        confidence=0.95, compensable=False)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.approval_tier, 3)

    def test_high_risk_prod_needs_tier2(self):
        decision = self.engine.evaluate(action="rollback_deployment",
                                        risk=RiskLevel.HIGH,
                                        environment="prod", confidence=0.95)
        self.assertTrue(decision.requires_approval)
        self.assertEqual(decision.approval_tier, 2)

    def test_medium_risk_prod_needs_tier1(self):
        decision = self.engine.evaluate(action="create_revert_pr",
                                        risk=RiskLevel.MEDIUM,
                                        environment="prod", confidence=0.95)
        self.assertEqual(decision.approval_tier, 1)

    def test_medium_low_confidence_gated_even_outside_prod(self):
        decision = self.engine.evaluate(action="scale_deployment",
                                        risk=RiskLevel.MEDIUM,
                                        environment="staging", confidence=0.5)
        self.assertTrue(decision.requires_approval)

    def test_medium_high_confidence_auto_outside_prod(self):
        decision = self.engine.evaluate(action="scale_deployment",
                                        risk=RiskLevel.MEDIUM,
                                        environment="staging", confidence=0.9)
        self.assertFalse(decision.requires_approval)

    def test_reads_always_allowed(self):
        decision = self.engine.evaluate(action="query_metrics",
                                        risk=RiskLevel.READ,
                                        environment="prod", confidence=0.1)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)


class TestPlanAdmission(unittest.TestCase):
    def test_plan_tier_is_max_of_step_tiers(self):
        verdict = PolicyEngine().evaluate_plan(
            [{"action": "rollback_deployment", "risk": "HIGH"},
             {"action": "create_revert_pr", "risk": "MEDIUM"}],
            environment="prod", confidence=0.87)
        self.assertTrue(verdict["allowed"])
        self.assertTrue(verdict["requires_approval"])
        self.assertEqual(verdict["approval_tier"], 2)

    def test_one_denied_step_denies_the_plan(self):
        verdict = PolicyEngine().evaluate_plan(
            [{"action": "rollback_deployment", "risk": "HIGH"},
             {"action": "drop_database", "risk": "CRITICAL"}],
            environment="prod", confidence=0.99)
        self.assertFalse(verdict["allowed"])


if __name__ == "__main__":
    unittest.main()
