"""Reviewer agent: independent plan verification before policy and gates
(docs/02-agents.md). Reviews like a senior engineer: it re-fetches ground
truth through its own connector reads and checks the plan against *that*,
never against the Planner's claims. Any failed check escalates before a
human is ever asked to approve.
"""
from __future__ import annotations

from aetherops.agents.base import Agent, PermanentError, score_confidence
from aetherops.agents.planner import STEP_CATALOG
from aetherops.core.types import AgentResult
from aetherops.gateway.model_gateway import TaskProfile


class ReviewerAgent(Agent):
    name = "reviewer"
    tier = "standard"

    def run(self, ctx) -> AgentResult:
        plan = ctx.results["planner"]
        rca = ctx.results["root_cause"]
        service = ctx.results["triage"].output["service"]
        steps = plan.output["steps"]

        # Independent ground truth: the reviewer's own (cached) read.
        deploys = ctx.connectors.call(
            "github", "list_recent_deploys", {"service": service},
            principal=self.name).data["deploys"]
        previous_revision = deploys[0]["previous_revision"] if deploys else None

        checks: list[dict] = []

        def check(name: str, passed: bool, note: str) -> None:
            checks.append({"name": name, "passed": bool(passed), "note": note})

        check("catalog-membership",
              all(step["action"] in STEP_CATALOG for step in steps),
              "every step must be a vetted Step Catalog action")

        rollback = next((s for s in steps
                         if s["action"] == "rollback_deployment"), None)
        if rollback is not None:
            check("rollback-target-grounded",
                  previous_revision is not None
                  and rollback["args"].get("revision") == previous_revision,
                  f"rollback target must equal the actual previous revision "
                  f"({previous_revision})")
            check("service-scope",
                  rollback["args"].get("service") == service,
                  "steps must be scoped to the incident's service")

        revert = next((s for s in steps
                       if s["action"] == "create_revert_pr"), None)
        if revert is not None:
            check("revert-sha-grounded",
                  revert["args"].get("sha") == rca.output.get("suspect_commit"),
                  "revert PR must target the diagnosed suspect commit")

        check("addresses-failure-class",
              not rca.output.get("failure_class", "").startswith(
                  "deploy-regression")
              or rollback is not None,
              "a deploy regression plan must reverse the deploy")

        passed = sum(1 for c in checks if c["passed"])
        response = ctx.gateway.complete(
            f"[review] checks_passed={passed}/{len(checks)} service={service}",
            TaskProfile(task="review", tier_hint=self.tier,
                        severity=ctx.incident.severity))

        failed = [c["name"] for c in checks if not c["passed"]]
        if failed:
            raise PermanentError(
                f"reviewer rejected plan: failed checks {failed} — escalate")

        return AgentResult(
            agent=self.name,
            output={"verdict": "approve", "checks": checks,
                    "note": response.text},
            confidence=score_confidence(0.95, 1.0),
            citations=list(plan.citations),
            model_id=response.model_id,
            tokens=response.tokens)
