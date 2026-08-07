# 08 — Scalability, Availability, and Disaster Recovery

Platform Infrastructure — AetherOps. Inherits terminology from
[01-architecture.md](01-architecture.md): planes, cells, Signals, Evidence,
Gates, the incident FSM. Companion docs: backpressure interacts with the
model gateway ([07-model-gateway.md](07-model-gateway.md)), the connector
gateway ([04-connectivity.md](04-connectivity.md)), and degraded-mode
semantics ([11-failure-handling.md](11-failure-handling.md)).

---

## 1. Load model: storm mode is the primary case

All sizing starts from one observation: **the platform's own worst load
arrives exactly when the estate is on fire.** A major outage multiplies alert
volume, evidence queries, model calls, and approval traffic simultaneously.
A platform sized for the average day is a platform that fails during the one
hour it exists to serve. We therefore design for storm mode as the primary
case and treat quiet operation as the optimization target, not the reverse.

### 1.1 Derived load figures

| Quantity | Steady state | Storm (100×) | Derivation |
|---|---|---|---|
| Alerts ingested | ~2,000,000/day ≈ 23/s | ~2,300/s sustained for minutes–hours | 2M / 86,400 s; 100× multiplier observed in estate-wide outages (cloud AZ loss, shared-dependency failure) |
| Signals surviving dedup/suppression | ~2–4/s | ~20–60/s | Normalizer collapses duplicates and storm-groups (§4.2) before workflow creation |
| SEV incident workflows started | ~2,000/month ≈ 2.8/hour | tens/hour | Storm detector collapses N related alerts into one incident workflow |
| Concurrent workflows (all lanes) | low thousands | ~5,000–10,000 | Incident + change-intelligence + learning + eval-replay lanes |
| Evidence tool calls | ~50–150/s | ~1,500–3,000/s | 20–80 federated reads per INVESTIGATING pass × concurrency |
| Model calls | ~10–30/s | ~300–800/s | Bounded by token-budget backpressure (§4.4), not raw demand |
| Log lines reachable | billions/day | billions/day | Federated, never copied — load lands on connector reads, not our storage |

Two consequences drive the rest of this document:

1. **Admission control must live in the Sense plane**, before workflow
   creation. 2,300 alerts/s must never become 2,300 workflows/s; Temporal,
   the agent runtime, and the model gateway are protected by construction.
2. **Every downstream component is sized to the post-suppression rate plus
   headroom**, and the suppression machinery itself (webhook receivers,
   Kafka, normalizer) is sized to the raw storm rate.

## 2. Horizontal scaling per component

| Component | Plane | Scaling mechanism | Key detail |
|---|---|---|---|
| Webhook receivers / API GW | Sense | Stateless, HPA on CPU + request rate | No local state; any pod handles any tenant; validated payload → Kafka and ack |
| Kafka (`signals.raw`, `incidents.detected`, metering topics) | Sense | Partition count + broker scale-out, RF=3 | Signals partitioned by hash(tenant, service) — see decision below |
| Alert normalizer | Sense | Kafka consumer groups | Consumers scale to partition count; dedup state in Redis keyed by (tenant, service, fingerprint) |
| Temporal | Control | History/matching shard scaling; worker pools per task queue | 4k history shards per cell (fixed at cell creation); per-agent-type task queues |
| Agent runtime | Intelligence | KEDA on task-queue depth + token-budget-aware concurrency | Pods scale on backlog, but effective concurrency is capped by remaining token budget (§5) |
| Model gateway | Intelligence | Stateless HPA; priority queues per tier | Backpressure point for the entire Intelligence plane (§4.4) |
| Connector gateway | Execution | Per-connector worker pools, HPA per pool | Bulkhead isolation: one slow upstream cannot starve others — see decision below |
| Postgres | Control/Intelligence | Vertical primary + read replicas; declarative partitioning | Partitioned by (time range, tenant); replicas serve read-heavy memory and reporting queries |
| Qdrant | Intelligence | Sharded collections, replicated | Shard key = tenant; collection per memory tier; replicas serve retrieval reads |
| Neo4j | Intelligence | Causal cluster (1 leader, ≥2 followers) per cell | Graph fits in-cell (ownership + failure-class graph is MBs–GBs, not TBs); reads on followers |
| Redis | Sense/Intelligence | Cluster mode, keyspace sharding | Dedup fingerprints, rate-limit counters, hot evidence cache |

### 2.1 Decision: Kafka partitioning by hash(tenant, service)

- **Why chosen:** Ordering matters exactly at the granularity of "alerts
  about the same service for the same tenant" — that is the unit the dedup
  and storm detector reason over. Hashing (tenant, service) to a partition
  guarantees per-(tenant, service) ordering while spreading load across
  partitions. No global ordering is needed anywhere in the platform.
- **Alternatives considered:** (a) partition by tenant only — a large tenant
  in storm saturates one partition and head-of-line-blocks its own signals;
  (b) round-robin — maximal spread but loses ordering, forcing the
  normalizer to re-sequence with windows and timestamps, which is exactly
  the fragile logic Kafka partitioning exists to avoid; (c) partition by
  alert fingerprint — ordering too fine-grained, dedup state fragments.
- **Trade-offs:** a single pathological (tenant, service) pair still maps to
  one partition; we accept this because per-key throughput ceilings
  (~10k msg/s/partition) exceed any plausible single-service storm after
  receiver-side coalescing, and the storm detector collapses the stream
  anyway.
- **Operational implications:** partition count is provisioned for storm
  (raw storm rate / per-partition ceiling, ×2 headroom → 128 partitions per
  cell for `signals.raw`); repartitioning is an online but
  ordering-disruptive operation, so it is scheduled, never automatic.
  Consumer-lag per partition is a first-class SLO input
  ([09-observability.md](09-observability.md)).

### 2.2 Decision: Temporal shard and worker topology

- **Why chosen:** Temporal history shards are fixed at cluster creation, so
  we provision 4k shards per cell — far above steady concurrency — because
  shards are cheap when idle and resharding is a migration, not a knob.
  Workers are split into pools per task queue, with a **task queue per agent
  type** (`tq.triage`, `tq.rootcause`, `tq.planner`, …) plus queues for
  connector activities and verification timers.
- **Alternatives considered:** (a) one shared worker pool and task queue —
  simplest, but a burst of long-running Root Cause activities starves
  cheap Triage activities, inverting the priority we want during storms;
  (b) a queue per tenant — clean isolation but thousands of queues defeat
  Temporal's matching efficiency and complicate autoscaling.
- **Trade-offs:** per-agent-type queues mean more deployments to manage and
  capacity fragmentation (an idle Planner pool cannot absorb Triage
  backlog). We accept fragmentation because it is exactly what makes
  per-agent-type KEDA scaling and per-agent-type token budgets enforceable.
- **Operational implications:** each agent type scales, alerts, and is
  budgeted independently; a misbehaving agent type is throttled by shrinking
  its pool without touching the rest of the FSM.

### 2.3 Decision: connector gateway bulkhead isolation

- **Why chosen:** Per-connector worker pools with per-connector concurrency
  limits, timeouts, and circuit breakers. During an estate outage the
  slowest upstream (often the observability vendor that is itself degraded)
  would otherwise absorb every gateway thread; bulkheads confine the damage
  to that connector's pool while GitHub, K8s, and PagerDuty reads proceed.
- **Alternatives considered:** shared async pool with per-request deadlines
  — fewer moving parts, but deadline enforcement under memory pressure is
  unreliable and one connector's retry amplification still consumes shared
  connection and CPU budget.
- **Trade-offs:** N pools cost idle capacity and per-connector tuning. The
  idle cost is small (pools scale to a floor of 1–2 pods); the tuning cost
  is real and is owned by the connector team via per-connector runbooks
  ([04-connectivity.md](04-connectivity.md)).
- **Operational implications:** a connector outage manifests as one pool's
  breaker opening and an explicit evidence gap
  ([11-failure-handling.md](11-failure-handling.md) §3) — never as
  gateway-wide latency.

### 2.4 Decision: Postgres partitioning and replicas

- **Why chosen:** Declarative partitioning by month sub-partitioned by
  tenant for the high-churn tables (workflow projections, evidence
  pointers, audit-index, metering). Time partitions make retention a
  `DETACH`/drop instead of a delete storm; tenant sub-partitions localize a
  noisy tenant's vacuum and index bloat. Read replicas serve memory-tier
  reads, dashboards, and the eval service so the primary handles only
  transactional writes.
- **Alternatives considered:** (a) Citus-style sharded Postgres — premature;
  per-cell write volume (thousands of rows/s at storm) is within a single
  well-partitioned primary; (b) moving high-churn tables to Cassandra —
  loses transactions with workflow state projections and adds an
  operational stack per cell.
- **Trade-offs:** replicas are async — dashboards and memory reads can lag
  seconds behind; anything correctness-critical (policy inputs, approval
  state, ledger writes) reads the primary. Vertical primary is an eventual
  ceiling; the cell-split playbook (§3.1) is the pressure valve before that
  ceiling is reached.
- **Operational implications:** partition maintenance is automated
  (pre-create next month, detach expired); replica lag is an SLO metric
  with a documented threshold that flips reads back to the primary.

### 2.5 Qdrant and Neo4j sharding

Qdrant collections are sharded by tenant with replication factor 2; storm
load is read-dominant (retrieval), so replicas scale reads while writes
(memory distillation in LEARNED) are batch and off-peak. Neo4j runs as a
per-cell causal cluster; the ownership/failure-class graph is small and
read-heavy, so followers absorb agent queries and the leader handles the
LEARNED-state writes. Neither system is on the incident critical write
path — if either is degraded, retrieval falls back to Postgres pgvector
with a recorded evidence-coverage penalty
([06-retrieval-and-memory.md](06-retrieval-and-memory.md)).

## 3. Partitioning and tenancy

### 3.1 Cell assignment

Tenants (business units) are pinned to cells by residency first, then
business-unit affinity, then capacity (see the deployment diagram in
[01-architecture.md](01-architecture.md) §7). A cell serves 5–20 tenants.
When a cell approaches 70% of any provisioned ceiling (Temporal state
transitions/s, Postgres write throughput, Kafka partition utilization), the
fleet console flags it for a cell split: a new cell is provisioned, tenants
migrate one at a time (drain new-workflow starts, replicate memory, flip
tenant→cell routing at the global gateway, let in-flight workflows finish in
the old cell).

### 3.2 Within-cell sharding

Workflow execution shards by **incident ID** (Temporal workflow ID =
`inc-{tenant}-{incident_id}`), which distributes uniformly across history
shards and makes every FSM instance independently schedulable. All
per-incident state hangs off that ID: workflow state, Evidence bundle,
audit-chain segment, OTel trace.

### 3.3 Noisy-neighbor controls

| Quota (per tenant, per cell) | Default | Enforced at | On breach |
|---|---|---|---|
| Concurrent incident workflows | 200 | Normalizer, before workflow start | Excess signals queue in Kafka; oldest-SEV-first admission |
| Workflow starts/min | 60 | Normalizer | Storm detector engages (§4.2); alerts merge into open incidents |
| Model tokens/hour per tier | per contract, e.g. 5M standard-tier | Model gateway | Tenant's calls queue behind budget; SEV1 lane exempt to a hard cap |
| Tool calls/min per connector | connector-specific (e.g. 300 GitHub reads/min) | Connector gateway | 429 to the activity → retry with jitter; counts as availability gap if sustained |
| Evidence cache storage | 50 GiB | Retrieval service | LRU eviction, oldest incidents first |

Quotas are soft-limited at 80% (emit warning event, page tenant-owning SRE)
and hard-limited at 100%. Quota consumption is a Governance-plane metric and
feeds chargeback ([13-cost-model.md](13-cost-model.md)).

## 4. Backpressure, end to end

Principle: **every queue in the platform is bounded, and every producer has
a defined behavior when its consumer is full.** Unbounded queues convert
overload into memory exhaustion and silent latency; bounded queues convert
it into explicit, prioritized shedding.

### 4.1 Admission control at the Sense plane

The Sense plane is the only tier that faces raw storm rate, so suppression
happens **before workflow creation**:

1. **Receiver coalescing:** webhook receivers batch-produce to Kafka;
   payload validation only, no lookups — receivers stay O(2,300/s) cheap.
2. **Dedup:** normalizer drops exact re-fires within the dedup window
   (Redis fingerprint, per (tenant, service, alert-key)).
3. **Storm suppression:** see §4.2.
4. **Workflow admission:** only post-suppression incident candidates check
   tenant quotas and start FSM instances.

### 4.2 Storm detector

When the normalizer sees alert arrival for a (tenant, service) — or a
correlated set of services from the dependency graph — exceed a rate/window
threshold, it stops creating one workflow per alert and instead **collapses
N related alerts into one incident workflow** whose Signal set grows as
alerts continue to arrive. Late-arriving alerts attach to the open incident
as additional Signals (visible to the Triage agent as scope evidence)
rather than spawning siblings.

- **Why chosen:** during a shared-dependency outage, 10,000 alerts describe
  one incident. One rich workflow produces a better diagnosis than 10,000
  starved ones, and it is the only shape the human on call can consume.
- **Alternatives considered:** rate-limiting workflow creation without
  grouping — protects the platform but drops information and produces
  arbitrary "which alert won" behavior; pure vendor-side grouping
  (PagerDuty AIOps) — helps but cannot be assumed present or correct across
  15,000 services.
- **Trade-offs:** aggressive collapse risks merging genuinely distinct
  incidents. Mitigation: collapse requires either identical alert keys or
  adjacency in the service dependency graph; the Triage agent can split an
  incident (fork a child workflow) when evidence shows independent causes.
- **Operational implications:** collapse decisions are audited and measured
  (`storm.collapse_ratio`, false-merge rate via the eval service,
  [10-evaluation.md](10-evaluation.md)); thresholds are per-tenant tunable.

### 4.3 Load shedding order

When a cell exceeds sustained capacity despite autoscaling, lanes shed in
strict order — and **writes are never silently shed**: any dropped or
deferred work emits an audit event and a metric.

| Shed order | Lane | Behavior under shedding |
|---|---|---|
| 1 (first) | Batch/eval lanes (eval replay, memory distillation, change-intelligence rescoring) | Pause entirely; scale to zero; resume after storm |
| 2 | Read-cache degradation | Serve cached evidence with staleness stamps instead of live federated reads; TTLs stretched, staleness recorded on each Evidence record |
| 3 (last) | Interactive incident workflows | Never shed while the cell lives; degrade per-workflow (longer queues, tier fallback per [07-model-gateway.md](07-model-gateway.md)) |
| Never | Audit ledger, approval decisions, workflow state writes | Producers block (bounded, with alerting) rather than drop; loss here is a compliance event, not a performance event |

### 4.4 Token-budget backpressure at the model gateway

The model gateway is the deliberate choke point for the Intelligence plane.
It maintains per-tier bounded queues with priority ordering by incident
severity (SEV1 > SEV2 > SEV3 > change-intelligence > batch). When provider
throughput or tenant token budget is exhausted, calls queue; queue depth and
wait time propagate backward as slower agent activities, which Temporal
absorbs durably — no work is lost, it is re-ordered by importance. Runaway
consumers hit per-workflow cost circuit breakers
([11-failure-handling.md](11-failure-handling.md) §4) before they hit the
queue's fairness.

## 5. Autoscaling

| Component | Scaler | Signal | Floor / notes |
|---|---|---|---|
| Webhook receivers | HPA | CPU + requests/s | Floor sized for 2,300/s storm ingress (§5.1) |
| Normalizer | KEDA | Kafka consumer lag on `signals.raw` | Floor 2 pods/cell; max = partition count |
| Temporal workers (per agent-type queue) | KEDA | Task-queue backlog + schedule-to-start latency | Floor 1–2 pods per queue; storm floor for Triage/RootCause pools |
| Agent runtime concurrency | In-process governor | Remaining token budget ÷ expected tokens per invocation | Pods can be plentiful while effective concurrency is token-capped — prevents scaling into a budget wall |
| Model gateway | HPA | In-flight requests + queue depth | Floor 3 pods/cell |
| Connector gateway pools | HPA per pool | Pool utilization + upstream latency | Floor 1–2 per connector; breaker-open pools do not scale up |
| Batch/eval lanes | KEDA | Backlog topics | Scale to zero; first shed lane |

### 5.1 Decision: provisioned floor for storm readiness

- **Why chosen:** autoscaling reacts in minutes; storms ramp in seconds and
  arrive exactly when node pools are contended (the estate's outage may be
  our cloud provider's bad day too). We hold a provisioned floor — receivers
  and Sense-plane capacity at full storm rate, Triage/RootCause worker
  pools and model-gateway capacity at ~25% of storm rate — so the first
  minutes of a storm are absorbed while KEDA/HPA catch up on the rest.
- **Alternatives considered:** (a) pure reactive scaling — cheapest,
  but the platform browns out during the exact minutes that define its
  value; (b) full storm capacity always on — ~4× the steady-state compute
  bill for capacity used a few hours a month.
- **Trade-offs:** the floor costs real money for idle capacity
  (quantified in [13-cost-model.md](13-cost-model.md)); we spend it at the
  Sense plane and the front of the FSM (triage/diagnosis) where seconds
  matter, and rely on reactive scaling for later FSM states where Temporal
  durability makes waiting safe.
- **Operational implications:** quarterly storm drills replay a recorded
  storm at 100× into a staging cell to validate that floor + scale-up meets
  the time-to-triage SLO ([09-observability.md](09-observability.md) §8);
  floor sizes are revisited from drill data, not intuition.

## 6. Multi-region, HA, and disaster recovery

### 6.1 Cell-internal high availability

Each cell spans three availability zones:

- **Postgres:** synchronous multi-AZ HA pair (managed failover, ~30 s),
  async read replicas.
- **Kafka:** RF=3, min.insync.replicas=2, producers `acks=all` for
  workflow-triggering topics.
- **Temporal:** ≥3 replicas per role (frontend/history/matching/worker)
  spread across AZs; persistence on the HA Postgres.
- **Qdrant/Neo4j/Redis:** replicated across AZs as per §2.5; OPA and Vault
  run clustered per cell.

Loss of one AZ is a non-event by design: no data loss, capacity drops
one-third until the node pool rebalances.

### 6.2 Cross-cell DR: paired-cell warm standby

Every cell has a designated standby cell in a different region (standbys
also serve their own tenants — capacity is reserved, not idle hardware).
Targets from [01-architecture.md](01-architecture.md): **RPO ≤ 5 min for
workflow state, RTO ≤ 30 min.**

**What replicates (async, continuous):**

| Data class | Mechanism | RPO |
|---|---|---|
| Workflow state / history | Temporal multi-cluster replication | ≤ 5 min |
| Relational state (workflow projections, Step Catalog, approvals, quotas) | Postgres streaming replication | ≤ 5 min |
| Audit ledger | Postgres streaming + WORM S3 cross-region bucket replication | ≤ 5 min (ledger segments); S3 object copies ≤ 15 min |
| Sanitized org-level memory | Explicit classification-filtering pipeline (see [01-architecture.md](01-architecture.md) §7) | ≤ 24 h — acceptable: it improves quality, it does not gate correctness |

**What never replicates:** credentials (each cell's Vault is sovereign; the
standby holds its own pre-provisioned credentials for the same estate) and
the raw evidence cache (may contain tenant-classified excerpts; rebuilt on
demand from source systems, which remain the systems of record). This is a
security boundary, not a cost optimization — see
[05-security.md](05-security.md).

### 6.3 Failover runbook (outline)

1. **Detect & declare (T+0–5 min):** automated cell health verdict (composite
   of Temporal frontend, Postgres primary, Kafka controller probes from two
   external vantage points); a human platform SRE declares failover — never
   automatic, to prevent split-brain on partitions.
2. **Fence (T+5–10):** global gateway stops routing the failed cell's
   tenants; the failed cell's egress credentials are revoked via Vault so a
   zombie cell cannot execute writes against the estate.
3. **Promote (T+10–20):** standby Temporal cluster promoted to active for
   the replicated namespaces; Postgres replica promoted; standby connector
   gateway activates its own credentials.
4. **Resume (T+20–30):** in-flight workflows resume from last replicated
   history event; any workflow that was in EXECUTING re-enters POLICY_CHECK
   before continuing ([11-failure-handling.md](11-failure-handling.md) §6);
   signals buffered at the global gateway drain into the standby's Kafka.
5. **Verify & communicate:** synthetic incident end-to-end probe passes;
   tenant status page updated; audit ledger records the failover as a
   first-class event.

### 6.4 RPO/RTO per data class

| Data class | RPO | RTO | Loss impact if RPO breached |
|---|---|---|---|
| Workflow state (Temporal) | ≤ 5 min | ≤ 30 min | Recent FSM transitions replay from Kafka signals; duplicate-safe (idempotent transitions) |
| Approval decisions | ≤ 5 min | ≤ 30 min | Un-replicated approvals are void — affected workflows re-enter AWAITING_APPROVAL |
| Audit ledger | ≤ 5 min (≤ 15 min S3) | ≤ 30 min | Hash chain shows an explicit, bounded gap; gap itself is an audited event |
| Evidence cache | ∞ (not replicated) | rebuilt on demand | None — sources of record are authoritative |
| Sanitized org memory | ≤ 24 h | ≤ 30 min | Marginal diagnosis-quality loss for one day of learnings |
| Credentials | never replicated | pre-provisioned | None — standby credentials exist ahead of time |

## 7. Degraded modes catalog

Full failure semantics live in
[11-failure-handling.md](11-failure-handling.md); this table is the
capacity/availability view.

| Degraded mode | Trigger | Platform behavior | Exit |
|---|---|---|---|
| Model-provider outage | Provider error rate / breaker at model gateway | Calls queue durably by severity; tenants and platform SRE notified; if outage exceeds queue tolerance, cell enters **advisory-only mode**: no new EXECUTING transitions, diagnosis continues from cache/deterministic retrieval, humans act on evidence bundles | Provider healthy for sustained probe window; queue drains oldest-SEV-first |
| Connector outage | Per-connector circuit breaker open | Evidence collected from remaining connectors; bundle carries explicit availability gaps; coverage drop lowers Confidence, which tightens the approval path automatically | Half-open probes succeed; gap window closed and recorded on the incident |
| Temporal outage (in-cell) | History/matching unavailable beyond HA failover | Sense plane keeps ingesting: signals buffer in Kafka (retention ≥ 24 h); no workflow progress; on recovery, normalizer replays and dedup collapses the backlog | Temporal healthy; backlog drained; storm detector prevents replay stampede |
| Full-cell loss | Cell health verdict + human declaration | §6.3 failover runbook; tenants served by standby within RTO | Failed cell rebuilt, tenants migrated back during a maintenance window |
| Sustained overload (no failure) | Cell above capacity despite autoscaling | Shedding order of §4.3; batch first, interactive last, writes never | Load subsides or cell split executed (§3.1) |

Each mode is drilled: model-provider and connector outages monthly via fault
injection in staging cells; full-cell failover quarterly against the live
standby with synthetic tenants. Drill results feed the eval service and the
risk register ([14-risks-and-roadmap.md](14-risks-and-roadmap.md)).
