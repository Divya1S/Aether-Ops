"""Triage agent: dedupe/classify/severity/service attribution
(docs/02-agents.md). Severity mapping is deterministic — a wrong severity must
be impossible given the monitor metadata; the fast-tier model only writes the
human-facing summary.
"""
from __future__ import annotations

from aetherops.agents.base import Agent, score_confidence
from aetherops.core.types import AgentResult, Evidence, Severity, new_id
from aetherops.gateway.model_gateway import TaskProfile
from aetherops.prompts.registry import get_prompt


_SEVERITY_RULES = {
    ("high", True): Severity.SEV2,     # (urgency, customer_impact)
    ("high", False): Severity.SEV3,
    ("low", True): Severity.SEV3,
    ("low", False): Severity.SEV4,
}


class TriageAgent(Agent):
    name = "triage"
    tier = "fast"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["severity", "service", "summary"],
        "properties": {
            "severity": {"type": "string",
                         "enum": ["SEV1", "SEV2", "SEV3", "SEV4"]},
            "service": {"type": "string"},
            "summary": {"type": "string"},
        }}

    def run(self, ctx) -> AgentResult:
        alert = ctx.connectors.call(
            "pagerduty", "get_incident",
            {"incident_id": ctx.incident.labels.get("pagerduty_id", "P-8842")},
            principal=self.name)
        evidence = ctx.add_evidence(Evidence(
            id=new_id("ev"), kind="alert",
            summary=f"PagerDuty {alert.data['id']}: {alert.data['title']} "
                    f"(urgency={alert.data['urgency']})",
            citation=alert.citation))

        severity = _SEVERITY_RULES.get(
            (alert.data["urgency"], bool(alert.data.get("customer_impact"))),
            Severity.SEV3)

        prompt = get_prompt("triage")
        response = ctx.gateway.complete(
            prompt.render(title=alert.data["title"],
                          service=alert.data["service"],
                          urgency=alert.data["urgency"]),
            TaskProfile(task="triage", tier_hint=self.tier,
                        severity=ctx.incident.severity,
                        prompt_id=prompt.id, prompt_version=prompt.version))

        return AgentResult(
            agent=self.name,
            output={"severity": severity.value,
                    "service": alert.data["service"],
                    "summary": response.text},
            confidence=score_confidence(0.95, 1.0),
            citations=[evidence.citation],
            model_id=response.model_id,
            tokens=response.tokens)
