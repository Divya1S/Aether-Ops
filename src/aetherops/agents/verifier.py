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

P99_BASELINE_MS = 300


class VerifierAgent(Agent):
    name = "verifier"
    tier = "standard"

    def run(self, ctx) -> AgentResult:
        service = ctx.results["triage"].output["service"]
        metrics = ctx.connectors.call(
            "datadog", "query_metrics",
            {"query": f"p99{{service:{service}}}", "window": "post-remediation"},
            principal=self.name)
        latest_p99 = metrics.data["series"][-1]["p99_ms"]
        recovered = (latest_p99 <= P99_BASELINE_MS
                     and metrics.data.get("oomkilled_events_last_10m", 0) == 0)

        response = ctx.gateway.complete(
            f"[verify] post-remediation p99={latest_p99}ms, "
            f"baseline={P99_BASELINE_MS}ms, "
            f"oomkilled_last_10m={metrics.data.get('oomkilled_events_last_10m')}",
            TaskProfile(task="verify", tier_hint=self.tier,
                        severity=ctx.incident.severity))

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
