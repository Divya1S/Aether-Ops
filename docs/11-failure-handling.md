# 11 — Failure Handling and Degradation

Platform Infrastructure — AetherOps. Inherits terminology from
[01-architecture.md](01-architecture.md). Capacity-level degraded modes and
DR are in [08-scalability.md](08-scalability.md); this document covers the
failure semantics of individual workflows, connectors, models, and humans —
and how every failure degrades the platform toward *human judgment with
good evidence*, never toward silence or guessing.

Two invariants govern everything below:

1. **No silent death.** Every workflow reaches a terminal FSM state
   (`RESOLVED→LEARNED` or `ESCALATED`) with an explanation. A workflow that
   cannot finish its job finishes its handoff.
2. **Degradation tightens governance.** Anything that reduces the quality
   of the platform's inputs (missing evidence, weaker model tier, stale
   data) mechanically reduces Confidence, and reduced Confidence
   mechanically strictens the approval path. The platform is never more
   autonomous than its evidence justifies.

---

## 1. Failure taxonomy

| Class | Modes | Typical blast radius |
|---|---|---|
| Infrastructure | Node loss; AZ loss; full-cell loss | Absorbed by K8s / multi-AZ HA / cell failover ([08-scalability.md](08-scalability.md) §6) — invisible to workflows except added latency |
| Dependency | Connector upstream down; slow; rate-limited (429/Retry-After) | One connector's evidence stream per cell (§3) |
| Model | Provider outage; latency degradation; quality regression; refusal | All agent invocations on the affected tier (§4) |
| Semantic | Schema-invalid output; hallucinated citation; low confidence; contradictory evidence | Single agent invocation — contained by the validation ladder (§5) |
| Workflow | State/workflow timeout; token- or cost-budget exhaustion; stuck gate | Single workflow (§2) |
| Human | No approver available; plan rejected; approver requests changes | Single workflow's gate (§8) |
| Data | Stale evidence; missing telemetry (source never had it); classification block | Confidence of dependent conclusions (§3, §7) |

Semantic failures are the class unique to an LLM-driven platform and get
the deepest defense (§5). Infrastructure failures get the least ink here
precisely because [08-scalability.md](08-scalability.md) makes them boring.

## 2. Timeout architecture: hierarchical budgets

Every workflow runs under a budget tree. Parent budgets propagate down:
each level receives a deadline derived from its parent's remaining budget,
so no child can outlive its parent and no expiry is ever discovered "later".

```
workflow budget (per severity)
└── FSM-state budget (per state, ≤ remaining workflow budget)
    └── agent-invocation budget (≤ remaining state budget)
        └── tool-call / model-call budget (≤ remaining invocation budget)
```

| Level | Default budgets | On expiry |
|---|---|---|
| Tool call | 10 s interactive reads; 60 s heavy queries (log federation) | Retry per §3; then recorded as an evidence gap — never blocks the invocation past its own deadline |
| Model call | Per tier: fast 30 s, standard 90 s, reasoning 300 s, frontier 600 s | Retry once on transient; then tier-fallback path (§4) |
| Agent invocation | Triage 60 s; investigation/diagnosis agents 5–10 min; Planner 5 min | Invocation fails with partial output discarded; state logic decides skip-with-gap vs. escalate |
| FSM state | e.g. INVESTIGATING 15 min (SEV1) / 30 min (SEV2); VERIFYING = verification window + margin | **Skip-with-gap** if the state is enrichment (missing input becomes an explicit gap, Confidence drops); **escalate** if the state is load-bearing (DIAGNOSED, PLANNED, POLICY_CHECK cannot be skipped) |
| Workflow | SEV1 30 min; SEV2 2 h; SEV3 8 h (to reach EXECUTING or ESCALATED) | Transition to ESCALATED **with partial findings**: everything gathered so far — evidence bundle, hypotheses with confidence, what was tried, what timed out — is packaged for the human. Never silent death. |

- **Why chosen:** hierarchical propagation makes "how long can this step
  take" a local computation with a global guarantee; a hung tool call can
  cost at most its own budget, and the human always gets the handoff within
  the workflow deadline.
- **Alternatives considered:** flat per-operation timeouts only — simple,
  but a workflow can crawl forever with each step individually "on time";
  no timeouts with human monitoring — indistinguishable from silent death
  at 2,000 incidents/month.
- **Trade-offs:** budget tuning per severity and state is a real
  maintenance surface; budgets that are too tight cause premature
  escalation. We bias budgets generous and rely on the escalation-quality
  metric (§8) to tell us when tightening actually loses value.
- **Operational implications:** all budgets are config, hot-reloadable per
  cell; expiries are distinct metrics per level
  ([09-observability.md](09-observability.md) §4), and a rising
  state-expiry rate is an early-warning signal for upstream degradation.

## 3. Connector failures and the partial-evidence protocol

Mechanics (implemented in the connector gateway,
[04-connectivity.md](04-connectivity.md)):

- **Circuit breakers, per connector per cell:** error-rate and latency
  thresholds open the breaker; half-open probes (single request per probe
  interval) test recovery; state transitions are audited and metered.
- **Retry with jitter:** exponential backoff with full jitter, honoring
  `Retry-After` exactly when the upstream provides it — an upstream that is
  telling us when to come back must not be hammered by our own schedule.
  Retries never exceed the tool-call budget (§2).
- **Cache fallback with staleness stamping:** on breaker-open or budget
  exhaustion, the retrieval service may serve the read-replica cache. Every
  cache-served Evidence record carries `retrieved_at` and
  `served_stale: true`; staleness is data, never hidden.

**Partial-evidence protocol.** When a source is unavailable, the Evidence
bundle carries an explicit, machine-readable gap record:

```
{ "gap": "connector-unavailable",
  "source": "datadog",
  "window": "2026-08-07T14:02Z–14:20Z",
  "attempted": ["metrics.query p99 checkout", "monitors.list checkout"],
  "fallback": "cache (age 34 min)" }
```

Gaps render verbatim in agent prompts and human summaries — "Datadog
unavailable 14:02–14:20; metrics conclusions based on 34-minute-old cache"
— so no one downstream, human or model, can mistake absence of evidence
for evidence of absence.

### 3.1 The key mechanism: availability → confidence → governance

This coupling is the load-bearing design decision of the document.
Evidence-coverage is a factor of Confidence by definition
([01-architecture.md](01-architecture.md) §9: model self-estimate ×
evidence coverage × historical calibration). Gap records reduce measured
coverage; reduced coverage reduces Confidence arithmetically; and OPA
policies key approval tiers on Confidence bands
([05-security.md](05-security.md)). Therefore **a connector outage
automatically strictens the approval path for every affected workflow,
with no special-case code**:

```
Datadog breaker opens
  → Evidence bundle records the gap (coverage 0.9 → 0.6)
  → RootCause confidence 0.87 → 0.58
  → OPA: rollback at confidence <0.7 ⇒ approval tier 2 → tier 3
  → human approves with the gap visible in the summary
```

- **Why chosen:** it removes an entire class of bugs — "the platform acted
  confidently on partial data" — by making that state *unrepresentable*.
  Degraded inputs cannot produce autonomous outputs, because autonomy is a
  function of Confidence and Confidence is a function of inputs.
- **Alternatives considered:** (a) hard-stop on any connector outage —
  safe but wasteful: diagnosis from four of five sources is still worth
  hours to the human who receives it; (b) explicit degraded-mode rules per
  connector ("if Datadog down, require approval for X") — a combinatorial
  rule matrix that drifts from reality within a quarter.
- **Trade-offs:** during a broad estate outage — precisely storm mode —
  Confidence drops fleet-wide and more workflows route to humans, at peak
  human load. This is the correct failure direction (the platform demands
  oversight exactly when its vision is impaired), but it makes approval-
  queue capacity a storm-planning input
  ([08-scalability.md](08-scalability.md) §4.4).
- **Operational implications:** coverage weights per evidence source and
  the Confidence→tier bands are versioned policy, reviewed with security;
  the eval service continuously validates that Confidence remains
  calibrated under injected gaps ([10-evaluation.md](10-evaluation.md)).

## 4. LLM failures

Mechanics live in the model gateway ([07-model-gateway.md](07-model-gateway.md));
failure semantics:

- **Provider outage:** breaker at the gateway; calls queue durably by
  severity; sustained outage → advisory-only mode per
  [08-scalability.md](08-scalability.md) §7.
- **Tier fallback with confidence penalty:** if the routed tier (e.g.
  reasoning: `claude-opus-5`) is unavailable, the gateway may fall back one
  tier (standard: `claude-sonnet-5`) for tolerant call sites. Fallback
  applies a multiplicative confidence penalty to the invocation's output —
  a weaker model's answer must not inherit the stronger tier's historical
  calibration — which, via §3.1, may strict the approval path. Fallback
  *up* (to frontier: `claude-fable-5`) is never automatic; it is a routing
  decision with budget implications.
- **Quality regression:** continuous canary evals — a fixed golden slice
  replayed against each tier hourly ([10-evaluation.md](10-evaluation.md)).
  A statistically significant score drop (provider-side model change,
  silent quantization, our own template regression) alerts platform SRE
  and can pin the gateway to the last-known-good routing.
- **Refusal handling:** on a safety refusal for legitimate operational
  content, the gateway retries **once** with a neutral rephrase of the
  instruction frame (never altering Evidence content); a second refusal
  escalates the invocation — refusals are logged with prompt hash and are
  a tracked metric, because a refusal-rate step change is a provider-side
  behavior change we need to know about within hours.
- **Runaway-output guards:** hard `max_tokens` per call site; per-workflow
  cost circuit breaker (default $25 SEV2-class, config per tenant) that
  trips the workflow to ESCALATED-with-findings rather than silently
  burning budget; token-anomaly detection per template version catches the
  slow version of the same failure
  ([09-observability.md](09-observability.md) §7).

## 5. Hallucination containment: layered defense

No single check catches fabrication reliably; five layers, cheapest and
most deterministic first, each independently sufficient to stop an output
from becoming an action:

| Layer | Check | Deterministic? |
|---|---|---|
| 1. Schema validation | Output parses against the agent's typed contract ([02-agents.md](02-agents.md)); bounded repair-retries, then invocation fails | Yes |
| 2. Citation verification | Every cited Evidence ID must exist in the workflow's bundle; every quoted excerpt must appear **literally** (normalized string containment) in the cited Evidence content. No LLM involved — a fabricated citation cannot pass string matching | Yes |
| 3. Security agent screening | Injection/exfiltration screening of outputs derived from external content ([05-security.md](05-security.md)) | Model-assisted |
| 4. Reviewer agent | Independent model (different tier or template lineage) reviews generated Plans and PRs for coherence with cited evidence | Model-assisted |
| 5. Human gates | Anything below auto-approval thresholds — including everything Confidence-penalized by §3–§4 — reaches a human with the full evidence bundle | Human |

Layer 2 deserves emphasis: because Evidence records are immutable once
recorded and every claim must cite, the *only* way a hallucinated fact
reaches a Plan is with a citation that either does not exist (caught) or
does not contain the quoted text (caught). What layer 2 cannot catch —
correctly-quoted evidence assembled into a wrong conclusion — is exactly
what layers 3–5 exist for.

**Feedback loop:** every hallucination flag from any layer (or post-hoc
from the eval service's judge fleet) is recorded against (agent,
prompt-template version) and folded into that agent's **historical
calibration weight** — the third factor of Confidence. An agent that has
recently hallucinated is arithmetically less trusted, which routes more of
its output through human gates until its measured reliability recovers.
Trust is earned back with data, not with time.

## 6. Network partitions and split-brain prevention

- **Sense plane:** Kafka retains `signals.raw` ≥ 24 h. If the path from
  Sense to Control is severed, ingestion continues; on heal, the normalizer
  replays, dedup collapses the backlog, and the storm detector prevents a
  replay stampede ([08-scalability.md](08-scalability.md) §7).
- **Control plane:** Temporal workflows are durable — a partition pauses
  progress, loses nothing, and workflows resume from their last event on
  heal.
- **Approvals — split-brain prevention:** every approval decision is
  recorded with a **fencing token** (monotonic per workflow, issued by the
  approval service at gate creation, persisted in workflow state and the
  audit ledger). An EXECUTING step validates its fencing token at dispatch;
  a stale token — from a superseded gate, a re-opened workflow, or the
  losing side of a partition/failover — is rejected deterministically. One
  gate, one token, at most one execution.
- **Time-bounded approvals:** if gate-to-execution delay exceeds a
  threshold (default 15 min; lower for HIGH-risk steps), the workflow
  **re-enters POLICY_CHECK** before EXECUTING: the world the approver saw
  may no longer exist, so the policy evaluation — and if policy demands it,
  the approval — must be refreshed. The same rule fires unconditionally
  after any cell failover ([08-scalability.md](08-scalability.md) §6.3).
  Trade-off: occasionally a human approves twice; we accept the annoyance
  because the alternative is executing a decision made about a past world.

## 7. Stale knowledge

- **TTL stamping:** every Evidence record carries `retrieved_at` and a
  source-class TTL (metrics: minutes; deploy history: hours; runbooks:
  until version change). Agents receive age with every record; policies can
  refuse to auto-execute Plans grounded in evidence past TTL.
- **Runbook version pinning:** a Plan that cites a runbook pins its version
  hash; if the runbook changes between PLANNED and EXECUTING, the pin
  mismatch forces re-planning — the platform never executes yesterday's
  procedure against today's system on the strength of yesterday's read.
- **Memory review cycles:** organizational memory (failure classes,
  remediation efficacy) is re-validated on a scheduled cycle and demoted
  when contradicted by newer episodes
  ([06-retrieval-and-memory.md](06-retrieval-and-memory.md)).
- **Invalidation webhooks:** connectors that can push (GitHub, Confluence,
  ArgoCD) register change hooks that evict affected cache entries and mark
  dependent memory for re-validation, shrinking the stale window from TTL
  to propagation delay.
- **Tracked defect class:** "acted on stale evidence" is a first-class
  defect class in the eval service ([10-evaluation.md](10-evaluation.md)):
  any incident where post-hoc review shows a decision grounded in evidence
  that source systems had already superseded. Its rate is a standing
  quality KPI, and its case studies are the primary input for tuning the
  TTL table — TTLs are set from measured staleness harm, not intuition.

## 8. Human escalation matrix

Escalation is a **first-class success path, not a failure**. The metric
frame is deliberate: an escalation that hands a human a triaged incident, a
cited evidence bundle, and three ranked hypotheses has already saved most
of the investigation hours — the 60–75% of MTTR that is diagnosis
([00-executive-summary.md](00-executive-summary.md) §2). We therefore
measure **escalation quality** (evidence coverage at handoff, human-rated
usefulness, time saved vs. cold start) as a positive KPI alongside
autonomous-resolution rate — and never set incentives that reward the
platform for guessing instead of escalating. A platform optimized to avoid
escalation at all costs is a platform optimized to hallucinate; the
"evidence or silence" commitment requires that silence be cheap and
dignified.

| Trigger | Escalates to | Context delivered |
|---|---|---|
| Insufficient evidence / low Confidence at DIAGNOSED | Service on-call (incident channel) | Evidence bundle with gaps highlighted, ranked hypotheses with confidence and citations, queries already run, suggested next queries |
| Contradictory evidence | Service on-call + platform SRE tagged | Both evidence chains side by side; the contradiction stated explicitly, never averaged into a middle guess |
| Plan rejected at gate | Planner re-plans once with rejection reason; second rejection → service on-call | Rejected Plan, rejection rationale, alternatives considered by the Planner |
| No approver within gate SLA | Approval-tier escalation chain (team lead → manager → duty IC) | Original approval summary + elapsed-wait context; page, not passive notification |
| Workflow/cost budget exhausted | Service on-call | Partial findings package per §2: everything gathered, what was tried, exactly where budget ran out |
| Verification failed, rollback also failed | Duty incident commander (SEV escalation) | Full action history with compensation status per step, current system state evidence, "hands off — state uncertain" banner |
| Policy denial (POLICY_CHECK → ESCALATED) | Service on-call + policy owner | Denying rule ID, the Plan, and the evidence — so humans can fix the policy or do the action manually with eyes open |
| Platform-side failure (advisory-only mode) | Platform SRE + tenant status page | Cell mode, cause, ETA; tenant workflows continue in advisory form |

Every escalation names *why* it escalated in one machine-readable reason
code — feeding the escalation-reason Pareto in
[09-observability.md](09-observability.md) §6.

## 9. Top-10 failure modes: end-to-end mapping

| # | Failure mode | Detection signal | Automatic response | Degraded mode | Human touchpoint | Recovery | Eval service learns |
|---|---|---|---|---|---|---|---|
| 1 | Connector upstream down | Breaker open (error rate) | Retry w/ jitter → cache fallback → gap record | Partial evidence; Confidence↓ ⇒ stricter approvals | Approver sees gap in summary | Half-open probe closes breaker; gap window recorded | Diagnosis accuracy delta under gaps → coverage weights |
| 2 | Connector rate-limited | 429 + Retry-After | Honor Retry-After; queue reads | Slower evidence; possible gap | None unless gap forms | Limit window passes | Per-connector budget tuning data |
| 3 | Model provider outage | Gateway breaker + canary probes | Queue by severity; tier fallback where allowed | Advisory-only if sustained | Platform SRE paged; tenants notified | Provider healthy; queue drains | Fallback-tier quality deltas → routing policy |
| 4 | Model quality regression | Canary eval score drop | Pin last-known-good routing | Possibly reduced tier | Platform SRE reviews canary diff | Provider fix or template fix; unpin | Regression case → golden set |
| 5 | Schema-invalid output | Validation layer 1 | Bounded repair-retries; fail invocation | Retry cost; state may skip-with-gap | None (contained) unless state escalates | Next invocation | Template/agent error patterns → prompt fixes |
| 6 | Hallucinated citation | Layer 2 literal-match failure | Output rejected; invocation retried then failed | Confidence↓ via calibration weight | Human gate if repeated | Calibration recovers with clean record | Flag → (agent, template) calibration weights |
| 7 | Workflow timeout | Budget-tree expiry | ESCALATED with partial findings | Human-led from handoff | Service on-call receives package | Human resolves; workflow closed as escalated | Where time went → budget + bottleneck tuning |
| 8 | Stuck gate (no approver) | Gate SLA timer | Escalation chain page | Waiting; incident clock running | Tier chain up to duty IC | Approval or expiry→ESCALATED | Gate-wait patterns → approval-routing fixes |
| 9 | Verification failure | Verifier: metrics not recovered | ROLLING_BACK via compensation handlers | Post-rollback re-diagnosis or escalate | On-call notified with before/after evidence | Rollback verified; re-plan or human takeover | Failed remediation → failure-class efficacy update |
| 10 | Acted on stale evidence | Post-hoc eval review; invalidation-hook mismatch | (Preventive: TTL + pinning + re-check §6) | N/A — detected retrospectively | Postmortem review flag | TTL table and hook coverage updated | Defect-class case study → TTL policy (§7) |

The last column is the point of the table: every failure mode terminates in
the Evaluation service, because a failure the platform cannot learn from is
a failure it will repeat at 15,000-service scale. The learning loop —
failure → flag → calibration/policy/golden-set update — is the same
mechanism that powers organizational learning for the estate
([10-evaluation.md](10-evaluation.md)); the platform is its own tenant
zero.
