"""Run the vertical slices end-to-end:

    python3 -m aetherops             # incident: run to the approval gate, pause
    python3 -m aetherops --approve   # incident: pause, then resume approved
    python3 -m aetherops --deny      # incident: pause, then resume denied
    python3 -m aetherops --change    # change intelligence: risky vs benign
"""
from __future__ import annotations

import argparse

from aetherops.core.types import ChangeEvent, WorkflowStatus, new_id
from aetherops.demo import build_demo_environment
from aetherops.graph.service_graph import default_graph
from aetherops.workflows.change_risk import run_change_risk
from aetherops.workflows.incident_remediation import run_incident_remediation

RULE = "─" * 72


def _section(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="aetherops")
    parser.add_argument("--approve", action="store_true",
                        help="approve the gate and resume execution")
    parser.add_argument("--deny", action="store_true",
                        help="deny the gate")
    parser.add_argument("--change", action="store_true",
                        help="run the change-intelligence demo instead")
    parser.add_argument("--postmortem", metavar="FILE",
                        help="write the generated postmortem to FILE")
    parser.add_argument("--live", action="store_true",
                        help="serve agents from a local Ollama model, "
                             "falling back to offline if unavailable")
    args = parser.parse_args()

    if args.change:
        return change_demo()

    backends_spec = "ollama,offline" if args.live else None
    incident, env = build_demo_environment(backends_spec=backends_spec)

    _section(f"AetherOps — incident {incident.id}")
    print(f"{incident.severity.value}  {incident.title}  "
          f"[env={incident.environment}]")

    run, ctx = run_incident_remediation(incident, **env)

    _section("Evidence bundle (gathered, cited, redacted at the gateway)")
    for i, ev in enumerate(ctx.evidence, start=1):
        print(f"  [E{i}] ({ev.kind}, {ev.citation.source}) {ev.summary}")
        print(f"        ↳ {ev.citation.ref}")

    _section("Agent conclusions")
    for name, result in ctx.results.items():
        print(f"  {name:<12} confidence={result.confidence:.2f} "
              f"model={result.model_id} citations={len(result.citations)}")
    rca = ctx.results.get("root_cause")
    if rca:
        print(f"\n  Diagnosis ({rca.output.get('failure_class')}):")
        print(f"  {rca.output['hypothesis']}")

    plan = ctx.results.get("planner")
    if plan:
        _section("Remediation plan (Step Catalog actions only)")
        for step in plan.output["steps"]:
            print(f"  - {step['action']}  risk={step['risk']}  args={step['args']}")
        verdict = run.checkpoint.get("policy_check", {})
        print(f"\n  Policy: allowed={verdict.get('allowed')} "
              f"approval_tier={verdict.get('approval_tier')}")
        for decision in verdict.get("decisions", []):
            print(f"    [{decision['rule_id']}] {decision['reason']}")

    if run.status == WorkflowStatus.PAUSED:
        _section(f"PAUSED at gate '{run.pending_gate}' — awaiting human approval")
        if args.approve or args.deny:
            decision = bool(args.approve)
            print(f"  Decision recorded: {'APPROVED' if decision else 'DENIED'}")
            run, ctx = run_incident_remediation(
                incident, **env, ctx=ctx,
                approvals={run.pending_gate: decision},
                checkpoint=run.checkpoint)
        else:
            print("  Re-run with --approve (or --deny) to resume this workflow.")

    if run.status == WorkflowStatus.SUCCEEDED:
        _section("Execution, verification, learning")
        for record in run.checkpoint["execute"]["executed"]:
            print(f"  executed {record['action']}: {record['result']}")
        print(f"  verified: {run.checkpoint['verify']}")
        print(f"  learned episode: {run.checkpoint['learn']}")

        postmortem = run.checkpoint.get("postmortem", {})
        if postmortem:
            _section(f"Postmortem generated "
                     f"({postmortem['line_count']} lines, "
                     f"{len(postmortem['follow_ups'])} follow-ups)")
            for line in postmortem["markdown"].splitlines()[:8]:
                print(f"  {line}")
            print("  …")
            if args.postmortem:
                with open(args.postmortem, "w", encoding="utf-8") as fh:
                    fh.write(postmortem["markdown"])
                print(f"  full document written to {args.postmortem}")
            else:
                print("  (pass --postmortem FILE to write the full document)")
    elif run.status != WorkflowStatus.PAUSED:
        _section(f"Workflow ended: {run.status.value}")
        print(f"  {run.error}")

    _print_trace(env["audit"])

    _section("Governance")
    audit = env["audit"]
    print(f"  audit records: {len(audit)}  hash-chain verified: {audit.verify()}")
    print(f"  model tokens metered: {env['gateway'].tokens_used}  "
          f"est. production cost: ${env['gateway'].est_cost_usd:.4f}")
    return 0


def _print_trace(audit) -> None:
    """Per-workflow trace: model calls (backend, latency, cost) and node
    durations — all reconstructed from the audit ledger, the same way the
    postmortem timeline is."""
    model_calls = [r.payload for r in audit.records if r.action == "model.call"]
    fallbacks = [r.payload for r in audit.records
                 if r.action == "backend.fallback"]
    decisions = [r.payload for r in audit.records
                 if r.action == "agent.decision"]
    for decision in decisions:
        print(f"  decide step {decision['step']}: "
              f"{decision['action']:<20} {decision['args']}")
    nodes = [r.payload for r in audit.records if r.action == "node.succeeded"]

    _section("Trace (from the audit ledger)")
    for call in model_calls:
        print(f"  model  {call['task']:<12} {call['backend']:<8} "
              f"{call['served_model']:<22} {call['latency_ms']:>8.1f} ms  "
              f"{call['tokens_in']:>5}→{call['tokens_out']:<5} tok  "
              f"${call['est_cost_usd']:.5f}")
    for fb in fallbacks:
        print(f"  FALLBACK: {fb['failed_backend']} → {fb['next_backend']} "
              f"({fb['error'][:60]})")
    slowest = sorted(nodes, key=lambda n: -n.get("duration_ms", 0))[:5]
    for node in slowest:
        print(f"  node   {node['node']:<16} {node.get('duration_ms', 0):>8.1f} ms")
    total_latency = sum(c["latency_ms"] for c in model_calls)
    print(f"  totals: {len(model_calls)} model calls, "
          f"{total_latency:.1f} ms model latency")


def change_demo() -> int:
    """Change Intelligence (docs/00 pillar 2): scores a risky and a benign
    change against post-incident organizational memory. Gates auto-approve
    here — the demo shows decision quality, not approval latency."""
    _, env = build_demo_environment()
    memory = env["memory"]
    memory.add({                      # the episode the incident demo learned
        "service": "checkout-service",
        "failure_class": "deploy-regression/memory",
        "summary": "Deploy raised DB connection pool max_size 20 -> 200; "
                   "OOMKilled cascade breached p99; rollback verified",
        "remediation": ["rollback_deployment", "create_revert_pr"],
        "verified": True})

    changes = [
        ChangeEvent(id=new_id("chg"), service="orders-service", sha="b7e21c9",
                    title="Raise DB connection pool max_size 25 -> 250",
                    diff="-  max_size: 25\n+  max_size: 250",
                    labels={"peak_window": True}),
        ChangeEvent(id=new_id("chg"), service="orders-service", sha="a11c3f0",
                    title="Update README copy",
                    diff="- old text\n+ new text",
                    labels={"peak_window": True}),
    ]

    for change in changes:
        base = {"gateway": env["gateway"], "audit": env["audit"],
                "memory": memory, "policy": env["policy"],
                "graph": default_graph()}
        _section(f"Change {change.sha} on {change.service}: {change.title}")
        run, ctx = run_change_risk(change, **base)

        verdict = run.checkpoint["score"]
        print(f"  score={verdict['score']}/100 band={verdict['band']} "
              f"components={verdict['components']}")
        print(f"  rationale: {verdict['rationale']}")
        decision = run.checkpoint.get("policy_check")
        if decision:
            print(f"  policy [{decision['rule_id']}]: {decision['reason']}")

        if run.status == WorkflowStatus.PAUSED:
            print(f"  PAUSED at gate (tier {decision['approval_tier']}) "
                  "— auto-approving for demo")
            run, ctx = run_change_risk(change, **base, ctx=ctx,
                                       approvals={run.pending_gate: True},
                                       checkpoint=run.checkpoint)
        if run.status == WorkflowStatus.SUCCEEDED:
            print(f"  recorded: {run.checkpoint['record']}")
        else:
            print(f"  outcome: {run.status.value} — {run.error}")

    _section("Governance")
    print(f"  audit records: {len(env['audit'])}  "
          f"hash-chain verified: {env['audit'].verify()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
