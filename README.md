# AetherOps

[![CI](https://github.com/Divya1S/Aether-Ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Divya1S/Aether-Ops/actions/workflows/ci.yml)

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
make test         # 72 tests, pure stdlib — no network, no keys
make demo         # canonical SEV2 end-to-end (deterministic offline backend)
make eval         # golden-scenario evaluation + release gate
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


