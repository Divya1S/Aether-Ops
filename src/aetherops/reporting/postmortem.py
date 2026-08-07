"""Postmortem builder (docs/00-executive-summary.md §4, pillar 4).

The document is assembled deterministically from the workflow's own record —
evidence bundle, agent results, policy verdict, execution record, and the
audit ledger's actual timestamps. The model writes only the narrative
summary. If a fact isn't in the record, it isn't in the postmortem: the
document is traceable by construction, not by discipline.
"""
from __future__ import annotations

import time

from aetherops.gateway.model_gateway import TaskProfile

# Failure-class-specific preventive actions — the maintained half of the
# follow-up list; dynamic items (e.g. the revert PR) come from the run itself.
FOLLOW_UPS: dict[str, list[str]] = {
    "deploy-regression/memory": [
        "Add change-risk gating for connection-pool/config changes "
        "(Change Intelligence, pillar 2)",
        "Alert on pod OOMKilled rate for the affected service",
        "Add a canary stage to the service's deploy pipeline",
    ],
}


def _ts(seconds: float) -> str:
    return time.strftime("%H:%M:%S UTC", time.gmtime(seconds))


def build_postmortem(ctx) -> dict:
    incident = ctx.incident
    triage = ctx.results["triage"]
    rca = ctx.results["root_cause"]
    plan = ctx.results["planner"]
    verifier = ctx.results["verifier"]
    executed = ctx.params.get("executed", [])
    verdict = ctx.params.get("policy_verdict", {})
    episode = ctx.params.get("episode", {})
    service = triage.output["service"]
    p99 = verifier.output.get("p99_ms")

    follow_ups = [f"Merge draft revert PR {record['result']['pr_url']} "
                  "(fix-forward path)"
                  for record in executed if record["result"].get("pr_url")]
    follow_ups += FOLLOW_UPS.get(rca.output.get("failure_class", ""), [])

    narrative = ctx.gateway.complete(
        f"[postmortem] service={service} "
        f"failure_class={rca.output.get('failure_class')} "
        f"suspect={rca.output.get('suspect_commit')} "
        f"steps={[s['action'] for s in plan.output['steps']]} "
        f"recovered_p99={p99}",
        TaskProfile(task="postmortem", tier_hint="fast",
                    severity=incident.severity)).text

    timeline: list[tuple[float, str]] = []
    for record in ctx.audit.records:
        if record.action == "node.succeeded":
            timeline.append((record.ts, f"`{record.payload['node']}` completed"))
        elif record.action == "gate.paused":
            timeline.append((record.ts, "approval requested "
                             f"({record.payload.get('reason', 'gate')})"))
        elif record.action == "gate.approved":
            timeline.append((record.ts, "approval granted"))
    timeline.sort(key=lambda item: item[0])

    lines: list[str] = []
    add = lines.append
    add(f"# Postmortem — {incident.id}: {incident.title}")
    add("")
    add(f"- **Severity:** {incident.severity.value}   **Service:** {service}   "
        f"**Environment:** {incident.environment}")
    add(f"- **Failure class:** {rca.output.get('failure_class')}   "
        f"**Diagnosis confidence:** {rca.confidence:.2f}")
    add(f"- **Detected:** {_ts(incident.created_at)}   "
        f"**Learned episode:** {episode.get('episode_id', 'n/a')}")
    add("")
    add("## Summary")
    add("")
    add(narrative)
    add("")
    add("## Timeline (from the audit ledger)")
    add("")
    for ts, event in timeline:
        add(f"- {_ts(ts)} — {event}")
    add("")
    add("## Root cause")
    add("")
    add(rca.output["hypothesis"])
    add("")
    add("## Evidence")
    add("")
    add("| # | Kind | Source | Reference | Note |")
    add("|---|------|--------|-----------|------|")
    for i, evidence in enumerate(ctx.evidence, start=1):
        if evidence.classification == "QUARANTINED":
            note = "QUARANTINED — content withheld (suspected prompt injection)"
        else:
            note = evidence.summary[:80]
        add(f"| E{i} | {evidence.kind} | {evidence.citation.source} | "
            f"{evidence.citation.ref} | {note} |")
    add("")
    add("## Remediation")
    add("")
    approval = ("human approval required" if verdict.get("requires_approval")
                else "auto-approved by policy")
    add(f"- Plan admitted at approval tier {verdict.get('approval_tier', '?')} "
        f"({approval})")
    for record in executed:
        add(f"- Executed `{record['action']}` -> {record['result']}")
    add("")
    add("## Verification")
    add("")
    add(f"- Recovered: {verifier.output.get('recovered')} (p99 {p99}ms). "
        f"{verifier.output.get('note', '')}")
    add("")
    add("## Follow-ups")
    add("")
    for item in follow_ups:
        add(f"- [ ] {item}")
    add("")
    add("## Governance")
    add("")
    models = sorted({result.model_id for result in ctx.results.values()
                     if result.model_id != "n/a"})
    add(f"- Audit records: {len(ctx.audit)}; hash-chain verified: "
        f"{ctx.audit.verify()}")
    add(f"- Model tokens metered: {ctx.gateway.tokens_used}; "
        f"models: {', '.join(models)}")

    markdown = "\n".join(lines) + "\n"
    return {"markdown": markdown, "follow_ups": follow_ups,
            "line_count": len(lines) + 1}
