"""FastAPI surface for AetherOps (optional extra).

The stdlib `http.server` API (`api/server.py`) is the zero-dependency default.
This exposes the same incident lifecycle as an idiomatic FastAPI app — Pydantic
request/response models, a Bearer-token auth dependency, typed handlers, and
auto-generated OpenAPI docs at /docs — for teams standardized on FastAPI +
Uvicorn.

    pip install "aetherops[api]"
    uvicorn aetherops.integrations.fastapi_app:app --port 8080

Opt-in and additive: the core never imports it, so CI stays stdlib-only.
"""
from __future__ import annotations

import os
import threading
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from aetherops.connectors.adapters import connector_roster
from aetherops.core.types import WorkflowStatus
from aetherops.demo import build_demo_environment
from aetherops.evals.scenarios import all_scenarios, build_environment
from aetherops.workflows.incident_remediation import run_incident_remediation

app = FastAPI(
    title="AetherOps",
    version="0.1.0",
    description="Autonomous incident-remediation & change-intelligence "
                "platform — FastAPI surface over the stdlib core.")

_INCIDENTS: dict[str, dict] = {}
_LOCK = threading.Lock()


# --- auth dependency --------------------------------------------------------
def require_token(authorization: str = Header(default="")) -> str:
    token = os.environ.get("AETHEROPS_API_TOKEN") or "aetherops-dev"
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return "admin"


# --- Pydantic models --------------------------------------------------------
class CreateIncident(BaseModel):
    scenario: str | None = Field(default=None,
                                 description="golden scenario id; default canonical")


class Decision(BaseModel):
    decision: str = Field(description="'approve' or 'deny'")
    fence: str | None = None


def _summary(entry: dict) -> dict:
    run, ctx = entry["run"], entry["ctx"]
    rca = ctx.results.get("root_cause")
    planner = ctx.results.get("planner")
    return {
        "incident_id": entry["incident"].id,
        "title": entry["incident"].title,
        "status": run.status.value,
        "pending_gate": run.pending_gate,
        "fence": entry["fence"],
        "diagnosis": ({"failure_class": rca.output.get("failure_class"),
                       "suspect_commit": rca.output.get("suspect_commit"),
                       "confidence": round(rca.confidence, 3)} if rca else None),
        "plan": ([s["action"] for s in planner.output["steps"]]
                 if planner else []),
        "error": run.error,
    }


# --- endpoints --------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "aetherops", "surface": "fastapi"}


@app.get("/v1/scenarios")
def scenarios(_: str = Depends(require_token)) -> dict:
    return {"scenarios": [
        {"id": s.id, "name": s.name, "expected_outcome": s.truth.outcome}
        for s in all_scenarios()]}


@app.get("/v1/connectors")
def connectors(_: str = Depends(require_token)) -> dict:
    return {"connectors": connector_roster()}


@app.post("/v1/incidents", status_code=201)
def create_incident(body: CreateIncident,
                    _: str = Depends(require_token)) -> dict:
    scenario = None
    if body.scenario:
        scenario = next((s for s in all_scenarios() if s.id == body.scenario),
                        None)
        if scenario is None:
            raise HTTPException(400, f"unknown scenario {body.scenario!r}")
    incident, env = (build_environment(scenario) if scenario
                     else build_demo_environment())
    run, ctx = run_incident_remediation(incident, **env)
    entry = {"incident": incident, "env": env, "run": run, "ctx": ctx,
             "fence": uuid.uuid4().hex}
    with _LOCK:
        _INCIDENTS[incident.id] = entry
    return _summary(entry)


@app.get("/v1/incidents/{incident_id}")
def get_incident(incident_id: str, _: str = Depends(require_token)) -> dict:
    entry = _INCIDENTS.get(incident_id)
    if entry is None:
        raise HTTPException(404, "unknown incident")
    return _summary(entry)


@app.post("/v1/incidents/{incident_id}/approvals")
def approve(incident_id: str, body: Decision,
            _: str = Depends(require_token)) -> dict:
    if body.decision not in ("approve", "deny"):
        raise HTTPException(400, "decision must be 'approve' or 'deny'")
    with _LOCK:
        entry = _INCIDENTS.get(incident_id)
        if entry is None:
            raise HTTPException(404, "unknown incident")
        if body.fence != entry["fence"]:                # fence REQUIRED
            raise HTTPException(409, "missing or stale fence token")
        run = entry["run"]
        if run.status != WorkflowStatus.PAUSED:
            raise HTTPException(409, "incident is not awaiting approval")
        run, ctx = run_incident_remediation(
            entry["incident"], **entry["env"], ctx=entry["ctx"],
            approvals={run.pending_gate: body.decision == "approve"},
            checkpoint=run.checkpoint)
        entry["run"], entry["ctx"] = run, ctx
        entry["fence"] = uuid.uuid4().hex
    response = _summary(entry)
    postmortem = run.checkpoint.get("postmortem")
    if postmortem:
        response["postmortem_excerpt"] = postmortem["markdown"][:400]
    return response
