"""Access control before the model (PROMPT-10, inspired by the ReviewOps
Agent capstone): classification-gated prompts, principal-gated writes,
role-gated API. Humans keep full visibility; models and callers don't."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from aetherops.api import server as api_server
from aetherops.connectors.fakes import FakeKubernetes
from aetherops.core.context import WorkflowContext
from aetherops.core.types import Citation, Evidence, WorkflowStatus
from aetherops.evals.scenarios import build_environment, canonical
from aetherops.security.audit import AuditLog
from aetherops.workflows.incident_remediation import run_incident_remediation


def _evidence(kind: str, classification: str) -> Evidence:
    return Evidence(
        id=f"ev-{kind}", kind=kind, summary=f"the {kind} content",
        citation=Citation(source="test", ref=f"test://{kind}",
                          excerpt=f"secret {kind} excerpt",
                          retrieved_at=0.0),
        classification=classification)


class TestClassificationGatesTheModel(unittest.TestCase):
    def _ctx(self):
        ctx = WorkflowContext(incident=None, connectors=None, gateway=None,
                              audit=None, memory=None)
        ctx.add_evidence(_evidence("metrics", "INTERNAL"))
        ctx.add_evidence(_evidence("discussion", "CONFIDENTIAL"))
        return ctx

    def test_confidential_content_never_enters_the_prompt(self):
        digest = self._ctx().evidence_digest()
        self.assertIn("the metrics content", digest)
        self.assertNotIn("secret discussion excerpt", digest)
        self.assertIn("classification CONFIDENTIAL exceeds model clearance",
                      digest)
        self.assertIn("[E2]", digest)            # numbering stays stable

    def test_clearance_is_configuration(self):
        os.environ["AETHEROPS_MODEL_CLEARANCE"] = "CONFIDENTIAL"
        try:
            digest = self._ctx().evidence_digest()
            self.assertIn("secret discussion excerpt", digest)
        finally:
            del os.environ["AETHEROPS_MODEL_CLEARANCE"]

    def test_workflow_slack_evidence_is_confidential_yet_human_visible(self):
        import dataclasses
        base = canonical()
        scenario = dataclasses.replace(
            base, snapshot=dataclasses.replace(
                base.snapshot,
                slack_messages=("benign on-call chatter about the deploy",)))
        incident, env = build_environment(scenario)
        paused, ctx = run_incident_remediation(incident, **env)
        done, ctx = run_incident_remediation(
            incident, **env, ctx=ctx,
            approvals={paused.pending_gate: True},
            checkpoint=paused.checkpoint)
        self.assertEqual(done.status, WorkflowStatus.SUCCEEDED)

        digest = ctx.evidence_digest()           # what models see
        self.assertNotIn("benign on-call chatter", digest)
        self.assertIn("exceeds model clearance", digest)
        doc = done.checkpoint["postmortem"]["markdown"]   # what humans see
        self.assertIn("slack://inc-checkout-service/thread", doc)


class TestPrincipalGatesWrites(unittest.TestCase):
    def test_agent_principals_cannot_invoke_write_tools(self):
        audit = AuditLog()
        k8s = FakeKubernetes(audit=audit)
        for principal in ("workflow", "knowledge", "root_cause"):
            with self.assertRaises(PermissionError):
                k8s.call("rollback_deployment",
                         {"service": "s", "revision": "r"},
                         principal=principal)
        denied = [r for r in audit.records if r.action == "tool.denied"]
        self.assertEqual(len(denied), 3)
        self.assertEqual(denied[0].payload["risk"], "HIGH")

    def test_executor_principal_is_allowed_and_reads_stay_open(self):
        k8s = FakeKubernetes(audit=AuditLog())
        result = k8s.call("rollback_deployment",
                          {"service": "s", "revision": "r"},
                          principal="executor")
        self.assertTrue(result.data["dry_run"])
        k8s.call("get_events", {"service": "s"}, principal="knowledge")


class TestRoleGatedApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api_server.STATE = api_server.AppState()
        api_server.STATE.tokens["viewer-tok"] = "viewer"
        api_server.STATE.tokens["approver-tok"] = "approver"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         api_server.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _request(self, path, method="GET", token=None, body=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"}
                        if token else {})})
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_viewer_reads_but_cannot_mutate(self):
        status, _ = self._request("/v1/runbooks/search?q=rollback",
                                  token="viewer-tok")
        self.assertEqual(status, 200)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._request("/v1/incidents", method="POST",
                          token="viewer-tok", body={})
        self.assertEqual(caught.exception.code, 403)

    def test_approver_decides_but_cannot_create(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._request("/v1/incidents", method="POST",
                          token="approver-tok", body={})
        self.assertEqual(caught.exception.code, 403)

        _, created = self._request("/v1/incidents", method="POST",
                                   token="aetherops-dev", body={})
        _, resolved = self._request(
            f"/v1/incidents/{created['incident_id']}/approvals",
            method="POST", token="approver-tok",
            body={"decision": "approve"})
        self.assertEqual(resolved["status"], "SUCCEEDED")

    def test_unknown_token_is_401_not_403(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._request("/v1/evals", token="stolen-token")
        self.assertEqual(caught.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
