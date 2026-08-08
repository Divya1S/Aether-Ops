"""Model gateway: the single choke point for all model calls
(docs/07-model-gateway.md).

Routing is deterministic configuration, never an LLM choosing models: task
class picks a tier, then dynamic signals (severity) can escalate it. Serving
goes through an ordered **backend chain** (docs/17 Milestone 6): each call is
tried against the configured backends in order — a backend that raises is
skipped with an audited `backend.fallback` event — and the offline heuristic
is the guaranteed last resort, so the platform cannot lose its brain.

Every call is metered: latency, tokens in/out, and `est_cost_usd` — the
estimated cost **at the docs/13 production-tier planning prices**, i.e. what
the call would cost served by the hosted tier model. Local (Ollama) and
offline serving costs $0 in reality; the estimate exists so the cost model
in docs/13 becomes a measured, comparable number.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from aetherops.core.types import Severity
from aetherops.gateway.backends import (BackendResult, OfflineHeuristicBackend,
                                        OllamaBackend, build_backend_chain)

__all__ = ["TIERS", "TaskProfile", "ModelResponse", "ModelGateway",
           "OfflineHeuristicBackend", "OllamaBackend", "BackendResult"]

TIERS = {
    "fast": "claude-haiku-4-5-20251001",
    "standard": "claude-sonnet-5",
    "reasoning": "claude-opus-5",
    "frontier": "claude-fable-5",
}

# Planning-assumption prices per million tokens (input, output) — the same
# figures docs/13 uses. Labeled assumptions, not quotes.
PRICES_PER_MTOK = {
    "fast": (1.0, 5.0),
    "standard": (3.0, 15.0),
    "reasoning": (5.0, 25.0),
    "frontier": (5.0, 25.0),
}


@dataclass(frozen=True)
class TaskProfile:
    task: str                       # "triage" | "root_cause" | "plan" | ...
    tier_hint: str = "standard"
    severity: Severity | None = None


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model_id: str                   # the routed production-tier model
    tier: str
    tokens: int                     # tokens_in + tokens_out
    backend: str = "offline"        # which chain link served the call
    served_model: str = ""          # what actually generated the text
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    est_cost_usd: float = 0.0       # at production-tier planning prices


class ModelGateway:
    def __init__(self, backend=None, audit=None, backends: list | None = None):
        if backend is not None:          # single-backend override (tests)
            self._chain = [backend]
        elif backends is not None:
            self._chain = backends
        else:
            self._chain = build_backend_chain()
        self._audit = audit
        self.tokens_used = 0
        self.est_cost_usd = 0.0

    def route(self, profile: TaskProfile) -> tuple[str, str]:
        """Deterministic: hinted tier, escalated to frontier for SEV1."""
        tier = profile.tier_hint if profile.tier_hint in TIERS else "standard"
        if profile.severity == Severity.SEV1:
            tier = "frontier"
        return tier, TIERS[tier]

    def complete(self, prompt: str, profile: TaskProfile) -> ModelResponse:
        tier, model_id = self.route(profile)
        last_error: Exception | None = None

        for index, backend in enumerate(self._chain):
            started = time.monotonic()
            try:
                result: BackendResult = backend.complete(model_id, prompt,
                                                         profile.task)
            except Exception as exc:
                last_error = exc
                next_name = (self._chain[index + 1].name
                             if index + 1 < len(self._chain) else None)
                if self._audit is not None:
                    self._audit.append(
                        actor="model-gateway", action="backend.fallback",
                        payload={"task": profile.task,
                                 "failed_backend": getattr(backend, "name",
                                                           type(backend).__name__),
                                 "next_backend": next_name,
                                 "error": str(exc)[:200]})
                continue

            latency_ms = round((time.monotonic() - started) * 1000, 1)
            price_in, price_out = PRICES_PER_MTOK[tier]
            est_cost = round(result.tokens_in / 1e6 * price_in
                             + result.tokens_out / 1e6 * price_out, 6)
            total = result.tokens_in + result.tokens_out
            self.tokens_used += total
            self.est_cost_usd = round(self.est_cost_usd + est_cost, 6)

            if self._audit is not None:
                self._audit.append(
                    actor="model-gateway", action="model.call",
                    payload={"task": profile.task, "tier": tier,
                             "model_id": model_id,
                             "backend": getattr(backend, "name",
                                                type(backend).__name__),
                             "served_model": result.served_model,
                             "tokens_in": result.tokens_in,
                             "tokens_out": result.tokens_out,
                             "latency_ms": latency_ms,
                             "est_cost_usd": est_cost})

            return ModelResponse(
                text=result.text, model_id=model_id, tier=tier,
                tokens=total,
                backend=getattr(backend, "name", type(backend).__name__),
                served_model=result.served_model,
                tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                latency_ms=latency_ms, est_cost_usd=est_cost)

        raise RuntimeError(
            f"all model backends failed (last error: {last_error})")
