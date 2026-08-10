"""Plan & review safety (audit P2): the compiler bounds and de-dupes the
plan (no write amplification), and the Reviewer scopes EVERY step to the
incident's service, not just the rollback."""
import unittest
from types import SimpleNamespace

from aetherops.agents.base import PermanentError
from aetherops.agents.planner import MAX_PLAN_STEPS, PlannerAgent
from aetherops.agents.reviewer import ReviewerAgent
from aetherops.core.types import WorkflowStatus
from aetherops.evals.scenarios import build_environment, canonical
from aetherops.workflows.incident_remediation import run_incident_remediation


class _AllTools:
    TOOLS = {"rollback_deployment": 1, "create_revert_pr": 1,
             "restart_pods": 1, "scale_deployment": 1, "rotate_certificate": 1}


class _Registry:
    def get(self, system):
        return _AllTools()


def _compile(steps):
    ctx = SimpleNamespace(connectors=_Registry())
    return PlannerAgent()._compile(
        ctx, {"self_estimate": 0.9, "rationale": "x", "steps": steps})


class TestCompilerBounds(unittest.TestCase):
    def test_step_count_is_capped(self):
        steps = [{"action": "rollback_deployment",
                  "args": {"service": "s", "revision": f"r{i}"}}
                 for i in range(MAX_PLAN_STEPS + 1)]
        compiled, error = _compile(steps)
        self.assertIsNone(compiled)
        self.assertIn("steps", error)

    def test_duplicate_identical_steps_are_rejected(self):
        step = {"action": "rollback_deployment",
                "args": {"service": "s", "revision": "r"}}
        compiled, error = _compile([step, dict(step)])
        self.assertIsNone(compiled)
        self.assertIn("duplicate", error)

    def test_distinct_valid_plan_still_compiles(self):
        compiled, error = _compile([
            {"action": "rollback_deployment",
             "args": {"service": "s", "revision": "r"}},
            {"action": "create_revert_pr", "args": {"sha": "abc1234"}}])
        self.assertIsNotNone(compiled)
        self.assertEqual([s["action"] for s in compiled],
                         ["rollback_deployment", "create_revert_pr"])


class TestReviewerScopesEveryStep(unittest.TestCase):
    def test_off_scope_step_is_rejected(self):
        incident, env = build_environment(canonical())
        run, ctx = run_incident_remediation(incident, **env)
        self.assertEqual(run.status, WorkflowStatus.PAUSED)   # reviewer ran ok
        # Inject a well-formed step that targets a DIFFERENT service.
        ctx.results["planner"].output["steps"].append({
            "action": "rotate_certificate", "system": "kubernetes",
            "tool": "rotate_certificate",
            "args": {"service": "unrelated-service"},
            "risk": "MEDIUM", "compensable": True})
        with self.assertRaises(PermanentError) as caught:
            ReviewerAgent().run(ctx)
        self.assertIn("service", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
