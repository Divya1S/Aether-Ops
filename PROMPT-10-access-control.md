# Refined Prompt — Milestone 10: Access Control Before the Model (self-issued)

Inspired by the **ReviewOps Agent** capstone (Google×Kaggle AI Agents
Intensive), whose published thesis is the line worth stealing:
*"access control runs before the model."* Authorization there is not a
filter on model output — it decides what evidence may enter the prompt at
all, with data-subject consent gating collection. AetherOps designed the
same idea (docs/05 §2 RBAC, §5 classification stamping) and left the seams
dormant in code: `Evidence.classification` exists but only quarantine uses
it; connectors accept a `principal` they never check; the API has one
token and no roles. This milestone makes the design real.

## The prompt

```text
MISSION
Enforce authorization before the model, at three layers, without breaking
a single existing behavior: (1) classification-gated prompts — evidence
above the model's clearance never enters a prompt; (2) principal-gated
writes — only the Control plane's executor may invoke write-risk tools;
(3) role-gated API — viewer / operator / approver / admin tokens.

OPERATING RULES
1. Pre-model, not post-hoc. The evidence digest withholds any item whose
   classification exceeds the model clearance (env, default INTERNAL;
   order PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED), preserving [En]
   numbering exactly as quarantine does. Slack discussion evidence is
   born CONFIDENTIAL — people's words get the strictest default, the
   consent-shaped instinct from ReviewOps.
2. Humans keep full visibility. The audit ledger and postmortems are the
   human record — classification gates model prompts only. Quarantine
   (hostile) outranks classification (sensitive).
3. Deterministic write-guard at the connector gateway: any tool with
   risk >= MEDIUM called by a principal other than "executor" is denied
   and audited (tool.denied). Even a fully compromised agent cannot
   invoke a write — LLM06 hardened from policy to mechanism.
4. API roles: admin (back-compat default token), operator (create/run),
   approver (decide gates), viewer (read-only). Role checks are a policy
   table, not scattered ifs.
5. Nothing breaks: all 108 existing tests stay green (the two connector
   tests exercising write tools act as "executor" now — the authorized
   caller), demos byte-equivalent, eval metrics unchanged.

DELIVERABLES
- context digest clearance gating; CONFIDENTIAL discussion evidence
- connector write-guard + tool.denied audit; policy role table
- API token→role registry with per-route guards
- tests/test_access_control.py: clearance gating on/off, unauthorized
  write denial (and executor allowed), viewer 403s, audit trail
- README + docs/05 §11 rows updated honestly

QUALITY BAR
[ ] make test green (old + new); make eval byte-identical; demos unchanged
[ ] A CONFIDENTIAL item provably absent from the RCA prompt yet present
    in the postmortem's human-facing evidence table
```
