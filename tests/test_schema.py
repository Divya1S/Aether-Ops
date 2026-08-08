"""Structured outputs: the JSON-Schema-subset validator and its enforcement
in the agent-node wrapper — semantic retry then escalation (docs/17 #10;
OWASP LLM05 Improper Output Handling, docs/05 §11)."""
import unittest

from aetherops.agents.base import Agent, PermanentError
from aetherops.core.context import WorkflowContext
from aetherops.core.schema import validate
from aetherops.core.types import AgentResult, Citation
from aetherops.workflows.incident_remediation import _agent_node

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["count", "label"],
    "properties": {
        "count": {"type": "integer"},
        "label": {"type": "string", "enum": ["a", "b"]},
        "flag": {"type": "boolean"},
        "maybe": {"type": ["string", "null"]},
        "items": {"type": "array", "items": {"type": "number"}},
    }}


class TestValidator(unittest.TestCase):
    def test_valid_instance_passes(self):
        self.assertEqual(validate(
            {"count": 3, "label": "a", "flag": True, "maybe": None,
             "items": [1, 2.5]}, SCHEMA), [])

    def test_missing_required_and_unexpected_properties(self):
        errors = validate({"count": 1, "extra": 1}, SCHEMA)
        self.assertTrue(any("label" in e for e in errors))
        self.assertTrue(any("extra" in e for e in errors))

    def test_bool_is_not_an_integer(self):
        errors = validate({"count": True, "label": "a"}, SCHEMA)
        self.assertTrue(any("expected type" in e for e in errors))

    def test_enum_and_union_types(self):
        self.assertTrue(validate({"count": 1, "label": "z"}, SCHEMA))
        self.assertEqual(validate({"count": 1, "label": "b",
                                   "maybe": "text"}, SCHEMA), [])

    def test_item_errors_carry_paths(self):
        errors = validate({"count": 1, "label": "a", "items": [1, "x"]},
                          SCHEMA)
        self.assertTrue(any("items[1]" in e for e in errors))


class _MalformedAgent(Agent):
    """Emits schema-violating output every time — exercises the semantic
    retry then the escalation path."""

    name = "malformed"
    output_schema = SCHEMA

    def __init__(self):
        self.calls = 0

    def run(self, ctx) -> AgentResult:
        self.calls += 1
        return AgentResult(
            agent=self.name, output={"count": "NaN"}, confidence=0.9,
            citations=[Citation(source="test", ref="test://x",
                                excerpt="x")])


class TestEnforcement(unittest.TestCase):
    def _ctx(self):
        return WorkflowContext(incident=None, connectors=None, gateway=None,
                               audit=None, memory=None)

    def test_malformed_output_gets_one_semantic_retry_then_escalates(self):
        agent = _MalformedAgent()
        node = _agent_node(agent)
        with self.assertRaises(PermanentError) as caught:
            node(self._ctx())
        self.assertEqual(agent.calls, 2)          # original + semantic retry
        self.assertIn("schema validation", str(caught.exception))

    def test_valid_output_passes_through_once(self):
        agent = _MalformedAgent()
        agent.run = lambda ctx: AgentResult(     # type: ignore[method-assign]
            agent="malformed",
            output={"count": 1, "label": "a"}, confidence=0.9,
            citations=[Citation(source="test", ref="test://x",
                                excerpt="x")])
        ctx = self._ctx()
        checkpoint = _agent_node(agent)(ctx)
        self.assertEqual(checkpoint["output"], {"count": 1, "label": "a"})
        self.assertIn("malformed", ctx.results)

    def test_every_model_calling_agent_declares_a_schema(self):
        from aetherops.agents.change_intel import ChangeIntelligenceAgent
        from aetherops.agents.knowledge import KnowledgeAgent
        from aetherops.agents.planner import PlannerAgent
        from aetherops.agents.reviewer import ReviewerAgent
        from aetherops.agents.root_cause import RootCauseAgent
        from aetherops.agents.security import SecurityAgent
        from aetherops.agents.triage import TriageAgent
        from aetherops.agents.verifier import VerifierAgent
        for agent_cls in (TriageAgent, KnowledgeAgent, SecurityAgent,
                          RootCauseAgent, PlannerAgent, ReviewerAgent,
                          VerifierAgent, ChangeIntelligenceAgent):
            self.assertIsInstance(agent_cls.output_schema, dict,
                                  agent_cls.__name__)


if __name__ == "__main__":
    unittest.main()
