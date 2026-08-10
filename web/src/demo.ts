// Recorded transcript captured from the live API — powers DEMO mode on
// the static deploy (same data the vanilla console shipped).
import type { Incident, Change, Runbook, Evals } from './types';

export const DEMO: {
  created: Incident;
  resolved: Partial<Incident> & { postmortem_excerpt: string; follow_ups: string[] };
  changes: { risky: Change; benign: Change };
  runbooks: Runbook[];
  evals: Evals;
} = {
  "created": {
    "incident_id": "inc_8866fd2be805",
    "title": "checkout-service p99 latency > 2000ms",
    "status": "PAUSED",
    "pending_gate": "approval_gate",
    "approval_tier": 2,
    "diagnosis": {
      "status": "diagnosed",
      "failure_class": "deploy-regression/memory",
      "suspect_commit": "c9a1f42",
      "confidence": 0.75
    },
    "hypothesis": "Hypothesis (primary): the deploy [E3] shipped commit c9a1f42 raising the DB connection pool [E4]. Pod memory grew past its limit, causing OOMKilled and CrashLoopBackOff [E5]; surviving pods absorbed the load, breaching p99 latency [E2]. Causal chain: deploy [E3] -> pool change [E4] -> memory exhaustion [E5] -> latency breach [E2]. A prior episode with the same signature supports this class [E8]. Recommended class: deploy-regression/memory.",
    "agents": [
      {
        "name": "triage",
        "confidence": 0.95,
        "model": "claude-haiku-4-5-20251001"
      },
      {
        "name": "knowledge",
        "confidence": 0.9,
        "model": "claude-haiku-4-5-20251001"
      },
      {
        "name": "security",
        "confidence": 0.95,
        "model": "n/a"
      },
      {
        "name": "root_cause",
        "confidence": 0.75,
        "model": "claude-opus-5"
      },
      {
        "name": "planner",
        "confidence": 0.68,
        "model": "claude-opus-5"
      },
      {
        "name": "reviewer",
        "confidence": 0.95,
        "model": "claude-sonnet-5"
      }
    ],
    "evidence": [
      {
        "kind": "alert",
        "source": "pagerduty",
        "ref": "pagerduty://incidents/P-8842",
        "summary": "PagerDuty P-8842: checkout-service p99 latency > 2000ms (urgency=high)",
        "classification": "INTERNAL"
      },
      {
        "kind": "metrics",
        "source": "datadog",
        "ref": "datadog://query/p99-incident",
        "summary": "p99 series [{'ts': '14:00Z', 'p99_ms': 181}, {'ts': '14:05Z', 'p99_ms': 2140}, {'ts': '14:10Z', 'p99_ms': 2412}]",
        "classification": "INTERNAL"
      },
      {
        "kind": "deploy",
        "source": "github",
        "ref": "github://checkout-service/deployments/v2025.08.07-3",
        "summary": "deploys: [{'service': 'checkout-service', 'revision': 'v2025.08.07-3', 'previous_revision': 'v2025.08.07-2', 'deployed_at': '2026-08-07T14:0",
        "classification": "INTERNAL"
      },
      {
        "kind": "commit",
        "source": "github",
        "ref": "github://commit/c9a1f42",
        "summary": "c9a1f42: Raise DB connection pool max_size 20 -> 200",
        "classification": "INTERNAL"
      },
      {
        "kind": "k8s-event",
        "source": "kubernetes",
        "ref": "k8s://prod/checkout-service/events",
        "summary": "events: [{'reason': 'OOMKilled', 'pod': 'checkout-7f9c-1', 'ts': '14:06Z'}, {'reason': 'OOMKilled', 'pod': 'checkout-7f9c-4', 'ts': '14:07Z'",
        "classification": "INTERNAL"
      },
      {
        "kind": "runbook",
        "source": "aetherops-rag",
        "ref": "rag://runbook-latency#0",
        "summary": "Investigating p99 latency spikes: For a sudden p99 latency regression, first ask what changed: deploys, config flags, dependency versions, t",
        "classification": "INTERNAL"
      },
      {
        "kind": "runbook",
        "source": "aetherops-rag",
        "ref": "rag://runbook-rollback#304",
        "summary": "Rolling back a bad deployment: After service recovery, open a revert pull request for the offending commit so the permanent fix goes through",
        "classification": "INTERNAL"
      },
      {
        "kind": "episode",
        "source": "aetherops-memory",
        "ref": "memory://episode/ep_743a7ffbb130",
        "summary": "past incident (deploy-regression/memory): Connection pool increase caused OOMKilled cascade; rollback restored p99 within 10 minutes",
        "classification": "INTERNAL"
      }
    ],
    "plan": [
      {
        "action": "rollback_deployment",
        "risk": "HIGH",
        "args": {
          "service": "checkout-service",
          "revision": "v2025.08.07-2"
        }
      },
      {
        "action": "create_revert_pr",
        "risk": "MEDIUM",
        "args": {
          "sha": "c9a1f42"
        }
      }
    ],
    "evidence_count": 8,
    "tokens": 3220,
    "est_cost_usd": 0.01288
  },
  "resolved": {
    "status": "SUCCEEDED",
    "evidence_count": 8,
    "tokens": 3376,
    "est_cost_usd": 0.013684,
    "agents": [
      {
        "name": "triage",
        "confidence": 0.95,
        "model": "claude-haiku-4-5-20251001"
      },
      {
        "name": "knowledge",
        "confidence": 0.9,
        "model": "claude-haiku-4-5-20251001"
      },
      {
        "name": "security",
        "confidence": 0.95,
        "model": "n/a"
      },
      {
        "name": "root_cause",
        "confidence": 0.75,
        "model": "claude-opus-5"
      },
      {
        "name": "planner",
        "confidence": 0.68,
        "model": "claude-opus-5"
      },
      {
        "name": "reviewer",
        "confidence": 0.95,
        "model": "claude-sonnet-5"
      },
      {
        "name": "verifier",
        "confidence": 0.95,
        "model": "claude-sonnet-5"
      }
    ],
    "postmortem_excerpt": "# Postmortem \u2014 inc_8866fd2be805: checkout-service p99 latency > 2000ms\n\n- Severity: SEV2   Service: checkout-service   Environment: prod\n- Failure class: deploy-regression/memory   Diagnosis confidence: 0.75\n\n## Summary\n\nOn checkout-service, a deploy-introduced change (c9a1f42) matching class deploy-regression/memory exhausted pod memory and breached latency SLOs. The platform correlated deploy, commit, and runtime evidence, executed a policy-gated rollback with a fix-forward revert PR, and verified recovery.",
    "follow_ups": [
      "Merge draft revert PR github://checkout-service/pull/4127 (fix-forward path)",
      "Add change-risk gating for connection-pool/config changes (Change Intelligence, pillar 2)",
      "Alert on pod OOMKilled rate for the affected service",
      "Add a canary stage to the service's deploy pipeline"
    ]
  },
  "changes": {
    "risky": {
      "band": "MEDIUM",
      "score": 55,
      "components": {
        "failure_signature": 20,
        "blast_radius": 20,
        "deploy_window": 15,
        "service_history": 0
      },
      "requires_approval": false,
      "canary_required": true
    },
    "benign": {
      "band": "LOW",
      "score": 35,
      "components": {
        "failure_signature": 0,
        "blast_radius": 20,
        "deploy_window": 15,
        "service_history": 0
      },
      "requires_approval": false,
      "canary_required": false
    }
  },
  "runbooks": [
    {
      "doc": "runbook-rollback",
      "title": "Rolling back a bad deployment",
      "ref": "rag://runbook-rollback#304",
      "score": 0.270359,
      "excerpt": "After service recovery, open a revert pull request for the offending commit so the permanent fix goes through code review. Never leave a rolled-back service without a revert PR."
    },
    {
      "doc": "runbook-latency",
      "title": "Investigating p99 latency spikes",
      "ref": "rag://runbook-latency#0",
      "score": 0.157725,
      "excerpt": "For a sudden p99 latency regression, first ask what changed: deploys, config flags, dependency versions, traffic mix. Pull the deployment timeline and overlay it on the latency series."
    },
    {
      "doc": "runbook-disk",
      "title": "Disk pressure and eviction storms",
      "ref": "rag://runbook-disk#189",
      "score": 0.144905,
      "excerpt": "Mitigate by pruning unused images, enforcing log rotation, and moving noisy writers to bounded volumes."
    },
    {
      "doc": "runbook-canary",
      "title": "Canary rollout policy",
      "ref": "rag://runbook-canary#231",
      "score": 0.127761,
      "excerpt": "A change flagged HIGH risk by change review must not skip canary stages. Manual promotion requires the service owner's approval."
    }
  ],
  "evals": {
    "aggregates": {
      "scenarios": 7,
      "rca_precision_at_1": 1.0,
      "escalation_correctness": 1.0,
      "plan_step_accuracy": 1.0,
      "verification_pass_rate": 1.0,
      "citation_faithfulness": 1.0,
      "mean_calibration_error": 0.231,
      "all_audit_chains_verified": true
    },
    "retrieval": {
      "paragraph": {
        "chunks": 20,
        "precision_at_1": 0.833,
        "recall_at_5": 0.861,
        "mrr": 0.847
      },
      "fixed": {
        "chunks": 17,
        "precision_at_1": 0.833,
        "recall_at_5": 0.861,
        "mrr": 0.847
      }
    },
    "trust_ladder": {
      "deploy-regression/memory": {
        "episodes": 2,
        "precision": 1.0,
        "stage": "gated-writes eligible"
      },
      "cert-expiry/tls": {
        "episodes": 1,
        "precision": 1.0,
        "stage": "advisory-only"
      }
    },
    "release_gate": {
      "criterion": "rca_precision_at_1 >= 0.6",
      "passed": true
    }
  }
};
