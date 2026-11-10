"""Read models for the API: JSON projections of one incident's record
(docs/20-operator-console.md §4).

Every function here is a pure projection — context, run and audit ledger in,
plain dict out. Nothing appends to the ledger, calls a connector or a model,
so a view can be rebuilt on any state change and cached without side effects.
The transport (api/server.py) stays a thin router; the MCP server reuses the
same projections, so an IDE client and the console read identical shapes.

Humans keep full visibility here: classification is shown as a badge on each
evidence item, never used to withhold it. Only model prompts are gated
(core/context.py).
"""
from __future__ import annotations

from aetherops.reasoning.hypotheses import build_hypothesis_tree, gather_facts
from aetherops.workflows.incident_remediation import build_workflow

STAGE_LABELS = {
    "triage": "Triage", "gather_evidence": "Investigate",
    "security_screen": "Screen", "diagnose": "Diagnose", "plan": "Plan",
    "review": "Review", "policy_check": "Policy",
    "approval_gate": "Approval", "execute": "Execute", "verify": "Verify",
    "learn": "Learn", "postmortem": "Postmortem",
}


def outcome_of(run) -> str:
    """One word for where an incident ended up — the console's primary
    status. `escalated` is a correct outcome, not a failure
    (docs/11 §8): the platform declined to act on evidence it could not
    ground."""
    if run is None:
        return "running"
    status = run.status.value
    if status == "PAUSED":
        return "awaiting-approval"
    if status == "SUCCEEDED":
        return "remediated"
    error = (run.error or "").lower()
    if "approval denied" in error:
        return "denied"
    if "escalate" in error:
        return "escalated"
    return "failed"


def _first(records, action: str, node: str | None = None):
    for record in records:
        if record.action == action and (
                node is None or record.payload.get("node") == node):
            return record
    return None


def build_stages(run, records) -> list[dict]:
    """The pipeline as an ordered stepper: one entry per DAG node with its
    state and measured duration, read off the ledger (so it also renders
    for a run that is still executing)."""
    done = {r.payload.get("node"): r for r in records
            if r.action == "node.succeeded"}
    failed = {r.payload.get("node"): r for r in records
              if r.action in ("node.failed", "node.timeout")}
    gates = {r.payload.get("node"): r.action for r in records
             if r.action.startswith("gate.")}
    pending_gate = getattr(run, "pending_gate", None) if run else None
    completed = set(getattr(run, "completed", []) or []) if run else set()

    stages, blocked = [], False
    for node in build_workflow():
        name = node.name
        entry = {"id": name, "label": STAGE_LABELS.get(name, name),
                 "gate": node.gate is not None, "state": "pending"}
        if name in failed:
            entry["state"] = "failed"
            entry["error"] = failed[name].payload.get("error", "timed out")
            blocked = True
        elif gates.get(name) == "gate.denied":
            entry["state"] = "denied"
            blocked = True
        elif name == pending_gate:
            entry["state"] = "paused"
            blocked = True
        elif name in done or name in completed:
            entry["state"] = "done"
            if name in done:
                entry["duration_ms"] = done[name].payload.get("duration_ms")
        elif blocked:
            entry["state"] = "skipped"
        stages.append(entry)
    return stages


def _humanize(record) -> tuple[str, str, str]:
    """(kind, title, detail) for one ledger record."""
    action, p = record.action, record.payload
    node = p.get("node", "")
    if action == "workflow.start":
        return "workflow", "Workflow started", p.get("workflow", "")
    if action == "tool.call":
        cached = " (cached)" if p.get("cached") else ""
        redacted = (f" · {len(p['redactions'])} redaction(s)"
                    if p.get("redactions") else "")
        return ("write" if p.get("risk") not in ("READ", None) else "tool",
                f"{p.get('system')}.{p.get('tool')}{cached}",
                f"risk {p.get('risk')}{redacted}")
    if action == "tool.denied":
        return ("denied", f"{p.get('system')}.{p.get('tool')} denied",
                p.get("reason", ""))
    if action == "model.call":
        return ("model", f"Model call · {p.get('task')}",
                f"{p.get('served_model')} via {p.get('backend')} · "
                f"{p.get('tokens_in', 0)}→{p.get('tokens_out', 0)} tok · "
                f"{p.get('latency_ms', 0)} ms"
                + (f" · {p['prompt']}" if p.get("prompt") else ""))
    if action == "agent.decision":
        return ("decision", f"Chose {p.get('action')}",
                p.get("rationale", ""))
    if action == "node.succeeded":
        return ("stage", f"{STAGE_LABELS.get(node, node)} completed",
                f"{p.get('duration_ms', 0)} ms")
    if action == "node.failed":
        return ("failed", f"{STAGE_LABELS.get(node, node)} failed",
                p.get("error", ""))
    if action == "node.timeout":
        return ("failed", f"{STAGE_LABELS.get(node, node)} timed out",
                f"budget {p.get('timeout_s')}s")
    if action == "node.retry":
        return ("retry", f"{STAGE_LABELS.get(node, node)} retried",
                p.get("error", ""))
    if action == "gate.paused":
        return ("gate", "Paused for approval",
                f"{p.get('risk')} risk — {p.get('reason', '')}")
    if action == "gate.approved":
        return "gate", "Approval granted", f"{p.get('risk')} risk"
    if action == "gate.denied":
        return "denied", "Approval denied", "nothing executed"
    if action == "plan.proposed":
        return ("decision", "Plan proposed",
                " → ".join(p.get("actions", [])))
    if action in ("plan.fallback", "investigate.fallback"):
        return ("fallback", "Fell back to the deterministic baseline",
                p.get("error", ""))
    if action == "backend.fallback":
        return ("fallback",
                f"Model backend {p.get('failed_backend')} failed",
                f"continued on {p.get('next_backend')}")
    if action == "human.feedback":
        return ("human", f"Operator marked the diagnosis {p.get('verdict')}",
                p.get("note", ""))
    if action.startswith("compensation") or action == "node.compensated":
        return "compensation", action.replace(".", " "), p.get("error", node)
    return "other", action, ""


def build_timeline(records) -> list[dict]:
    """The investigation, step by step, in ledger order. `t_ms` is the
    offset from the first record so the console can show pacing without
    trusting a wall clock."""
    if not records:
        return []
    start = records[0].ts
    timeline = []
    for record in records:
        kind, title, detail = _humanize(record)
        timeline.append({
            "seq": record.seq, "ts": record.ts,
            "t_ms": round((record.ts - start) * 1000, 1),
            "actor": record.actor, "action": record.action,
            "kind": kind, "title": title, "detail": str(detail)[:240]})
    return timeline


def build_signals(ctx, run) -> dict | None:
    """The symptom on one time axis: the latency series with the deploy and
    pod events laid over it — what a runbook tells an operator to draw
    first."""
    facts = gather_facts(ctx.evidence)
    if not facts.series:
        return None
    deploy = facts.deploy
    signals = {
        "metric": "p99 latency", "unit": "ms",
        "series": [{"ts": p.get("ts"), "value": p.get("p99_ms")}
                   for p in facts.series],
        "onset": facts.onset,
        "deploy": ({"ts": deploy.get("deployed_at"),
                    "revision": deploy.get("revision"),
                    "previous_revision": deploy.get("previous_revision"),
                    "commit": (facts.commit or {}).get("sha")}
                   if deploy else None),
        "events": [{"ts": e.get("ts"), "reason": e.get("reason"),
                    "pod": e.get("pod")} for e in (facts.events or [])],
        "refs": {"series": facts.ref("metrics"),
                 "deploy": facts.ref("deploy"),
                 "events": facts.ref("k8s-event")},
    }
    verify = run.checkpoint.get("verify") if run is not None else None
    if verify:
        signals["recovered"] = {
            "value": verify.get("output", {}).get("p99_ms"),
            "recovered": verify.get("output", {}).get("recovered")}
    return signals


def _evidence_view(ctx) -> list[dict]:
    return [{"id": f"E{n}", "kind": e.kind, "source": e.citation.source,
             "ref": e.citation.ref, "summary": e.summary[:140],
             "excerpt": e.citation.excerpt[:240],
             "classification": e.classification}
            for n, e in enumerate(ctx.evidence, start=1)]


def _similar(ctx) -> list[dict]:
    return [{"ref": e.citation.ref, "summary": e.citation.excerpt[:200],
             "evidence": f"E{n}"}
            for n, e in enumerate(ctx.evidence, start=1)
            if e.kind == "episode"]


def incident_view(entry: dict, graph=None) -> dict:
    """The full incident document the console renders. Keys present before
    the console redesign keep their names and meaning; everything else is
    additive."""
    incident, run, ctx, env = (entry["incident"], entry["run"],
                               entry["ctx"], entry["env"])
    audit = env["audit"]
    records = audit.records
    rca = ctx.results.get("root_cause")
    planner = ctx.results.get("planner")
    knowledge = ctx.results.get("knowledge")
    verdict = run.checkpoint.get("policy_check") or {}

    view = {
        "incident_id": incident.id,
        "title": incident.title,
        "service": incident.service,
        "severity": incident.severity.value,
        "environment": incident.environment,
        "created_at": incident.created_at,
        "scenario": entry.get("scenario"),
        "alert_group_id": entry.get("alert_group_id"),
        "status": run.status.value,
        "outcome": outcome_of(run),
        "pending_gate": run.pending_gate,
        "evidence_count": len(ctx.evidence),
        "tokens": env["gateway"].tokens_used,
        "est_cost_usd": env["gateway"].est_cost_usd,
        "archived": False,
    }
    if rca is not None:
        diagnosed = rca.output.get("status") == "diagnosed"
        view["diagnosis"] = {
            "status": rca.output.get("status"),
            "failure_class": rca.output.get("failure_class"),
            "suspect_commit": rca.output.get("suspect_commit"),
            "confidence": round(rca.confidence, 3),
            # Whether the platform still stands behind this diagnosis. A
            # diagnosis the Reviewer (or verification) escalated is a model
            # proposal that was REJECTED: it stays on record, but lists and
            # reports must not count it as what was wrong.
            "standing": diagnosed and view["outcome"] not in ("escalated",
                                                              "failed"),
        }
        view["hypothesis"] = rca.output.get("hypothesis", "")
    if verdict:
        view["approval_tier"] = verdict.get("approval_tier")
        view["policy"] = {
            "allowed": verdict.get("allowed"),
            "requires_approval": verdict.get("requires_approval"),
            "approval_tier": verdict.get("approval_tier"),
            "decisions": verdict.get("decisions", [])}
    if run.error:
        view["error"] = run.error

    view["agents"] = [
        {"name": name, "confidence": round(result.confidence, 2),
         "model": result.model_id, "tokens": result.tokens,
         "citations": len(result.citations)}
        for name, result in ctx.results.items()]
    view["evidence"] = _evidence_view(ctx)
    if planner is not None:
        view["plan"] = [
            {"action": s["action"], "risk": s["risk"], "args": s["args"],
             "compensable": s.get("compensable", True)}
            for s in planner.output.get("steps", [])]
        view["plan_rationale"] = planner.output.get("rationale", "")
    if knowledge is not None:
        view["investigation"] = knowledge.output.get("investigation", {})
        view["gaps"] = knowledge.output.get("gaps", [])

    view["stages"] = build_stages(run, records)
    view["timeline"] = build_timeline(records)
    view["hypotheses"] = build_hypothesis_tree(ctx, run)
    view["signals"] = build_signals(ctx, run)
    view["review_checks"] = ctx.params.get("review_checks", [])
    view["similar_incidents"] = _similar(ctx)
    if graph is not None:
        view["blast_radius"] = sorted(graph.dependents(incident.service))

    executed = ctx.params.get("executed") or []
    if executed:
        view["executed"] = [
            {"action": r["action"],
             "dry_run": r["result"].get("dry_run"),
             "compensable": bool(r["result"].get("undo")),
             "result": {k: v for k, v in r["result"].items()
                        if k not in ("undo", "dry_run")}}
            for r in executed]
    verifier = ctx.results.get("verifier")
    if verifier is not None:
        view["verification"] = dict(verifier.output)
    episode = ctx.params.get("episode")
    if episode:
        view["episode_id"] = episode.get("episode_id")
    postmortem = run.checkpoint.get("postmortem")
    view["postmortem_available"] = bool(postmortem)
    if postmortem:
        view["postmortem_excerpt"] = postmortem["markdown"][:400]
        view["follow_ups"] = postmortem["follow_ups"]

    start = _first(records, "workflow.start")
    diagnosed = _first(records, "node.succeeded", "diagnose")
    if start is not None and diagnosed is not None:
        view["time_to_diagnosis_ms"] = round(
            (diagnosed.ts - start.ts) * 1000, 1)
    if records:
        view["elapsed_ms"] = round((records[-1].ts - records[0].ts) * 1000, 1)
    view["audit"] = {"count": len(records), "chain_verified": audit.verify()}
    view["feedback"] = entry.get("feedback")
    return view


ROW_KEYS = ("incident_id", "title", "service", "severity", "status",
            "outcome", "created_at", "scenario", "alert_group_id",
            "pending_gate", "evidence_count", "tokens", "est_cost_usd",
            "elapsed_ms", "time_to_diagnosis_ms", "archived")


def incident_row(view: dict) -> dict:
    """The list projection of an incident view."""
    row = {key: view.get(key) for key in ROW_KEYS}
    diagnosis = view.get("diagnosis") or {}
    standing = diagnosis.get("standing",
                             diagnosis.get("status") == "diagnosed")
    row["failure_class"] = diagnosis.get("failure_class") if standing else None
    row["confidence"] = diagnosis.get("confidence") if standing else None
    row["feedback"] = (view.get("feedback") or {}).get("verdict")
    return row


def running_view(entry: dict) -> dict:
    """An async incident whose workflow has not returned yet: identity plus
    live stage progress read from the ledger so far."""
    incident = entry["incident"]
    records = entry["env"]["audit"].records
    return {"incident_id": incident.id, "title": incident.title,
            "service": incident.service,
            "severity": incident.severity.value,
            "created_at": incident.created_at,
            "scenario": entry.get("scenario"),
            "alert_group_id": entry.get("alert_group_id"),
            "status": "RUNNING", "outcome": "running", "archived": False,
            "stages": build_stages(None, records),
            "timeline": build_timeline(records)}


def _mean(values: list) -> float | None:
    values = [v for v in values if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 1) if values else None


def build_stats(rows: list[dict], alert_stats: dict, episodes: int) -> dict:
    """Fleet-level reporting over the incidents the API has run."""
    def tally(key: str) -> dict:
        out: dict[str, int] = {}
        for row in rows:
            value = row.get(key)
            if value:
                out[value] = out.get(value, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    finished = [r for r in rows if r.get("outcome") != "running"]
    diagnosed = [r for r in finished if r.get("failure_class")]
    correct = sum(r.get("feedback") == "correct" for r in rows)
    incorrect = sum(r.get("feedback") == "incorrect" for r in rows)
    return {
        "incidents": {
            "total": len(rows),
            "by_outcome": tally("outcome"),
            "by_failure_class": tally("failure_class"),
            "by_service": tally("service"),
        },
        "diagnosis_rate": round(len(diagnosed) / len(finished), 3)
        if finished else None,
        "mean_time_to_diagnosis_ms": _mean(
            [r.get("time_to_diagnosis_ms") for r in finished]),
        "mean_elapsed_ms": _mean([r.get("elapsed_ms") for r in finished]),
        "tokens": sum(r.get("tokens") or 0 for r in rows),
        "est_cost_usd": round(sum(r.get("est_cost_usd") or 0.0
                                  for r in rows), 6),
        "feedback": {
            "correct": correct, "incorrect": incorrect,
            "acceptance": round(correct / (correct + incorrect), 3)
            if (correct + incorrect) else None},
        "alerts": alert_stats,
        "memory": {"episodes": episodes},
    }


def build_topology(graph, alert_pressure: dict, rows: list[dict],
                   focus: str | None = None) -> dict:
    """The service map: declared dependencies, with live alert pressure and
    open incidents overlaid. Services seen only in alerts or incidents are
    included as `known: false` — visible, but with no claimed topology."""
    open_incidents: dict[str, list[str]] = {}
    for row in rows:
        if row.get("outcome") in ("running", "awaiting-approval"):
            open_incidents.setdefault(row["service"], []).append(
                row["incident_id"])

    names = list(graph.services())
    for extra in sorted(set(alert_pressure) | set(open_incidents)):
        if extra not in names:
            names.append(extra)

    nodes = []
    for name in names:
        known = graph.knows(name)
        nodes.append({
            "id": name, "known": known,
            "depth": graph.depth(name) if known else 0,
            "dependents": len(graph.dependents(name)) if known else 0,
            "dependencies": len(graph.dependencies(name)) if known else 0,
            "alerts": alert_pressure.get(name),
            "incidents": open_incidents.get(name, [])})

    topology = {"nodes": nodes,
                "edges": [{"from": src, "to": target}
                          for src, target in graph.edges()],
                "focus": None}
    if focus and focus in names:
        known = graph.knows(focus)
        topology["focus"] = {
            "service": focus,
            "blast_radius": sorted(graph.dependents(focus)) if known else [],
            "upstream": sorted(graph.dependencies(focus)) if known else []}
    return topology
