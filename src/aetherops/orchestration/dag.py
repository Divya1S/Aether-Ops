"""Deterministic DAG executor — the reference mirror of the Temporal-based
Control plane (docs/03-orchestration.md).

Semantics preserved 1:1 with the production design:
- topological execution with build-time validation (unique names, known deps,
  acyclic);
- retry taxonomy: TransientError retried with backoff, PermanentError and
  unexpected exceptions escalate immediately;
- durable checkpoints: node outputs recorded after each success; resume skips
  completed nodes;
- approval gates: a gate node with no recorded decision PAUSES the run
  (production: durable Temporal signal + timer); a denial fails the run
  without compensation (nothing gated has executed yet — pre-gate nodes are
  reads by construction);
- saga compensation: on execution failure, registered undo handlers run in
  reverse completion order; a failed compensation is audited and skipped
  (production: pages a human), never retried in a loop.

LLMs appear nowhere in this file. That is the point.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from aetherops.agents.base import PermanentError, RetryPolicy, TransientError
from aetherops.core.types import RiskLevel, WorkflowStatus


@dataclass(frozen=True)
class GateSpec:
    """Approval gate attached to a node. `needed` inspects the checkpoint so
    gating can depend on upstream results (e.g., policy_check output)."""

    risk: RiskLevel
    reason: str
    needed: Callable[[dict], bool] = lambda checkpoint: True


@dataclass
class Node:
    name: str
    run: Optional[Callable] = None          # fn(ctx) -> dict | None
    deps: tuple = ()
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    compensate: Optional[Callable] = None   # fn(ctx, node_output) -> None
    gate: Optional[GateSpec] = None


@dataclass
class DagRun:
    status: WorkflowStatus
    checkpoint: dict                        # node name -> output
    completed: list                         # completion order (this run + resumed)
    pending_gate: Optional[str] = None
    error: Optional[str] = None
    compensated: list = field(default_factory=list)


class DagExecutor:
    def __init__(self, nodes: list[Node], audit=None,
                 sleeper: Callable[[float], None] = time.sleep):
        self._by_name = {}
        for node in nodes:
            if node.name in self._by_name:
                raise ValueError(f"duplicate node name: {node.name}")
            self._by_name[node.name] = node
        self._order = self._topo_sort(nodes)
        self._audit = audit
        self._sleep = sleeper

    def _topo_sort(self, nodes: list[Node]) -> list[Node]:
        for node in nodes:
            for dep in node.deps:
                if dep not in self._by_name:
                    raise ValueError(f"node {node.name!r} depends on unknown {dep!r}")
        ordered, seen, visiting = [], set(), set()

        def visit(node: Node):
            if node.name in seen:
                return
            if node.name in visiting:
                raise ValueError(f"cycle involving node {node.name!r}")
            visiting.add(node.name)
            for dep in node.deps:
                visit(self._by_name[dep])
            visiting.discard(node.name)
            seen.add(node.name)
            ordered.append(node)

        for node in nodes:
            visit(node)
        return ordered

    def execute(self, ctx, approvals: dict | None = None,
                checkpoint: dict | None = None) -> DagRun:
        approvals = approvals or {}
        cp = dict(checkpoint or {})
        completed = [n.name for n in self._order if n.name in cp]

        for node in self._order:
            if node.name in cp:
                continue

            if node.gate is not None and node.gate.needed(cp):
                decision = approvals.get(node.name)
                if decision is None:
                    self._log("gate.paused", node.name,
                              {"risk": node.gate.risk.name, "reason": node.gate.reason})
                    return DagRun(WorkflowStatus.PAUSED, cp, completed,
                                  pending_gate=node.name)
                if decision is False:
                    self._log("gate.denied", node.name, {})
                    return DagRun(WorkflowStatus.FAILED, cp, completed,
                                  error=f"approval denied at gate {node.name!r}")
                self._log("gate.approved", node.name, {"risk": node.gate.risk.name})

            if node.run is None:            # pure gate node
                cp[node.name] = {"gate": "passed"}
                completed.append(node.name)
                continue

            try:
                output = self._run_with_retry(node, ctx)
            except Exception as exc:
                self._log("node.failed", node.name, {"error": str(exc)})
                compensated = self._compensate(ctx, cp, completed)
                status = (WorkflowStatus.COMPENSATED if compensated
                          else WorkflowStatus.FAILED)
                return DagRun(status, cp, completed,
                              error=f"{node.name}: {exc}", compensated=compensated)

            cp[node.name] = output
            completed.append(node.name)

        return DagRun(WorkflowStatus.SUCCEEDED, cp, completed)

    def _run_with_retry(self, node: Node, ctx) -> dict:
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                output = node.run(ctx) or {}
                self._log("node.succeeded", node.name,
                          {"attempt": attempt,
                           "duration_ms": round((time.monotonic() - started)
                                                * 1000, 1)})
                return output
            except TransientError as exc:
                if attempt >= node.retry.max_attempts:
                    raise PermanentError(
                        f"retries exhausted after {attempt} attempts: {exc}"
                    ) from exc
                self._log("node.retry", node.name,
                          {"attempt": attempt, "error": str(exc)})
                self._sleep(node.retry.delay(attempt))

    def _compensate(self, ctx, cp: dict, completed: list) -> list:
        compensated = []
        for name in reversed(completed):
            node = self._by_name[name]
            if node.compensate is None:
                continue
            try:
                node.compensate(ctx, cp.get(name, {}))
                compensated.append(name)
                self._log("node.compensated", name, {})
            except Exception as exc:   # never let one undo block the rest
                self._log("compensation.failed", name, {"error": str(exc)})
        return compensated

    def _log(self, action: str, node: str, payload: dict) -> None:
        if self._audit is not None:
            self._audit.append(actor="orchestrator", action=action,
                               payload={"node": node, **payload})
