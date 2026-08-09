# 05 — Security & Governance Model

Security spans two planes: the Execution plane's boundary controls
(specified with the connector gateway in
[04-connectivity.md](04-connectivity.md)) and the Governance plane's record
of everything ([01-architecture.md](01-architecture.md) §1). This document
defines the platform-wide model: who may do what (identity, policy), what the
data is (classification, redaction), what the models may see and say
(injection defense, model governance), and how we prove it afterward (audit,
compliance).

The two invariants everything else leans on, restated from
[01-architecture.md](01-architecture.md): **only the Execution plane holds
credentials to external systems**, and **only the Control plane can invoke
writes**. Both are network-enforced (§10), not convention.

---

## 1. Threat model summary

| Attacker class | Vector | Primary controls | Residual risk |
|---|---|---|---|
| Malicious insider | Operator abuses platform to run damaging writes or read data beyond their remit | RBAC/ABAC (§2), approval tiers with separation of duties (§9), full audit ledger (§8), JIT elevation with expiry | Colluding approver pairs; mitigated by quorum at T3 and ledger analytics |
| Compromised connector credentials | Stolen upstream token used outside the platform | Short-lived credentials and workload identity ([04-connectivity.md](04-connectivity.md) §3), minimal downstream scopes (§4.2 there), per-tenant Vault namespaces, anomaly detection on upstream audit logs | Window between issuance and expiry (≤ 1 h typical) |
| Prompt injection via retrieved content | Attacker plants instructions in a commit message, log line, ticket, or wiki page the platform will retrieve | Quarantine labeling, structural prompt separation, Security agent screening, per-agent tool allow-lists, deterministic write gates (§6) | Novel injection phrasings that pass screening — but capability is still capped by allow-lists and gates |
| Model supply chain | Compromised or degraded hosted model; poisoned local model artifact | Model allow-lists and pinned versions (§7), signed local-model artifacts, output schema validation, canary strings (§6), eval-service regression detection ([10-evaluation.md](10-evaluation.md)) | Subtle quality degradation below eval thresholds |
| Tenant cross-contamination | Tenant A's evidence, memory, or credentials reach tenant B | Cell isolation ([08-scalability.md](08-scalability.md)), per-tenant Vault namespaces, tenant in every cache key and OPA input, sanitized-only cross-cell replication ([01-architecture.md](01-architecture.md) §7) | Bugs in the sanitizing replication pipeline; mitigated by classification-aware contract tests |
| Exfiltration via citations | Citations or excerpts leak RESTRICTED content to under-privileged viewers | Classification stamped on every Evidence record at retrieval (§5), viewer-side ACL check on citation dereference, redaction before model or human display | Over-broad excerpt boundaries; bounded by excerpt size caps |

The threat model is reviewed quarterly and after every SEV1 platform
incident; changes land as policy bundle updates (§3.4), not documentation
edits alone.

---

## 2. Identity and access

### 2.1 Human identity: RBAC layered with ABAC

RBAC answers *what kind of actor are you*; ABAC answers *in what context is
this action acceptable*. Both must allow.

| Role | Grants |
|---|---|
| `viewer` | Read workflows, evidence, audit summaries for services they can see |
| `operator` | `viewer` + trigger read-only investigations, re-run diagnosis, annotate incidents |
| `approver` | `operator` + approve gated actions up to their tier eligibility (§9) |
| `platform-admin` | Manage cell infrastructure, connectors, policy bundles; **cannot approve remediations** (separation of duties) |
| `tenant-admin` | Manage their tenant's users, connector enablement, scope maps; no cross-tenant visibility |

ABAC attributes evaluated by OPA on every decision:

| Attribute | Source | Example effect |
|---|---|---|
| `environment` | target resource metadata | prod requires higher approval tier than staging |
| `service_ownership` | ownership graph ([06-retrieval-and-memory.md](06-retrieval-and-memory.md)) | T2 approvals must include an owner of the affected service |
| `data_classification` | Evidence record label (§5) | RESTRICTED evidence never rendered to `viewer` |
| `risk_class` | ToolSpec / Step Catalog | drives approval tier selection (§9) |
| `time_of_day` / freeze windows | change calendar | non-incident writes denied during freezes (§3.3) |

- **Why layered RBAC+ABAC over pure RBAC:** pure RBAC at 50,000 engineers
  and ~15,000 services either explodes into thousands of micro-roles or
  collapses into over-broad ones. Five roles plus contextual attributes keeps
  the role set auditable while the context does the fine-grained work.
- **Alternatives considered:** pure ABAC (no roles) — maximally flexible,
  but every access review becomes a policy-simulation exercise; auditors and
  tenant admins reason far better over named roles. Per-service ACLs —
  rejected; unmaintainable at 15,000 services.
- **Trade-offs:** two evaluation layers mean two places to misconfigure;
  mitigated by policy CI (§3.4) with golden allow/deny cases per role.
- **Operational implications:** default grant for a new engineer is
  `viewer` on owned services only — least privilege is the default, not an
  aspiration. Broader access is requested, approved by `tenant-admin`, and
  logged.

### 2.2 JIT elevation

Standing `approver`/`platform-admin` rights are minimized. Elevation is
just-in-time: requested with a reason, approved by a second party, expires in
≤ 4 hours, fully audited. Expired elevation drops mid-session — long-running
approvals re-check at gate time, not grant time.

### 2.3 Service identity: SPIFFE/SVID

Every platform workload holds a SPIFFE identity
(`spiffe://aetherops/<cell>/<plane>/<service>`) delivered as an X.509 SVID,
rotated hourly. mTLS between planes uses SVIDs; OPA authorizes plane-to-plane
calls by SPIFFE ID, which is how "only Control invokes writes" is enforced —
the connector gateway rejects any non-`READ` ToolCall whose client SVID is
not the workflow engine's. No shared secrets, no bearer tokens between
internal services.

---

## 3. Policy engine

OPA sits at every decision point; nothing security-relevant is decided by
imperative code alone.

| Decision point | Question OPA answers |
|---|---|
| Tool authorization (gateway) | May this principal call this tool with these args now? |
| Plan admission (Control) | Is every step in this compiled DAG within policy for this workflow? |
| Approval tier selection (Control) | Which tier does this step require in this context? |
| Model allow-lists (Intelligence) | May this content classification go to this model tier? (§7) |

### 3.1 Example: CRITICAL writes in prod require tier ≥ 2 and calibrated confidence

```rego
package aetherops.execution

import rego.v1

deny contains msg if {
    input.step.risk_class == "CRITICAL"
    input.context.environment == "prod"
    input.workflow.approval.tier < 2
    msg := sprintf("CRITICAL step %s in prod requires approval tier >= 2, got %d",
        [input.step.name, input.workflow.approval.tier])
}

deny contains msg if {
    input.step.risk_class == "CRITICAL"
    input.context.environment == "prod"
    input.workflow.diagnosis.confidence < 0.8
    msg := sprintf("CRITICAL step %s in prod requires confidence >= 0.8, got %.2f",
        [input.step.name, input.workflow.diagnosis.confidence])
}
```

(Tier selection normally routes CRITICAL-in-prod to T3, §9; this rule is the
floor that holds even if tier-selection policy regresses.)

### 3.2 Example: deny tool calls outside the agent's declared scope

```rego
package aetherops.tools

import rego.v1

deny contains msg if {
    not input.tool.name in data.agent_scopes[input.agent.name]
    msg := sprintf("agent %s is not scoped for tool %s",
        [input.agent.name, input.tool.name])
}

# Belt and braces: no agent identity may ever originate a write.
deny contains msg if {
    input.tool.risk_class != "READ"
    input.caller.plane != "control"
    msg := "non-READ tools are callable only by the Control plane workflow engine"
}
```

`data.agent_scopes` is generated from the agent contracts in
[02-agents.md](02-agents.md) at bundle build time — the roster is the source
of truth, and injected text cannot expand it (§6).

### 3.3 Example: freeze windows

```rego
package aetherops.change

import rego.v1

deny contains msg if {
    input.step.risk_class != "READ"
    some window in data.freeze_windows
    window.environment == input.context.environment
    time.parse_rfc3339_ns(window.start) <= time.now_ns()
    time.now_ns() < time.parse_rfc3339_ns(window.end)
    not incident_mitigation
    msg := sprintf("write blocked by freeze window %s", [window.id])
}

incident_mitigation if {
    input.workflow.kind == "incident_remediation"
    input.workflow.severity in {"SEV1", "SEV2"}
}
```

Incident mitigation pierces freezes deliberately — a freeze that blocks a
rollback during a SEV1 causes the outage it exists to prevent — but the
pierce itself is a distinct audit event and raises the approval tier by one.

### 3.4 Policy-as-code lifecycle

| Stage | Mechanism |
|---|---|
| Authoring | Rego in a dedicated repo; every rule owns golden allow/deny test cases |
| CI | `opa test` + regression suite replaying historical decisions against the new bundle; any decision flip must be acknowledged in review |
| Versioning | Signed OPA bundles, semver; the active bundle version is recorded in every audit event (§8) |
| Rollout | Staged: shadow (log-only diff) → one cell → fleet; automatic rollback on decision-flip anomaly rates |
| Break-glass | `platform-admin` + one `approver` may pin the previous bundle cell-wide; ledger-logged, pages security on-call, **mandatory postmortem within 72 h** |

**Why OPA everywhere:** one language, one test harness, one audit format for
every decision class; decisions are data (input document in, decision out),
which makes the replay-based CI possible. **Alternatives considered:**
policy logic in application code (unauditable sprawl, no shadow rollout);
Cedar (attractive model, weaker ecosystem fit with our K8s admission and
sidecar patterns at adoption time). **Trade-off:** Rego's learning curve is
real; the golden-test convention and a small reviewed rule library keep
authors out of the language's sharp corners. **Operational implication:**
policy evaluation is in the hot path of every tool call — bundles are pushed
to sidecars, evaluation is local (~sub-ms), and the gateway fails closed if
its sidecar is unhealthy.

---

## 4. Secrets

| Control | Implementation |
|---|---|
| Storage | HashiCorp Vault per cell; per-tenant namespaces ([04-connectivity.md](04-connectivity.md) §3.1) |
| Credential shape | Dynamic, short-lived wherever an engine exists; workload identity for clouds; static-with-rotation only as tracked exceptions |
| Prompts and logs | No secret material ever enters a model prompt or a log line: prompt assembly reads only from workflow state and Evidence (which never contain credentials by construction — the Execution plane injects credentials after policy, below the contract layer); log pipeline runs a secret-pattern scanner as a tripwire, and any hit is a SEV2 |
| Envelope encryption | All at-rest stores (Postgres, Redis persistence, object storage) encrypt with data keys wrapped by per-cell KMS keys |
| Key isolation | Per-cell KMS keys, no cross-cell key reuse — a cell's data is cryptographically dead outside that cell, which also implements residency (§5.4) |

---

## 5. Data protection

### 5.1 Classification

Four levels: `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`. Every
Evidence record is stamped **at retrieval time** by the gateway's
classification stage ([04-connectivity.md](04-connectivity.md) §7) using
source-system defaults (per-connector, per-tenant mapping tables) plus
content-based upgrade rules (detected PII or secrets upgrade the label; a
label is never downgraded automatically). The stamp is immutable and travels
with the record — into memory tiers, citations, approval cards, and model
prompts, where it drives the model allow-list (§7).

### 5.2 PII redaction pipeline

Applied to every retrieved excerpt **before any content reaches a hosted
model**, inside the Execution plane:

1. **Deterministic patterns** — validated matchers for emails, phone
   numbers, credit cards (Luhn), government IDs, IP addresses, bearer-token
   shapes. Deterministic rules run first because they are cheap, auditable,
   and never hallucinate.
2. **Small local NER model** — a distilled transformer running on cell-local
   CPU/GPU catches contextual PII (names, addresses in prose) that patterns
   miss. Replacements are typed placeholders (`⟨PERSON_1⟩`, `⟨EMAIL_2⟩`) with
   a per-workflow reversible mapping held only in cell-local encrypted
   working memory, so agents can still correlate entities across evidence.

**Never a hosted LLM for redaction — why:** (a) circularity — the redaction
step exists so sensitive content does not leave the cell; sending content to
a hosted model *to* redact it defeats the control it implements; (b)
determinism and auditability — compliance requires stating exactly what the
redactor does; a local pinned model with a versioned eval set is attestable,
a remote model is not; (c) availability and cost — redaction sits on the hot
path of every retrieval at ~2M alerts/day scale; it cannot depend on an
external service's latency, quota, or outage. **Trade-off:** a small local
model has a lower recall ceiling than a frontier model; we compensate with
the deterministic layer, conservative thresholds (prefer over-redaction), and
a human-reviewed sampling loop feeding the model's eval set
([10-evaluation.md](10-evaluation.md)).

### 5.3 Residency and retention

Data residency is implemented by **cell pinning**: a tenant's signals,
evidence, memory, and audit data live in its pinned cell
([01-architecture.md](01-architecture.md) §7); cross-cell replication carries
only sanitized, classified org-level memory. Retention by classification
(full schedules in [12-apis-and-storage.md](12-apis-and-storage.md)):

| Classification | Evidence/working data TTL | Notes |
|---|---|---|
| PUBLIC | 24 months | |
| INTERNAL | 12 months | |
| CONFIDENTIAL | 90 days | excerpts re-retrievable from source via citation |
| RESTRICTED | 30 days, excerpt purged, citation retained | audit keeps hash + reference, never content (§8.3) |

---

## 6. Prompt-injection defense in depth

Premise: retrieved external content is **quarantined data, never
instructions**. Any commit message, log line, or wiki page may contain text
crafted to steer a model. No single layer is trusted to catch it; the layers
below must *each* fail for an injection to cause a harmful write.

| Layer | Mechanism |
|---|---|
| 1. Structural separation | Prompt assembly wraps all external content in delimited, labeled untrusted blocks; system prompts state that quarantined blocks are data to analyze, never directives. Assembly is code in the model gateway ([07-model-gateway.md](07-model-gateway.md)) — agents cannot opt out |
| 2. Instruction-detection screening | The Security agent ([02-agents.md](02-agents.md)) screens quarantined content for imperative-toward-the-platform patterns (tool-invocation phrasing, role-reassignment, exfiltration prompts) using the local classifier tier; hits mark the Evidence `suspect`, which raises approval tier and flags the content on approval cards |
| 3. Tool allow-lists per agent | §3.2 — injected text cannot expand an agent's capability; the Root Cause agent has no write tools to be tricked into calling, in any plane |
| 4. No content-to-write chain | A write happens only when the workflow engine executes a Step Catalog action that passed OPA and its approval gate. Model output influences *which plan is proposed*, never whether gates apply — there is no code path from model text to an unguarded external call |
| 5. Output validation | Every agent output is validated against its JSON Schema; a "plan" naming a step outside the Step Catalog, or args violating a step's schema, is rejected before policy is even consulted |
| 6. Canary strings | System prompts embed per-workflow canary tokens; gateway and output filters scan all model output and outbound tool args for them — a canary surfacing means prompt leakage, quarantines the workflow, and pages security on-call |

Residual risk is honestly stated in §1: novel phrasings may pass layer 2.
The design goal is that layers 3–5 make the *worst case* of a successful
injection "a misleading diagnosis a human reviews with flagged-suspect
evidence" — never an unauthorized action.

---

## 7. Model governance

Model routing mechanics live in [07-model-gateway.md](07-model-gateway.md);
this section is the policy above it.

| Control | Implementation |
|---|---|
| Allow-lists | OPA maps (tenant, data classification) → permitted model tiers. Defaults: PUBLIC/INTERNAL → any tier (fast `claude-haiku-4-5-20251001`, standard `claude-sonnet-5`, reasoning `claude-opus-5`, frontier `claude-fable-5`); CONFIDENTIAL → tiers under the tenant's enterprise data agreement; RESTRICTED → local models only unless the tenant explicitly opts a hosted tier in |
| Version pinning | Model IDs are pinned per tier per cell; upgrades ride the eval service's regression gate ([10-evaluation.md](10-evaluation.md)) before fleet rollout |
| Fine-tuning | **No fine-tuning on tenant data without explicit written agreement.** Organizational learning uses retrieval and structured memory ([06-retrieval-and-memory.md](06-retrieval-and-memory.md)), not weight updates — learning stays inspectable and deletable |
| Output filtering | Post-generation filters: secret-pattern scan, canary scan (§6), classification-consistent citation check (§1, exfiltration row) before any output reaches a human surface or tool argument |
| Hosted-model retention posture | Hosted inference under enterprise terms: no training on our traffic, bounded retention (30-day abuse-monitoring window or shorter), documented per provider per tenant in the trust register; private connectivity where available (§10) |

---

## 8. Audit and compliance

### 8.1 The ledger

Append-only, hash-chained: each event carries `hash(prev_event_hash ||
canonical_event_bytes)`; chain heads are anchored hourly to WORM object
storage (S3 Object Lock, compliance mode), making silent tampering or
truncation detectable against an immutable anchor. Recorded per event:

| Field group | Contents |
|---|---|
| Identity | event id, cell, tenant, workflow id, step id, chain position, prev-hash |
| Actor | SPIFFE ID or human identity; `on_behalf_of` approver where applicable |
| Action | event type (tool call, model call, policy decision, approval, state transition, config/policy change), tool or step name, risk class |
| Decision context | OPA bundle version, decision + rule ids, approval tier, confidence, model id and prompt/response *hashes* (not bodies) |
| Data references | Evidence ids, citation URIs, classification labels — references and hashes, never raw excerpt content (§8.3) |
| Integrity | event hash, timestamp (cell time authority), signature |

**Why hash-chained ledger + WORM anchoring over a blockchain or plain audit
table:** a plain table is silently mutable by an admin; a distributed
blockchain adds consensus cost with no additional trust benefit when a
single-operator WORM anchor already provides external immutability.
**Trade-off:** hash-chaining makes per-event deletion impossible — which is
exactly the property auditors want and the reason §8.3's GDPR design stores
references, not PII.

### 8.2 SOC 2 Type II mapping

| Criteria | Platform mechanism |
|---|---|
| CC1 (control environment) | Separation of duties in roles (§2.1); security ownership and review cadence (§1) |
| CC2 (communication) | Policy bundles, ToolSpecs, and Step Catalog are versioned, reviewable artifacts; approval cards communicate control context to actors (§9) |
| CC3 (risk assessment) | Quarterly threat-model review (§1); risk classes on every action |
| CC4 (monitoring) | Governance plane taps everything: OTel spans + ledger anomaly analytics ([09-observability.md](09-observability.md)) |
| CC5 (control activities) | OPA at every decision point (§3); fail-closed defaults |
| CC6 (logical access) | RBAC/ABAC, JIT elevation, SPIFFE mTLS (§2); Vault credential isolation (§4) |
| CC7 (system operations) | Incident workflows for the platform itself; degradation ladders ([11-failure-handling.md](11-failure-handling.md)) |
| CC8 (change management) | Policy-as-code lifecycle (§3.4); staged rollouts; freeze windows (§3.3); break-glass with mandatory postmortem |
| CC9 (risk mitigation) | Compensation handlers on every write step; blast-radius bounds; approval tiers (§9) |

### 8.3 GDPR

- **Data minimization by architecture:** federated retrieval means
  petabyte telemetry stays in source systems; AetherOps stores excerpts and
  pointers only ([01-architecture.md](01-architecture.md) §6), redacted at
  ingestion (§5.2) and TTL-bounded (§5.3).
- **DSAR handling:** subject identifiers are searchable across evidence,
  memory, and redaction mappings within the tenant's cell; export produces
  the records plus their citations to systems of record (where the
  authoritative copy lives).
- **Right to erasure vs. immutable audit — resolution:** the ledger stores
  **references and hashes, never raw PII** (§8.1). Erasure deletes the
  content-bearing stores (evidence excerpts, memory entries, redaction
  mappings); ledger entries remain intact and intact-ness-provable, but
  post-erasure they reference content that no longer exists and hashes that
  can no longer be reversed to anything. Auditability and erasure stop
  competing because they never share a store.
- **Residency:** EU tenants pin to EU cells; per-cell KMS keys make
  cross-region reads cryptographically impossible, not merely disallowed.

### 8.4 HIPAA extensibility

For tenants handling PHI: dedicated **BAA-scoped cells** (BAAs with cloud and
model providers attached to the cell, not the fleet); a `PHI` handling flag
that pins classification at RESTRICTED with HIPAA retention overrides;
stricter model allow-lists (local models by default, hosted tiers only under
the provider BAA); access logging granularity raised to per-record reads.
No platform redesign — cells were built as the compliance boundary.

---

## 9. Approval workflows

| Tier | Approvers | Typical trigger (policy-selected, §3) |
|---|---|---|
| T0 | none (auto) | READ everywhere; LOW in non-prod |
| T1 | one eligible `approver` | LOW in prod; MEDIUM in non-prod |
| T2 | service owner **and** on-call SRE | MEDIUM/HIGH in prod |
| T3 | quorum (≥ 3, incl. service owner + SRE lead) or standing CAB | CRITICAL anywhere; any freeze-window pierce (§3.3) |

Timeout escalation: T1 15 min → next in on-call chain → engineering manager;
T2 10 min per approver → secondary on-call → incident commander if a SEV is
open; T3 pages the CAB chain immediately. A gate that exhausts its chain
never auto-approves — the workflow transitions to `ESCALATED`
([01-architecture.md](01-architecture.md) §3) and a human owns the decision.

Every approval card (Slack/Teams, via the approval service) must show:
**evidence citations** (mandatory — an uncited claim cannot appear on a
card), **confidence** with its calibration basis, **blast radius** (services,
regions, traffic share affected), and the **rollback plan** (the compensation
chain that will run on failure or regret). Suspect-flagged evidence (§6
layer 2) is visibly marked. Approving from a card records the approver for
on-behalf-of attribution downstream
([04-connectivity.md](04-connectivity.md) §4.3).

**Why tiered approvals over uniform human-in-the-loop:** uniform gating at
~2M alerts/day would either drown approvers (everything gated) or rubber-stamp
(nothing meaningfully reviewed). Tiers spend human attention where risk
concentrates. **Trade-off:** tier-selection policy becomes safety-critical —
which is why §3.1's floor rule exists independently of tier selection, and
why tier-selection changes ride the full policy lifecycle (§3.4).

---

## 10. Network isolation

| Boundary | Control |
|---|---|
| Cell perimeter | Per-cell VPC; no cross-cell network path except the sanitized replication endpoint ([01-architecture.md](01-architecture.md) §7) |
| Plane boundaries | K8s NetworkPolicies + service mesh authorization by SPIFFE ID; the "only Control invokes writes" and "only Execution egresses" invariants are mesh-enforced deny rules, testable in CI against the mesh config |
| Egress | **All** external egress flows through the connector gateway; connector sandboxes carry per-connector egress allowlists ([04-connectivity.md](04-connectivity.md) §7); everything else in the cell — including the entire agent runtime — has **no route to the internet** |
| Model traffic | Model gateway → hosted providers via private links (PrivateLink / Private Service Connect) where the provider offers them; TLS-pinned public egress from a dedicated, allowlisted gateway otherwise — never from agent workloads |
| Admin access | No SSH to production nodes; break-glass access is time-boxed, session-recorded, and ledger-logged |

An agent that is compromised — by injection (§6) or by a bug — is a process
with no credentials (§4), no internet route, a tool allow-list (§3.2), and a
gateway that will not accept a write from it (§2.3). The network layer is
the last of the concentric rings, and it holds even if every layer above it
fails.

## 11. OWASP LLM Top 10 (2025) mapping

The defenses above, restated in the industry's shared vocabulary. "Code"
means implemented and exercised by the reference implementation's test
suite; "design" means specified in these documents for the production
build. The honest gaps are labeled — that honesty is part of the control.

| OWASP ID | Risk | AetherOps control | Status |
|---|---|---|---|
| LLM01 | Prompt Injection | Retrieved content is quarantined data, never instructions: Security-agent screening, digest withholding with stable evidence numbering, and the attack itself in the test suite (`tests/test_security_agents.py`) | **Code** |
| LLM02 | Sensitive Information Disclosure | Deterministic secret/PII redaction at the connector gateway before content reaches workflow state or any model (`security/redaction.py`); classification-gated prompts — evidence above the model clearance (Slack discussion is CONFIDENTIAL by default) is withheld from every model prompt while humans retain audit visibility (`core/context.py`) | **Code** |
| LLM03 | Supply Chain | Reference implementation is pure stdlib (no third-party packages to poison); production model/version pinning per tenant (§7) | Code (narrow) + design |
| LLM04 | Data & Model Poisoning | Golden datasets frozen with leakage hygiene ([10-evaluation.md](10-evaluation.md)); no fine-tuning on tenant data (§7) | Design |
| LLM05 | Improper Output Handling | JSON-Schema validation of every agent output with semantic-retry-then-escalate (`core/schema.py`); citation-reference validation; plans compiled only from the vetted Step Catalog | **Code** |
| LLM06 | Excessive Agency | The load-bearing control: agents propose, they never execute — typed Step Catalog with risk classes, policy tiers, human approval gates, independent Reviewer verification, saga compensation; plus a connector-gateway write-guard: write-risk tools are invocable only by the executor principal, so even a fully compromised agent cannot call one (`policy/engine.py`, `orchestration/dag.py`, `agents/reviewer.py`, `connectors/base.py`) | **Code** |
| LLM07 | System Prompt Leakage | Prompts are versioned registry artifacts containing no secrets by construction (`prompts/registry.py`); canary strings in production prompts (§6) | Code + design |
| LLM08 | Vector & Embedding Weaknesses | Retrieval carries per-chunk source attribution (rag://doc#offset); retrieved guidance is advisory-only (excluded from diagnosis confidence); retrieval quality measured against labeled data in CI (`rag/`, `evals/retrieval.py`) | **Code** |
| LLM09 | Misinformation | Citations mandatory (no-citation-no-claim enforced in the agent wrapper); "insufficient evidence" escalation; calibration error measured per agent ([10-evaluation.md](10-evaluation.md)) | **Code** |
| LLM10 | Unbounded Consumption | Per-tool rate limiting (`connectors/base.py`), token/cost metering per call; production budgets and storm-mode shedding ([08-scalability.md](08-scalability.md)) | Code (partial) + design |
