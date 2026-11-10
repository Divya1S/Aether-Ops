// Client-side mirror of api/views.py:build_stats and incident_row, used only
// by the demo client (the live console reads GET /v1/stats). Kept pure so
// the two implementations can be held to the same expectations in tests.
import type { AlertStats, Incident, IncidentRow, Stats } from "../types";

export function toRow(incident: Incident): IncidentRow {
  // Mirrors api/views.py:incident_row — a diagnosis that was escalated is a
  // rejected proposal and is not reported as the incident's diagnosis.
  const diagnosed = incident.diagnosis?.standing ?? incident.diagnosis?.status === "diagnosed";
  return {
    incident_id: incident.incident_id,
    title: incident.title,
    service: incident.service,
    severity: incident.severity,
    status: incident.status,
    outcome: incident.outcome,
    created_at: incident.created_at,
    scenario: incident.scenario,
    alert_group_id: incident.alert_group_id,
    pending_gate: incident.pending_gate ?? null,
    evidence_count: incident.evidence_count,
    tokens: incident.tokens,
    est_cost_usd: incident.est_cost_usd,
    elapsed_ms: incident.elapsed_ms,
    time_to_diagnosis_ms: incident.time_to_diagnosis_ms,
    archived: incident.archived,
    failure_class: diagnosed ? (incident.diagnosis?.failure_class ?? null) : null,
    confidence: diagnosed ? (incident.diagnosis?.confidence ?? null) : null,
    feedback: incident.feedback?.verdict ?? null,
  };
}

function tally(rows: IncidentRow[], key: "outcome" | "failure_class" | "service"): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const row of rows) {
    const value = row[key];
    if (value) counts[value] = (counts[value] ?? 0) + 1;
  }
  return Object.fromEntries(
    Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
  );
}

function mean(values: (number | undefined)[]): number | null {
  const present = values.filter((v): v is number => typeof v === "number");
  if (!present.length) return null;
  return Math.round((present.reduce((s, v) => s + v, 0) / present.length) * 10) / 10;
}

export function buildStats(rows: IncidentRow[], alerts: AlertStats, episodes: number): Stats {
  const finished = rows.filter((r) => r.outcome !== "running");
  const diagnosed = finished.filter((r) => r.failure_class);
  const correct = rows.filter((r) => r.feedback === "correct").length;
  const incorrect = rows.filter((r) => r.feedback === "incorrect").length;
  return {
    incidents: {
      total: rows.length,
      by_outcome: tally(rows, "outcome"),
      by_failure_class: tally(rows, "failure_class"),
      by_service: tally(rows, "service"),
    },
    diagnosis_rate: finished.length ? Math.round((diagnosed.length / finished.length) * 1000) / 1000 : null,
    mean_time_to_diagnosis_ms: mean(finished.map((r) => r.time_to_diagnosis_ms)),
    mean_elapsed_ms: mean(finished.map((r) => r.elapsed_ms)),
    tokens: rows.reduce((s, r) => s + (r.tokens ?? 0), 0),
    est_cost_usd: Math.round(rows.reduce((s, r) => s + (r.est_cost_usd ?? 0), 0) * 1e6) / 1e6,
    feedback: {
      correct,
      incorrect,
      acceptance: correct + incorrect ? Math.round((correct / (correct + incorrect)) * 1000) / 1000 : null,
    },
    alerts,
    memory: { episodes },
  };
}
