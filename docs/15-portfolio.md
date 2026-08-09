# 15 — Portfolio Justification, Resume Material, and Interview Preparation

**Audience note.** This document is for the platform author presenting this
work — in portfolio reviews, resume drafting, and interviews. The tone stays
professional and the claims stay honest: this is a design plus a reference
implementation, not a production deployment, and every bullet and answer
below is worded accordingly. Technical claims herein defer to the canonical
docs ([00-executive-summary.md](00-executive-summary.md),
[01-architecture.md](01-architecture.md)).

---

## 1. Portfolio justification: why this demonstrates Staff/Principal-level thinking

AetherOps is a **socio-technical system design**, not a model demo. The hard
problems it solves are not "can an LLM read a stack trace" — they are: how
does an organization of 50,000 engineers come to *trust* automated action
against production, how is that trust earned incrementally and revoked
automatically, and how does the system remain auditable, reversible, and
economically justified while doing it. The trust ladder, approval tiers, and
escalation-is-success framing are organizational mechanisms expressed as
architecture — which is precisely the register Staff/Principal work operates
in.

The signals, enumerated:

| Signal | Where it shows |
|---|---|
| Explicit trade-off analysis | Every major decision recorded with rejected alternatives and revisit triggers (section 4 below; [01-architecture.md](01-architecture.md) §7) |
| Failure-first design | Failure taxonomy and degradation ladders ([11-failure-handling.md](11-failure-handling.md)); a risks doc that names the existential risk and what year one will not do ([14-risks-and-roadmap.md](14-risks-and-roadmap.md)) |
| Security as architecture, not feature | Plane separation makes "only Execution holds credentials, only Control invokes writes" network-checkable ([05-security.md](05-security.md)) |
| Evaluation as growth mechanism | The trust ladder ties autonomy to measured precision per (agent, failure-class); golden sets and replay gate every promotion ([10-evaluation.md](10-evaluation.md)) |
| Honest cost/ROI modeling | ~$3–6 per automated incident vs ~$2,100 manual, with token budgets and storm circuit breakers, modeled not asserted ([13-cost-model.md](13-cost-model.md)) |
| Phased de-risked rollout | Four phases with measurable exit criteria; writes gated behind advisory success; autonomy gated behind gated-execution success ([14-risks-and-roadmap.md](14-risks-and-roadmap.md)) |

The differentiator versus typical "AI agent" portfolio projects: this design
starts from the reasons enterprises say no — unauditable control flow,
ungoverned writes, uncited claims, unbounded cost — and makes each one a
first-class architectural constraint rather than a disclaimer.

---

## 2. Resume bullets

Verb choice is deliberate: "Designed", "Architected", "Built reference
implementation of" — no claim of production deployment anywhere.

1. Designed AetherOps, an autonomous incident-remediation and
   change-intelligence platform closing the loop alert → triage →
   evidence-grounded RCA → policy-gated reversible remediation →
   verification → organizational learning, targeting a 50,000-engineer /
   ~15,000-service / ~2M-alerts-per-day enterprise scale point.
2. Architected a 13-agent multi-agent system in which agents never call each
   other directly — a deterministic workflow engine (Temporal) mediates all
   interaction through typed workflow state, making every run replayable and
   auditable.
3. Designed "plan compilation": LLM-proposed remediations are expressed only
   as references into a typed, versioned Step Catalog and compiled to DAGs
   with saga compensation handlers, so probabilistic models never control
   execution flow.
4. Designed an MCP connector gateway as the platform's sole egress to 25
   enterprise integrations (GitHub, Datadog, PagerDuty, Kubernetes, AWS, and
   peers), concentrating credentials, rate limiting, caching, and sandboxing
   in one auditable choke point.
5. Architected OPA-based governance: every action carries a risk class, a
   Rego policy, an approval tier, and a compensation handler, with a
   hash-chained append-only audit ledger recording every model call, tool
   call, decision, and approval.
6. Designed an evidence pipeline with mandatory citations and
   anti-hallucination validation — federated retrieval over systems of
   record (no petabyte copying), evidence-coverage thresholds, and an
   "evidence or silence" rule that escalates instead of guessing.
7. Built an evaluation framework as code: golden-scenario replay harness
   with RCA precision, calibration error, and citation-faithfulness
   metrics driving a trust ladder (advisory → gated → auto-path per
   failure class), enforced as CI release gates on every push.
8. Built a RAG subsystem with measured retrieval quality: hybrid
   keyword+vector search over attributed chunks (rag://doc#offset), two
   comparable chunking strategies, swappable embedders (stdlib TF-IDF /
   Ollama), and a 22-query labeled evaluation — fixed-window chunking
   measured at precision@1 = 1.000 / MRR = 1.000 vs. paragraph at
   0.955 / 0.977 — with each incident's generated postmortem ingested
   back for future retrieval.
9. Integrated a free local LLM (Ollama) behind an ordered fallback chain
   with audited graceful degradation — kill the model mid-incident and
   the workflow completes on the deterministic backend — plus per-call
   latency, token, and estimated-cost metering feeding workflow traces
   reconstructed from the audit ledger.
10. Engineered LLM-security controls mapped to OWASP LLM Top 10 (2025)
    with the attacks in the test suite: prompt-injection quarantine
    (LLM01), governed execution via Step Catalog + approval gates
    (LLM06 Excessive Agency), schema-validated agent outputs with
    semantic retry (LLM05), and a versioned prompt registry whose
    sha256 lockfile fails the build on unversioned prompt edits.
11. Productionized the platform at zero infrastructure cost: an
    authenticated REST API replaying approval-gate semantics over HTTP,
    SQLite persistence with a tamper-evident audit chain, a Docker
    image smoke-tested end to end, and an MCP server (JSON-RPC/stdio)
    exposing platform tools to any MCP client.
12. Modeled unit economics end-to-end (~$3–6 per automated incident vs
    ~$2,100 manual; ~45–60k engineer-hours/month returned at maturity)
    and designed cell-based multi-region deployment with data-residency
    pinning — then made cost a measured number via per-call metering.
13. Built the reference implementation pure-stdlib by design: 132 tests
    and dual evaluation gates run in CI on Python 3.11–3.13 with no
    dependencies, no network, and no API keys.

---

## 3. Interview questions with strong-answer sketches

### Architecture

**Q1. Why deterministic orchestration instead of autonomous agents?**
Because control flow is the certification surface. An LLM-planned tool loop
cannot be audited, replayed, or bounded — and enterprises correctly refuse it
write access, which forfeits the entire ROI. In AetherOps, LLMs generate
hypotheses and draft plans, but plans compile into typed DAGs of Step
Catalog verbs executed by Temporal with retries, checkpoints, sagas, and
gates. The trade is flexibility: the platform can only do what the catalog
permits. That constraint is the product — it is why the security review can
say yes.

**Q2. Why cell-based deployment instead of one global multi-tenant system?**
Three forcing functions: blast radius, data residency, and organizational
trust. A poison workflow or tenant storm must not degrade the whole fleet,
and GDPR-pinned tenants need per-cell residency rather than per-row
filtering. Per-tenant deployments were rejected as operationally
unaffordable at 100+ tenants; cells are the standard middle ground. The
accepted cost is that org-wide learning needs an explicit sanitizing
replication pipeline — which we wanted anyway, because it forces the
data-classification boundary compliance requires.

**Q3. Why federated retrieval instead of a central data lake?**
Copying petabytes of telemetry into a platform lake fails on cost,
freshness, and governance simultaneously — you duplicate spend, you diagnose
against stale data, and you inherit every source system's compliance scope.
Federated queries through the connector gateway hit systems of record live,
and AetherOps persists only pointers plus retrieved excerpts as immutable
Evidence records with citations. The trade-off is query-time dependence on
source availability and rate limits, mitigated by the gateway's caching and
read replicas — and it keeps the citation story honest, since every claim
points into the system of record itself.

### Agents

**Q4. Why 13 specialized agents instead of one general agent?**
Specialization buys three things a monolith cannot provide: per-agent typed
contracts (narrow inputs and outputs are testable and replayable),
per-agent trust (the ladder promotes the Root Cause Agent on
deploy-regressions independently of the Infrastructure Agent on capacity),
and per-agent model tiering (triage runs on the fast tier; deep causal work
runs on reasoning tiers). The cost is orchestration complexity, which is
exactly what the deterministic workflow engine absorbs. One prompt doing
triage, diagnosis, planning, and verification would be untestable and
unpromotable as a unit.

**Q5. How does confidence calibration actually work?**
Confidence is not the model's self-reported number. It is a composite:
model self-estimate × evidence coverage × historical calibration, where the
calibration term comes from replaying past outputs against known outcomes
per agent and failure class. The thresholds then have teeth: below 0.5 the
workflow escalates, 0.5–0.8 requires human approval, above 0.8 is
auto-eligible subject to policy and the trust ladder. Calibration curves are
recomputed whenever the model version changes, because a provider update
silently invalidates the historical term.

**Q6. How do agents avoid compounding each other's errors?**
Structurally, not behaviorally. Agents never call each other; every output
lands in typed workflow state with citations and confidence, and the
workflow engine decides what happens next. Downstream agents receive
upstream claims as cited evidence they can weigh, not as ground truth in a
shared context window. Confidence gates sit between stages — a low-confidence
diagnosis never reaches the Planner — and the Reviewer and Verifier agents
are adversarial by contract: one attacks the plan before execution, one
measures actual recovery after it, closing the loop with reality rather
than with another model's opinion.

### Security

**Q7. Describe the prompt-injection defense in depth.**
Assume retrieved content is hostile — commit messages, tickets, and log
lines are attacker-writable. Layer one: evidence is wrapped and labeled as
untrusted data in every prompt, never concatenated as instructions. Layer
two: the Plan Compiler accepts only Step Catalog references, so injected
free text has no path to becoming a verb. Layer three: OPA evaluates every
action against policy regardless of plan origin. Layer four: the connector
gateway is the sole egress, so even a fully steered agent process cannot
reach an external system directly. Finally, injection canaries live in the
golden datasets, so the detection rate is a measured number, not an
assumption.

**Q8. Why does redaction never use hosted LLMs?**
Circularity: redaction's job is to prevent sensitive content from reaching
hosted models, so using a hosted model to redact sends the secrets in order
to remove them. Redaction therefore runs on deterministic pattern engines
and locally hosted classifiers inside the cell, before any content crosses
the model gateway. The trade-off is lower recall on exotic PII shapes than
a frontier model might achieve, covered by conservative classification
defaults and by the audit ledger recording redaction decisions for review.

**Q9. How do you reconcile GDPR erasure with an immutable audit ledger?**
Separate what must be immutable from what must be erasable. The
hash-chained ledger stores the *facts of operation* — who approved what,
which policy fired, hashes of payloads — while personal and sensitive
content lives in referenced storage under classification-driven retention.
Erasure deletes or crypto-shreds the referenced content; the ledger retains
the hash chain, which no longer resolves to content but still proves the
sequence of actions was untampered. Chain integrity survives; the personal
data does not.

### Operations

**Q10. Walk through storm-mode design.**
A regional outage can multiply alert volume a hundredfold, and the naive
platform response — a workflow per alert — is a self-inflicted
denial-of-service plus a cost incident. The Sense plane dedups and
storm-suppresses first, collapsing correlated alerts into few incident
workflows. The model gateway enforces per-workflow token budgets and
downgrades tiers under load, reserving reasoning capacity for the storm's
root workflow. Backpressure propagates through Kafka consumer lag rather
than dropping signals, and a per-cell spend circuit breaker halts non-SEV
work. The design goal is that the platform's marginal cost during a storm
grows with incident count, not alert count.

**Q11. What happens when the model provider is down mid-incident?**
The Control plane does not need a model to stay safe — Temporal owns the
FSM, so workflow state is durable and nothing is lost. The gateway first
falls back across model tiers; if all tiers are unavailable, workflows
degrade along a designed ladder: in-flight executions complete their
current typed step or compensate, diagnosis-stage workflows escalate to
humans with the evidence bundle assembled so far, and the Sense plane keeps
queuing. The platform's floor is "organized evidence, clean handoff" —
never a stalled write against production.

**Q12. How would you roll out a new Step Catalog action safely?**
Like a production launch, because it is one. The verb ships with its risk
class, Rego policy, approval tier, and a compensation handler proven in
staging under fault injection. It then walks the trust ladder from the
bottom regardless of how proven neighboring verbs are: shadow-mode
proposals first, then gated execution with mandatory approval, then
auto-path eligibility only after sustained precision per failure class. A
single policy breach or compensation failure resets the pair to advisory.
The catalog is versioned, so plans reference an exact verb version and
replay stays meaningful.

### Evaluation

**Q13. How do you measure hours saved honestly?**
By refusing the flattering denominators. The model counts only incidents
where the platform materially participated — a bundle rated useful, an
approved execution — and compares MTTR and paged-engineer-hours against a
pre-platform baseline for the same failure classes, not against the fleet
average. Escalations count as partial wins only for the investigation time
demonstrably shortened, and SRE ratings gate whether a bundle counts at
all. The headline ~45–60k hours/month is a maturity-state model with stated
assumptions in [13-cost-model.md](13-cost-model.md), presented as modeled,
not measured — and the Phase 1 exit criteria exist to replace the model
with data.

**Q14. How does the trust ladder promote and demote?**
Promotion is earned per (agent, failure-class) pair, never globally. A pair
advances from advisory to gated writes on sustained golden-set precision
plus human-approval history at volume; it advances to auto-path on gated
precision, low compensation rate, and low override rate — with thresholds
signed off by the SRE org, not set unilaterally by the platform team.
Demotion is asymmetric and automatic: a policy breach, a compensation
spike, calibration drift, or a rising postmortem-disagreement rate drops
the pair a rung immediately, and re-promotion requires re-earning the
record. Trust is cheap to lose and expensive to regain by design, because
that mirrors the organization it serves.

**Q15. Your evaluator is an LLM judge — why should anyone trust it?**
Alone, they should not, so it is never alone. Judges are themselves
evaluated: their verdicts are spot-audited against human labels, and a
judge that drifts from human agreement is recalibrated or replaced.
High-stakes signals — trust-ladder promotions, model-version gates — always
combine judge scores with hard outcome metrics that need no judgment:
verifier-measured recovery, compensation rate, override rate,
postmortem-vs-RCA disagreement. The judge fleet scales label coverage;
the ground truth remains human ratings and production outcomes.

### The cut question

**Q16. You have 3 engineers and 6 months. What do you cut?**
Everything below advisory. Ship Phase 1 only: read-only federated
retrieval over 5 connectors — PagerDuty, Datadog, GitHub, Kubernetes, Slack
— feeding Triage and Root Cause agents that post cited evidence bundles
into the incident channel, with ratings feeding a golden dataset from day
one. Cut all writes, hence the Step Catalog, approval service, most of the
OPA surface, and compensation machinery; cut cells (one region, one
deployment); collapse the 13 agents to 3–4. What is deliberately kept is
the spine that cannot be retrofitted: citations mandatory, evidence
immutability, the audit ledger, and the model gateway abstraction. This is
defensible because Phase 1 is where trust and training data are created —
and investigation, not remediation, is 60–75% of MTTR, so advisory-only
still returns the majority of the available hours.

---

## 4. Design tradeoffs table

| Decision | Alternative rejected | Why | What we gave up | Revisit trigger |
|---|---|---|---|---|
| Temporal for orchestration | Building durable workflows on queues + state tables | Durable execution, replay, timers, and saga support are years of work to rebuild credibly; determinism is the audit story | Operational cost of a Temporal fleet per cell; team learning curve | Temporal licensing/operational cost dominating cell TCO, or a managed durable-execution offering with equivalent replay semantics |
| Cell-based deployment | Global multi-tenant; per-tenant stacks | Blast-radius caps and per-cell data residency; per-tenant is unaffordable at 100+ tenants | Org-wide learning needs a sanitizing replication pipeline; fleet ops burden | Cell count growth outpacing platform team, or residency requirements collapsing to one region |
| Federated retrieval | Central telemetry lake | Petabyte copying fails on cost, freshness, and compliance scope; citations should point into systems of record | Query-time dependence on source availability and rate limits | Source-side query costs or latency SLO breaches exceeding modeled cache benefit |
| MCP connector gateway | Bespoke per-tool SDK integrations | One protocol, one egress choke point, one audit surface; connector marketplace leverage | Protocol overhead; MCP spec maturity risk | MCP stagnation or a connector class fundamentally unrepresentable in the protocol |
| 13 specialized agents | One monolithic agent | Typed narrow contracts are testable; trust and model tier assignable per agent; independent promotion | Orchestration complexity; more contracts to version | Frontier models making single-context reliability measurably superior on golden sets |
| pgvector-first, graduate to Qdrant | Qdrant-everywhere from day one | One fewer stateful system per cell early; Postgres ops maturity; most memory tiers are small initially | A migration when episodic/org memory outgrows pgvector | Vector count or recall-latency SLOs breaching pgvector comfort zone in any cell (path already in [01-architecture.md](01-architecture.md) §8) |
| Deterministic policy (OPA) on actions | LLM judgment call per action | Policy must be reviewable, diffable, testable, and certifiable; "the model decided it was fine" fails every audit | Nuance on novel situations; Rego authoring burden | Never for the allow/deny decision; LLMs may pre-screen or explain, not decide |
| Python for agent runtime and workers | Go across the platform | ML ecosystem, SDK maturity, iteration speed where the work is prompt-and-evidence shaped; Temporal removes the concurrency argument | Higher per-worker footprint; slower cold starts | Worker compute becoming a top-3 cost line, or connector gateway throughput demanding Go/Rust rewrite of hot paths |
| Buy hosted Claude tiers via gateway | Self-hosting open-weight models | Frontier reasoning quality on RCA is the product bottleneck; GPU fleet ops is a distraction at this stage; gateway keeps the exit open | Per-token cost; provider dependence (risk R9 in [14-risks-and-roadmap.md](14-risks-and-roadmap.md)) | Open-weight models matching golden-set precision at materially lower cost, or data-boundary mandates barring hosted inference |
| Hash-chained Postgres audit + WORM S3 | Blockchain-style distributed ledger | Threat model is tamper-evidence for auditors, not Byzantine consensus among adversarial parties; a chain of hashes in Postgres with WORM archival satisfies it | Cryptographic theater to point at in sales decks | An actual multi-party trust requirement (e.g., cross-company federation) — until then, rejected as complexity theater |
| Evidence immutability once recorded | Mutable evidence store with updates | RCA claims must cite what was actually seen at decision time; mutation destroys replay and audit | Storage growth; corrections require new records, not edits | Storage cost material at scale — mitigate with tiering, never with mutation |

---

## 5. Why this reflects Staff Engineer thinking

**Leverage over novelty.** Nothing in AetherOps is a research contribution —
Temporal, OPA, MCP, Kafka, and hosted Claude tiers are all
off-the-shelf. The design work is in the composition: which invariants make
the whole certifiable, where the choke points go, what the organization can
actually adopt. Staff work is choosing boring components and a novel
arrangement, not the reverse.

**Blast-radius framing.** Every layer asks the same question — what is the
worst this can do, and what bounds it? Cells bound tenant failures, the
Step Catalog bounds verbs, OPA bounds scope, compensation bounds
irreversibility, approval tiers bound authority, budgets bound spend. A
platform that touches production earns its existence by how it fails, not
by how it succeeds.

**Organizational adoption as a design constraint.** The trust ladder,
escalation-is-success metrics, per-team hours-returned reporting, and the
"best-informed participant, never the commander" scoping are not messaging
— they are architecture, because the failure mode they prevent
(organizational rejection, risk R6) kills the platform as surely as any
outage. Designing for the humans who must say yes is the difference
between a system that works in a demo and one that survives contact with
50,000 engineers.

**Evaluation before autonomy.** Autonomy is never granted, only earned:
golden datasets before writes, gated execution before auto-path, measured
precision per (agent, failure-class) before promotion, automatic demotion
on breach. The evaluation service is not QA at the end of the pipeline —
it is the mechanism by which the platform is allowed to grow.

**Cost as a first-class requirement.** The ~$3–6 versus ~$2,100 unit
economics, token budgets, tier routing, and storm circuit breakers are in
the architecture documents, not in a pricing appendix, because a platform
whose marginal cost scales with alert volume rather than incident count is
a cost incident waiting for a bad week. Knowing what the system costs per
unit of value — and designing the meters before the bill arrives — is what
makes the ROI claim an engineering statement instead of a pitch.
