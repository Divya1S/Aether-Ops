"""The canonical incident-remediation workflow (docs/01-architecture.md §5,
docs/03-orchestration.md): the compiled DAG for a SEV2 deploy regression.

  triage -> gather_evidence -> security_screen -> diagnose -> plan
        -> review -> policy_check -> [approval gate] -> execute
        -> verify -> learn -> postmortem

Every agent node enforces the citation contract (no citations, no claim).
Execution steps return `undo` descriptors; compensation executes them in
reverse if verification fails (saga).
"""
from __future__ import annotations

from aetherops.agents.base import PermanentError
from aetherops.agents.knowledge import KnowledgeAgent
from aetherops.agents.planner import PlannerAgent
from aetherops.agents.reviewer import ReviewerAgent
from aetherops.agents.root_cause import RootCauseAgent
from aetherops.agents.security import SecurityAgent
from aetherops.agents.triage import TriageAgent
from aetherops.agents.verifier import VerifierAgent
from aetherops.core.context import WorkflowContext
from aetherops.core.types import RiskLevel
from aetherops.orchestration.dag import DagExecutor, DagRun, GateSpec, Node
from aetherops.reporting.postmortem import build_postmortem


def _agent_node(agent):
    def run(ctx):
        result = agent.run(ctx)
        if not result.citations:
            raise PermanentError(
                f"{agent.name}: result carries no citations — claim rejected")
        ctx.record(result)
        return result.to_checkpoint()
    return run


def _policy_check(ctx):
    plan = ctx.results["planner"]
    verdict = ctx.policy.evaluate_plan(
        plan.output["steps"],
        environment=ctx.incident.environment,
        confidence=plan.confidence)
    if not verdict["allowed"]:
        denied = [d["reason"] for d in verdict["decisions"] if not d["allowed"]]
        raise PermanentError(f"plan denied by policy: {denied}")
    ctx.params["policy_verdict"] = verdict
    return verdict


def _execute(ctx):
    executed = []
    for step in ctx.results["planner"].output["steps"]:
        result = ctx.connectors.call(step["system"], step["tool"],
                                     step["args"], principal="executor")
        executed.append({"action": step["action"], "result": result.data})
    ctx.params["executed"] = executed
    return {"executed": executed}


def _compensate_execute(ctx, output):
    """Saga: run each executed step's undo descriptor, most recent first."""
    for record in reversed(output.get("executed", [])):
        undo = record["result"].get("undo")
        if undo:
            ctx.connectors.call(undo["system"], undo["tool"], undo["args"],
                                principal="compensator")


def _learn(ctx):
    rca = ctx.results["root_cause"]
    plan = ctx.results["planner"]
    verified = ctx.results["verifier"].output
    episode_id = ctx.memory.add({
        "service": ctx.results["triage"].output["service"],
        "failure_class": rca.output["failure_class"],
        "summary": rca.output["hypothesis"][:300],
        "remediation": [s["action"] for s in plan.output["steps"]],
        "verified": verified["recovered"],
    })
    output = {"episode_id": episode_id,
              "failure_class": rca.output["failure_class"]}
    ctx.params["episode"] = output
    return output


def _postmortem(ctx):
    result = build_postmortem(ctx)
    # Close the knowledge loop: incident N's postmortem becomes retrievable
    # context for incident N+1 (docs/17 M7 rule 5).
    if getattr(ctx, "rag", None) is not None:
        from aetherops.rag.corpus import Document
        chunks = ctx.rag.add_document(Document(
            id=f"postmortem-{ctx.incident.id}",
            title=f"Postmortem: {ctx.incident.title}",
            kind="postmortem",
            text=result["markdown"]))
        result = {**result, "ingested_chunks": chunks}
    return result


def build_workflow() -> list[Node]:
    return [
        Node("triage", _agent_node(TriageAgent())),
        Node("gather_evidence", _agent_node(KnowledgeAgent()),
             deps=("triage",)),
        Node("security_screen", _agent_node(SecurityAgent()),
             deps=("gather_evidence",)),
        Node("diagnose", _agent_node(RootCauseAgent()),
             deps=("security_screen",)),
        Node("plan", _agent_node(PlannerAgent()), deps=("diagnose",)),
        Node("review", _agent_node(ReviewerAgent()), deps=("plan",)),
        Node("policy_check", _policy_check, deps=("review",)),
        Node("approval_gate", run=None, deps=("policy_check",),
             gate=GateSpec(
                 risk=RiskLevel.HIGH,
                 reason="execute remediation against prod",
                 needed=lambda cp: cp.get("policy_check", {})
                                     .get("requires_approval", True))),
        Node("execute", _execute, deps=("approval_gate",),
             compensate=_compensate_execute),
        Node("verify", _agent_node(VerifierAgent()), deps=("execute",)),
        Node("learn", _learn, deps=("verify",)),
        Node("postmortem", _postmortem, deps=("learn",)),
    ]


def run_incident_remediation(incident, *, connectors, gateway, audit, memory,
                             policy, rag=None,
                             approvals: dict | None = None,
                             checkpoint: dict | None = None,
                             ctx: WorkflowContext | None = None
                             ) -> tuple[DagRun, WorkflowContext]:
    """Executes (or resumes) the workflow. Returns the run record and the
    context; a PAUSED run resumes by passing the same ctx, the returned
    checkpoint, and the approval decision."""
    if ctx is None:
        ctx = WorkflowContext(incident=incident, connectors=connectors,
                              gateway=gateway, audit=audit, memory=memory,
                              policy=policy, rag=rag)
    executor = DagExecutor(build_workflow(), audit=audit,
                           sleeper=lambda seconds: None)
    run = executor.execute(ctx, approvals=approvals, checkpoint=checkpoint)
    return run, ctx
