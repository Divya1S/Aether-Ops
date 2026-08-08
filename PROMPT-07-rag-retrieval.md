# Refined Prompt — Milestone 7: RAG + Retrieval Evaluation (self-issued)

The gap analysis ([docs/17](docs/17-ai-engineer-gap-analysis.md)) ranks this
the biggest differentiator: RAG appears in 31.6% of 10,000+ postings, and
*measuring* retrieval quality — the thing this milestone makes routine — is
the rarest skill in both datasets. AetherOps has the perfect native corpus:
its own runbooks, episodes, and generated postmortems.

## The prompt

```text
MISSION
Replace keyword-overlap recall with a real retrieval pipeline over the
platform's own knowledge — ingestion, chunking, embeddings, hybrid search,
source attribution — and make its quality a measured, CI-gated number.

OPERATING RULES
1. Zero dependencies, deterministic by default. The default embedder is a
   pure-stdlib TF-IDF vectorizer so retrieval evaluation runs in CI with no
   network; an Ollama embedding backend is selectable by config for live
   mode. Chunking strategy and embedder are configuration, not code.
2. Comparison is the point. Two chunking strategies (fixed-size+overlap,
   paragraph) must be runnable side by side, with per-strategy metrics in
   the eval report — the interview question is "why this chunking?", and
   the answer must be a table, not a vibe.
3. Attribution survives end to end. Every retrieved chunk carries doc ID +
   offset; retrieved guidance enters the workflow as Evidence with a
   rag:// citation like every other source. Runbook guidance is advisory
   context, not causal evidence — it must not inflate diagnosis confidence
   (coverage excludes it), keeping golden-scenario metrics stable.
4. Labeled evaluation, honest metrics. A hand-labeled dataset of ≥20
   queries → relevant runbook docs; report precision@1, precision@5,
   recall@5, and MRR per strategy; the CI release gate fails below a
   precision@1 floor of 0.6.
5. The knowledge loop closes. Generated postmortems are ingested into the
   store at the end of each run — incident N's writeup is retrievable
   context for incident N+1.

DELIVERABLES
- src/aetherops/rag/: corpus (seeded runbooks), chunking, embeddings
  (TfidfEmbedder + OllamaEmbedder), hybrid retriever with attribution,
  labeled eval set
- Knowledge agent retrieves runbook guidance as cited Evidence
- evals: retrieval metrics per strategy + gate wired into make eval / CI
- tests: chunkers, embedder determinism, ranking sanity, attribution,
  workflow integration, the metric gate itself
- README updated; all existing tests stay green with unchanged eval
  aggregates (calibration must not shift)

QUALITY BAR
[ ] make test green; make eval shows retrieval metrics for both strategies
    and still exits 0; CI needs no network
[ ] Acceptance criteria #5–9 from docs/17 all pass
[ ] Workflow evidence now includes cited runbook guidance without changing
    RCA confidence or trust-ladder verdicts
```
