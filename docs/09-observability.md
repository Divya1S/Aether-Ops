# 09 — Observability

Platform Infrastructure — AetherOps. Inherits terminology from
[01-architecture.md](01-architecture.md). The observability pipeline is a
Governance-plane component alongside the audit ledger, the evaluation
service ([10-evaluation.md](10-evaluation.md)), and cost metering
([13-cost-model.md](13-cost-model.md)).

---

## 1. Principle: the explainer must be the most explainable

AetherOps exists to explain other systems' incidents. A platform that
diagnoses production failures but cannot account for its own behavior is
unacceptable on two grounds: operationally (platform SREs cannot debug it)
and institutionally (no security or compliance review will grant write
access to a black box). The standing requirement is therefore:

> **Every workflow run is fully reconstructable, after the fact, from its
> OTel trace plus its audit-ledger segment — every FSM transition, every
> agent invocation, every model call, every tool call, every policy
> decision, every approval, with inputs identified (by hash and Evidence
> ID) and outputs recorded.**

The audit ledger is the tamper-evident *legal* record (hash-chained,
append-only — [05-security.md](05-security.md)); OTel is the
high-cardinality *engineering* record. They share correlation IDs so either
can be joined to the other. Reconstruction is not aspirational: the eval
service's replay harness consumes exactly these records
([10-evaluation.md](10-evaluation.md)), so any gap in instrumentation
surfaces immediately as a replay failure.

## 2. OpenTelemetry everywhere

### 2.1 Trace model

**One trace per workflow run.** The trace ID is minted when the normalizer
admits an incident (before the Temporal workflow starts, so admission
decisions are on the same trace) and lives until a terminal FSM state.

Span hierarchy:

```
trace: incident workflow run (inc-{tenant}-{incident_id})
└── span: workflow            (aetherops.workflow)
    ├── span: fsm_state       (e.g. INVESTIGATING)         ── one per state entry
    │   ├── span: agent_invocation  (e.g. rootcause_agent)
    │   │   ├── span: model_call    (gateway → provider)   ── one per call/retry
    │   │   ├── span: tool_call     (gateway → connector)  ── one per federated read
    │   │   └── span: validation    (schema / citation checks)
    │   └── span: agent_invocation  (…parallel agents share the state span…)
    ├── span: fsm_state       (POLICY_CHECK)
    │   └── span: policy_eval (OPA query + decision)
    ├── span: fsm_state       (AWAITING_APPROVAL)          ── duration = gate wait
    └── span: fsm_state       (EXECUTING) … etc.
```

Long-lived gates (AWAITING_APPROVAL can span hours) are represented as a
state span with explicit `gate.opened_at` / `gate.closed_at` events rather
than a single held-open span, so trace backends with span-duration limits
still render the workflow correctly.

### 2.2 Standard span attributes

Every span carries the applicable subset; attribute names are a versioned
schema owned by the Governance plane (breaking changes require a schema
version bump, mirrored in the metering records of §7).

| Attribute | Type | On spans | Notes |
|---|---|---|---|
| `aetherops.workflow_id` | string | all | `inc-{tenant}-{incident_id}` |
| `aetherops.incident_id` | string | all | Join key to audit ledger and incident API |
| `aetherops.tenant` | string | all | Cell-local tenant slug |
| `aetherops.fsm_state` | string | fsm_state and below | One of the 13 FSM states |
| `aetherops.agent` | string | agent_invocation and below | e.g. `triage`, `rootcause`, `planner` (roster: [02-agents.md](02-agents.md)) |
| `aetherops.model_id` | string | model_call | e.g. `claude-sonnet-5`, `claude-opus-5` |
| `aetherops.tier` | string | model_call | `fast` / `standard` / `reasoning` / `frontier` |
| `aetherops.tokens_in` / `tokens_out` | int | model_call | From provider usage block, not estimated |
| `aetherops.cost_usd` | double | model_call, tool_call | Computed at emit time from the metering rate card |
| `aetherops.confidence` | double | agent_invocation | Calibrated score as defined in [01-architecture.md](01-architecture.md) §9 |
| `aetherops.evidence_count` | int | agent_invocation | Evidence records cited in the output |
| `aetherops.prompt_hash` | string | model_call | SHA-256 of rendered prompt (§3) |
| `aetherops.prompt_template` | string | model_call | Template ID + version, e.g. `rootcause/v14` |
| `aetherops.policy_decision` | string | policy_eval | `allow` / `deny` / `require_approval` + rule ID |
| `aetherops.approval_tier` | int | policy_eval, fsm_state | Tier demanded by policy ([05-security.md](05-security.md)) |
| `aetherops.connector` | string | tool_call | e.g. `datadog`, `github`, `k8s` |
| `aetherops.cache` | string | tool_call | `hit` / `miss` / `stale-fallback` |

### 2.3 Context propagation across async boundaries

- **Kafka:** W3C `traceparent`/`tracestate` are injected into Kafka record
  headers by every producer and extracted by every consumer (the OTel Kafka
  instrumentation does this; the normalizer additionally promotes the
  admitted signal's context to become the workflow trace root). Storm
  collapse (see [08-scalability.md](08-scalability.md) §4.2) records the
  collapsed signals' trace IDs as span links on the surviving incident
  trace, so suppressed alerts remain findable.
- **Temporal:** trace context travels in Temporal headers via
  interceptors — the client interceptor injects context into
  workflow/activity headers; worker interceptors extract it and open the
  activity's span as a child of the calling state span. Because Temporal
  replays workflow code, span creation for *workflow* code happens only on
  non-replay execution (guarded by the SDK's replay flag); activities are
  side-effect-free to instrument. `aetherops.workflow_id` is also a Temporal
  search attribute, so Temporal Web and the trace backend cross-link 1:1.

## 3. Agent execution tracing

Agent invocations are the spans platform engineers actually debug, so they
carry the most structure:

- **Prompt hash, not raw prompt.** Rendered prompts embed Evidence excerpts,
  which can contain tenant secrets and PII surfaced from logs and configs.
  Raw prompts in a telemetry backend would silently re-classify that backend
  to the highest data classification in the estate. We record
  `prompt_hash` + `prompt_template` version instead; the hash is sufficient
  to prove *which* prompt was sent (the eval replay harness re-renders and
  re-hashes deterministically from workflow state + template version).
- **Evidence IDs used** (inputs) and **Evidence IDs cited** (outputs) as
  span attributes — this is what makes "which evidence led to this
  conclusion" a query, not an archaeology project.
- **Schema validation attempts:** each structured-output validation pass is
  a `validation` span with outcome (`valid` / `invalid` / `repaired`),
  failing JSON-pointer paths, and retry index.
- **Retries:** every model-call retry is its own span with
  `retry.reason` (`schema_invalid`, `provider_5xx`, `timeout`, `refusal`).

**RESTRICTED debug store.** A sampled slice of full payloads (rendered
prompt, raw completion) — 1% baseline, 100% for workflows flagged by the
eval service, and on-demand per incident via a break-glass toggle — is
written to a separate store classified RESTRICTED: access limited to
platform engineers with an audited access path, 72-hour TTL, hard-deleted,
never replicated cross-cell ([05-security.md](05-security.md)). This is the
escape hatch that keeps the main telemetry backend at INTERNAL
classification while preserving deep-debug capability.

## 4. Metrics catalog

Prometheus/OTel metrics; all carry `cell`, `tenant`, and where applicable
`agent`, `tier`, `connector`, `fsm_state` labels.

| Metric | Type | Purpose |
|---|---|---|
| `agent_invocation_duration_seconds` | histogram per agent | Per-agent latency distributions |
| `agent_invocation_total{outcome}` | counter | Success / schema-fail / timeout / escalated rates per agent |
| `agent_confidence` | histogram per agent | Confidence distribution; drift here precedes quality regressions |
| `tool_call_total{connector,outcome}` | counter | Per-connector success rate; feeds circuit breakers ([11-failure-handling.md](11-failure-handling.md)) |
| `tool_call_duration_seconds{connector}` | histogram | Upstream latency; bulkhead tuning input |
| `evidence_cache_hits_total{result}` | counter | Cache hit / miss / stale-fallback rates |
| `kafka_consumer_lag{topic,partition}` | gauge | Backpressure signal; KEDA input |
| `temporal_task_queue_backlog{queue}` | gauge | Per-agent-type queue depth; KEDA input |
| `model_tokens_total{tier,direction}` | counter | Token consumption per tier (in/out) |
| `model_gateway_queue_depth{tier,severity}` | gauge | Token-budget backpressure visibility |
| `gate_wait_seconds{approval_tier}` | histogram | Time in AWAITING_APPROVAL per tier |
| `fsm_transition_duration_seconds{from,to}` | histogram | Time spent in each FSM state |
| `verification_pass_total{outcome}` | counter | VERIFYING outcomes: pass / fail→ROLLING_BACK |
| `rollback_total{reason}` | counter | Rollback rate — a first-order quality signal |
| `workflow_cost_usd` | histogram | End-to-end cost per workflow; unit-economics input |
| `storm_collapse_ratio` | gauge | Alerts collapsed per incident during storm mode |

## 5. Structured logging standards

- **JSON only**, one event per line; schema-versioned envelope:
  `ts, level, cell, service, workflow_id, incident_id, trace_id, span_id,
  event, fields`.
- **Correlation mandatory:** any log line emitted while a workflow context
  is active must carry `workflow_id` and `trace_id`; the logging layer
  injects them from context — hand-rolled loggers are lint-failed in CI.
- **Classification-aware:** the log pipeline runs at INTERNAL
  classification. Evidence *content* (excerpts, prompt text, tool-result
  bodies) is never logged above INTERNAL — logs reference Evidence IDs and
  hashes instead, mirroring §3. A redaction filter in the log shipper
  drops known-shape secrets (tokens, connection strings) as defense in
  depth; a redaction *hit* is itself an alertable event because it means a
  service tried to log something it should not have.
- **Levels:** `ERROR` pages someone or explains a page; `WARN` is
  actionable within a day; `INFO` narrates FSM-relevant events;
  `DEBUG` is sampled and off by default in production cells.

## 6. Dashboards

Four standing dashboards per cell, plus a fleet rollup.

**Cost** ([13-cost-model.md](13-cost-model.md) is the model; this is the
live view): token spend as tokens × tier × tenant × workflow-type; unit
cost per resolved incident (workflow cost ÷ RESOLVED count, trended);
budget burn per tenant against contract; top-10 most expensive workflows
with one-click trace links.

**Latency:** per-FSM-state duration percentiles (p50/p95/p99);
time-to-diagnosis (DETECTED → DIAGNOSED) and time-to-triage
(DETECTED → TRIAGED) against SLO; time-in-gate by approval tier — the
canonical "the platform was fast, the humans were the long pole" chart,
which matters for MTTR attribution.

**Failure analytics:** failure-class Pareto of platform-side failures
(schema-invalid, connector-gap, timeout, refusal…); agent × retry-reason
heatmap; hallucination-flag rate from the Evaluation service
([10-evaluation.md](10-evaluation.md)) trended per agent and per
prompt-template version — a step change after a template rollout is a
rollback trigger; escalation-reason breakdown.

**Capacity:** storm headroom — current sustained throughput vs. drilled
storm capacity per component (Sense ingest, Temporal transitions/s, model
gateway tokens/s, per-connector limits), expressed as "minutes of 100×
storm absorbable at current floor" (see
[08-scalability.md](08-scalability.md) §5.1).

## 7. LLM token analytics

Every model call produces a metering record — emitted by the model gateway,
not the agents, so it cannot be forgotten — to Kafka topic
`metering.model_calls`:

```
{ ts, cell, tenant, workflow_id, incident_id, fsm_state, agent,
  prompt_template, prompt_template_version, model_id, tier,
  tokens_in, tokens_out, cached_tokens, cost_usd, latency_ms,
  outcome, retry_index, schema_version }
```

The topic feeds two consumers: near-real-time aggregation for the cost
dashboard (§6) and the analytics warehouse for long-horizon queries
(retention and schema in [12-apis-and-storage.md](12-apis-and-storage.md)).

**Anomaly detection on token spikes.** A per-(agent, prompt-template
version) baseline of tokens_in per invocation is maintained; sustained
deviation beyond banded thresholds raises a `token-anomaly` alert. The
canonical failure it catches is **runaway prompt growth**: an agent
accumulating context across retries, or an evidence-formatting change that
silently doubles prompt size — invisible in latency, ruinous in cost, and
detectable within an hour from this baseline. Detection at (agent,
template-version) granularity means the alert names the culprit directly:
"rootcause/v15 consumes 2.4× the input tokens of v14" is an actionable
statement; "spend is up 30%" is not.

## 8. Alerting on the platform itself

The platform is monitored with the same discipline it applies to the
estate. SLOs, measured per cell over 30-day windows:

| SLO | Target | Measurement |
|---|---|---|
| Time-to-triage p95 (DETECTED → TRIAGED) | ≤ 60 s | `fsm_transition_duration_seconds` |
| Diagnosis latency p95 (DETECTED → DIAGNOSED) | ≤ 10 min (SEV2 class) | same |
| Gate-to-execution latency p95 (approval granted → EXECUTING) | ≤ 60 s | Platform-owned portion only; human wait time is reported, not SLO'd |
| Verification success rate | ≥ 97% of EXECUTING workflows reach RESOLVED without ROLLING_BACK | `verification_pass_total`, `rollback_total` |
| Ingest availability | 99.95% of signals admitted or explicitly suppressed | Sense-plane synthetic probes |

**Burn-rate alerting:** dual-window multiwindow burn-rate rules per SLO
(fast: 5 min/1 h windows at 14× burn → page; slow: 1 h/6 h at 6× burn →
page; 6 h/3 d at 1× → ticket). Threshold-only alerts are reserved for
binary conditions (breaker open, queue at capacity, redaction hit).

**Who gets paged:** the **platform SRE** rotation owns all platform SLOs —
never tenant on-call engineers, who must be able to trust that a page from
AetherOps is always about *their* systems, not about AetherOps itself.
Escalation ladder: platform SRE → platform engineering lead → declared
platform incident. A declared platform incident of sufficient severity
(model quality regression, verification success below floor, audit-ledger
write failures) triggers **advisory-only mode** — the cell stops entering
EXECUTING, continues producing diagnoses with evidence bundles, and routes
all remediation to humans — degrading value, never trust (mode semantics:
[11-failure-handling.md](11-failure-handling.md); capacity view:
[08-scalability.md](08-scalability.md) §7).

Platform incidents are run through AetherOps' own incident process where
possible (a designated cell watches the others), and every platform
incident becomes an eval-service case: the platform's postmortems feed the
same learning loop it sells ([10-evaluation.md](10-evaluation.md)).
