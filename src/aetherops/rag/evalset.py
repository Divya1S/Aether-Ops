"""Labeled retrieval dataset (docs/17 acceptance #5): 36 operator-phrased
queries hand-labeled with the relevant seed runbooks. The unit of relevance
is the document — chunking strategies compete on surfacing the right doc,
which is exactly what the comparison measures.

The last six are a PARAPHRASE subset: deliberately vocabulary-divergent from
the target runbook, so the metric measures generalization rather than lexical
overlap. A purely lexical (TF-IDF) retriever is expected to miss them — that
honesty is the point, and the bootstrap CI lower-bound gate absorbs it.
"""
from __future__ import annotations

# (query, relevant doc ids)
LABELED_QUERIES: list[tuple[str, list[str]]] = [
    ("pods getting OOMKilled after a deployment",
     ["runbook-oom", "runbook-rollback"]),
    ("how should I size a database connection pool",
     ["runbook-conn-pool"]),
    ("p99 latency spiked right after a release",
     ["runbook-latency", "runbook-rollback"]),
    ("roll back a bad deploy to the previous revision",
     ["runbook-rollback"]),
    ("TLS handshake failures and SSL errors in client logs",
     ["runbook-cert"]),
    ("certificate expired what do I do",
     ["runbook-cert"]),
    ("intermittent DNS lookup timeouts across many services",
     ["runbook-dns"]),
    ("CoreDNS pods restarting and name resolution errors",
     ["runbook-dns"]),
    ("node disk full and pods being evicted",
     ["runbook-disk"]),
    ("kubelet eviction storm from disk pressure",
     ["runbook-disk"]),
    ("what are the canary rollout stages and abort rules",
     ["runbook-canary"]),
    ("can I promote a canary manually",
     ["runbook-canary"]),
    ("deploying during a change freeze window",
     ["runbook-freeze"]),
    ("break-glass exception during a freeze",
     ["runbook-freeze"]),
    ("who gets paged for a SEV1 outage",
     ["runbook-escalation"]),
    ("when should automation escalate to a human",
     ["runbook-escalation"]),
    ("memory limit exceeded container killed",
     ["runbook-oom"]),
    ("connection pool increase caused memory exhaustion",
     ["runbook-conn-pool", "runbook-oom"]),
    ("garbage collection pauses raising tail latency",
     ["runbook-latency"]),
    ("open a revert PR after rolling back",
     ["runbook-rollback"]),
    ("error rate SLO breach during rollout",
     ["runbook-canary"]),
    ("investigate what changed before a latency regression",
     ["runbook-latency"]),
    # More operator-phrased queries sharing vocabulary with the runbooks.
    ("abort the rollout automatically on an SLO breach",
     ["runbook-canary"]),
    ("block risky changes during peak trading days",
     ["runbook-freeze"]),
    ("how many database connections should each instance hold",
     ["runbook-conn-pool"]),
    ("JVM heap flag change and the container got OOMKilled",
     ["runbook-oom"]),
    ("prune images and rotate logs to reclaim node disk",
     ["runbook-disk"]),
    ("page the on-call and incident commander for a SEV1",
     ["runbook-escalation"]),
    ("conntrack table exhaustion causing lookup failures",
     ["runbook-dns"]),
    ("watch the golden signals for ten minutes after a rollback",
     ["runbook-rollback"]),
    # Paraphrase subset (audit F1): deliberately vocabulary-divergent from the
    # target runbook, so the metric reflects generalization, not lexical
    # overlap. A purely lexical retriever is EXPECTED to miss some of these —
    # that honesty is the point; the CI lower-bound gate absorbs it.
    ("the app keeps dying because it ran out of RAM",
     ["runbook-oom"]),
    ("clients can't establish a secure channel to the service",
     ["runbook-cert"]),
    ("microservices can't find each other by hostname",
     ["runbook-dns"]),
    ("the box filled up its storage and started killing workloads",
     ["runbook-disk"]),
    ("everything got sluggish right after we shipped the new build",
     ["runbook-latency"]),
    ("hand the incident to a person when the bot isn't sure",
     ["runbook-escalation"]),
]
