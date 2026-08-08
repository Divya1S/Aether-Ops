"""Security agent: prompt-injection screening of retrieved evidence
(docs/02-agents.md, docs/05-security.md §6).

Retrieved content is data, never instructions. Deterministic patterns flag
instruction-like text (production layers a small in-cell model on top —
never a hosted LLM, docs/05 §5); flagged items are QUARANTINED: they stay in
the bundle for auditability, but every model-facing digest withholds their
content. Quarantine couples into governance with no extra wiring: withheld
evidence cannot be cited, so coverage drops, so confidence drops, so the
approval path tightens.
"""
from __future__ import annotations

import dataclasses
import re

from aetherops.agents.base import Agent, score_confidence
from aetherops.core.types import AgentResult

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override-instructions", re.compile(
        r"(?i)\b(ignore|disregard|forget)\b.{0,40}\binstructions?\b")),
    ("role-hijack", re.compile(r"(?i)\b(system prompt|new instructions?:)")),
    ("action-solicitation", re.compile(
        r"(?i)\b(immediately|you must)\b.{0,60}\b(call|execute|run|invoke)\b")),
    ("approval-solicitation", re.compile(
        r"(?i)\b(mark|consider|treat)\b.{0,30}\bapproved\b")),
    ("broad-blast", re.compile(r"(?i)\bon all (services|clusters|environments)\b")),
]


def screen_text(text: str) -> list[str]:
    return [label for label, pattern in INJECTION_PATTERNS
            if pattern.search(text)]


class SecurityAgent(Agent):
    name = "security"
    tier = "fast"
    output_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["screened", "quarantined"],
        "properties": {
            "screened": {"type": "integer"},
            "quarantined": {"type": "array", "items": {
                "type": "object",
                "required": ["evidence_id", "kind", "source", "patterns"],
                "properties": {
                    "evidence_id": {"type": "string"},
                    "kind": {"type": "string"},
                    "source": {"type": "string"},
                    "patterns": {"type": "array",
                                 "items": {"type": "string"}},
                }}},
        }}

    def run(self, ctx) -> AgentResult:
        quarantined: list[dict] = []
        for index, evidence in enumerate(ctx.evidence):
            hits = screen_text(f"{evidence.summary} {evidence.citation.excerpt}")
            if hits:
                ctx.evidence[index] = dataclasses.replace(
                    evidence, classification="QUARANTINED")
                quarantined.append({"evidence_id": evidence.id,
                                    "kind": evidence.kind,
                                    "source": evidence.citation.source,
                                    "patterns": hits})

        return AgentResult(
            agent=self.name,
            output={"screened": len(ctx.evidence),
                    "quarantined": quarantined},
            confidence=score_confidence(0.95, 1.0),
            citations=[e.citation for e in ctx.evidence],
            model_id="n/a",   # deterministic patterns (+ local model in prod)
            tokens=0)
