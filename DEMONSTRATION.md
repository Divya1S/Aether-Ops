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

## Proof 6 — alert storm to hypothesis tree, over the API (added 2026-11)

**Command:** `make serve`, then the requests below (captured from the real
server; ids elided). The console drives exactly these endpoints.

```
$ POST /v1/alerts            # 16 raw events, three monitoring sources
202  received=16 deduplicated=7 new_alerts=9 new_groups=3
     noise_reduction=0.812  (16 events -> 3 groups)
     group origin=checkout-service  services=checkout-service,storefront-web  events=12 alerts=6 investigable=True
     group origin=payments-gateway  services=payments-gateway                 events=3  alerts=2 investigable=True
     group origin=orders-service    services=orders-service                   events=1  alerts=1 investigable=False
       x4 checkout-service  HighP99Latency                     <- first alert in the group
       x3 checkout-service  PodOOMKilled                       <- same service as origin checkout-service
       x2 storefront-web    High5xxRate                        <- calls checkout-service (downstream symptom)

$ POST /v1/alerts/groups/{id}/investigate
201  checkout-service p99 latency > 2000ms  status=PAUSED outcome=awaiting-approval tier=2
     hypothesis tree: validated (validated=deploy-regression/memory, agrees_with_pipeline=True)
       H1 VALIDATED    4/4  deploy-regression/memory
       H2 INVALIDATED  0/2  cert-expiry/tls  ruled out by ['tls-symptom']
       H3 INVALIDATED  0/3  dependency-or-capacity  ruled out by ['no-preceding-change', 'no-local-crash']
         [supported] A deployment shipped in the lookback window ['E3'] — v2025.08.07-3 deployed 2026-08-07T14:02:00Z
         [supported] The deploy precedes symptom onset ['E3', 'E2'] — deployed 2026-08-07T14:02:00Z; first breach 14:05Z
         [supported] The service's pods were OOMKilled ['E5'] — 2 OOMKilled event(s) in the bundle
         [supported] The shipped change raises a resource limit ['E4'] — commit c9a1f42 raises the connection-pool limit 20 -> 200

$ POST /v1/incidents/{id}/approvals   {decision: approve, fence}
200  outcome=remediated  executed=['rollback_deployment', 'create_revert_pr']  recovered_p99=182ms  audit={'count': 48, 'chain_verified': True}

$ POST /v1/incidents   {scenario: s6-deploy-after-breach}
201  outcome=escalated  diagnosis_standing=False
     review: reviewer rejected plan: failed checks ['temporal-precedence'] — escalate
       H1 INVALIDATED  3/4  deploy-regression/memory  ruled out by ['temporal-precedence']

$ POST /v1/incidents   {scenario: s7-heap-oom}
201  outcome=escalated  diagnosis_standing=False
       H1 INCONCLUSIVE 3/4  deploy-regression/memory
       gather next: heap and memory profiles for the new revision

$ POST /v1/incidents/{id}/feedback   {verdict: incorrect}
200  feedback=incorrect memory_updated=True  episode.verified=False  chain_verified=True

$ GET /   (response header)
Content-Security-Policy: default-src 'none'; script-src 'sha256…; style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
```

**What this proves:** 16 raw events become 3 investigable groups, each alert
with a stated reason; the investigation explains itself criterion by criterion
with citations; an escalation names the check that ruled the cause out (or the
evidence that would settle it) instead of only saying "insufficient evidence";
a human verdict reaches both the tamper-evident ledger and memory; and the
console is served under a policy that admits only its own scripts.

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
| Engineering quality | Proof 4 | ✅ 224 tests green in CI (at capture; 319 backend + 35 console tests as of 2026-11) |
| Storms collapse; reasoning is legible | Proof 6 | ✅ 16 events → 3 groups; hypothesis tree agrees with the pipeline on all 7 scenarios |

Every command above is in the `Makefile` / runnable with `python3 -m aetherops`.
No keys, no network, no cost — reproducible on any machine with Python 3.11+.
