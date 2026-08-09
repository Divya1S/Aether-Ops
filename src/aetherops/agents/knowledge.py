"""Knowledge agent: the agentic investigation loop (PROMPT-11 rule 1).

Each iteration, the model chooses the next read-only action from the tool
menu — or declares finish with a reason. Decisions are structured JSON
validated against DECISION_SCHEMA, with one re-prompt on an invalid reply;
a second invalid reply triggers the deterministic scripted fallback, which
completes the baseline evidence bundle (the loop can degrade, never
regress below the pre-agentic behavior).

Hard bounds: MAX_STEPS iterations, duplicate-call rejection (a repeat
consumes a step — a stuck model runs out of budget, it does not spin),
forced finish on exhaustion. Every decision is audited (`agent.decision`),
making trajectories replayable from the ledger like everything else.

Safety is inherited, not asserted: the menu is READ-only, and even a fully
hijacked decision stream cannot reach a write — the connector gateway's
principal guard denies write-risk tools to everyone but the executor
(tests/test_agentic.py proves it).
"""
from __future__ import annotations

import json
import time

from aetherops.agents.base import Agent, score_confidence
from aetherops.agents.investigation import (BASELINE_SEQUENCE,
                                            DECISION_SCHEMA, TOOLS,
                                            extract_json, render_menu,
                                            run_tool)
from aetherops.core.schema import validate
from aetherops.core.types import AgentResult, Citation, Evidence, new_id
from aetherops.gateway.model_gateway import TaskProfile
from aetherops.prompts.registry import get_prompt

MAX_STEPS = 8


class KnowledgeAgent(Agent):
    name = "knowledge"
    tier = "fast"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["evidence_count", "planned_queries", "gaps",
                     "similar_episodes", "investigation"],
        "properties": {
            "evidence_count": {"type": "integer"},
            "planned_queries": {"type": "integer"},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "similar_episodes": {"type": "array",
                                 "items": {"type": "string"}},
            "investigation": {"type": "object"},
        }}

    def run(self, ctx) -> AgentResult:
        service = ctx.results["triage"].output["service"]
        template = get_prompt("investigate")
        profile = TaskProfile(task="investigate", tier_hint=self.tier,
                              severity=ctx.incident.severity,
                              prompt_id=template.id,
                              prompt_version=template.version)

        gaps: list[str] = []
        trajectory: list[dict] = []
        called: set[str] = set()
        called_names: list[str] = []
        pending_commits: list[str] = []
        attempted = 0
        tokens = 0
        model_id = "n/a"
        observation = "none yet"
        termination = "budget:max-steps"

        for step in range(MAX_STEPS):
            kinds = sorted({e.kind for e in ctx.evidence})
            prompt = template.render(
                tools=render_menu(), kinds=kinds,
                pending=pending_commits, called=sorted(set(called_names)),
                steps=step, max_steps=MAX_STEPS,
                observation=observation[:200],
                title=ctx.incident.title, service=service)

            decision, error = None, "no JSON object found"
            for parse_attempt in range(2):
                suffix = ("" if parse_attempt == 0 else
                          f"\nYour previous reply was invalid ({error}). "
                          "Reply with ONLY the JSON object.")
                response = ctx.gateway.complete(prompt + suffix, profile)
                tokens += response.tokens
                model_id = response.model_id
                parsed = extract_json(response.text)
                errors = (validate(parsed, DECISION_SCHEMA)
                          if parsed is not None else ["no JSON object found"])
                if not errors:
                    decision = parsed
                    break
                error = "; ".join(errors[:2])

            if decision is None:
                # Two invalid decisions: degrade to the scripted baseline —
                # never below the pre-agentic behavior.
                if ctx.audit is not None:
                    ctx.audit.append(actor=self.name,
                                     action="investigate.fallback",
                                     payload={"step": step, "error": error})
                attempted += self._scripted_fallback(ctx, service, called,
                                                     pending_commits, gaps)
                termination = "fallback:invalid-decisions"
                break

            action = decision["action"]
            args = decision.get("args") or {}
            trajectory.append({"action": action, "args": args})
            if ctx.audit is not None:
                ctx.audit.append(
                    actor=self.name, action="agent.decision",
                    payload={"step": step, "action": action, "args": args,
                             "rationale": decision["rationale"][:120]})

            if action == "finish":
                termination = f"model:{decision['rationale'][:80]}"
                break
            if action not in TOOLS:
                observation = (f"action {action!r} rejected: not in the "
                               "read-only tool menu")
                continue
            key = f"{action}:{json.dumps(args, sort_keys=True, default=str)}"
            if key in called:
                observation = f"duplicate call {action} rejected"
                continue

            called.add(key)
            called_names.append(action)
            outcome = run_tool(ctx, service, action, args)
            if outcome.counted:
                attempted += 1
            if outcome.gap:
                gaps.append(outcome.gap)
            observation = outcome.observation
            for sha in outcome.commits:
                if sha not in pending_commits:
                    pending_commits.append(sha)
            if action == "get_commit_diff" and args.get("sha") in pending_commits:
                pending_commits.remove(args["sha"])

        similar = ctx.memory.search(f"{service} OOMKilled pool latency deploy")
        for episode in similar:
            ctx.add_evidence(Evidence(
                id=new_id("ev"), kind="episode",
                summary=f"past incident ({episode.get('failure_class')}): "
                        f"{episode.get('summary')}",
                citation=Citation(
                    source="aetherops-memory",
                    ref=f"memory://episode/{episode['id']}",
                    excerpt=str(episode.get("summary", ""))[:200],
                    retrieved_at=time.time())))

        coverage = (attempted - len(gaps)) / attempted if attempted else 0.0
        return AgentResult(
            agent=self.name,
            output={"evidence_count": len(ctx.evidence),
                    "planned_queries": attempted,
                    "gaps": gaps,
                    "similar_episodes": [e["id"] for e in similar],
                    "investigation": {"steps": len(trajectory),
                                      "termination": termination,
                                      "trajectory": trajectory}},
            confidence=score_confidence(0.9, coverage),
            citations=[e.citation for e in ctx.evidence],
            model_id=model_id, tokens=tokens)

    @staticmethod
    def _scripted_fallback(ctx, service, called, pending_commits, gaps) -> int:
        """Deterministic baseline gathering for whatever the loop didn't
        cover — the pre-agentic behavior as a floor."""
        attempted = 0
        for action in BASELINE_SEQUENCE:
            if action == "get_commit_diff":
                for sha in list(pending_commits):
                    key = f"{action}:{json.dumps({'sha': sha}, sort_keys=True)}"
                    if key in called:
                        continue
                    called.add(key)
                    outcome = run_tool(ctx, service, action, {"sha": sha})
                    attempted += 1 if outcome.counted else 0
                    if outcome.gap:
                        gaps.append(outcome.gap)
                    pending_commits.remove(sha)
                continue
            key = f"{action}:{json.dumps({}, sort_keys=True)}"
            if any(existing.startswith(f"{action}:") for existing in called):
                continue
            called.add(key)
            outcome = run_tool(ctx, service, action, {})
            if outcome.counted:
                attempted += 1
            if outcome.gap:
                gaps.append(outcome.gap)
            for sha in outcome.commits:
                if sha not in pending_commits:
                    pending_commits.append(sha)
        # commits discovered by the fallback's own deploy query
        for sha in list(pending_commits):
            outcome = run_tool(ctx, service, "get_commit_diff", {"sha": sha})
            attempted += 1 if outcome.counted else 0
            if outcome.gap:
                gaps.append(outcome.gap)
            pending_commits.remove(sha)
        return attempted
