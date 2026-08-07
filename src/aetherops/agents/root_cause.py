"""Root Cause agent: hypothesis generation and ranking over the evidence
bundle (docs/02-agents.md). Reasoning tier. Every causal claim must reference
evidence IDs; hallucinated references ([En] beyond the bundle) are a
PermanentError — hallucination containment layer 2
(docs/11-failure-handling.md §5).
"""
from __future__ import annotations

import re

from aetherops.agents.base import Agent, PermanentError, score_confidence
from aetherops.core.types import AgentResult
from aetherops.gateway.model_gateway import TaskProfile

_EREF = re.compile(r"\[E(\d+)\]")


class RootCauseAgent(Agent):
    name = "root_cause"
    tier = "reasoning"

    def run(self, ctx) -> AgentResult:
        digest = ctx.evidence_digest()
        prompt = (
            "[root_cause] Produce a causal hypothesis for this incident. "
            "Cite evidence as [En] for every causal claim; if the evidence "
            "does not support a causal chain, say 'Insufficient evidence'.\n"
            f"Incident: {ctx.incident.title}\nEvidence bundle:\n{digest}")
        response = ctx.gateway.complete(
            prompt, TaskProfile(task="root_cause", tier_hint=self.tier,
                                severity=ctx.incident.severity))

        cited_idx = sorted({int(m) for m in _EREF.findall(response.text)})
        invalid = [i for i in cited_idx if i < 1 or i > len(ctx.evidence)]
        if invalid:
            raise PermanentError(
                f"hallucinated citation(s) E{invalid}: bundle has "
                f"{len(ctx.evidence)} items")

        if "insufficient evidence" in response.text.lower() or not cited_idx:
            return AgentResult(
                agent=self.name,
                output={"status": "insufficient-evidence",
                        "hypothesis": response.text},
                confidence=score_confidence(0.3, 0.3),
                citations=[e.citation for e in ctx.evidence[:1]],
                model_id=response.model_id, tokens=response.tokens)

        suspect = self._suspect_commit(ctx)
        grounded = suspect is not None and suspect in response.text
        failure_class = ("deploy-regression/memory"
                         if "deploy-regression/memory" in response.text
                         else "unclassified")
        coverage = len(cited_idx) / len(ctx.evidence)
        cited = [ctx.evidence[i - 1] for i in cited_idx]

        return AgentResult(
            agent=self.name,
            output={"status": "diagnosed",
                    "hypothesis": response.text,
                    "suspect_commit": suspect if grounded else None,
                    "failure_class": failure_class,
                    "cited_evidence": [e.id for e in cited]},
            confidence=score_confidence(0.9 if grounded else 0.6, coverage),
            citations=[e.citation for e in cited],
            model_id=response.model_id, tokens=response.tokens)

    @staticmethod
    def _suspect_commit(ctx) -> str | None:
        """Deterministic grounding: the suspect commit must exist in the
        evidence bundle (commit-kind citation refs), not just in model text."""
        for evidence in ctx.evidence_of_kind("commit"):
            sha = evidence.citation.ref.rsplit("/", 1)[-1]
            if re.fullmatch(r"[0-9a-f]{7,40}", sha):
                return sha
        return None
