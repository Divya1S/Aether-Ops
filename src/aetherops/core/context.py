"""Workflow context: the working memory of one workflow run.

In production this state lives in Temporal workflow state + Redis; here it is
an in-process object handed to every DAG node. Agents read evidence from it
and write AgentResults into it — the only channel through which agents
"communicate" (mediated composition, docs/01-architecture.md §4).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# Data-classification ladder (docs/05 §5). Higher = more sensitive.
_CLASS_ORDER = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2,
                "RESTRICTED": 3}

from aetherops.core.types import AgentResult, Evidence, IncidentEvent


@dataclass
class WorkflowContext:
    incident: IncidentEvent | None
    connectors: object      # connectors.base.ConnectorRegistry
    gateway: object         # gateway.model_gateway.ModelGateway
    audit: object           # security.audit.AuditLog
    memory: object          # memory.store.EpisodicMemory
    policy: object = None   # policy.engine.PolicyEngine
    change: object = None   # core.types.ChangeEvent (change-risk workflow)
    graph: object = None    # graph.service_graph.ServiceGraph
    rag: object = None      # rag.retriever.RagStore
    evidence: list[Evidence] = field(default_factory=list)
    results: dict[str, AgentResult] = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    def add_evidence(self, item: Evidence) -> Evidence:
        self.evidence.append(item)
        return item

    def evidence_of_kind(self, *kinds: str) -> list[Evidence]:
        return [e for e in self.evidence if e.kind in kinds]

    def record(self, result: AgentResult) -> AgentResult:
        self.results[result.agent] = result
        return result

    def evidence_digest(self) -> str:
        """Numbered digest (E1..En) given to reasoning-tier agents so their
        outputs can reference evidence by ID — the claim–evidence link that
        citation validation checks (docs/06-retrieval-and-memory.md).

        Access control runs BEFORE the model (docs/05 §5, PROMPT-10): two
        withholding rules, both preserving [En] numbering because citation
        validation depends on it. QUARANTINED items (suspected injection —
        hostile content) are withheld; items whose classification exceeds
        the model clearance (AETHEROPS_MODEL_CLEARANCE, default INTERNAL —
        sensitive content) are withheld. Humans retain full visibility via
        the audit ledger and postmortems; only model prompts are gated."""
        clearance = _CLASS_ORDER.get(
            os.environ.get("AETHEROPS_MODEL_CLEARANCE", "INTERNAL"), 1)
        lines = []
        for i, e in enumerate(self.evidence, start=1):
            if e.classification == "QUARANTINED":
                lines.append(
                    f"[E{i}] (QUARANTINED {e.kind}, {e.citation.source}) "
                    "content withheld — suspected prompt injection; treat "
                    "this source as unavailable")
                continue
            if _CLASS_ORDER.get(e.classification, 1) > clearance:
                lines.append(
                    f"[E{i}] ({e.kind}, {e.citation.source}) content "
                    f"withheld — classification {e.classification} exceeds "
                    "model clearance; treat this source as unavailable")
                continue
            lines.append(
                f"[E{i}] ({e.kind}, {e.citation.source}) {e.summary} "
                f"| excerpt: {e.citation.excerpt!r} | ref: {e.citation.ref}"
            )
        return "\n".join(lines)
