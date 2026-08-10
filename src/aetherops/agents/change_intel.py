"""Change Intelligence agent (docs/02-agents.md): change-set analysis,
failure-signature matching against episodic memory, and blast-radius
estimation via the service graph. This is where incident learning pays
forward — the episodes past incidents wrote are the evidence this agent
retrieves before the next similar change ships.
"""
from __future__ import annotations

import time

from aetherops.agents.base import Agent, score_confidence
from aetherops.core.types import AgentResult, Citation, Evidence, new_id


class ChangeIntelligenceAgent(Agent):
    name = "change_intel"
    tier = "standard"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["matched_episodes", "blast_radius", "dependents",
                     "service_incident_count"],
        "properties": {
            "matched_episodes": {"type": "array",
                                 "items": {"type": "string"}},
            "blast_radius": {"type": "integer"},
            "dependents": {"type": "array", "items": {"type": "string"}},
            "service_incident_count": {"type": "integer"},
        }}

    def run(self, ctx) -> AgentResult:
        change = ctx.change

        ctx.add_evidence(Evidence(
            id=new_id("ev"), kind="change",
            summary=f"{change.sha}: {change.title}",
            citation=Citation(
                source="github",
                ref=f"github://{change.service}/commit/{change.sha}",
                excerpt=f"{change.title} | {change.diff}"[:200],
                retrieved_at=time.time())))

        dependents = sorted(ctx.graph.dependents(change.service))
        ctx.add_evidence(Evidence(
            id=new_id("ev"), kind="topology",
            summary=f"blast radius of {change.service}: {len(dependents)} "
                    f"transitive dependents",
            citation=Citation(
                source="aetherops-graph",
                ref=f"graph://service/{change.service}/dependents",
                excerpt=f"dependents: {dependents}",
                retrieved_at=time.time())))

        # Failure-signature match: does this diff look like something that
        # has caused an incident before? Only incident episodes (those with a
        # failure_class) count — recorded change *decisions* are not
        # incidents and must not inflate the risk of later changes.
        # Only VERIFIED incident episodes are precedent (audit H4). An
        # unverified episode is a failed remediation — its diagnosis is
        # unproven, so it must not inflate a later change's risk or it would
        # amplify its own error.
        matched = [episode
                   for episode in ctx.memory.search(f"{change.title} {change.diff}")
                   if episode.get("failure_class") and episode.get("verified")]
        for episode in matched:
            ctx.add_evidence(Evidence(
                id=new_id("ev"), kind="episode",
                summary=f"past incident ({episode.get('failure_class')}, "
                        f"{episode.get('service')}): {episode.get('summary')}",
                citation=Citation(
                    source="aetherops-memory",
                    ref=f"memory://episode/{episode['id']}",
                    excerpt=str(episode.get("summary", ""))[:200],
                    retrieved_at=time.time())))

        # Service incident history counts every incident that OCCURRED on the
        # service — deliberately NOT filtered by `verified` (unlike the
        # failure-signature match above): a service with failed remediations is
        # more incident-prone, not less, so excluding unverified episodes here
        # would perversely make a troubled service look safe.
        service_history = [
            episode for episode in ctx.memory.search(change.service, k=10)
            if episode.get("service") == change.service
            and episode.get("failure_class")]

        return AgentResult(
            agent=self.name,
            output={"matched_episodes": [e["id"] for e in matched],
                    "blast_radius": len(dependents),
                    "dependents": dependents,
                    "service_incident_count": len(service_history)},
            confidence=score_confidence(0.9, 1.0),
            citations=[e.citation for e in ctx.evidence],
            model_id="n/a",   # analysis here is deterministic retrieval
            tokens=0)
