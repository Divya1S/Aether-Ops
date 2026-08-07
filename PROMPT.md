# Refined Prompt — AetherOps Platform Design (optimized for Claude Fable 5)

This file is the deliverable for "refine this prompt to work best for Fable 5."
The original prompt was a flat list of ~140 requirements. The refinement below
restructures it around how a frontier agentic model actually does its best
work. Use the fenced prompt at the bottom verbatim.

## What changed and why

| Change | Why it produces better output from Fable 5 |
|---|---|
| **One concrete product decision up front** ("invent X, name it, commit to it") instead of an open constraint list | Frontier models produce deeper work when forced to commit early to a single load-bearing concept; otherwise requirement coverage becomes checkbox prose. |
| **Canon-first ordering**: write the executive summary + architecture first, everything else must inherit that terminology | Prevents drift and self-contradiction across a 15-document set — the largest failure mode of long generations. |
| **Decision-record format** (Why / Alternatives / Trade-offs / Operational implications) required per decision | Converts "avoid simplistic explanations" from a vibe into a checkable output contract. |
| **Staged execution plan with tool use** (write canon → fan out sections → build code → run and verify) | Fable 5 is agentic; telling it *how to sequence the work* (including parallel subagents and actually executing the code) beats asking for one monolithic essay. |
| **Runnable reference implementation required, zero-dependency** | "Start implementation" is otherwise interpreted as pseudocode. Forcing `python3 -m unittest` + a runnable demo makes the design falsifiable. |
| **Honesty rules for numbers** (label assumptions, show the math) | Blocks the classic failure of confident fabricated ROI figures. |
| **Explicit non-goals and anti-patterns** kept from the original, but moved next to the mission | Constraints adjacent to the mission are respected; constraints buried at position 90 of a list are not. |
| **Per-document line budgets and file paths** | Bounds output, prevents both padding and truncation, makes the result reviewable as a repo instead of a scroll. |

## The refined prompt

```text
ROLE
You are a Principal Engineer designing a production platform for a Fortune 100
engineering organization (50,000 engineers). Write and build like an internal
Staff+ design review is your audience.

MISSION
Invent ONE original AI engineering-automation platform, commit to it by name,
and deliver (a) a complete internal design-document set and (b) a runnable
reference implementation of its core vertical slice, in this repository.

The platform must AUTOMATE difficult engineering work end-to-end — detect,
investigate, decide, act (governed and reversibly), verify, and learn — not
answer questions. Target workflow: production incident remediation and change
risk, or an equally consequential workflow you can defend.

NON-GOALS (reject these shapes explicitly in the design)
Chatbot, coding assistant, documentation bot, search engine, generic RAG app,
thin wrapper on a model API, single autonomous agent with an LLM-planned loop.

OPERATING RULES
1. Canon first. Write docs/00 (executive summary, problem, why existing tools
   fail) and docs/01 (architecture, diagrams, canonical terminology table)
   before anything else. Every later document and all code must reuse that
   terminology exactly.
2. Decision records. Every major architectural choice states: Why chosen,
   Alternatives considered, Trade-offs, Operational implications.
3. Determinism boundary. LLMs may generate hypotheses, rank evidence, and
   propose plans expressed only as references to a vetted, typed action
   catalog. Execution flow, policy decisions, budgets, and state transitions
   are deterministic software. State this boundary and enforce it in code.
4. Evidence or silence. Every system claim/recommendation carries citations to
   concrete artifacts (commits, metrics, events, tickets). Design an explicit
   "insufficient evidence → human escalation" path. No uncited causal claims.
5. Honest numbers. Every cost/ROI/scale figure is either derived (show the
   arithmetic) or labeled as a planning assumption.
6. Enterprise reality. RBAC+ABAC, OPA policy-as-code, secret management, PII
   redaction before hosted models, prompt-injection defense-in-depth, audit
   ledger, approval tiers, SOC2/GDPR/HIPAA posture, multi-region cells.
7. Use your tools. Parallelize independent document sections with subagents
   sharing the canon docs. Write the reference implementation in pure Python
   stdlib (no network, no API keys), then RUN its tests and demo and fix
   what fails before finishing. Report actual command output.

DELIVERABLES (exact file layout)
docs/00-executive-summary.md      docs/08-scalability.md
docs/01-architecture.md           docs/09-observability.md
docs/02-agents.md                 docs/10-evaluation.md
docs/03-orchestration.md          docs/11-failure-handling.md
docs/04-connectivity.md           docs/12-apis-and-storage.md
docs/05-security.md               docs/13-cost-model.md
docs/06-retrieval-and-memory.md   docs/14-risks-and-roadmap.md
docs/07-model-gateway.md          docs/15-portfolio.md
src/<platform>/  — typed core, deterministic DAG executor with retries,
  checkpoints, compensation (saga), approval gates; agent base with
  confidence scoring; policy engine; connector gateway abstraction with
  auth/cache/rate-limit/audit/redaction; model gateway with tier routing;
  episodic memory; one end-to-end workflow wired to faked connectors.
tests/ — stdlib unittest covering DAG semantics, policy, redaction, audit
  chain, and the end-to-end workflow.  README.md, Makefile, pyproject.toml.

CONTENT REQUIREMENTS (distribute across the docs; 220–500 lines each)
- Multi-agent architecture: 12+ specialized agents (triage, planner,
  coordinator, root cause, code intelligence, CI/CD, infrastructure,
  knowledge, policy, security, evaluation/verifier, reviewer, human
  approval), each with mission, API, inputs/outputs, tools, memory access,
  model tier, confidence scoring, retry policy, failure modes. Explain why
  specialized agents beat one general agent, and which "agents" should be
  deterministic services.
- Orchestration: why deterministic orchestration over autonomous planning;
  plan compilation; DAG semantics; state machines; event-driven triggers;
  scheduling; retries; rollback/compensation; checkpoints; an explicit
  LLM-vs-deterministic decision table.
- Connectivity: MCP-based connector gateway covering GitHub, GitLab,
  Bitbucket, Jira, Linear, Slack, Teams, Confluence, Notion, Datadog,
  Grafana, Prometheus, PagerDuty, Splunk, Elastic, AWS, Azure, GCP,
  Kubernetes, ArgoCD, Terraform, CircleCI, GitHub Actions, Buildkite,
  Jenkins — with authN/Z, caching, rate limiting, sandboxing.
- Security, retrieval+memory (working/episodic/long-term/organizational,
  vector + knowledge graph, TTLs), model routing (small/large/reasoning/
  embedding/reranker/local, with fallback chains), scalability (50k
  engineers, 10M repos, storm-mode backpressure, cells, DR), observability
  (OTel, token analytics, cost dashboards), evaluation (golden datasets,
  replay, LLM-as-judge, trust ladder, hours-saved methodology), failure
  handling (taxonomy → detection → degraded mode → escalation), APIs and
  storage schemas, cost model, risks, phased roadmap with exit criteria,
  and a portfolio/interview-preparation document.

QUALITY BAR (self-check before finishing)
[ ] A Staff engineer could find the answer to "why not X?" for every major
    decision without asking.
[ ] No claim of production deployment; reference implementation clearly
    scoped as a vertical slice.
[ ] Tests and demo actually executed; output shown; failures fixed.
[ ] No terminology drift across documents; no orphan concepts.
[ ] Every number is derived or labeled an assumption.
```
