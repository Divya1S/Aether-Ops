# Refined Prompt — Milestone 9: Productionization + MCP + Live Demo (self-issued)

The last milestone from [docs/17](docs/17-ai-engineer-gap-analysis.md):
turn "runs via make" into "runs as a service", add the 2026-differentiator
MCP surface, persist state, containerize, and give the repo a public face.

## The prompt

```text
MISSION
Ship the production face: a REST API with auth over the existing
workflows, SQLite persistence, a Dockerfile, an MCP server exposing the
platform's tools to any MCP client, refreshed portfolio docs, and a $0
public live-demo page.

OPERATING RULES
1. The API is pure stdlib (http.server) — a deliberate deviation from
   docs/17's FastAPI suggestion, for a reason worth defending: it keeps
   the everything-runs-anywhere guarantee, is fully tested in CI (FastAPI
   tests would skip where it isn't installed), and FastAPI remains the
   documented production choice and optional extra. The API wraps the
   existing run_* functions; it invents no new semantics.
2. Auth is real: every mutating endpoint requires a bearer token
   (AETHEROPS_API_TOKEN); /health is open. The incident approval endpoint
   replays the existing gate semantics — approve resumes, deny refuses,
   exactly as the DAG defines them.
3. Persistence is opt-in and stdlib: SQLite-backed episodic memory and
   audit ledger with identical interfaces; the in-memory default keeps
   demos and evals byte-stable. Restart survival is tested.
4. The MCP server speaks real MCP: newline-delimited JSON-RPC 2.0 over
   stdio — initialize, tools/list, tools/call — exposing at least three
   read-only tools (runbook search, memory search, eval summary). The
   test speaks raw JSON-RPC to a spawned subprocess.
5. Docker: a slim image whose CMD serves the API; build if the docker CLI
   exists, document if it doesn't. compose.yaml for one-command bring-up.
6. Portfolio surfaces: docs/15 resume bullets rewritten to cover
   milestones 2–9 (honest verbs, measured numbers); README gains the API/
   MCP/Docker story and the live-demo link; the live demo is a GitHub
   Pages walkthrough (free tier) published from a gh-pages branch.

DELIVERABLES
- api/server.py + python -m aetherops.api; storage/sqlite.py;
  mcp/server.py + python -m aetherops.mcp; Dockerfile + compose.yaml
- tests: API auth + gate replay + endpoints, MCP wire protocol, SQLite
  restart survival — all runnable in CI with stdlib only
- docs/15 refresh; README; gh-pages site at push time

QUALITY BAR
[ ] make test green everywhere, stdlib only
[ ] Acceptance criteria #13–16, #18, #20 from docs/17 pass (with the
    stdlib-API deviation recorded)
[ ] curl can drive an incident end to end: create → inspect → approve →
    postmortem
```
