# 13 — Cost Model and Unit Economics

Token budgets, infrastructure cost, and ROI for the fleet described in
[01-architecture.md](01-architecture.md). All prices and footprints in this
document are **planning assumptions**, stated inline; actuals are tracked by
the cost-metering service (Governance plane) from the `metering.tokens`
stream ([12-apis-and-storage.md](12-apis-and-storage.md) §4) and reviewed
weekly (§6).

---

## 1. Cost philosophy

Cost is an engineered property of the system, not a bill discovered at
month-end. Concretely:

- **Budgets are enforced at the model gateway per workflow, not observed
  after the fact.** Every workflow starts with a token/cost budget attached
  by the workflow engine; the gateway checks remaining budget before each
  model call and refuses overruns, triggering the degradation ladder
  ([11-failure-handling.md](11-failure-handling.md)) rather than an
  unbounded spend.
- The same applies to tool calls (per-workflow tool budgets,
  [04-connectivity.md](04-connectivity.md) §6) — model tokens and connector
  quota are the two meters every workflow carries.
- Offline work never competes with incident traffic for spend: it runs in
  batch lanes with its own budget pool (§6).

**Why gateway enforcement:** an agentic system's cost distribution is
heavy-tailed — the mean is harmless, the p99.9 (a looping investigation
during an alert storm) is what produces surprise invoices and, worse,
latency starvation for real incidents. Enforcement converts the tail into
an explicit, audited degradation. **Alternatives considered:** post-hoc
alerting on spend (reacts hours late, after the storm); hard per-call caps
only (blunt — kills legitimately deep investigations that a per-workflow
budget would allow). **Trade-off:** budget exhaustion mid-investigation is
now a designed state that needs UX (escalation with partial findings), not
an error page. **Operational implication:** budget denials are a tracked
metric; a rising denial rate is a capacity/pricing signal, not noise.

---

## 2. Per-workflow token model — typical SEV2 remediation

Working prices (assumption, per Mtok in/out): fast tier
`claude-haiku-4-5-20251001` $1/$5 · standard `claude-sonnet-5` $3/$15 ·
reasoning `claude-opus-5` $5/$25 · frontier `claude-fable-5` $5/$25
(frontier is eval/judge only, [10-evaluation.md](10-evaluation.md) §5).

Single pass through the canonical FSM ([01-architecture.md](01-architecture.md) §3):

| Step (agent) | Tier | Tokens in | Tokens out | Cost |
|---|---|---:|---:|---:|
| Triage | fast | 1,600 | 400 | $0.004 |
| Evidence synthesis (Knowledge) | standard | 25,000 | 5,000 | $0.150 |
| Root-cause analysis (Root Cause + Code Intel) | reasoning | 50,000 | 10,000 | $0.500 |
| Plan drafting (Planner) | reasoning | 12,000 | 3,000 | $0.135 |
| Plan review (Reviewer) | standard | 8,000 | 2,000 | $0.054 |
| Verification (Verifier) | standard | 6,000 | 2,000 | $0.048 |
| Summaries / approval cards | fast | 3,000 | 2,000 | $0.013 |
| **Single-pass total** | | **105,600** | **24,400** | **$0.90** |

Adjustments (all assumptions, stated):

| Adjustment | Factor | Effect |
|---|---|---|
| Multi-turn tool loops (context re-sent each turn, ~3 turns avg) | ×3 on input, ×1.3 on output | $0.90 → **$1.91** uncached |
| Prompt caching on evidence-heavy calls (shared evidence prefix across RCA/plan/review; ~60% input savings on those steps — assumption) | −$0.74 | $1.91 → **$1.17** |
| Retries, tier fallbacks, verifier re-checks | +15% | **≈ $1.35** |

**Model spend per SEV2 remediation: ~$1.40** (range $1.2–1.9 if cache hit
rate degrades). Add marginal infra actually consumed by the workflow
(workflow-engine compute, retrieval queries, connector gateway, storage and
audit writes — estimated $1.5–4.5, assumption): **~$3–6 per automated
incident**, consistent with [00-executive-summary.md](00-executive-summary.md).

Worked example of the price math (RCA row): 50,000 in × $5/Mtok = $0.25;
10,000 out × $25/Mtok = $0.25 → $0.50 single-pass. Loop-adjusted: input
$0.75, output $0.325. With the ~60% cache assumption on the looped input:
$0.30 + $0.325 ≈ $0.63 — still ~45% of the workflow's model spend, which is
why the evidence prefix is cache-structured and why RCA is the first target
for distillation to the standard tier once
[10-evaluation.md](10-evaluation.md) gates prove parity per failure class.

---

## 3. Monthly model spend at scale

Volumes from [00-executive-summary.md](00-executive-summary.md): ~2,000 SEV
workflows/month, ~30,000 alert-triage-only workflows/month (alerts that
warrant investigation but resolve at TRIAGED), ~50,000 change-risk
evaluations/month.

| Workload | Volume / month | Model $ / unit (assumption) | $ / month |
|---|---:|---:|---:|
| SEV remediation workflows | 2,000 | $1.40 (§2) | $2,800 |
| Alert triage-only workflows | 30,000 | $0.015 (fast tier, ~4k/0.8k tokens; ~10% take a standard-tier evidence peek) | $450 |
| Change-risk scores | 50,000 | $0.055 (standard, ~12k/1.5k; cached org-context prefix; ~5% escalate to reasoning) | $2,750 |
| **Production subtotal** | | | **$6,000** |
| Eval/replay/judge batch load (~20% of production, batch lanes) | | | $1,200 |
| **Total model spend** | | | **≈ $7,200** |

Budgeted at **$9,000/month** with ~25% storm headroom. Two observations the
weekly review keeps re-learning: model spend is ~3% of platform run cost
(§4) — infra, not tokens, is the cost center; and change intelligence costs
roughly as much as remediation despite 25× the volume, because tier routing
works ([07-model-gateway.md](07-model-gateway.md)).

---

## 4. Infrastructure cost per cell

Planning assumptions for one production cell (all five planes,
[01-architecture.md](01-architecture.md) §7), monthly, on-demand cloud
pricing without committed-use discounts:

| Component | Footprint (assumption) | $ / month |
|---|---|---:|
| K8s compute — agent runtime, gateways, workers, connector sandboxes | ~35 nodes mixed general/compute | $18,000 |
| Postgres HA | primary + 2 replicas + PITR storage | $3,500 |
| Kafka | 3 brokers + tiered storage | $4,000 |
| Temporal | server + workers + dedicated Postgres | $3,000 |
| Redis | 3-node HA | $1,500 |
| Qdrant | 3-node cluster | $2,500 |
| Neo4j | 3-node causal cluster | $3,000 |
| Vault + OPA + security tooling | HA Vault, OPA sidecars marginal | $2,000 |
| Observability | OTel collectors + metrics/log/trace ingest | $4,500 |
| Object store, LB, NAT, egress | WORM + blobs + network | $2,000 |
| **Per-cell total** | | **≈ $44,000** |

| Fleet | $ / month |
|---|---:|
| 6 cells × $44,000 | $264,000 |
| Global layer (gateway, fleet console, eval service compute, model gateway control plane, golden-set storage) | $26,000 |
| **Fleet infra total** | **≈ $290,000** |
| Model spend (§3, budgeted) | $9,000 |
| **Platform run cost** | **≈ $300,000 / month** |

Excludes the platform engineering team, which is reported separately in
headcount plans — burying payroll inside unit economics flatters no one.
Committed-use pricing and right-sizing after six months of utilization data
plausibly take 25–35% off the infra line; not assumed here.

---

## 5. Unit economics and ROI

Manual baseline (canonical assumptions): 4.2 engineers × ~4.5 h engaged
time × $110/h loaded ≈ **$2,100 per manually handled SEV2**.

| Cost view | $ / incident | Notes |
|---|---:|---|
| Marginal (model + consumed infra) | $3–6 | §2; the number that governs "should this workflow run" |
| Fully loaded (40% of run cost allocated to remediation ÷ 2,000 SEV/month — allocation assumption; remainder carries change intelligence, triage, learning) | ~$60 | The number that governs "should this platform exist" |
| Manual handling | ~$2,100 | Baseline |

Even fully loaded, automation runs at ~35:1 against manual handling.
Payback framing: the platform's entire monthly run cost equals the manual
cost of ~143 SEV2s ($300,000 ÷ $2,100) — about 7% of monthly SEV volume.
Modeled Phase 3 automation is ~60% of ~2,000 incidents/month, and modeled
engineer-hours returned are ~45,000–60,000 h/month ≈ **$5.0–6.6M/month**
(midpoint $5.8M, [00-executive-summary.md](00-executive-summary.md)).

Sensitivity (monthly, midpoint benefit $5.8M, run cost $0.30M):

| Scenario | Benefit / month | Run cost / month | Net | ROI |
|---|---:|---:|---:|---:|
| Baseline: 60% automatable, modeled MTTR gains | $5.8M | $0.30M | $5.5M | ~19× |
| MTTR improvement half of modeled | $2.9M | $0.30M | $2.6M | ~10× |
| Token prices double | $5.8M | $0.31M | $5.5M | ~19× |
| Only 30% of incidents automatable | $2.9M | $0.29M | $2.6M | ~10× |
| All three combined (pessimistic) | $1.45M | $0.30M | $1.15M | ~5× |

The table's real finding: **token price is not a risk axis** — doubling it
moves platform cost ~3%. The economics live or die on adoption (fraction of
incidents the platform is allowed to handle) and realized MTTR delta, which
is why the evaluation framework ([10-evaluation.md](10-evaluation.md))
measures MTTR delta with matched controls instead of asserting it, and why
the roadmap gates expansion on those measurements
([14-risks-and-roadmap.md](14-risks-and-roadmap.md)).

---

## 6. Cost controls

| Control | Mechanism |
|---|---|
| Per-workflow budgets | Attached by workflow engine, enforced pre-call at the model gateway (§1); exhaustion → degradation ladder, never overrun |
| Per-tenant budgets | Monthly model-spend budget per tenant; alerts at 70%/90%, hard actions at 100% per tenant-configured policy (degrade vs. queue non-SEV work) |
| Tier downgrade under budget pressure | Reasoning-tier calls route to standard with an explicit confidence penalty from `calibration_weights` and stricter gating — downgraded outputs are capped below auto-path eligibility regardless of raw score. Never silent: the downgrade is recorded in workflow state, shown on approval cards, and audited |
| Prompt-cache hit-rate SLO | ≥ 70% on evidence-heavy calls (assumption-derived from §2 cache structure); breach pages the model-gateway owner because it silently multiplies §2 costs |
| Batch lanes | All offline traffic (eval, replay, judge, re-embedding, memory distillation) runs in preemptible batch lanes with a separate budget pool — offline work can be starved, incident work cannot |
| Storm-mode circuit breakers | On alert storms: Sense-plane dedup collapses storms into umbrella incidents, concurrent remediation workflows are capped per cell, triage stays on fast tier, non-essential model traffic freezes ([08-scalability.md](08-scalability.md)) |
| Weekly cost review | Dashboards from `metering.tokens` + infra metering: $ per workflow class, per tenant, per agent, per failure class; cache hit rates; budget-denial rates ([09-observability.md](09-observability.md)) |

**Why tier downgrade carries a confidence penalty:** the cheap failure mode
of cost pressure is quality loss that nobody sees — the standard tier keeps
emitting confident-looking diagnoses and the platform keeps auto-executing
them. Binding downgrade to a penalty plus mandatory gating makes cost
pressure *visible as increased human involvement*, which is the correct
failure direction for an autonomous remediation platform.
**Alternatives considered:** queueing work until budget refresh (unacceptable
for SEVs); rejecting new workflows outright (worse — the platform's whole
value is being present during incidents). **Trade-off:** under sustained
budget pressure, approval load on humans rises; that load is the intended
pressure-relief valve and is itself alarmed. **Operational implication:**
finance-facing reporting and engineering-facing budgets read from the same
metering stream — there is one source of cost truth, disputes are about
allocation, never about measurement.

---

## 7. Assumption register

Every number above that is an assumption rather than a measurement, in one
place, with its blast radius if wrong:

| Assumption | Value | If wrong |
|---|---|---|
| Token prices (all four tiers) | §2 header | §5 shows ±2× moves platform cost ~3% — low risk |
| Tool-loop input multiplier | ×3 | Linear on §2 model spend; measured from week one via `metering.tokens` |
| Cache savings on evidence-heavy calls | ~60% | Degradation to 0% raises per-incident model spend to ~$1.90 — §6 SLO exists for this |
| Marginal infra per automated incident | $1.5–4.5 | Bounds the $3–6 claim; re-derived from metering after first quarter |
| Per-cell infra footprint | $44k/month | Dominant cost line; ±30% moves ROI between ~15× and ~25× at baseline benefit |
| Remediation share of infra (fully-loaded view) | 40% | Allocation only — changes reporting, not spend |
| Automatable incident fraction at Phase 3 | 60% | The primary ROI driver; gated empirically per [10-evaluation.md](10-evaluation.md) |
| Engaged time per manual SEV2 | 4.5 h × 4.2 engineers | Sets the $2,100 baseline; validated against time-in-incident telemetry |

The register is re-baselined quarterly against metered actuals; assumptions
that survive two quarters unmeasured are treated as findings for the weekly
cost review, not as facts.
