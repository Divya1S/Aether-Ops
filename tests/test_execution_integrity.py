"""Execution integrity (audit C2/C3/H1): compensation can actually undo
MEDIUM+ writes and does so safely, a partial batch never leaves un-undone
writes, and a timed-out node escalates instead of racing a retry."""
import time
import unittest
from types import SimpleNamespace

from aetherops.agents.base import PermanentError, RetryPolicy, TransientError
from aetherops.connectors.fakes import FakeKubernetes, Snapshot
from aetherops.core.types import WorkflowStatus
from aetherops.evals.scenarios import (GroundTruth, Scenario, build_environment,
                                       canonical)
from aetherops.orchestration.dag import DagExecutor, Node
from aetherops.workflows.incident_remediation import (_compensate_execute,
                                                      _execute,
                                                      run_incident_remediation)


class TestCompensatorPrincipal(unittest.TestCase):
    """C2 mechanical fix: the saga could not previously undo MEDIUM+ writes
    because compensation ran as a principal the write-guard denied."""

    def test_compensator_may_invoke_medium_write(self):
        k8s = FakeKubernetes(snapshot=Snapshot())
        result = k8s.call("rotate_certificate", {"service": "x"},
                          principal="compensator")
        self.assertTrue(result.data["rotated"])

    def test_ordinary_agent_principal_still_denied(self):
        k8s = FakeKubernetes(snapshot=Snapshot())
        with self.assertRaises(PermissionError):
            k8s.call("rotate_certificate", {"service": "x"},
                     principal="planner")


class TestSafeSagaOnVerificationFailure(unittest.TestCase):
    """C2 end-to-end: when post-remediation metrics stay bad, the saga
    compensates — safely."""

    def _run(self):
        snap = Snapshot(p99_post=(("14:40Z", 2100), ("14:45Z", 2050)))  # bad
        scen = Scenario(
            id="verify-fails", name="verification fails", snapshot=snap,
            truth=GroundTruth(outcome="remediated"),
            preload_episodes=canonical().preload_episodes)
        incident, env = build_environment(scen)
        paused, ctx = run_incident_remediation(incident, **env)
        run, ctx = run_incident_remediation(
            incident, **env, ctx=ctx,
            approvals={paused.pending_gate: True}, checkpoint=paused.checkpoint)
        return run, env["audit"]

    def test_status_is_compensated(self):
        run, _ = self._run()
        self.assertEqual(run.status, WorkflowStatus.COMPENSATED)

    def test_revert_pr_is_undone_by_compensator(self):
        _, audit = self._run()
        undo_calls = [r for r in audit.records
                      if r.action == "tool.call" and r.actor == "compensator"]
        tools = {r.payload["tool"] for r in undo_calls}
        self.assertIn("close_pr", tools)        # the revert PR was closed

    def test_rollback_is_not_dangerously_redeployed(self):
        # The rollback to the known-good revision must NOT be "undone" (that
        # would redeploy the bad revision).
        _, audit = self._run()
        bad = [r for r in audit.records
               if r.action == "tool.call" and r.actor == "compensator"
               and r.payload["tool"] == "rollback_deployment"]
        self.assertEqual(bad, [])


class TestPartialExecuteSelfCompensates(unittest.TestCase):
    """C3: a mid-batch failure must undo already-applied steps and never
    leave a write un-undone, and must not double-apply."""

    def _ctx_with_failing_second_step(self):
        calls = []

        class _Conn:
            def call(self, system, tool, args, principal):
                calls.append((tool, principal))
                if tool == "fail_write":
                    raise TransientError("boom")
                if tool == "undo_step1":
                    return SimpleNamespace(data={})
                return SimpleNamespace(
                    data={"undo": {"system": "s", "tool": "undo_step1",
                                   "args": {}}})

        steps = [{"action": "step1", "system": "s", "tool": "ok_write",
                  "args": {}},
                 {"action": "step2", "system": "s", "tool": "fail_write",
                  "args": {}}]
        ctx = SimpleNamespace(
            results={"planner": SimpleNamespace(output={"steps": steps})},
            connectors=_Conn(), params={}, audit=None)
        return ctx, calls

    def test_applied_step_is_undone_and_not_double_applied(self):
        ctx, calls = self._ctx_with_failing_second_step()
        with self.assertRaises(Exception):
            _execute(ctx)
        tools = [t for t, _ in calls]
        self.assertEqual(tools.count("ok_write"), 1)      # no double-apply
        self.assertIn("undo_step1", tools)                # self-compensated
        # The undo ran as the compensator principal.
        self.assertIn(("undo_step1", "compensator"), calls)

    def test_partial_progress_is_visible_to_compensation(self):
        # Even though _execute raised, the applied step was published to ctx
        # BEFORE the failing call (so it could never be left invisible/un-undone).
        ctx, _ = self._ctx_with_failing_second_step()
        with self.assertRaises(Exception):
            _execute(ctx)
        self.assertEqual([r["action"] for r in ctx.params["executed"]],
                         ["step1"])


class TestTimeoutEscalates(unittest.TestCase):
    """H1: a timed-out node escalates and is NOT retried — retrying an
    uncancellable worker would race a second attempt against shared state."""

    def test_timeout_does_not_retry(self):
        calls = []

        def slow(ctx):
            calls.append(1)
            time.sleep(0.3)
            return {}

        node = Node("slow", slow, timeout_s=0.05,
                    retry=RetryPolicy(max_attempts=3))
        run = DagExecutor([node], sleeper=lambda s: None).execute(None)
        self.assertEqual(run.status, WorkflowStatus.FAILED)
        self.assertIn("escalate", run.error.lower())
        self.assertEqual(len(calls), 1)     # escalated once, never retried


if __name__ == "__main__":
    unittest.main()
