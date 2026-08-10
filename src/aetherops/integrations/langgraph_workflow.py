"""LangGraph orchestration of the AetherOps agent pipeline (optional extra).

The stdlib DAG engine (`orchestration/dag.py`) is the deterministic default.
This module runs the *same specialized agents* as a real LangGraph
`StateGraph` — nodes per agent, conditional routing (escalate vs. proceed),
and a genuine human-in-the-loop interrupt at the approval gate via a
checkpointer — for teams standardized on the LangChain/LangGraph stack.

    pip install "aetherops[langgraph]"
    python -m aetherops.integrations.langgraph_workflow      # runs a demo

It is OPT-IN and additive: nothing in the core imports it, so CI stays
stdlib-only and network-free. The heavy WorkflowContext lives in a registry
keyed by thread id (not in the checkpointed state), the idiomatic way to keep
non-serializable resources out of LangGraph state.
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from aetherops.agents.base import PermanentError
from aetherops.agents.knowledge import KnowledgeAgent
from aetherops.agents.planner import PlannerAgent
from aetherops.agents.reviewer import ReviewerAgent
from aetherops.agents.root_cause import RootCauseAgent
from aetherops.agents.security import SecurityAgent
from aetherops.agents.triage import TriageAgent
from aetherops.core.context import WorkflowContext

# Non-serializable resources (connectors, gateway, live context) kept out of
# the checkpointed state, keyed by the graph's thread id.
_CTX: dict[str, WorkflowContext] = {}


class IncidentState(TypedDict, total=False):
    """Only JSON-friendly fields live in graph state (checkpointer-safe)."""
    thread_id: str
    status: str                 # running | paused | remediated | escalated | denied
    failure_class: str
    suspect_commit: str | None
    steps: list[str]
    error: str


def _ctx(config) -> WorkflowContext:
    return _CTX[config["configurable"]["thread_id"]]


# --- nodes: each wraps an existing agent, mutating the shared context --------
def _triage(state: IncidentState, config) -> IncidentState:
    ctx = _ctx(config)
    ctx.record(TriageAgent().run(ctx))
    return {"status": "running"}


def _gather(state: IncidentState, config) -> IncidentState:
    ctx = _ctx(config)
    ctx.record(KnowledgeAgent().run(ctx))
    return {}


def _screen(state: IncidentState, config) -> IncidentState:
    ctx = _ctx(config)
    ctx.record(SecurityAgent().run(ctx))
    return {}


def _diagnose(state: IncidentState, config) -> IncidentState:
    ctx = _ctx(config)
    result = RootCauseAgent().run(ctx)
    ctx.record(result)
    return {"failure_class": result.output.get("failure_class"),
            "suspect_commit": result.output.get("suspect_commit")}


def _plan(state: IncidentState, config) -> IncidentState:
    ctx = _ctx(config)
    result = PlannerAgent().run(ctx)
    ctx.record(result)
    return {"steps": [s["action"] for s in result.output["steps"]]}


def _review(state: IncidentState, config) -> IncidentState:
    ctx = _ctx(config)
    try:
        ctx.record(ReviewerAgent().run(ctx))
        return {"status": "reviewed"}
    except PermanentError as exc:            # grounding checks failed -> escalate
        return {"status": "escalated", "error": str(exc)}


def _execute(state: IncidentState, config) -> IncidentState:
    return {"status": "remediated"}          # reached only after human approval


def _escalate(state: IncidentState, config) -> IncidentState:
    ctx = _ctx(config)
    rca = ctx.results.get("root_cause")
    reason = (state.get("error")
              or (rca.output.get("hypothesis", "") if rca else "")
              or "insufficient evidence")
    return {"status": "escalated", "error": reason[:200]}


# --- conditional routing ----------------------------------------------------
def _route_after_diagnosis(state: IncidentState, config) -> str:
    ctx = _ctx(config)
    rca = ctx.results.get("root_cause")
    return "plan" if rca and rca.output.get("status") == "diagnosed" else "escalate"


def _route_after_review(state: IncidentState) -> str:
    return "execute" if state.get("status") == "reviewed" else "escalate"


def build_incident_graph():
    """The compiled StateGraph. `interrupt_before=["execute"]` is the HITL
    approval gate — the model proposes; a human resumes execution."""
    graph = StateGraph(IncidentState)
    for name, fn in (("triage", _triage), ("gather_evidence", _gather),
                     ("security_screen", _screen), ("diagnose", _diagnose),
                     ("plan", _plan), ("review", _review),
                     ("execute", _execute), ("escalate", _escalate)):
        graph.add_node(name, fn)
    graph.add_edge(START, "triage")
    graph.add_edge("triage", "gather_evidence")
    graph.add_edge("gather_evidence", "security_screen")
    graph.add_edge("security_screen", "diagnose")
    graph.add_conditional_edges("diagnose", _route_after_diagnosis,
                                {"plan": "plan", "escalate": "escalate"})
    graph.add_edge("plan", "review")
    graph.add_conditional_edges("review", _route_after_review,
                                {"execute": "execute", "escalate": "escalate"})
    graph.add_edge("execute", END)
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=MemorySaver(),
                         interrupt_before=["execute"])


def run_incident_langgraph(scenario=None, approve: bool = True) -> dict:
    """Build the environment, run the agents through the LangGraph, and honor
    the human-in-the-loop interrupt. Returns the final state."""
    from aetherops.evals.scenarios import build_environment, canonical
    incident, env = build_environment(scenario or canonical())
    _CTX[incident.id] = WorkflowContext(
        incident=incident, connectors=env["connectors"],
        gateway=env["gateway"], audit=env["audit"], memory=env["memory"],
        policy=env["policy"], rag=env["rag"])
    graph = build_incident_graph()
    config = {"configurable": {"thread_id": incident.id}}
    try:
        state = graph.invoke({"thread_id": incident.id, "status": "running"},
                             config)
        snapshot = graph.get_state(config)
        if snapshot.next == ("execute",):        # paused at the approval gate
            if approve:
                state = graph.invoke(None, config)   # human approves -> resume
            else:
                state = {**state, "status": "denied"}
        return dict(state)
    finally:
        _CTX.pop(incident.id, None)


def main() -> int:
    from aetherops.evals.scenarios import canonical, reverted_pool
    print("LangGraph orchestration of the AetherOps agent pipeline\n" + "-" * 60)
    approved = run_incident_langgraph(canonical(), approve=True)
    print(f"canonical (approve): status={approved['status']} "
          f"class={approved.get('failure_class')} steps={approved.get('steps')}")
    escalated = run_incident_langgraph(reverted_pool())
    print(f"reverted-pool (adversarial): status={escalated['status']} "
          f"— the graph routed to escalate, not remediate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
