# 17 — AI Engineer Job-Market Gap Analysis & Upgrade Plan

**Inputs:** [16-ai-engineer-job-descriptions.md](16-ai-engineer-job-descriptions.md)
(8 postings; one exact duplicate → **7 unique companies**, incl. WelbeHealth and
Exelixis; level skews associate → mid "AI Engineer / GenAI Engineer") and the
full AetherOps codebase at commit `2b3f0cf` (verified by running the 62-test
suite, not by reading the README).

**External validation:** the 7-posting sample is cross-checked in Phase 2b
against large-sample market studies — Axial Search's analysis of 10,000+
AI/ML postings and 365 Data Science's study of 903 Glassdoor postings — plus
2026 hiring-skill guides and hiring-manager portfolio guidance (sources cited
there). The roadmap in Phase 9 reflects both datasets.

**Standing constraint:** zero paid services. Every recommendation below is
implementable at $0 (stdlib, free open-source libraries, Ollama for local
models, GitHub free tier).

---

## Phase 1–2 — Normalized requirements and frequency analysis

Counts are exact over the 7 unique postings (labeled A–G in file order:
A=WelbeHealth, B=enterprise-consulting blurb, C=agents/copilots posting
(appears twice), D=Python/Java+MongoDB, E=Exelixis, F=GenAI/ML stack,
G=SWE+GenAI features). Where a capability is implied rather than named, the
count says so.

### A. Extremely repeated (≥5/7) — table stakes for every posting

| Capability | Freq | Example JD wording | Why companies want it | What "good" looks like in a project |
|---|---|---|---|---|
| LLM / GenAI application development | **7/7** | "AI-powered applications using enterprise LLMs (OpenAI, Anthropic Claude, Google Gemini)" (A) | The job *is* building software around LLMs | A working system where model calls are abstracted, routed, metered, and swappable — not one hardcoded API call |
| Agentic AI (agents, copilots, workflow automation) | **5/7** (B,C,D,E; F via LangGraph) | "Develop agents that automate multi-step IT and business tasks by connecting LLMs, prompts, tools, and APIs" (E) | 2025-26 hiring wave is agents-first; companies want multi-step automation, not chatbots | Multiple specialized agents with defined contracts, an orchestration layer, and evidence the loop is controlled (retries, gates, budgets) |
| Validation / QA of AI behavior | **5/7** (A,C,E,G; C's "validation steps") | "Assist in validation and QA testing of new AI use cases" (A); "pre-release testing/validation" (E) | AI features fail in ways normal software doesn't; teams need people who test model behavior, not just code paths | Automated evaluation with datasets, metrics, and regression gates — runnable in CI |
| Reliability / troubleshooting / scalability | **5/7** (C,D,E,G; C dup) | "Troubleshoot issues and optimize reliability and scalability of deployed solutions" (C) | Prototypes are easy; production reliability is the actual skill gap | Explicit failure handling: retry taxonomies, fallbacks, timeouts, graceful degradation — with tests |
| Documentation / governance practices | **5/7** (A,C,E,G; +B implied) | "Follow AI governance standards, including documentation, validation, and reusability practices" (C); "runbooks, SOPs" (E) | AI systems face compliance scrutiny; undocumented AI doesn't ship in enterprises | Design docs with decision records, runbooks, audit trails, versioned artifacts |

### B. Frequently repeated (3–4/7) — expected of a strong candidate

| Capability | Freq | Example wording | Why | What "good" looks like |
|---|---|---|---|---|
| Tool / function calling + API integration | 4/7 (A,D,E,G) | "basic function/tool calling integrations" (A); "Integrate AI capabilities with backend systems, APIs, and databases" (D) | Agents are useless without governed access to systems | Typed tool contracts, rate limiting, error surfaces, audit of every call |
| RAG (retrieval-grounded generation) | 3/7 (A,B,F) | "RAG systems that ground AI responses in proprietary data" (A) | Hallucination is the #1 enterprise blocker; grounding in company data is the standard fix | Ingestion → chunking → embeddings → vector search → **measured** retrieval quality |
| Prompt engineering / structured prompts | 3/7 (A,C,E) | "structured prompts, workflows, and validation steps" (C) | Prompts are production artifacts; unmanaged prompts are unmanaged behavior | Prompts as versioned, testable artifacts — not inline string literals |
| Cloud deployment | 3/7 (A,B,F) | "cloud-based deployments in Azure… Docker, private endpoints, secure configurations" (A) | AI features live inside cloud estates | Containerized service, documented deploy path, secure configuration |
| Business translation | 3/7 (A,C,E) | "Translate business requirements into scalable, repeatable AI-driven tools" (C) | Companies fear engineers who build tech without a problem | A project framed around a quantified business problem with an ROI model |
| Backend / data integration | 3/7 (D,F,G) | "Work with MongoDB to design and manage application data" (D) | AI features sit on normal software stacks | Clean persistence layer, real API surface, schema-thinking |

### C. Emerging / differentiating (1–2/7) — low frequency, high signal

| Capability | Freq | Example wording | Why it differentiates |
|---|---|---|---|
| **Retrieval quality evaluation** | 1/7 (A) | "Help evaluate retrieval quality and document findings" | Almost no junior candidate can *measure* retrieval; anyone can call a vector DB |
| Security / responsible AI | 2/7 (A,E) | "secure-by-design and responsible AI principles… safe prompting practices" (E) | Injection defense and data handling are rare, senior-flavored skills |
| CI/CD with validation gates | 2/7 (A,E) | "CI/CD pipeline tasks for automated testing, build validation gates, artifact versioning" (E) | Wiring *AI evaluation* into CI is a step beyond running pytest |
| Structured outputs | 2/7 (A,E) | "structured output handling" (A) | Schema-constrained model output is what separates demos from systems |
| Monitoring / versioning (AI ops) | 3/7 (A,C,E) | "monitoring, versioning, and CI/CD pipelines" (A) | Token/cost/latency telemetry is asked about in every serious interview |
| Coding-agent fluency | 1/7 (E) | "Use coding agents (e.g., Claude Code, GitHub Copilot, Codex) to accelerate delivery" | New and rising; being productive *with* AI tooling is itself a skill |
| POC → production hardening | 2/7 (A,E) | "evolve prototypes into maintainable code through clear specifications, structured prompts, tests, modular design, and error handling" (E) | The exact junior-to-mid gap companies complain about |

### D. Generic (frequent, near-zero differentiation)

Python (2/7 explicit, all implied) · REST APIs (G) · debugging (D,G) · agile/
code reviews (E,G) · "stay current with AI" (A,D) · clean, documented code
(G). Every candidate claims these; a project only needs to not *contradict*
them. Named-framework shopping lists (LangChain/LangGraph/PySpark/Airflow,
1/7) are keyword noise — the capability behind them (orchestration, pipelines)
matters; the brand names do not.

### Phase 2b — External market validation (beyond the 7 postings)

Frequencies from independent large-sample studies:

| Requirement | Axial Search, 10,000+ postings | 365 Data Science, 903 Glassdoor postings |
|---|---:|---:|
| Python | 61.5% | 71% |
| Cloud platforms | 54.7% | AWS 32.9% / Azure 26% |
| Foundation models / LLMs | 51.3% | NLP 19.7% |
| **Observability & monitoring** | **40.3%** | — |
| CI/CD | 34.0% | Continuous deployment 10.4% |
| RAG | 31.6% | 13.6% |
| Kubernetes / Docker | — | 17.6% / 15.4% |
| Fine-tuning | — | 14.8% |
| LangChain (named) | — | 10.7% |
| AI agents | — | 10.6% |

Qualitative signals repeated across 2026 hiring guides and hiring-manager
material: **eval design is "the single biggest signal of having actually
built with LLMs"**; the 2026 skill set is agent orchestration, MCP
integration, eval design, prompt engineering, vector DB/RAG, cost
optimization, safety/guardrails (OWASP LLM Top 10 vocabulary, with
LLM06 Excessive Agency as the senior-vs-mid discriminator), production
observability/LLM tracing, and frontier-model fluency; hiring managers scan
for **production signals** — failure handling, evals, cost engineering,
graceful degradation — and rate a **live demo URL** far above a repo link;
"the job is closer to distributed-systems engineering with a probabilistic
component than to ML research"; specialists in a domain beat generalists
(76% of postings in the 903-sample seek domain specialists).

**What the external data changes versus the 7-posting sample:**

1. **Observability is far bigger than the sample suggested** (40.3% — 4th of
   all requirements). Promoted from a sub-bullet into the Milestone 6 core:
   per-call latency, per-node timing, cost metering, workflow trace summary.
2. **Eval design is the #1 hiring signal industry-wide** — AetherOps' eval
   harness is already its strongest asset; the roadmap extends and showcases
   it rather than treating it as done.
3. **MCP fluency is a 2026-specific differentiator** the 7 postings never
   mention. AetherOps' docs/04 already *designs* an MCP connector gateway —
   adding a real, minimal MCP server (stdlib JSON-RPC) closes a docs-vs-code
   gap and lands a rare skill. Added to Milestone 9.
4. **Cost engineering is named an interview differentiator** — per-workflow
   cost reporting joins Milestone 6 (the docs/13 model becomes measured
   numbers).
5. **Safety should speak OWASP LLM Top 10** — AetherOps already implements
   the defenses (injection quarantine = LLM01; Step Catalog + approval tiers
   = LLM06 Excessive Agency); a mapping section in docs/05 converts existing
   work into recognized vocabulary. Added to Milestone 8.
6. **A live demo URL matters** — a $0 deployment (e.g. Hugging Face Spaces
   free tier or a GitHub Pages walkthrough) joins Milestone 9.
7. **Fine-tuning (14.8%) and PyTorch/TensorFlow** appear in the broad-market
   data but are deliberately excluded: those postings skew classic-ML roles;
   AetherOps' thesis is orchestration + evaluation of hosted/local models,
   not model training. The documented reason is the interview answer.
8. **Domain specialization is confirmed as correct strategy** — AetherOps'
   incident-operations focus beats a generic "AI app" portfolio.

Sources: [Axial Search — AI/ML Engineering Jobs 2026](https://axialsearch.com/insights/ai-ml-engineering-jobs/) ·
[365 Data Science — AI Engineer Job Outlook](https://365datascience.com/career-advice/career-guides/ai-engineer-job-outlook-2025/) ·
[Digital Applied — AI Developer Hiring Skills 2026](https://www.digitalapplied.com/blog/ai-developer-hiring-skills-that-matter-2026) ·
[dataskew.io — AI Engineering Roadmap](https://dataskew.io/roadmaps/ai-engineering/) ·
[Dev.to — AI Portfolio Projects That Get You Hired](https://dev.to/klement_gunndu/5-ai-portfolio-projects-that-actually-get-you-hired-in-2026-5bpl) ·
[hireagentic.dev — How to Hire an AI Engineer](https://hireagentic.dev/blog/hire-ai-engineer-guide) ·
[Lightcast — Generative AI Job Market](https://lightcast.io/resources/blog/the-generative-ai-job-market-2025-data-insights)

---

## Phase 3 — The candidate these postings are collectively hiring

Reading all seven as one hiring manager: they want someone who **treats LLM
applications as software systems** — who can take a business workflow, wire
models + tools + data into it, and then do the unglamorous engineering
(validation, reliability, documentation, security) that lets it ship. The
repeated junior-level phrasing ("under guidance", "assist") plus the repeated
production vocabulary ("validation", "reliability", "governance") says the
market is full of people who can *prompt* and short of people who can
*engineer*.

Per category — "knows the technology" (K) vs. **"has engineered it into a
reliable system" (E)** — the second column is what gets hired:

| # | Category | K — knows | **E — has engineered** |
|---|---|---|---|
| 1 | LLM capabilities | Has called a chat API | Model abstraction layer: routing by task, fallbacks, metering, swappable backends |
| 2 | RAG | Has used a vector DB tutorial | Owns the full pipeline *and measures it*: chunking choices justified, retrieval precision/recall evaluated against labels |
| 3 | Agentic AI | Has run a LangChain agent | Designed agent contracts, bounded the loop, handled agent failure, proved injection can't hijack it |
| 4 | Backend/SWE | Writes Python scripts | Layered architecture, typed contracts, 60+ tests, clean interfaces between planes |
| 5 | Data/DB | Has used an ORM | Persistence with schemas, retention thinking, evidence/citation data model |
| 6 | Cloud/infra | Has an AWS account | Containerized, configurable via env, documented deploy path |
| 7 | Testing/evaluation | Writes unit tests | Golden datasets, LLM-behavior regression gates, calibration measurement, eval in CI |
| 8 | Observability | Prints logs | Token/cost/latency per call, audit trails, failure analytics |
| 9 | Security/responsible AI | Knows injection exists | Redaction before models, quarantine of untrusted content, attack tests in the suite |
| 10 | CI/CD & LLMOps | Has a green checkmark | Eval quality gates that fail the build; versioned prompts and models |
| 11 | Product translation | Vague "saves time" claims | Quantified problem, unit economics, phased rollout with exit criteria |
| 12 | Documentation | Has a README | Decision records (why/alternatives/trade-offs), runbooks, postmortems |

---

## Phase 4 — What AetherOps actually is today (verified in code)

Inspected: every module in `src/aetherops/`, `tests/`, `.github/workflows/`,
`Makefile`, `pyproject.toml`, docs 00–15. Verification method: the full suite
(62 tests) and all three demos were executed this session; claims below cite
the file that implements them.

**Genuinely implemented (code, with tests):**
- Multi-agent architecture: 9 agents with a common contract (`agents/base.py`), confidence scoring, retry taxonomy
- Deterministic orchestration: DAG executor with retries, checkpoints, pause/resume approval gates, saga compensation (`orchestration/dag.py`)
- Two full workflows on the same core: incident remediation, change-risk scoring (`workflows/`)
- Tool-calling infrastructure: typed `ToolSpec` with risk classes, rate limiting, read-through caching, redaction, hash-chained audit of every call (`connectors/base.py`, `security/audit.py`)
- Model gateway with tier routing and token metering (`gateway/model_gateway.py`) — **but the backend is an offline heuristic; no real model is ever called**
- Evaluation framework: golden scenarios, replay harness, RCA precision / calibration error / citation faithfulness, trust ladder, CI release gate (`evals/`)
- Security engineering: PII/secret redaction, prompt-injection quarantine with the attack in the test suite, independent plan review, policy engine with approval tiers (`security/`, `agents/security.py`, `agents/reviewer.py`, `policy/engine.py`)
- Postmortem generation traceable to the audit ledger (`reporting/postmortem.py`)
- CI: GitHub Actions, Python 3.11/3.12/3.13 matrix, tests + eval gate (`.github/workflows/ci.yml`)
- 16 design docs with decision records; cost/ROI model; phased roadmap

**Claimed in docs but NOT in code (the honesty list):**
- Real LLM calls (docs/07 describes Anthropic tiers; code has `OfflineHeuristicBackend` only)
- Vector search / embeddings / chunking (docs/06 describes pgvector/Qdrant; `memory/store.py` is keyword overlap over in-memory dicts)
- JSON-Schema validation of agent outputs (docs/02 specifies it; code does deterministic checks — citation refs, catalog membership — but no schema validation)
- REST API (docs/12 specifies the surface; no server exists)
- Latency measurement, structured logging, tracing (docs/09; code records tokens and audit events only)
- Containerization, deployment (docs/13; no Dockerfile)
- Persistence (everything is in-memory; audit JSONL optional)

---

## Phase 5 — Requirements × capability matrix

| Industry requirement | Freq | Importance | Implemented? | Current quality (verified) | Gap | Recommended upgrade |
|---|---:|---:|---|---|---|---|
| Agentic AI / workflow automation | 5/7 | High | ✅ | 9 agents, 2 workflows, orchestration with gates/sagas; attack-tested | None material | Keep; this is the crown jewel |
| AI validation / evaluation | 5/7 | High | ✅ | Golden scenarios, metrics, trust ladder, CI gate (`evals/`) | Retrieval eval missing (no retrieval to evaluate) | Extend eval harness to retrieval quality when RAG lands |
| Reliability engineering | 5/7 | High | ✅/🟡 | Retry taxonomy, sagas, gates in code; model-fallback chain documented only | No fallback across model backends; no circuit breaker in code | Backend chain with automatic fallback (free: Ollama→offline) |
| Documentation / governance | 5/7 | Med | ✅ | 16 docs, decision records, audit ledger, postmortems | None | Keep |
| LLM application development | 7/7 | **Critical** | 🟡 | Real gateway/routing/metering architecture; **zero real model calls** | The project never talks to an actual LLM | **Pluggable backend: Ollama (local, $0) with offline fallback** |
| Tool/function calling | 4/7 | High | 🟡 | Superb *governed tool execution*; but tools are invoked by deterministic plans, never chosen by a live model | No model-driven function-calling path | With Ollama backend: let the RCA/planner path consume real model output; keep catalog validation as the safety layer (already built) |
| RAG | 3/7 | **High** | 🔴 | Keyword-overlap memory stub (`memory/store.py`) | No chunking, embeddings, vector search, or retrieval measurement | **Build the RAG subsystem over the platform's own corpus (runbooks/postmortems/episodes) + retrieval eval** |
| Prompt engineering / management | 3/7 | Med | 🟡 | Grounded evidence-digest prompting pattern (good); prompts are inline literals | No registry, no versioning, no prompt tests | Prompt registry: versioned templates, referenced by ID, version recorded in audit/postmortems |
| Structured outputs | 2/7 | Med | 🟡 | Typed dicts + deterministic semantic checks | No schema validation despite docs claiming it | Minimal JSON-Schema validator (stdlib) enforcing per-agent output schemas; validation-failure → semantic retry path (docs/02 already specifies the behavior) |
| Cloud deployment | 3/7 | Med | 🔴 | Docs only | No container, no deploy path | Dockerfile + compose ($0); free-tier deploy runbook |
| CI/CD | 2/7 | Med | ✅ | 3-version matrix + eval release gate | — | Add retrieval-eval to the gate when RAG lands |
| Monitoring / AI ops | 3/7 | Med | 🟡 | Token metering, hash-chained audit | No latency capture, no per-call cost, no structured logs | Per-call latency + cost in gateway; JSON log option |
| Backend / data | 3/7 | Med | 🟡 | Clean layering; in-memory stores | No persistence, no API surface | SQLite persistence (stdlib, $0) for memory+audit; FastAPI surface as optional extra |
| Security / responsible AI | 2/7 | High (differentiator) | ✅ | Redaction, quarantine, attack tests, policy, audit | None material | Keep; showcase harder |
| Business translation | 3/7 | Med | ✅ | Cost model, ROI, phased roadmap (docs 13/14) | — | Keep |
| Python engineering | 7/7 impl. | Table stakes | ✅ | Typed, layered, 62 tests, zero deps | — | Keep |

---

## Phase 6 — Highest-value gaps, ranked

1. **The system never calls a real model.** Every posting is about *LLM-powered* applications; this is the one place AetherOps' beautiful architecture is visibly hollow. Fix is cheap and free: an Ollama backend behind the existing `complete()` seam, with automatic fallback to the offline backend — which simultaneously implements the documented fallback-chain reliability story. Highest signal per line of code in the whole plan.
2. **No real RAG.** Explicit in 3/7 postings, and the *measurement* of it (chunking comparison, retrieval precision against labels) appears in the market exactly once — meaning almost no competing candidate demonstrates it. AetherOps has the perfect native corpus: its own runbooks, postmortems, and incident episodes. Replacing the keyword stub with ingestion → chunking → embeddings → hybrid search → **retrieval eval in the existing harness** is architecturally natural (docs/06 already specifies it).
3. **Structured outputs + prompt versioning.** Two moderate gaps, one fix each; both close the docs-vs-code honesty gap and directly answer JD language ("structured output handling", "structured prompts").
4. **Productionization face: API + Docker + latency/cost telemetry.** Turns "runs via make" into "runs as a service you can `docker run`", which is what "deployment" means to these companies. All free.
5. **Persistence (SQLite).** Cheap credibility for the backend/data requirement; makes memory and audit survive restarts. Lower priority than 1–4.

Deliberately **not** added (rule 4: no technology soup): LangChain/LangGraph
(the platform's whole thesis is owning the orchestration they abstract — a
better interview story), Kubernetes/Airflow/Kafka/PySpark (docs-level design
is the right altitude for a solo repo), MongoDB/Pinecone (wrong tools vs.
SQLite/local vectors at this scale), paid APIs of any kind (standing
constraint).

---

## Phase 7–8 — Upgrade design (preserving the architecture)

The existing five-plane architecture stays untouched. Every upgrade slots
into an existing seam — that's the engineering-maturity story: **the system
was designed so these upgrades are plug-ins, and the upgrades prove it.**

```
Business problem (docs/00: incident hours)          [exists ✅]
  ↓ API layer          → NEW: FastAPI surface (optional extra) over run_* functions
  ↓ Orchestration      → exists ✅ (DAG, gates, sagas)
  ↓ LLM / agents       → UPGRADE: gateway backend chain  Ollama → offline
  ↓ Tools / data       → exists ✅ (connector gateway)
  ↓ RAG / retrieval    → UPGRADE: rag/ package replaces memory keyword stub
  ↓ Validation         → UPGRADE: JSON-Schema on agent outputs (+ existing citation checks)
  ↓ Evaluation         → EXTEND: retrieval metrics into evals/ + CI gate
  ↓ Observability      → UPGRADE: latency + cost per model call, JSON logs
  ↓ Reliable response  → exists ✅ (gates, verification, postmortems)
```

Key design decisions (each will get a decision record):
- **Backend chain, not backend swap:** `ModelGateway` gets an ordered backend list; a backend that's unreachable or errors falls through, with the event audited. Offline heuristic remains last — the system literally cannot lose its brain. This *implements* docs/07's fallback design.
- **RAG over the platform's own knowledge:** corpus = runbooks (seeded), generated postmortems, incident episodes. Chunking strategies (fixed-size vs. paragraph) are a config choice so they can be *compared* in the eval. Embeddings via Ollama's embedding endpoint when available; deterministic TF-IDF vectors (pure stdlib) otherwise — so retrieval eval runs in CI at $0 with no network.
- **Retrieval evaluation as a first-class eval:** a small labeled dataset (query → relevant doc IDs), precision@k / recall@k / MRR, wired into `evals/` and the CI gate, with a chunking-strategy comparison report.
- **Prompt registry:** templates move to `prompts/registry.py` with semantic versions; every `ModelResponse` and postmortem records the prompt version used. Auditable prompts = the governance language in postings C and E.
- **Schema validation:** ~60-line stdlib validator (type/required/enum/additionalProperties subset — documented as a deliberate subset, jsonschema listed as the production choice); each agent declares an output schema; violations raise the *existing* semantic-retry path.

---

## Phase 9 — Implementation roadmap

### Phase 0 — Current state (preserve, don't touch)
Orchestration core, agent contracts, connector gateway, security stack, eval
harness, CI, docs 00–15. These already exceed what the postings ask.

### Phase 1 — Highest ROI (Milestone 6): live model + observability core
- **Build:** `gateway/backends.py` — `OllamaBackend` (stdlib `urllib` against `localhost:11434`, zero deps), backend-chain fallback in `ModelGateway`, env-configurable (`AETHEROPS_BACKENDS`); **observability core** (promoted per Phase 2b #1): per-model-call latency + estimated cost on every `ModelResponse`, per-node duration spans in the audit ledger, and a per-workflow trace/cost summary printed by demos and aggregated by the eval report.
- **Why:** closes the "never calls a real LLM" gap, implements the documented fallback-chain reliability, and lands the market's 4th-most-demanded capability (observability, 40.3%) in the same stroke. **JD reqs:** LLM app dev (7/7 + 51.3%), observability (40.3%), reliability (5/7), cost optimization (2026 differentiator).
- **Files:** `gateway/`, `orchestration/dag.py` (timing), `demo.py`, `__main__.py`, tests. **Deps:** none (Ollama is an optional local install; CI never needs it). **Complexity:** M.
- **Signal:** "my agents run on a local Llama model via a fallback chain; unplug Ollama mid-run and the system degrades gracefully — audited, timed, and costed."
- **Acceptance:** see Phase 10 (#1–4, #17).

### Phase 2 — Production AI engineering (Milestone 7): RAG + retrieval eval
- **Build:** `rag/` package — document store, two chunkers, `TfidfEmbedder` (stdlib) + `OllamaEmbedder`, cosine vector index, hybrid (keyword+vector) search; Knowledge/ChangeIntel agents retrieve through it; labeled retrieval dataset + precision@k/recall@k/MRR in `evals/`; CI gate extended; chunking comparison in the eval report.
- **Why:** the biggest differentiating gap; retrieval *measurement* is the rarest skill in the dataset. **JD reqs:** RAG (3/7), retrieval eval (the differentiator), AI validation (5/7).
- **Files:** new `rag/`, `memory/store.py` (becomes a thin wrapper), `agents/knowledge.py`, `evals/`. **Deps:** none required. **Complexity:** M–L.
- **Signal:** interview-dominant — chunking trade-offs, hybrid search rationale, eval methodology, all with numbers.
- **Acceptance:** #5–9.

### Phase 3 — Structured outputs + prompt registry + OWASP mapping (Milestone 8)
- **Build:** stdlib JSON-Schema-subset validator; per-agent output schemas enforced in `_agent_node`; `prompts/registry.py` with versioned templates; prompt version recorded in audit + postmortems; prompt regression tests; **docs/05 section mapping every existing defense to OWASP LLM Top 10 IDs** (injection quarantine → LLM01; Step Catalog + policy tiers + approval gates → LLM06 Excessive Agency; redaction → LLM02/LLM06 data exposure), with the attack tests cross-referenced.
- **JD reqs:** structured outputs (2/7), prompt engineering (3/7), governance (5/7), safety/guardrails in recognized vocabulary (2026 differentiator). **Complexity:** S–M. **Acceptance:** #10–12, #19.

### Phase 4 — Productionization + MCP + live demo (Milestone 9)
- **Build:** FastAPI app (`api/`, optional extra `aetherops[api]`) exposing incidents/changes/approvals/evals endpoints with token auth, over the existing `run_*` functions; SQLite persistence for memory + audit (stdlib); `Dockerfile` + `compose.yaml`; **a minimal MCP server** (`aetherops/mcp/`, stdlib JSON-RPC over stdio) exposing read-only platform tools (query incidents, eval results, memory search) so any MCP client — including Claude Code — can drive the platform, making docs/04's MCP claim literal; **a $0 live demo** (Hugging Face Spaces free tier running the API demo, or a GitHub Pages walkthrough) since hiring managers rate a live URL far above a repo link; deploy runbook (free-tier only); README repositioned; docs/15 resume bullets updated to match milestones 2–9.
- **JD reqs:** cloud/Docker (3/7 + 54.7%/15.4%), backend/data (3/7), APIs, MCP integration (2026 differentiator), portfolio "production signals". **Complexity:** M–L. **Acceptance:** #13–16, #18, #20.

---

## Phase 10 — Testable acceptance criteria

1. With Ollama running, `make demo` serves RCA from a local model; killing Ollama mid-run falls back to the offline backend with an audited `backend.fallback` event and the run still completes. (Test: fake failing backend.)
2. Every `ModelResponse` carries `backend`, `latency_ms`, and `cost_estimate`; the demo and eval report aggregate them.
3. CI remains green with no network and no Ollama installed (offline backend only).
4. Backend selection is configuration (`AETHEROPS_BACKENDS=ollama,offline`), not code.
5. Retrieval quality is measured against a labeled dataset ≥20 queries; `make eval` reports precision@5, recall@5, MRR.
6. Two chunking strategies are runnable via config and compared in the eval output with per-strategy scores.
7. Embedding provider is swappable via config (TF-IDF ↔ Ollama) with identical interfaces; TF-IDF path has zero dependencies.
8. Every retrieved chunk carries source attribution (doc ID + offset) that survives into agent citations.
9. Retrieval eval runs in CI and fails the build below a precision floor.
10. Every agent's output validates against a declared schema; an invalid output triggers the documented semantic-retry, then escalation — covered by a test that injects a malformed output.
11. Every prompt has a registry ID + version; audit records and postmortems show which prompt version produced each model call.
12. Changing a prompt template without bumping its version fails a test.
13. `docker build` + `docker run` yields a working API container; `GET /health` returns build info.
14. All mutating API endpoints require a bearer token; approval endpoints replay the existing gate semantics.
15. Memory and audit survive process restart (SQLite file), with `make demo` unchanged (in-memory default).
16. README shows the full story (problem → architecture → run it → measure it) in under 2 minutes of reading.
17. Every workflow run emits a trace: per-node durations and per-model-call latency/cost, printed as a timing summary by the demo and aggregated in the eval report.
18. An MCP client can list and call at least 3 read-only AetherOps tools served by `python -m aetherops.mcp` over stdio; covered by a protocol-level test that speaks raw JSON-RPC.
19. docs/05 maps each implemented defense to an OWASP LLM Top 10 identifier, and the injection/tamper tests reference the IDs they exercise (LLM01, LLM06).
20. A public live-demo URL exists using only free-tier services; the README links it beside the repo badges.

---

## Phase 11 — What an interviewer could now probe (with evidence)

| Question | Evidence in project |
|---|---|
| "Walk me through your architecture" | docs/01 planes + the code mirrors each plane |
| "Why deterministic orchestration instead of an agent loop?" | docs/03 decision record; `dag.py`; injection tests proving why it matters |
| "How do you pick which model serves a task?" | `route()` tiers + backend chain; cost model docs/13 |
| "What happens when the model is down mid-incident?" | Backend-chain fallback test; audited degradation |
| "How did you chunk your documents, and why?" | Two strategies + eval comparison numbers (Phase 2) |
| "How do you *know* your retrieval works?" | Labeled dataset, precision@k/recall@k/MRR in CI |
| "How do you prevent hallucination?" | Citation-mandate + `[En]` validation (`root_cause.py`), evidence-or-silence design, grounding checks |
| "How do you stop prompt injection?" | Quarantine + the attack in `tests/test_security_agents.py` |
| "How are tool calls governed?" | `ToolSpec` risk classes, rate limits, audit chain, policy tiers |
| "How do you evaluate agent quality over time?" | Golden scenarios, calibration error, trust ladder, CI release gate |
| "What does a bad deploy of *your prompts* look like?" | Prompt versioning + regression test (Phase 3) |
| "Cost of running this at scale?" | docs/13 unit economics; per-call cost metering (Phase 1) |
| "How would you deploy it?" | Dockerfile, compose, API + auth, deploy runbook (Phase 4) |
| "Show me a failure you engineered for" | Saga compensation test; verification-failure → rollback; escalation-as-success |

---

## Phase 12 — What companies actually want

The postings, read together, are not asking for AI researchers or framework
operators. They are asking for **software engineers who can be trusted with
model-shaped uncertainty**: people who wrap probabilistic components in
deterministic engineering — validation, fallbacks, measurement, audit — and
who can explain to a stakeholder why the system can be trusted. The repeated
junior phrasing plus repeated production vocabulary reveals the actual market
pain: plenty of prompt-writers, few system-builders.

**Top 10 capabilities this project MUST demonstrate**
1. Real LLM integration behind an abstraction (Phase 1)
2. Multi-agent orchestration with bounded control flow ✅
3. Governed tool calling ✅
4. RAG with source attribution (Phase 2)
5. Measured retrieval quality (Phase 2)
6. Automated AI evaluation gating CI ✅ (extend)
7. Reliability engineering: retries, fallbacks, degradation (✅ + Phase 1)
8. Structured, validated outputs (Phase 3)
9. Token/cost/latency observability (Phase 1)
10. Documentation and governance ✅

**Top 6 differentiators** (validated against the 2026 skill-shift data)
1. Retrieval evaluation with chunking comparisons (rarest skill in both datasets)
2. Prompt-injection defense proven by attack tests, mapped to OWASP LLM01/LLM06 ✅ (+Phase 3)
3. Evaluation-driven trust ladder (advisory → gated → auto) ✅
4. Fallback chain demonstrated live — unplug the model, system survives, timed and costed (Phase 1)
5. MCP server exposing the platform's tools (2026-specific skill; ~0 junior candidates have it) (Phase 4)
6. Audit-traceable generated postmortems ✅

**Do NOT add without architectural justification:** LangChain/LangGraph,
Kubernetes, Kafka, Airflow, PySpark, MongoDB, Pinecone/hosted vector DBs, any
paid API. Each has a documented reason for absence — that reasoning is itself
interview material.

**Single biggest current weakness:** the platform never calls a real model —
the one hollow spot in an otherwise real system.

**Single highest-impact improvement:** the Ollama backend chain (Milestone 6)
— smallest change, largest credibility shift, and it unlocks the RAG
embedding path behind it.

**What the finished project should prove:** that its author treats LLM
applications as production software — designs the seams, measures the
behavior, engineers the failures, and can defend every decision — which is
precisely the candidate all seven postings are trying to find.
