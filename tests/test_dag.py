"""DAG executor semantics: retries, compensation order, gates, resume."""
import unittest

from aetherops.agents.base import PermanentError, RetryPolicy, TransientError
from aetherops.core.types import RiskLevel, WorkflowStatus
from aetherops.orchestration.dag import DagExecutor, GateSpec, Node

NO_SLEEP = lambda seconds: None


class TestRetries(unittest.TestCase):
    def test_transient_failures_are_retried_until_success(self):
        attempts = []

        def flaky(ctx):
            attempts.append(1)
            if len(attempts) < 3:
                raise TransientError("connector timeout")
            return {"ok": True}

        executor = DagExecutor([Node("flaky", flaky,
                                     retry=RetryPolicy(max_attempts=3))],
                               sleeper=NO_SLEEP)
        run = executor.execute(ctx=None)
        self.assertEqual(run.status, WorkflowStatus.SUCCEEDED)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(run.checkpoint["flaky"], {"ok": True})

    def test_retry_exhaustion_becomes_permanent(self):
        def always_fails(ctx):
            raise TransientError("still down")

        executor = DagExecutor([Node("down", always_fails,
                                     retry=RetryPolicy(max_attempts=2))],
                               sleeper=NO_SLEEP)
        run = executor.execute(ctx=None)
        self.assertEqual(run.status, WorkflowStatus.FAILED)
        self.assertIn("retries exhausted", run.error)

    def test_permanent_error_is_never_retried(self):
        attempts = []

        def denied(ctx):
            attempts.append(1)
            raise PermanentError("policy denial")

        executor = DagExecutor([Node("denied", denied)], sleeper=NO_SLEEP)
        run = executor.execute(ctx=None)
        self.assertEqual(run.status, WorkflowStatus.FAILED)
        self.assertEqual(len(attempts), 1)


class TestCompensation(unittest.TestCase):
    def test_compensations_run_in_reverse_completion_order(self):
        undone = []
        nodes = [
            Node("a", lambda ctx: {"n": "a"},
                 compensate=lambda ctx, out: undone.append("a")),
            Node("b", lambda ctx: {"n": "b"}, deps=("a",),
                 compensate=lambda ctx, out: undone.append("b")),
            Node("boom", lambda ctx: (_ for _ in ()).throw(PermanentError("x")),
                 deps=("b",)),
        ]
        run = DagExecutor(nodes, sleeper=NO_SLEEP).execute(ctx=None)
        self.assertEqual(run.status, WorkflowStatus.COMPENSATED)
        self.assertEqual(undone, ["b", "a"])
        self.assertEqual(run.compensated, ["b", "a"])

    def test_failed_compensation_does_not_block_the_rest(self):
        undone = []

        def bad_undo(ctx, out):
            raise RuntimeError("undo broke")

        nodes = [
            Node("a", lambda ctx: {}, compensate=lambda ctx, out: undone.append("a")),
            Node("b", lambda ctx: {}, deps=("a",), compensate=bad_undo),
            Node("boom", lambda ctx: (_ for _ in ()).throw(PermanentError("x")),
                 deps=("b",)),
        ]
        run = DagExecutor(nodes, sleeper=NO_SLEEP).execute(ctx=None)
        self.assertEqual(run.compensated, ["a"])   # b's undo failed, a's still ran


class TestGates(unittest.TestCase):
    def _nodes(self, ran):
        return [
            Node("pre", lambda ctx: ran.append("pre") or {"done": True}),
            Node("gate", run=None, deps=("pre",),
                 gate=GateSpec(risk=RiskLevel.HIGH, reason="test")),
            Node("post", lambda ctx: ran.append("post") or {"done": True},
                 deps=("gate",)),
        ]

    def test_gate_pauses_then_resume_skips_completed_nodes(self):
        ran = []
        executor = DagExecutor(self._nodes(ran), sleeper=NO_SLEEP)

        paused = executor.execute(ctx=None)
        self.assertEqual(paused.status, WorkflowStatus.PAUSED)
        self.assertEqual(paused.pending_gate, "gate")
        self.assertEqual(ran, ["pre"])

        resumed = executor.execute(ctx=None, approvals={"gate": True},
                                   checkpoint=paused.checkpoint)
        self.assertEqual(resumed.status, WorkflowStatus.SUCCEEDED)
        self.assertEqual(ran, ["pre", "post"])   # 'pre' not re-executed

    def test_gate_denial_fails_without_running_downstream(self):
        ran = []
        executor = DagExecutor(self._nodes(ran), sleeper=NO_SLEEP)
        paused = executor.execute(ctx=None)
        denied = executor.execute(ctx=None, approvals={"gate": False},
                                  checkpoint=paused.checkpoint)
        self.assertEqual(denied.status, WorkflowStatus.FAILED)
        self.assertIn("denied", denied.error)
        self.assertEqual(ran, ["pre"])

    def test_conditional_gate_is_skipped_when_not_needed(self):
        nodes = [
            Node("pre", lambda ctx: {"requires_approval": False}),
            Node("gate", run=None, deps=("pre",),
                 gate=GateSpec(risk=RiskLevel.HIGH, reason="test",
                               needed=lambda cp: cp["pre"]["requires_approval"])),
            Node("post", lambda ctx: {}, deps=("gate",)),
        ]
        run = DagExecutor(nodes, sleeper=NO_SLEEP).execute(ctx=None)
        self.assertEqual(run.status, WorkflowStatus.SUCCEEDED)


class TestValidation(unittest.TestCase):
    def test_cycles_are_rejected_at_build_time(self):
        with self.assertRaises(ValueError):
            DagExecutor([Node("a", lambda c: {}, deps=("b",)),
                         Node("b", lambda c: {}, deps=("a",))])

    def test_unknown_dependency_rejected(self):
        with self.assertRaises(ValueError):
            DagExecutor([Node("a", lambda c: {}, deps=("ghost",))])

    def test_duplicate_names_rejected(self):
        with self.assertRaises(ValueError):
            DagExecutor([Node("a", lambda c: {}), Node("a", lambda c: {})])


if __name__ == "__main__":
    unittest.main()
