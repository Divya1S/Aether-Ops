# 01 — System Architecture

Canonical architecture document. All other docs and the reference
implementation inherit the terminology defined here.

---

## 1. Architectural planes

AetherOps is organized into five planes with strict dependency direction
(lower planes never call upward):

| Plane | Responsibility | Key components |
|---|---|---|
| **Sense** | Ingest signals from the enterprise estate | Webhook receivers, event bus, alert normalizer, dedup/storm suppression |
| **Intelligence** | Turn signals + evidence into decisions | Agent runtime, model gateway, retrieval/evidence service, memory services |
| **Control** | Own execution semantics and governance decisions | Workflow engine (Temporal), Step Catalog, policy engine (OPA), approval service, scheduler |
| **Execution** | Touch external systems, and nothing else does | MCP connector gateway, tool sandboxes, credential vault |
| **Governance** | Record, measure, and constrain everything above | Audit ledger, observability pipeline, evaluation service, cost metering |

**Why planes, not microservice soup:** the security review and the compliance
story hinge on two invariants — *only the Execution plane holds credentials to
external systems* and *only the Control plane can invoke writes*. Planes make
those invariants checkable at the network layer (see [05-security.md](05-security.md)),
not just in code review.

## 2. Overall architecture diagram

```
                        ENTERPRISE ESTATE (systems of record — never bulk-copied)
   GitHub GitLab Bitbucket │ Jira Linear │ Slack Teams │ Confluence Notion │ Datadog Grafana
   Prometheus Splunk Elastic │ PagerDuty │ AWS Azure GCP │ K8s ArgoCD Terraform │ CI systems
        ▲                                                                       ▲
        │ (reads: evidence)                                (writes: typed actions)
════════╪═══════════════════════════════════════════════════════════════════════╪════════
        │                     EXECUTION PLANE                                   │
        │   ┌────────────────────────────────────────────────────────────┐      │
        └───┤  MCP CONNECTOR GATEWAY                                     ├──────┘
            │  per-connector adapters · authN/Z · rate limits · caching  │
            │  read replica cache · tool sandbox · credential vault      │
            └────────────────────────────▲───────────────────────────────┘
                                         │ typed ToolCall / ToolResult
════════════════════════════════════════╪═══════════════════════════════════════════════
   CONTROL PLANE                         │
   ┌─────────────────┐   ┌───────────────┴────────────┐   ┌──────────────────────┐
   │ POLICY ENGINE   │◄──┤  WORKFLOW ENGINE (Temporal)│──►│ APPROVAL SERVICE     │
   │ OPA / Rego      │   │  DAG runs · retries        │   │ tiers · routing      │
   │ risk classes    │   │  checkpoints · sagas       │   │ Slack/Teams surfaces │
   └─────────────────┘   │  gates · timers            │   └──────────────────────┘
                         └───▲───────────────────▲────┘
        ┌────────────────────┘                   │ plan (typed DAG)
════════╪════════════════════════════════════════╪══════════════════════════════════════
        │  INTELLIGENCE PLANE                    │
   ┌────┴─────────┐  ┌──────────────┐  ┌─────────┴────────┐  ┌────────────────────┐
   │ AGENT RUNTIME│  │ MODEL GATEWAY│  │ PLAN COMPILER    │  │ MEMORY SERVICES    │
   │ 13 agents    │──│ tier routing │  │ LLM plan → Step  │  │ working · episodic │
   │ (see 02)     │  │ fallbacks    │  │ Catalog DAG      │  │ long-term · org    │
   └────▲─────────┘  └──────────────┘  └──────────────────┘  │ vector + graph     │
        │        ┌──────────────────────────┐                └────────────────────┘
        │        │ RETRIEVAL / EVIDENCE SVC │  federated queries, citations,
        └────────┤ (see 06)                 │  hybrid search, reranking
                 └──────────▲───────────────┘
════════════════════════════╪═══════════════════════════════════════════════════════════
   SENSE PLANE              │
   ┌──────────────┐  ┌──────┴────────┐  ┌───────────────────────────────┐
   │ WEBHOOK RECV │─►│ EVENT BUS     │─►│ ALERT NORMALIZER              │─► workflow
   │ + API GW     │  │ (Kafka)       │  │ dedup · storm suppression     │   triggers
   └──────────────┘  └───────────────┘  │ severity mapping · routing    │
                                        └───────────────────────────────┘
════════════════════════════════════════════════════════════════════════════════════════
   GOVERNANCE PLANE (taps everything)
   ┌──────────────┐ ┌──────────────────┐ ┌───────────────┐ ┌───────────────────────┐
   │ AUDIT LEDGER │ │ OBSERVABILITY    │ │ EVAL SERVICE  │ │ COST METERING         │
   │ hash-chained │ │ OTel traces      │ │ replay, judge │ │ tokens · infra · $ per│
   │ append-only  │ │ metrics · logs   │ │ golden sets   │ │ team/workflow         │
   └──────────────┘ └──────────────────┘ └───────────────┘ └───────────────────────┘
```

## 3. The core loop as a state machine

Every incident workflow is an instance of this finite state machine (FSM),
owned by the workflow engine — never by an LLM:

```
 DETECTED ─► TRIAGED ─► INVESTIGATING ─► DIAGNOSED ─► PLANNED ─► POLICY_CHECK
                                            │                        │
                                            │ (low confidence /      ├─ auto-approved ──► EXECUTING
                                            │  insufficient evidence)├─ needs approval ─► AWAITING_APPROVAL ─► EXECUTING
                                            ▼                        └─ denied ─────────► ESCALATED
                                        ESCALATED                                            
 EXECUTING ─► VERIFYING ─► RESOLVED ─► LEARNED                                              
     │            │                                                                          
     └─(failure)──┴──► ROLLING_BACK ─► ESCALATED                                             
```

Terminal states: `RESOLVED→LEARNED`, `ESCALATED` (human owns it, platform
continues assisting). Every transition is durable, audited, and idempotent.

## 4. Agent interaction diagram

Agents never call each other directly. All interaction is mediated by the
workflow engine (data flows through typed workflow state), which is what makes
execution replayable and auditable. Full agent specs: [02-agents.md](02-agents.md).

```
                          ┌─────────────────────────────┐
        alert/webhook ───►│   WORKFLOW ENGINE (owner)   │◄─── human approvals
                          └──┬───────────────────────┬──┘
             invokes (typed inputs, typed outputs, budgets)
   ┌───────────┬──────────┬──┴────────┬───────────┬──┴───────┬───────────┐
   ▼           ▼          ▼           ▼           ▼          ▼           ▼
 Triage     Knowledge  Root Cause  Code Intel  Planner   Policy      Security
 Agent      Agent      Agent       Agent       Agent     Agent       Agent
   │           │          │           │           │          │           │
   └───────────┴──────────┴───────────┴───────────┴──────────┴───────────┘
                    all read evidence via Retrieval Svc; all write
                    results (with citations + confidence) to workflow state
                                      │
                    ┌─────────────────┼──────────────────┐
                    ▼                 ▼                  ▼
              CI/CD Agent      Infrastructure      Reviewer Agent
              (rollback,       Agent (K8s/cloud    (reviews generated
              pipeline ops)    actions)            PRs & plans)
                    │                 │                  
                    └────────┬────────┘                  
                             ▼                            
                   Human Approval Agent ──► Evaluation/Verifier Agent
                   (routing, summaries,     (post-action verification,
                   escalation, timeouts)     regression watch)
```

## 5. Sequence diagram — canonical SEV2 (bad deploy)

The scenario implemented end-to-end in the reference slice
(`src/aetherops/workflows/incident_remediation.py`):

```
PagerDuty   Sense     Workflow    Agents/Intelligence      Policy/Approval   Connector GW
   │          │        Engine              │                     │                │
   │ alert    │           │                │                     │                │
   ├─────────►│ normalize │                │                     │                │
   │          ├──────────►│ start FSM      │                     │                │
   │          │           ├─ triage ──────►│ Triage: SEV2,       │                │
   │          │           │                │ service=checkout    │                │
   │          │           ├─ evidence ────►│ Retrieval: deploys, │                │
   │          │           │                │ metrics, K8s events,├────reads──────►│──► Datadog,
   │          │           │                │ commits, past inc.  │                │    GitHub, K8s
   │          │           ├─ diagnose ────►│ RootCause+CodeIntel:│                │
   │          │           │                │ "deploy 14:02, pool │                │
   │          │           │                │ 20→200 → OOMKill"   │                │
   │          │           │                │ conf 0.87, 6 cites  │                │
   │          │           ├─ plan ────────►│ Planner: rollback + │                │
   │          │           │                │ revert PR (typed)   │                │
   │          │           ├─ policy check ─┼────────────────────►│ OPA: HIGH risk │
   │          │           │                │                     │ in prod ⇒      │
   │          │           │◄─ gate ────────┼─────────────────────┤ approval tier 2│
   │          │  Slack: "approve rollback? evidence: […]" ──► on-call approves    │
   │          │           ├─ execute ──────┼─────────────────────┼───────writes──►│──► K8s rollback,
   │          │           │                │                     │   (sandboxed)  │    GitHub revert PR
   │          │           ├─ verify ──────►│ Verifier: p99 back  │                │
   │          │           │                │ to baseline 8 min   │                │
   │          │           ├─ learn ───────►│ Memory: episode +   │                │
   │          │           │                │ failure-class update│                │
   │          │        RESOLVED→LEARNED    │                     │                │
```

## 6. Data flow summary

1. **Signals in:** webhooks/polls → API gateway → Kafka (`signals.raw`) →
   normalizer → `incidents.detected`. Petabyte telemetry **stays in source
   systems**; AetherOps stores pointers + retrieved excerpts, never bulk
   copies (see [06-retrieval-and-memory.md](06-retrieval-and-memory.md)).
2. **Evidence:** Retrieval Service executes federated, scoped queries through
   the connector gateway; results become `Evidence` records (content excerpt +
   citation + classification label) in the workflow's working memory.
3. **Decisions:** agents consume evidence, produce typed outputs with
   confidence + citations into workflow state.
4. **Actions out:** the workflow engine invokes Step Catalog actions; the
   connector gateway is the *only* path to external writes.
5. **Everything → Governance:** every model call, tool call, decision, and
   approval emits audit records and OTel spans; the eval service replays
   workflows against golden datasets offline.

## 7. Deployment architecture

Cell-based, multi-region. A **cell** is a self-contained instance of all five
planes serving a subset of tenants/business units. Cells cap blast radius
(a poison workflow or noisy tenant cannot take down the platform) and map
cleanly to data-residency boundaries.

```
                    GLOBAL LAYER (thin, stateless where possible)
   ┌──────────────────────────────────────────────────────────────────────┐
   │  Global API gateway / DNS · tenant→cell routing · fleet console      │
   │  eval service (offline) · model gateway control plane · golden sets  │
   └──────────────────────────────────────────────────────────────────────┘
        │                          │                            │
   ┌────▼─────────┐          ┌─────▼────────┐             ┌─────▼────────┐
   │ CELL us-e-1  │          │ CELL us-w-2  │             │ CELL eu-c-1  │
   │ (BU: retail) │          │ (BU: infra)  │             │ (EU tenants, │
   │              │          │              │             │  GDPR pinned)│
   │ K8s cluster  │          │ K8s cluster  │             │ K8s cluster  │
   │ Kafka        │          │ Kafka        │             │ Kafka        │
   │ Temporal     │          │ Temporal     │             │ Temporal     │
   │ Postgres(HA) │          │ Postgres(HA) │             │ Postgres(HA) │
   │ Redis, Qdrant│          │ Redis, Qdrant│             │ Redis, Qdrant│
   │ OPA, Vault   │          │ OPA, Vault   │             │ OPA, Vault   │
   │ conn. gateway│          │ conn. gateway│             │ conn. gateway│
   └──────────────┘          └──────────────┘             └──────────────┘
   Cross-cell: async replication of org-level memory (sanitized, classified)
   only — never credentials, never raw evidence. DR: paired-cell warm standby,
   RPO ≤ 5 min (workflow state), RTO ≤ 30 min. Details: 08-scalability.md.
```

**Why cells over one big multi-tenant deployment:** alternatives considered
were (a) global shared deployment — simplest, but one bad connector or tenant
storm degrades everyone and data-residency becomes per-row instead of
per-cell; (b) per-tenant deployment — cleanest isolation, but 100+ tenants ×
full stack is operationally unaffordable. Cells are the standard middle
ground (Slack, AWS internal). Trade-off: org-wide learning requires an
explicit, sanitizing replication pipeline; we accept that because it forces
the data-classification boundary we need for compliance anyway.

## 8. Core platform services

| Service | Plane | Technology (production) | Reference impl |
|---|---|---|---|
| API gateway | Sense | Envoy + OIDC | — (documented in 12) |
| Event bus | Sense | Kafka | in-process queue |
| Workflow engine | Control | Temporal | `orchestration/dag.py` (deterministic executor) |
| Step Catalog | Control | versioned registry (Postgres) | typed steps in `workflows/` |
| Policy engine | Control | OPA sidecars + Rego bundles | `policy/engine.py` |
| Approval service | Control | service + Slack/Teams apps | gate nodes in DAG executor |
| Agent runtime | Intelligence | Python 3.12 workers on K8s | `agents/` |
| Model gateway | Intelligence | gateway service; Claude tiers | `gateway/model_gateway.py` |
| Retrieval/evidence | Intelligence | federated query svc + Qdrant + reranker | fake connectors return cited evidence |
| Memory services | Intelligence | Postgres + pgvector→Qdrant, Neo4j | `memory/store.py` |
| Connector gateway | Execution | MCP servers + gateway (mTLS) | `connectors/` |
| Credential vault | Execution | HashiCorp Vault / cloud KMS | — (documented in 05) |
| Audit ledger | Governance | append-only, hash-chained, WORM S3 | `security/audit.py` |
| Observability | Governance | OTel → Datadog/Grafana | structured audit + run records |
| Eval service | Governance | replay harness + judge fleet | — (documented in 10) |

## 9. Canonical terminology

| Term | Definition |
|---|---|
| **Signal** | Normalized inbound event (alert, webhook, schedule) that may trigger a workflow |
| **Evidence** | A retrieved artifact excerpt with citation + classification, immutable once recorded |
| **Citation** | Pointer to a system-of-record artifact: `{source, ref/URI, excerpt, retrieved_at}` |
| **Step Catalog** | Versioned registry of typed, policy-annotated, compensable actions — the only verbs the platform can execute |
| **Plan** | LLM-proposed remediation expressed *only* as Step Catalog references; compiled to a DAG |
| **Gate** | Durable pause in a workflow awaiting human approval or timer |
| **Compensation** | Registered undo handler for a step (saga pattern) |
| **Confidence** | Calibrated score attached to every agent output: model self-estimate × evidence coverage × historical calibration |
| **Cell** | Self-contained deployment unit of all five planes |
| **Failure class** | Learned taxonomy label for incidents (e.g., `deploy-regression/memory`) powering recall and change gating |
