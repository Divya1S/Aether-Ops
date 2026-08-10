# 04 — Enterprise Connectivity: The MCP Connector Gateway

The Execution plane in one document. The connector gateway is the **only**
egress path from AetherOps to enterprise systems: every read the Retrieval
Service performs and every write the workflow engine invokes crosses this
boundary. No other plane holds credentials to external systems
([01-architecture.md](01-architecture.md) §1), and only the Control plane may
invoke writes through it. Security properties of this boundary are specified
in [05-security.md](05-security.md); failure behavior in
[11-failure-handling.md](11-failure-handling.md).

> **Reference implementation.** The default connectors serve a frozen
> `Snapshot` (deterministic demos/evals). But the connector interface is a
> real seam, not a mock-only one: `connectors/adapters.py` ships **real HTTP
> adapters** — `GitHubConnector` (deployments + commit diffs over the free
> GitHub REST API; the two write tools are dry-run so it never mutates a repo)
> and `PrometheusConnector` (the metrics slot, mapping a range query to the
> agents' series shape). `build_live_registry` swaps them in where env-
> configured (`AETHEROPS_GITHUB_REPO`, `AETHEROPS_PROMETHEUS_URL`) and falls
> back to the fakes otherwise; `GET /v1/connectors` reports the roster. Real
> calls still cross the same gateway (rate-limit → cache → redact → audit),
> and the adapters are unit-tested against mocked HTTP so CI stays
> network-free.

---

## 1. Connector abstraction

Every integration is an **MCP server** exposing **typed tools**. A central
**Connector Gateway** fronts all of them. Three layers, strictly ordered:

```
 Control / Intelligence planes
        │  ToolCall {tool, args, principal, workflow_id, budget}
        ▼
 ┌─────────────────────────────────────────────────────────┐
 │ GATEWAY LAYER      authN/Z (OPA) · quota · cache ·      │
 │                    audit emit · redaction · result caps │
 ├─────────────────────────────────────────────────────────┤
 │ CONTRACT LAYER     ToolSpec: name · JSON Schema args/   │
 │                    result · scopes · risk class ·       │
 │                    cacheability · rate class · idempot. │
 ├─────────────────────────────────────────────────────────┤
 │ ADAPTER LAYER      per-system API mapping, pagination,  │
 │                    auth dance, error normalization      │
 └─────────────────────────────────────────────────────────┘
        │  mTLS, egress allowlist
        ▼
 Enterprise system of record (GitHub, Datadog, K8s, …)
```

### 1.1 Adapter layer

One adapter per external system, packaged as a sandboxed MCP server process
(§7). The adapter owns everything idiosyncratic: REST vs. GraphQL vs. gRPC,
pagination cursors, upstream error taxonomies, API-version drift. Nothing
above the adapter ever sees a raw upstream response — adapters normalize into
the contract layer's result schemas, including a normalized error envelope
(`retryable`, `rate_limited`, `auth_failed`, `not_found`, `upstream_5xx`).

### 1.2 Contract layer

Every tool a connector exposes is declared by a **ToolSpec**, registered and
versioned alongside the Step Catalog:

| ToolSpec field | Meaning |
|---|---|
| `name` | Globally unique, `connector.verb_object` (e.g., `k8s.rollback_deployment`) |
| `args_schema` | JSON Schema; gateway rejects non-conforming calls before egress |
| `result_schema` | JSON Schema; gateway rejects non-conforming results before ingress (§7) |
| `scopes` | Minimum downstream credential scopes required (§4) |
| `risk_class` | `READ` / `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `cacheability` | TTL class or `never` (§5) |
| `rate_class` | Token-bucket class (§6) |
| `idempotency` | `idempotent`, `idempotent_with_key`, or `effectful` |

Write tools (risk class above `READ`) additionally exist as Step Catalog
actions with OPA policy, approval tier, and compensation handler
([03-orchestration.md](03-orchestration.md)). A write tool with no Step
Catalog entry is unreachable by construction: the gateway refuses any
non-`READ` call whose caller is not the Control plane's workflow engine
executing a registered step.

### 1.3 Gateway layer

The single enforcement point: authenticates the calling plane (SPIFFE
identity), authorizes the call via OPA (§4), applies quotas (§6), serves or
fills cache (§5), emits the audit record and OTel span
([09-observability.md](09-observability.md)), runs the redaction pipeline on
results before they leave the Execution plane
([05-security.md](05-security.md) §5), and stamps every result with a
classification and quarantine label.

### 1.4 Design decision: MCP over bespoke SDK integrations

- **Why chosen:** (a) a *uniform tool contract* — agents and the Plan
  Compiler consume one calling convention across 25+ systems, so adding a
  connector never touches agent code; (b) *ecosystem reuse* — vendors and the
  community ship MCP servers we can wrap rather than rewrite, and internal
  teams can contribute connectors against a public spec; (c) *sandboxable
  processes* — an MCP server is a separate process with a narrow stdio/HTTP
  interface, which is exactly the unit we want to isolate (§7); an in-process
  SDK call is not.
- **Alternatives considered:** (a) direct SDK calls from agent code —
  fastest to prototype, but scatters credentials into the Intelligence plane
  and makes the egress invariant unenforceable; rejected on security grounds
  alone. (b) A bespoke internal connector RPC framework — equivalent
  isolation, but we would own the spec, the client libraries, and every
  connector with zero external reuse. (c) An off-the-shelf iPaaS
  (Workato-class) — good breadth, but opaque execution, no typed risk
  classes, and a third party in the credential path.
- **Trade-offs:** MCP adds a process hop (~3–8 ms p50 intra-cell) and the
  protocol is younger than REST — we pin protocol versions per connector and
  conformance-test in CI. Community servers are a supply-chain surface: every
  third-party server is forked into our registry, pinned, scanned, and runs
  under the same sandbox and egress allowlist as first-party code.
- **Operational implications:** connectors are deployed and scaled
  independently of the gateway; a crashing adapter affects one integration,
  not the plane. Connector on-call owns adapters; platform on-call owns the
  gateway.

---

## 2. Connector matrix

All connectors ship read tools; write tools exist only where a remediation
verb justifies them, and every write tool is a Step Catalog action. "Highest
write risk class" is the ceiling declared in ToolSpecs — OPA can only
restrict further, never widen.

| Connector | Category | Example read tools | Example write (Step Catalog) tools | Highest write risk class | Notes |
|---|---|---|---|---|---|
| GitHub | SCM / code host | `github.get_commit`, `github.diff_range`, `github.list_deployments` | `github.create_revert_pr`, `github.comment_pr`, `github.merge_pr` | HIGH | Revert PRs open as drafts; `merge_pr` restricted to reverts with passing checks |
| GitLab | SCM / code host | `gitlab.get_mr`, `gitlab.diff_range`, `gitlab.list_pipelines` | `gitlab.create_revert_mr`, `gitlab.comment_mr` | HIGH | Mirrors GitHub verbs; per-group app installs |
| Bitbucket | SCM / code host | `bitbucket.get_pr`, `bitbucket.get_commit` | `bitbucket.create_revert_pr`, `bitbucket.comment_pr` | HIGH | Lower API rate ceilings; aggressive caching (§5) |
| Jira | Ticketing | `jira.get_issue`, `jira.search_issues` | `jira.create_issue`, `jira.transition_issue`, `jira.link_incident` | LOW | JQL from a vetted template library only — never model-composed |
| Linear | Ticketing | `linear.get_issue`, `linear.search_issues` | `linear.create_issue`, `linear.update_status` | LOW | GraphQL adapter; webhook-driven cache invalidation |
| Slack | ChatOps | `slack.get_thread`, `slack.search_messages` | `slack.post_message`, `slack.post_approval_card` | LOW | Approval cards are Control-plane surfaces ([05-security.md](05-security.md) §9) |
| Microsoft Teams | ChatOps | `teams.get_thread`, `teams.search_messages` | `teams.post_message`, `teams.post_approval_card` | LOW | Graph API adapter; same card schema as Slack |
| Confluence | Knowledge | `confluence.get_page`, `confluence.search` | `confluence.create_page`, `confluence.update_runbook_draft` | LOW | Postmortem/runbook drafts land in a review space, never published pages |
| Notion | Knowledge | `notion.get_page`, `notion.search` | `notion.create_page` | LOW | Draft-only writes, same policy as Confluence |
| Datadog | Observability | `datadog.query_metrics`, `datadog.query_logs`, `datadog.get_monitor` | `datadog.mute_monitor`, `datadog.create_downtime` | MEDIUM | Mutes are TTL-bounded and auto-expire; unbounded mute is not a tool |
| Grafana | Observability | `grafana.query_datasource`, `grafana.get_dashboard` | `grafana.create_annotation`, `grafana.silence_alert` | MEDIUM | Annotations mark remediation events for humans |
| Prometheus | Observability | `prometheus.instant_query`, `prometheus.query_range` | `alertmanager.create_silence` | MEDIUM | Silences TTL-bounded; PromQL from vetted templates |
| PagerDuty | Incident mgmt | `pagerduty.get_incident`, `pagerduty.get_oncall` | `pagerduty.ack_incident`, `pagerduty.add_responder`, `pagerduty.resolve_incident` | MEDIUM | `resolve_incident` allowed only after Verifier success ([02-agents.md](02-agents.md)) |
| Splunk | Logs | `splunk.search_template` | — | READ | Deliberately read-only; SPL from vetted templates with bounded time ranges |
| Elastic | Logs | `elastic.search_template` | — | READ | Same posture as Splunk; DSL templates only |
| AWS | Cloud | `aws.describe_asg`, `aws.get_cloudwatch_metrics`, `aws.describe_rds` | `aws.scale_asg`, `aws.update_ecs_service`, `aws.failover_rds` | CRITICAL | No delete-class verbs exist in any ToolSpec; IRSA identity (§3) |
| Azure | Cloud | `azure.get_vmss`, `azure.query_monitor` | `azure.scale_vmss`, `azure.restart_app_service`, `azure.swap_deployment_slots` | CRITICAL | Workload Identity Federation; slot swap is the rollback primitive |
| GCP | Cloud | `gcp.get_mig`, `gcp.query_monitoring` | `gcp.resize_mig`, `gcp.rollback_cloud_run_revision` | CRITICAL | Workload Identity Federation; per-project scoping |
| Kubernetes | Runtime | `k8s.get_events`, `k8s.get_pod_logs`, `k8s.describe_deployment` | `k8s.rollback_deployment`, `k8s.scale_deployment`, `k8s.restart_rollout`, `k8s.cordon_node` | CRITICAL | Per-cluster RBAC service accounts; no `delete` verbs; drain is CRITICAL |
| ArgoCD | GitOps CD | `argocd.get_app`, `argocd.get_sync_status`, `argocd.diff` | `argocd.sync_app`, `argocd.rollback_app` | HIGH | Preferred K8s write path where GitOps owns the cluster — rollback stays declarative |
| Terraform | IaC | `terraform.get_state_summary`, `terraform.preview_plan` | `terraform.apply_saved_plan` | CRITICAL | Applies only *saved, human-reviewed* plan files; never model-composed HCL |
| CircleCI | CI | `circleci.get_pipeline`, `circleci.get_test_results` | `circleci.rerun_workflow`, `circleci.cancel_workflow` | MEDIUM | Rerun is idempotent-with-key |
| GitHub Actions | CI | `gha.get_run`, `gha.get_job_logs` | `gha.rerun_run`, `gha.cancel_run`, `gha.dispatch_workflow` | HIGH | `dispatch_workflow` is HIGH because org deploy pipelines hang off it |
| Buildkite | CI | `buildkite.get_build`, `buildkite.get_job_log` | `buildkite.retry_build`, `buildkite.cancel_build` | MEDIUM | Log reads size-capped at gateway (§7) |
| Jenkins | CI | `jenkins.get_build`, `jenkins.get_console_log` | `jenkins.trigger_job`, `jenkins.abort_build` | HIGH | `trigger_job` limited to an allowlisted job set per tenant; legacy auth handled in adapter |

Coverage guarantees are per-tenant: a tenant enables only the connectors it
uses, and OPA scope maps (§4) are provisioned per tenant at onboarding.

---

## 3. Authentication

### 3.1 Credential isolation

All upstream credentials live in HashiCorp Vault, namespaced
`tenants/<tenant>/connectors/<connector>` — per-tenant isolation is a Vault
namespace boundary, not a naming convention. The gateway layer is the only
component with a Vault role that can read connector secrets; adapters receive
short-lived credentials by injection at call time and never persist them.
Vault runs per cell ([01-architecture.md](01-architecture.md) §7), so a cell
compromise cannot yield another cell's credentials.

### 3.2 Credential type ladder (strongest available wins)

| Preference | Mechanism | Used for |
|---|---|---|
| 1 | Cloud workload identity — IRSA (AWS), Workload Identity Federation (GCP/Azure) | AWS, Azure, GCP, EKS/GKE/AKS clusters |
| 2 | OAuth2/OIDC app installation, per-tenant install, minimal scopes | GitHub, GitLab, Slack, Teams, Jira, Linear, PagerDuty, Confluence, Notion, Datadog |
| 3 | Vault dynamic secrets (generated per lease, auto-revoked) | Databases, Jenkins, systems with Vault engines |
| 4 | Static token in Vault, 90-day forced rotation, scoped service account | Legacy systems only; each instance carries a tracked exception |

- **Why chosen:** workload identity eliminates the stored-secret class
  entirely for the highest-risk connectors (the clouds); OAuth app installs
  give tenant admins a revocation lever they already understand.
- **Alternatives considered:** uniform static service accounts everywhere —
  operationally simple, rejected because a single Vault compromise would
  yield long-lived cloud keys; per-user credential passthrough — rejected as
  primary mechanism because workflows outlive user sessions (but see
  on-behalf-of semantics, §4.3).
- **Trade-offs:** four mechanisms to operate instead of one; the ladder is
  encoded in connector onboarding checklists and verified by a nightly
  conformance job that flags any connector below its best-available rung.
- **Operational implications:** token lifetimes are 15 min (workload
  identity), ≤ 1 h (OAuth access tokens, refresh in gateway), ≤ 24 h (dynamic
  secrets). Rotation is automatic; rotation failure pages connector on-call
  before expiry, and the gateway degrades that connector to cache-only reads
  rather than serving with stale credentials.

### 3.3 Transport

Gateway ↔ MCP server links are mTLS with SPIFFE/SVID identities on both ends
([05-security.md](05-security.md) §2); MCP server → upstream uses the
upstream's TLS plus the injected credential. Plaintext egress is blocked at
the network policy layer regardless of adapter code.

---

## 4. Authorization

Two independent levels; both must pass.

### 4.1 Level 1 — platform decision (OPA)

Before egress, the gateway asks OPA: *may this principal (workflow + agent +
initiating tenant) call this tool with these args now?* Inputs include the
ToolSpec risk class, the agent's declared tool scope
([02-agents.md](02-agents.md)), tenant, environment, freeze windows, and for
writes the workflow's approval state. Deny is fail-closed and audited. Policy
detail and Rego examples: [05-security.md](05-security.md) §3.

### 4.2 Level 2 — downstream least privilege

The credential the adapter receives is itself scoped to the minimum the
connector needs, so even a bypassed or buggy gateway policy cannot exceed the
credential's ceiling. Each connector maintains a **scope map** from platform
capabilities to upstream scopes, reviewed at onboarding and re-audited
quarterly. Excerpts:

| Connector | Platform capability | Upstream scope granted |
|---|---|---|
| GitHub | read commits/PRs | `contents:read`, `pull_requests:read` |
| GitHub | open revert PR | `contents:write`, `pull_requests:write` (no `administration`) |
| Kubernetes | rollback deployment | RBAC Role: `deployments: get,list,patch` in namespace only |
| AWS | scale ASG | IAM: `autoscaling:SetDesiredCapacity` on tagged ASGs only |
| Datadog | mute monitor | `monitors_downtime` (no `monitors_write`) |
| PagerDuty | ack/resolve | `incidents.write` (no `users.write`, no `schedules.write`) |

**Why two levels:** defense in depth against exactly the failure modes that
matter — a policy bug (level 2 still holds) and a leaked credential (level 1
never authorized the call, and the credential alone is scope-capped).
**Trade-off:** scope maps drift as upstream APIs evolve; the quarterly
re-audit plus a CI diff against vendor scope catalogs keeps them honest.

### 4.3 On-behalf-of semantics

When a human approves a gated write, the audit record and the downstream call
both carry the approver: the ToolCall is annotated
`on_behalf_of: <approver-identity>`, and where the upstream supports it
(GitHub, Jira, PagerDuty) the action is attributed via the approver's OAuth
grant rather than the service identity. Upstream audit logs then show *who
approved*, not just "the platform did it." Where attribution is unsupported,
the gateway writes the approver into the action's annotation field (K8s
annotations, Datadog downtime message) as a fallback.

---

## 5. Caching

Read-through, two tiers, entirely inside the gateway layer:

| Tier | Store | Holds | Eviction |
|---|---|---|---|
| Hot | Redis (per cell) | Serialized ToolResults, keyed as below | TTL + LRU |
| Warm | Postgres (per cell) | Immutable-class results (commits, closed CI runs) and Evidence excerpts already cited | TTL by class; cited Evidence retained per [12-apis-and-storage.md](12-apis-and-storage.md) |

### 5.1 TTL classes

| TTL class | TTL | Example tools |
|---|---|---|
| `immutable` | forever (content-addressed) | `github.get_commit`, closed CI runs, `terraform.get_state_summary` at a version |
| `metrics` | 60 s | `datadog.query_metrics`, `prometheus.query_range` |
| `tickets` | 5 m | `jira.get_issue`, `linear.get_issue`, `pagerduty.get_incident` |
| `topology` | 15 m | `k8s.describe_deployment`, `aws.describe_asg` |
| `chat` | 2 m | `slack.get_thread`, `teams.get_thread` |
| `never` | — | anything with risk class above READ; log searches (result sets too large and query-specific) |

### 5.2 Cache key discipline

Key = `hash(tool, canonical_args, tenant, principal_scope_set, tool_version)`.
Including the **principal's effective scope set** is mandatory: without it, a
narrowly-scoped agent could read a result cached by a broadly-scoped one —
privilege leakage through the cache. The cost is a lower hit rate across
principals; we accept it, and recover most of the loss because the dominant
callers (Retrieval Service worker pools) share a scope set per tenant.

### 5.3 Invalidation

Webhooks the Sense plane already receives (push, PR update, ticket change,
monitor state change) are teed to the gateway as invalidation events keyed by
resource URI, so caches converge faster than their TTLs during active
incidents. TTL remains the correctness backstop — invalidation is an
optimization, never load-bearing.

**Alternatives considered:** a shared cross-tenant cache keyed only by tool +
args (higher hit rate, rejected — cross-tenant leakage is a non-starter);
per-agent in-process caches (rejected — no central invalidation, no audit of
cache serving). **Operational implications:** cache hit rate per tool class
is a first-class SLI; `immutable`-class hit rate below ~85% usually signals
an adapter emitting non-canonical args.

---

## 6. Rate limiting and quotas

### 6.1 Mechanisms

| Mechanism | Granularity | Purpose |
|---|---|---|
| Token buckets | (tenant, connector, rate class) | Fairness between tenants; protect upstream API quotas |
| Adaptive backoff | per connector | Honor upstream 429/`Retry-After`; multiplicatively shrink bucket refill on sustained 429s, recover additively |
| Per-workflow tool budgets | workflow instance | The workflow engine attaches a budget (calls and cost) to each ToolCall; the gateway enforces it — a runaway investigation cannot exhaust a tenant's GitHub quota |
| Storm mode | cell | Entered on alert-storm or upstream brownout signals |

Rate classes (`interactive`, `bulk_read`, `write`) come from the ToolSpec;
`write` buckets are small by design because writes are rare, gated, and never
latency-critical at bucket granularity.

### 6.2 Storm-mode load shedding

In storm mode, reads degrade to cache: the gateway serves stale-while-storm
results with an explicit `staleness` field on the ToolResult, and Evidence
built from them carries that staleness in its citation. **Writes are never
shed silently.** A write that cannot proceed fails loudly to the workflow
engine, which pauses the step, surfaces the condition on the approval card if
one is pending, and escalates per
[11-failure-handling.md](11-failure-handling.md). A silently dropped
mitigation is strictly worse than a visibly delayed one.

**Why token buckets over global concurrency limits:** buckets isolate
tenants from each other and map directly onto upstream per-org quotas;
a global limit lets one tenant's storm consume everyone's headroom.
**Trade-off:** bucket parameters per (connector, tenant) are configuration
that must be tuned; defaults derive from the upstream plan tier recorded at
connector onboarding, and the cost-metering service
([13-cost-model.md](13-cost-model.md)) flags buckets that persistently
saturate.

---

## 7. Tool execution and sandboxing

MCP server processes are untrusted-by-default workloads, because they parse
attacker-influenceable external content and (via forked community servers)
carry supply-chain risk.

| Control | Implementation |
|---|---|
| Process isolation | gVisor-class sandbox per MCP server pod (Firecracker microVMs for connectors parsing high-risk formats); no shared filesystem with the gateway |
| Egress allowlist | Per-connector network policy: the GitHub server can reach `api.github.com` and the tenant's GHES hosts, nothing else — exfiltration from a compromised adapter has nowhere to go |
| Result size caps | Per-ToolSpec cap (default 1 MiB, log tools 4 MiB); oversized results are truncated at a structural boundary with an explicit `truncated` marker, never silently |
| Result schema validation | Gateway validates every result against `result_schema` before it enters any plane; non-conforming results are rejected and audited, not repaired |
| Quarantine labeling | Every result is wrapped as quarantined external content — data, never instructions — before any agent or model sees it; the enforcement contract is [05-security.md](05-security.md) §6 |

- **Why chosen:** gVisor gives syscall-surface reduction at near-container
  density — the right default for 25+ connector fleets per cell; Firecracker
  is reserved for the adapters where parser exploits are the realistic threat.
- **Alternatives considered:** plain containers with seccomp (insufficient
  against kernel-surface escapes for code we forked from third parties);
  Firecracker everywhere (strongest isolation, but the density and cold-start
  cost across every connector isn't justified when most adapters only speak
  TLS to one API).
- **Trade-offs:** gVisor costs ~10–25% syscall-heavy throughput; irrelevant
  for I/O-bound API adapters. Some upstream SDKs misbehave under gVisor —
  adapters are conformance-tested in-sandbox in CI, not just on developer
  machines.
- **Operational implications:** sandbox escapes are treated as SEV1 platform
  incidents; connector pods are rebuilt and redeployed weekly from pinned
  bases regardless of change activity, so drift between image and registry
  cannot accumulate.

Result flow, end to end:

```
 adapter result ─► schema validation ─► size cap ─► redaction pipeline (05 §5)
   ─► classification stamp ─► quarantine wrap ─► cache write (if cacheable)
   ─► audit record ─► ToolResult to caller
```

Every stage is fail-closed: a result that cannot be validated, redacted, or
classified is dropped with an audited error, and the calling workflow decides
whether to retry, degrade, or escalate — the gateway never passes unvetted
content upward.
