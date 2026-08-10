"""Reviewer agent: independent plan verification before policy and gates
(docs/02-agents.md). Reviews like a senior engineer: it re-fetches ground
truth through its own connector reads and checks the plan against *that*,
never against the Planner's claims. Any failed check escalates before a
human is ever asked to approve.
"""
from __future__ import annotations

import re

from aetherops.agents.base import Agent, PermanentError, score_confidence
from aetherops.agents.planner import STEP_CATALOG
from aetherops.core.types import AgentResult
from aetherops.gateway.model_gateway import TaskProfile
from aetherops.prompts.registry import get_prompt

_HHMM = re.compile(r"(\d{1,2}):(\d{2})")


def _minute_of_day(ts: str | None) -> int | None:
    """Minutes-of-day from the first HH:MM in a timestamp. The golden
    snapshots are same-day, so minute-of-day ordering is sufficient here;
    production parses full RFC-3339 timestamps."""
    match = _HHMM.search(ts or "")
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def _first_breach(series: list[dict]) -> str | None:
    """Timestamp of the first sample whose p99 exceeds 2x the baseline (first
    sample) — a data-driven onset, not a hard-coded SLO threshold."""
    if not series:
        return None
    baseline = series[0].get("p99_ms", 0)
    for point in series:
        if point.get("p99_ms", 0) > 2 * baseline:
            return point.get("ts")
    return None


def _diff_raises_resource(diff: str) -> bool:
    """True when the diff increases a numeric resource limit (a memory
    regression must RAISE something to exhaust memory; a diff that lowers it
    cannot be the cause). Falls back to intent keywords when no numeric
    delta is present."""
    removed = [int(n) for n in re.findall(r"-\s*[^\n:]*:\s*(\d+)", diff)]
    added = [int(n) for n in re.findall(r"\+\s*[^\n:]*:\s*(\d+)", diff)]
    if added and removed:
        return max(added) > max(removed)
    return bool(re.search(r"(?i)\b(rais\w*|increas\w*|bump\w*|expand\w*)\b",
                          diff))


class ReviewerAgent(Agent):
    name = "reviewer"
    tier = "standard"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["verdict", "checks", "note"],
        "properties": {
            "verdict": {"type": "string", "enum": ["approve"]},
            "note": {"type": "string"},
            "checks": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "passed", "note"],
                "properties": {
                    "name": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "note": {"type": "string"},
                }}},
        }}

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

            # Temporal precedence: the deploy being reverted must PRE-date
            # symptom onset, or it cannot be the cause (audit C4/H5 — the one
            # check that questions the diagnosis, not the plan's self-
            # consistency). Independent reads: the reviewer's own metrics fetch.
            series = ctx.connectors.call(
                "datadog", "query_metrics", {"service": service},
                principal=self.name).data["series"]
            deploy_ts = deploys[0].get("deployed_at") if deploys else None
            breach_ts = _first_breach(series)
            dep_min = _minute_of_day(deploy_ts)
            breach_min = _minute_of_day(breach_ts)
            check("temporal-precedence",
                  dep_min is not None and breach_min is not None
                  and dep_min <= breach_min,
                  f"suspect deploy ({deploy_ts}) must precede first breach "
                  f"({breach_ts})")

            # Mechanism consistency: a memory regression's suspect commit must
            # RAISE a resource limit; one that lowers a limit cannot exhaust
            # memory (audit C4 — defends the live-model path the offline
            # backend's direction-check covers for replay).
            suspect = rca.output.get("suspect_commit")
            if suspect and rca.output.get("failure_class", "").endswith(
                    "/memory"):
                diff = ctx.connectors.call(
                    "github", "get_commit_diff", {"sha": suspect},
                    principal=self.name).data["diff"]
                check("mechanism-consistency", _diff_raises_resource(diff),
                      "a memory regression's suspect commit must increase a "
                      "resource limit, not reduce it")

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
        template = get_prompt("review")
        response = ctx.gateway.complete(
            template.render(passed=passed, total=len(checks),
                            service=service),
            TaskProfile(task="review", tier_hint=self.tier,
                        severity=ctx.incident.severity,
                        prompt_id=template.id,
                        prompt_version=template.version))

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
