"""Knowledge agent: retrieval orchestration (docs/02-agents.md,
docs/06-retrieval-and-memory.md). Plans queries across connectors, records
Evidence with citations, recalls similar episodes from memory, and reports
explicit gaps — a connector outage lowers evidence coverage, which lowers
downstream confidence, which tightens the approval path
(docs/11-failure-handling.md).

Query planning is data-dependent: commit queries are derived from what the
deploy evidence actually contains, never assumed.
"""
from __future__ import annotations

import time

from aetherops.agents.base import Agent, TransientError, score_confidence
from aetherops.core.types import AgentResult, Citation, Evidence, new_id


class KnowledgeAgent(Agent):
    name = "knowledge"
    tier = "fast"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["evidence_count", "planned_queries", "gaps",
                     "similar_episodes"],
        "properties": {
            "evidence_count": {"type": "integer"},
            "planned_queries": {"type": "integer"},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "similar_episodes": {"type": "array",
                                 "items": {"type": "string"}},
        }}

    def run(self, ctx) -> AgentResult:
        service = ctx.results["triage"].output["service"]
        planned = 0
        gaps: list[str] = []

        def attempt(system, tool, args, kind, summarize):
            nonlocal planned
            planned += 1
            try:
                result = ctx.connectors.call(system, tool, args,
                                             principal=self.name)
            except TransientError as exc:
                gaps.append(f"{system}.{tool}: {exc}")
                return None
            ctx.add_evidence(Evidence(
                id=new_id("ev"), kind=kind,
                summary=summarize(result.data),
                citation=result.citation))
            return result

        attempt("datadog", "query_metrics",
                {"query": f"p99{{service:{service}}}", "window": "incident"},
                "metrics", lambda d: f"p99 series {d['series']}")

        deploys = attempt(
            "github", "list_recent_deploys", {"service": service}, "deploy",
            lambda d: (f"deploys: {d['deploys']}" if d["deploys"]
                       else "no deployments for this service in the lookback window"))

        # Data-dependent expansion: fetch diffs only for commits the deploy
        # evidence actually names.
        if deploys is not None and deploys.data["deploys"]:
            for sha in deploys.data["deploys"][0]["commits"]:
                attempt("github", "get_commit_diff", {"sha": sha}, "commit",
                        lambda d: f"{d['sha']}: {d['title']}")

        attempt("kubernetes", "get_events", {"service": service}, "k8s-event",
                lambda d: (f"events: {d['events'][:2]}..." if d["events"]
                           else "no abnormal pod events in window"))

        # Incident-channel discussion: recorded as evidence only when a
        # thread exists. External free text is the classic injection vector —
        # the Security agent screens it before any reasoning agent sees the
        # digest (docs/05-security.md §6).
        planned += 1
        try:
            thread = ctx.connectors.call("slack", "get_thread",
                                         {"service": service},
                                         principal=self.name)
            if thread.data["messages"]:
                ctx.add_evidence(Evidence(
                    id=new_id("ev"), kind="discussion",
                    summary=f"{len(thread.data['messages'])} message(s) in "
                            f"{thread.data['channel']}",
                    citation=thread.citation))
        except TransientError as exc:
            gaps.append(f"slack.get_thread: {exc}")
        except KeyError:                     # no slack connector registered
            planned -= 1

        # Runbook/postmortem retrieval through the RAG store: advisory
        # guidance with full source attribution (rag://doc#offset). It is
        # context for the diagnosis, not causal evidence — the Root Cause
        # agent excludes these kinds from its coverage denominator.
        if getattr(ctx, "rag", None) is not None:
            planned += 1
            query = f"{ctx.incident.title} {ctx.incident.description}"
            seen_docs: set[str] = set()
            for hit in ctx.rag.search(query, k=6):
                if hit.chunk.doc_id in seen_docs:
                    continue
                seen_docs.add(hit.chunk.doc_id)
                ctx.add_evidence(Evidence(
                    id=new_id("ev"), kind=hit.chunk.doc_kind,
                    summary=f"{hit.chunk.doc_title}: "
                            f"{hit.chunk.text[:120]}",
                    citation=Citation(
                        source="aetherops-rag", ref=hit.ref,
                        excerpt=hit.chunk.text[:200],
                        retrieved_at=time.time())))
                if len(seen_docs) == 2:
                    break

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

        coverage = (planned - len(gaps)) / planned if planned else 0.0
        return AgentResult(
            agent=self.name,
            output={"evidence_count": len(ctx.evidence),
                    "planned_queries": planned,
                    "gaps": gaps,
                    "similar_episodes": [e["id"] for e in similar]},
            confidence=score_confidence(0.9, coverage),
            citations=[e.citation for e in ctx.evidence],
            model_id="n/a",   # retrieval orchestration is deterministic here
            tokens=0)
