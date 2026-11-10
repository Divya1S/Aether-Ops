"""HTTP surface for AetherOps (docs/12's shape, stdlib implementation).

The transport is a declarative route table (`ROUTES`): one row per endpoint
carrying its method, path template, the policy grant it requires and a
one-line summary. Dispatch, access control, rate limiting, error mapping and
the generated OpenAPI document all read that single table, so an endpoint
cannot exist without declaring who may call it.

    GET  /                                  the operator console (public)
    GET  /health                            liveness + build info (public)
    GET  /v1/openapi.json                   generated from ROUTES
    GET  /v1/whoami                         the caller's role and grants

    POST /v1/alerts                         ingest (native or Alertmanager)
    GET  /v1/alerts                         correlated groups + noise stats
    POST /v1/alerts/groups/{id}/investigate open an incident for a group

    POST /v1/incidents                      start an incident, run to the gate
    GET  /v1/incidents                      history
    GET  /v1/incidents/{id}                 the full incident document
    GET  /v1/incidents/{id}/audit           hash-chained ledger + verification
    GET  /v1/incidents/{id}/postmortem      the generated postmortem
    POST /v1/incidents/{id}/approvals       decide the gate (fenced, atomic)
    POST /v1/incidents/{id}/feedback        mark the diagnosis right or wrong

    GET  /v1/topology  /v1/stats  /v1/catalog  /v1/scenarios  /v1/connectors
    GET  /v1/evals     /v1/runbooks/search?q=
    POST /v1/changes/score

Auth: `Authorization: Bearer $AETHEROPS_API_TOKEN`. The server refuses to
boot without a real token unless AETHEROPS_ALLOW_DEV_TOKEN=1 is set, and the
built-in dev token is only ever served on loopback (see `_preflight`). It
binds 127.0.0.1 by default; AETHEROPS_BIND selects another interface but
then requires a real token. Mutations are impossible without a valid bearer
token (docs/17 acceptance #14).
"""
from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

MAX_BODY_BYTES = 64 * 1024          # request bodies are small JSON by design
MAX_ALERT_BODY_BYTES = 256 * 1024   # an alert batch is the one large body
MAX_INCIDENTS_RETAINED = 200        # oldest resolved entries evicted first
MAX_ASYNC_INFLIGHT = 16             # backpressure: reject async beyond this
DEFAULT_RATE_LIMIT_PER_MIN = 600    # mutating requests per role, per minute
MAX_NOTE_CHARS = 500

from aetherops import __version__
from aetherops.agents.investigation import TOOLS as INVESTIGATION_TOOLS
from aetherops.agents.planner import REQUIRED_ARGS, STEP_CATALOG
from aetherops.alerts import AlertStore, AlertValidationError, normalize
from aetherops.api.views import (STAGE_LABELS, build_stats, build_topology,
                                 incident_row, incident_view, running_view)
from aetherops.connectors.adapters import connector_roster
from aetherops.core.types import ChangeEvent, WorkflowStatus, new_id
from aetherops.demo import build_demo_environment
from aetherops.evals.harness import run_all
from aetherops.evals.retrieval import run_retrieval_eval
from aetherops.evals.scenarios import all_scenarios, build_environment
from aetherops.gateway.model_gateway import ModelGateway
from aetherops.graph.service_graph import default_graph
from aetherops.memory.store import EpisodicMemory
from aetherops.policy.engine import PolicyEngine
from aetherops.prompts.registry import current_locks
from aetherops.rag.retriever import RagStore
from aetherops.security.audit import AuditLog
from aetherops.storage.sqlite import (SqliteEpisodicMemory,
                                      SqliteIncidentArchive)
from aetherops.workflows.change_risk import run_change_risk
from aetherops.workflows.incident_remediation import (build_workflow,
                                                      run_incident_remediation)

UI_PATHS = ("/", "/app", "/index.html")
ANY_ROLE = "*"                      # route grant: any authenticated role


class ApiError(Exception):
    """A request the API refuses; mapped to a JSON error response."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.payload = {"error": message, **extra}


def _tokens() -> dict[str, str]:
    """Token → role registry (docs/05 §2). The primary token is admin for
    back-compatibility; scoped tokens are opt-in via environment."""
    # `or` (not a default arg) so an explicitly-empty env value can't register
    # "" as a valid admin token; _preflight governs whether the dev token boots.
    registry = {(os.environ.get("AETHEROPS_API_TOKEN") or "aetherops-dev"):
                "admin"}
    for env_var, role in (("AETHEROPS_VIEWER_TOKEN", "viewer"),
                          ("AETHEROPS_OPERATOR_TOKEN", "operator"),
                          ("AETHEROPS_APPROVER_TOKEN", "approver"),
                          ("AETHEROPS_INGEST_TOKEN", "ingest")):
        token = os.environ.get(env_var)
        if token:
            registry[token] = role
    return registry


class RateLimiter:
    """Sliding one-minute window per key. Guards the mutating endpoints so a
    runaway client (or a misconfigured webhook) degrades into 429s instead
    of an unbounded queue of workflow runs."""

    def __init__(self, limit_per_min: int, clock=time.monotonic):
        self.limit = limit_per_min
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = {}

    def check(self, key: str) -> float:
        """0.0 when the request is admitted, else seconds until a slot
        frees up."""
        if self.limit <= 0:
            return 0.0
        now = self._clock()
        with self._lock:
            window = self._hits.setdefault(key, deque())
            while window and now - window[0] >= 60.0:
                window.popleft()
            if len(window) >= self.limit:
                return max(0.1, 60.0 - (now - window[0]))
            window.append(now)
            return 0.0


def _rate_limit_from_env() -> int:
    try:
        return int(os.environ.get("AETHEROPS_RATE_LIMIT_PER_MIN",
                                  DEFAULT_RATE_LIMIT_PER_MIN))
    except ValueError:
        return DEFAULT_RATE_LIMIT_PER_MIN


class AppState:
    def __init__(self):
        self.incidents: OrderedDict[str, dict] = OrderedDict()
        self.rag = RagStore()
        self.tokens = _tokens()
        self.graph = default_graph()
        # Sense-plane intake: dedup + topology correlation (alerts/store.py).
        self.alerts = AlertStore(self.graph)
        self.limiter = RateLimiter(_rate_limit_from_env())
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
        # Incident history persists too: the rendered document of every
        # incident is archived on each state change (storage/sqlite.py).
        self.archive = None
        if self.db_path:
            self.audit_dir = self.db_path + ".audit"
            os.makedirs(self.audit_dir, exist_ok=True)
            self.archive = SqliteIncidentArchive(self.db_path)
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

    def refresh(self, entry: dict) -> dict:
        """Rebuild and cache an incident's document after a state change,
        and archive it when persistence is on. Call under the incident's
        lock."""
        view = incident_view(entry, self.graph)
        entry["view"] = view
        if self.archive is not None:
            self.archive.save(view["incident_id"], view["created_at"], view)
        return view

    def rows(self) -> list[dict]:
        """Every incident the API knows — live first, then anything only the
        archive remembers — newest first."""
        with self.state_lock:
            entries = list(self.incidents.values())
        rows, seen = [], set()
        for entry in entries:
            view = entry.get("view") if entry["run"] is not None else None
            row = incident_row(view if view is not None
                               else running_view(entry))
            rows.append(row)
            seen.add(row["incident_id"])
        if self.archive is not None:
            for view in self.archive.all():
                if view["incident_id"] not in seen:
                    rows.append(incident_row({**view, "archived": True}))
        rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
        return rows

    def close(self) -> None:
        self.memory.close()
        if self.archive is not None:
            self.archive.close()


STATE = AppState()


def _scenario_for_service(service: str):
    """The golden snapshot that can serve evidence for `service`, if any.
    Connectors are snapshot fakes by default, so an investigation is only
    possible where frozen evidence exists."""
    return next((s for s in all_scenarios()
                 if s.snapshot.service == service), None)


# --------------------------------------------------------------- handlers
# Each handler takes the request plus its path parameters and returns
# (status, payload). Raising ApiError maps to a JSON error response.

def h_health(req) -> tuple[int, dict]:
    return 200, {
        "status": "ok", "service": "aetherops", "version": __version__,
        "backends": os.environ.get("AETHEROPS_BACKENDS", "offline"),
        "persistence": "sqlite" if STATE.db_path else "in-memory",
        "episodes": len(STATE.memory)}


def h_whoami(req) -> tuple[int, dict]:
    role = req._role()
    return 200, {"role": role, "grants": sorted(
        action for action in PolicyEngine.API_ROLE_GRANTS
        if PolicyEngine.role_allows(role, action))}


def h_openapi(req) -> tuple[int, dict]:
    return 200, openapi_document()


def h_evals(req) -> tuple[int, dict]:
    report = run_all()
    return 200, {
        "aggregates": report["aggregates"],
        "trust_ladder": report["trust_ladder"],
        "release_gate": report["release_gate"],
        "judge_gate": report["judge_gate"],
        "scenarios": [
            {key: row[key] for key in (
                "scenario", "name", "outcome", "expected_outcome",
                "outcome_correct", "predicted_class", "expected_class",
                "diagnosis_correct", "steps_correct", "confidence",
                "calibration_error", "tool_calls", "investigation_steps",
                "tokens", "audit_verified")}
            for row in report["rows"]],
        "retrieval": run_retrieval_eval()}


def h_connectors(req) -> tuple[int, dict]:
    # Which integration slot is served by a REAL adapter vs a fake, per
    # current env config (Phase Q). Default: all fakes.
    return 200, {"connectors": connector_roster()}


def h_scenarios(req) -> tuple[int, dict]:
    # The API can run any golden scenario, not just the canonical one
    # (Phase P): the console offers these; the adversarial ones
    # demonstrate escalation rather than remediation.
    return 200, {"scenarios": [
        {"id": s.id, "name": s.name, "service": s.snapshot.service,
         "expected_outcome": s.truth.outcome}
        for s in all_scenarios()]}


def h_catalog(req) -> tuple[int, dict]:
    """The governed surface, as data: what the platform can do, who may ask
    it to, and which prompt versions are pinned."""
    return 200, {
        "step_catalog": [
            {"action": name, "system": entry["system"],
             "tool": entry["tool"], "risk": entry["risk"],
             "compensable": entry["compensable"],
             "required_args": list(REQUIRED_ARGS.get(name, ()))}
            for name, entry in sorted(STEP_CATALOG.items())],
        "investigation_tools": [
            {"name": name, "description": spec["description"],
             "risk": "READ"}
            for name, spec in INVESTIGATION_TOOLS.items()],
        "stages": [{"id": node.name,
                    "label": STAGE_LABELS.get(node.name, node.name),
                    "gate": node.gate is not None}
                   for node in build_workflow()],
        "role_grants": {action: sorted(roles) for action, roles
                        in PolicyEngine.API_ROLE_GRANTS.items()},
        "prompts": sorted(current_locks()),
    }


def h_runbooks(req) -> tuple[int, dict]:
    query = req.query("q")
    hits = [{"doc": r.chunk.doc_id, "title": r.chunk.doc_title,
             "ref": r.ref, "score": r.score,
             "excerpt": r.chunk.text[:200]}
            for r in STATE.rag.search(query, k=5)]
    return 200, {"query": query, "results": hits}


def h_topology(req) -> tuple[int, dict]:
    return 200, build_topology(STATE.graph, STATE.alerts.by_service(),
                               STATE.rows(), focus=req.query("service"))


def h_stats(req) -> tuple[int, dict]:
    return 200, build_stats(STATE.rows(), STATE.alerts.stats(),
                            len(STATE.memory))


# ------------------------------------------------------------------ alerts
def _alerts_payload() -> dict:
    groups = STATE.alerts.groups()
    for group in groups:
        # Whether this deployment has evidence to investigate the origin.
        group["investigable"] = \
            _scenario_for_service(group["origin"]) is not None
    return {"stats": STATE.alerts.stats(), "groups": groups}


def h_alerts_list(req) -> tuple[int, dict]:
    return 200, _alerts_payload()


def h_alerts_ingest(req) -> tuple[int, dict]:
    try:
        alerts = normalize(req.body())
    except AlertValidationError as exc:
        raise ApiError(400, str(exc)) from exc
    outcome = STATE.alerts.ingest(alerts)
    return 202, {**outcome, "stats": STATE.alerts.stats()}


def h_alerts_clear(req) -> tuple[int, dict]:
    STATE.alerts.clear()
    return 200, _alerts_payload()


def h_alerts_investigate(req, group_id: str) -> tuple[int, dict]:
    group = STATE.alerts.group(group_id)
    if group is None:
        raise ApiError(404, "unknown alert group")
    if group["incident_id"]:
        raise ApiError(409, "alert group is already under investigation",
                       incident_id=group["incident_id"])
    scenario = _scenario_for_service(group["origin"])
    if scenario is None:
        raise ApiError(
            422, f"no evidence source for service {group['origin']!r}: the "
                 "connectors are snapshot fakes (see GET /v1/connectors) "
                 "and no frozen snapshot covers it")
    is_async = bool(req.body().get("async"))
    return _start_incident(scenario, is_async, alert_group_id=group_id)


# --------------------------------------------------------------- incidents
def _start_incident(scenario, is_async: bool,
                    alert_group_id: str | None = None) -> tuple[int, dict]:
    if is_async:
        # Backpressure BEFORE doing any work, so a rejected request
        # creates no entry to leak (audit M2).
        with STATE.state_lock:
            if STATE.async_inflight >= MAX_ASYNC_INFLIGHT:
                raise ApiError(429, "too many incidents in flight; "
                                    "retry shortly")
            STATE.async_inflight += 1

    incident, env = (build_environment(scenario) if scenario is not None
                     else build_demo_environment())
    if alert_group_id is not None and not STATE.alerts.attach_incident(
            alert_group_id, incident.id):
        # Lost the race to another operator: do not run a second
        # investigation for the same group.
        if is_async:
            with STATE.state_lock:
                STATE.async_inflight -= 1
        raise ApiError(409, "alert group is already under investigation")

    if STATE.db_path:
        # Persistence mode: incidents read AND write the shared, durable
        # organizational memory, so the learning flywheel spans requests
        # and survives a restart (Phase K). Default mode keeps each
        # incident's memory isolated (test-stable).
        env["memory"] = STATE.memory
        # ...and this incident's audit chain persists to JSONL (M).
        env["audit"].attach_path(
            os.path.join(STATE.audit_dir, f"{incident.id}.jsonl"))
    entry = {"incident": incident, "env": env, "run": None, "ctx": None,
             "view": None, "feedback": None, "fence": uuid.uuid4().hex,
             "scenario": scenario.id if scenario is not None else None,
             "alert_group_id": alert_group_id}
    with STATE.state_lock:          # insert atomic vs. evict (audit M3)
        STATE.incidents[incident.id] = entry
    STATE.evict()

    def _execute():
        try:
            run, ctx = run_incident_remediation(incident, **env)
            with STATE.lock_for(incident.id):
                entry["run"], entry["ctx"] = run, ctx
                STATE.refresh(entry)
        finally:
            if is_async:
                with STATE.state_lock:
                    STATE.async_inflight -= 1

    if is_async:
        STATE.pool.submit(_execute)     # 202 now, poll via GET
        return 202, {**running_view(entry), "fence": entry["fence"]}
    _execute()
    return 201, {**entry["view"], "fence": entry["fence"]}


def h_incidents_create(req) -> tuple[int, dict]:
    body = req.body()
    # Optional scenario selection (Phase P); default is the canonical
    # incident, so existing behavior is unchanged. Resolve BEFORE the
    # async backpressure so an invalid id can't leak the counter.
    scenario_id = body.get("scenario")
    scenario = None
    if scenario_id:
        scenario = next((s for s in all_scenarios()
                         if s.id == scenario_id), None)
        if scenario is None:
            raise ApiError(400, f"unknown scenario {scenario_id!r}")
    return _start_incident(scenario, bool(body.get("async")))


def h_incidents_list(req) -> tuple[int, dict]:
    rows = STATE.rows()
    outcome = req.query("outcome")
    if outcome:
        rows = [r for r in rows if r.get("outcome") == outcome]
    try:
        limit = max(1, min(200, int(req.query("limit") or 50)))
    except ValueError as exc:
        raise ApiError(400, "limit must be an integer") from exc
    return 200, {"count": len(rows), "incidents": rows[:limit]}


def _archived(incident_id: str) -> dict | None:
    if STATE.archive is None:
        return None
    view = STATE.archive.get(incident_id)
    return {**view, "archived": True} if view else None


def h_incident_get(req, incident_id: str) -> tuple[int, dict]:
    entry = STATE.incidents.get(incident_id)
    if entry is None:
        view = _archived(incident_id)
        if view is None:
            raise ApiError(404, "unknown incident")
        return 200, view
    if entry["run"] is None:            # async run still executing
        return 200, {**running_view(entry), "fence": entry["fence"]}
    return 200, {**entry["view"], "fence": entry["fence"]}


def h_incident_audit(req, incident_id: str) -> tuple[int, dict]:
    # Governance made visible (audit H3a/H7): fetch an incident's
    # hash-chained ledger and its verification status. The "recorded,
    # tamper-evident" claim is only real if it is reachable.
    entry = STATE.incidents.get(incident_id)
    persisted = (os.path.join(STATE.audit_dir, f"{incident_id}.jsonl")
                 if STATE.audit_dir else None)
    if entry is not None:
        audit = entry["env"]["audit"]
    elif (persisted and re.fullmatch(r"[A-Za-z0-9_-]+", incident_id)
          and os.path.exists(persisted)):
        audit = AuditLog.load(persisted)     # reloaded after a restart
    else:
        raise ApiError(404, "unknown incident")
    return 200, {
        "incident_id": incident_id,
        "count": len(audit.records),
        "chain_verified": audit.verify(),
        "records": [{"seq": r.seq, "ts": r.ts, "actor": r.actor,
                     "action": r.action, "payload": r.payload,
                     "prev_hash": r.prev_hash, "hash": r.hash}
                    for r in audit.records]}


def h_incident_postmortem(req, incident_id: str) -> tuple[int, dict]:
    entry = STATE.incidents.get(incident_id)
    if entry is None or entry["run"] is None:
        raise ApiError(404, "unknown incident" if entry is None
                       else "incident is still running")
    postmortem = entry["run"].checkpoint.get("postmortem")
    if not postmortem:
        raise ApiError(404, "no postmortem: one is generated only after a "
                            "verified remediation")
    return 200, {"incident_id": incident_id,
                 "markdown": postmortem["markdown"],
                 "follow_ups": postmortem["follow_ups"]}


def h_incident_approve(req, incident_id: str) -> tuple[int, dict]:
    entry = STATE.incidents.get(incident_id)
    if entry is None:
        if _archived(incident_id) is not None:
            raise ApiError(409, "incident was restored from the archive: "
                                "its paused workflow state did not survive "
                                "the restart — re-trigger it")
        raise ApiError(404, "unknown incident")
    body = req.body()
    decision = body.get("decision")
    if decision not in ("approve", "deny"):
        raise ApiError(400, "decision must be 'approve' or 'deny'")
    # Atomic check-then-act: without the lock, two racing approvals
    # both observe PAUSED and the remediation executes twice. The
    # fence token (docs/12) rejects decisions made against a stale view
    # of the incident.
    with STATE.lock_for(incident_id):
        # Fence is REQUIRED (audit M6): a decision must echo the
        # current token, so an approval made against a stale view of
        # the incident is rejected rather than silently accepted.
        if body.get("fence") != entry["fence"]:
            raise ApiError(409, "missing or stale fence token — re-read "
                                "the incident")
        run = entry["run"]
        if run is None:
            raise ApiError(409, "incident still running")
        if run.status != WorkflowStatus.PAUSED:
            raise ApiError(409, "incident is not awaiting approval")
        run, ctx = run_incident_remediation(
            entry["incident"], **entry["env"], ctx=entry["ctx"],
            approvals={run.pending_gate: decision == "approve"},
            checkpoint=run.checkpoint)
        entry["run"], entry["ctx"] = run, ctx
        entry["fence"] = uuid.uuid4().hex
        view = STATE.refresh(entry)
    return 200, {**view, "fence": entry["fence"]}


def h_incident_feedback(req, incident_id: str) -> tuple[int, dict]:
    """Human verdict on a diagnosis. It is written to the incident's audit
    chain and — when the incident learned an episode — into memory: an
    episode an operator marks incorrect stops counting as verified
    precedent for recall and change-risk scoring (audit H4)."""
    entry = STATE.incidents.get(incident_id)
    if entry is None:
        raise ApiError(404, "unknown incident")
    body = req.body()
    verdict = body.get("verdict")
    if verdict not in ("correct", "incorrect"):
        raise ApiError(400, "verdict must be 'correct' or 'incorrect'")
    note = body.get("note", "")
    if not isinstance(note, str):
        raise ApiError(400, "note must be a string")
    note = note.strip()[:MAX_NOTE_CHARS]
    role = req._role()

    with STATE.lock_for(incident_id):
        if entry["run"] is None:
            raise ApiError(409, "incident still running")
        ctx, env = entry["ctx"], entry["env"]
        if "root_cause" not in ctx.results:
            raise ApiError(409, "incident has no diagnosis to assess")
        env["audit"].append(actor=f"human:{role}", action="human.feedback",
                            payload={"verdict": verdict, "note": note})
        memory_updated = False
        episode_id = (ctx.params.get("episode") or {}).get("episode_id")
        if episode_id:
            episode = env["memory"].get(episode_id) or {}
            machine = episode.get("machine_verified",
                                  episode.get("verified", False))
            memory_updated = env["memory"].update(episode_id, {
                "machine_verified": machine,
                "human_verdict": verdict,
                "verified": bool(machine) and verdict == "correct"})
        entry["feedback"] = {"verdict": verdict, "note": note, "role": role,
                             "at": time.time(),
                             "memory_updated": memory_updated}
        view = STATE.refresh(entry)
    return 200, {**view, "fence": entry["fence"]}


def h_change_score(req) -> tuple[int, dict]:
    body = req.body()
    change = ChangeEvent(
        id=new_id("chg"),
        service=str(body.get("service", "orders-service"))[:120],
        sha=str(body.get("sha", "0000000"))[:64],
        title=str(body.get("title", ""))[:300],
        diff=str(body.get("diff", ""))[:20_000],
        labels={"peak_window": bool(body.get("peak_window", False)),
                "freeze": bool(body.get("freeze", False))})
    audit = AuditLog()
    run, ctx = run_change_risk(
        change, gateway=ModelGateway(audit=audit), audit=audit,
        memory=STATE.memory, policy=PolicyEngine(), graph=STATE.graph)
    verdict = run.checkpoint.get("score", {})
    decision = run.checkpoint.get("policy_check", {})
    intel = ctx.results.get("change_intel")
    return 200, {
        "change_id": change.id,
        "service": change.service,
        "status": run.status.value,
        "score": verdict.get("score"),
        "band": verdict.get("band"),
        "components": verdict.get("components"),
        "rationale": verdict.get("rationale"),
        "requires_approval": decision.get("requires_approval"),
        "canary_required": decision.get("canary_required"),
        "policy_rule": decision.get("rule_id"),
        "policy_reason": decision.get("reason"),
        "dependents": intel.output.get("dependents", []) if intel else [],
        "matched_episodes": [
            {"ref": e.citation.ref, "summary": e.summary[:200]}
            for e in ctx.evidence if e.kind == "episode"],
        "error": run.error}


# ------------------------------------------------------------- route table
@dataclass(frozen=True)
class Route:
    method: str
    path: str                   # template; {name} captures one segment
    grant: str | None           # policy grant, ANY_ROLE, or None = public
    handler: Callable
    summary: str
    tag: str = "platform"
    max_body: int = MAX_BODY_BYTES

    @property
    def pattern(self) -> re.Pattern:
        return _compile(self.path)


_PATTERNS: dict[str, re.Pattern] = {}


def _compile(template: str) -> re.Pattern:
    if template not in _PATTERNS:
        _PATTERNS[template] = re.compile(
            re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", template))
    return _PATTERNS[template]


ROUTES: list[Route] = [
    Route("GET", "/health", None, h_health,
          "Liveness and build info"),
    Route("GET", "/v1/whoami", ANY_ROLE, h_whoami,
          "The caller's role and policy grants"),
    Route("GET", "/v1/openapi.json", ANY_ROLE, h_openapi,
          "This API as an OpenAPI 3.1 document"),

    Route("GET", "/v1/alerts", "read", h_alerts_list,
          "Correlated alert groups and noise-reduction stats", "alerts"),
    Route("POST", "/v1/alerts", "ingest", h_alerts_ingest,
          "Ingest alerts (native shape or Alertmanager webhook)", "alerts",
          max_body=MAX_ALERT_BODY_BYTES),
    Route("DELETE", "/v1/alerts", "create", h_alerts_clear,
          "Clear the alert store", "alerts"),
    Route("POST", "/v1/alerts/groups/{group_id}/investigate", "create",
          h_alerts_investigate,
          "Open an incident for an alert group's probable origin", "alerts"),

    Route("GET", "/v1/incidents", "read", h_incidents_list,
          "Incident history, newest first", "incidents"),
    Route("POST", "/v1/incidents", "create", h_incidents_create,
          "Start an incident and run it to the approval gate", "incidents"),
    Route("GET", "/v1/incidents/{incident_id}", "read", h_incident_get,
          "The full incident document", "incidents"),
    Route("GET", "/v1/incidents/{incident_id}/audit", "read",
          h_incident_audit,
          "Hash-chained audit ledger with live verification", "incidents"),
    Route("GET", "/v1/incidents/{incident_id}/postmortem", "read",
          h_incident_postmortem, "The generated postmortem", "incidents"),
    Route("POST", "/v1/incidents/{incident_id}/approvals", "approve",
          h_incident_approve,
          "Approve or deny at the gate (fenced, atomic)", "incidents"),
    Route("POST", "/v1/incidents/{incident_id}/feedback", "feedback",
          h_incident_feedback,
          "Mark the diagnosis correct or incorrect", "incidents"),

    Route("POST", "/v1/changes/score", "create", h_change_score,
          "Score a change for deploy risk", "changes"),
    Route("GET", "/v1/topology", "read", h_topology,
          "Service map with alert pressure and blast radius"),
    Route("GET", "/v1/stats", "read", h_stats,
          "Fleet-level incident and alert reporting"),
    Route("GET", "/v1/catalog", "read", h_catalog,
          "Step Catalog, pipeline stages, role grants, pinned prompts"),
    Route("GET", "/v1/scenarios", "read", h_scenarios,
          "Golden scenarios the API can run"),
    Route("GET", "/v1/connectors", "read", h_connectors,
          "Which connector slots are real adapters vs fakes"),
    Route("GET", "/v1/evals", "read", h_evals,
          "Golden-scenario and retrieval evaluation"),
    Route("GET", "/v1/runbooks/search", "read", h_runbooks,
          "Attributed runbook search"),
]


def openapi_document() -> dict:
    paths: dict[str, dict] = {}
    for route in ROUTES:
        operation = {
            "summary": route.summary, "tags": [route.tag],
            "responses": {"200": {"description": "JSON response"}},
        }
        params = re.findall(r"\{(\w+)\}", route.path)
        if params:
            operation["parameters"] = [
                {"name": name, "in": "path", "required": True,
                 "schema": {"type": "string"}} for name in params]
        if route.grant is None:
            operation["security"] = []
        else:
            operation["x-required-grant"] = route.grant
            if route.grant != ANY_ROLE:
                operation["x-roles"] = sorted(
                    PolicyEngine.API_ROLE_GRANTS.get(route.grant, ()))
        paths.setdefault(route.path, {})[route.method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": "AetherOps", "version": __version__,
                 "description": "Autonomous incident remediation and "
                                "change intelligence."},
        "security": [{"bearer": []}],
        "components": {"securitySchemes": {
            "bearer": {"type": "http", "scheme": "bearer"}}},
        "paths": paths,
    }


# ------------------------------------------------------------- the console
_SCRIPT_TAG = re.compile(rb"<script\b([^>]*)>(.*?)</script>", re.S)
_UI_CACHE: dict = {}


def _console() -> tuple[bytes, str]:
    """The console document and a Content-Security-Policy that admits
    exactly the scripts it ships: each inline script is allowed by its
    sha256, so an injected script cannot run. Everything else is locked to
    same-origin, and the page cannot be framed."""
    path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    mtime = os.path.getmtime(path)
    if _UI_CACHE.get("mtime") != mtime:
        with open(path, "rb") as fh:
            body = fh.read()
        hashes = []
        for attrs, script in _SCRIPT_TAG.findall(body):
            if b"src=" in attrs:
                continue            # external scripts are never admitted
            digest = base64.b64encode(hashlib.sha256(script).digest())
            hashes.append(f"'sha256-{digest.decode()}'")
        csp = "; ".join([
            "default-src 'none'",
            f"script-src {' '.join(hashes) or chr(39) + 'none' + chr(39)}",
            "style-src 'unsafe-inline'",        # React inline style props
            "img-src 'self' data:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "base-uri 'none'", "form-action 'none'",
            "frame-ancestors 'none'"])
        _UI_CACHE.update(mtime=mtime, body=body, csp=csp)
    return _UI_CACHE["body"], _UI_CACHE["csp"]


class Handler(BaseHTTPRequestHandler):
    server_version = f"AetherOps/{__version__}"

    # ------------------------------------------------------------------ util
    def _send(self, code: int, obj: dict, headers: dict | None = None) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-Id", getattr(self, "_request_id", "-"))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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
            self._send(401, {"error": "missing or invalid bearer token"},
                       {"WWW-Authenticate": "Bearer"})
            return False
        if action != ANY_ROLE and not PolicyEngine.role_allows(role, action):
            self._send(403, {"error": f"role {role!r} may not {action}"})
            return False
        return True

    def _send_ui(self) -> None:
        """The operator console — a single self-contained page served at the
        API root, so `make serve` / `docker run` yields a clickable UI that
        drives the real endpoints (docs/17 Milestone 13)."""
        body, csp = _console()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def query(self, name: str) -> str:
        return parse_qs(urlparse(self.path).query).get(name, [""])[0]

    def body(self) -> dict:
        """The request's JSON object (parsed once). An absent body is {};
        valid JSON that is not an object is coerced to {} (callers do
        body.get — audit L2). Unparseable JSON is a 400, never a silent
        default: a mangled approval must not read as an empty request."""
        if hasattr(self, "_parsed_body"):
            return self._parsed_body
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:              # malformed Content-Length -> no body
            length = 0
        limit = getattr(self, "_max_body", MAX_BODY_BYTES)
        if length > limit:
            # Refused, not silently dropped: an oversized approval must not
            # degrade into "a request with no decision".
            self.close_connection = True
            raise ApiError(413, f"request body exceeds {limit} bytes")
        parsed: object = {}
        if length > 0:
            try:
                parsed = json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ApiError(400, "request body is not valid JSON") \
                    from exc
        self._parsed_body = parsed if isinstance(parsed, dict) else {}
        return self._parsed_body

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
            "request_id": getattr(self, "_request_id", "-"),
            "latency_ms": round(
                (time.monotonic() - getattr(self, "_t0", time.monotonic()))
                * 1000, 1)}), flush=True)

    def log_message(self, format, *args):       # noqa: A002 (stdlib name)
        """Silence the stdlib's unstructured stderr lines; the structured
        access log above is the only request log."""

    # ------------------------------------------------------------- dispatch
    def _dispatch(self, method: str) -> None:
        self._t0 = time.monotonic()
        self._request_id = uuid.uuid4().hex[:12]
        self.__dict__.pop("_parsed_body", None)     # keep-alive: per request
        path = urlparse(self.path).path
        if method == "GET" and path in UI_PATHS:
            return self._send_ui()

        allowed = []
        for route in ROUTES:
            match = route.pattern.fullmatch(path)
            if match is None:
                continue
            if route.method != method:
                allowed.append(route.method)
                continue
            if route.grant is not None and not self._require(route.grant):
                return
            if method != "GET":
                wait = STATE.limiter.check(self._role() or "anon")
                if wait:
                    return self._send(
                        429, {"error": "rate limit exceeded; retry shortly"},
                        {"Retry-After": str(int(wait) + 1)})
            self._max_body = route.max_body
            try:
                status, payload = route.handler(self, **match.groupdict())
            except ApiError as exc:
                return self._send(exc.status, exc.payload)
            except Exception:
                # A bug, not a refusal. The caller gets a stable 500 that
                # names the request (never the internals); the operator
                # gets the traceback, keyed by the same id.
                print(json.dumps({
                    "level": "error", "request_id": self._request_id,
                    "method": method, "path": path,
                    "traceback": traceback.format_exc()}),
                    file=sys.stderr, flush=True)
                return self._send(500, {"error": "internal error",
                                        "request_id": self._request_id})
            return self._send(status, payload)

        # No route for this method + path. Unknown routes still demand a
        # token, so the API's shape is not enumerable anonymously.
        if self._role() is None:
            return self._send(401, {"error": "missing or invalid bearer "
                                             "token"},
                              {"WWW-Authenticate": "Bearer"})
        if allowed:
            return self._send(405, {"error": f"{method} not allowed here"},
                              {"Allow": ", ".join(sorted(set(allowed)))})
        self._send(404, {"error": "unknown route"})

    def _serve(self, method: str) -> None:
        try:
            self._dispatch(method)
        except (BrokenPipeError, ConnectionResetError):
            # The client hung up before reading its response. There is
            # nobody left to answer and nothing went wrong on this side.
            self.close_connection = True

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def do_DELETE(self):
        self._serve("DELETE")


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
                      "console": f"http://{bind}:{port}/",
                      "auth": "Authorization: Bearer $AETHEROPS_API_TOKEN"}))
    server.serve_forever()
