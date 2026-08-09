"""Evaluation harness: golden scenarios, metrics, trust ladder, release gate."""
import unittest

from aetherops.core.types import WorkflowStatus
from aetherops.evals.harness import RCA_PRECISION_GATE, run_all, run_scenario
from aetherops.evals.scenarios import (build_environment, payments_regression,
                                       uncorrelated_latency)
from aetherops.workflows.incident_remediation import run_incident_remediation


class TestEscalationScenario(unittest.TestCase):
    def test_uncorrelated_incident_escalates_instead_of_guessing(self):
        incident, env = build_environment(uncorrelated_latency())
        run, ctx = run_incident_remediation(incident, **env)
        self.assertEqual(run.status, WorkflowStatus.FAILED)
        self.assertIn("escalate", run.error.lower())
        rca = ctx.results["root_cause"]
        self.assertEqual(rca.output["status"], "insufficient-evidence")
        self.assertLess(rca.confidence, 0.5)     # knows what it doesn't know
        self.assertNotIn("execute", run.checkpoint)


class TestGeneralization(unittest.TestCase):
    def test_second_regression_scenario_diagnoses_its_own_commit(self):
        row = run_scenario(payments_regression())
        self.assertEqual(row["outcome"], "remediated")
        self.assertEqual(row["predicted_commit"], "f3d92ab")
        self.assertEqual(row["predicted_class"], "deploy-regression/memory")
        self.assertTrue(row["steps_correct"])
        self.assertTrue(row["audit_verified"])


class TestHarnessReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_all()

    def test_aggregate_metrics(self):
        aggregates = self.report["aggregates"]
        self.assertEqual(aggregates["scenarios"], 4)
        self.assertEqual(aggregates["rca_precision_at_1"], 1.0)
        self.assertEqual(aggregates["escalation_correctness"], 1.0)
        self.assertEqual(aggregates["plan_step_accuracy"], 1.0)
        self.assertEqual(aggregates["verification_pass_rate"], 1.0)
        self.assertEqual(aggregates["citation_faithfulness"], 1.0)
        self.assertTrue(aggregates["all_audit_chains_verified"])
        self.assertLess(aggregates["mean_calibration_error"], 0.35)

    def test_trust_ladder_verdict(self):
        ladder = self.report["trust_ladder"]
        verdict = ladder["deploy-regression/memory"]
        self.assertEqual(verdict["episodes"], 2)
        self.assertEqual(verdict["precision"], 1.0)
        self.assertTrue(verdict["stage"].startswith("gated-writes"))
        # A correct-but-single-episode class stays advisory — the ladder
        # demands sample size, not just precision.
        self.assertEqual(ladder["cert-expiry/tls"]["episodes"], 1)
        self.assertEqual(ladder["cert-expiry/tls"]["stage"], "advisory-only")

    def test_cert_expiry_class_end_to_end(self):
        row = next(r for r in self.report["rows"]
                   if r["scenario"] == "s4-cert-expiry")
        self.assertEqual(row["outcome"], "remediated")
        self.assertEqual(row["predicted_class"], "cert-expiry/tls")
        self.assertIsNone(row["predicted_commit"])   # nothing to revert
        self.assertEqual(row["steps"], ["rotate_certificate"])
        self.assertTrue(row["audit_verified"])

    def test_release_gate_passes_phase1_criterion(self):
        self.assertGreaterEqual(RCA_PRECISION_GATE, 0.6)
        self.assertTrue(self.report["release_gate"]["passed"])

    def test_escalation_scores_as_good_calibration(self):
        row = next(r for r in self.report["rows"]
                   if r["scenario"] == "s2-search-uncorrelated")
        self.assertEqual(row["outcome"], "escalated")
        self.assertTrue(row["outcome_correct"])
        self.assertLess(row["calibration_error"], 0.2)


if __name__ == "__main__":
    unittest.main()
