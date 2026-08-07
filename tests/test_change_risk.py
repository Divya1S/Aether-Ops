"""Change Intelligence workflow: scoring, gating, freeze-block, and the
incident->episode->higher-risk learning flywheel."""
import unittest

from aetherops.core.types import ChangeEvent, WorkflowStatus, new_id
from aetherops.evals.scenarios import build_environment, canonical
from aetherops.gateway.model_gateway import ModelGateway
from aetherops.graph.service_graph import default_graph
from aetherops.memory.store import EpisodicMemory
from aetherops.policy.engine import PolicyEngine
from aetherops.security.audit import AuditLog
from aetherops.workflows.change_risk import run_change_risk, score_change
from aetherops.workflows.incident_remediation import run_incident_remediation


def _env(memory=None):
    audit = AuditLog()
    return {"gateway": ModelGateway(audit=audit), "audit": audit,
            "memory": memory if memory is not None else EpisodicMemory(),
            "policy": PolicyEngine(), "graph": default_graph()}


def _memory_with_history():
    memory = EpisodicMemory()
    memory.add({"service": "payments-service",
                "failure_class": "deploy-regression/memory",
                "summary": "Connection pool increase caused OOMKilled cascade; "
                           "rollback restored p99"})
    memory.add({"service": "checkout-service",
                "failure_class": "deploy-regression/memory",
                "summary": "Deploy raised DB connection pool max_size; "
                           "OOMKilled breached p99; rollback verified"})
    return memory


def _pool_change(**extra_labels):
    return ChangeEvent(
        id=new_id("chg"), service="orders-service", sha="b7e21c9",
        title="Raise DB connection pool max_size 25 -> 250",
        diff="-  max_size: 25\n+  max_size: 250",
        labels={"peak_window": True, **extra_labels})


def _benign_change():
    return ChangeEvent(
        id=new_id("chg"), service="orders-service", sha="a11c3f0",
        title="Update README copy",
        diff="- old text\n+ new text",
        labels={"peak_window": True})


class TestScoring(unittest.TestCase):
    def test_score_components_and_bands(self):
        high = score_change(2, 2, True, 0)
        self.assertEqual(high["score"], 75)
        self.assertEqual(high["band"], "HIGH")
        self.assertEqual(high["components"]["failure_signature"], 40)

        medium = score_change(1, 2, True, 0)
        self.assertEqual((medium["score"], medium["band"]), (55, "MEDIUM"))

        low = score_change(0, 2, True, 0)
        self.assertEqual((low["score"], low["band"]), (35, "LOW"))

    def test_blast_radius_is_transitive(self):
        graph = default_graph()
        self.assertEqual(graph.dependents("orders-service"),
                         {"mobile-bff", "storefront-web"})
        # payments is reached transitively by everything above it
        self.assertEqual(graph.blast_radius("payments-service"), 4)


class TestChangeWorkflow(unittest.TestCase):
    def test_risky_change_scores_high_gates_and_records(self):
        env = _env(_memory_with_history())
        episodes_before = len(env["memory"])
        run, ctx = run_change_risk(_pool_change(), **env)
        self.assertEqual(run.status, WorkflowStatus.PAUSED)
        self.assertEqual(run.pending_gate, "approval_gate")
        self.assertEqual(run.checkpoint["score"]["band"], "HIGH")
        self.assertEqual(run.checkpoint["policy_check"]["approval_tier"], 2)
        self.assertTrue(run.checkpoint["policy_check"]["canary_required"])

        run, ctx = run_change_risk(_pool_change(), **env, ctx=ctx,
                                   approvals={"approval_gate": True},
                                   checkpoint=run.checkpoint)
        self.assertEqual(run.status, WorkflowStatus.SUCCEEDED)
        self.assertEqual(run.checkpoint["record"]["band"], "HIGH")
        self.assertEqual(len(env["memory"]), episodes_before + 1)
        self.assertTrue(env["audit"].verify())

    def test_benign_change_is_auto_allowed_without_gate(self):
        env = _env(_memory_with_history())
        run, ctx = run_change_risk(_benign_change(), **env)
        self.assertEqual(run.status, WorkflowStatus.SUCCEEDED)   # no pause
        self.assertEqual(run.checkpoint["score"]["band"], "LOW")
        self.assertFalse(run.checkpoint["policy_check"]["requires_approval"])
        self.assertFalse(run.checkpoint["record"]["canary_required"])

    def test_freeze_window_blocks_non_low_changes(self):
        env = _env(_memory_with_history())
        run, ctx = run_change_risk(_pool_change(freeze=True), **env)
        self.assertEqual(run.status, WorkflowStatus.FAILED)
        self.assertIn("freeze", run.error)
        self.assertIn("escalate", run.error)
        self.assertNotIn("record", run.checkpoint)

    def test_citations_present_on_change_intel(self):
        env = _env(_memory_with_history())
        _, ctx = run_change_risk(_pool_change(), **env)
        result = ctx.results["change_intel"]
        self.assertTrue(result.citations)
        kinds = {evidence.kind for evidence in ctx.evidence}
        self.assertEqual(kinds, {"change", "topology", "episode"})


class TestLearningFlywheel(unittest.TestCase):
    def test_incident_episode_raises_subsequent_change_risk(self):
        """The compounding loop: against pre-incident memory, the pool-raise
        change scores MEDIUM; after the incident workflow writes its learned
        episode, the identical change scores HIGH. The two measurements use
        identically-initialized but separate memories so the first change's
        own decision episode cannot contaminate the comparison."""
        _, baseline_env = build_environment(canonical())
        before, _ = run_change_risk(_pool_change(),
                                    **_env(baseline_env["memory"]))
        self.assertEqual(before.checkpoint["score"]["band"], "MEDIUM")

        incident, incident_env = build_environment(canonical())
        paused, ctx = run_incident_remediation(incident, **incident_env)
        done, ctx = run_incident_remediation(
            incident, **incident_env, ctx=ctx,
            approvals={"approval_gate": True}, checkpoint=paused.checkpoint)
        self.assertEqual(done.status, WorkflowStatus.SUCCEEDED)

        after, _ = run_change_risk(_pool_change(),
                                   **_env(incident_env["memory"]))
        self.assertEqual(after.checkpoint["score"]["band"], "HIGH")


if __name__ == "__main__":
    unittest.main()
