"""Planner agent: the model PROPOSES the remediation plan as JSON; the
compile step validates it — schema → catalog membership → required args →
tool availability — and a proposal that fails twice degrades to the
deterministic fallback plan (audited `plan.fallback`). This makes
docs/03 §3's "plan compilation" literally true (PROMPT-11 rule 2): the
model decides the remediation; the catalog decides what is possible; the
Reviewer's independent grounding checks remain the last line.

The model's self_estimate replaces the old hardcoded 0.9 prior in the
planner's confidence — model-derived where structured output exists.
"""
from __future__ import annotations

from aetherops.agents.base import Agent, PermanentError, score_confidence
from aetherops.agents.investigation import extract_json
from aetherops.core.schema import validate
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

REQUIRED_ARGS: dict[str, tuple] = {
    "rollback_deployment": ("service", "revision"),
    "create_revert_pr": ("sha",),
    "restart_pods": ("service",),
    "scale_deployment": ("service",),
}

PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["self_estimate", "rationale", "steps"],
    "properties": {
        "self_estimate": {"type": "number"},
        "rationale": {"type": "string"},
        "steps": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["action", "args"],
            "properties": {"action": {"type": "string"},
                           "args": {"type": "object"}}}},
    }}


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

        service = ctx.results["triage"].output["service"]
        deploys = ctx.connectors.call(          # cached read: no extra quota
            "github", "list_recent_deploys", {"service": service},
            principal=self.name).data["deploys"]
        previous_revision = (deploys[0]["previous_revision"] if deploys
                             else "unknown")
        suspect = rca.output.get("suspect_commit") or "unknown"

        template = get_prompt("plan")
        catalog_lines = "\n".join(
            f"- {name} (risk {entry['risk']}; args: "
            f"{', '.join(REQUIRED_ARGS[name])})"
            for name, entry in sorted(STEP_CATALOG.items()))
        prompt = template.render(
            catalog=catalog_lines, hypothesis=rca.output["hypothesis"],
            service=service, previous_revision=previous_revision,
            suspect=suspect)
        profile = TaskProfile(task="plan", tier_hint=self.tier,
                              severity=ctx.incident.severity,
                              prompt_id=template.id,
                              prompt_version=template.version)

        tokens, model_id = 0, "n/a"
        compiled, proposal, error = None, None, "no JSON object found"
        for attempt in range(2):
            suffix = ("" if attempt == 0 else
                      f"\nYour previous plan was rejected ({error}). "
                      "Reply with ONLY the JSON object.")
            response = ctx.gateway.complete(prompt + suffix, profile)
            tokens += response.tokens
            model_id = response.model_id
            proposal = extract_json(response.text)
            compiled, error = self._compile(ctx, proposal)
            if compiled is not None:
                break

        if compiled is not None:
            self_estimate = min(0.99, max(0.05,
                                          float(proposal["self_estimate"])))
            rationale = proposal["rationale"]
            if ctx.audit is not None:
                ctx.audit.append(actor=self.name, action="plan.proposed",
                                 payload={"actions": [s["action"]
                                                      for s in compiled],
                                          "self_estimate": self_estimate})
        else:
            # Deterministic fallback: the pre-agentic plan for the class.
            compiled = self._fallback_plan(rca, service, previous_revision)
            self_estimate = 0.9        # documented prior, not a model claim
            rationale = ("fallback plan (model proposal rejected: "
                         f"{error})")
            if ctx.audit is not None:
                ctx.audit.append(actor=self.name, action="plan.fallback",
                                 payload={"error": error[:200]})

        return AgentResult(
            agent=self.name,
            output={"steps": compiled, "rationale": rationale},
            confidence=score_confidence(self_estimate, rca.confidence),
            citations=list(rca.citations),
            model_id=model_id, tokens=tokens)

    def _compile(self, ctx, proposal) -> tuple[list | None, str]:
        """Proposal JSON → validated, catalog-grounded steps; or an error."""
        if proposal is None:
            return None, "no JSON object found"
        errors = validate(proposal, PLAN_SCHEMA)
        if errors:
            return None, "; ".join(errors[:2])
        if not proposal["steps"]:
            return None, "plan must contain at least one step"
        compiled = []
        for step in proposal["steps"]:
            action = step["action"]
            entry = STEP_CATALOG.get(action)
            if entry is None:
                return None, f"uncataloged action {action!r}"
            missing = [k for k in REQUIRED_ARGS[action]
                       if k not in step["args"]]
            if missing:
                return None, f"{action}: missing args {missing}"
            try:                       # compile-time availability check
                connector = ctx.connectors.get(entry["system"])
            except KeyError:
                return None, f"{action}: system {entry['system']} unavailable"
            if entry["tool"] not in connector.TOOLS:
                return None, (f"{action}: tool {entry['tool']} not served "
                              f"by {entry['system']}")
            allowed = set(REQUIRED_ARGS[action])
            compiled.append({
                "action": action, "system": entry["system"],
                "tool": entry["tool"],
                "args": {k: v for k, v in step["args"].items()
                         if k in allowed},
                "risk": entry["risk"],
                "compensable": entry["compensable"]})
        return compiled, ""

    @staticmethod
    def _fallback_plan(rca, service, previous_revision) -> list:
        if not rca.output["failure_class"].startswith("deploy-regression"):
            raise PermanentError(
                f"no cataloged remediation for {rca.output['failure_class']} "
                "— escalate with diagnosis")
        entry_rb = STEP_CATALOG["rollback_deployment"]
        entry_pr = STEP_CATALOG["create_revert_pr"]
        return [
            {"action": "rollback_deployment", "system": entry_rb["system"],
             "tool": entry_rb["tool"],
             "args": {"service": service, "revision": previous_revision},
             "risk": entry_rb["risk"], "compensable": entry_rb["compensable"]},
            {"action": "create_revert_pr", "system": entry_pr["system"],
             "tool": entry_pr["tool"],
             "args": {"sha": rca.output["suspect_commit"]},
             "risk": entry_pr["risk"], "compensable": entry_pr["compensable"]},
        ]
