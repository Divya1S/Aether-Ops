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


if __name__ == "__main__":
    unittest.main()
