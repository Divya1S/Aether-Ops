# 12 — APIs, Events, and Storage Design

The platform's external contract (REST), internal contract (gRPC), event
backbone (Kafka), and persistence layer. Terminology follows
[01-architecture.md](01-architecture.md); security enforcement is specified
in [05-security.md](05-security.md); retention and cost implications feed
[13-cost-model.md](13-cost-model.md).

---

## 1. API design philosophy

| Surface | Protocol | Contract source | Consumers |
|---|---|---|---|
| External (tenant-facing) | REST + JSON over HTTPS | OpenAPI 3.1, spec-first, generated SDKs | Engineer tooling, CI/CD hooks, dashboards, ChatOps backends |
| Internal (service-to-service) | gRPC + protobuf, mTLS/SPIFFE | `.proto` registry, versioned with the Step Catalog | All five planes |
| Async (both) | Webhooks + Kafka event subscriptions | CloudEvents 1.0 envelope (§4) | Tenant systems, Governance plane, cross-cell replication |

Rules applying to the entire external surface:

1. **OpenAPI-first.** The spec is the reviewed artifact; handlers are
   generated stubs. The gateway rejects unspecified paths.
2. **Everything async-capable.** Any operation that can outlive an HTTP
   request returns `202 Accepted` with a resource URL; completion is
   observable via webhook or event subscription — polling never required.
3. **All mutating endpoints are idempotent via `Idempotency-Key`.** The
   gateway stores `(tenant, route, key) → response` for 24 h (Redis, §5.3)
   and replays it on retry — non-idempotent retries against an execution
   platform are how you get two rollbacks.
4. **Errors are RFC 9457 problem documents** with a stable `type` URI plus
   `workflow_run_id`/`trace_id` where applicable; internal taxonomies never leak.

**Why REST external / gRPC internal:** the external audience is 50,000
engineers with heterogeneous tooling — REST+JSON+OpenAPI is the contract
every CI system, script, and dashboard already speaks; internally we control
both ends, so gRPC's typed contracts and deadline propagation (which carries
budget enforcement, [07-model-gateway.md](07-model-gateway.md)) are worth the
toolchain. **Alternatives considered:** gRPC everywhere with JSON transcoding
(leaks proto idioms; webhook receivers still need plain HTTP); GraphQL
external (unbounded query shapes are hostile to rate limiting and audit);
REST internal (loses typed contracts and deadlines). **Trade-offs:** two
contract toolchains, mitigated by generating both from one schema registry in
CI. **Operational implications:** external versioning is path-based (`/v1/`)
with 12-month deprecation windows; internal protos are additive-only,
linter-enforced. If the idempotency-key store is down, mutating endpoints
fail closed with `503` rather than risk replay ambiguity.

---

## 2. Authentication and authorization

- **AuthN:** OIDC bearer tokens from the tenant's enterprise IdP, validated
  at the global API gateway; machine callers use client-credential grants.
- **Tenancy:** every request carries `X-AetherOps-Tenant`; the global gateway
  maps tenant → cell and routes. Token tenant claim and header must agree.
- **AuthZ:** RBAC roles (`viewer`, `operator`, `approver`, `policy-admin`,
  `connector-admin`, `eval-admin`) plus ABAC attributes (service ownership,
  environment, business unit) evaluated by OPA at the gateway and
  re-evaluated at the owning service ([05-security.md](05-security.md) §3).

| Endpoint group | Minimum role | ABAC constraints |
|---|---|---|
| Incidents (read) | `viewer` | Service ownership or org-wide read grant |
| Incidents (create/mutate) | `operator` | Service ownership |
| Workflows (signals/cancel) | `operator` | Owns incident's service; cancel while EXECUTING requires `approver` |
| Approvals (decide) | `approver` | Must satisfy the gate's tier routing; self-approval of own change denied |
| Step Catalog / Plans (read) | `viewer` | Plans redacted to evidence the caller may see |
| Change Intelligence | `operator` | Repo/service ownership; CI service identities allowed |
| Policies | `policy-admin` | Environment-scoped; dry-run open to `operator` |
| Evals | `eval-admin` | Aggregate results readable by `viewer` |
| Connectors admin | `connector-admin` | Per-connector, per-tenant |
| Memory query | `viewer` | Classification-filtered ([06-retrieval-and-memory.md](06-retrieval-and-memory.md)) |

---

## 3. Public API catalog

### 3.1 Incidents API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/incidents` | Manually open an incident (most originate from the Sense plane) |
| `GET` | `/v1/incidents/{id}` | Incident record incl. FSM state, confidence, failure class |
| `GET` | `/v1/incidents/{id}/evidence` | Paginated Evidence records with citations |
| `GET` | `/v1/incidents/{id}/timeline` | Ordered state transitions, agent outputs, approvals, actions |

Example — create:

```json
POST /v1/incidents
Authorization: Bearer <oidc-jwt>
X-AetherOps-Tenant: retail
Idempotency-Key: 7f9c2d1e-ae30-4c11-9f5a-2b6f0d8f41aa

{
  "severity": "SEV2", "service": "checkout",
  "title": "checkout p99 latency regression after 14:02 deploy",
  "source": {"system": "pagerduty", "ref": "PD-INC-88213"},
  "annotations": {"suspected_change": "deploy-2026-08-07-1402"}
}
```

```json
HTTP/1.1 202 Accepted
Location: /v1/incidents/inc_01J9WXYZ8Q

{
  "id": "inc_01J9WXYZ8Q", "state": "DETECTED", "severity": "SEV2",
  "service": "checkout", "workflow_run_id": "wfr_01J9WXZ2MK",
  "links": {"timeline": "/v1/incidents/inc_01J9WXYZ8Q/timeline", "evidence": "/v1/incidents/inc_01J9WXYZ8Q/evidence"}
}
```

Example — evidence read (citation mandatory on every record):

```json
GET /v1/incidents/inc_01J9WXYZ8Q/evidence?limit=1

{
  "items": [{
    "id": "ev_01J9WY0A4T",
    "source": "datadog",
    "excerpt": "checkout p99 1240ms (baseline 210ms) from 14:03Z",
    "citation": {"source": "datadog", "ref": "https://app.datadoghq.com/metric/…?query=p99:checkout", "retrieved_at": "2026-08-07T14:06:12Z"},
    "classification": "internal",
    "staleness": null
  }],
  "next": null
}
```

### 3.2 Workflows API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/workflows/{id}` | Workflow run: FSM state, current step, gates, budgets consumed |
| `POST` | `/v1/workflows/{id}/signals` | Deliver a typed signal (`human-context-added`, `hold`, `resume`) |
| `POST` | `/v1/workflows/{id}/cancel` | Cancel with compensation: saga runs registered undo handlers ([03-orchestration.md](03-orchestration.md)) |

Signals are an enumerated set defined in the workflow's proto contract —
arbitrary payload injection into a running workflow is not a feature; cancel
on an `EXECUTING` workflow transitions through `ROLLING_BACK`, never an
abrupt mid-step halt.

### 3.3 Approvals API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/approvals?pending=true` | Pending gates routed to the caller (same source of truth as Slack/Teams cards) |
| `POST` | `/v1/approvals/{id}/decision` | Approve/deny with fencing token |

Every gate issue carries a **fencing token** — a monotonic counter per gate,
incremented whenever the gate is re-issued (timeout, plan revision, storm
re-route). A decision must echo the token; a stale decision — approving a
plan that has since changed — is rejected with `409`, closing the
approve-after-revise race without locking.

```json
POST /v1/approvals/apr_01J9WY7K2E/decision
Idempotency-Key: 0b54c7e6-90d3-4bfa-8f2e-6f2a9c1d7e55

{"decision": "approve", "fencing_token": 3, "comment": "Rollback OK; freeze ends 15:00Z"}
```

```json
HTTP/1.1 200 OK

{
  "id": "apr_01J9WY7K2E", "status": "APPROVED", "fencing_token": 3,
  "decided_by": "alice@corp.example", "workflow_run_id": "wfr_01J9WXZ2MK",
  "effect": "workflow resumed at step k8s.rollback_deployment"
}
```

### 3.4 Plans, change intelligence, policies, evals, connectors, memory

| Group | Method | Path | Purpose |
|---|---|---|---|
| Step Catalog | `GET` | `/v1/step-catalog` | Versioned catalog: step name, args schema, risk class, approval tier, compensation |
| Plans | `GET` | `/v1/plans/{id}` | Compiled plan DAG: steps, dependencies, policy annotations, linked evidence |
| Change Intelligence | `POST` | `/v1/changes/score` | Score a PR/deploy against incident history, ownership graph, blast radius |
| Policies | `GET` | `/v1/policies/bundles` | Deployed OPA bundle versions and status per cell |
| Policies | `POST` | `/v1/policies/evaluate` | Dry-run: would this (principal, step, context) be allowed, at which tier? |
| Evals | `POST` | `/v1/evals/runs` | Launch a replay/eval run against a golden set ([10-evaluation.md](10-evaluation.md)) |
| Evals | `GET` | `/v1/evals/runs/{id}` | Run status and progress |
| Evals | `GET` | `/v1/evals/runs/{id}/results` | Metric results, per-episode outcomes, judge verdicts |
| Connectors | `GET` | `/v1/connectors` | Per-tenant connector inventory and health ([04-connectivity.md](04-connectivity.md)) |
| Connectors | `GET` | `/v1/connectors/{name}/scopes` | Effective scope map vs. best-available credential rung |
| Connectors | `GET` | `/v1/connectors/{name}/quotas` | Token-bucket state, upstream 429 rates, storm-mode status |
| Connectors | `POST` | `/v1/connectors/{name}/credentials/rotate` | Force credential rotation (idempotent) |
| Memory | `POST` | `/v1/memory/query` | Hybrid search over episodes/runbooks/docs, classification-filtered |
| Memory | `GET` | `/v1/memory/episodes/{id}` | Distilled episode: failure class, causal chain, remediation efficacy |

Group notes: plans are created only by the Plan Compiler — there is
deliberately no `POST /v1/plans`. `/v1/changes/score` (called from CI,
~50,000 evaluations/month) returns a risk score, contributing factors with
citations, and a gating recommendation; async `202` + webhook for batch
re-scoring. Policy dry-run evaluates live bundles but never executes, so
owners preview gating before enabling auto-path. Memory results are filtered
by caller clearance **before** ranking — a caller never learns that a
restricted episode exists.

---

## 4. Event topics (Kafka)

All events use a CloudEvents 1.0 envelope; payloads are versioned Avro
schemas with backward-compatible evolution enforced in CI:

```json
{
  "specversion": "1.0", "id": "evt_01J9WY9QX3",
  "source": "/cells/us-e-1/control/workflow-engine",
  "type": "io.aetherops.incidents.state-changed.v1",
  "subject": "inc_01J9WXYZ8Q", "time": "2026-08-07T14:31:02Z", "tenantid": "retail",
  "data": {"from": "PLANNED", "to": "POLICY_CHECK", "workflow_run_id": "wfr_01J9WXZ2MK"}
}
```

| Topic | Partition key | Retention | Producer → consumers |
|---|---|---|---|
| `signals.raw` | `(tenant, source_system)` | 3 days | Webhook receivers → alert normalizer |
| `incidents.detected` | `incident_id` | 30 days | Normalizer → workflow triggers, dashboards |
| `incidents.state-changed` | `incident_id` | 90 days | Workflow engine → timeline svc, notifications, metering |
| `evidence.recorded` | `incident_id` | 30 days | Retrieval svc → working memory, audit tap |
| `plans.proposed` | `incident_id` | 90 days | Plan compiler → approval svc, audit tap |
| `approvals.requested` | `workflow_run_id` | 90 days | Approval svc → Slack/Teams surfaces, escalation timers |
| `approvals.decided` | `workflow_run_id` | 90 days | Approval svc → workflow engine, audit tap |
| `actions.executed` | `workflow_run_id` | 90 days | Connector gateway → verifier triggers, audit tap |
| `verifications.completed` | `incident_id` | 90 days | Verifier agent → workflow engine, learning pipeline |
| `audit.events` | `(tenant, seq_bucket)` | 7 days (buffer) | All planes → audit ledger writer (§5.1), SIEM export |
| `metering.tokens` | `(tenant, workflow_run_id)` | 30 days | Model gateway → cost metering ([13-cost-model.md](13-cost-model.md)) |

**Why Kafka topics as the integration spine:** the Governance plane taps
everything without coupling producers to consumers, and cross-cell memory
replication ([08-scalability.md](08-scalability.md)) consumes the same
streams. **Alternatives considered:** direct RPC fan-out (couples producers
to every consumer's availability); a single `events` topic (no per-class
retention or ACLs). **Trade-off:** eleven topics × per-tenant ACLs is real
operational surface; partition keys preserve per-incident ordering where
consumers depend on it. **Operational implication:** `audit.events` is a
buffer, not the ledger — ledger-writer consumer lag pages at 60 s.

---

## 5. Storage design

Principle inherited from [01-architecture.md](01-architecture.md) §6:
petabyte telemetry stays in the systems of record; AetherOps stores
**pointers, excerpts, decisions, and provenance** — gigabytes per cell.

### 5.1 Postgres (per cell, HA)

System of record for workflow and governance state. Large tables are
partitioned monthly by time with `tenant_id` leading every index — partition
drops keep churn local; tenant-first indexes keep queries tenant-pruned.

```sql
CREATE TABLE incidents (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('SEV1','SEV2','SEV3')),
  state TEXT NOT NULL,                     -- FSM states, 01 §3
  service TEXT NOT NULL, failure_class TEXT, title TEXT NOT NULL,
  source JSONB NOT NULL, detected_at TIMESTAMPTZ NOT NULL,
  resolved_at TIMESTAMPTZ, workflow_run_id UUID
) PARTITION BY RANGE (detected_at);

CREATE TABLE workflow_runs (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, incident_id UUID,
  workflow_type TEXT NOT NULL,             -- remediation | triage-only | change-score
  temporal_run_ref TEXT NOT NULL,          -- Temporal owns execution state; this row is the query index
  state TEXT NOT NULL,
  budgets JSONB NOT NULL,                  -- token/tool/cost budgets granted & consumed
  started_at TIMESTAMPTZ NOT NULL, closed_at TIMESTAMPTZ
) PARTITION BY RANGE (started_at);

CREATE TABLE evidence (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, incident_id UUID NOT NULL,
  source TEXT NOT NULL, citation_ref TEXT NOT NULL,   -- connector; URI into system of record
  excerpt BYTEA NOT NULL, excerpt_sha256 BYTEA NOT NULL,  -- encrypted, redacted excerpt
  blob_ref TEXT,                           -- object-store key when > 32 KiB
  classification TEXT NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL,
  encryption_key_id TEXT NOT NULL          -- per-tenant DEK; enables crypto-shredding (§6)
) PARTITION BY RANGE (retrieved_at);
CREATE INDEX ev_incident ON evidence (tenant_id, incident_id, retrieved_at);

CREATE TABLE plans (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, incident_id UUID NOT NULL,
  revision INT NOT NULL, dag JSONB NOT NULL,  -- Step Catalog refs only; validated at compile
  policy_result JSONB NOT NULL,            -- per-step risk class, tier, OPA decision id
  evidence_ids UUID[] NOT NULL, created_at TIMESTAMPTZ NOT NULL,
  UNIQUE (incident_id, revision)
) PARTITION BY RANGE (created_at);

CREATE TABLE approvals (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, workflow_run_id UUID NOT NULL,
  gate_id TEXT NOT NULL, tier SMALLINT NOT NULL,
  fencing_token BIGINT NOT NULL,           -- monotonic per (workflow_run_id, gate_id)
  status TEXT NOT NULL,                    -- PENDING|APPROVED|DENIED|EXPIRED|SUPERSEDED
  requested_at TIMESTAMPTZ NOT NULL, decided_at TIMESTAMPTZ,
  approver_subject TEXT, reason TEXT,      -- OIDC subject; feeds on-behalf-of (04 §4.3)
  UNIQUE (workflow_run_id, gate_id, fencing_token)
) PARTITION BY RANGE (requested_at);

CREATE TABLE audit_ledger (
  seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id TEXT NOT NULL, event_type TEXT NOT NULL, actor JSONB NOT NULL,
  payload JSONB NOT NULL,                  -- references + hashes; PII pseudonymized (§6)
  payload_sha256 BYTEA NOT NULL, prev_hash BYTEA NOT NULL,
  entry_hash BYTEA NOT NULL,               -- SHA-256(prev_hash ‖ payload_sha256 ‖ seq ‖ recorded_at)
  recorded_at TIMESTAMPTZ NOT NULL
);  -- append-only: no UPDATE/DELETE grants exist for any role

CREATE TABLE calibration_weights (
  agent TEXT NOT NULL, failure_class TEXT NOT NULL, tenant_id TEXT NOT NULL,
  weight DOUBLE PRECISION NOT NULL,        -- historical-calibration factor (01 §9)
  brier_score DOUBLE PRECISION NOT NULL, sample_count INT NOT NULL,
  window_start TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (agent, failure_class, tenant_id)
);

CREATE TABLE memory_episodes (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, incident_id UUID NOT NULL,
  failure_class TEXT NOT NULL,
  causal_chain JSONB NOT NULL, remediation JSONB NOT NULL,  -- sanitized, classified (01 §7)
  efficacy REAL,                           -- verified outcome score
  embedding_ref TEXT,                      -- Qdrant point id (episodes collection)
  sanitized BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL
) PARTITION BY RANGE (created_at);
```

Retention per table (hot tier; archival in §5.2/§6):

| Table | Hot retention | Then |
|---|---|---|
| `incidents`, `workflow_runs`, `plans`, `approvals` | 13 months | Parquet archive to object store, drop partition |
| `evidence` | 90 days (cited evidence held while parent incident is hot) | Archive; blob refs follow |
| `audit_ledger` | 13 months | Sealed segments to WORM archive (mirrored continuously) |
| `calibration_weights` | Current + 8 quarterly snapshots | — |
| `memory_episodes` | Indefinite — this is the organization's learning | Re-embedded on model change |

pgvector served the first vector workload; production retrieval runs on
Qdrant (§5.4), with Postgres the durable source of truth — vectors are
always rebuildable.

### 5.2 Object store (per cell, cross-region replicated)

| Bucket class | Contents | Controls |
|---|---|---|
| `audit-worm` | Sealed audit ledger segments (daily), approval card renders | Object Lock compliance mode, 7-year retention, no delete API |
| `evidence-blobs` | Large evidence bodies (log slices, diffs) via `evidence.blob_ref` | SSE with per-tenant KMS keys; cold tier at 90 days |
| `archives` | Dropped Postgres partitions (Parquet), eval artifacts, golden-set snapshots ([10-evaluation.md](10-evaluation.md)) | Versioned; cold tier at 6 months |

### 5.3 Redis (per cell)

| Namespace | Contents | TTL |
|---|---|---|
| `toolres:` | Connector gateway hot cache ([04-connectivity.md](04-connectivity.md) §5) | Per TTL class |
| `idem:` | Idempotency-key → response | 24 h |
| `wfbudget:` | Live per-workflow token/tool budget counters | Workflow lifetime + 1 h |
| `rate:` | Token buckets (tenant, connector, class) | Rolling |
| `dedupe:` | Signal dedup / storm-suppression fingerprints | 15 min |

Redis is cache and coordination only — every value is rebuildable; losing
Redis degrades latency and hit rate, never correctness.

### 5.4 Qdrant collections (per cell)

Embedding dimension 1024 (embedding model and dim are working assumptions;
the re-embedding pipeline treats dim as config). Cosine distance, HNSW.

| Collection | Vectors | Payload schema (filterable) |
|---|---|---|
| `episodes` | Distilled incident episodes | `tenant_id`, `failure_class`, `service`, `severity`, `classification`, `created_at`, `efficacy` |
| `runbooks` | Runbook/playbook chunks | `tenant_id`, `service`, `source_ref`, `classification`, `verified` |
| `docs` | Postmortems, architecture docs, ADR chunks | `tenant_id`, `source_system`, `source_ref`, `classification`, `chunk_ix` |

Classification is a filter applied at query time from the caller's clearance
(§3.4). Snapshots nightly; full rebuild from Postgres and source systems is
a tested runbook (≤ 4 h per cell).

### 5.5 Neo4j (per cell)

The organizational knowledge graph powering blast-radius and ownership
reasoning ([06-retrieval-and-memory.md](06-retrieval-and-memory.md)).

Node types (with properties): `Service(name, tier, environment, tenant)`,
`Team(name, oncall_ref)`, `Deployment(ref, version, deployed_at)`,
`Change(ref, risk_score, scored_at)`, `Incident(id, severity, failure_class,
detected_at)`, `FailureClass(label, taxonomy_version)`, `Runbook(ref, verified)`.

| Edge | Meaning |
|---|---|
| `(Service)-[:DEPENDS_ON {criticality}]->(Service)` | Runtime dependency (drives blast radius) |
| `(Service)-[:OWNED_BY]->(Team)` | Ownership / approval routing |
| `(Deployment)-[:DEPLOYS]->(Service)` | Change surface |
| `(Change)-[:SHIPPED_IN]->(Deployment)` | PR → deploy lineage |
| `(Incident)-[:CAUSED_BY]->(Change)` | Adjudicated root cause |
| `(Incident)-[:CLASSIFIED_AS]->(FailureClass)` | Learned taxonomy |
| `(Incident)-[:RESOLVED_BY]->(Runbook)` | Remediation provenance |

Graph writes come only from the learning pipeline and connector-driven
topology sync — agents read the graph, never write it.

---

## 6. Data lifecycle

**Hot → warm → cold.** Hot: Postgres + Redis + Qdrant (live operations,
≤ 13 months). Warm: object-store Parquet archives and evidence blobs on
infrequent-access tier (offline query, 13 months – 3 years). Cold: WORM
audit segments and compliance archives (to 7 years). Movement is
partition-drop plus lifecycle policy — never row-level churn.

**GDPR erasure.** Two data classes, two mechanisms:

1. *Mutable stores* (non-ledger Postgres, Qdrant, Redis, Neo4j): personal
   data (approver identities, chat excerpts inside evidence) is locatable via
   a subject index; erasure deletes or redacts rows and re-embeds affected
   vectors within 30 days, verified by an automated re-scan.
2. *Hash-chained audit ledger and WORM archives*: rows are never deleted —
   deletion would break `prev_hash` chains and the compliance story. The
   ledger therefore stores personal data only as **references and
   pseudonymous subject IDs**, with payload fields containing personal data
   encrypted under per-subject data keys. Erasure = **crypto-shredding**:
   destroy the subject's key, leaving those fields permanently unreadable
   while chain integrity and non-personal content remain verifiable. The
   key-destruction event is itself an audit entry.

**Backups and restore testing.** Postgres: continuous WAL archiving (RPO
≤ 5 min, matching [08-scalability.md](08-scalability.md) DR targets) plus
nightly base backups, with automated nightly restore-verification into a
scratch instance (checksum and row-count assertions). Kafka: 3× replication
plus tiered storage for long-retention topics. Qdrant/Neo4j: nightly
snapshots; both rebuildable from Postgres and source systems. Object store:
cross-region replication with Object Lock preserved on WORM buckets.
Quarterly game-day: full cell restore into standby infrastructure, timed
against the RTO ≤ 30 min target, results recorded in the operational
scorecard ([09-observability.md](09-observability.md)).
