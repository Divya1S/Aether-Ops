# 19 — Design decisions & rationale

Why the system is built the way it is — the load-bearing choices, what each one
trades away, and how it is verified. Reads as an index into docs/00–18.

## Thesis

Incident remediation is a **safety-critical control problem wearing an AI hat**.
The hard part is not generating a plausible fix — an LLM does that in one call —
it is *never executing a wrong one*. So the architecture puts a deterministic
control plane in charge and gives the model bounded authority: it may *decide
what to investigate* and *propose* a remediation, but gates, policy, execution,
and audit stay deterministic. Autonomy of investigation and proposal, never of
execution.

## Decisions

### 1. Deterministic control plane; agents inside it
**Decision.** A DAG workflow engine sequences specialized agents (triage,
knowledge, security, root-cause, planner, reviewer, verifier); the model is
called only at well-defined nodes.
**Why.** Determinism is what makes runs replayable, auditable, and testable. An
agent free-for-all is neither. **Tradeoff.** Less "magical" than a single
autonomous agent; the control flow is explicit code, not emergent.
**Verified.** Golden-scenario replay is byte-stable; every node's output is
schema-checked at the workflow layer.

### 2. Grounded, or it escalates
**Decision.** Every causal claim must cite a real evidence artifact; the failure
class is inferred deterministically from evidence markers (never a model/injected
substring), and an **independent Reviewer** re-checks *temporal precedence* (a
suspect deploy must pre-date symptom onset) and *mechanism consistency* (a memory
regression's commit must *raise* a resource, not lower it) against its own
connector reads. No correlating change → "insufficient evidence" → escalate.
**Why.** A confidently-wrong remediation is worse than a page to a human.
**Tradeoff.** The system refuses some incidents it might have guessed right.
**Verified.** Three **adversarial** golden scenarios must *escalate*, not
remediate (a pool *reduction* coincident with an OOM; a deploy *after* onset; a
memory regression with no pool signature). Reverting a grounding check flips one
to "remediated" and turns CI red — the test has teeth.

### 3. Bounded agentic loop
**Decision.** During investigation the model chooses the next read-only tool from
a menu (or "finish"), capped at 8 steps, duplicate calls rejected, malformed
decisions degraded to a deterministic baseline; every decision audited as a
replayable trajectory.
**Why.** Real agency where it is cheap (read-only evidence gathering) with hard
stops so a loop can't run away or spend unbounded tokens. **Tradeoff.** A cap can
cut off a genuinely long investigation (then it escalates). **Verified.** A test
hijacks the loop into demanding a rollback and proves it cannot reach a write.

### 4. The model proposes, the catalog disposes
**Decision.** The planner emits a JSON plan; a compile step enforces catalog
membership, required args, and tool availability, with a deterministic fallback
plan. Execution is single-attempt and **self-compensating** (saga undo for
MEDIUM+ writes); a rollback to a known-good revision is a **safe terminal
state**, never auto-undone.
**Why.** The model decides the remediation; the catalog decides what is
physically possible; compensation makes partial failure recoverable.
**Tradeoff.** The catalog must be curated — the system can only do what it is
taught. **Verified.** Concurrent-approval test proves fencing tokens + per-
incident locks make double-execution impossible.

### 5. One gateway for every model call, with a fallback chain
**Decision.** All model calls route through a gateway that picks a tier
deterministically and serves through an ordered chain: **Anthropic API → local
Ollama → deterministic offline heuristic**, metering latency/tokens/cost and
auditing every call (and every fallback).
**Why.** A single choke point for cost, observability, routing, and resilience —
the platform "cannot lose its brain" if a backend dies mid-run. **Tradeoff.** The
offline heuristic is weaker than a real model (acceptable: it's the floor, not
the target). **Verified.** Tests pin the offline backend so replay never depends
on what's installed; a fallback mid-run completes the workflow with the switch in
the ledger.

### 6. Evaluation is the product, not an afterthought
**Decision.** Golden scenarios (incl. adversarial), retrieval scored with
**bootstrap 95% CIs** and gated on the interval's *lower bound*, an LLM-as-judge
whose faithfulness score is *overridden* by a deterministic citation anchor, a
**trust ladder** (a single-episode class stays advisory-only), all as CI gates.
**Why.** "It worked when I tried it" is not evidence; a metric that can't fail is
theater. **Tradeoff.** Small n (7 self-authored scenarios) — stated honestly, and
the trust ladder encodes that humility. **Verified.** `make eval` fails below the
gate; the judge's blind spots are demonstrated by `make judge-live`.

### 7. Security runs before the model
**Decision.** Evidence above the model's clearance (Slack is CONFIDENTIAL by
default) is withheld from prompts; write-risk tools are callable only by the
executor principal; API tokens carry roles; controls mapped to the **OWASP LLM
Top 10** with LLM01/LLM06 attack tests; a **hash-chained audit ledger** makes the
trail tamper-evident.
**Why.** A compromised or prompt-injected agent must be *physically* unable to
exfiltrate or execute. **Tradeoff.** Redaction can hide context that would help a
benign diagnosis (the audit trail keeps human visibility). **Verified.** Injection
tests; the executor-only guard is unit-tested; audit chains re-verify after
restart.

### 8. Zero-dependency stdlib core; frameworks as optional extras
**Decision.** The core is pure Python stdlib (no network, no keys, byte-stable
CI). Framework interop — LangGraph, FastAPI, ChromaDB/pgvector, LangSmith,
LoRA/PEFT — ships as opt-in extras the core never imports.
**Why.** The stdlib core is the platform's testable, portable identity; the extras
prove the same architecture maps onto the industry stacks without taking on their
weight or flakiness in CI. **Tradeoff.** Two expressions of some seams to
maintain. **Note.** RAGAS was *not* shipped: its release pins `langchain<1`, which
conflicts with the LangGraph extra — so LangSmith serves the eval/observability
slot, and RAGAS-style faithfulness is already native (the LLM-judge + citation
anchor). Choosing not to ship a broken dependency is itself the decision.

## Honest scope & limitations

- **It is a reference implementation, not a fleet in production.** Connectors are
  realistic fakes by default; *real* GitHub and Prometheus HTTP adapters ship and
  are opt-in (dry-run writes), unit-tested against mocked HTTP.
- **Metrics are from golden scenarios and a self-labeled retrieval set (n small),
  not live traffic.** They are stated with their sample size and CIs, not inflated.
- **No GPU here:** the fine-tuning pipeline's dataset + contract are CI-verified
  and a CPU smoke-train emits a real adapter; a *quality* adapter needs a GPU run
  (docs/18 has the recipe).

## If I productionized it next

Persist workflow state in a real store with idempotency keys; move the agentic
loop to a durable executor (Temporal-style) so a node can be cancelled rather than
just timed-out; expand the catalog + connectors with per-action arg schemas and
OPA policy; grow the eval set with real incident replays and human adjudication;
serve structured-output tasks from the fine-tuned adapter behind the gateway with
the same eval gate.
