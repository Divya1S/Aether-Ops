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
from aetherops.prompts.registry import get_prompt

_EREF = re.compile(r"\[E(\d+)\]")


class RootCauseAgent(Agent):
    name = "root_cause"
    tier = "reasoning"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["status", "hypothesis"],
        "properties": {
            "status": {"type": "string",
                       "enum": ["diagnosed", "insufficient-evidence"]},
            "hypothesis": {"type": "string"},
            "suspect_commit": {"type": ["string", "null"]},
            "failure_class": {"type": "string"},
            "cited_evidence": {"type": "array",
                               "items": {"type": "string"}},
        }}

    def run(self, ctx) -> AgentResult:
        digest = ctx.evidence_digest()
        template = get_prompt("root_cause")
        response = ctx.gateway.complete(
            template.render(title=ctx.incident.title, digest=digest),
            TaskProfile(task="root_cause", tier_hint=self.tier,
                        severity=ctx.incident.severity,
                        prompt_id=template.id,
                        prompt_version=template.version))

        cited_idx = sorted({int(m) for m in _EREF.findall(response.text)})
        invalid = [i for i in cited_idx if i < 1 or i > len(ctx.evidence)]
        if invalid:
            raise PermanentError(
                f"hallucinated citation(s) E{invalid}: bundle has "
                f"{len(ctx.evidence)} items")

        # Insufficient means the model *led* with the refusal marker or made
        # no evidence references at all. A hedging caveat buried inside an
        # otherwise cited diagnosis (common with small live models) is not a
        # refusal.
        leads_with_refusal = response.text.lower().lstrip().startswith(
            "insufficient evidence")
        if leads_with_refusal or not cited_idx:
            return AgentResult(
                agent=self.name,
                output={"status": "insufficient-evidence",
                        "hypothesis": response.text},
                confidence=score_confidence(0.3, 0.3),
                citations=[e.citation for e in ctx.evidence[:1]],
                model_id=response.model_id, tokens=response.tokens)

        suspect = self._suspect_commit(ctx)
        grounded = suspect is not None and suspect in response.text
        failure_class = self._classify(ctx, response.text)
        # Coverage counts causal evidence only: retrieved runbook/postmortem
        # guidance is advisory context and must not move diagnosis
        # confidence (docs/17 M7 rule 3).
        causal = [e for e in ctx.evidence
                  if e.kind not in ("runbook", "postmortem")]
        coverage = min(1.0, len(cited_idx) / max(1, len(causal)))
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
    def _classify(ctx, text: str) -> str:
        """Failure class: taken from the model's text when it names a known
        class; otherwise inferred deterministically from evidence markers —
        deterministic validation wrapped around a probabilistic narrative,
        the platform's standard pattern (docs/03 §2)."""
        if "deploy-regression/memory" in text:
            return "deploy-regression/memory"
        has_oom = any("OOMKilled" in e.summary
                      for e in ctx.evidence_of_kind("k8s-event"))
        pool_commit = any("pool" in e.summary.lower()
                          for e in ctx.evidence_of_kind("commit"))
        if has_oom and pool_commit and ctx.evidence_of_kind("deploy"):
            return "deploy-regression/memory"
        return "unclassified"

    @staticmethod
    def _suspect_commit(ctx) -> str | None:
        """Deterministic grounding: the suspect commit must exist in the
        evidence bundle (commit-kind citation refs), not just in model text."""
        for evidence in ctx.evidence_of_kind("commit"):
            sha = evidence.citation.ref.rsplit("/", 1)[-1]
            if re.fullmatch(r"[0-9a-f]{7,40}", sha):
                return sha
        return None
