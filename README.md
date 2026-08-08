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
make test         # 85 tests, pure stdlib — no network, no keys
make demo         # canonical SEV2 end-to-end (deterministic offline backend)
make eval         # golden scenarios + retrieval quality, dual release gates
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
and `make eval` scores retrieval against a hand-labeled query set —
precision@1/precision@5/recall@5/MRR per chunking strategy, gated in CI.
Each resolved incident's postmortem is ingested back into the store, so
incident N's writeup is retrievable context for incident N+1.


