"""Replay harness and metrics catalog (docs/10-evaluation.md §3, §7).

Runs every golden scenario through the real workflow (auto-approving gates —
the harness measures decision quality, not approval latency) and scores the
result against adjudicated ground truth. Escalation on an undiagnosable
scenario scores as CORRECT, and low confidence there counts as good
calibration — the platform earns trust by knowing what it doesn't know.
"""
from __future__ import annotations

from aetherops.core.types import WorkflowStatus
from aetherops.evals.scenarios import Scenario, all_scenarios, build_environment
from aetherops.workflows.incident_remediation import run_incident_remediation

# Phase 1 exit criterion (docs/14-risks-and-roadmap.md): below this, the
# release gate fails.
RCA_PRECISION_GATE = 0.60


def run_scenario(scenario: Scenario) -> dict:
    # Always offline: golden-scenario replay is deterministic by contract
    # (docs/10) and must not vary with what's installed on the machine.
    incident, env = build_environment(scenario, backends_spec="offline")
    run, ctx = run_incident_remediation(incident, **env)
    if run.status == WorkflowStatus.PAUSED:
        run, ctx = run_incident_remediation(
            incident, **env, ctx=ctx,
            approvals={run.pending_gate: True}, checkpoint=run.checkpoint)

    if run.status == WorkflowStatus.SUCCEEDED:
        outcome = "remediated"
    elif run.error and "escalate" in run.error.lower():
        outcome = "escalated"
    else:
        outcome = "failed"

    rca = ctx.results.get("root_cause")
    diagnosed = bool(rca) and rca.output.get("status") == "diagnosed"
    predicted_class = rca.output.get("failure_class") if diagnosed else None
    predicted_commit = rca.output.get("suspect_commit") if diagnosed else None
    steps = (tuple(s["action"] for s in ctx.results["planner"].output["steps"])
             if "planner" in ctx.results else ())

    truth = scenario.truth
    diagnosis_correct = (predicted_class == truth.failure_class
                         and predicted_commit == truth.suspect_commit)
    outcome_correct = outcome == truth.outcome
    steps_correct = steps == tuple(truth.expected_steps)

    # Calibration target: 1.0 only when a diagnosis was expected AND correct.
    # An expected escalation has target 0.0 — low confidence there is GOOD.
    confidence = rca.confidence if rca else 0.0
    target = 1.0 if (truth.failure_class is not None and diagnosis_correct) else 0.0
    calibration_error = abs(confidence - target)

    verification_passed = None
    if outcome == "remediated":
        verification_passed = bool(
            run.checkpoint.get("verify", {}).get("output", {}).get("recovered"))

    return {
        "scenario": scenario.id,
        "name": scenario.name,
        "outcome": outcome,
        "expected_outcome": truth.outcome,
        "outcome_correct": outcome_correct,
        "predicted_class": predicted_class,
        "expected_class": truth.failure_class,
        "predicted_commit": predicted_commit,
        "expected_commit": truth.suspect_commit,
        "diagnosis_correct": diagnosis_correct,
        "steps": list(steps),
        "expected_steps": list(truth.expected_steps),
        "steps_correct": steps_correct,
        "correct": diagnosis_correct and outcome_correct and steps_correct,
        "confidence": round(confidence, 3),
        "calibration_error": round(calibration_error, 3),
        "citation_hallucination": bool(run.error
                                       and "hallucinated citation" in run.error),
        "verification_passed": verification_passed,
        "tool_calls": sum(1 for record in env["audit"].records
                          if record.action == "tool.call"),
        "investigation_steps": (ctx.results["knowledge"].output
                                .get("investigation", {}).get("steps")
                                if "knowledge" in ctx.results else None),
        "investigation_termination": (ctx.results["knowledge"].output
                                      .get("investigation", {})
                                      .get("termination")
                                      if "knowledge" in ctx.results
                                      else None),
        "tokens": env["gateway"].tokens_used,
        "model_latency_ms": round(sum(
            record.payload.get("latency_ms", 0.0)
            for record in env["audit"].records
            if record.action == "model.call"), 1),
        "est_cost_usd": env["gateway"].est_cost_usd,
        "audit_verified": env["audit"].verify(),
    }


def trust_ladder(rows: list[dict]) -> dict:
    """Per-failure-class ladder verdict (docs/10-evaluation.md §1). The
    reference thresholds: precision 1.0 and mean calibration error <= 0.35
    over >= 2 episodes for gated-writes; auto-path needs >= 50 golden
    episodes and is therefore out of reach here by construction."""
    by_class: dict[str, list[dict]] = {}
    for row in rows:
        if row["expected_class"] is not None:
            by_class.setdefault(row["expected_class"], []).append(row)

    ladder = {}
    for failure_class, class_rows in by_class.items():
        precision = sum(r["diagnosis_correct"] for r in class_rows) / len(class_rows)
        mean_cal = sum(r["calibration_error"] for r in class_rows) / len(class_rows)
        if precision == 1.0 and mean_cal <= 0.35 and len(class_rows) >= 2:
            stage = ("gated-writes eligible (auto-path requires >=50 golden "
                     "episodes — docs/10)")
        else:
            stage = "advisory-only"
        ladder[failure_class] = {
            "episodes": len(class_rows),
            "precision": round(precision, 3),
            "mean_calibration_error": round(mean_cal, 3),
            "stage": stage,
        }
    return ladder


def run_all(scenarios: list[Scenario] | None = None) -> dict:
    rows = [run_scenario(s) for s in (scenarios or all_scenarios())]

    diagnosable = [r for r in rows if r["expected_class"] is not None]
    escalations = [r for r in rows if r["expected_class"] is None]
    remediated = [r for r in rows if r["outcome"] == "remediated"]

    def ratio(numer, denom):
        return round(numer / denom, 3) if denom else None

    aggregates = {
        "scenarios": len(rows),
        "rca_precision_at_1": ratio(
            sum(r["diagnosis_correct"] for r in diagnosable), len(diagnosable)),
        "escalation_correctness": ratio(
            sum(r["outcome"] == "escalated" for r in escalations),
            len(escalations)),
        "plan_step_accuracy": ratio(
            sum(r["steps_correct"] for r in diagnosable), len(diagnosable)),
        "verification_pass_rate": ratio(
            sum(bool(r["verification_passed"]) for r in remediated),
            len(remediated)),
        "citation_faithfulness": ratio(
            sum(not r["citation_hallucination"] for r in rows), len(rows)),
        "mean_calibration_error": round(
            sum(r["calibration_error"] for r in rows) / len(rows), 3),
        "all_audit_chains_verified": all(r["audit_verified"] for r in rows),
        "total_tokens": sum(r["tokens"] for r in rows),
        "total_tool_calls": sum(r["tool_calls"] for r in rows),
        "total_model_latency_ms": round(
            sum(r["model_latency_ms"] for r in rows), 1),
        "total_est_cost_usd": round(
            sum(r["est_cost_usd"] for r in rows), 6),
    }

    precision = aggregates["rca_precision_at_1"]
    gate_passed = precision is not None and precision >= RCA_PRECISION_GATE

    return {
        "rows": rows,
        "aggregates": aggregates,
        "trust_ladder": trust_ladder(rows),
        "release_gate": {
            "criterion": f"rca_precision_at_1 >= {RCA_PRECISION_GATE}",
            "passed": gate_passed,
        },
    }
