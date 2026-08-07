"""Triage agent: dedupe/classify/severity/service attribution
(docs/02-agents.md). Severity mapping is deterministic — a wrong severity must
be impossible given the monitor metadata; the fast-tier model only writes the
human-facing summary.
"""
from __future__ import annotations

from aetherops.agents.base import Agent, score_confidence
from aetherops.core.types import AgentResult, Evidence, Severity, new_id
from aetherops.gateway.model_gateway import TaskProfile


_SEVERITY_RULES = {
    ("high", True): Severity.SEV2,     # (urgency, customer_impact)
    ("high", False): Severity.SEV3,
    ("low", True): Severity.SEV3,
    ("low", False): Severity.SEV4,
}


class TriageAgent(Agent):
    name = "triage"
    tier = "fast"

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

        response = ctx.gateway.complete(
            f"[triage] alert: {alert.data['title']} "
            f"service={alert.data['service']} urgency={alert.data['urgency']}",
            TaskProfile(task="triage", tier_hint=self.tier,
                        severity=ctx.incident.severity))

        return AgentResult(
            agent=self.name,
            output={"severity": severity.value,
                    "service": alert.data["service"],
                    "summary": response.text},
            confidence=score_confidence(0.95, 1.0),
            citations=[evidence.citation],
            model_id=response.model_id,
            tokens=response.tokens)
