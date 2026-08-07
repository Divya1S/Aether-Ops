# Refined Prompt — Milestone 2: Evaluation Harness (self-issued)

Milestone 1 delivered the design canon and a vertical slice that proves the
execution semantics. Its weakest point, by its own documentation's standards
([docs/10-evaluation.md](docs/10-evaluation.md)): a single canned scenario and
an offline model backend hard-coded to it. A platform whose thesis is
"evaluation is the growth mechanism" must ship its evaluation harness next —
before more connectors, more agents, or any real model backend.

## The prompt

```text
MISSION
Implement docs/10-evaluation.md as running code: a golden-scenario set, a
replay harness, a metrics catalog, and the trust-ladder verdict — in the same
zero-dependency, runnable style as the existing slice.

OPERATING RULES
1. Scenario-driven, not canned. Refactor the faked connectors to serve a
   typed evidence Snapshot; the canonical SEV2 becomes Scenario 1 of a golden
   set, byte-compatible with the existing demo and tests.
2. The offline model backend must become evidence-driven: it parses the
   evidence digest in the prompt and templates conclusions from what it
   actually finds (commit SHA, evidence indices, symptom markers). No
   evidence, no claim — the same grounding contract hosted models are held
   to. Remove every hard-coded incident fact from it.
3. Escalation is success. Include at least one scenario whose ground truth is
   "escalate: no correlated change event"; scoring must reward the platform
   for refusing to diagnose it, and a low RCA confidence there must count as
   GOOD calibration.
4. Include a second diagnosable scenario (different service, different
   commit, different revisions) to prove the pipeline generalizes.
5. Metrics must be defined operationally, not aspirationally: RCA
   precision@1, escalation correctness, plan-step accuracy, verification pass
   rate, citation faithfulness (hallucinated-reference detection), mean
   calibration error (|confidence − correctness|), tool calls, tokens, audit
   integrity. Aggregate + per-scenario rows.
6. Trust ladder: per failure class, compute advisory-only vs gated-writes
   eligibility from precision, calibration error, and episode count, citing
   the docs/10 thresholds. Auto-path must be reported as out of reach
   (requires ≥50 golden episodes).
7. Release-gate behavior: the eval CLI exits nonzero if RCA precision@1
   falls below the Phase 1 exit criterion (0.60, docs/14).

DELIVERABLES
- src/aetherops/evals/{scenarios.py, harness.py, __main__.py}
- refactors: connectors/fakes.py (Snapshot), agents/knowledge.py (derive
  commit queries from deploy evidence — no hard-coded SHA),
  gateway/model_gateway.py (evidence-driven backend), demo.py (thin wrapper
  over the canonical scenario)
- tests/test_evals.py; all existing tests stay green
- make eval target; README updated

QUALITY BAR
[ ] make test green (old + new), make demo output semantically unchanged
[ ] make eval prints per-scenario rows, aggregates, trust ladder, gate result
[ ] Scenario 2 escalates with low confidence and that scores as correct
[ ] No incident-specific literals remain in the model backend
```
