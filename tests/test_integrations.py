"""Optional framework integrations (LangGraph, FastAPI, vector stores, RAGAS).

Each test SKIPS when its extra isn't installed, so the stdlib core stays the
default and CI (which installs no extras) stays green and network-free. Run
locally after `pip install "aetherops[langgraph,api,vectorstores]"`.
"""
import importlib.util
import unittest


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


@unittest.skipUnless(_has("langgraph"), "langgraph extra not installed")
class TestLangGraphWorkflow(unittest.TestCase):
    """The agent pipeline orchestrated as a real LangGraph StateGraph with
    conditional routing and a human-in-the-loop interrupt."""

    def test_canonical_remediates_through_the_graph(self):
        from aetherops.evals.scenarios import canonical
        from aetherops.integrations.langgraph_workflow import \
            run_incident_langgraph
        state = run_incident_langgraph(canonical(), approve=True)
        self.assertEqual(state["status"], "remediated")
        self.assertEqual(state["failure_class"], "deploy-regression/memory")
        self.assertIn("rollback_deployment", state["steps"])

    def test_adversarial_escalates_via_conditional_routing(self):
        from aetherops.evals.scenarios import reverted_pool
        from aetherops.integrations.langgraph_workflow import \
            run_incident_langgraph
        state = run_incident_langgraph(reverted_pool())
        self.assertEqual(state["status"], "escalated")

    def test_denied_at_the_human_in_the_loop_gate(self):
        from aetherops.evals.scenarios import canonical
        from aetherops.integrations.langgraph_workflow import \
            run_incident_langgraph
        state = run_incident_langgraph(canonical(), approve=False)
        self.assertEqual(state["status"], "denied")


@unittest.skipUnless(_has("fastapi"), "api extra (fastapi) not installed")
class TestFastAPISurface(unittest.TestCase):
    """The same incident lifecycle exposed as an idiomatic FastAPI app."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from aetherops.integrations.fastapi_app import app
        self.client = TestClient(app)
        self.auth = {"Authorization": "Bearer aetherops-dev"}

    def test_health_reports_fastapi_surface(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["surface"], "fastapi")

    def test_incident_lifecycle_over_fastapi(self):
        created = self.client.post("/v1/incidents", json={}, headers=self.auth)
        self.assertEqual(created.status_code, 201)
        body = created.json()
        self.assertEqual(body["status"], "PAUSED")
        self.assertEqual(body["diagnosis"]["suspect_commit"], "c9a1f42")
        resolved = self.client.post(
            f"/v1/incidents/{body['incident_id']}/approvals",
            json={"decision": "approve", "fence": body["fence"]},
            headers=self.auth)
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["status"], "SUCCEEDED")

    def test_scenario_selection_and_400(self):
        cert = self.client.post("/v1/incidents", json={"scenario": "s4-cert-expiry"},
                                headers=self.auth)
        self.assertEqual(cert.json()["diagnosis"]["failure_class"],
                         "cert-expiry/tls")
        bad = self.client.post("/v1/incidents", json={"scenario": "nope"},
                               headers=self.auth)
        self.assertEqual(bad.status_code, 400)

    def test_auth_is_required(self):
        self.assertEqual(
            self.client.post("/v1/incidents", json={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
