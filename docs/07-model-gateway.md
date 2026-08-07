# 07 — Model Architecture and Routing

**Plane:** Intelligence (Model Gateway)
**Depends on:** [05-security.md](05-security.md) (classification, egress rules), [06-retrieval-and-memory.md](06-retrieval-and-memory.md) (evidence bundles as model inputs)
**Consumed by:** every agent in [02-agents.md](02-agents.md); budgets enforced by the workflow engine ([03-orchestration.md](03-orchestration.md)); spend feeds [13-cost-model.md](13-cost-model.md)

---

## 1. The gateway as the single chokepoint

Every model call in AetherOps — thirteen agents, thousands of concurrent
workflows, ~2M alerts/day upstream — passes through the model gateway. **No
agent holds a provider API key or talks to a provider endpoint directly.**
The gateway is where five concerns are enforced once instead of thirteen times:

| Concern | Enforcement at the gateway |
|---|---|
| Allow-lists | Per-agent, per-tenant allow-list of (tier, model version) pairs; a call outside the list is rejected, not downgraded silently |
| Budgets | Token/call/cost budgets per invocation, per workflow, per tenant; the workflow engine funds each call, the gateway debits and hard-stops overruns |
| Caching | Prompt-prefix caching and response caching for idempotent calls (§6) |
| Observability | Every call emits OTel spans, token counts, latency, validation outcomes to the Governance plane ([09-observability.md](09-observability.md)) |
| Provider failover | Fallback chains and circuit breakers (§4) live here, invisible to agents |

**Why chosen:** the compliance and cost stories both require a single point of
truth. "Which model versions can see tenant X's data" and "what did incident
Y cost in tokens" are answerable only if there is exactly one door.
**Alternatives considered:** per-agent SDK clients with a shared library
(policy drifts per agent; a library cannot revoke a key at runtime); a
service mesh egress policy alone (blocks rogue traffic but cannot validate
schemas, meter tokens, or run fallbacks).
**Trade-offs:** the gateway is on the critical path of every intelligent
operation — it must be the most available service in the Intelligence plane
(stateless replicas per cell, no shared state beyond Redis counters).
**Operational implications:** gateway deploys are canaried against replay
traffic ([10-evaluation.md](10-evaluation.md)) before fleet rollout; a
gateway outage degrades the platform to deterministic-only operation (§7),
which is a designed posture, not a crash.

---

## 2. Model tiers

| Tier | Model | Used for | Typical callers |
|---|---|---|---|
| **fast** | `claude-haiku-4-5-20251001` | Triage classification, summarization, field extraction, approval-card rendering | Triage Agent, Human Approval Agent, notification surfaces |
| **standard** | `claude-sonnet-5` | Evidence synthesis, code analysis, PR drafting, PR/plan review | Knowledge, Code Intel, CI/CD, Reviewer Agents |
| **reasoning** | `claude-opus-5` | Root-cause hypothesis generation and ranking, remediation plan proposal | Root Cause Agent, Planner Agent |
| **frontier** | `claude-fable-5` | SEV1-class incidents, re-analysis after low-confidence escalation, eval judging | Any agent when severity/escalation dictates; Eval Service judges ([10-evaluation.md](10-evaluation.md)) |
| **embeddings** | Dedicated embedding model (voyage-class) | Dense vectors for hybrid retrieval and memory similarity ([06-retrieval-and-memory.md](06-retrieval-and-memory.md) §3, §8) | Retrieval/Evidence Service, memory services |
| **reranker** | Cross-encoder reranker | Top-K evidence reranking (accuracy workhorse of the retrieval pipeline) | Retrieval/Evidence Service |
| **local** | Small local models, CPU/GPU in-cell | PII/NER redaction, data classification — sensitive-path work that must not leave the cell ([05-security.md](05-security.md)) | Retrieval pipeline stage 4; connector gateway ingress |

Tier boundaries follow a simple rule: **pay for reasoning only where the
output is a decision input; pay frontier only where being wrong is
expensive.** Triage runs ~2M times/day and must be cheap and fast; root-cause
hypothesis ranking runs ~2,000 times/month per SEV load and must be right.

The **local tier is not an economy measure** — it is a security boundary.
Redaction and classification see unredacted excerpts; by definition that
content cannot transit to a hosted endpoint, whatever the contract terms.
Local models are small (NER/classifier scale), pinned, and evaluated with the
same lifecycle as hosted models (§8).

---

## 3. Routing logic

Routing is **deterministic configuration evaluated in the gateway**, in two
stages: static rules first, dynamic adjustments second.

**Stage 1 — task class → default tier** (from versioned routing config):

| Task class | Default tier | Rationale |
|---|---|---|
| `triage.classify` | fast | High volume, bounded label set, latency-sensitive |
| `summarize.approval_card` | fast | Rendering over reasoning; human re-checks anyway |
| `evidence.synthesize` | standard | Needs cross-document reading, not deep search |
| `code.analyze` / `pr.draft` / `pr.review` | standard | Code competence at volume economics |
| `rootcause.hypothesize` / `rootcause.rank` | reasoning | The decision the whole platform exists to get right |
| `plan.propose` | reasoning | Output becomes a typed DAG; errors are expensive downstream |
| `incident.sev1.*` | frontier | Severity overrides task class |
| `escalation.reanalyze` | frontier | Second opinion must be stronger than the first |
| `eval.judge` | frontier | Judge must outrank the judged ([10-evaluation.md](10-evaluation.md)) |
| `redact.pii` / `classify.data` | local | Must not leave the cell |

**Stage 2 — dynamic signals adjust within allowed bounds:**

| Signal | Effect |
|---|---|
| Input token size | Over per-tier context threshold ⇒ evidence bundle is re-ranked/compacted first; escalating tiers to fit context is forbidden |
| Incident severity | SEV1 forces frontier for diagnosis/planning task classes; SEV3 caps at standard unless confidence gates fail |
| Data classification | Highest label in the payload must be within the (tier, tenant) allow-list; otherwise route to local path or refuse — never downgrade the label |
| Latency SLO | Interactive lane with tight SLO may pick the faster same-tier region; never silently drops a tier |
| Remaining workflow token budget | Budget below reserve ⇒ gateway returns `budget_exhausted`; the workflow gates to a human ([03-orchestration.md](03-orchestration.md)) rather than finishing on a weaker model |
| Historical quality per failure class | Calibration data ([10-evaluation.md](10-evaluation.md)) can pin a failure class to a higher tier (e.g., `deploy-regression/memory` diagnoses measurably better on reasoning than standard) |

**Pseudocode (the entire routing surface):**

```
def route(call: ModelCall) -> Decision:
    tier = routing_config[call.task_class]                  # stage 1
    tier = max(tier, severity_floor(call.incident_severity))# SEV1 → frontier
    tier = max(tier, quality_pin(call.failure_class))       # calibration pins

    if not allowlist.permits(call.tenant, tier, call.data_classification):
        return Refuse(reason="classification_exceeds_tier_allowlist")
    if call.workflow_budget.remaining < reserve(tier):
        return BudgetExhausted()                            # → human gate

    endpoint = healthy_endpoint(tier, call.latency_slo)     # region choice
    return Invoke(endpoint, model=pinned_version(call.tenant, tier),
                  timeout=timeout_budget(tier), schema=call.output_schema)
```

**Why deterministic config, not an LLM choosing models:** routing is a
*governance* decision — it determines cost, latency, and which model version
sees which classification of data. Governance decisions must be reproducible
(same inputs, same route, for audit and replay), testable in CI (the routing
table is data with unit tests), and tamper-evident (config changes are
reviewed diffs, not prompt drift). An LLM router would add a model call to
every model call, could be prompt-injected via evidence content into choosing
a weaker or unauthorized model, and turns every cost anomaly into a debugging
séance. This is the same principle as the deterministic control plane in
[03-orchestration.md](03-orchestration.md): models produce content, never
control flow.
**Alternatives considered:** learned bandit routing (attractive economics,
unauditable route provenance; revisit only for the fast tier where all arms
are within allow-list); agent-selected models (rejected — agents would
self-escalate to frontier under uncertainty, which is exactly when budgets
matter).
**Trade-offs:** static tables lag reality; the quality-pin mechanism is the
pressure valve, driven by offline evaluation rather than online improvisation.
**Operational implications:** routing config is versioned per cell with the
same review gates as Rego policy; route decisions are logged with the config
version that produced them.

---

## 4. Fallback chains, timeouts, circuit breakers

**Fallback chain, in order, per call:**

1. **Primary:** pinned model version, in-region provider endpoint.
2. **Provider outage → same-tier alternate region** (or alternate provider
   endpoint for the same pinned model where offered). No quality change; adds
   cross-region latency; permitted only where the tenant's data-residency
   policy allows ([05-security.md](05-security.md)) — EU-pinned cells fail
   over only within EU regions.
3. **Same-tier exhausted → one tier down, with consequences.** The gateway
   attaches a `tier_degraded` flag; the agent runtime applies a fixed
   **confidence penalty** (multiplier on the calibrated confidence score,
   sized per tier-pair from calibration data) and the workflow engine
   **forces a human gate** on any action-bearing output produced under
   degradation — auto-approval is disabled regardless of score.
4. **No tier available → deterministic-only degradation.** The workflow parks
   at its last durable checkpoint or escalates per the ladder in
   [11-failure-handling.md](11-failure-handling.md).

Tier-up substitution (haiku call served by sonnet) is allowed during fast-tier
outages — quality is a superset — but budget-metered at the actual model's
cost, so outages are visible in spend, never hidden.

**Timeout budgets per tier** (interactive lane; batch lane in §6 is 10×):

| Tier | Per-call timeout | Retries (transport) | Notes |
|---|---|---|---|
| fast | 10 s | 2 | Triage latency budget dominates |
| standard | 60 s | 2 | Bounded by approval-card freshness |
| reasoning | 240 s | 1 | Long-context diagnosis; retry is expensive |
| frontier | 480 s | 1 | SEV1 tolerance for a better answer |
| embeddings / reranker | 5 s / 10 s | 2 | Inside retrieval pipeline SLO |
| local | 5 s | 2 | In-cell; failure here blocks the sensitive path and fails closed |

**Circuit breakers** are per model endpoint (provider × region × model
version): rolling error/timeout rate over a short window trips the breaker
open, calls flow to the next chain link immediately (no per-call timeout
burn), half-open probes use synthetic canary prompts rather than live
incident traffic. Breaker state changes are Governance-plane events — a
tripped frontier breaker during a SEV1 is something the on-call sees.

**Why chosen:** the platform's worst hour (regional provider outage during an
alert storm) is precisely when incidents need it most; the chain converts an
outage into a bounded quality/latency degradation with mandatory human
oversight instead of an outage of AetherOps itself.
**Trade-offs:** tier-down with forced gating trades autonomy for safety —
throughput of auto-remediation drops during provider incidents by design.
**Operational implications:** fallback drills run quarterly against staging
cells; `tier_degraded` rate is a top-line platform health metric.

---

## 5. Structured output enforcement

Every agent call carries a **JSON Schema** for its output (the typed
contracts of [02-agents.md](02-agents.md), including claim–evidence linking
fields from [06-retrieval-and-memory.md](06-retrieval-and-memory.md) §5).
Enforcement is at the gateway:

1. Gateway invokes the model with schema-constrained decoding where the
   provider supports it, plus the schema embedded in the system prompt.
2. Response is validated against the schema — structural validity, enum
   membership, and referential checks (every cited Evidence ID exists in the
   supplied bundle).
3. On failure: **retry with the validation errors appended** (the model sees
   exactly which fields failed and why). **Maximum 2 retries.**
4. Still failing → **semantic-failure path**: the call returns a typed
   `SemanticFailure` to the workflow engine, which treats it like
   `insufficient_evidence` — degrade or escalate per
   [11-failure-handling.md](11-failure-handling.md), never "best-effort
   parse". The transcript is captured for the eval service as a candidate
   golden-set failure case.

**Why schema-at-gateway rather than per-agent parsing:** (a) one
implementation of validation, retry, and failure semantics instead of
thirteen divergent ones; (b) the retry loop is metered and budgeted centrally
— a retry storm is visible as a gateway metric, not hidden in agent code;
(c) referential checks need the evidence bundle, which the gateway already
sees as the cached prompt prefix (§6); (d) validation outcomes become
uniform observability data — schema-failure rate per agent per model version
is the earliest signal that a model change regressed.
**Alternatives considered:** per-agent parsing with a shared library (drifts,
unmeterable); accepting free text and extracting structure with a second
model call (doubles cost and stacks two failure modes).
**Trade-offs:** the gateway must know every agent's schema — schemas are
registered artifacts, versioned with the agents, fetched by reference; a
schema change is a deployment event, which is exactly the change control we
want on agent contracts.
**Operational implications:** schema-retry rate >2% for any (agent, model)
pair pages the platform team; retries consume the calling workflow's budget,
so a misbehaving schema surfaces in cost dashboards the same day.

---

## 6. Prompt caching, lanes, and metering

**Prompt caching.** Call prompts are assembled as
`[stable system prompt | agent contract + schema | evidence bundle | task]`,
in that order, so cacheable prefixes are maximal:

- **Stable system prompts and agent contracts** change only on deployment —
  near-100% cache hit within a model version.
- **Evidence bundles** are immutable once assembled
  ([06-retrieval-and-memory.md](06-retrieval-and-memory.md) §3) and reused
  across the multiple agent calls of a single investigation (root cause →
  planner → reviewer all read the same bundle), so the bundle is cached once
  per workflow and each subsequent call pays only its task-specific suffix.
  For reasoning/frontier-tier incident work this is the single largest cost
  lever in [13-cost-model.md](13-cost-model.md).

**Lanes.** Two priority lanes at the gateway:

| Lane | Work | Properties |
|---|---|---|
| **Interactive** | Live incident workflows, change-intelligence scoring, approval surfaces | Priority scheduling, tier timeouts of §4, preemptive capacity reservation per cell |
| **Batch** | Eval replay ([10-evaluation.md](10-evaluation.md)), memory distillation ([06-retrieval-and-memory.md](06-retrieval-and-memory.md) §7), embedding backfills, shadow evaluation (§8) | Provider batch APIs where available, 10× timeouts, off-peak scheduling, strictly yields to interactive under contention |

Batch never starves interactive: the gateway sheds batch load first under any
capacity or provider degradation, automatically.

**Per-tenant token metering.** Every call is attributed
`(tenant, cell, workflow, agent, task_class, model_version, lane)` with input/
output/cached token counts and computed cost. Meters aggregate to the cost
model in [13-cost-model.md](13-cost-model.md) ($ per incident, per team, per
tenant) and enforce tenant-level monthly ceilings — a runaway tenant degrades
to deterministic-only operation at its ceiling rather than consuming the
cell's shared capacity.

---

## 7. When not to use a model at all

The cheapest, most reliable model call is the one never made. The following
surfaces are **deterministic by design** — implemented as code, config, or
Rego, with unit tests, and explicitly out of scope for any model tier:

| Surface | Deterministic mechanism | Owner |
|---|---|---|
| Policy decisions (can this action run?) | OPA/Rego evaluation | Control plane ([05-security.md](05-security.md)) |
| Model routing (§3) | Versioned routing config | Model gateway |
| Budget enforcement | Counters + hard limits | Gateway + workflow engine |
| Alert dedup / storm suppression | Fingerprint hashing, windowed rate rules | Sense plane |
| Severity mapping from monitor metadata | Static mapping tables per monitor source | Sense plane normalizer |
| FSM transitions of the incident workflow | Temporal workflow code — never an LLM ([03-orchestration.md](03-orchestration.md)) | Control plane |
| Approval-tier selection | Risk class → tier lookup from the Step Catalog | Control plane |
| Cache TTL / staleness decisions | Volatility-class tables ([06-retrieval-and-memory.md](06-retrieval-and-memory.md) §9) | Retrieval service |

The dividing line, stated once: **models generate content under uncertainty;
anything that is a lookup, a threshold, or a state transition is code.** A
model asked to do a lookup is slower, costlier, and occasionally wrong — a
strictly dominated choice.

---

## 8. Local vs. hosted, and model lifecycle

**Trade-off analysis.**

| Dimension | Hosted frontier-lab models (fast/standard/reasoning/frontier tiers) | Local in-cell models (local tier) |
|---|---|---|
| Quality on open-ended reasoning | State of the art; not reproducible locally at any feasible cell footprint | Adequate only for narrow tasks (NER, classification) |
| Data boundary | Redacted, classified content only, under provider no-training/no-retention terms | Unredacted content permitted — never leaves the cell |
| Cost shape | Per-token, elastic, zero idle cost | Fixed in-cell compute, cheap per call, capacity-planned |
| Latency | Network round-trip; regional endpoints mitigate | Sub-second, no egress |
| Operational burden | Version churn managed via lifecycle below | Model serving is our pager |

Verdict: hosted for everything whose input can be redacted to policy, local
for the redaction/classification path itself and nothing more. We do not run
open-weight mid-size models "to save money": at our call mix the fast tier is
already cheaper than the engineering cost of operating GPU serving fleets per
cell, and the quality gap at reasoning/frontier tiers is decision-relevant.
Revisited annually with benchmark data, not vibes.

**Model lifecycle.**

1. **Shadow evaluation before promotion.** A candidate model version (new
   Claude release, new embedding model, retrained local classifier) runs in
   the batch lane against golden datasets and replayed production workflows
   ([10-evaluation.md](10-evaluation.md)): task metrics, schema-failure rate,
   citation-faithfulness, confidence calibration, cost and latency deltas.
   Promotion requires meeting or beating the incumbent on the gate metrics —
   judged partly by frontier-tier LLM-as-judge, with human review of
   regressions.
2. **Pinned versions per tenant for change control.** Tenants pin (tier →
   model version) via the allow-list. Promotion rolls fleet-default forward;
   regulated tenants opt in on their own change calendar. Embedding-model
   pins additionally gate the re-embed pipeline
   ([06-retrieval-and-memory.md](06-retrieval-and-memory.md) §8).
3. **Rollback.** Because versions are pinned config, rollback is a config
   revert propagating in minutes — no deployment. Retired versions stay on
   the allow-list in `rollback-only` state for 30 days after fleet
   promotion. Rollback of an embedding model additionally re-activates the
   retained previous vector index (dual-index window), which is why old
   indexes are dropped only after the rollback window closes.

**Operational implications:** every gateway response carries the exact model
version used, so any incident diagnosis is attributable to a specific model
build; eval dashboards track per-version quality continuously, making "the
new model got worse at Kafka incidents" a detectable, revertible event rather
than an anecdote.

---

*Siblings: agent contracts and confidence composition in
[02-agents.md](02-agents.md); the deterministic control plane that consumes
model outputs in [03-orchestration.md](03-orchestration.md); token economics
in [13-cost-model.md](13-cost-model.md).*
