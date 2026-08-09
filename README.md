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
make test         # 133 tests, pure stdlib — no network, no keys
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
and `make eval` scores retrieval against a hand-labeled query set (n=22,
self-labeled) — precision@1/precision@5/recall@5/MRR per chunking strategy,
gated in CI. Golden-scenario metrics are honest about their scale: n=4
self-authored scenarios spanning two diagnosable failure classes
(deploy regression, certificate expiry) plus a must-escalate case — they
gate the *pipeline* deterministically; `make eval --live` additionally
reports (never gates) real-model behavior on the same set. The trust
ladder demands sample size, not just precision: a correct-but-single-
episode class stays advisory-only.
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
wall-clock budget (a timed-out attempt is a retryable failure, audited
`node.timeout`) with an optional workflow-level deadline that escalates
with partial findings; semantic retries roll back the failed attempt's
side effects before re-running; and the API's approval endpoint is atomic —
per-incident locks plus fencing tokens make double-execution of a
remediation impossible (proven by a concurrent-approval test). Incidents
can run asynchronously (`{"async": true}` → 202 + poll).

**Run it as a service — with an operator console:**

```bash
make serve        # authenticated REST API + web UI (stdlib) on :8080
make docker       # or: docker compose up — same API, containerized
```

Open `http://localhost:8080/` and you get a real **operator console** (one
self-contained HTML file served by the API — no build step, no npm, no
external assets): trigger the SEV2 incident, watch the agent pipeline and
cited evidence render, **approve the rollback at the policy gate**, and read
the generated postmortem — all driving the live endpoints. The same page
also scores changes, searches runbooks, and runs the evaluation. The
[public demo]( https://divya1s.github.io/Aether-Ops/) is that exact console
in **recorded mode** (a real transcript captured from the API), so it's
clickable at $0 where no backend runs; served locally it flips to **live
mode** automatically.

`POST /v1/incidents` starts an incident and runs it to the approval gate;
`POST /v1/incidents/{id}/approvals {"decision":"approve"}` replays the exact
gate semantics and returns the generated postmortem; `/v1/changes/score`,
`/v1/evals`, and `/v1/runbooks/search` expose the rest. Every mutating
endpoint requires `Authorization: Bearer $AETHEROPS_API_TOKEN`. State can
persist across restarts via the SQLite-backed memory and tamper-evident
audit ledger (`storage/sqlite.py`). There's also an **MCP server** —
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


