# AetherOps

[![CI](https://github.com/Divya1S/Aether-Ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Divya1S/Aether-Ops/actions/workflows/ci.yml)
**[Live demo →](https://divya1s.github.io/Aether-Ops/)**

**Autonomous incident remediation & change-intelligence platform** — an
enterprise AI system that closes the loop humans currently close at 3 a.m.:

```
alert → triage → evidence-grounded root cause → policy-gated, reversible
remediation → verification → organizational learning
```

Not a chatbot, not a coding assistant, not a RAG search box. AetherOps is an
**execution platform**: a deterministic workflow engine drives a fleet of
specialized agents against production systems through a governed connector
gateway. Every action is typed, policy-checked, approved where required,
compensable (saga undo), and recorded in a hash-chained audit ledger. Every
claim carries citations to real artifacts — commits, metrics, K8s events,
past incidents — or the system says "insufficient evidence" and escalates.

## Run it (zero dependencies)

```bash
make test         # 141 tests, pure stdlib — no network, no keys
make demo         # canonical SEV2 end-to-end (deterministic offline backend)
make eval         # golden scenarios (n=4) + retrieval quality, dual gates
make demo-live    # same incident, diagnosed by a real local model (free)
```

**Live model mode** is optional and free: install [Ollama](https://ollama.com)
(`brew install ollama`), pull a small model (`ollama pull llama3.2:3b`), and
`make demo-live` serves the agents from it through the gateway's **fallback
chain** — if Ollama is missing or dies mid-run, the workflow completes on the
deterministic offline backend with the switch recorded in the audit ledger.
Every model call is metered (backend, latency, tokens, estimated production
cost) and each run prints its trace. Tests and evals always pin the offline
backend, so golden-scenario replay never depends on what's installed.

**Retrieval (RAG) is measured, not assumed:** agents retrieve runbook and
postmortem guidance through a hybrid (keyword + vector) retriever with
`rag://doc#offset` source attribution; chunking strategy and embedder are
configuration (fixed vs. paragraph, stdlib TF-IDF vs. Ollama embeddings);
and `make eval` scores retrieval against a hand-labeled query set (n=36,
self-labeled, including a deliberately vocabulary-divergent paraphrase subset
that a lexical retriever is *expected* to miss) — precision@1/precision@5/
recall@5/MRR per chunking strategy, with a **bootstrap 95% CI** on
precision@1. CI is gated on the interval's **lower bound**, not the point
estimate, so a regression has to move the whole interval rather than hide in
small-sample noise. A **free local semantic embedder** (Ollama
`nomic-embed-text`) is reported opt-in via `make eval-live` and *never gated*
— it lifts the paraphrase subset's precision@1 from ~0 to ~0.5 (reaching
synonyms like "ran out of RAM" → OOM that lexical retrieval can't), and skips
cleanly when Ollama isn't installed, so the deterministic gate stays
network-free. Golden-scenario metrics are honest about their scale: n=7
self-authored scenarios — two diagnosable failure classes (deploy
regression, certificate expiry), a must-escalate case, and **three
adversarial grounding cases** the pipeline must *escalate* rather than
mis-remediate: a pool *reduction* coincident with an OOM (which cannot be
the cause), a deploy that landed *after* symptom onset, and a memory
regression with no pool signature. These have teeth — reverting a grounding
check flips an adversarial scenario from escalated to remediated and turns
the gate red, which is what separates an evaluation from a self-consistency
fixture. Grounding is falsifiable, not asserted: the failure class is
inferred deterministically from evidence markers (never a model/injected
substring), and the independent Reviewer checks **temporal precedence**
(a suspect deploy must pre-date symptom onset) and **mechanism consistency**
(a memory regression's commit must *raise* a resource, not lower it) against
its own connector reads. `make eval --live` additionally reports (never
gates) real-model behavior on the same set. The trust
ladder demands sample size, not just precision: a correct-but-single-
episode class stays advisory-only. Deterministic metrics can't grade the
*quality of generated prose*, so an **LLM-as-judge** scores each root-cause
hypothesis on causal correctness, grounding, and clarity — but its
subjective scores are never trusted blind: a deterministic citation check
computes ground truth (which `[En]` references actually exist) and
*overrides* the judge on faithfulness, flagging disagreement. The
deterministic anchor gates CI (zero hallucinated citations); the judge's
quality scores are reported. Offline the judge is a reproducible policy;
`make eval --live` runs a real-model judge over the same set, and
**`make judge-live`** shows it directly: a local model grades a faithful and
a fabricated-citation hypothesis, and the deterministic anchor overrides it —
small local models routinely miss the fake `[E42]`/`[E99]` references the
anchor catches.
Each resolved incident's postmortem is ingested back into the store, so
incident N's writeup is retrievable context for incident N+1.

**Outputs are contracts, prompts are artifacts:** every agent declares a
JSON-Schema output contract enforced at the workflow layer (one semantic
retry on violation, then escalation); every prompt is a versioned registry
entry whose sha256 is pinned in a lockfile — editing a template without
bumping its version fails the build — and each model call's `prompt@version`
is recorded in the audit ledger and listed in the generated postmortem. The
security controls are mapped to the OWASP LLM Top 10 (2025) in
[docs/05 §11](docs/05-security.md), with the LLM01/LLM06 attack tests in
the suite.

**Reliability is mechanism, not intention:** every workflow node carries a
wall-clock budget, and because an in-process worker cannot be cancelled, a
timed-out node *escalates* rather than retrying — retrying would race a
second attempt against shared state (audited `node.timeout`); a wired
per-invocation deadline backstops the whole run. Execution is single-attempt
and **self-compensating**: a partially-applied batch undoes itself rather
than being blindly replayed, so no write is double-applied or left
un-undone. The saga can genuinely undo MEDIUM+ writes (a dedicated
`compensator` principal the write-guard authorizes), and a rollback to a
known-good revision is a **safe terminal state** — never auto-undone into
redeploying the bad revision. The API's approval endpoint is atomic —
per-incident locks plus fencing tokens make double-execution of a
remediation impossible (proven by a concurrent-approval test). Incidents
can run asynchronously (`{"async": true}` → 202 + poll).

**Run it as a service — with an operator console:**

```bash
make serve        # authenticated REST API + web UI (stdlib) on :8080
make docker       # build the image; run with a token (see below)
```

The server is **secure by default**: it binds loopback and *refuses to boot*
without a real `AETHEROPS_API_TOKEN` unless you explicitly opt into the
built-in dev token (`AETHEROPS_ALLOW_DEV_TOKEN=1`, which `make serve` sets for
local use), and it will never serve that dev token on a non-loopback
interface. A container that publishes a port must therefore be given a real
token: `docker run --rm -p 8080:8080 -e AETHEROPS_API_TOKEN=choose-a-token
aetherops:latest`.

Open `http://localhost:8080/` and you get a real **operator console** — a
**React 19 + TypeScript** app (`web/`, built with Vite into one
self-contained, CSP-safe file the stdlib API serves; `make web` rebuilds it):
trigger the canonical SEV2 incident **or pick any golden scenario** from the
dropdown — the adversarial ones (a pool *reduction*, a deploy *after* symptom
onset) visibly **escalate** instead of remediating — watch the agent pipeline
and cited evidence render, **approve the rollback at the policy gate**, and
read the generated postmortem — all driving the live endpoints
(`GET /v1/scenarios` lists them). The same app also scores
changes, searches runbooks, and runs the evaluation. The
[public demo]( https://divya1s.github.io/Aether-Ops/) is that exact console
in **recorded mode** (a real transcript captured from the API), so it's
clickable at $0 where no backend runs; served locally it flips to **live
mode** automatically. (The *backend* stays zero-dependency stdlib; only the
frontend has a build.)

`POST /v1/incidents` starts an incident and runs it to the approval gate;
`POST /v1/incidents/{id}/approvals {"decision":"approve"}` replays the exact
gate semantics and returns the generated postmortem;
`GET /v1/incidents/{id}/audit` returns that incident's hash-chained ledger
with its live verification status, so the governance trail is *reachable*,
not just written; `/v1/changes/score`, `/v1/evals`, and
`/v1/runbooks/search` expose the rest. Each request emits a structured
access log (status, role, correlation id = incident id, latency). Every mutating
endpoint requires `Authorization: Bearer $AETHEROPS_API_TOKEN`. State can
persist across restarts: set **`AETHEROPS_DB`** and the API's organizational
memory becomes SQLite-backed, so every incident's learned episode is durable
and shared — the flywheel spans requests and survives a restart (an incident
run today raises the change-risk of a similar deploy tomorrow, across
reboots). The store is WAL + serialized-write safe to share across the API's
worker threads, and the audit append is atomic so the hash chain can't fork
under concurrency (`storage/sqlite.py`). Each incident's audit chain persists
too (JSONL beside the DB) and is reloadable + re-verifiable via
`GET /v1/incidents/{id}/audit` after a restart — the governance trail
outlives the process. Default (no `AETHEROPS_DB`) stays in-memory so demos
and tests are byte-stable. There's also an **MCP server** —
`python3 -m aetherops.mcp` speaks JSON-RPC over stdio so any MCP client
(Claude Code included) can search runbooks and pull eval summaries straight
from the platform.

**A bounded agentic core inside a deterministic control plane:** the model
makes real decisions in two places, under hard budgets. During
investigation, it *chooses* the next evidence query from a read-only tool
menu (or declares "finish" with a reason) — max 8 steps, duplicate calls
rejected, malformed decisions degrade to the deterministic baseline, every
decision audited as a replayable trajectory. During planning, the model
*proposes* the remediation as JSON, and a compile step enforces catalog
membership, required args, and tool availability — with a deterministic
fallback plan and the independent Reviewer as the last line. The model's
self-estimate feeds confidence (validated by the measured calibration
metric). A test hijacks the loop into demanding a rollback and proves it
cannot reach a write. Gates, policy, execution, and audit stay
deterministic — autonomy of investigation and proposal, never of execution.

**Access control runs before the model** (inspired by the ReviewOps Agent
capstone): evidence classified above the model clearance — Slack discussion
is CONFIDENTIAL by default — is withheld from every prompt while humans keep
full visibility in the audit trail and postmortems; write-risk tools are
callable only by the Control plane's executor principal (a compromised
agent physically cannot invoke a rollback — audited as `tool.denied`); and
API tokens carry roles (viewer/operator/approver/admin) checked against a
policy table, so a viewer can read everything and mutate nothing.


