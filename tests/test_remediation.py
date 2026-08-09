"""Audit remediation (M12): approval-race atomicity, fencing, async
execution, node timeouts, workflow deadlines, semantic-retry purity, and
input limits."""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from aetherops.agents.base import Agent, RetryPolicy
from aetherops.api import server as api_server
from aetherops.core.context import WorkflowContext
from aetherops.core.types import AgentResult, Citation, WorkflowStatus
from aetherops.orchestration.dag import DagExecutor, Node
from aetherops.rag.retriever import RagStore
from aetherops.security.audit import AuditLog
from aetherops.workflows.incident_remediation import _agent_node

TOKEN = "aetherops-dev"


def _request(port, path, method="GET", body=None, token=TOKEN):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode())


class TestApprovalAtomicity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api_server.STATE = api_server.AppState()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         api_server.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_concurrent_approvals_execute_exactly_once(self):
        _, created = _request(self.port, "/v1/incidents", method="POST",
                              body={})
        path = f"/v1/incidents/{created['incident_id']}/approvals"
        results = []

        def approve():
            try:
                status, _ = _request(self.port, path, method="POST",
                                     body={"decision": "approve"})
                results.append(status)
            except urllib.error.HTTPError as err:
                results.append(err.code)

        threads = [threading.Thread(target=approve) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 409])   # one wins, one 409

    def test_stale_fence_token_rejected(self):
        _, created = _request(self.port, "/v1/incidents", method="POST",
                              body={})
        path = f"/v1/incidents/{created['incident_id']}/approvals"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            _request(self.port, path, method="POST",
                     body={"decision": "approve", "fence": "stale-fence"})
        self.assertEqual(caught.exception.code, 409)
        status, resolved = _request(
            self.port, path, method="POST",
            body={"decision": "approve", "fence": created["fence"]})
        self.assertEqual(resolved["status"], "SUCCEEDED")

    def test_async_incident_returns_202_then_becomes_pollable(self):
        status, accepted = _request(self.port, "/v1/incidents",
                                    method="POST", body={"async": True})
        self.assertEqual(status, 202)
        self.assertEqual(accepted["status"], "RUNNING")
        for _ in range(100):
            _, polled = _request(self.port,
                                 f"/v1/incidents/{accepted['incident_id']}")
            if polled["status"] != "RUNNING":
                break
            time.sleep(0.05)
        self.assertEqual(polled["status"], "PAUSED")

    def test_oversized_body_is_ignored(self):
        _, created = _request(self.port, "/v1/incidents", method="POST",
                              body={})
        big = {"decision": "approve", "padding": "x" * (80 * 1024)}
        with self.assertRaises(urllib.error.HTTPError) as caught:
            _request(self.port,
                     f"/v1/incidents/{created['incident_id']}/approvals",
                     method="POST", body=big)
        self.assertEqual(caught.exception.code, 400)   # body dropped ⇒ no decision


class TestTimeouts(unittest.TestCase):
    def test_node_timeout_is_a_transient_then_fails(self):
        audit = AuditLog()

        def sleepy(ctx):
            time.sleep(0.5)
            return {}

        executor = DagExecutor(
            [Node("slow", sleepy, timeout_s=0.05,
                  retry=RetryPolicy(max_attempts=2, base_delay_s=0.01))],
            audit=audit, sleeper=lambda s: None)
        run = executor.execute(WorkflowContext(
            incident=None, connectors=None, gateway=None, audit=audit,
            memory=None))
        self.assertEqual(run.status, WorkflowStatus.FAILED)
        self.assertIn("budget", run.error)
        self.assertTrue(any(r.action == "node.timeout"
                            for r in audit.records))

    def test_workflow_deadline_escalates_between_nodes(self):
        def slow_enough(ctx):
            time.sleep(0.1)
            return {}

        executor = DagExecutor(
            [Node("first", slow_enough),
             Node("second", lambda ctx: {}, deps=("first",))],
            deadline_s=0.05, sleeper=lambda s: None)
        run = executor.execute(WorkflowContext(
            incident=None, connectors=None, gateway=None, audit=None,
            memory=None))
        self.assertEqual(run.status, WorkflowStatus.FAILED)
        self.assertIn("deadline", run.error)
        self.assertIn("first", run.checkpoint)          # partial findings kept
        self.assertNotIn("second", run.checkpoint)


class _FlakyEvidenceAgent(Agent):
    """Adds evidence every attempt; emits invalid output on the first —
    proves the semantic retry rolls back the failed attempt's mutations."""

    name = "flaky"
    output_schema = {"type": "object", "additionalProperties": False,
                     "required": ["ok"],
                     "properties": {"ok": {"type": "boolean"}}}

    def __init__(self):
        self.calls = 0

    def run(self, ctx) -> AgentResult:
        self.calls += 1
        from aetherops.core.types import Evidence
        ctx.add_evidence(Evidence(
            id=f"ev-{self.calls}", kind="metrics",
            summary=f"attempt {self.calls}",
            citation=Citation(source="t", ref=f"t://{self.calls}",
                              excerpt="x", retrieved_at=0.0)))
        ctx.params[f"attempt_{self.calls}"] = True
        output = {"ok": True} if self.calls > 1 else {"ok": "not-a-bool"}
        return AgentResult(agent=self.name, output=output, confidence=0.9,
                           citations=[Citation(source="t", ref="t://c",
                                               excerpt="x",
                                               retrieved_at=0.0)])


class TestSemanticRetryPurity(unittest.TestCase):
    def test_failed_attempt_side_effects_are_rolled_back(self):
        agent = _FlakyEvidenceAgent()
        ctx = WorkflowContext(incident=None, connectors=None, gateway=None,
                              audit=None, memory=None)
        _agent_node(agent)(ctx)
        self.assertEqual(agent.calls, 2)
        self.assertEqual(len(ctx.evidence), 1)          # not two
        self.assertEqual(ctx.evidence[0].summary, "attempt 2")
        self.assertNotIn("attempt_1", ctx.params)


class TestBounds(unittest.TestCase):
    def test_search_docs_handles_k_beyond_corpus(self):
        store = RagStore(chunker="paragraph", embedder="tfidf")
        docs = store.search_docs("latency rollback certificate dns disk",
                                 k=50)
        self.assertLessEqual(len(docs), 10)             # corpus size
        self.assertEqual(len(docs), len(set(docs)))     # no duplicates


if __name__ == "__main__":
    unittest.main()
