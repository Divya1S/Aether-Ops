"""Core typed vocabulary shared by every plane.

These types are the reference-implementation mirror of the canonical
terminology table in docs/01-architecture.md: Evidence, Citation, risk
classes, the incident event, and the agent result contract.
"""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Severity(str, enum.Enum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class RiskLevel(enum.IntEnum):
    """Risk class of a tool/action. Ordering is meaningful: policy rules
    compare risk levels, so this is an IntEnum."""

    READ = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class WorkflowStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"          # durable gate: awaiting human approval
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"  # failed, and registered undo handlers ran


@dataclass(frozen=True)
class Citation:
    """Pointer to an artifact in a system of record. Evidence without a
    citation is not evidence; agents without citations may not make claims."""

    source: str        # connector system, e.g. "datadog", "github"
    ref: str           # stable URI or identifier in the source system
    excerpt: str       # the literal excerpt relied upon
    retrieved_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str          # "alert" | "metrics" | "deploy" | "commit" | "k8s-event" | "episode"
    summary: str
    citation: Citation
    classification: str = "INTERNAL"   # PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED


@dataclass
class IncidentEvent:
    """Normalized signal that starts an incident workflow (Sense plane output)."""

    id: str
    title: str
    service: str
    severity: Severity
    description: str
    environment: str = "prod"
    labels: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class ChangeEvent:
    """A proposed change (deploy/PR webhook) entering the Change Intelligence
    workflow (docs/00-executive-summary.md §4, pillar 2)."""

    id: str
    service: str
    sha: str
    title: str
    diff: str
    environment: str = "prod"
    labels: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    """Contract every agent returns. `output` must be JSON-serializable and
    schema-shaped per agent; `confidence` is calibrated (agents/base.py);
    `citations` back every claim made in `output`."""

    agent: str
    output: dict
    confidence: float
    citations: list[Citation]
    model_id: str = "n/a"
    tokens: int = 0

    def to_checkpoint(self) -> dict:
        """Serializable projection stored in the DAG checkpoint."""
        return {
            "agent": self.agent,
            "output": self.output,
            "confidence": round(self.confidence, 3),
            "citations": len(self.citations),
            "model_id": self.model_id,
            "tokens": self.tokens,
        }
