# Refined Prompt — Milestone 11: The Agentic Core (self-issued)

The adversarial audit's central finding: no model output ever changes what
the system does next — a governed workflow, not an agent. This milestone
adds bounded agency at the two seams built for it, inheriting safety from
the existing write-guard, catalog, schema validator, and audit ledger.

## The prompt

```text
MISSION
Make the model a decision-maker in two places — evidence investigation and
remediation planning — under hard budgets, with deterministic fallbacks,
full trajectory auditing, and byte-stable golden evals.

OPERATING RULES
1. Agentic investigation loop (Knowledge phase): each iteration the model
   chooses the next action from a typed READ-only tool menu (metrics,
   deploys, commit diff, k8s events, slack thread, runbook search) or
   declares finish with a reason. Decisions are structured JSON validated
   by the existing schema validator; invalid → one re-prompt carrying the
   error; invalid again → scripted fallback completes the baseline bundle
   (the loop can degrade, never regress). Hard bounds: max 8 steps,
   duplicate-call rejection, forced finish on budget exhaustion. Every
   decision is audited (agent.decision) — replayable trajectories.
2. Model-proposed plans (make "plan compilation" literally true): the
   model emits the plan as JSON referencing ONLY Step Catalog actions,
   given the known grounded values (service, previous revision, suspect
   commit). Compilation validates schema → catalog membership → required
   args; two failures → deterministic fallback plan (audited
   plan.fallback). The Reviewer's independent checks remain the last
   line. The model's self_estimate replaces the hardcoded 0.9 prior in
   the planner's confidence — the audit's "constants" finding fixed where
   structured output exists; score_confidence's first param renamed to
   `prior` everywhere for honesty.
3. Determinism where it is load-bearing: the offline backend implements
   both behaviors as deterministic policies (state-driven next-action;
   canonical plan JSON), reproducing today's exact evidence order and
   steps — golden-eval metrics stay byte-identical; only token counts
   move.
4. Safety must be attacked, not asserted: a test forces the loop to
   request a write tool (rollback) — the menu rejects it, the write-guard
   would deny it anyway, the workflow completes read-only. A test forces
   a catalog-violating plan — compilation rejects it and falls back.
5. New prompts are registry artifacts: investigate@1.0.0 added,
   plan bumped to 2.0.0, lockfile regenerated deliberately.

DELIVERABLES
- agents/investigation.py (tool menu, executors, decision schema, JSON
  extraction); knowledge.py rewritten as the loop; planner.py rewritten
  as propose-validate-compile-fallback
- offline.py policies for investigate/plan; registry + lock updates
- eval rows gain investigation_steps/termination; trajectory in output
- tests/test_agentic.py (baseline parity, budget exhaustion, malformed-
  JSON fallback, write-request containment, catalog-violation fallback,
  self-estimate flow); all 116 existing tests stay green
- README: "bounded agentic core" section replacing overclaims

QUALITY BAR
[ ] make test green (old + new); eval precision/calibration byte-identical
[ ] make demo-live shows the model actually choosing tools, audited
[ ] The audit's Phase 2 checklist rows (tool selection, multi-step
    reasoning, adaptation, termination) each map to a file:line
```
