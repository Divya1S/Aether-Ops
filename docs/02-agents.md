# 02 — Multi-Agent Architecture

**Author:** Platform Infrastructure — AetherOps
**Status:** Proposed — reference implementation in `src/aetherops/agents/`
**Reads with:** [01-architecture.md](01-architecture.md) (planes, FSM), [03-orchestration.md](03-orchestration.md) (plan compilation, sagas, gates), [06-retrieval-and-memory.md](06-retrieval-and-memory.md) (evidence, memory tiers), [07-model-gateway.md](07-model-gateway.md) (tier routing), [10-evaluation.md](10-evaluation.md) (calibration).

---

## 1. Roster overview

Thirteen agents run in the Intelligence plane's agent runtime (Python 3.12 workers on Kubernetes). Agents never call each other directly: every invocation is an activity scheduled by the workflow engine (Temporal), and all inter-agent data flows through typed workflow state. This is the property that makes runs replayable, auditable, and safe to retry.

| # | Agent | Kind | Model tier | Primary role in the FSM |
|---|---|---|---|---|
| 1 | Triage | Deterministic rules + LLM assist | fast (`claude-haiku-4-5-20251001`) | DETECTED → TRIAGED |
| 2 | Planner | LLM | reasoning (`claude-opus-5`); frontier (`claude-fable-5`) on SEV1/novel failure class | DIAGNOSED → PLANNED |
| 3 | Coordinator | **Deterministic service** | n/a | Sequences all agent invocations |
| 4 | Root Cause | LLM | reasoning (`claude-opus-5`); frontier on SEV1/novel | INVESTIGATING → DIAGNOSED |
| 5 | Code Intelligence | LLM | standard (`claude-sonnet-5`) | Evidence for diagnosis; suspect commits |
| 6 | CI/CD | LLM | standard (`claude-sonnet-5`) | Pipeline forensics; rollback targets |
| 7 | Infrastructure | LLM | standard (`claude-sonnet-5`) | Cluster/cloud state; infra Step Catalog actions |
| 8 | Knowledge | LLM | standard; fast for query expansion | Evidence bundle assembly (retrieval orchestration) |
| 9 | Policy | **Deterministic service** | fast-tier summarization only | POLICY_CHECK |
| 10 | Security | Deterministic scanners + LLM assist | fast for injection screening; standard for action-risk | Screens evidence, artifacts, actions |
| 11 | Evaluation/Verifier | Deterministic checks + LLM judge | standard (`claude-sonnet-5`) | VERIFYING; feeds calibration |
| 12 | Reviewer | LLM | reasoning (`claude-opus-5`) | Reviews plans/PRs before humans see them |
| 13 | Human Approval | **Deterministic service** | fast-tier summarization only | AWAITING_APPROVAL |

**Why Coordinator, Policy, and Human Approval are deterministic services in the roster.** They sit on the authority path: they decide *what runs next*, *what is permitted*, and *who must consent*. A probabilistic component in any of those positions would make the platform's core compliance claims — replayable execution, provable policy enforcement, attributable consent — unfalsifiable. They stay in the roster because they implement the same `run(ctx) -> AgentResult` contract and are budgeted, traced, audited, and evaluated like every other agent. LLMs appear inside them only to *summarize* already-made decisions for humans (fast tier), never to make them.

---

## 2. The common agent contract

### 2.1 Signature and `AgentResult`

Every agent — deterministic or LLM-backed — implements exactly one entrypoint:

```python
class Agent(Protocol):
    name: str                    # stable identifier, e.g. "root_cause"
    output_schema: JsonSchema    # versioned; validated on every return
    def run(self, ctx: WorkflowContext) -> AgentResult: ...

@dataclass(frozen=True)
class AgentResult:
    output: dict                 # typed payload, validated against output_schema
    confidence: float            # calibrated, 0.0–1.0 (see §3)
    citations: list[Citation]    # mandatory for every claim in `output`
    cost: Cost                   # {tokens_in: int, tokens_out: int, tool_calls: int}
    duration: timedelta          # wall clock, measured by the runtime
```

`WorkflowContext` is a read view over typed workflow state (incident record, evidence bundle, prior agent outputs), the per-invocation budget envelope, and a scoped tool handle that reaches external systems only through the MCP connector gateway. Agents cannot see state outside their declared inputs and cannot write anything except their returned `AgentResult` — the workflow engine merges results into workflow state.

### 2.2 Schema-validated outputs

Each agent declares a versioned JSON Schema for `output`. The runtime validates every return; for LLM-backed agents the model gateway additionally enforces structured output at generation time ([07-model-gateway.md](07-model-gateway.md)). On validation failure the runtime re-prompts the model with the validator's error message appended (semantic retry, max 2 — see §4). A payload that still fails after retries is recorded as a failed invocation, never passed downstream as "mostly right"; the Coordinator then applies the degradation ladder ([11-failure-handling.md](11-failure-handling.md)).

- **Why chosen:** downstream consumers (plan compiler, OPA input documents, approval cards) are all typed; one malformed field silently propagating is how advisory systems become untrustworthy. Validation at the contract boundary converts model flakiness into an explicit, retryable, metered failure mode.
- **Alternatives considered:** free-text outputs parsed by consumers (pushes fragility into N consumers); in-process Pydantic-only validation (loses the language-neutral schema registry the audit and eval pipelines need); grammar-constrained decoding alone (prevents syntactic breakage but not semantic violations like an unknown Step Catalog id).
- **Trade-offs:** schemas are a change-management surface — every schema version bump is a coordinated deploy with the workflow definitions that consume it.
- **Operational implications:** schema-validation failure rate per agent per tier is a first-class SLI ([09-observability.md](09-observability.md)); a step change in it is the earliest signal of a model regression after a tier or model-version change.

### 2.3 Token and tool budgets per invocation

Every invocation carries a budget envelope set by the Coordinator from per-agent defaults × severity multipliers: `{max_tokens_in, max_tokens_out, max_tool_calls, deadline}`. The model gateway enforces token ceilings, the connector gateway enforces the tool-call ceiling, and the workflow engine enforces the deadline as an activity timeout. Exhausting a budget is not an error to hide: the agent returns its best partial result flagged `budget_exhausted`, with confidence capped at 0.49 — which by the thresholds in §3 forces human escalation. Budgets are what keep ~2,000 SEV incidents/month at $3–6 of model spend per automated incident ([13-cost-model.md](13-cost-model.md)) instead of unbounded agentic wandering.

### 2.4 Idempotency keys

The workflow engine may re-deliver any activity (worker crash, timeout ambiguity). Every invocation therefore carries `idempotency_key = hash(workflow_id, step_id, input_digest, schema_version)`. The runtime memoizes `AgentResult` by key (Redis, TTL = workflow retention), so a re-delivered invocation returns the recorded result without a second model call. The same key is forwarded on every ToolCall so the connector gateway can deduplicate side-effecting calls ([04-connectivity.md](04-connectivity.md)). Rule: *retries change the attempt counter, never the key; new inputs change the key.* This is what makes "LLM inside a durable workflow" safe — at-least-once delivery collapses to exactly-once observable effect.

---

## 3. The confidence model

Raw model self-estimates are systematically miscalibrated and drift across model versions. Every `AgentResult.confidence` is therefore computed, not reported:

```
confidence = calibrated(model_self_estimate)          # isotonic map, per agent per tier
           × evidence_coverage                        # cited claims / total claims, weighted
           × calibration_weight[agent][failure_class] # learned from Verifier outcomes
```

- **Calibrated self-estimate:** the model's stated probability passed through an isotonic regression map fitted per agent per model tier on eval outcomes, so that "0.9" means 0.9.
- **Evidence coverage:** fraction of atomic claims in `output` carrying at least one citation the retrieval service can re-resolve, weighted by claim materiality (a causal claim weighs more than a descriptive one). Uncited material claims are rejected at validation, so this factor punishes *thin* evidence, not *absent* evidence.
- **Historical calibration weight:** for each (agent, failure class) pair, the Evaluation service compares past stated confidence against verified outcomes — did the diagnosis hold, did the remediation restore baseline ([10-evaluation.md](10-evaluation.md)). Weights update on a rolling window after each `RESOLVED→LEARNED` transition and each offline replay batch; a pair with fewer than 50 verified outcomes is clamped to a conservative prior of 0.75, so novel failure classes cannot self-authorize the auto-path.

Thresholds, enforced by the Coordinator and encoded in Rego so policy can tighten but never loosen them:

| Confidence | Consequence |
|---|---|
| < 0.5 | Escalate to human ("insufficient evidence"); platform continues assisting |
| 0.5 – 0.8 | Human approval required **regardless of risk class** |
| > 0.8 | Eligible for auto-path, still subject to OPA policy and risk class |

- **Why chosen:** a scalar that gates execution must be defensible in a compliance review; "the model felt confident" is not. Multiplicative composition means any weak factor drags the score down — high self-belief cannot compensate for thin evidence.
- **Alternatives considered:** raw self-estimate (miscalibrated, gameable by prompt drift); a learned end-to-end confidence model (opaque, cold-start problem per failure class); evidence coverage alone (ignores reasoning quality on well-cited but wrong causal chains).
- **Trade-offs:** the multiplicative form is conservative by construction; early-life agents over-escalate. We accept deliberate over-escalation as the correct failure direction.
- **Operational implications:** calibration curves per (agent, failure class) are dashboards owned by the platform team; a model-version bump resets isotonic maps to last-known-good until the eval replay harness re-fits them.

---

## 4. Retry policy taxonomy

All retries are executed by the workflow engine per its activity retry policies — never by an agent looping internally, which would be invisible to audit and budgets.

| Class | Trigger examples | Policy | Budget interaction |
|---|---|---|---|
| **Transient** | Connector timeout, 429/503 from source system, worker eviction | Exponential backoff with jitter (1s → 4s → 16s), max 3 attempts | Same idempotency key; tool-call budget not recharged |
| **Semantic** | Output schema validation failure, unknown Step Catalog reference, uncited material claim | Re-prompt same tier with validator error appended, max 2 attempts | Retry tokens draw from the same envelope |
| **Systemic** | Model tier outage, sustained gateway 5xx, tier-wide latency SLO breach | Fall back one tier via the model gateway (frontier→reasoning→standard→fast); output confidence ×0.9 per tier dropped | Fresh attempt, same key |
| **Non-retryable** | OPA policy denial, budget exhaustion, security quarantine of inputs, approval rejection | No retry. Recorded, audited, routed: denial → ESCALATED or plan revision; quarantine → human review | n/a |

Non-retryable is the load-bearing class: a policy denial retried "until it works" is a policy bypass. Denials terminate the attempt and re-enter the FSM at the state the Coordinator maps them to ([03-orchestration.md](03-orchestration.md)).

---

## 5. Agent specifications

Schema sketches are abbreviated; canonical versioned schemas live in the schema registry ([12-apis-and-storage.md](12-apis-and-storage.md)). Memory tiers are those defined in [06-retrieval-and-memory.md](06-retrieval-and-memory.md): working (per-incident), episodic (past incidents), long-term (distilled failure classes, remediation efficacy), organizational (ownership, service graph, runbooks).

### 5.1 Triage Agent

**Mission.** Convert a normalized Signal into a deduplicated, severity-classified, service-attributed incident record fast enough to matter at ~2M alerts/day.

- Deduplicate against open incidents and active alert storms (fingerprint + time window).
- Classify severity (SEV1–SEV4) from deterministic rules over alert metadata and SLO burn.
- Attribute owning service and team via the Neo4j service graph; attach failure-class priors.

```python
class TriageAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {signal: Signal, open_incidents: [IncidentRef], storm_state: StormWindow}
# out: {incident_id, dedup_of: IncidentRef|None, severity: "SEV1".."SEV4",
#       service: {name, team, tier}, failure_class_priors: [{class, p}]}
```

- **Inputs:** normalized Signal from the Sense plane; open-incident index; storm-suppression state. **Outputs:** triage record into workflow state; `incidents.triaged` event.
- **Tools/connectors:** PagerDuty, Datadog/Grafana monitor metadata (read); service-graph query. No writes.
- **Memory:** working (write: incident record); long-term (read: failure-class priors); organizational (read: ownership).
- **Model tier:** deterministic rules first; fast tier only for ambiguous service attribution and free-text alert bodies.
- **Confidence:** rule-path outputs get fixed calibrated scores from rule precision stats; LLM-assisted attribution uses the full §3 formula.
- **Retry:** transient ×3 on connector reads; semantic ×2 on LLM assist; ambiguity after retries → attribute to broadest matching team, flag `attribution_uncertain`.
- **Failure modes / fallbacks:** storm misclassification — suppression heuristics fail open to *create* incidents, never silently drop; stale service graph — attribution from alert tags, confidence capped 0.6.

### 5.2 Planner Agent

**Mission.** Propose a remediation plan for a diagnosed incident, expressed exclusively as references to Step Catalog actions — never free-form actions.

- Select and order Step Catalog actions with typed parameters, preconditions, and expected post-conditions.
- Attach per-step verification probes; rely on each step's registered compensation for reversibility.
- Produce plan-level rationale with citations to the diagnosis and to past remediation efficacy.

```python
class PlannerAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {diagnosis: RootCauseOutput, evidence: EvidenceBundle,
#       catalog_view: [StepSpec], efficacy_priors: [RemediationStat]}
# out: {plan: {steps: [{step_id: CatalogRef, params, depends_on, verify: ProbeRef,
#       expected_effect}], rationale, est_blast_radius}}
```

- **Inputs:** Root Cause diagnosis; evidence bundle; the policy-filtered Step Catalog view for this service/risk context; efficacy priors. **Outputs:** typed Plan, handed to the plan compiler for DAG compilation ([03-orchestration.md](03-orchestration.md)).
- **Tools/connectors:** Step Catalog registry (read); retrieval service. No direct external-system access.
- **Memory:** working (read/write); long-term (read: what fixed this failure class before, and how well); episodic (read: nearest past incidents).
- **Model tier:** reasoning; frontier (`claude-fable-5`) for SEV1 or failure classes with no long-term prior.
- **Confidence:** evidence-coverage factor computed over causal-link citations plus efficacy citations; calibration weight keyed to failure class.
- **Retry:** semantic ×2 — schema failure and *unknown/ineligible Step Catalog reference* both re-prompt with the violation; systemic → tier fallback (never below standard for planning).
- **Failure modes / fallbacks:** no viable plan within the eligible catalog view → returns `no_safe_plan` with reasons → ESCALATED with an evidence dossier; over-broad plan → blast-radius estimate triggers Reviewer plus mandatory approval.

### 5.3 Coordinator Agent

**Mission.** The deterministic, workflow-engine-facing service that sequences agent invocations, enforces budgets and timeouts, and maps agent results onto FSM transitions.

- Own invocation order and fan-out/fan-in of agents per workflow definition; assemble each agent's `WorkflowContext`.
- Enforce budget envelopes, deadlines, retry classes (§4), and confidence thresholds (§3).
- Map results and failures to FSM transitions, gates, and saga compensation triggers.

```python
class CoordinatorAgent(Agent):   # deterministic; no model calls
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {fsm_state, pending_results: [AgentResult], workflow_def: WorkflowDefRef}
# out: {next_invocations: [{agent, context_view, budget, idempotency_key}],
#       transitions: [FsmTransition], gates: [GateRef]}
```

- **Inputs:** workflow state, FSM state, agent results, versioned workflow definitions (data, not runtime-chosen code paths). **Outputs:** scheduling decisions consumed by the workflow engine; transition records.
- **Tools/connectors:** Temporal APIs, Redis (idempotency memo), schema registry. No external estate access.
- **Memory:** working (read/write, mediated through workflow state). No semantic memory.
- **Model tier:** n/a — no model calls at all, not even summarization.
- **Confidence:** always 1.0 by construction; correctness is guaranteed by determinism tests and replay, not estimated.
- **Retry:** transient only (infrastructure-level); logic errors are deploy rollbacks, not retries.
- **Failure modes / fallbacks:** worker crash → Temporal replays deterministically; workflow-definition bug → poison-workflow detection pauses that workflow class cell-wide and pages the platform team.

**Why the Coordinator must not be an LLM.** (1) *Replayability:* Temporal's correctness model requires deterministic decision code — an LLM re-deciding differently on replay corrupts workflow history. (2) *Auditability:* "why did step 7 run?" must have a line-of-code answer for compliance sign-off. (3) *Security:* the sequencer is the highest-leverage prompt-injection target in the system; a model that reads retrieved content *and* schedules writes is exactly the confused deputy [05-security.md](05-security.md) is designed to prevent. (4) *Cost/latency:* sequencing runs on every transition of every workflow, thousands of times per incident. The alternative considered — an LLM "orchestrator agent" as in generic agent frameworks — was rejected on all four axes. Trade-off: new coordination patterns require code changes and deploys rather than prompt edits; we accept that as a feature, because coordination changes get code review. Operationally, the Coordinator is the one agent whose availability SLO matches the workflow engine's.

### 5.4 Root Cause Agent

**Mission.** Generate and rank causal hypotheses over the evidence bundle, producing a diagnosis in which every causal claim carries citations.

- Generate candidate hypotheses across the standard change axes (deploy, config, capacity, dependency, infra, external).
- Rank hypotheses by evidence support and temporal consistency; make disconfirming evidence explicit.
- Emit a causal chain (symptom ← mechanism ← trigger) or an explicit `insufficient_evidence` result.

```python
class RootCauseAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {triage: TriageOutput, evidence: EvidenceBundle,
#       code_intel: CodeIntelOutput|None, cicd: CicdOutput|None}
# out: {hypotheses: [{cause, mechanism, rank, supporting: [CitationRef],
#       contradicting: [CitationRef]}], causal_chain, failure_class}
```

- **Inputs:** triage record; evidence bundle from Knowledge; Code Intelligence and CI/CD findings when available. **Outputs:** ranked diagnosis; assigned failure class (drives calibration weights and memory writes).
- **Tools/connectors:** retrieval service only — it requests evidence; it never queries source systems directly.
- **Memory:** working (read/write); episodic (read: similar past incidents and their verified causes); long-term (read: failure-class mechanisms).
- **Model tier:** reasoning; frontier for SEV1 or when the top two hypotheses rank within 0.1 of each other.
- **Confidence:** strictest evidence-coverage weighting in the fleet — every causal edge must cite; an uncited edge fails schema validation outright.
- **Retry:** semantic ×2; systemic → tier fallback with confidence ×0.9; hypotheses still tied after frontier escalation → `insufficient_evidence`.
- **Failure modes / fallbacks:** plausible-but-wrong narrative — mitigated by Verifier feedback depressing that failure class's calibration weight; evidence gaps → requests one additional targeted retrieval round via the Coordinator, then escalates rather than guessing.

### 5.5 Code Intelligence Agent

**Mission.** Connect the incident to code: change-set analysis, suspect-commit ranking, blame/ownership, and blast-radius estimation over the Neo4j service graph.

- Rank commits/merges in the incident window by suspicion (diff semantics × deploy timing × path sensitivity).
- Resolve blame and ownership for suspect changes across the 10M-repo estate (scoped by service attribution).
- Estimate blast radius of both the suspect change and any proposed revert via service-graph traversal.

```python
class CodeIntelligenceAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {service, window: TimeRange, deploys: [DeployRef], evidence: EvidenceBundle}
# out: {suspect_commits: [{sha, repo, score, why, citations}], owners: [TeamRef],
#       blast_radius: {services: [SvcRef], depth, traffic_weighted_score}}
```

- **Inputs:** service attribution; incident time window; deploy manifests; diff and blame data via connectors. **Outputs:** suspect ranking, ownership, blast-radius estimate — consumed by Root Cause, Planner, CI/CD, Reviewer.
- **Tools/connectors:** GitHub/GitLab/Bitbucket (read), Neo4j service graph (read), CI metadata (read).
- **Memory:** working (read/write); organizational (read: ownership graph, dependency edges); episodic (read: commits previously implicated in this failure class).
- **Model tier:** standard; diff volume beyond budget triggers deterministic pre-filtering (path/deploy heuristics), not tier escalation.
- **Confidence:** per-suspect scores, not just top-level; the coverage factor counts diff-hunk and deploy-log citations per suspect.
- **Retry:** transient ×3 on repo connectors; semantic ×2; repo unreachable → rank from deploy metadata alone, confidence capped 0.6.
- **Failure modes / fallbacks:** monorepo diff floods → build-graph-scoped diffing; stale service-graph edges → blast radius flagged `graph_staleness > SLO`, which Policy treats as a risk-class escalation.

### 5.6 CI/CD Agent

**Mission.** Pipeline forensics and safe rollback-target selection; operate deploy gating as part of Change Intelligence.

- Reconstruct the pipeline timeline (builds, artifacts, promotions, flags) around the incident window.
- Select rollback targets: last verified-good artifact, checked for schema/config compatibility.
- Gate in-flight deploys to affected services during active incidents (Step Catalog action, policy-checked).

```python
class CicdAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {service, window: TimeRange, suspects: [CommitRef]|None}
# out: {timeline: [PipelineEvent], rollback_target: {artifact, verified_at,
#       compat_checks: [Check]}, gate_recommendations: [CatalogRef]}
```

- **Inputs:** service, window, suspect commits from Code Intelligence. **Outputs:** pipeline forensics into evidence; rollback target for Planner; gate recommendations.
- **Tools/connectors:** CI systems, ArgoCD, artifact registries (read); deploy-gate and rollback verbs exist only as Step Catalog references it recommends — it executes nothing itself.
- **Memory:** working (read/write); episodic (read: past rollbacks of this service and their outcomes).
- **Model tier:** standard.
- **Confidence:** dominated by compatibility-check coverage — a rollback target without migration/config compatibility citations cannot exceed 0.7.
- **Retry:** transient ×3; semantic ×2; CI history gaps → widen the window once, then report a partial timeline flagged as such.
- **Failure modes / fallbacks:** no verified-good artifact in window → recommend a fix-forward path to Planner instead of rollback; artifact registry diverged from Git state → flag and escalate, never guess a target.

### 5.7 Infrastructure Agent

**Mission.** Inspect Kubernetes and cloud state, and parameterize infra Step Catalog actions (scale, rollback, failover) — always dry-run-first.

- Snapshot relevant K8s/cloud state (workloads, events, quotas, HPA state, node pressure) as cited evidence.
- Parameterize infra actions from the Step Catalog with computed safe bounds (e.g., max scale step, PDB-aware).
- Execute the dry-run variant of every action first; attach the dry-run diff to the real action's approval context.

```python
class InfrastructureAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {service, clusters: [ClusterRef], intent: CatalogRef|None}
# out: {state_findings: [{finding, citations}], action_params: {step_id, params,
#       dry_run_result: Diff, safe_bounds}, anomalies: [Anomaly]}
```

- **Inputs:** service/cluster scope; optionally a Planner-selected step to parameterize. **Outputs:** cited state findings; fully parameterized, dry-run-validated action specs for the workflow engine to execute.
- **Tools/connectors:** K8s API (read + typed Step Catalog writes), AWS/Azure/GCP (read + typed writes), Terraform state (read). Writes flow only through the connector gateway under saga compensation.
- **Memory:** working (read/write); episodic (read: prior infra interventions on this service).
- **Model tier:** standard; the dry-run and parameter-bounds logic is deterministic code — the LLM interprets state and drafts findings.
- **Confidence:** dry-run success is a hard multiplier — a failed or skipped dry-run forces confidence to 0, so the action cannot proceed on any path.
- **Retry:** transient ×3 on reads; **no automatic retry of writes** — a failed write triggers the step's compensation handler and returns control to the workflow engine.
- **Failure modes / fallbacks:** dry-run/real divergence (admission webhooks, quota races) → compensation + escalation; partial multi-cluster visibility → findings scoped and labeled per cluster, never extrapolated.

### 5.8 Knowledge Agent

**Mission.** Retrieval orchestration: plan federated queries across connectors, rerank and deduplicate results, and recall similar past incidents into the evidence bundle.

- Compile an incident-specific query plan across telemetry, code, ticket, chat, and doc connectors (petabyte telemetry stays federated in source systems).
- Rerank, deduplicate, and classify retrieved excerpts into `Evidence` records with citations.
- Recall nearest past incidents (vector: pgvector→Qdrant; graph: Neo4j) with verified-cause annotations.

```python
class KnowledgeAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {triage: TriageOutput, questions: [EvidenceRequest], budget: Budget}
# out: {evidence: EvidenceBundle, similar_incidents: [{episode_id, similarity,
#       verified_cause}], coverage_report: {answered, unanswered}}
```

- **Inputs:** triage record; evidence requests from the Coordinator on behalf of downstream agents. **Outputs:** the evidence bundle every other agent consumes; an explicit coverage report of unanswered questions.
- **Tools/connectors:** the widest read scope in the fleet — all read connectors via the retrieval service; Qdrant, Neo4j, Postgres. Zero write verbs.
- **Memory:** working (write: evidence bundle); episodic (read); long-term (read); organizational (read).
- **Model tier:** fast for query expansion/decomposition; standard for reranking fusion and evidence classification.
- **Confidence:** reflects retrieval coverage (questions answered / asked, source diversity), not answer correctness — downstream agents own interpretation.
- **Retry:** transient ×3 per connector, isolated per source — one slow connector degrades one evidence stream, not the bundle; semantic ×2 on classification.
- **Failure modes / fallbacks:** source outage → bundle marked `sources_degraded`, which lowers downstream evidence-coverage factors mechanically; retrieved-content injection risk → every excerpt passes Security screening before entering the bundle (§5.10).

### 5.9 Policy Agent

**Mission.** Deterministic OPA/Rego evaluation of every proposed plan, step, and auto-path decision; the LLM renders human-readable explanations of decisions only.

- Evaluate plans and steps against Rego bundles: risk class × environment × confidence × actor scope → allow / deny / require-approval-tier.
- Emit machine-readable decision records with rule provenance into the audit ledger.
- Render fast-tier natural-language explanations of decisions for approval cards and escalations.

```python
class PolicyAgent(Agent):    # decision path is deterministic OPA evaluation
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {subject: Plan|Step, context: {env, risk_class, confidence, actor}}
# out: {decision: "allow"|"deny"|"require_approval", tier: int|None,
#       matched_rules: [RegoRuleRef], explanation_text: str}
```

- **Inputs:** plan or step under evaluation; full decision context from workflow state. **Outputs:** binding decision + tier; explanation text (advisory, labeled as a rendering of the decision, never its source).
- **Tools/connectors:** OPA sidecars, Rego bundle registry, audit ledger append. No estate access.
- **Memory:** working (read: decision context; write: decision record). No semantic memory.
- **Model tier:** fast-tier summarization only; the decision path makes zero model calls.
- **Confidence:** 1.0 on the decision (deterministic); explanation text carries no confidence — it is not a claim, and it cites the matched rules.
- **Retry:** transient ×3 on the OPA sidecar; an unreachable policy engine is fail-closed — no decision means deny.
- **Failure modes / fallbacks:** stale Rego bundle → bundle-version pinning per workflow run detects skew and fails closed; explanation-rendering failure → ship the raw rule trace, never block the decision on the summarizer.

### 5.10 Security Agent

**Mission.** Screen everything that moves: secrets/PII in evidence and generated artifacts, prompt injection in retrieved content, and risk assessment of proposed actions.

- Scan evidence excerpts and generated artifacts (PR bodies, plans, summaries) for secrets and PII; redact or quarantine.
- Screen retrieved content for prompt-injection patterns before it enters any model context.
- Produce action-risk assessments (scope, reversibility, data sensitivity) as OPA input.

```python
class SecurityAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {payloads: [Evidence|Artifact], actions: [StepRef]|None}
# out: {verdicts: [{ref, verdict: "clean"|"redacted"|"quarantined", findings}],
#       action_risk: [{step_id, risk_factors, recommended_class}]}
```

- **Inputs:** every evidence excerpt pre-bundle; every generated artifact pre-publication; proposed steps pre-policy-check. **Outputs:** verdicts and redacted payloads; risk assessments feeding the Policy Agent's input document.
- **Tools/connectors:** Vault (secret fingerprint sets, read-only), classifier services, audit ledger. No estate access.
- **Memory:** working (read/write: verdicts). Deliberately no episodic/long-term read — its context must not be steerable by the retrieved content it is judging.
- **Model tier:** deterministic scanners (entropy, fingerprints, DLP rules) first; fast tier for injection screening; standard for action-risk assessments.
- **Confidence:** asymmetric by design — "quarantine" verdicts execute at any confidence; "clean" below 0.8 is re-screened on standard tier before release.
- **Retry:** semantic ×2; systemic → tier fallback for screening; scanner infrastructure failure is fail-closed — content quarantined, workflow degrades per [11-failure-handling.md](11-failure-handling.md).
- **Failure modes / fallbacks:** false-positive redaction storms → quarantine review queue with human release, never auto-release; a novel injection technique passing screening is contained by defense in depth — agents never execute instructions found in evidence; only the workflow engine schedules actions.

### 5.11 Evaluation/Verifier Agent

**Mission.** Verify remediation against baseline metrics after execution, watch for regressions, and feed verified outcomes into the calibration system.

- Compare post-action metrics against the pre-incident baseline captured at triage (deterministic checks: restoration, stability window, side-effect probes).
- Run a regression watch for a failure-class-specific window; trigger rollback via the workflow engine on regression.
- Emit verified-outcome records that update calibration weights and remediation-efficacy priors ([10-evaluation.md](10-evaluation.md)).

```python
class VerifierAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {baseline: MetricSnapshot, plan: ExecutedPlan, watch: WatchSpec}
# out: {verdict: "restored"|"partial"|"regressed", checks: [{metric, baseline,
#       observed, pass}], outcome_record: VerifiedOutcome}
```

- **Inputs:** baseline snapshot; executed plan with expected effects; watch spec. **Outputs:** verdict driving `VERIFYING → RESOLVED` or `→ ROLLING_BACK`; the outcome record that closes the calibration loop for every upstream agent.
- **Tools/connectors:** Datadog/Grafana/Prometheus (read); eval service APIs.
- **Memory:** working (read); episodic (**write**: the incident episode); long-term (**write**: failure-class and efficacy updates) — the roster's semantic-memory writer.
- **Model tier:** standard — for interpreting ambiguous partial recoveries and drafting the verification narrative; the pass/fail checks are deterministic.
- **Confidence:** grounded in check coverage — the fraction of the plan's `expected_effect` claims actually probed; unprobed effects cap the verdict at "partial".
- **Retry:** transient ×3 on metric reads; verdict computation is deterministic and not semantically retried.
- **Failure modes / fallbacks:** metrics lag masking a regression → minimum stability window per failure class before RESOLVED; baseline captured mid-degradation → sanity check against the 7-day seasonal norm, else verdict capped at "partial" and a human confirms resolution.

### 5.12 Reviewer Agent

**Mission.** Review generated PRs and plans the way a senior engineer would, before any human sees them — a quality gate, not an authority gate.

- Review generated revert/fix-forward PRs for correctness, scope creep, migration hazards, and ownership/style conventions.
- Review plans for step-ordering hazards, missing verification probes, and blast-radius understatement.
- Produce a structured review (blocking findings vs. advisory notes) attached to the approval card.

```python
class ReviewerAgent(Agent):
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {artifact: GeneratedPR|Plan, diagnosis: RootCauseOutput, conventions: OrgStandardsRef}
# out: {verdict: "approve"|"request_changes", findings: [{severity: "blocking"|
#       "advisory", location, issue, citations}], summary}
```

- **Inputs:** generated artifact; the diagnosis it claims to address; organizational conventions and ownership context. **Outputs:** structured review; `request_changes` routes back to Planner/Code Intelligence for one revision cycle before human escalation.
- **Tools/connectors:** GitHub/GitLab (read: diff, checks, ownership files); retrieval service.
- **Memory:** working (read/write); organizational (read: standards, past review outcomes); episodic (read: whether similar past PRs caused regressions).
- **Model tier:** reasoning — review quality is the point; a cheap reviewer is negative value because it launders bad artifacts with an "approved" label.
- **Confidence:** scored per finding and for the overall verdict; a low-confidence "approve" (< 0.8) is treated by the Coordinator as `request_changes`.
- **Retry:** semantic ×2; systemic tier fallback is *not* permitted below reasoning — on reasoning-tier outage the review gate degrades to mandatory human review instead.
- **Failure modes / fallbacks:** rubber-stamping drift → eval-set audits of review recall ([10-evaluation.md](10-evaluation.md)); revision ping-pong → hard cap of one automated revision cycle, then human.

### 5.13 Human Approval Agent

**Mission.** Deterministic routing of approval requests to the right humans with the right evidence, on time — tiering, escalation chains, timeouts, and quorum for CRITICAL actions.

- Resolve the approval tier (from Policy) to concrete approvers: on-call chains, service owners, org escalation paths.
- Enforce timeouts with escalation (e.g., tier 2: 10 min → next in chain; 30 min → engineering manager on-call) and quorum rules (CRITICAL risk class: two independent approvers from disjoint teams).
- Assemble the approval card: decision, plan, dry-run diffs, Reviewer verdict, citations — with a fast-tier evidence summary clearly labeled as generated.

```python
class HumanApprovalAgent(Agent):   # routing/quorum/timeout logic is deterministic
    def run(self, ctx: WorkflowContext) -> AgentResult: ...
# in:  {gate: GateRef, tier: int, plan: Plan, review: ReviewerOutput,
#       policy_decision: PolicyOutput}
# out: {routing: {approvers, quorum, timeout_chain}, card: ApprovalCard,
#       resolution: "approved"|"denied"|"timed_out"|None}
```

- **Inputs:** gate reference from the workflow engine; policy tier; the full decision dossier. **Outputs:** routing plan; posted approval card (Slack/Teams surfaces); signed resolution recorded to the audit ledger and returned to the gate.
- **Tools/connectors:** Slack/Teams apps, PagerDuty schedules (read), identity provider (read), audit ledger (append).
- **Memory:** working (read: dossier; write: resolution record). No semantic memory.
- **Model tier:** fast-tier summarization only, for the card's evidence summary; routing, quorum, and timeout logic make no model calls. Deterministic because consent is a legal artifact: *who could approve, who did approve, and when* must be exactly reproducible.
- **Confidence:** n/a on routing (deterministic); the card summary carries the source agents' confidences and does not mint its own.
- **Retry:** transient ×3 on messaging connectors with channel fallback (Slack → Teams → email → phone via PagerDuty); resolutions are idempotent by gate id, so double-taps collapse.
- **Failure modes / fallbacks:** approver unreachable → timeout chain, terminal fallback deny-by-timeout (fail closed) with incident-channel notification; stale on-call schedule → route to team channel and owner-of-record simultaneously; summary-generation failure → post the card with raw dossier links, never delay the gate on the summarizer.

---

## 6. Agent interaction matrix

All flows are mediated by the workflow engine through typed workflow state — ✓ means "consumer reads producer's output from workflow state", never a direct call. The Coordinator is omitted as a column: it consumes every result by definition.

| Producer ▼ / Consumer ► | Tri | Pln | RC | CInt | CI/CD | Infra | Know | Pol | Sec | Ver | Rev | HAp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Triage | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ |
| Planner | · | — | · | · | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | ✓ |
| Root Cause | · | ✓ | — | · | · | · | · | ✓ | · | ✓ | ✓ | ✓ |
| Code Intelligence | · | ✓ | ✓ | — | ✓ | · | · | ✓ | · | · | ✓ | ✓ |
| CI/CD | · | ✓ | ✓ | · | — | · | · | ✓ | · | ✓ | · | ✓ |
| Infrastructure | · | ✓ | ✓ | · | · | — | · | ✓ | · | ✓ | · | ✓ |
| Knowledge | · | ✓ | ✓ | ✓ | ✓ | ✓ | — | · | ✓ | · | ✓ | ✓ |
| Policy | · | ✓ | · | · | · | · | · | — | · | · | · | ✓ |
| Security | · | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | · | ✓ | ✓ |
| Evaluation/Verifier | · | ✓ | ✓ | · | · | · | ✓ | · | · | — | ✓ | · |
| Reviewer | · | ✓ | · | ✓ | · | · | · | · | · | · | — | ✓ |
| Human Approval | · | · | · | · | · | · | · | · | · | ✓ | · | — |

Reading notes: Knowledge → Security is the mandatory screening path — no evidence reaches any other consumer unscreened, so Security's row is effectively a filter on Knowledge's. Verifier's outputs to Planner, Root Cause, and Reviewer are asynchronous: calibration weights and efficacy priors are consumed on *future* incidents, closing the organizational-learning loop. Human Approval → Verifier carries the approved-plan context that scopes the verification watch. Policy → Planner is the eligible Step Catalog view: deny rules pre-filter the verbs Planner may reference.

---

## 7. Why 13 specialized agents instead of one general agent

**Why chosen.** Four compounding reasons:

1. **Context isolation.** Each agent sees only its declared slice of workflow state. The Security Agent judges retrieved content without that content being able to steer a planner; the Planner sees a policy-filtered Step Catalog view, not raw connector access. A single general agent would hold the union of all context — the maximal prompt-injection and cross-contamination surface — in every model call.
2. **Least-privilege tool scoping.** Tool grants are per-agent at the connector gateway: Knowledge gets wide reads and zero writes; Infrastructure gets typed infra verbs and no repo access; Policy and Human Approval touch no external estate at all. One general agent needs the union of all grants on every invocation, which no security review of a 50,000-engineer estate would pass — and rightly so ([05-security.md](05-security.md)).
3. **Independent eval and calibration.** Confidence calibration (§3) is per agent per failure class. With 13 narrow contracts, the Evaluation service can attribute an outcome ("diagnosis right, plan wrong") to the responsible agent, build golden sets per contract, and regress-test a model-tier change against one agent at a time. A monolithic agent yields one entangled score that cannot localize failures.
4. **Model-tier economics.** Roughly 80% of invocation volume (triage assists, retrieval planning, screening, summaries) runs on fast/standard tiers; reasoning and frontier tiers are reserved for diagnosis, planning, and review. A general agent must run every call on the tier its hardest sub-task needs — at ~2M alerts/day and ~2,000 SEV incidents/month, that difference decides whether the cost model in [13-cost-model.md](13-cost-model.md) holds.

**Alternatives considered.** (a) One general agent with a large tool belt — rejected on all four grounds above; its control loop is exactly the generic-agent-framework pattern the platform exists to avoid. (b) Three coarse agents (diagnose / act / govern) — better, but still entangles tool scopes and blurs calibration attribution across sub-tasks. (c) Dynamic agent spawning per incident — maximal flexibility, but unbounded tool-scope combinations defeat static policy review; every scope set must be enumerable at audit time.

**Trade-offs.** More agents means more contracts, schemas, golden sets, and dashboards to maintain; cross-cutting improvements (a better citation style, say) fan out across 13 prompts and schemas. Handoff latency adds up — mitigated by the Coordinator fanning out independent agents (Knowledge, Code Intelligence, CI/CD, Infrastructure run concurrently during INVESTIGATING) rather than chaining them.

**Operational implications.** Each agent versions and deploys independently behind its own eval gate; a regression in one agent degrades one capability, not the platform. On-call runbooks are per-agent. The roster is closed by design: adding agent #14 is an architecture-review event — a new tool-scope grant, a new calibration namespace, a Step Catalog and policy review — not a config change. That friction is intentional; it is the same reasoning that keeps the Step Catalog the only verb set the platform can execute.
