"""Planner agent: proposes remediation expressed ONLY as Step Catalog
references (docs/02-agents.md, docs/03-orchestration.md §3). A proposal that
references anything outside the catalog is rejected, not sanitized — the
mechanism that keeps prompt injection from minting new capabilities.
"""
from __future__ import annotations

from aetherops.agents.base import Agent, PermanentError, score_confidence
from aetherops.core.types import AgentResult
from aetherops.gateway.model_gateway import TaskProfile
from aetherops.prompts.registry import get_prompt

# The vetted action catalog: the only verbs the platform can execute.
# Production: versioned registry with arg schemas, OPA annotations, and
# compensation handlers per entry (docs/03-orchestration.md §3).
STEP_CATALOG: dict[str, dict] = {
    "rollback_deployment": {"system": "kubernetes", "tool": "rollback_deployment",
                            "risk": "HIGH", "compensable": True},
    "create_revert_pr": {"system": "github", "tool": "create_revert_pr",
                         "risk": "MEDIUM", "compensable": True},
    "restart_pods": {"system": "kubernetes", "tool": "restart_pods",
                     "risk": "LOW", "compensable": False},
    "scale_deployment": {"system": "kubernetes", "tool": "scale_deployment",
                         "risk": "MEDIUM", "compensable": True},
}


class PlannerAgent(Agent):
    name = "planner"
    tier = "reasoning"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["steps", "rationale"],
        "properties": {
            "rationale": {"type": "string"},
            "steps": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["action", "system", "tool", "args", "risk",
                             "compensable"],
                "properties": {
                    "action": {"type": "string"},
                    "system": {"type": "string"},
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                    "risk": {"type": "string",
                             "enum": ["READ", "LOW", "MEDIUM", "HIGH",
                                      "CRITICAL"]},
                    "compensable": {"type": "boolean"},
                }}},
        }}

    def run(self, ctx) -> AgentResult:
        rca = ctx.results["root_cause"]
        if rca.output.get("status") != "diagnosed":
            raise PermanentError("cannot plan without a diagnosis — escalate")

        template = get_prompt("plan")
        response = ctx.gateway.complete(
            template.render(hypothesis=rca.output["hypothesis"],
                            catalog=sorted(STEP_CATALOG),
                            digest=ctx.evidence_digest()),
            TaskProfile(task="plan", tier_hint=self.tier,
                        severity=ctx.incident.severity,
                        prompt_id=template.id,
                        prompt_version=template.version))

        # Reference slice: deterministic mapping from failure class to
        # proposed actions (production: parsed from the schema-validated LLM
        # plan JSON; the catalog-membership check below is identical).
        if rca.output["failure_class"].startswith("deploy-regression"):
            proposed = ["rollback_deployment", "create_revert_pr"]
        else:
            raise PermanentError(
                f"no cataloged remediation for {rca.output['failure_class']} "
                "— escalate with diagnosis")

        deploy = ctx.connectors.call(          # cached read: no extra quota
            "github", "list_recent_deploys",
            {"service": ctx.results["triage"].output["service"]},
            principal=self.name).data["deploys"][0]

        steps = []
        for action in proposed:
            entry = STEP_CATALOG.get(action)
            if entry is None:
                raise PermanentError(f"plan rejected: uncataloged action {action!r}")
            args = {}
            if action == "rollback_deployment":
                args = {"service": deploy["service"],
                        "revision": deploy["previous_revision"]}
            elif action == "create_revert_pr":
                args = {"sha": rca.output["suspect_commit"]}
            steps.append({"action": action, "system": entry["system"],
                          "tool": entry["tool"], "args": args,
                          "risk": entry["risk"],
                          "compensable": entry["compensable"]})

        return AgentResult(
            agent=self.name,
            output={"steps": steps, "rationale": response.text},
            confidence=score_confidence(0.9, rca.confidence),
            citations=list(rca.citations),
            model_id=response.model_id, tokens=response.tokens)
