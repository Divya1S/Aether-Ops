# AetherOps — Proof of Working (end-to-end demonstration)

This document is a **captured, reproducible run of the entire system**. Every
block below is real output from the commands shown — nothing is mocked up for
the doc. You can reproduce all of it in under two minutes with no keys, no
network, and no paid services:

```bash
python3 -m aetherops --approve     # 1. full incident lifecycle (below)
python3 -m aetherops --change      # 2. change-intelligence pillar
make eval                          # 3. correctness gates on golden scenarios
make test                          # 4. the full automated test suite
```

Everything runs on a deterministic offline backend so the results are
**byte-for-byte reproducible**. A live local model (`make demo-live`) and live
external data (`make live-demo`) are optional and shown at the end.

---

## Proof 1 — the complete incident lifecycle, end to end

**Command:** `python3 -m aetherops --approve`

One command drives the whole product: an alert arrives → evidence is gathered
and **cited** → six agents reason → a **grounded diagnosis** is produced → a
reversible **plan** is compiled from a vetted catalog → policy **pauses at a
human approval gate** → on approval it **executes, verifies recovery, learns**,
and writes a **postmortem** → and the entire run is recorded in a
**hash-chained audit ledger that verifies**.

```
────────────────────────────────────────────────────────────────────────
AetherOps — incident inc_47c328f39393
────────────────────────────────────────────────────────────────────────
SEV2  checkout-service p99 latency > 2000ms  [env=prod]

────────────────────────────────────────────────────────────────────────
Evidence bundle (gathered, cited, redacted at the gateway)
────────────────────────────────────────────────────────────────────────
  [E1] (alert, pagerduty) PagerDuty P-8842: checkout-service p99 latency > 2000ms (urgency=high)
        ↳ pagerduty://incidents/P-8842
  [E2] (metrics, datadog) p99 series [{'ts':'14:00Z','p99_ms':181},{'ts':'14:05Z','p99_ms':2140},{'ts':'14:10Z','p99_ms':2412}]
        ↳ datadog://query/p99-incident
  [E3] (deploy, github) deploys: checkout-service v2025.08.07-3 (prev v2025.08.07-2), commit c9a1f42
        ↳ github://checkout-service/deployments/v2025.08.07-3
  [E4] (commit, github) c9a1f42: Raise DB connection pool max_size 20 -> 200
        ↳ github://commit/c9a1f42
  [E5] (k8s-event, kubernetes) events: OOMKilled checkout-7f9c-1 @14:06Z, OOMKilled checkout-7f9c-4 @14:07Z
        ↳ k8s://prod/checkout-service/events
  [E6] (runbook, aetherops-rag) Investigating p99 latency spikes: first ask what changed…
        ↳ rag://runbook-latency#0
  [E7] (runbook, aetherops-rag) Rolling back a bad deployment: open a revert PR for the offending commit…
        ↳ rag://runbook-rollback#304
  [E8] (episode, aetherops-memory) past incident (deploy-regression/memory): pool increase caused OOMKilled cascade; rollback restored p99
        ↳ memory://episode/ep_94bab4094da2

────────────────────────────────────────────────────────────────────────
Agent conclusions
────────────────────────────────────────────────────────────────────────
  triage       confidence=0.95 model=claude-haiku-4-5-20251001 citations=1
  knowledge    confidence=0.90 model=claude-haiku-4-5-20251001 citations=8
  security     confidence=0.95 model=n/a citations=8
  root_cause   confidence=0.75 model=claude-opus-5 citations=5
  planner      confidence=0.68 model=claude-opus-5 citations=5
  reviewer     confidence=0.95 model=claude-sonnet-5 citations=5

  Diagnosis (deploy-regression/memory):
  Hypothesis (primary): the deploy [E3] shipped commit c9a1f42 raising the DB connection
  pool [E4]. Pod memory grew past its limit, causing OOMKilled and CrashLoopBackOff [E5];
  surviving pods absorbed the load, breaching p99 latency [E2]. Causal chain: deploy [E3]
  -> pool change [E4] -> memory exhaustion [E5] -> latency breach [E2]. A prior episode
  with the same signature supports this class [E8]. Recommended class: deploy-regression/memory.

────────────────────────────────────────────────────────────────────────
Remediation plan (Step Catalog actions only)
────────────────────────────────────────────────────────────────────────
  - rollback_deployment  risk=HIGH  args={'service':'checkout-service','revision':'v2025.08.07-2'}
  - create_revert_pr  risk=MEDIUM  args={'sha':'c9a1f42'}

  Policy: allowed=True approval_tier=2
    [R3-high-prod] rollback_deployment: HIGH-risk write in prod requires tier-2 approval
    [R4-medium-gated] create_revert_pr: MEDIUM-risk write requires tier-1 approval (env=prod, confidence=0.68)

────────────────────────────────────────────────────────────────────────
PAUSED at gate 'approval_gate' — awaiting human approval
────────────────────────────────────────────────────────────────────────
  Decision recorded: APPROVED

────────────────────────────────────────────────────────────────────────
Execution, verification, learning
────────────────────────────────────────────────────────────────────────
  executed rollback_deployment: {'service':'checkout-service','rolled_back_to':'v2025.08.07-2','dry_run':True}
  executed create_revert_pr: {'pr_url':'github://checkout-service/pull/4127','reverts':'c9a1f42','dry_run':True,…}
  verified: {'recovered':True,'p99_ms':182,'note':'Post-remediation window shows p99 at 182ms with 0 OOMKilled events in the last 10 minutes.'}
  learned episode: {'episode_id':'ep_0eb83f9a6f78','failure_class':'deploy-regression/memory'}

────────────────────────────────────────────────────────────────────────
Postmortem generated (65 lines, 4 follow-ups)
────────────────────────────────────────────────────────────────────────
  # Postmortem — inc_47c328f39393: checkout-service p99 latency > 2000ms
  - **Severity:** SEV2   **Service:** checkout-service   **Environment:** prod
  - **Failure class:** deploy-regression/memory   **Diagnosis confidence:** 0.75
  …

────────────────────────────────────────────────────────────────────────
Governance
────────────────────────────────────────────────────────────────────────
  audit records: 48  hash-chain verified: True
  model tokens metered: 3376  est. production cost: $0.0137
```

**What this proves:** the full loop works — gather → cite → diagnose → plan →
**gate** → execute → verify → learn → postmortem — with every claim tied to a
citation, a human approval in the middle, reversible (dry-run) execution, and a
**tamper-evident audit chain that verifies (`True`)**.

---

## Proof 2 — it refuses to guess when the evidence doesn't support a cause

**Command (adversarial scenario — a pool *reduction* coincident with an OOM,
which cannot be the cause):**

```
Adversarial scenario: ops-service p99 latency > 1800ms
  outcome: FAILED | pending_gate: None
  reason : plan: cannot plan without a diagnosis — escalate
  root_cause status: insufficient-evidence
  audit hash-chain verified: True
```

**What this proves:** the system is *grounded*. On evidence that doesn't
support a cause it **escalates to a human instead of fabricating a fix**. This
is the single most important property — and it's enforced, not hoped for.
(`make eval` runs three such adversarial cases; all correctly escalate.)

---

## Proof 3 — Change Intelligence: catch the risky change *before* it ships

**Command:** `python3 -m aetherops --change`

```
Change b7e21c9 on orders-service: Raise DB connection pool max_size 25 -> 250
  score=75/100 band=HIGH  components={'failure_signature':40,'blast_radius':20,'deploy_window':15}
  rationale: HIGH: matches 2 prior incident episode(s) with the same failure signature; blast radius 2 services.
  policy [C2-high]: HIGH-risk change requires tier-2 approval and canary rollout

Change a11c3f0 on orders-service: Update README copy
  score=35/100 band=LOW  components={'failure_signature':0,'blast_radius':20,'deploy_window':15}
  policy [C4-low]: LOW-risk change allowed

Governance:  audit records: 12  hash-chain verified: True
```

**What this proves:** the second pillar works — a genuinely risky change (a
pool increase matching a past incident) is scored **HIGH** and gated for canary
+ approval, while a harmless docs change is cleared **LOW**. This is how the
system prevents outages, not just fixes them.

---

## Proof 4 — correctness gates and the automated test suite

**Command:** `make eval` (adjudicated golden scenarios + retrieval quality)

```
Release gates
    rca_precision_at_1 >= 0.6 -> PASSED
    best-strategy precision_at_1 95% CI lower bound >= 0.6 -> PASSED
    judge citation anchor: 0 hallucinated refs -> PASSED
```

**Command:** `make test` (the full suite; runs in CI on Python 3.11/3.12/3.13)

```
Ran 224 tests in 18.489s
OK (skipped=1)
```

**What this proves:** the diagnoses are *correct* on a labeled scenario set
(not just plausible), retrieval clears a statistical lower-bound gate, the
model-as-judge finds **zero** fabricated citations, and 224 automated tests
pass. CI enforces all of this on every push.

---

## Proof 5 — see it live (optional, still $0)

- **Operator console (clickable UI):** `make serve` → open http://localhost:8080/
  → *Trigger incident* → watch the pipeline → *Approve rollback* → read the
  postmortem. Pick an adversarial scenario from the dropdown and watch it
  **escalate** instead of remediating.
- **Real local model doing the reasoning:** `make demo-live` (needs
  [Ollama](https://ollama.com); free) runs the same lifecycle with a real LLM.
- **Live external data:** `make live-demo` searches GitHub for a real commit
  that raises a resource limit and has a real local model review its real diff
  for deploy risk.

---

## Summary

| Claim | Proof | Verified |
|-------|-------|----------|
| Diagnoses a real incident from cited evidence | Proof 1 | ✅ deploy-regression/memory, 8 citations |
| Won't act without human approval | Proof 1 | ✅ pauses at tier-2 gate |
| Reversible, audited execution | Proof 1 | ✅ dry-run + hash-chain verified `True` |
| Refuses to guess (grounded) | Proof 2 | ✅ adversarial → escalate |
| Prevents risky changes | Proof 3 | ✅ HIGH vs LOW scoring |
| Diagnoses are *correct* | Proof 4 | ✅ eval gates PASSED |
| Engineering quality | Proof 4 | ✅ 224 tests green in CI |

Every command above is in the `Makefile` / runnable with `python3 -m aetherops`.
No keys, no network, no cost — reproducible on any machine with Python 3.11+.
