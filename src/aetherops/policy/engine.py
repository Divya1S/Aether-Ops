"""Deterministic policy engine (docs/05-security.md).

Production: OPA sidecars evaluating versioned Rego bundles. The reference
implementation encodes the same first-match rule shape so the workflow and
tests exercise real governance semantics. The OPA input document this mirrors:

    {
      "action": "rollback_deployment",
      "risk": "HIGH",
      "environment": "prod",
      "confidence": 0.87,
      "compensable": true
    }

Decisions are data, produced before any write executes (plan admission), and
re-produced when a gate resumes after the staleness window — approvals age out
(docs/03-orchestration.md §7).
"""
from __future__ import annotations

from dataclasses import dataclass

from aetherops.core.types import RiskLevel


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    approval_tier: int          # 0 = auto, 1..3 = human tiers
    rule_id: str
    reason: str


class PolicyEngine:
    AUTO_CONFIDENCE = 0.8       # below this, writes always need a human

    def evaluate(self, *, action: str, risk: RiskLevel, environment: str = "prod",
                 confidence: float = 1.0, compensable: bool = True) -> PolicyDecision:
        if risk == RiskLevel.CRITICAL and environment == "prod":
            return PolicyDecision(
                False, False, 0, "R1-critical-prod",
                f"{action}: CRITICAL writes in prod are break-glass only")

        if risk >= RiskLevel.HIGH and not compensable:
            return PolicyDecision(
                True, True, 3, "R2-noncompensable",
                f"{action}: non-compensable HIGH-risk action requires tier-3 quorum")

        if risk >= RiskLevel.HIGH and environment == "prod":
            return PolicyDecision(
                True, True, 2, "R3-high-prod",
                f"{action}: HIGH-risk write in prod requires tier-2 approval")

        if risk >= RiskLevel.MEDIUM and (environment == "prod"
                                         or confidence < self.AUTO_CONFIDENCE):
            return PolicyDecision(
                True, True, 1, "R4-medium-gated",
                f"{action}: MEDIUM-risk write requires tier-1 approval "
                f"(env={environment}, confidence={confidence:.2f})")

        if risk >= RiskLevel.MEDIUM:
            return PolicyDecision(
                True, False, 0, "R5-medium-auto",
                f"{action}: MEDIUM-risk write auto-approved outside prod "
                f"with confidence {confidence:.2f}")

        return PolicyDecision(
            True, False, 0, "R6-read-low",
            f"{action}: {risk.name} operations are always permitted")

    # API role grants (docs/05 §2 RBAC, PROMPT-10 layer 3) — a policy
    # table, not scattered ifs. Roles: viewer (read-only), operator
    # (create/run), approver (decide gates), admin (all — the default
    # token's role, preserving back-compatibility).
    API_ROLE_GRANTS = {
        "read": {"viewer", "operator", "approver", "admin"},
        "create": {"operator", "admin"},
        "approve": {"approver", "admin"},
    }

    @classmethod
    def role_allows(cls, role: str | None, action: str) -> bool:
        return role in cls.API_ROLE_GRANTS.get(action, set())

    def evaluate_change(self, *, band: str, environment: str = "prod",
                        freeze: bool = False) -> dict:
        """Change-admission rules (docs/00 pillar 2). Returned as a dict so
        the verdict lands verbatim in the workflow checkpoint. Freeze-block
        is a first-class outcome: the change is refused, not errored."""
        if freeze and band != "LOW":
            return {"allowed": False, "requires_approval": False,
                    "approval_tier": 0, "canary_required": False,
                    "rule_id": "C1-freeze",
                    "reason": f"{band} change blocked by freeze window — "
                              "escalate to the release manager"}
        if band == "HIGH":
            return {"allowed": True, "requires_approval": True,
                    "approval_tier": 2, "canary_required": True,
                    "rule_id": "C2-high",
                    "reason": "HIGH-risk change requires tier-2 approval and "
                              "canary rollout"}
        if band == "MEDIUM":
            return {"allowed": True, "requires_approval": False,
                    "approval_tier": 0, "canary_required": True,
                    "rule_id": "C3-medium",
                    "reason": "MEDIUM-risk change auto-allowed with mandatory "
                              "canary rollout"}
        return {"allowed": True, "requires_approval": False,
                "approval_tier": 0, "canary_required": False,
                "rule_id": "C4-low",
                "reason": "LOW-risk change allowed"}

    def evaluate_plan(self, steps: list[dict], *, environment: str,
                      confidence: float) -> dict:
        """Plan admission: evaluate every step; the plan's approval tier is
        the max of its steps'. A single denied step denies the plan."""
        decisions = []
        for step in steps:
            decision = self.evaluate(
                action=step["action"],
                risk=RiskLevel[step["risk"]],
                environment=environment,
                confidence=confidence,
                compensable=step.get("compensable", True),
            )
            decisions.append({"action": step["action"], "rule_id": decision.rule_id,
                              "allowed": decision.allowed,
                              "requires_approval": decision.requires_approval,
                              "approval_tier": decision.approval_tier,
                              "reason": decision.reason})
        return {
            "decisions": decisions,
            "allowed": all(d["allowed"] for d in decisions),
            "requires_approval": any(d["requires_approval"] for d in decisions),
            "approval_tier": max((d["approval_tier"] for d in decisions), default=0),
        }
