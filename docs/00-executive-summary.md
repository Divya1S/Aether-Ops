# 00 — Executive Summary, Problem Statement, and Why Existing Tools Fail

**Document set:** AetherOps Platform Design (docs 00–15)
**Audience:** VP Engineering, Principal/Staff engineers, Security & Compliance
**Status:** Proposed — reference implementation in `src/aetherops/`

---

## 1. Executive summary

AetherOps is an **autonomous incident remediation and change-intelligence platform**. It closes the loop that today is closed by tired humans at 3 a.m.:

```
alert → triage → evidence-grounded root cause → policied, reversible remediation
      → verification → organizational learning
```

It is not a chatbot, not a coding assistant, and not a RAG search box. It is an
**execution platform**: a deterministic workflow engine that drives a fleet of
specialized agents against production systems through a governed connector
gateway, with every action typed, policy-checked, approved where required,
reversible, and audit-logged.

Three design commitments distinguish it from every "AI ops assistant" on the market:

1. **Deterministic control plane, probabilistic advisors.** LLMs generate
   hypotheses, rank evidence, and draft plans. They never control execution
   flow. Plans are compiled into typed DAGs of vetted steps from a Step
   Catalog and executed by a durable workflow engine (Temporal) with retries,
   checkpoints, compensation, and approval gates. (See [03-orchestration.md](03-orchestration.md).)

2. **Evidence or silence.** Every claim the system makes — "this deploy caused
   the regression" — must carry citations to concrete artifacts: commits, CI
   runs, metrics queries, Kubernetes events, past incidents. If evidence
   coverage is below threshold, the system says "insufficient evidence" and
   escalates to a human instead of guessing. (See [06-retrieval-and-memory.md](06-retrieval-and-memory.md).)

3. **Blast-radius-bounded action.** The platform is read-only by default.
   Writes exist only as typed actions in the Step Catalog, each with a risk
   class, an OPA policy, an approval tier, and a compensation (undo) handler.
   (See [05-security.md](05-security.md).)

### Impact model (Fortune 100 assumptions)

| Assumption | Value |
|---|---|
| Engineers | 50,000 |
| Services in production | ~15,000 |
| SEV1–SEV3 incidents / month | ~2,000 |
| Alert volume / day | ~2,000,000 (after monitor noise) |
| Mean engineers paged per incident | 4.2 |
| Mean MTTR today (SEV2) | ~4.1 hours |

Conservative modeled outcomes at Phase 3 maturity (see [13-cost-model.md](13-cost-model.md) for the full model):

- **MTTR:** 4.1 h → ~55 min for the ~60% of incidents matching known failure
  classes (bad deploy, config drift, capacity, dependency degradation).
- **Engineer-hours returned:** ~45,000–60,000 hours/month (investigation,
  war-room attendance, postmortem drafting, repeat-incident rework).
- **Repeat incidents:** −30% via preventive fix PRs and runbook distillation.
- **Unit economics:** ~$3–6 of model + infra spend per automated incident vs.
  ~$2,100 of engineering time per manually handled SEV2.

At loaded cost of ~$110/engineer-hour, the midpoint is **~$5.8M/month of
recovered engineering capacity** — before counting revenue protected by faster
mitigation.

---

## 2. Problem statement

Unplanned operational work is the largest un-automated engineering workflow at
enterprise scale. For a 50,000-engineer organization:

1. **Investigation dominates MTTR.** Industry and internal studies
   consistently attribute 60–75% of incident duration to *diagnosis* — not to
   applying the fix. The fix is usually one of a small set of verbs: roll
   back, revert, scale, failover, flush, flag-flip, restart. Finding *which*
   verb and *which target* requires correlating deploys, commits, metrics,
   logs, K8s events, and tribal knowledge scattered across Slack and heads.

2. **The knowledge never compounds.** Postmortems are written (late), filed,
   and unread. The same failure class recurs in a different team a quarter
   later. There is no mechanism that turns incident N into leverage for
   incident N+1 across a 15,000-service estate.

3. **Change risk is assessed by vibes.** The single biggest incident trigger
   is change (deploys, config, infra). Yet the decision "is this deploy
   risky?" is made by the author, with none of the organization's incident
   history in the loop.

4. **Runbook automation doesn't scale organizationally.** Deterministic
   automation (Rundeck, StackStorm, internal scripts) works only where a
   human pre-authored the exact playbook, and rots the moment the system
   changes. Coverage plateaus at the top 20 alert types.

The workflow to automate is therefore: **triage → investigate → decide →
act → verify → learn**, across heterogeneous enterprise tooling, under
security and compliance constraints that rule out "let an agent loose in prod."

---

## 3. Why existing tools fail

| Category | Examples | What they do | Why they don't close the loop |
|---|---|---|---|
| Observability | Datadog, Grafana, Splunk, Elastic | Detect and visualize | Surface symptoms; a human still does correlation, decision, action. "Watchdog"-style anomaly grouping stops at suggestion. |
| AIOps / alert correlation | Moogsoft, BigPanda, PagerDuty AIOps | Deduplicate and group alerts | Reduce noise at the top of the funnel; contribute nothing to diagnosis or remediation. |
| Runbook automation | Rundeck, StackStorm, Ansible EDA | Execute pre-authored playbooks | No diagnosis. Playbooks are hand-written per alert, rot quickly, and cover the head of the distribution only. |
| Incident management | PagerDuty, incident.io, FireHydrant | Coordinate humans | Optimize the war room, not the work. The investigation is still manual. |
| Coding assistants | Copilot-class tools | Accelerate typing | Wrong layer entirely: they help write code, not operate systems; no telemetry access, no policy model, no execution semantics. |
| "AI SRE" chatbots | various startups | Q&A over telemetry | Advisory-only; answers are uncited and unauditable; no typed action model, no approval workflow, no compensation semantics — so enterprises never grant them write access, so they never save the hours. |
| Generic agent frameworks | LangChain-style autonomous agents | LLM-planned tool loops | Non-deterministic control flow is unauditable and uncertifiable; no compliance story; failure modes (loops, hallucinated tool calls) are unacceptable against production systems. |

The gap is structural, not incremental: nobody has combined **(a)**
evidence-grounded diagnosis across the full enterprise toolchain, **(b)** an
enterprise-grade governance model (RBAC/ABAC, OPA, approvals, audit), and
**(c)** deterministic, reversible execution semantics. Each existing category
has exactly one of the three.

---

## 4. Product pillars

1. **Autonomous Incident Remediation** — the core loop above; SEV-triggered.
2. **Change Intelligence** — pre-deploy risk scoring: every PR/deploy is
   scored against the organization's incident history, ownership graph, and
   blast-radius model; risky changes get gated or canaried automatically.
3. **Organizational Learning** — every resolved incident is distilled into
   structured memory (failure class, causal chain, remediation efficacy) that
   improves both future diagnosis and future change gating.
4. **Preventive Engineering** — the platform opens fix-forward PRs (revert,
   config correction, limit adjustment, missing alert) as *drafts for human
   review*, converting incident learnings into merged prevention.

## 5. Non-goals

- Replacing incident commanders on SEV1s. The platform is the best-informed
  participant in the room, not the commander.
- General-purpose code generation or feature development.
- Replacing existing observability/CI/CD systems — AetherOps federates over
  them via connectors; it does not re-ingest petabytes of telemetry.
- Conversational UX as the primary interface. Slack/Teams surfaces exist for
  approvals and summaries; the product is the workflow, not the conversation.

## 6. Document map

| Doc | Contents |
|---|---|
| [01-architecture.md](01-architecture.md) | System architecture, planes, diagrams, data flow, deployment |
| [02-agents.md](02-agents.md) | The 13-agent roster: contracts, APIs, memory, confidence, retries |
| [03-orchestration.md](03-orchestration.md) | Deterministic orchestration, plan compilation, DAGs, sagas, gates |
| [04-connectivity.md](04-connectivity.md) | MCP connector gateway, auth, caching, rate limiting |
| [05-security.md](05-security.md) | RBAC/ABAC, OPA, redaction, audit, injection defense, compliance |
| [06-retrieval-and-memory.md](06-retrieval-and-memory.md) | Evidence pipeline, citations, memory tiers, knowledge graph |
| [07-model-gateway.md](07-model-gateway.md) | Model tiers, routing, fallbacks, structured output enforcement |
| [08-scalability.md](08-scalability.md) | Cells, partitioning, multi-region, backpressure, DR |
| [09-observability.md](09-observability.md) | OTel tracing, token analytics, cost/latency dashboards |
| [10-evaluation.md](10-evaluation.md) | Golden datasets, replay harness, LLM-as-judge, business metrics |
| [11-failure-handling.md](11-failure-handling.md) | Failure taxonomy, degradation ladders, human escalation |
| [12-apis-and-storage.md](12-apis-and-storage.md) | Public API surface, event topics, storage schemas, retention |
| [13-cost-model.md](13-cost-model.md) | Unit economics, token budgets, infra cost, ROI |
| [14-risks-and-roadmap.md](14-risks-and-roadmap.md) | Top risks with mitigations; phased roadmap with exit criteria |
| [15-portfolio.md](15-portfolio.md) | Portfolio justification, resume bullets, interview prep, tradeoffs |

Reference implementation (vertical slice, pure Python stdlib): `src/aetherops/` — see the repository [README.md](../README.md).
