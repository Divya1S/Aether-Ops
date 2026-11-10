// Shapes returned by the AetherOps API (src/aetherops/api/views.py is the
// source of truth). The demo client serves a recorded transcript of these
// same shapes, so both modes type-check against one contract.

export type Mode = "checking" | "live" | "demo";

export type Outcome =
  | "running"
  | "awaiting-approval"
  | "remediated"
  | "escalated"
  | "denied"
  | "failed";

export interface Agent {
  name: string;
  confidence: number;
  model: string;
  tokens?: number;
  citations?: number;
}

export interface Evidence {
  id: string; // "E3" — the citation handle hypotheses refer to
  kind: string;
  source: string;
  ref: string;
  summary: string;
  excerpt: string;
  classification: string;
}

export interface PlanStep {
  action: string;
  risk: string;
  args: Record<string, unknown>;
  compensable?: boolean;
}

export interface Diagnosis {
  status: string;
  failure_class: string | null;
  suspect_commit: string | null;
  confidence: number;
  /** False when the diagnosis was proposed but then rejected (escalated). */
  standing?: boolean;
}

export type StageState = "pending" | "done" | "paused" | "failed" | "denied" | "skipped";

export interface Stage {
  id: string;
  label: string;
  gate: boolean;
  state: StageState;
  duration_ms?: number;
  error?: string;
}

export type CriterionStatus = "supported" | "refuted" | "unknown";
export type HypothesisStatus = "VALIDATED" | "INVALIDATED" | "INCONCLUSIVE";

export interface Criterion {
  id: string;
  claim: string;
  status: CriterionStatus;
  note: string;
  evidence: string[];
  required: boolean;
  gather?: string;
}

export interface Hypothesis {
  id: string;
  failure_class: string;
  title: string;
  status: HypothesisStatus;
  supported: number;
  required: number;
  ruled_out_by: string[];
  criteria: Criterion[];
  gather_next: string[];
}

export interface HypothesisTree {
  conclusion: "validated" | "inconclusive";
  validated: string | null;
  summary: string;
  hypotheses: Hypothesis[];
  gather_next: string[];
  pipeline: {
    status: string | null;
    failure_class: string | null;
    escalated: boolean;
    effective_class: string | null;
    agrees: boolean;
  };
}

export interface Signals {
  metric: string;
  unit: string;
  series: { ts: string; value: number }[];
  onset: string | null;
  deploy: {
    ts: string;
    revision: string;
    previous_revision: string;
    commit: string | null;
  } | null;
  events: { ts: string; reason: string; pod: string }[];
  refs: { series: string[]; deploy: string[]; events: string[] };
  recovered?: { value: number; recovered: boolean };
}

export interface TimelineEvent {
  seq: number;
  ts: number;
  t_ms: number;
  actor: string;
  action: string;
  kind: string;
  title: string;
  detail: string;
}

export interface ReviewCheck {
  name: string;
  passed: boolean;
  note: string;
}

export interface PolicyDecision {
  action: string;
  rule_id: string;
  allowed: boolean;
  requires_approval: boolean;
  approval_tier: number;
  reason: string;
}

export interface Feedback {
  verdict: "correct" | "incorrect";
  note: string;
  role: string;
  at: number;
  memory_updated: boolean;
}

export interface IncidentRow {
  incident_id: string;
  title: string;
  service: string;
  severity: string;
  status: string;
  outcome: Outcome;
  created_at: number;
  scenario: string | null;
  alert_group_id: string | null;
  pending_gate: string | null;
  evidence_count?: number;
  tokens?: number;
  est_cost_usd?: number;
  elapsed_ms?: number;
  time_to_diagnosis_ms?: number;
  archived: boolean;
  failure_class: string | null;
  confidence?: number | null;
  feedback?: string | null;
}

export interface Incident {
  incident_id: string;
  title: string;
  service: string;
  severity: string;
  environment?: string;
  created_at: number;
  scenario: string | null;
  alert_group_id: string | null;
  status: string;
  outcome: Outcome;
  archived: boolean;
  fence?: string;
  stages: Stage[];
  timeline: TimelineEvent[];
  // Everything below is absent while a run is still executing.
  pending_gate?: string | null;
  approval_tier?: number;
  diagnosis?: Diagnosis;
  hypothesis?: string;
  hypotheses?: HypothesisTree;
  signals?: Signals | null;
  agents?: Agent[];
  evidence?: Evidence[];
  evidence_count?: number;
  plan?: PlanStep[];
  plan_rationale?: string;
  policy?: {
    allowed: boolean;
    requires_approval: boolean;
    approval_tier: number;
    decisions: PolicyDecision[];
  };
  review_checks?: ReviewCheck[];
  investigation?: { steps?: number; termination?: string };
  gaps?: string[];
  similar_incidents?: { ref: string; summary: string; evidence: string }[];
  blast_radius?: string[];
  executed?: {
    action: string;
    dry_run: boolean;
    compensable: boolean;
    result: Record<string, unknown>;
  }[];
  verification?: { recovered: boolean; p99_ms: number; note: string };
  episode_id?: string;
  postmortem_available?: boolean;
  postmortem_excerpt?: string;
  follow_ups?: string[];
  tokens?: number;
  est_cost_usd?: number;
  elapsed_ms?: number;
  time_to_diagnosis_ms?: number;
  audit?: { count: number; chain_verified: boolean };
  feedback?: Feedback | null;
  error?: string | null;
}

export interface AuditRecord {
  seq: number;
  ts: number;
  actor: string;
  action: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export interface AuditLedger {
  incident_id: string;
  count: number;
  chain_verified: boolean;
  records: AuditRecord[];
}

export interface Postmortem {
  incident_id: string;
  markdown: string;
  follow_ups: string[];
}

export interface ScenarioInfo {
  id: string;
  name: string;
  service: string;
  expected_outcome: string;
}

export type Severity = "info" | "warning" | "high" | "critical";

export interface Alert {
  id: string;
  fingerprint: string;
  source: string;
  name: string;
  service: string;
  severity: Severity;
  status: string;
  summary: string;
  labels: Record<string, string>;
  first_seen: number;
  last_seen: number;
  count: number;
  joined_because: string;
}

export interface AlertGroup {
  id: string;
  title: string;
  origin: string;
  services: string[];
  severity: Severity;
  status: "open" | "investigating" | "resolved";
  incident_id: string | null;
  first_seen: number;
  last_seen: number;
  events: number;
  unique_alerts: number;
  sources: string[];
  alerts: Alert[];
  investigable: boolean;
}

export interface AlertStats {
  received: number;
  deduplicated: number;
  unique_alerts: number;
  groups: number;
  open_groups: number;
  noise_reduction: number;
  window_s: number;
}

export interface AlertsPayload {
  stats: AlertStats;
  groups: AlertGroup[];
}

export interface Stats {
  incidents: {
    total: number;
    by_outcome: Record<string, number>;
    by_failure_class: Record<string, number>;
    by_service: Record<string, number>;
  };
  diagnosis_rate: number | null;
  mean_time_to_diagnosis_ms: number | null;
  mean_elapsed_ms: number | null;
  tokens: number;
  est_cost_usd: number;
  feedback: { correct: number; incorrect: number; acceptance: number | null };
  alerts: AlertStats;
  memory: { episodes: number };
}

export interface TopologyNode {
  id: string;
  known: boolean;
  depth: number;
  dependents: number;
  dependencies: number;
  alerts: { alerts: number; severity: Severity; origin: boolean } | null;
  incidents: string[];
}

export interface Topology {
  nodes: TopologyNode[];
  edges: { from: string; to: string }[];
  focus: { service: string; blast_radius: string[]; upstream: string[] } | null;
}

export interface ChangeRequest {
  service: string;
  sha: string;
  title: string;
  diff: string;
  peak_window: boolean;
  freeze: boolean;
}

export interface ChangeScore {
  change_id: string;
  service: string;
  status: string;
  score: number | null;
  band: string | null;
  components: Record<string, number> | null;
  rationale?: string;
  requires_approval?: boolean;
  canary_required?: boolean;
  policy_rule?: string;
  policy_reason?: string;
  dependents: string[];
  matched_episodes: { ref: string; summary: string }[];
  error: string | null;
}

export interface Runbook {
  doc: string;
  title: string;
  ref: string;
  score: number;
  excerpt: string;
}

export interface RetrievalStrategy {
  chunks: number;
  queries?: number;
  precision_at_1: number;
  precision_at_1_ci95?: [number, number];
  paraphrase_precision_at_1?: number;
  recall_at_5: number;
  mrr: number;
}

export interface Gate {
  criterion: string;
  passed: boolean;
}

export interface EvalScenarioRow {
  scenario: string;
  name: string;
  outcome: string;
  expected_outcome: string;
  outcome_correct: boolean;
  predicted_class: string | null;
  expected_class: string | null;
  diagnosis_correct: boolean;
  steps_correct: boolean;
  confidence: number;
  calibration_error: number;
  tool_calls: number;
  investigation_steps: number | null;
  tokens: number;
  audit_verified: boolean;
}

export interface Evals {
  aggregates: Record<string, unknown>;
  trust_ladder: Record<
    string,
    { episodes: number; precision: number; mean_calibration_error?: number; stage: string }
  >;
  release_gate: Gate;
  judge_gate?: Gate;
  scenarios?: EvalScenarioRow[];
  retrieval: {
    strategies: Record<string, RetrievalStrategy>;
    best: string;
    gate: Gate & { ci_lower_bound: number };
  };
}

export interface Catalog {
  step_catalog: {
    action: string;
    system: string;
    tool: string;
    risk: string;
    compensable: boolean;
    required_args: string[];
  }[];
  investigation_tools: { name: string; description: string; risk: string }[];
  stages: { id: string; label: string; gate: boolean }[];
  role_grants: Record<string, string[]>;
  prompts: string[];
}

export interface OpenApi {
  info: { title: string; version: string };
  paths: Record<
    string,
    Record<
      string,
      { summary: string; tags: string[]; security?: unknown[]; "x-required-grant"?: string; "x-roles"?: string[] }
    >
  >;
}

export interface WhoAmI {
  role: string;
  grants: string[];
}
