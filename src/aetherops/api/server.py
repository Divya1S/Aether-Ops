"""HTTP surface for AetherOps (docs/12's shape, stdlib implementation).

Endpoints (JSON in/out):
  GET  /health                          open — liveness + build info
  POST /v1/incidents                    auth — start the canonical incident,
                                        runs to the approval gate
  GET  /v1/incidents/{id}               auth — status + diagnosis summary
  POST /v1/incidents/{id}/approvals     auth — {"decision": "approve"|"deny"}
                                        replays the DAG gate semantics
  POST /v1/changes/score                auth — change-risk scoring
  GET  /v1/evals                        auth — golden + retrieval metrics
  GET  /v1/runbooks/search?q=...        auth — attributed RAG search

Auth: `Authorization: Bearer $AETHEROPS_API_TOKEN` (default: aetherops-dev
— set a real token in any non-local deployment). Mutations are impossible
without it (docs/17 acceptance #14).
"""
from __future__ import annotations

import concurrent.futures
import hmac
import json
import os
import threading
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BODY_BYTES = 64 * 1024          # request bodies are small JSON by design
MAX_INCIDENTS_RETAINED = 200        # oldest resolved entries evicted first

from aetherops import __version__
from aetherops.core.types import ChangeEvent, WorkflowStatus, new_id
from aetherops.demo import build_demo_environment
from aetherops.evals.harness import run_all
from aetherops.evals.retrieval import run_retrieval_eval
from aetherops.gateway.model_gateway import ModelGateway
from aetherops.graph.service_graph import default_graph
from aetherops.memory.store import EpisodicMemory
from aetherops.policy.engine import PolicyEngine
from aetherops.rag.retriever import RagStore
from aetherops.security.audit import AuditLog
from aetherops.workflows.change_risk import run_change_risk
from aetherops.workflows.incident_remediation import run_incident_remediation


def _tokens() -> dict[str, str]:
    """Token → role registry (docs/05 §2). The primary token is admin for
    back-compatibility; scoped tokens are opt-in via environment."""
    registry = {os.environ.get("AETHEROPS_API_TOKEN",
                               "aetherops-dev"): "admin"}
    for env_var, role in (("AETHEROPS_VIEWER_TOKEN", "viewer"),
                          ("AETHEROPS_OPERATOR_TOKEN", "operator"),
                          ("AETHEROPS_APPROVER_TOKEN", "approver")):
        token = os.environ.get(env_var)
        if token:
            registry[token] = role
    return registry


class AppState:
    def __init__(self):
        self.incidents: OrderedDict[str, dict] = OrderedDict()
        self.rag = RagStore()
        self.tokens = _tokens()
        # Per-incident locks: approval is check-then-act and MUST be atomic
        # (two racing approvals would double-execute the remediation — the
        # exact failure class the platform exists to prevent).
        self.locks: dict[str, threading.Lock] = {}
        self.state_lock = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def lock_for(self, incident_id: str) -> threading.Lock:
        with self.state_lock:
            return self.locks.setdefault(incident_id, threading.Lock())

    def evict(self) -> None:
        with self.state_lock:
            while len(self.incidents) > MAX_INCIDENTS_RETAINED:
                for key, entry in self.incidents.items():
                    if entry["run"].status.value != "PAUSED":
                        self.incidents.pop(key)
                        self.locks.pop(key, None)
                        break
                else:                    # everything paused: evict oldest
                    key, _ = self.incidents.popitem(last=False)
                    self.locks.pop(key, None)


STATE = AppState()


def _incident_summary(entry: dict) -> dict:
    run, ctx = entry["run"], entry["ctx"]
    rca = ctx.results.get("root_cause")
    summary = {
        "incident_id": entry["incident"].id,
        "title": entry["incident"].title,
        "status": run.status.value,
        "pending_gate": run.pending_gate,
        "evidence_count": len(ctx.evidence),
        "tokens": entry["env"]["gateway"].tokens_used,
        "est_cost_usd": entry["env"]["gateway"].est_cost_usd,
    }
    if rca is not None:
        summary["diagnosis"] = {
            "status": rca.output.get("status"),
            "failure_class": rca.output.get("failure_class"),
            "suspect_commit": rca.output.get("suspect_commit"),
            "confidence": round(rca.confidence, 3),
        }
    verdict = run.checkpoint.get("policy_check")
    if verdict:
        summary["approval_tier"] = verdict.get("approval_tier")
    if run.error:
        summary["error"] = run.error
    return summary


class Handler(BaseHTTPRequestHandler):
    server_version = f"AetherOps/{__version__}"

    # ------------------------------------------------------------------ util
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _role(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        presented = header[len("Bearer "):]
        for token, role in STATE.tokens.items():   # timing-safe comparison
            if hmac.compare_digest(presented, token):
                return role
        return None

    def _require(self, action: str) -> bool:
        """Access control before anything runs: 401 unknown token, 403
        known token whose role the policy table doesn't grant `action`."""
        role = self._role()
        if role is None:
            self._send(401, {"error": "missing or invalid bearer token"})
            return False
        if not PolicyEngine.role_allows(role, action):
            self._send(403, {"error": f"role {role!r} may not {action}"})
            return False
        return True

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length or length > MAX_BODY_BYTES:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, *args):        # structured access log, opt-in
        if os.environ.get("AETHEROPS_API_LOG"):
            print(json.dumps({"api": self.path, "method": self.command}))

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._send(200, {
                "status": "ok", "service": "aetherops",
                "version": __version__,
                "backends": os.environ.get("AETHEROPS_BACKENDS", "offline")})
        if not self._require("read"):
            return
        if parsed.path == "/v1/evals":
            report = run_all()
            return self._send(200, {
                "aggregates": report["aggregates"],
                "trust_ladder": report["trust_ladder"],
                "release_gate": report["release_gate"],
                "retrieval": run_retrieval_eval()})
        if parsed.path == "/v1/runbooks/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            hits = [{"doc": r.chunk.doc_id, "title": r.chunk.doc_title,
                     "ref": r.ref, "score": r.score,
                     "excerpt": r.chunk.text[:200]}
                    for r in STATE.rag.search(query, k=5)]
            return self._send(200, {"query": query, "results": hits})
        if parsed.path.startswith("/v1/incidents/"):
            incident_id = parsed.path.rsplit("/", 1)[-1]
            entry = STATE.incidents.get(incident_id)
            if entry is None:
                return self._send(404, {"error": "unknown incident"})
            if entry["run"] is None:            # async run still executing
                return self._send(200, {"incident_id": incident_id,
                                        "status": "RUNNING",
                                        "fence": entry["fence"]})
            return self._send(200, {**_incident_summary(entry),
                                    "fence": entry["fence"]})
        self._send(404, {"error": "unknown route"})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/v1/incidents":
            if not self._require("create"):
                return
            body = self._body()
            incident, env = build_demo_environment()
            entry = {"incident": incident, "env": env, "run": None,
                     "ctx": None, "fence": uuid.uuid4().hex,
                     "phase": "RUNNING"}
            STATE.incidents[incident.id] = entry
            STATE.evict()

            def _execute():
                run, ctx = run_incident_remediation(incident, **env)
                with STATE.lock_for(incident.id):
                    entry["run"], entry["ctx"] = run, ctx
                    entry["phase"] = "READY"

            if body.get("async"):
                STATE.pool.submit(_execute)     # 202 now, poll via GET
                return self._send(202, {"incident_id": incident.id,
                                        "status": "RUNNING",
                                        "fence": entry["fence"]})
            _execute()
            return self._send(201, {**_incident_summary(entry),
                                    "fence": entry["fence"]})

        if (parsed.path.startswith("/v1/incidents/")
                and parsed.path.endswith("/approvals")):
            if not self._require("approve"):
                return
            incident_id = parsed.path.split("/")[3]
            entry = STATE.incidents.get(incident_id)
            if entry is None:
                return self._send(404, {"error": "unknown incident"})
            body = self._body()
            decision = body.get("decision")
            if decision not in ("approve", "deny"):
                return self._send(400, {"error": "decision must be "
                                                 "'approve' or 'deny'"})
            # Atomic check-then-act: without the lock, two racing approvals
            # both observe PAUSED and the remediation executes twice. The
            # optional fence token (docs/12) rejects decisions made against
            # a stale view of the incident.
            with STATE.lock_for(incident_id):
                if (body.get("fence") is not None
                        and body["fence"] != entry["fence"]):
                    return self._send(409, {"error": "stale fence token"})
                run = entry["run"]
                if run is None:
                    return self._send(409, {"error": "incident still "
                                                     "running"})
                if run.status != WorkflowStatus.PAUSED:
                    return self._send(409, {"error": "incident is not "
                                                     "awaiting approval"})
                run, ctx = run_incident_remediation(
                    entry["incident"], **entry["env"], ctx=entry["ctx"],
                    approvals={run.pending_gate: decision == "approve"},
                    checkpoint=run.checkpoint)
                entry["run"], entry["ctx"] = run, ctx
                entry["fence"] = uuid.uuid4().hex
            response = _incident_summary(entry)
            postmortem = run.checkpoint.get("postmortem")
            if postmortem:
                response["postmortem_excerpt"] = \
                    postmortem["markdown"][:400]
                response["follow_ups"] = postmortem["follow_ups"]
            return self._send(200, response)

        if parsed.path == "/v1/changes/score":
            if not self._require("create"):
                return
            body = self._body()
            change = ChangeEvent(
                id=new_id("chg"),
                service=body.get("service", "orders-service"),
                sha=body.get("sha", "0000000"),
                title=body.get("title", ""),
                diff=body.get("diff", ""),
                labels={"peak_window": bool(body.get("peak_window", False)),
                        "freeze": bool(body.get("freeze", False))})
            audit = AuditLog()
            run, ctx = run_change_risk(
                change, gateway=ModelGateway(audit=audit), audit=audit,
                memory=EpisodicMemory(), policy=PolicyEngine(),
                graph=default_graph())
            verdict = run.checkpoint.get("score", {})
            decision = run.checkpoint.get("policy_check", {})
            return self._send(200, {
                "change_id": change.id,
                "status": run.status.value,
                "score": verdict.get("score"),
                "band": verdict.get("band"),
                "components": verdict.get("components"),
                "requires_approval": decision.get("requires_approval"),
                "canary_required": decision.get("canary_required"),
                "error": run.error})

        if self._role() is None:      # unknown routes still demand a token
            return self._send(401, {"error": "missing or invalid bearer "
                                             "token"})
        self._send(404, {"error": "unknown route"})


def serve(port: int = 8080):
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(json.dumps({"listening": port, "health": "/health",
                      "auth": "Authorization: Bearer $AETHEROPS_API_TOKEN"}))
    server.serve_forever()
