"""End-to-end vertical slice: the canonical SEV2 from detection through
approval gate, execution, verification, and learning."""
import unittest

from aetherops.core.types import WorkflowStatus
from aetherops.demo import build_demo_environment
from aetherops.workflows.incident_remediation import run_incident_remediation


class TestIncidentRemediation(unittest.TestCase):
    def setUp(self):
        self.incident, self.env = build_demo_environment()

    def test_workflow_pauses_at_approval_gate(self):
        run, ctx = run_incident_remediation(self.incident, **self.env)
        self.assertEqual(run.status, WorkflowStatus.PAUSED)
        self.assertEqual(run.pending_gate, "approval_gate")
        # HIGH-risk rollback in prod -> tier-2 approval required by R3
        verdict = run.checkpoint["policy_check"]
        self.assertTrue(verdict["requires_approval"])
        self.assertEqual(verdict["approval_tier"], 2)
        # nothing has executed yet
        self.assertNotIn("execute", run.checkpoint)

    def test_approved_workflow_completes_and_learns(self):
        paused, ctx = run_incident_remediation(self.incident, **self.env)
        run, ctx = run_incident_remediation(
            self.incident, **self.env, ctx=ctx,
            approvals={"approval_gate": True}, checkpoint=paused.checkpoint)

        self.assertEqual(run.status, WorkflowStatus.SUCCEEDED)

        # Diagnosis is grounded: suspect commit exists in the evidence bundle
        rca = ctx.results["root_cause"]
        self.assertEqual(rca.output["suspect_commit"], "c9a1f42")
        self.assertEqual(rca.output["failure_class"], "deploy-regression/memory")
        self.assertGreater(rca.confidence, 0.5)

        # Citation contract: every agent result carries citations
        for name, result in ctx.results.items():
            self.assertTrue(result.citations, f"{name} has no citations")

        # Evidence bundle includes the recalled prior episode and retrieved
        # runbook guidance (advisory — excluded from RCA coverage)
        kinds = {evidence.kind for evidence in ctx.evidence}
        self.assertEqual(
            kinds, {"alert", "metrics", "deploy", "commit", "k8s-event",
                    "episode", "runbook"})

        # Saga contract with SAFE semantics (audit C2): a reversible step
        # carries an undo, but a rollback to a known-good revision does NOT —
        # auto-undoing it would redeploy the bad revision.
        executed = run.checkpoint["execute"]["executed"]
        self.assertEqual([record["action"] for record in executed],
                         ["rollback_deployment", "create_revert_pr"])
        by_action = {r["action"]: r["result"] for r in executed}
        self.assertIn("undo", by_action["create_revert_pr"])
        self.assertNotIn("undo", by_action["rollback_deployment"])

        # Verified, then learned: memory grew from 1 preloaded to 2 episodes
        self.assertTrue(run.checkpoint["verify"]["output"]["recovered"])
        self.assertEqual(len(self.env["memory"]), 2)

        # Governance: audit chain intact, tokens metered
        self.assertTrue(self.env["audit"].verify())
        self.assertGreater(len(self.env["audit"]), 20)
        self.assertGreater(self.env["gateway"].tokens_used, 0)

    def test_denied_workflow_never_executes(self):
        paused, ctx = run_incident_remediation(self.incident, **self.env)
        run, ctx = run_incident_remediation(
            self.incident, **self.env, ctx=ctx,
            approvals={"approval_gate": False}, checkpoint=paused.checkpoint)
        self.assertEqual(run.status, WorkflowStatus.FAILED)
        self.assertIn("denied", run.error)
        self.assertNotIn("execute", run.checkpoint)

    def test_resume_does_not_rerun_completed_nodes(self):
        paused, ctx = run_incident_remediation(self.incident, **self.env)
        evidence_before = len(ctx.evidence)
        run, ctx = run_incident_remediation(
            self.incident, **self.env, ctx=ctx,
            approvals={"approval_gate": True}, checkpoint=paused.checkpoint)
        # gather_evidence did not re-run on resume: bundle size unchanged
        self.assertEqual(len(ctx.evidence), evidence_before)


if __name__ == "__main__":
    unittest.main()
