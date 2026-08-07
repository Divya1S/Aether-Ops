# 06 — Retrieval, Evidence, and Memory

**Plane:** Intelligence (Retrieval/Evidence Service, Memory Services)
**Depends on:** [04-connectivity.md](04-connectivity.md) (connector gateway), [05-security.md](05-security.md) (classification, redaction)
**Consumed by:** every agent in [02-agents.md](02-agents.md); confidence formula in [02-agents.md](02-agents.md); storage schemas in [12-apis-and-storage.md](12-apis-and-storage.md)

---

## 1. Evidence-first retrieval philosophy

AetherOps' second design commitment ([00-executive-summary.md](00-executive-summary.md))
is **evidence or silence**. The retrieval subsystem is where that commitment is
enforced mechanically rather than aspirationally.

The contract of the Retrieval/Evidence Service is deliberately narrow:

> **Return Evidence records or nothing.** No raw strings, no paraphrases, no
> "the model remembers that…". Every unit of information handed to an agent is
> an immutable `Evidence` record: excerpt + citation
> `{source, ref/URI, excerpt, retrieved_at}` + data-classification label.

Consequences of the contract:

1. **Recommendations are only as good as their citations.** A root-cause
   hypothesis with six citations to concrete commits, metrics queries, and K8s
   events is reviewable in minutes; the same hypothesis without them is a
   guess wearing a confidence score. The approval surfaces in
   [03-orchestration.md](03-orchestration.md) render citations directly into
   approval cards, so the human gate is an evidence review, not a trust vote.
2. **"Insufficient evidence" is a success state, not a failure state.** When
   evidence coverage is below threshold, the correct output is
   `insufficient_evidence` and a transition to `ESCALATED` in the incident FSM
   — never a best guess (mechanics in §5).
3. **Retrieval is auditable end to end.** Because every Evidence record points
   at a system-of-record artifact with a `retrieved_at` stamp, the audit
   ledger (Governance plane) can reconstruct exactly what the platform knew,
   from where, at what time, for every decision it made.

**Why chosen:** in an enterprise with 50,000 engineers, an uncited automation
platform is granted read-only advisory status forever — the failure mode of
every "AI SRE" chatbot in [00-executive-summary.md](00-executive-summary.md) §3.
**Alternatives considered:** free-text retrieval with post-hoc citation
extraction (citations become decorative and drift from claims); trusting model
parametric knowledge for stable facts (unauditable, silently stale).
**Trade-offs:** every claim costs a retrieval round-trip; agents cannot "just
know" things, which adds latency to trivial questions.
**Operational implications:** retrieval throughput is a first-class SLO; the
Evidence record write path must be as reliable as the workflow engine itself.

---

## 2. Federated retrieval vs. central indexing

The enterprise estate holds petabytes of telemetry. AetherOps never bulk-copies
it. The split:

| Mode | What | How |
|---|---|---|
| **Query-time federation** | Volatile, high-volume data: metrics, logs, K8s events, tickets, CI runs, alerts | Live, scoped, budgeted queries through the MCP connector gateway at investigation time; only retrieved excerpts persist (as Evidence) |
| **Selective indexing** | High-value, low-volume corpora: runbooks, postmortems, service docs, distilled incident summaries | Ingested, chunked, embedded, and indexed in-cell (pgvector → Qdrant, §8) with lineage back to the source system |

**Why the split is chosen:**

1. **Cost.** Copying petabyte-scale telemetry into a platform index would cost
   more than the platform saves. A Datadog metrics query at incident time
   costs cents; a mirrored metrics lake costs millions per year.
2. **Staleness.** Metrics, logs, and K8s events are only useful *fresh*. A
   central index is stale the moment it is built; federation reads the system
   of record at the moment of investigation.
3. **Security perimeter.** Telemetry stays inside the security boundary of the
   system that owns it. AetherOps holds pointers and excerpts, so a compromise
   of the platform exposes citations and fragments, not the estate's data lake.
4. **GDPR / data minimization.** Storing only the excerpts actually used in a
   decision is a defensible minimization posture; mirroring everything is not.
   EU-pinned cells ([01-architecture.md](01-architecture.md) §7) keep even
   those excerpts in-region.

**Alternatives considered:** (a) full central data lake — maximal query power,
ruled out on all four grounds above; (b) pure federation, zero indexing — no
copies, but semantic search over runbooks/postmortems is impossible through
keyword-only connector APIs, and those corpora are where recall wins
incidents; (c) long-TTL caching proxies per connector — a central index in
disguise, with the staleness of one and the query power of neither.
**Trade-offs:** federated queries inherit source-system latency and rate
limits; investigations degrade when a source is down (degradation ladder in
[11-failure-handling.md](11-failure-handling.md)); indexed corpora need a
refresh pipeline and drift monitoring.
**Operational implications:** per-connector rate budgets and circuit breakers
live in the connector gateway ([04-connectivity.md](04-connectivity.md));
index freshness is an SLO (§8); the retrieval planner must know, per source,
whether it is federated or indexed and plan accordingly (§3).

---

## 3. Retrieval pipeline

The pipeline runs inside the Retrieval/Evidence Service. The Knowledge Agent
([02-agents.md](02-agents.md)) initiates it; the workflow engine funds it with
an explicit token/call/time budget.

```
  investigation question (typed, from workflow state)
        │
        ▼
 ┌──────────────────┐   Knowledge Agent decomposes the question into
 │ 1. QUERY PLANNING │   per-connector queries: "deploys to checkout
 └────────┬─────────┘   14:00–14:30", "p99 for checkout-api", "K8s
          │             events ns=checkout", "similar past incidents"
          ▼
 ┌──────────────────┐   parallel fan-out through the MCP connector
 │ 2. FEDERATED      │   gateway; each sub-query scoped (service, time
 │    EXECUTION      │   window, namespace) and budgeted (rows, bytes,
 └────────┬─────────┘   wall-clock); partial failure ⇒ partial results
          │             + coverage penalty, never silent omission
          ▼
 ┌──────────────────┐   raw ToolResults → Evidence records:
 │ 3. NORMALIZATION  │   excerpt + citation {source, ref/URI, excerpt,
 └────────┬─────────┘   retrieved_at}; excerpting is deterministic
          │             (window around match), not model-summarized
          ▼
 ┌──────────────────┐   small local models (in-cell, [05-security.md])
 │ 4. CLASSIFICATION │   stamp data-classification label + redact
 │    + REDACTION    │   PII/secrets BEFORE any hosted model sees the
 └────────┬─────────┘   excerpt; the stamp is immutable thereafter
          ▼
 ┌──────────────────┐   hybrid ranking: BM25 (exact identifiers:
 │ 5. HYBRID RANKING │   service names, error codes, SHAs) fused with
 └────────┬─────────┘   dense embeddings (semantic similarity), RRF
          ▼
 ┌──────────────────┐   cross-encoder reranker scores top-K
 │ 6. RERANKING      │   query/evidence pairs; expensive, so applied
 └────────┬─────────┘   to K≈50, keeps ≈15
          ▼
 ┌──────────────────┐   evidence bundle: deduplicated, diversity-
 │ 7. BUNDLE         │   constrained (≥N distinct sources), with a
 │    ASSEMBLY +     │   coverage score per facet of the question
 │    COVERAGE SCORE │   (change? metrics? infra? history?)
 └────────┬─────────┘
          ▼
  EvidenceBundle → workflow state (working memory) → agents
```

Stage notes:

- **Query planning is the only model-driven stage** (Knowledge Agent, standard
  tier via the model gateway, [07-model-gateway.md](07-model-gateway.md)); its
  output is a *typed query plan* validated against the connector registry —
  the model proposes queries, it does not execute anything.
- **Excerpting in stage 3 is deterministic** so that the excerpt in a citation
  is byte-reproducible from the source artifact. Model-written summaries are a
  separate, downstream artifact and always cite the Evidence IDs they compress.
- **Classification/redaction (stage 4) precedes ranking** because embeddings
  are computed on redacted text; the unredacted original never enters any
  index or hosted-model context ([05-security.md](05-security.md)).
- **Hybrid ranking:** BM25 alone misses paraphrase ("OOMKilled" vs "memory
  pressure eviction"); dense-only misses exact identifiers (a commit SHA, an
  error code). Reciprocal rank fusion of both, then cross-encoder reranking,
  is the standard recipe; the reranker is the accuracy workhorse and the BM25
  leg is the guarantee that exact-match evidence can never be embedded away.
- **Coverage scoring (stage 7)** grades the bundle per facet of the
  investigation question. It is the `evidence coverage` term in the canonical
  confidence formula (model self-estimate × evidence coverage × historical
  calibration) and the trigger for `insufficient_evidence` (§5).

**Trade-offs:** seven stages add ~2–6 s to a cold investigation query; stages
5–6 are skippable for exact-ref lookups ("fetch commit `abc123`") which bypass
ranking entirely.
**Operational implications:** every stage emits OTel spans and per-stage
latency metrics ([09-observability.md](09-observability.md)); the reranker is
the first candidate for capacity scaling during alert storms.

---

## 4. Evidence sources catalog

Volatility classes govern caching (§9): **immutable** (content at a ref never
changes), **append-only** (grows, never rewrites), **volatile** (changes
continuously), **semi-static** (changes on human edit cadence).

| Source | Connector | Volatility | Strategy | Evidence-cache TTL |
|---|---|---|---|---|
| Git commits | GitHub/GitLab/Bitbucket MCP | Immutable (by SHA) | Federated; graph nodes in Neo4j (§6) | 30 d (immutable at ref) |
| Pull requests | GitHub/GitLab/Bitbucket MCP | Append-only (discussion), immutable once merged | Federated | 1 h open · 30 d merged |
| CI logs | CI connector (Jenkins/GHA/Argo) | Immutable per run | Federated | 7 d |
| Test failures | CI connector | Immutable per run | Federated; failure signatures indexed for recurrence search | 7 d |
| Metrics | Datadog/Grafana/Prometheus MCP | Volatile | Federated only — never indexed | 60 s |
| K8s events | K8s connector | Volatile (etcd retention ≈1 h) | Federated, fetched eagerly at triage before they expire | 15 min |
| Tickets | Jira/Linear MCP | Volatile (workflow churn) | Federated | 10 min |
| Runbooks | Confluence/Notion/Git MCP | Semi-static | **Indexed** (chunked + embedded) with webhook-driven refresh | 24 h, webhook-invalidated |
| Documentation / service docs | Confluence/Notion/Git MCP | Semi-static | **Indexed** | 24 h, webhook-invalidated |
| Past incidents | AetherOps episodic memory (§7) | Append-only | **Indexed** natively (it is our own store) | n/a (system of record) |
| Slack threads | Slack MCP | Append-only | Federated; incident-channel threads distilled into episodes at `LEARNED` | 10 min live · frozen at resolve |
| PagerDuty alerts | PagerDuty MCP | Volatile (ack/resolve state) | Federated; normalized copies already flow through the Sense plane | 60 s |

The catalog is configuration, not code: adding a source means registering a
connector in the gateway ([04-connectivity.md](04-connectivity.md)) and a row
here with volatility class and strategy.

---

## 5. Anti-hallucination mechanics

Four mechanisms, enforced at different layers so no single bypass defeats them:

1. **Claim–evidence linking (schema-enforced).** Every agent output schema
   ([02-agents.md](02-agents.md)) requires each claim — in practice, each
   sentence of a diagnosis, each edge of a causal chain, each parameter of a
   proposed plan — to reference one or more Evidence IDs present in the
   workflow's evidence bundle. The model gateway validates the schema
   ([07-model-gateway.md](07-model-gateway.md)); references to nonexistent
   Evidence IDs are a validation failure, retried with the error, then routed
   to the semantic-failure path.
2. **Evidence-coverage score in the confidence formula.** Confidence =
   model self-estimate × **evidence coverage** × historical calibration. A
   fluent hypothesis over a thin bundle is arithmetically prevented from
   reaching auto-approval thresholds; policy tiers in
   [03-orchestration.md](03-orchestration.md) consume this composite score.
3. **`insufficient_evidence` as a first-class outcome.** Every investigation
   output schema is a tagged union: `diagnosis | insufficient_evidence`. The
   latter carries what was searched, what was missing, and which connectors
   failed or were budget-exhausted; the workflow engine maps it to the
   `ESCALATED` FSM state with a structured hand-off to the on-call (partial
   bundle, coverage gaps, suggested next queries). Escalation is a designed
   path ([11-failure-handling.md](11-failure-handling.md)), not an exception handler.
4. **No-citation-no-claim validation at the output-validation layer.** A
   deterministic validator (not a model) walks every outbound artifact —
   diagnosis, plan rationale, approval card, postmortem draft — and rejects
   any claim-bearing element lacking Evidence references. Numeric assertions
   are additionally cross-checked against the cited excerpt where the format
   permits (e.g., a quoted p99 must appear in the cited metrics excerpt).

**Why layered enforcement:** schema validation catches structure, coverage
scoring catches thin grounding, the output validator catches leakage into
prose. Any one alone is gameable by a sufficiently fluent model.
**Alternatives considered:** LLM-as-judge citation checking online (kept
offline in [10-evaluation.md](10-evaluation.md) — a probabilistic guard is
not a guard); fine-tuning for citation faithfulness (helps the mean, does not
bound the tail).
**Trade-offs:** strictness produces false rejections of harmless summary
sentences; authors of agent prompts must budget tokens for citation overhead
(~10–15% of output).
**Operational implications:** validator rejection rate per agent per failure
class is a tracked quality metric; rising rejection rates page the platform
team before they page a customer.

---

## 6. Knowledge graph (Neo4j)

The knowledge graph answers *structural* questions that vector search cannot:
what depends on what, who owns what, what changed where, near when.

**Node types**

| Node | Key properties | Primary source |
|---|---|---|
| `Service` | name, tier, criticality, cell, SLOs | Service catalog (Backstage-class) |
| `Deploy` | version, time, environment, actor | CI/CD connectors |
| `Commit` | SHA, author, files touched, risk score | Git connectors |
| `Incident` | severity, failure class, window, state | AetherOps episodic memory |
| `Alert` | monitor ID, severity, service | Sense plane normalizer |
| `Team` | on-call rotation, escalation policy | Service catalog + PagerDuty |
| `Runbook` | URI, version, last-reviewed, owning team | Docs connectors |
| `FailureClass` | taxonomy label, priors, efficacy stats | Learning loop ([02-agents.md](02-agents.md)) |

**Edge types:** `Service -DEPENDS_ON-> Service` (versioned, weighted by call
volume), `Deploy -DEPLOYS-> Service`, `Commit -INCLUDED_IN-> Deploy`,
`Incident -IMPACTED-> Service`, `Incident -CAUSED_BY-> Deploy|Commit`
(written only at `LEARNED`, with citations), `Alert -FIRED_ON-> Service`,
`Team -OWNS-> Service|Runbook`, `Runbook -COVERS-> FailureClass`,
`Incident -CLASSIFIED_AS-> FailureClass`.

**How it is built:** three ingestion paths, all idempotent upserts keyed on
natural IDs — (a) **CI/CD ingestion**: deploy and commit events from the Sense
plane create `Deploy`/`Commit` nodes and edges within seconds of the event;
(b) **service-catalog sync**: nightly full reconciliation plus webhook deltas
for `Service`, `Team`, ownership, and dependency edges (dependency edges are
additionally confirmed against tracing data where available, since declared
catalogs drift); (c) **incident ingestion**: the learning step of every
workflow writes the `Incident` node, its impact and causal edges, and its
`FailureClass` membership — causal edges carry the Evidence IDs that justify
them, so the graph itself is cited.

**What it answers:**

- **Blast radius:** `MATCH (s:Service {name:$s})<-[:DEPENDS_ON*1..3]-(up)` —
  who is downstream of a degraded service, weighted, depth-bounded.
- **Ownership:** service → team → on-call rotation, one hop, no stale wiki.
- **Change-window queries:** *"what changed near time T in the dependency
  cone of service S"* — the single highest-value diagnostic query: traverse
  the dependency cone of S, collect `Deploy` nodes in `[T−90m, T+5m]`, join
  their commits. This is the Root Cause Agent's first move on every incident.
- **Recurrence:** incidents sharing a `FailureClass` across the estate, and
  which runbooks covered them with what remediation efficacy.

**Why a property graph over relational joins:** the load-bearing query shape
is *variable-depth transitive closure with predicates on edges* (dependency
cones, blast radius). In Postgres this is recursive CTEs whose cost explodes
with depth and whose plans are fragile; in Cypher it is one bounded traversal
over an index-free-adjacency store, and the queries stay readable enough for
on-call engineers to audit.
**Alternatives considered:** Postgres recursive CTEs (fine at 100 services,
not at ~15,000 with 10-deep chains); a graph layer over the vector store
(wrong tool — vectors answer "similar", graphs answer "connected"); RDF/
SPARQL triple stores (power we don't need, operational maturity we'd miss).
**Trade-offs:** one more stateful system per cell; dual-write discipline
between Postgres (system of record for incidents) and Neo4j (projection) —
the graph is treated as a *rebuildable projection*, never the source of truth.
**Operational implications:** graph rebuild-from-log is a tested runbook;
staleness of catalog sync is monitored; traversals carry depth and time
budgets so a pathological dependency cycle cannot stall an investigation.

---

## 7. Memory architecture — four tiers

Memory is what turns incident N into leverage for incident N+1. Four tiers,
as named in [01-architecture.md](01-architecture.md):

| Tier | Scope | Storage | Write path | Read path | TTL / eviction |
|---|---|---|---|---|---|
| **Working** | One workflow run | Temporal workflow state (durable) + Redis (hot scratch) | Agents append typed outputs + evidence bundle refs during the run | Injected into each agent invocation by the workflow engine | Minutes–hours; archived to the run record at terminal state, Redis keys expire with the run |
| **Episodic** | One cell's incident history | Postgres (structured episodes) + vectors (§8) | `LEARNED` step distills the run: timeline, causal chain (cited), remediation, efficacy, failure class | Similarity search ("incidents like this one") + graph joins via `Incident` nodes (§6) | 13 months, then summarize-and-archive to cold storage (retains the seasonal cycle: Black Friday to Black Friday) |
| **Long-term** | Distilled operational knowledge per cell | Postgres, versioned documents (runbook diffs, failure-class playbooks) | Learning loop proposes distillations; **human-reviewed PR-style merge** — memory writes are gated like code | Indexed corpus in the retrieval pipeline (§4: runbooks, docs) | Indefinite, with mandatory review cycles (quarterly re-validation; unreviewed entries decay in ranking weight) |
| **Organizational** | Cross-cell, sanitized | Global replicated store (async, classification-filtered — [01-architecture.md](01-architecture.md) §7) | Cell-level aggregation jobs emit failure-class statistics, remediation-efficacy priors, calibration weights; sanitizer strips tenant identifiers and any excerpt content | Read-only priors loaded by agents at invocation (efficacy priors, calibration terms) | Indefinite; statistics re-aggregated continuously, never raw-appended |

Design decisions worth calling out:

- **Working memory lives in Temporal state, not agent context.** Agents are
  stateless between invocations; the workflow engine owns state. This is what
  makes runs replayable ([03-orchestration.md](03-orchestration.md)) and what
  prevents context divergence between agents.
- **Episodic memory keeps 13 months** because change-risk and recurrence
  models need one full seasonal cycle plus margin; keeping raw episodes longer
  fails data-minimization review for no modeled accuracy gain.
- **Long-term memory writes are human-gated.** A hallucinated "lesson" merged
  into a runbook poisons every future retrieval. Distillations are drafts;
  merge requires the owning team's review, exactly like the fix-forward PRs in
  the Preventive Engineering pillar.
- **Organizational memory carries statistics, never excerpts.** Cross-cell
  replication moves failure-class priors and calibration weights only — never
  Evidence content, never credentials — which is what keeps EU-pinned cells
  compliant while still letting every cell benefit from fleet-wide learning.

**Alternatives considered:** one unified "memory store" (collapses four
different consistency/privacy/TTL regimes into the strictest one, making all
memory as expensive as the most sensitive); fine-tuning models on incident
history instead of retrieval (unauditable, un-deletable, incompatible with
per-tenant data boundaries).
**Trade-offs:** four tiers means four write paths to monitor; the human gate
on long-term memory throttles learning velocity deliberately.
**Operational implications:** memory-tier lag (episode distillation backlog,
org-replication delay) is dashboarded; a cell can run degraded with stale
organizational priors, but never with a broken working-memory path.

---

## 8. Vector storage: pgvector first, Qdrant at scale

**Default: Postgres + pgvector, per cell.**
**Why chosen:** operational simplicity (the cell already runs HA Postgres —
zero new stateful systems for cells below the threshold) and transactional
co-location — an episode row, its chunks, and its embeddings commit in one
transaction, so there is no index/source drift window and backup/restore is
one system.
**Escalation trigger:** migrate a cell to Qdrant when it exceeds **~50M
vectors** (where pgvector HNSW build times and recall/latency at our filter
selectivity degrade past SLO) **or** when it needs multi-tenant sharded search
(per-tenant collections with isolated quotas — Qdrant's sharding and payload-
filtered HNSW handle this natively; pgvector partial indexes do not scale to
hundreds of tenants).
**Alternatives considered:** Qdrant everywhere from day one (uniform, but a
second stateful system in every cell including the many that never approach
the threshold); a managed vector SaaS (embeddings of runbooks and incident
text leaving the cell fails the security perimeter established in §2).
**Trade-offs:** two storage backends behind one retrieval API means the API
must be strictly backend-agnostic; migration is a live dual-write + backfill +
cutover runbook, tested before any cell needs it.
**Operational implications:** vector count and ANN latency per cell are
capacity-planning metrics with alerts at 60% of threshold.

**Embedding refresh strategy:** embeddings are stamped with
`embedding_model_version`. Model upgrades trigger a rolling re-embed of
indexed corpora (long-term memory first, episodic archive last) behind a
dual-index read (query both versions, prefer new) until backfill completes;
per-document refresh otherwise happens only on source change via webhook
invalidation (§9). Cross-version cosine scores are never compared.

**Chunking policy (runbooks/postmortems):** structure-aware chunking on
heading boundaries, target 400–700 tokens, 15% overlap; every chunk carries
its heading path (`Runbook X > Diagnosis > OOM`) prepended for embedding and
its source URI + character range so a chunk hit reconstructs an exact
Citation. Tables and command blocks are kept intact (never split mid-table),
oversized ones become standalone chunks with the heading path as context.

---

## 9. Cache hierarchy and TTL policy

Three layers, all serving Evidence records or their precursors — never
uncited raw payloads:

| Layer | Location | Contents | Scope | Eviction |
|---|---|---|---|---|
| **L1** | In-process, per agent invocation | Evidence records already fetched in this invocation; parsed bundle | Single model call | Freed at invocation end |
| **L2** | Redis, per cell | Connector query results (keyed by normalized query + scope), embedding vectors for repeated texts, reranker scores | Cell-wide, all workflows | TTL by volatility class (below) + explicit invalidation |
| **L3** | Postgres warm evidence store | Every Evidence record ever attached to a workflow (immutable, cited) | Cell-wide, durable | Retention per [12-apis-and-storage.md](12-apis-and-storage.md); never served as *fresh* — only as historical record or re-fetch hint |

TTLs by volatility class (must match the catalog in §4):

| Volatility class | Examples | L2 TTL | Invalidation |
|---|---|---|---|
| Volatile | metrics, PagerDuty alert state, K8s events, tickets | 60 s (metrics/alerts) · 15 min (K8s events) · 10 min (tickets) | TTL only — webhooks too chatty to subscribe per-query |
| Append-only | open PRs, live Slack threads, CI streams | 10–60 min | Connector webhooks (new comment/commit/message) purge the key |
| Immutable | commits by SHA, merged PRs, completed CI runs | 7–30 d | None needed; keyed by immutable ref |
| Semi-static | runbooks, docs | 24 h | Connector webhooks on edit purge cache **and** trigger re-chunk/re-embed (§8) |

**Invalidation via connector webhooks:** connectors that emit change events
(Git pushes, doc edits, ticket transitions) publish them onto Kafka
(`connectors.changes`); a cache-invalidator consumer maps events to L2 key
patterns and to index refresh jobs. Webhook delivery is best-effort, which is
exactly why TTLs exist as the backstop — invalidation makes caches fresher,
TTLs bound how wrong they can be.

**Staleness stamping — the rule that makes caching safe:** every Evidence
record carries `retrieved_at` (part of the Citation schema; the cache's
honesty mechanism). The workflow engine compares `now − retrieved_at` against
the source's volatility-class TTL at each **action-bearing** step; Evidence
older than its TTL is stale. Stale evidence may still support *historical*
claims ("p99 was 900 ms at 14:07"), but an agent about to *act* — propose a
plan, verify a remediation — must re-fetch first. Concretely: the Verifier
Agent never confirms recovery from cached metrics, and a plan resuming from a
long approval gate re-validates its supporting evidence before `EXECUTING`.

**Why chosen:** during an alert storm, hundreds of concurrent workflows ask
the same questions of the same connectors; without L2, AetherOps becomes a
DDoS on its own evidence sources and burns connector rate budgets that
investigations need.
**Alternatives considered:** no caching (rate-limit exhaustion at ~2M
alerts/day is a certainty, not a risk); longer TTLs on volatile data (a
verification pass on 5-minute-old metrics is a wrong verification); caching
at the connector gateway only (necessary but not sufficient — L2 also caches
post-pipeline artifacts, embeddings and reranker scores, that the gateway
never sees).
**Trade-offs:** cache keys must incorporate the caller's authorization scope
so cached results never leak across permission boundaries
([05-security.md](05-security.md)) — this fragments hit rates, and we accept
that.
**Operational implications:** hit rates per layer per volatility class are
dashboarded ([09-observability.md](09-observability.md)); stale-evidence
re-fetch counts are a leading indicator of connector latency problems; L3
growth is bounded by the retention schedule in
[12-apis-and-storage.md](12-apis-and-storage.md).

---

*Siblings: federated query execution is specified in
[04-connectivity.md](04-connectivity.md); evidence consumers in
[02-agents.md](02-agents.md); retrieval failure modes in
[11-failure-handling.md](11-failure-handling.md).*
