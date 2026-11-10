# 20 — Operator console, alert intake, and the hypothesis tree

How an operator actually meets the platform: what arrives (alerts), what the
platform concluded and why (the hypothesis tree), what it wants to do (the plan
at the gate), and what it did (timeline, postmortem, audit ledger). This document
covers the reference implementation of those surfaces and the API behind them.

It inherits the canon of [00](00-executive-summary.md) and
[01](01-architecture.md): five planes, the Step Catalog, Evidence/Citation,
calibrated Confidence, the trust ladder. Nothing here changes *what may execute* —
gates, policy, the Reviewer and the audit ledger are exactly as specified in
[03](03-orchestration.md) and [05](05-security.md).

## 1. What was adopted from the field, and what was not

Before building, the open-source and commercial AI-SRE landscape was surveyed for
ideas worth taking. The rule for adoption: an idea had to make the platform's
*existing* guarantees more legible or more useful — not add a second, softer way
of reaching a conclusion.

| Idea | Seen in | What AetherOps does with it |
|---|---|---|
| **Hypothesis tree** — competing causes, each validated or invalidated against telemetry; an investigation may end *inconclusive* | [Datadog Bits AI SRE](https://docs.datadoghq.com/bits_ai/bits_ai_sre/investigate_issues/) | §2. Built as a **deterministic** differential diagnosis: falsifiable criteria per candidate cause, each citing the `[En]` evidence that decided it. Same predicates as the Reviewer's gate. |
| **Fingerprint deduplication and topology correlation** of alerts | [Keep](https://github.com/keephq/keep) ([dedup](https://docs.keephq.dev/overview/deduplication), [topology](https://docs.keephq.dev/overview/correlation-topology)) | §3. Sense-plane intake: identity fingerprints, a 15-minute correlation window, grouping along the declared service graph, and a *probable origin* per group. |
| **Alertmanager-native intake** — investigate what your monitoring already emits | [HolmesGPT](https://github.com/HolmesGPT/holmesgpt), Keep | §3. `POST /v1/alerts` accepts a Prometheus Alertmanager webhook unmodified, behind a least-privilege `ingest` role. |
| **Dependency graph and blast radius** as a first-class view | [Aurora](https://github.com/Arvo-AI/aurora) | §5. The service map overlays live alert pressure and open incidents on the graph that correlation and change-risk scoring already read. |
| **Investigate from the IDE** over MCP, read-only | [HolmesGPT](https://github.com/HolmesGPT/holmesgpt), Aurora, Rootly | §6. The MCP server gains `investigate`, which runs the workflow to — never past — the approval gate. |
| **Learning from operator feedback** | Cleric, [incident.io](https://incident.io/blog/introducing-ai-sre) | §4. A verdict is written to the audit chain and into memory: an episode marked incorrect stops counting as verified precedent. |
| **Investigation steps you can watch**, and reporting on time-to-conclusion | Bits AI SRE, [OpenSRE](https://github.com/Tracer-Cloud/opensre) | §4–5. The timeline is the audit ledger humanized; `/v1/stats` reports outcomes, time-to-diagnosis and noise reduction. |

Deliberately **not** adopted:

- **LLM-based alert correlation.** Grouping is what an operator trusts least and
  must be able to explain in one sentence. Rules over declared topology are
  explainable (every alert records *why* it joined); a model's clustering is not.
- **Autonomous remediation / auto-opened fix PRs.** Several tools ship these. The
  platform's position is unchanged: autonomy of investigation and proposal, never
  of execution ([16 §1](16-design-decisions.md)).
- **A long integration list.** Sixty connectors is a product milestone, not an
  architectural one. The two real adapters (GitHub, Prometheus) prove the seam.
- **A confidence percentage on each hypothesis.** The platform has one calibrated
  Confidence, defined in [02 §3](02-agents.md). The tree reports how many criteria
  held; inventing a second probability would dilute the first.

## 2. The hypothesis tree

`src/aetherops/reasoning/hypotheses.py`

The Root Cause agent writes one hypothesis in prose. That is enough to act on and
not enough to trust — least of all on the runs that escalate, where "insufficient
evidence" is the entire answer. The tree makes the reasoning explicit.

Each candidate cause is a set of **falsifiable criteria**. Every criterion is
evaluated against structured facts carried by the evidence bundle and resolves to
`supported`, `refuted` or `unknown`, naming the evidence it rests on:

| Hypothesis | Required criteria |
|---|---|
| **H1** `deploy-regression/memory` | a deployment shipped · it precedes symptom onset · pods were OOMKilled · the change raises a resource limit |
| **H2** `cert-expiry/tls` | TLS handshake failures present · no deployment correlates |
| **H3** `dependency-or-capacity` | no change before onset · no crash signature on the service's own pods · upstream telemetry shows degradation |

```
any required criterion refuted            -> INVALIDATED
every required criterion supported        -> VALIDATED
otherwise (evidence missing or ambiguous) -> INCONCLUSIVE
```

The design rules, each enforced by a test in `tests/test_hypotheses.py`:

1. **Deterministic and read-only.** No model call, no connector call, no audit
   append. The tree is a projection of the workflow's own record — the same
   contract as the postmortem builder — so it is safe to rebuild on every read.
2. **Explanation, never control flow.** The Reviewer, policy and the gate still
   decide what executes. The tree reports `pipeline.agrees`; with a
   nondeterministic live model a disagreement is possible, and it is surfaced as
   a warning rather than hidden.
3. **The same predicates as the gate.** Temporal precedence and mechanism
   direction live in `reasoning/signals.py` and are imported by both the Reviewer
   and the tree. The explanation cannot drift from the check that gates.
4. **Hostile evidence proves nothing.** QUARANTINED items are treated as
   unavailable, exactly as the model-facing digest treats them.
5. **Missing is `unknown`, never `refuted`.** A source that was not gathered
   cannot count against a hypothesis.
6. **An unknown says what would settle it.** Open hypotheses list the evidence to
   gather next. H3 can never validate — the platform collects no dependency
   telemetry and has no Step Catalog action for it — so on an uncorrelated latency
   spike the tree's answer is precisely *"gather upstream latency, saturation and
   request-rate metrics"*.

**The release-gate property:** on all seven golden scenarios the tree validates
exactly the adjudicated failure class, and validates *nothing* where the right
answer is to escalate. Removing the temporal check or the direction check flips
an adversarial scenario and fails the suite.

```
s6-deploy-after-breach   outcome=escalated
  H1 INVALIDATED  3/4  deploy-regression/memory  ruled out by temporal-precedence
     [refuted] The deploy precedes symptom onset [E3, E2]
               deployed 16:12; first breach 16:05 — the deploy landed after the symptom began
s7-heap-oom              outcome=escalated
  H1 INCONCLUSIVE 3/4  deploy-regression/memory
     gather next: heap and memory profiles for the new revision
```

### Structured evidence

To evaluate criteria without re-parsing prose, `Evidence` gained a `data` field:
the structured facts behind each summary (metric series, deploy records, the
commit diff, pod events). It is populated from connector results **after** the
gateway's redaction, is never part of a model prompt (the digest is still built
from summary and excerpt), and is what the console's symptom chart draws.

### A rejected diagnosis is not a diagnosis

In scenario s6 the model *does* diagnose a deploy regression; the Reviewer rejects
it. The incident document keeps the proposal on record (`diagnosis.status`,
`failure_class`) and adds `diagnosis.standing: false`. Lists, the stats endpoint
and the console count only standing diagnoses, so an escalation never appears in
"diagnosed failure classes".

## 3. Alert intake: dedup, then correlate

`src/aetherops/alerts/store.py` — the Sense plane's "alert normalizer, dedup/storm
suppression" ([01 §1](01-architecture.md)), in-memory and bounded.

An outage never arrives as one alert. The same monitor re-fires every evaluation
interval, pods page for OOM, and every caller pages for its own timeouts. Three
deterministic steps collapse that:

1. **Normalize.** The native shape, a native batch, or an Alertmanager v4 webhook
   as-is. Severity aliases are folded (`page` → `critical`); oversized fields are
   truncated; anything without a name and a service is refused with a 400.
2. **Deduplicate.** Identity is `sha256(source, service, name)`. A re-fire bumps a
   counter and `last_seen`; it can raise severity, never lower it. A `resolved`
   event closes the alert, so a later re-fire is a new one.
3. **Correlate.** A new alert joins an open group when it fired within the window
   *and* sits on the group's causal path — the same service as, a caller of, or a
   dependency of the group's origin. Otherwise it opens a group. A service the
   graph does not know correlates only with itself: the platform claims no
   topology it was not given.

The **probable origin** is the most upstream alerting service — the one none of
whose own dependencies are alerting. It is recomputed as alerts arrive, so the
symptom paging before the cause does not decide where the investigation starts.

```
POST /v1/alerts            16 raw events, three monitoring sources
202  received=16 deduplicated=7 new_alerts=9 new_groups=3   noise_reduction=0.812
     origin=checkout-service  services=checkout-service,storefront-web  events=12
       x4 checkout-service  HighP99Latency          <- first alert in the group
       x3 checkout-service  PodOOMKilled            <- same service as origin checkout-service
       x2 storefront-web    High5xxRate             <- calls checkout-service (downstream symptom)
     origin=payments-gateway  events=3      origin=orders-service  events=1
```

`POST /v1/alerts/groups/{id}/investigate` opens an incident for a group's origin.
Two honest constraints:

- **One group, one incident.** The group is claimed atomically before the workflow
  starts; a second request gets 409 with the existing incident's id.
- **No evidence, no investigation.** Connectors are snapshot fakes by default, so
  a group is `investigable` only where frozen evidence exists for its origin.
  Otherwise the request is refused with 422 and a reason — the platform does not
  fabricate a flat metrics series to have something to diagnose.

## 4. The incident document and the API

`src/aetherops/api/views.py` builds read models; `api/server.py` is the transport.

The transport is a **route table**: one row per endpoint with its method, path
template, required policy grant and summary. Dispatch, access control, rate
limiting, error mapping and the generated OpenAPI document all read that table, so
an endpoint cannot exist without declaring who may call it — a property the test
suite checks by walking every row anonymously.

| | |
|---|---|
| `POST /v1/alerts` · `GET /v1/alerts` · `POST …/groups/{id}/investigate` | intake, groups, promotion |
| `POST /v1/incidents` · `GET /v1/incidents` · `GET /v1/incidents/{id}` | start, history, the full document |
| `GET …/{id}/audit` · `GET …/{id}/postmortem` | ledger with hashes, generated postmortem |
| `POST …/{id}/approvals` · `POST …/{id}/feedback` | decide the gate (fenced, atomic); assess the diagnosis |
| `GET /v1/topology` · `/v1/stats` · `/v1/catalog` · `/v1/openapi.json` · `/v1/whoami` | map, reporting, governed surface, spec, grants |

The incident document adds to the original summary — every earlier key keeps its
name and meaning — `stages` (the stepper, read off the ledger so it also renders
mid-run), `timeline`, `hypotheses`, `signals`, `review_checks` (published *before*
a rejection is raised, so the checks that stopped a wrong rollback are readable),
`policy`, `executed`, `verification`, `blast_radius`, `similar_incidents`, and
`feedback`.

**Feedback closes a loop that already existed.** Recall and change-risk scoring
treat only *verified* episodes as precedent (audit H4). `POST …/feedback` appends
`human.feedback` to the incident's hash chain and, when the incident learned an
episode, sets `verified` to *what the verifier measured AND the human agrees* —
reversibly, so correcting a mistaken verdict restores the machine's result.

**Roles.** Two grants join `read` / `create` / `approve`:

| Grant | Roles | Why it is separate |
|---|---|---|
| `ingest` | ingest, operator, admin | The token that lives in an Alertmanager config can post alerts and do *nothing else* — not read them back, not open an incident. |
| `feedback` | operator, approver, admin | It writes to organizational memory, so viewers cannot do it. |

**Transport hardening** (all in `tests/test_api_console.py`):

- The console is served with a Content-Security-Policy that admits **exactly the
  scripts it ships**, each by sha256 — no `unsafe-inline`, no `unsafe-eval` for
  scripts — and locks everything else to same-origin; it cannot be framed.
- Malformed JSON is a 400. It used to read as an empty body, which on
  `POST /v1/incidents` silently started the canonical incident.
- An oversized body is a 413 (64 KB; 256 KB for alert batches), not a request
  with its decision dropped.
- Mutations are rate-limited per role (429 + `Retry-After`); reads are not.
- A handler bug is a 500 naming the request id — never the internals — with the
  traceback in the operator's log under the same id. A client hanging up
  mid-response is not an error.
- Every response carries `X-Request-Id`, `nosniff`, and `no-store`.

**Persistence.** With `AETHEROPS_DB` set, each incident's document is archived on
every state change, joining organizational memory and the audit chains. After a
restart the history is readable and each chain re-verifies. What is archived is
the *record*, not the workflow: an archived PAUSED incident is explicitly not
resumable (409), because its in-process state is gone — production resumes from
Temporal history ([03 §7](03-orchestration.md)).

## 5. The console

`web/` — React 19 + TypeScript, built by Vite into one self-contained file that
the stdlib API serves at `/` and GitHub Pages serves as the public demo. No
runtime dependencies beyond React; charts and the service map are hand-rolled SVG
so the file stays CSP-locked.

| View | Answers |
|---|---|
| **Overview** | How is the fleet doing — outcomes, time to diagnosis, noise removed, spend |
| **Alerts** | What paged, what it collapsed into, and why each alert was grouped |
| **Incidents** → detail | What happened, what was considered, what it wants to do, what it did |
| **Topology** | What breaks if this service does |
| **Change risk** | Would this diff have been stopped before it shipped |
| **Runbooks** · **Evaluation** · **Governance** | What it knows · whether it is releasable · what it is allowed to do |

Design choices worth defending:

- **Evidence is one click from every claim.** Each `[En]` in the narrative and on
  every criterion is a button that lights up the matching row in the bundle.
- **The symptom is drawn the way the runbook says to draw it:** the latency
  series with the deploy and pod events on the same clock, the 2× baseline line
  that defines onset, and a table view behind a toggle.
- **Escalation is not styled as failure.** It gets its own outcome, its own
  colour, and a banner that says the platform declined to act.
- **The role is real.** `/v1/whoami` drives the UI: a viewer sees the gate and
  cannot decide it; the view remounts when the token changes.
- **Status never rides on colour alone** — icon and label always accompany it.
  Light and dark are both first-class; the OS picks, the toggle overrides.

### LIVE and DEMO are the same code

Views talk to a `Client` interface. `liveClient` drives the API; the demo client
replays `web/src/demo-data.json`, which `tools/record_demo.py` captures by booting
the real server and driving it over HTTP. Every document the public demo shows is
a response the server actually returned. Each pausable scenario is recorded both
ways from two genuine runs, so the denied document carries its own incident id
rather than a rewritten one.

Two tests keep the artifacts honest: the API-served console and the Pages site
must be the same build, and the recorded transcript must match the API's current
shape. A stale recording fails CI instead of failing on the public demo.

## 6. MCP

`python3 -m aetherops.mcp` gains four read-only tools: `list_scenarios`,
`investigate`, `score_change`, `service_topology`. `investigate` runs the incident
workflow to the approval gate and returns the diagnosis, hypothesis tree, cited
evidence and proposed plan. It is read-only by construction rather than by
promise: every node before the gate is a read ([orchestration/dag.py](../src/aetherops/orchestration/dag.py)),
and the server exposes no tool that decides a gate.

## 7. Running it

```bash
make serve          # API + console on http://127.0.0.1:8080 (dev token, loopback)
make check          # everything CI runs: test, eval, web-test
make record-demo    # re-record the demo transcript from the real API
make web            # rebuild the console into the API and the Pages site
```

Point an existing Alertmanager at it:

```yaml
receivers:
  - name: aetherops
    webhook_configs:
      - url: http://127.0.0.1:8080/v1/alerts
        http_config:
          authorization: { credentials: "<AETHEROPS_INGEST_TOKEN>" }
```

## 8. Honest scope

- **Three hypotheses, two diagnosable classes.** The tree covers the failure
  classes the reference implementation can ground. It is a pattern for making
  grounding legible, not a general root-cause engine.
- **Correlation is single-process and in-memory.** Groups are not persisted;
  durability belongs to the incidents they become.
- **Investigations replay frozen evidence** unless the real GitHub/Prometheus
  adapters are configured. The console says so on every incident and refuses
  groups it has no evidence for.
- **The console was verified in Chrome** (scripted end-to-end walkthroughs in
  LIVE and DEMO mode, light and dark, desktop and 390 px, with zero console
  errors or CSP violations). Other browsers were not exercised.
