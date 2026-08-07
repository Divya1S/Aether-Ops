# 03 — Orchestration: Deterministic Control, Probabilistic Advisors

The single most important architectural decision in AetherOps is that **no LLM
ever controls execution flow**. This document specifies how that boundary is
drawn and how work actually executes: plan compilation, DAG semantics, state
machines, event-driven triggers, scheduling, retries, rollback, and
checkpoints. Reference implementation: `src/aetherops/orchestration/dag.py`.

---

## 1. Why deterministic orchestration instead of autonomous planning

An "autonomous agent" architecture — LLM in a loop choosing its next tool call
until it declares victory — was considered and rejected for the control path.

**Why chosen (deterministic):**

1. **Auditability is a product requirement, not a nice-to-have.** A Fortune
   100 security review asks: "enumerate every action this system can take
   against production, and prove it." With a Step Catalog and deterministic
   execution, the answer is a finite, versioned list with policies attached.
   With autonomous planning, the answer is "anything its tools allow,
   path-dependent on token sampling" — that system never gets write access.
2. **Reproducibility.** Incident workflows must be replayable for postmortems,
   compliance, and evaluation (see [10-evaluation.md](10-evaluation.md)).
   Deterministic DAGs over frozen evidence snapshots replay bit-for-bit;
   agent loops do not.
3. **Bounded failure.** A deterministic executor fails in enumerable ways
   (timeout, retry exhaustion, gate denial), each with a designed response
   ([11-failure-handling.md](11-failure-handling.md)). LLM-planned loops add
   unbounded ones: cycling, tool-call hallucination, goal drift, prompt-
   injection-steered planning.
4. **Transactional semantics.** Retries, idempotency, compensation, and
   durable timers are solved problems in workflow engines. Rebuilding them
   inside an LLM loop is rebuilding Temporal, badly, with tokens.

**Alternatives considered:** (a) fully autonomous planner-executor — rejected
above; (b) "LLM chooses among a few hard-coded playbooks" — safe but
regresses to runbook automation, can't handle novel compositions; (c) hybrid
**plan compilation** — chosen: the LLM proposes; deterministic software
validates, compiles, and executes.

**Trade-offs accepted:** the platform cannot invent new action types at
runtime — by design. Novel remediations require a human to add a Step Catalog
entry (code review + policy annotation + compensation handler). We lose
open-ended creativity in the *action space* and keep it in the *hypothesis
space*, which is where it pays.

**Operational implications:** the Step Catalog becomes a governed artifact
with an owner, versioning, and a release process; catalog coverage becomes a
tracked metric (fraction of proposed plans expressible in catalog terms).

## 2. The determinism boundary

| Concern | Owner | Rationale |
|---|---|---|
| Hypothesis generation, evidence synthesis, ranking | LLM (reasoning tier) | Genuinely open-ended; wrong answers are contained by citations + confidence + gates |
| Plan *proposal* | LLM (reasoning tier) | Creative composition — but output is only Step Catalog references |
| Plan *validation & compilation* | Deterministic | Schema check, catalog membership, arg validation, policy pre-check, budget check |
| Execution order, retries, timeouts, budgets | Deterministic (Temporal) | Transactional semantics; must be replayable |
| Policy decisions | Deterministic (OPA) | Compliance requires decidable, versioned rules |
| Approval routing & escalation | Deterministic | Org chart + tiers; LLM only renders the summary card |
| Severity mapping, dedup, storm suppression | Deterministic | High-volume, latency-sensitive, well-specified |
| FSM transitions | Deterministic | The workflow engine owns state; agents return data, not transitions |
| Summaries, explanations, PR prose | LLM (fast/standard tier) | Human-facing text; harmless failure mode |

Rule of thumb encoded in review checklists: **if a wrong output must be
impossible, it's software; if a wrong output is survivable and gated, it may
be a model.**

## 3. Plan compilation pipeline

```
 RootCause output                    STEP CATALOG (versioned registry)
 (diagnosis + confidence)            ┌──────────────────────────────────┐
        │                            │ rollback_deployment   HIGH  comp │
        ▼                            │ create_revert_pr      MED   comp │
 ┌─────────────┐   plan JSON         │ scale_deployment      MED   comp │
 │ Planner LLM │──(references only)─►│ flip_feature_flag     MED   comp │
 └─────────────┘                     │ restart_pods          LOW   comp │
        │                            │ failover_region       CRIT  comp │
        ▼                            └──────────────────────────────────┘
 ┌──────────────────────────────────────────────────────────────┐
 │ PLAN COMPILER (deterministic)                                │
 │ 1. JSON Schema validation (retry to LLM ≤2 on failure)       │
 │ 2. Catalog membership: every step ID must exist — no         │
 │    free-form actions, ever                                   │
 │ 3. Argument validation against each step's arg schema        │
 │ 4. Policy pre-check (OPA): risk class, environment, freeze   │
 │ 5. Budget check: token/tool/time cost vs workflow budget     │
 │ 6. Emit typed DAG: nodes + deps + gates + compensations      │
 └──────────────────────────────────────────────────────────────┘
        │ rejected → Planner retry (≤2) → human escalation
        ▼
 Temporal workflow execution
```

A plan that references anything outside the catalog is not "sanitized" — it is
**rejected**. This is the mechanism that makes prompt injection unable to mint
new capabilities (see [05-security.md](05-security.md)): injected text can at
worst propose a cataloged, policied, gated action.

## 4. DAG execution semantics

Implemented in `orchestration/dag.py` (production: Temporal workflows; the
reference executor preserves the semantics 1:1 so tests exercise them).

- **Nodes** are typed: name, dependencies, handler, retry policy, optional
  compensation handler, optional gate spec. Handlers receive the workflow
  context and return a JSON-serializable output recorded in the checkpoint.
- **Topological execution** with validation at build time (unique names,
  declared deps exist, acyclic). Independent branches may fan out (evidence
  gathering runs per-connector queries in parallel in production).
- **Idempotency:** every node execution carries an idempotency key
  `(workflow_id, node, attempt_epoch)`; connector writes pass it downstream so
  a retried step cannot double-execute (e.g., two revert PRs).
- **Timeouts:** hierarchical budgets — workflow > FSM state > node > tool
  call. A child may never outlive its parent's remaining budget.
- **Checkpoints:** node outputs are durably recorded after each success.
  Resume skips completed nodes. If a workflow resumes after more than a
  configured staleness window (default 15 min in a gate), the policy check
  re-runs before any write executes — approvals age out.

### Retry taxonomy

| Class | Trigger | Strategy | Cap |
|---|---|---|---|
| Transient | connector timeout, 429, model 5xx | exponential backoff + jitter | 3 |
| Semantic | schema-invalid LLM output | re-prompt with validation errors | 2 |
| Systemic | model/provider outage | tier fallback with confidence penalty ([07-model-gateway.md](07-model-gateway.md)) | 1 |
| Non-retryable | policy denial, gate denial, budget exhaustion | never retried — escalate | 0 |

Retries live in the executor, not in agents: an agent that retries internally
would corrupt cost accounting and hide failure signals from calibration.

### Rollback: sagas, not transactions

Cross-system operations (K8s + GitHub + feature flags) cannot be wrapped in a
transaction, so the platform uses **compensation (saga pattern)**: every
Step Catalog write declares an undo handler (`rollback_deployment` ↔
re-promote previous, `create_revert_pr` ↔ close PR, `scale_deployment` ↔
restore replica count). On a failed or verification-rejected execution, the
executor runs compensations for completed steps in reverse completion order,
then transitions the FSM to `ROLLING_BACK → ESCALATED`.

Two honest limits, by design:

1. **Some actions are not compensable** (a config already served to clients,
   a cache flush). Non-compensable steps require a strictly higher approval
   tier — the policy engine reads the `compensable: false` annotation.
2. **Compensation is itself verified.** After compensations run, the Verifier
   agent re-checks baselines; a failed compensation is a page to a human, not
   a retry loop.

## 5. State machines and events

The incident FSM ([01-architecture.md](01-architecture.md) §3) is owned by
the workflow engine. Agents return data; the Coordinator (deterministic
service) maps data + policy results to transitions. Transitions are events on
`incidents.state-changed` (Kafka), which is what the UI, Slack surfaces,
audit, and metering consume — the FSM is the platform's single source of
truth about "where is this incident."

**Event-driven triggers:** workflows start from `incidents.detected`
(normalized alerts), from webhook signals (deploy events start change-risk
workflows), or from durable timers (verification watch windows, approval
timeouts). Handlers are idempotent and keyed by `(source, dedup_key)`;
Kafka provides at-least-once, and the workflow engine's idempotent start
collapses duplicates — the standard outbox/idempotent-consumer pattern
rather than chasing exactly-once delivery.

**Why Temporal specifically:** durable execution with replay-based recovery,
signals (approvals), durable timers (gates, watch windows), per-task-queue
worker pools that map cleanly onto per-agent-type scheduling, and battle-
tested history at the required scale. Alternatives: Airflow (batch-oriented,
poor human-in-the-loop), Step Functions (vendor lock, weak local testing),
hand-rolled on Kafka + Postgres (we would re-implement Temporal's hardest
20%). Trade-off: Temporal's operational complexity (history shards, worker
tuning) — accepted; it is the best-understood component of its class.
Operational implication: a dedicated Temporal cluster per cell, sized for
storm mode ([08-scalability.md](08-scalability.md)).

## 6. Agent scheduling

- One Temporal task queue per agent type per cell (`q.root-cause.us-e-1`).
  Worker pools scale independently — reasoning-tier agents are few and
  expensive; fast-tier agents are many and cheap.
- **Priority = f(severity, tenant tier, workflow age).** SEV1 preempts queue
  position, never running work.
- **Fairness:** per-tenant concurrency quotas prevent one tenant's alert
  storm from starving the cell ([08-scalability.md](08-scalability.md)).
- **Budgets as scheduling inputs:** a workflow whose token budget is 80%
  consumed schedules remaining agent calls on cheaper tiers or defers
  non-critical nodes (e.g., prose postmortem drafting) to batch lanes.

## 7. Checkpoints and the approval gate lifecycle

```
 …→ policy_check ──► gate needed? ──no──► execute
                        │yes
                        ▼
              AWAITING_APPROVAL (durable timer + signal)
               │ approve signal        │ deny signal        │ timeout
               ▼                       ▼                    ▼
        staleness check          FSM → ESCALATED      escalate chain
         │fresh    │stale (>15m)  (with full evidence   (next approver,
         ▼         ▼               bundle attached)      then advisory)
      execute   re-run policy_check
```

Approval decisions carry **fencing tokens**: a decision references the exact
plan hash it approves. If the plan is recompiled (new evidence arrived), the
old approval is void. This closes the TOCTOU gap between "human saw plan A"
and "system executes plan B".

## 8. Mapping to the reference implementation

| Concept | File |
|---|---|
| DAG executor: topo order, retries, checkpoints, compensation, gates | `src/aetherops/orchestration/dag.py` |
| Retry taxonomy (transient vs permanent) | `src/aetherops/agents/base.py` |
| Incident workflow wiring (the canonical SEV2) | `src/aetherops/workflows/incident_remediation.py` |
| Policy pre-check + approval tiers | `src/aetherops/policy/engine.py` |
| Plan validation against a Step Catalog | `PlannerAgent` in `src/aetherops/agents/planner.py` |
| Pause/resume with approvals + checkpoint | `DagExecutor.execute(..., approvals=, checkpoint=)` |

The reference executor is intentionally small (~200 lines) so the semantics
are readable in one sitting; every guarantee above is exercised by
`tests/test_dag.py` and `tests/test_workflow.py`.
