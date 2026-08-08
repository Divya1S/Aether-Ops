"""Evaluation/Verifier agent: post-remediation verification against baseline
metrics (docs/02-agents.md). A remediation is not done when it executes; it is
done when the symptom is measurably gone. Verification failure is a
PermanentError, which triggers saga compensation of the executed steps
(docs/03-orchestration.md §4).
"""
from __future__ import annotations

from aetherops.agents.base import Agent, PermanentError, score_confidence
from aetherops.core.types import AgentResult
from aetherops.gateway.model_gateway import TaskProfile
from aetherops.prompts.registry import get_prompt

P99_BASELINE_MS = 300


class VerifierAgent(Agent):
    name = "verifier"
    tier = "standard"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["recovered", "p99_ms", "note"],
        "properties": {
            "recovered": {"type": "boolean"},
            "p99_ms": {"type": "number"},
            "note": {"type": "string"},
        }}

    def run(self, ctx) -> AgentResult:
        service = ctx.results["triage"].output["service"]
        metrics = ctx.connectors.call(
            "datadog", "query_metrics",
            {"query": f"p99{{service:{service}}}", "window": "post-remediation"},
            principal=self.name)
        latest_p99 = metrics.data["series"][-1]["p99_ms"]
        recovered = (latest_p99 <= P99_BASELINE_MS
                     and metrics.data.get("oomkilled_events_last_10m", 0) == 0)

        template = get_prompt("verify")
        response = ctx.gateway.complete(
            template.render(
                p99=latest_p99, baseline=P99_BASELINE_MS,
                oomkilled=metrics.data.get("oomkilled_events_last_10m")),
            TaskProfile(task="verify", tier_hint=self.tier,
                        severity=ctx.incident.severity,
                        prompt_id=template.id,
                        prompt_version=template.version))

        if not recovered:
            raise PermanentError(
                f"verification failed: p99={latest_p99}ms above baseline "
                f"{P99_BASELINE_MS}ms — compensate and escalate")

        return AgentResult(
            agent=self.name,
            output={"recovered": True, "p99_ms": latest_p99,
                    "note": response.text},
            confidence=score_confidence(0.95, 1.0),
            citations=[metrics.citation],
            model_id=response.model_id, tokens=response.tokens)
