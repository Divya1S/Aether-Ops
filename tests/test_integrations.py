"""Optional framework integrations (LangGraph, FastAPI, vector stores, LangSmith).

Each test SKIPS when its extra isn't installed, so the stdlib core stays the
default and CI (which installs no extras) stays green and network-free. Run
locally after `pip install "aetherops[langgraph,api,vectorstores,tracing]"`.
"""
import importlib.util
import os
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


@unittest.skipUnless(_has("chromadb"), "vectorstores extra (chromadb) not installed")
class TestChromaVectorStore(unittest.TestCase):
    """RAG retrieval over a real in-process ChromaDB collection."""

    @classmethod
    def setUpClass(cls):
        from aetherops.integrations.vectorstores import ChromaVectorStore
        cls.store = ChromaVectorStore()

    def test_retrieves_relevant_runbooks(self):
        self.assertIn("runbook-rollback", self.store.search_docs(
            "roll back a bad deploy to the previous revision", k=3))
        self.assertIn("runbook-cert", self.store.search_docs(
            "TLS handshake failures and SSL errors in client logs", k=3))
        self.assertIn("runbook-conn-pool", self.store.search_docs(
            "how should I size a database connection pool", k=3))


@unittest.skipUnless(_has("psycopg") and os.environ.get("DATABASE_URL"),
                     "pgvector: psycopg + DATABASE_URL required")
class TestPgVectorStore(unittest.TestCase):
    """RAG retrieval over Postgres + pgvector (runs only with a live DB)."""

    def test_retrieves_via_pgvector_cosine(self):
        from aetherops.integrations.vectorstores import PgVectorStore
        store = PgVectorStore()
        self.assertIn("runbook-rollback",
                      store.search_docs("roll back a bad deploy", k=3))


@unittest.skipUnless(_has("langsmith"), "tracing extra (langsmith) not installed")
class TestLangSmithTracing(unittest.TestCase):
    """LangSmith tracing instruments the gateway transparently (no network
    unless LANGCHAIN_TRACING_V2 is set)."""

    def test_tracing_is_transparent_to_completions(self):
        from aetherops.gateway.model_gateway import (ModelGateway,
                                                     OfflineHeuristicBackend,
                                                     TaskProfile)
        from aetherops.integrations.langsmith_tracing import trace_gateway
        gateway = ModelGateway(backend=OfflineHeuristicBackend())
        self.assertIs(trace_gateway(gateway), gateway)   # returns the gateway
        trace_gateway(gateway)                            # idempotent
        response = gateway.complete("why did the pods OOM",
                                    TaskProfile(task="root_cause"))
        self.assertEqual(response.tier, "standard")
        self.assertGreater(response.tokens, 0)

    def test_instrumented_gateway_runs_a_full_incident(self):
        from aetherops.evals.scenarios import build_environment, canonical
        from aetherops.integrations.langsmith_tracing import trace_gateway
        from aetherops.workflows.incident_remediation import \
            run_incident_remediation
        incident, env = build_environment(canonical())
        trace_gateway(env["gateway"])
        run, ctx = run_incident_remediation(incident, **env)
        self.assertEqual(run.status.value, "PAUSED")
        self.assertEqual(
            ctx.results.get("root_cause").output.get("failure_class"),
            "deploy-regression/memory")


if __name__ == "__main__":
    unittest.main()
