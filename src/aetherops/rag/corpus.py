"""Seed knowledge corpus: the platform's own operational runbooks.

Production ingests these from Confluence/Git (docs/06 §4); the reference
implementation ships them as data so retrieval — and its evaluation — runs
anywhere. Generated postmortems are ingested at runtime on top of this seed
(docs/17 M7: incident N's writeup is retrievable context for incident N+1).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    kind: str                 # "runbook" | "postmortem"
    text: str
    metadata: dict = field(default_factory=dict)


SEED_RUNBOOKS: list[Document] = [
    Document(
        id="runbook-rollback", title="Rolling back a bad deployment",
        kind="runbook", text=(
            "When a deployment is the suspected cause of an incident, roll "
            "back to the previous known-good revision first and investigate "
            "second. Use the deployment history to identify the prior "
            "revision, execute the rollback, and watch the golden signals "
            "(latency, error rate, saturation) for at least ten minutes.\n\n"
            "After service recovery, open a revert pull request for the "
            "offending commit so the permanent fix goes through code review. "
            "Never leave a rolled-back service without a revert PR: the next "
            "deploy would re-ship the regression.")),
    Document(
        id="runbook-oom", title="Debugging OOMKilled pods",
        kind="runbook", text=(
            "OOMKilled events mean a container exceeded its memory limit. "
            "Check for recent changes to memory-hungry configuration: cache "
            "sizes, JVM heap flags, and especially database connection pool "
            "sizes — each pooled connection holds buffers on both client and "
            "server, so a pool increase multiplies memory per pod.\n\n"
            "Correlate the first OOMKilled timestamp with deploys and config "
            "changes. If a change correlates, roll it back before tuning "
            "limits: raising the memory limit to accommodate a regression "
            "hides the defect and raises the fleet's cost.")),
    Document(
        id="runbook-conn-pool", title="Connection pool sizing guidance",
        kind="runbook", text=(
            "Size database connection pools from measured concurrency, not "
            "hope: pool size of roughly ((core_count * 2) + effective_spindle "
            "count) per instance is a sane starting point. Each idle "
            "connection consumes server memory and a file descriptor.\n\n"
            "Large pool increases must ship behind the change-risk review: "
            "a pool raised from tens to hundreds multiplies per-pod memory "
            "and can trigger OOMKilled cascades under load. Prefer queueing "
            "at the application layer over unbounded pool growth.")),
    Document(
        id="runbook-latency", title="Investigating p99 latency spikes",
        kind="runbook", text=(
            "For a sudden p99 latency regression, first ask what changed: "
            "deploys, config flags, dependency versions, traffic mix. Pull "
            "the deployment timeline and overlay it on the latency series — "
            "a spike that starts at a deploy boundary is a deploy regression "
            "until proven otherwise.\n\n"
            "If nothing changed, look for saturation: CPU throttling, "
            "connection pool exhaustion, downstream dependency slowness, and "
            "garbage-collection pauses. Check whether pod restarts or "
            "OOMKilled events are shrinking effective capacity, which raises "
            "latency on the survivors.")),
    Document(
        id="runbook-cert", title="TLS certificate expiry incidents",
        kind="runbook", text=(
            "Certificate expiry presents as sudden TLS handshake failures, "
            "SSL errors in client logs, and error-rate spikes with no "
            "deploy correlation. Confirm with openssl s_client and check "
            "the certificate's notAfter date.\n\n"
            "Mitigate by renewing and re-issuing the certificate, then "
            "rotating it into the load balancer or secret store. Follow up "
            "with expiry monitoring at 30/14/7 days and automated renewal "
            "so the class of incident is retired.")),
    Document(
        id="runbook-dns", title="DNS resolution failures in the cluster",
        kind="runbook", text=(
            "Cluster DNS failures look like intermittent connection "
            "timeouts, name-resolution errors, and errors that span many "
            "unrelated services at once. Check CoreDNS pod health and "
            "restarts first, then upstream resolver reachability.\n\n"
            "Watch for ndots-related lookup storms from misconfigured "
            "search domains, and for conntrack table exhaustion on nodes. "
            "Mitigations: scale CoreDNS, enable NodeLocal DNSCache, and fix "
            "the client's resolv.conf options.")),
    Document(
        id="runbook-disk", title="Disk pressure and eviction storms",
        kind="runbook", text=(
            "Node disk pressure triggers kubelet evictions, which present "
            "as pods terminating across a node with Evicted status. Check "
            "node filesystem usage, image cache growth, and runaway log "
            "files.\n\n"
            "Mitigate by pruning unused images, enforcing log rotation, and "
            "moving noisy writers to bounded volumes. If evictions killed "
            "stateful pods, verify data integrity before returning the node "
            "to service.")),
    Document(
        id="runbook-canary", title="Canary rollout policy",
        kind="runbook", text=(
            "Risky changes ship through canary stages: 1%, 10%, 50%, 100% "
            "of traffic, each held long enough to observe golden signals. "
            "Automatic abort triggers on error-rate or latency SLO breach "
            "and rolls traffic back to the stable revision.\n\n"
            "A change flagged HIGH risk by change review must not skip "
            "canary stages. Manual promotion requires the service owner's "
            "approval recorded in the deploy tooling.")),
    Document(
        id="runbook-freeze", title="Change freeze windows",
        kind="runbook", text=(
            "During declared freeze windows (peak trading days, major "
            "launches), non-LOW-risk changes are blocked by policy. The "
            "freeze is enforced at change admission, not by convention.\n\n"
            "Break-glass exceptions require an incident-mitigation "
            "justification and a tier-3 approval, and are audited. Routine "
            "work should be rescheduled rather than exempted.")),
    Document(
        id="runbook-escalation", title="Escalation and paging policy",
        kind="runbook", text=(
            "SEV1 means customer-facing outage: page the on-call, the "
            "service owner, and the incident commander immediately. SEV2 "
            "means degraded experience: page the on-call and open an "
            "incident channel. SEV3/4 are business-hours follow-ups.\n\n"
            "When automation cannot establish a grounded diagnosis, it "
            "escalates to a human with the partial evidence bundle attached "
            "— escalation with good context is a success path, not a "
            "failure.")),
]
