"""Change Intelligence workflow (docs/00-executive-summary.md §4, pillar 2):

  assess -> score -> policy_check -> [approval gate] -> record

Scoring is deterministic with fixed weights and published band thresholds —
the model writes only the rationale. This is the second workflow hosted by
the same orchestration core: same executor, same gates, same policy engine,
same audit — which is the point (docs/03-orchestration.md).
"""
from __future__ import annotations

from aetherops.agents.base import PermanentError
from aetherops.agents.change_intel import ChangeIntelligenceAgent
from aetherops.core.context import WorkflowContext
from aetherops.core.types import RiskLevel
from aetherops.gateway.model_gateway import TaskProfile
from aetherops.orchestration.dag import DagExecutor, DagRun, GateSpec, Node
from aetherops.workflows.incident_remediation import _agent_node

# Fixed scoring weights. HIGH >= 70, MEDIUM >= 40, else LOW.
SIGNATURE_WEIGHT = 20      # per matched failure-signature episode, cap 40
BLAST_WEIGHT = 10          # per transitive dependent, cap 30
PEAK_WINDOW_POINTS = 15
HISTORY_WEIGHT = 5         # per prior incident on this service, cap 15


def score_change(matched_episodes: int, blast_radius: int,
                 peak_window: bool, service_incident_count: int) -> dict:
    components = {
        "failure_signature": min(40, SIGNATURE_WEIGHT * matched_episodes),
        "blast_radius": min(30, BLAST_WEIGHT * blast_radius),
        "deploy_window": PEAK_WINDOW_POINTS if peak_window else 0,
        "service_history": min(15, HISTORY_WEIGHT * service_incident_count),
    }
    total = sum(components.values())
    band = "HIGH" if total >= 70 else "MEDIUM" if total >= 40 else "LOW"
    return {"score": total, "band": band, "components": components}


def _score(ctx):
    intel = ctx.results["change_intel"].output
    verdict = score_change(
        matched_episodes=len(intel["matched_episodes"]),
        blast_radius=intel["blast_radius"],
        peak_window=bool(ctx.change.labels.get("peak_window")),
        service_incident_count=intel["service_incident_count"])

    rationale = ctx.gateway.complete(
        f"[change_risk] service={ctx.change.service} "
        f"matched={len(intel['matched_episodes'])} "
        f"blast_radius={intel['blast_radius']} "
        f"band={verdict['band']} score={verdict['score']}",
        TaskProfile(task="change_risk", tier_hint="fast"))

    verdict = {**verdict, "rationale": rationale.text}
    ctx.params["score_verdict"] = verdict     # for downstream deterministic nodes
    return verdict


def _policy_check(ctx):
    verdict = ctx.params["score_verdict"]
    decision = ctx.policy.evaluate_change(
        band=verdict["band"],
        environment=ctx.change.environment,
        freeze=bool(ctx.change.labels.get("freeze")))
    if not decision["allowed"]:
        raise PermanentError(decision["reason"])
    ctx.params["policy_decision"] = decision
    return decision


def _record(ctx):
    verdict = ctx.params["score_verdict"]
    episode_id = ctx.memory.add({
        "type": "change-decision",
        "service": ctx.change.service,
        "sha": ctx.change.sha,
        "summary": f"change '{ctx.change.title}' scored "
                   f"{verdict['score']} ({verdict['band']})",
        "band": verdict["band"],
    })
    return {"band": verdict["band"], "score": verdict["score"],
            "canary_required":
                ctx.params.get("policy_decision", {}).get("canary_required"),
            "episode_id": episode_id}


def build_change_workflow() -> list[Node]:
    return [
        Node("assess", _agent_node(ChangeIntelligenceAgent())),
        Node("score", _score, deps=("assess",)),
        Node("policy_check", _policy_check, deps=("score",)),
        Node("approval_gate", run=None, deps=("policy_check",),
             gate=GateSpec(
                 risk=RiskLevel.MEDIUM,
                 reason="ship a risk-scored change",
                 needed=lambda cp: cp.get("policy_check", {})
                                     .get("requires_approval", True))),
        Node("record", _record, deps=("approval_gate",)),
    ]


def run_change_risk(change, *, gateway, audit, memory, policy, graph,
                    approvals: dict | None = None,
                    checkpoint: dict | None = None,
                    ctx: WorkflowContext | None = None
                    ) -> tuple[DagRun, WorkflowContext]:
    """Executes (or resumes) the change-risk workflow. Same pause/resume
    contract as the incident workflow."""
    if ctx is None:
        ctx = WorkflowContext(incident=None, connectors=None, gateway=gateway,
                              audit=audit, memory=memory, policy=policy,
                              change=change, graph=graph)
    executor = DagExecutor(build_change_workflow(), audit=audit,
                           sleeper=lambda seconds: None)
    run = executor.execute(ctx, approvals=approvals, checkpoint=checkpoint)
    return run, ctx
