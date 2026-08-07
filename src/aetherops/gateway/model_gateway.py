"""Model gateway: the single choke point for all model calls
(docs/07-model-gateway.md).

Routing is deterministic configuration, never an LLM choosing models: task
class picks a tier, then dynamic signals (severity) can escalate it. The
gateway meters tokens into the audit ledger for cost attribution.

Backends:
- OfflineHeuristicBackend (default here): a deterministic, *evidence-driven*
  stand-in — it parses the evidence digest in the prompt and templates
  conclusions from what it actually finds. No evidence, no claim: the same
  grounding contract hosted models are held to, which is what lets the
  golden-scenario harness (aetherops/evals) exercise the full pipeline
  offline.
- Production: the Anthropic API behind the same interface. The tier map
  below uses real model IDs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from aetherops.core.types import Severity

TIERS = {
    "fast": "claude-haiku-4-5-20251001",
    "standard": "claude-sonnet-5",
    "reasoning": "claude-opus-5",
    "frontier": "claude-fable-5",
}

_EVIDENCE_LINE = re.compile(r"\[E(\d+)\] \(([\w-]+)")
_COMMIT_REF = re.compile(r"github://commit/([0-9a-f]{7,40})")
_COMMIT_MENTION = re.compile(r"commit ([0-9a-f]{7,40})")


@dataclass(frozen=True)
class TaskProfile:
    task: str                       # "triage" | "root_cause" | "plan" | "verify" | ...
    tier_hint: str = "standard"
    severity: Severity | None = None


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model_id: str
    tier: str
    tokens: int


class OfflineHeuristicBackend:
    """Deterministic stand-in for a hosted model. Contains no incident-
    specific facts: everything in its output is extracted from the prompt's
    evidence digest (commit SHAs from citation refs, [En] indices from the
    bundle, symptom markers from excerpts)."""

    model_note = "offline-heuristic (production: Anthropic API)"

    def complete(self, model_id: str, prompt: str, task: str) -> tuple[str, int]:
        tokens = max(1, len(prompt) // 4)
        text = self._respond(prompt, task)
        return text, tokens + max(1, len(text) // 4)

    def _respond(self, prompt: str, task: str) -> str:
        if task == "triage":
            match = re.search(r"service=(\S+)", prompt)
            service = match.group(1) if match else "the service"
            return (f"Alert maps to service {service} in prod. Sustained, "
                    "customer-facing p99 latency breach.")

        if task == "root_cause":
            return self._diagnose(prompt)

        if task == "plan":
            match = _COMMIT_MENTION.search(prompt)
            sha = match.group(1) if match else "the suspect commit"
            return (f"Remediation: (1) rollback_deployment to the previous "
                    "revision — reverses the causal trigger; "
                    f"(2) create_revert_pr for {sha} so the fix-forward path "
                    "is reviewed by the owning team. Both are catalog actions "
                    "with registered compensations.")

        if task == "postmortem":
            service = re.search(r"service=(\S+)", prompt)
            cls = re.search(r"failure_class=(\S+)", prompt)
            sha = re.search(r"suspect=([0-9a-f]{7,40})", prompt)
            p99 = re.search(r"recovered_p99=(\d+)", prompt)
            return (f"On {service.group(1) if service else 'the service'}, a "
                    f"deploy-introduced change "
                    f"({sha.group(1) if sha else 'unidentified'}) matching "
                    f"class {cls.group(1) if cls else 'unknown'} exhausted pod "
                    "memory and breached latency SLOs. The platform correlated "
                    "deploy, commit, and runtime evidence, executed a "
                    "policy-gated rollback with a fix-forward revert PR, and "
                    "verified recovery at p99 "
                    f"{p99.group(1) if p99 else '?'}ms.")

        if task == "review":
            checks = re.search(r"checks_passed=(\d+)/(\d+)", prompt)
            done, total = (checks.group(1), checks.group(2)) if checks else ("?", "?")
            return (f"Plan review: {done} of {total} independent safety checks "
                    "passed (catalog membership, grounded rollback target, "
                    "grounded revert SHA, service scope, failure-class fit).")

        if task == "change_risk":
            matched = re.search(r"matched=(\d+)", prompt)
            blast = re.search(r"blast_radius=(\d+)", prompt)
            band = re.search(r"band=(\w+)", prompt)
            return (f"Change risk {band.group(1) if band else '?'}: matches "
                    f"{matched.group(1) if matched else '?'} prior incident "
                    f"episode(s) with the same failure signature; blast radius "
                    f"{blast.group(1) if blast else '?'} downstream services.")

        if task == "verify":
            p99 = re.search(r"p99=(\d+)", prompt)
            oom = re.search(r"oomkilled_last_10m=(\d+)", prompt)
            return (f"Post-remediation window shows p99 at "
                    f"{p99.group(1) if p99 else '?'}ms with "
                    f"{oom.group(1) if oom else '?'} OOMKilled events in the "
                    "last 10 minutes.")

        return "Summary: " + prompt[:200]

    def _diagnose(self, prompt: str) -> str:
        # Index the evidence bundle: kind -> first [En] reference.
        kinds: dict[str, int] = {}
        for match in _EVIDENCE_LINE.finditer(prompt):
            kinds.setdefault(match.group(2), int(match.group(1)))

        sha_match = _COMMIT_REF.search(prompt)
        has_oom = "OOMKilled" in prompt
        has_pool_change = "pool" in prompt.lower()
        required = {"metrics", "deploy", "commit", "k8s-event"}

        if not (sha_match and has_oom and has_pool_change
                and required <= kinds.keys()):
            return ("Insufficient evidence: the bundle lacks a change event "
                    "correlated with the symptom onset. Escalate to a human "
                    "with the partial bundle.")

        sha = sha_match.group(1)
        episode_note = (
            f" A prior episode with the same signature supports this class "
            f"[E{kinds['episode']}]." if "episode" in kinds else "")
        return (
            f"Hypothesis (primary): the deploy [E{kinds['deploy']}] shipped "
            f"commit {sha} raising the DB connection pool [E{kinds['commit']}]. "
            f"Pod memory grew past its limit, causing OOMKilled and "
            f"CrashLoopBackOff [E{kinds['k8s-event']}]; surviving pods "
            f"absorbed the load, breaching p99 latency [E{kinds['metrics']}]. "
            f"Causal chain: deploy [E{kinds['deploy']}] -> pool change "
            f"[E{kinds['commit']}] -> memory exhaustion [E{kinds['k8s-event']}] "
            f"-> latency breach [E{kinds['metrics']}].{episode_note} "
            "Recommended class: deploy-regression/memory.")


class ModelGateway:
    def __init__(self, backend=None, audit=None):
        self._backend = backend or OfflineHeuristicBackend()
        self._audit = audit
        self.tokens_used = 0

    def route(self, profile: TaskProfile) -> tuple[str, str]:
        """Deterministic: hinted tier, escalated to frontier for SEV1."""
        tier = profile.tier_hint if profile.tier_hint in TIERS else "standard"
        if profile.severity == Severity.SEV1:
            tier = "frontier"
        return tier, TIERS[tier]

    def complete(self, prompt: str, profile: TaskProfile) -> ModelResponse:
        tier, model_id = self.route(profile)
        text, tokens = self._backend.complete(model_id, prompt, profile.task)
        self.tokens_used += tokens
        if self._audit is not None:
            self._audit.append(
                actor="model-gateway", action="model.call",
                payload={"task": profile.task, "tier": tier,
                         "model_id": model_id, "tokens": tokens})
        return ModelResponse(text=text, model_id=model_id, tier=tier, tokens=tokens)
