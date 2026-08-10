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

Auth: `Authorization: Bearer $AETHEROPS_API_TOKEN`. The server refuses to
boot without a real token unless AETHEROPS_ALLOW_DEV_TOKEN=1 is set, and the
built-in dev token is only ever served on loopback (see `_preflight`). It
binds 127.0.0.1 by default; AETHEROPS_BIND selects another interface but
then requires a real token. Mutations are impossible without a valid bearer
token (docs/17 acceptance #14).
"""
from __future__ import annotations

import concurrent.futures
import hmac
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BODY_BYTES = 64 * 1024          # request bodies are small JSON by design
MAX_INCIDENTS_RETAINED = 200        # oldest resolved entries evicted first
MAX_ASYNC_INFLIGHT = 16             # backpressure: reject async beyond this

from aetherops import __version__
from aetherops.core.types import ChangeEvent, WorkflowStatus, new_id
from aetherops.demo import build_demo_environment
from aetherops.evals.harness import run_all
from aetherops.evals.retrieval import run_retrieval_eval
from aetherops.gateway.model_gateway import ModelGateway
from aetherops.graph.service_graph import default_graph
from aetherops.memory.store import EpisodicMemory
from aetherops.storage.sqlite import SqliteEpisodicMemory
from aetherops.policy.engine import PolicyEngine
from aetherops.rag.retriever import RagStore
from aetherops.security.audit import AuditLog
from aetherops.workflows.change_risk import run_change_risk
from aetherops.workflows.incident_remediation import run_incident_remediation


def _tokens() -> dict[str, str]:
    """Token → role registry (docs/05 §2). The primary token is admin for
    back-compatibility; scoped tokens are opt-in via environment."""
    # `or` (not a default arg) so an explicitly-empty env value can't register
    # "" as a valid admin token; _preflight governs whether the dev token boots.
    registry = {(os.environ.get("AETHEROPS_API_TOKEN") or "aetherops-dev"):
                "admin"}
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
        self.async_inflight = 0     # guarded by state_lock (backpressure)
        # Organizational memory (Phase K): incidents write their learned
        # episodes here and change-risk scoring reads them — the flywheel,
        # across requests. DURABLE when AETHEROPS_DB is set (learning survives
        # a restart), bounded in-memory otherwise. Seeded with the canonical
        # learned episode so risk differentiation is visible from request one.
        self.db_path = os.environ.get("AETHEROPS_DB") or None
        # Per-incident audit chains persist as JSONL beside the DB, so a past
        # incident's governance trail is reloadable + verifiable after a
        # restart (Phase M).
        self.audit_dir = None
        if self.db_path:
            self.audit_dir = self.db_path + ".audit"
            os.makedirs(self.audit_dir, exist_ok=True)
        self.memory = (SqliteEpisodicMemory(self.db_path) if self.db_path
                       else EpisodicMemory(max_episodes=1000))
        if len(self.memory) == 0:
            self.memory.add({
                "service": "checkout-service",
                "failure_class": "deploy-regression/memory",
                "summary": "Deploy raised DB connection pool max_size 20 -> "
                           "200; OOMKilled cascade breached p99; rollback "
                           "verified",
                "remediation": ["rollback_deployment", "create_revert_pr"],
                "verified": True})

    def lock_for(self, incident_id: str) -> threading.Lock:
        with self.state_lock:
            return self.locks.setdefault(incident_id, threading.Lock())

    def evict(self) -> None:
        with self.state_lock:
            while len(self.incidents) > MAX_INCIDENTS_RETAINED:
                for key, entry in self.incidents.items():
                    # A still-running (run is None) or PAUSED incident is
                    # in-flight — never evict it mid-flight.
                    run = entry["run"]
                    if run is not None and run.status.value != "PAUSED":
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

    # Rich fields for the operator console (humans keep full visibility —
    # evidence classification is shown as a badge, not withheld).
    summary["agents"] = [
        {"name": name, "confidence": round(result.confidence, 2),
         "model": result.model_id}
        for name, result in ctx.results.items()]
    summary["evidence"] = [
        {"kind": e.kind, "source": e.citation.source, "ref": e.citation.ref,
         "summary": e.summary[:140],
         "classification": e.classification}
        for e in ctx.evidence]
    if rca is not None:
        summary["hypothesis"] = rca.output.get("hypothesis", "")
    planner = ctx.results.get("planner")
    if planner is not None:
        summary["plan"] = [
            {"action": s["action"], "risk": s["risk"], "args": s["args"]}
            for s in planner.output.get("steps", [])]
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
            try:
                if hmac.compare_digest(presented, token):
                    return role
            except TypeError:           # non-ASCII bearer -> invalid, not 500
                return None
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

    def _send_ui(self) -> None:
        """The operator console — a single self-contained page served at the
        API root, so `make serve` / `docker run` yields a clickable UI that
        drives the real endpoints (docs/17 Milestone 13)."""
        path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:              # malformed Content-Length -> no body
            return {}
        if not length or length > MAX_BODY_BYTES:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        # Callers do body.get(...); a non-object body (list/str/number) would
        # otherwise 500. Coerce anything that isn't a dict to an empty body.
        return parsed if isinstance(parsed, dict) else {}

    def _correlation_id(self) -> str:
        """The incident id doubles as the request correlation id, tying an
        access-log line to the incident's audit chain (audit H7)."""
        parts = urlparse(self.path).path.split("/")
        if "incidents" in parts:
            idx = parts.index("incidents")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return "-"

    def log_request(self, code="-", size="-"):
        """One structured access line per request (opt-in via AETHEROPS_API_LOG;
        `make serve` sets it). Status, role, correlation id, and latency — the
        minimum needed to debug a request and find its audit chain (audit H7)."""
        if not os.environ.get("AETHEROPS_API_LOG"):
            return
        print(json.dumps({
            "method": self.command, "path": self.path,
            "status": int(code) if str(code).isdigit() else code,
            "role": self._role() or "anon",
            "corr_id": self._correlation_id(),
            "latency_ms": round(
                (time.monotonic() - getattr(self, "_t0", time.monotonic()))
                * 1000, 1)}), flush=True)

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        self._t0 = time.monotonic()
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/app", "/index.html"):
            return self._send_ui()
        if parsed.path == "/health":
            return self._send(200, {
                "status": "ok", "service": "aetherops",
                "version": __version__,
                "backends": os.environ.get("AETHEROPS_BACKENDS", "offline"),
                "persistence": "sqlite" if STATE.db_path else "in-memory",
                "episodes": len(STATE.memory)})
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
        if (parsed.path.startswith("/v1/incidents/")
                and parsed.path.endswith("/audit")):
            # Governance made visible (audit H3a/H7): fetch an incident's
            # hash-chained ledger and its verification status. The "recorded,
            # tamper-evident" claim is only real if it is reachable.
            incident_id = parsed.path.split("/")[3]
            entry = STATE.incidents.get(incident_id)
            persisted = (os.path.join(STATE.audit_dir, f"{incident_id}.jsonl")
                         if STATE.audit_dir else None)
            if entry is not None:
                audit = entry["env"]["audit"]
            elif persisted and os.path.exists(persisted):
                audit = AuditLog.load(persisted)     # reloaded after a restart
            else:
                return self._send(404, {"error": "unknown incident"})
            return self._send(200, {
                "incident_id": incident_id,
                "count": len(audit.records),
                "chain_verified": audit.verify(),
                "records": [{"seq": r.seq, "ts": r.ts, "actor": r.actor,
                             "action": r.action, "payload": r.payload}
                            for r in audit.records]})
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
        self._t0 = time.monotonic()
        parsed = urlparse(self.path)

        if parsed.path == "/v1/incidents":
            if not self._require("create"):
                return
            body = self._body()
            is_async = bool(body.get("async"))
            if is_async:
                # Backpressure BEFORE doing any work, so a rejected request
                # creates no entry to leak (audit M2).
                with STATE.state_lock:
                    if STATE.async_inflight >= MAX_ASYNC_INFLIGHT:
                        return self._send(429, {
                            "error": "too many incidents in flight; "
                                     "retry shortly"})
                    STATE.async_inflight += 1
            incident, env = build_demo_environment()
            if STATE.db_path:
                # Persistence mode: incidents read AND write the shared,
                # durable organizational memory, so the learning flywheel
                # spans requests and survives a restart (Phase K). Default
                # mode keeps each incident's memory isolated (test-stable).
                env["memory"] = STATE.memory
                # ...and this incident's audit chain persists to JSONL (M).
                env["audit"].attach_path(
                    os.path.join(STATE.audit_dir, f"{incident.id}.jsonl"))
            entry = {"incident": incident, "env": env, "run": None,
                     "ctx": None, "fence": uuid.uuid4().hex,
                     "phase": "RUNNING"}
            with STATE.state_lock:          # insert atomic vs. evict (audit M3)
                STATE.incidents[incident.id] = entry
            STATE.evict()

            def _execute():
                try:
                    run, ctx = run_incident_remediation(incident, **env)
                    with STATE.lock_for(incident.id):
                        entry["run"], entry["ctx"] = run, ctx
                        entry["phase"] = "READY"
                finally:
                    if is_async:
                        with STATE.state_lock:
                            STATE.async_inflight -= 1

            if is_async:
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
                # Fence is REQUIRED (audit M6): a decision must echo the
                # current token, so an approval made against a stale view of
                # the incident is rejected rather than silently accepted.
                if body.get("fence") != entry["fence"]:
                    return self._send(409, {"error": "missing or stale fence "
                                            "token — re-read the incident"})
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
            response = {**_incident_summary(entry), "fence": entry["fence"]}
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
                memory=STATE.memory, policy=PolicyEngine(),
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


def _preflight(env: dict) -> str:
    """Resolve the bind address and enforce the credential policy BEFORE the
    socket opens (audit C1). Returns the bind host; raises SystemExit on an
    unsafe combination. Layered rule:
      - a real AETHEROPS_API_TOKEN may bind anywhere;
      - the built-in dev token requires an explicit AETHEROPS_ALLOW_DEV_TOKEN=1
        opt-in AND a loopback bind — it is never exposed on a public interface;
      - no token and no opt-in refuses to boot rather than register a
        publicly-known admin credential silently.
    """
    bind = env.get("AETHEROPS_BIND", "127.0.0.1")   # loopback by default
    if env.get("AETHEROPS_API_TOKEN"):
        return bind
    if env.get("AETHEROPS_ALLOW_DEV_TOKEN") != "1":
        raise SystemExit(
            "AetherOps refuses to boot: AETHEROPS_API_TOKEN is unset. Set a "
            "real token, or export AETHEROPS_ALLOW_DEV_TOKEN=1 to run with the "
            "insecure built-in dev token on loopback only.")
    if bind not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            f"AetherOps refuses to boot: the built-in dev token must not be "
            f"exposed on {bind!r}. Set AETHEROPS_API_TOKEN to a real token to "
            f"bind a non-loopback address.")
    return bind


def serve(port: int = 8080):
    bind = _preflight(os.environ)
    server = ThreadingHTTPServer((bind, port), Handler)
    print(json.dumps({"listening": port, "bind": bind, "health": "/health",
                      "auth": "Authorization: Bearer $AETHEROPS_API_TOKEN"}))
    server.serve_forever()
