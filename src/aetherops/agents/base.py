"""Agent contract: run(ctx) -> AgentResult, with retry taxonomy and
calibrated confidence scoring (docs/02-agents.md, docs/03-orchestration.md §4).

Retries live in the DAG executor, not inside agents — an agent that retried
internally would corrupt cost accounting and hide failure signals from
calibration. Agents raise TransientError for retryable conditions and
PermanentError for conditions that must escalate.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

from aetherops.core.types import AgentResult


class TransientError(Exception):
    """Retryable: connector timeout, rate limit, model 5xx."""


class PermanentError(Exception):
    """Non-retryable: policy denial, budget exhaustion, verification failure.
    The executor escalates instead of retrying."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.05
    backoff: float = 2.0

    def delay(self, attempt: int) -> float:
        """Delay before retry `attempt` (1-based)."""
        return self.base_delay_s * (self.backoff ** (attempt - 1))


def score_confidence(
    self_estimate: float,
    evidence_coverage: float,
    calibration: float = 1.0,
) -> float:
    """Calibrated confidence = model self-estimate x evidence coverage x
    historical calibration weight for this (agent, failure-class).

    Evidence coverage is the fraction of planned evidence sources that
    actually contributed — a connector outage lowers coverage, which lowers
    confidence, which tightens the approval path automatically
    (docs/11-failure-handling.md). Calibration weights are learned by the
    Evaluation service; the reference implementation uses 1.0.
    """
    score = self_estimate * evidence_coverage * calibration
    return max(0.0, min(1.0, score))


class Agent(abc.ABC):
    """Base class for all agents. Subclasses set `name` and `tier` and
    implement `run`. Agents never call each other — composition happens in
    the workflow (docs/01-architecture.md §4)."""

    name: str = "agent"
    tier: str = "standard"   # model gateway tier hint: fast|standard|reasoning|frontier
    # JSON-Schema-subset contract for `AgentResult.output` (core/schema.py).
    # Enforced by the workflow's agent-node wrapper: one semantic retry on
    # violation, then escalation (docs/02 §2, §4).
    output_schema: dict | None = None

    @abc.abstractmethod
    def run(self, ctx) -> AgentResult:  # ctx: aetherops.core.context.WorkflowContext
        raise NotImplementedError
