# Refined Prompt — Milestone 3: Change Intelligence (self-issued)

Milestone 2 made incident learning measurable. But the learning loop is still
open: episodes flow *into* memory and nothing reads them before the next bad
deploy ships. The biggest incident trigger is change
([docs/00-executive-summary.md](docs/00-executive-summary.md) §2.3), and the
platform's second pillar — pre-deploy change-risk scoring — exists only on
paper. Close the loop.

## The prompt

```text
MISSION
Implement the Change Intelligence workflow: every proposed change (service,
commit, diff) is scored against the organization's incident history, the
service dependency graph, and the deploy window; risky changes gate, benign
changes flow. Same zero-dependency, runnable, tested style.

OPERATING RULES
1. Second workflow, same core. Reuse the DAG executor, policy engine, gates,
   audit, memory, and model gateway unchanged. If the orchestration core
   needs surgery to host a second workflow shape, that is a design defect to
   fix, not work around.
2. Scoring is deterministic; the model writes only the rationale. Score
   components (failure-signature match against episodic memory, blast radius
   from the service graph, deploy window, service incident history) must be
   inspectable numbers with fixed weights and published band thresholds
   (HIGH >= 70, MEDIUM >= 40).
3. The learning loop must be demonstrated end-to-end in a test: run the
   canonical incident workflow to completion, then score a similar change —
   the episode the incident just wrote must raise the change's risk score.
4. Blast radius comes from a real (small) service graph structure mirroring
   the Neo4j design in docs/06: transitive dependents, not a magic number.
5. Policy semantics: HIGH -> tier-2 approval + canary required; MEDIUM ->
   canary required, auto-allowed; LOW -> allow; freeze window + non-LOW ->
   block with escalation to the release manager. Freeze-block is a
   first-class outcome, not an error.
6. Implement the Change Intelligence agent from the docs/02 roster: evidence
   from the change payload, topology evidence from the graph, episode
   evidence from memory — citations mandatory, as everywhere.

DELIVERABLES
- src/aetherops/graph/service_graph.py (transitive dependents, blast radius)
- src/aetherops/agents/change_intel.py
- src/aetherops/workflows/change_risk.py (assess -> score -> policy ->
  gate -> record; record writes the decision back to memory)
- core: ChangeEvent type; context carries change + graph
- gateway backend: evidence-driven change_risk rationale (no literals)
- python3 -m aetherops --change demo; make demo-change
- tests/test_change_risk.py incl. the learning-loop integration test;
  all existing tests stay green

QUALITY BAR
[ ] make test green (old + new); make demo / make eval unchanged
[ ] Risky pool-raise change: HIGH, pauses at tier-2 gate, canary required
[ ] Benign copy change on the same service: LOW, no gate, auto-allowed
[ ] Freeze window blocks with a clear escalation path
[ ] Incident -> episode -> higher change score proven in one test
```
