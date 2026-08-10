export interface Agent {
  name: string;
  confidence: number;
  model: string;
}

export interface Evidence {
  kind: string;
  source: string;
  ref: string;
  summary: string;
  classification: string;
}

export interface PlanStep {
  action: string;
  risk: string;
  args: Record<string, unknown>;
}

export interface Diagnosis {
  status: string;
  failure_class: string;
  suspect_commit: string | null;
  confidence: number;
}

export interface Incident {
  incident_id: string;
  title: string;
  status: string;
  pending_gate: string | null;
  approval_tier?: number;
  diagnosis?: Diagnosis;
  hypothesis?: string;
  agents: Agent[];
  evidence: Evidence[];
  plan?: PlanStep[];
  evidence_count: number;
  tokens: number;
  est_cost_usd: number;
  fence?: string;
  postmortem_excerpt?: string | null;
  follow_ups?: string[] | null;
  error?: string | null;
}

export interface Change {
  band: string;
  score: number;
  components: Record<string, number>;
  requires_approval: boolean;
  canary_required: boolean;
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
  precision_at_1: number;
  recall_at_5: number;
  mrr: number;
}

export interface TrustLadderEntry {
  episodes: number;
  precision: number;
  stage: string;
}

export interface Evals {
  aggregates: Record<string, number | boolean>;
  retrieval: Record<string, RetrievalStrategy>;
  trust_ladder: Record<string, TrustLadderEntry>;
  release_gate: { criterion: string; passed: boolean };
}

export type Mode = "checking" | "live" | "demo";
