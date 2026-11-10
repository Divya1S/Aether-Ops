"""Model Context Protocol server (docs/04's design made literal; docs/17
acceptance #18).

Speaks MCP's stdio transport: newline-delimited JSON-RPC 2.0. Implements
the core handshake (initialize / notifications/initialized) plus
tools/list and tools/call, exposing read-only platform tools so any MCP
client — Claude Code included — can query AetherOps:

    {"mcpServers": {"aetherops": {"command": "python3",
        "args": ["-m", "aetherops.mcp"],
        "env": {"PYTHONPATH": "src"}}}}

Pure stdlib; tools are read-only by design (the write path stays behind
the governed API and its approval gates). `investigate` runs the incident
workflow only as far as its approval gate: every node before the gate is a
read by construction (orchestration/dag.py), and this server has no tool
that decides a gate — so an IDE client can get a full diagnosis, hypothesis
tree and proposed plan, and still cannot cause a write.
"""
from __future__ import annotations

import json
import sys

from aetherops import __version__
from aetherops.api.views import build_topology, incident_view
from aetherops.core.types import ChangeEvent, new_id
from aetherops.demo import build_demo_environment
from aetherops.evals.harness import run_all
from aetherops.evals.retrieval import run_retrieval_eval
from aetherops.evals.scenarios import all_scenarios, build_environment
from aetherops.gateway.model_gateway import ModelGateway
from aetherops.graph.service_graph import default_graph
from aetherops.memory.store import EpisodicMemory
from aetherops.policy.engine import PolicyEngine
from aetherops.rag.corpus import SEED_RUNBOOKS
from aetherops.rag.retriever import RagStore
from aetherops.security.audit import AuditLog
from aetherops.workflows.change_risk import run_change_risk
from aetherops.workflows.incident_remediation import run_incident_remediation

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "search_runbooks",
        "description": "Hybrid (keyword+vector) search over AetherOps' "
                       "operational runbooks; returns attributed chunks "
                       "(rag://doc#offset).",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"]},
    },
    {
        "name": "list_runbooks",
        "description": "List the runbook corpus: id and title per document.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "eval_summary",
        "description": "Run the golden-scenario and retrieval evaluations "
                       "and return aggregate metrics, trust-ladder verdicts, "
                       "and release-gate status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_scenarios",
        "description": "List the golden incident scenarios `investigate` "
                       "can run: id, name, service, expected outcome.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "investigate",
        "description": "Investigate an incident up to (never past) the "
                       "approval gate. Returns the diagnosis, the hypothesis "
                       "tree (each candidate cause validated / invalidated / "
                       "inconclusive with the evidence that decided it), the "
                       "cited evidence bundle and the proposed plan. "
                       "Read-only: nothing is executed.",
        "inputSchema": {"type": "object",
                        "properties": {"scenario": {
                            "type": "string",
                            "description": "scenario id from "
                                           "list_scenarios; default is the "
                                           "canonical SEV2"}}},
    },
    {
        "name": "score_change",
        "description": "Score a proposed change for deploy risk against "
                       "incident history, blast radius and the deploy "
                       "window. Returns the band, score components and the "
                       "policy verdict.",
        "inputSchema": {"type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "title": {"type": "string"},
                            "diff": {"type": "string"},
                            "peak_window": {"type": "boolean"}},
                        "required": ["service", "title"]},
    },
    {
        "name": "service_topology",
        "description": "The service dependency map. With `service`, also "
                       "returns its blast radius (transitive callers) and "
                       "upstream dependencies.",
        "inputSchema": {"type": "object",
                        "properties": {"service": {"type": "string"}}},
    },
]

_STORE: RagStore | None = None


def _store() -> RagStore:
    global _STORE
    if _STORE is None:
        _STORE = RagStore()
    return _STORE


def _call_tool(name: str, arguments: dict) -> dict:
    if name == "search_runbooks":
        hits = [{"doc": r.chunk.doc_id, "title": r.chunk.doc_title,
                 "ref": r.ref, "score": r.score,
                 "excerpt": r.chunk.text[:200]}
                for r in _store().search(arguments.get("query", ""), k=5)]
        return {"results": hits}
    if name == "list_runbooks":
        return {"runbooks": [{"id": d.id, "title": d.title}
                             for d in SEED_RUNBOOKS]}
    if name == "eval_summary":
        report = run_all()
        return {"aggregates": report["aggregates"],
                "trust_ladder": report["trust_ladder"],
                "release_gate": report["release_gate"],
                "retrieval": run_retrieval_eval()}
    if name == "list_scenarios":
        return {"scenarios": [
            {"id": s.id, "name": s.name, "service": s.snapshot.service,
             "expected_outcome": s.truth.outcome}
            for s in all_scenarios()]}
    if name == "investigate":
        return _investigate(arguments.get("scenario"))
    if name == "score_change":
        return _score_change(arguments)
    if name == "service_topology":
        return build_topology(default_graph(), {}, [],
                              focus=arguments.get("service"))
    raise ValueError(f"unknown tool {name!r}")


def _investigate(scenario_id: str | None) -> dict:
    scenario = None
    if scenario_id:
        scenario = next((s for s in all_scenarios()
                         if s.id == scenario_id), None)
        if scenario is None:
            raise ValueError(f"unknown scenario {scenario_id!r}")
    # Offline is pinned: an IDE tool call must be reproducible and must
    # never reach for a model backend the user did not ask for.
    incident, env = (build_environment(scenario, backends_spec="offline")
                     if scenario is not None
                     else build_demo_environment(backends_spec="offline"))
    run, ctx = run_incident_remediation(incident, **env)   # stops at gate
    view = incident_view({"incident": incident, "env": env, "run": run,
                          "ctx": ctx, "scenario": scenario_id},
                         default_graph())
    keep = ("incident_id", "title", "service", "severity", "status",
            "outcome", "pending_gate", "diagnosis", "hypothesis",
            "hypotheses", "evidence", "plan", "policy", "review_checks",
            "blast_radius", "similar_incidents", "error", "tokens",
            "est_cost_usd", "audit")
    return {key: view[key] for key in keep if key in view}


def _score_change(arguments: dict) -> dict:
    change = ChangeEvent(
        id=new_id("chg"), service=str(arguments.get("service", "")),
        sha=str(arguments.get("sha", "0000000")),
        title=str(arguments.get("title", "")),
        diff=str(arguments.get("diff", "")),
        labels={"peak_window": bool(arguments.get("peak_window", False))})
    memory = EpisodicMemory()
    memory.add({                    # the precedent the canonical SEV2 learned
        "service": "checkout-service",
        "failure_class": "deploy-regression/memory",
        "summary": "Deploy raised DB connection pool max_size 20 -> 200; "
                   "OOMKilled cascade breached p99; rollback verified",
        "remediation": ["rollback_deployment", "create_revert_pr"],
        "verified": True})
    audit = AuditLog()
    run, _ = run_change_risk(
        change, gateway=ModelGateway(audit=audit), audit=audit,
        memory=memory, policy=PolicyEngine(), graph=default_graph())
    verdict = run.checkpoint.get("score", {})
    decision = run.checkpoint.get("policy_check", {})
    return {"service": change.service, "status": run.status.value,
            "score": verdict.get("score"), "band": verdict.get("band"),
            "components": verdict.get("components"),
            "rationale": verdict.get("rationale"),
            "requires_approval": decision.get("requires_approval"),
            "canary_required": decision.get("canary_required"),
            "policy_reason": decision.get("reason")}


def handle(message: dict) -> dict | None:
    """Returns the JSON-RPC response, or None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        params = message.get("params", {})
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": params.get("protocolVersion",
                                          PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aetherops", "version": __version__}}}

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id,
                "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = message.get("params", {})
        try:
            payload = _call_tool(params.get("name", ""),
                                 params.get("arguments", {}) or {})
            result = {"content": [{"type": "text",
                                   "text": json.dumps(payload,
                                                      default=str)}],
                      "isError": False}
        except Exception as exc:
            result = {"content": [{"type": "text", "text": str(exc)}],
                      "isError": True}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if msg_id is not None:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601,
                          "message": f"method not found: {method}"}}
    return None


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line or len(line) > 1_000_000:   # cap frame size
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0
