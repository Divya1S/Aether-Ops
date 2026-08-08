# Refined Prompt — Milestone 6: Live Model + Observability Core (self-issued)

The gap analysis ([docs/17](docs/17-ai-engineer-gap-analysis.md)) found one
hollow spot in an otherwise real system: the platform never calls an actual
model. External market data adds a second mandate: observability is the 4th
most-demanded capability (40.3% of 10,000+ postings). This milestone fixes
both in one stroke, at $0.

## The prompt

```text
MISSION
Give the model gateway a real brain and a nervous system: a free local-model
backend (Ollama) behind an ordered fallback chain, with per-call latency and
cost metering and per-node timing spans — while CI, tests, and evals remain
deterministic, offline, and dependency-free.

OPERATING RULES
1. Fallback chain, not backend swap. The gateway tries backends in
   configured order; a dead backend is skipped with an audited
   `backend.fallback` event; the offline heuristic is always last — the
   system cannot lose its brain. This implements docs/07's documented
   design.
2. Determinism is sacred where it is load-bearing. The eval harness and the
   test suite ALWAYS pin the offline backend regardless of environment —
   golden-scenario replay must never depend on what's installed. Live mode
   is an explicit opt-in (env var / --live flag), never a silent default.
3. Zero dependencies, still. The Ollama client is stdlib urllib against
   localhost:11434. Ollama itself is an optional external install; nothing
   in the repo imports anything new.
4. Honest telemetry. Every ModelResponse carries backend, served_model,
   tokens in/out, latency_ms, and est_cost_usd — the estimate priced at the
   docs/13 tier assumptions (what the call WOULD cost on the production
   tier; local/offline serving costs $0 in reality, and the field name and
   docs must not blur that). Every DAG node records its duration in the
   audit ledger. Demos print a per-workflow trace summary; the eval report
   aggregates latency and cost per scenario.
5. Prove the failure path. A test injects a backend that dies mid-workflow
   and asserts the run completes on the fallback with the audit trail
   showing the switch. The Ollama protocol itself is tested against a fake
   local HTTP server speaking the real wire format.

DELIVERABLES
- src/aetherops/gateway/backends.py (BackendResult, OllamaBackend,
  BackendUnavailable, chain builder with AETHEROPS_BACKENDS /
  AETHEROPS_OLLAMA_MODEL / AETHEROPS_OLLAMA_URL config)
- ModelGateway upgraded to a fallback chain + latency/cost metering
- orchestration/dag.py: per-node duration_ms in audit
- demo `--live` flag + `make demo-live`; trace summary in demo output
- evals harness rows + aggregates gain latency/cost
- tests/test_backends.py; all existing tests stay green untouched by env
- README "Live model mode (optional, free)" section

QUALITY BAR
[ ] make test green with and without Ollama installed/running
[ ] make demo output unchanged in substance; adds trace summary
[ ] make demo-live serves agents from a local model when Ollama runs,
    falls back gracefully (audited) when it doesn't
[ ] Acceptance criteria #1–4 and #17 from docs/17 all pass
```
