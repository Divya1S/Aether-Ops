"""The hypothesis tree: a differential diagnosis over the evidence bundle
(docs/20-operator-console.md §2).

The Root Cause agent writes ONE hypothesis in prose. That is enough to act
on, and not enough to trust: an operator at the approval gate also needs to
see which competing causes were considered and what ruled each one out —
most of all on the runs that escalate, where "insufficient evidence" is the
whole answer. This module makes that reasoning explicit.

Each candidate cause is a set of falsifiable criteria. Every criterion is
evaluated against the structured facts carried by the bundle and resolves to
`supported`, `refuted` or `unknown`, naming the evidence ([En]) it rests on:

  - any required criterion refuted           -> INVALIDATED
  - every required criterion supported       -> VALIDATED
  - otherwise (evidence missing or ambiguous) -> INCONCLUSIVE

Design rules:

  - Deterministic and read-only. No model call, no connector call, no audit
    append: the tree is a projection of the workflow's own record, safe to
    recompute on every read (the same contract as reporting/postmortem.py).
  - Explanation, not control flow. Gates, policy and the Reviewer still
    decide what executes; the tree never changes an outcome. It reports
    whether it AGREES with the pipeline, so a disagreement (possible with a
    nondeterministic live model) is surfaced instead of hidden.
  - Same predicates as the gate. Temporal precedence and mechanism direction
    come from reasoning/signals.py — the functions the Reviewer enforces.
  - Hostile evidence proves nothing. QUARANTINED items are treated as
    unavailable, exactly as the model-facing digest treats them.
  - No invented confidence. A hypothesis reports how many criteria held, not
    a probability the platform has no basis to state.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aetherops.reasoning.signals import (first_breach, has_pool_signature,
                                         minute_of_day, numeric_delta)

SUPPORTED, REFUTED, UNKNOWN = "supported", "refuted", "unknown"
VALIDATED, INVALIDATED, INCONCLUSIVE = ("VALIDATED", "INVALIDATED",
                                        "INCONCLUSIVE")

_TLS_MARKERS = ("tls", "handshake", "certificate")


@dataclass
class Facts:
    """Structured facts lifted from the bundle, each with the [En] it came
    from. Absent evidence stays None — a missing source must read as
    `unknown`, never as a silent negative."""

    refs: dict = field(default_factory=dict)     # kind -> ["E2", ...]
    alert_title: str = ""
    series: list | None = None
    onset: str | None = None
    deploys: list | None = None                  # [] = looked, found none
    commit: dict | None = None
    events: list | None = None                   # [] = looked, found none
    runbook_text: str = ""

    def ref(self, *kinds: str) -> list[str]:
        out: list[str] = []
        for kind in kinds:
            out.extend(self.refs.get(kind, []))
        return out

    @property
    def deploy(self) -> dict | None:
        return self.deploys[0] if self.deploys else None

    @property
    def reasons(self) -> list[str]:
        return [str(e.get("reason", "")) for e in (self.events or [])]


def gather_facts(evidence: list) -> Facts:
    facts = Facts()
    for number, item in enumerate(evidence, start=1):
        if item.classification == "QUARANTINED":
            continue                    # suspected injection: proves nothing
        facts.refs.setdefault(item.kind, []).append(f"E{number}")
        data = item.data or {}
        if item.kind == "alert":
            facts.alert_title = data.get("title") or item.summary
        elif item.kind == "metrics" and "series" in data:
            facts.series = list(data["series"])
            facts.onset = first_breach(facts.series)
        elif item.kind == "deploy" and "deploys" in data:
            facts.deploys = list(data["deploys"])
        elif item.kind == "commit" and facts.commit is None and "sha" in data:
            facts.commit = dict(data)
        elif item.kind == "k8s-event" and "events" in data:
            facts.events = list(data["events"])
        elif item.kind in ("runbook", "postmortem"):
            facts.runbook_text += f" {item.summary} {item.citation.excerpt}"
    return facts


def _criterion(cid: str, claim: str, status: str, note: str,
               evidence: list[str], required: bool = True,
               gather: str | None = None) -> dict:
    out = {"id": cid, "claim": claim, "status": status, "note": note,
           "evidence": evidence, "required": required}
    if gather and status == UNKNOWN:
        out["gather"] = gather          # the evidence that would settle it
    return out


def _change_shipped(facts: Facts) -> dict:
    claim = "A deployment shipped in the lookback window"
    if facts.deploys is None:
        return _criterion("change-correlated", claim, UNKNOWN,
                          "deploy history was not gathered", [],
                          gather="recent deployments for the service")
    if not facts.deploys:
        return _criterion("change-correlated", claim, REFUTED,
                          "no deployment in the lookback window",
                          facts.ref("deploy"))
    deploy = facts.deploy
    return _criterion(
        "change-correlated", claim, SUPPORTED,
        f"{deploy.get('revision', 'a revision')} deployed "
        f"{deploy.get('deployed_at', 'at an unknown time')}",
        facts.ref("deploy"))


def _precedence(facts: Facts) -> tuple[str, str]:
    """(status, note) for 'the deploy precedes symptom onset' — the
    Reviewer's temporal-precedence check, read off the bundle."""
    if not facts.deploy:
        return UNKNOWN, "not evaluated — there is no deployment to order"
    deployed = minute_of_day(facts.deploy.get("deployed_at"))
    onset = minute_of_day(facts.onset)
    if deployed is None or onset is None:
        return UNKNOWN, "deploy time or symptom onset could not be read"
    note = (f"deployed {facts.deploy.get('deployed_at')}; first breach "
            f"{facts.onset}")
    if deployed <= onset:
        return SUPPORTED, note
    return REFUTED, note + " — the deploy landed after the symptom began"


def _deploy_memory(facts: Facts) -> dict:
    criteria = [_change_shipped(facts)]

    status, note = _precedence(facts)
    criteria.append(_criterion(
        "temporal-precedence", "The deploy precedes symptom onset", status,
        note, facts.ref("deploy", "metrics"),
        gather="the latency series and the deploy timestamp"))

    claim = "The service's pods were OOMKilled"
    if facts.events is None:
        criteria.append(_criterion(
            "crash-signature", claim, UNKNOWN,
            "pod events were not gathered", [],
            gather="Kubernetes pod events for the service"))
    elif "OOMKilled" in facts.reasons:
        count = facts.reasons.count("OOMKilled")
        criteria.append(_criterion(
            "crash-signature", claim, SUPPORTED,
            f"{count} OOMKilled event(s) in the bundle",
            facts.ref("k8s-event")))
    else:
        criteria.append(_criterion(
            "crash-signature", claim, REFUTED,
            "no OOMKilled events on the service's pods",
            facts.ref("k8s-event")))

    claim = "The shipped change raises a resource limit"
    commit = facts.commit
    if commit is None:
        criteria.append(_criterion(
            "mechanism-consistency", claim, UNKNOWN,
            "not evaluated — there is no shipped change to inspect", [],
            gather="the diff of the commit the deploy shipped"))
    else:
        text = f"{commit.get('title', '')}\n{commit.get('diff', '')}"
        delta = numeric_delta(commit.get("diff", ""))
        if delta is not None and delta[1] < delta[0]:
            criteria.append(_criterion(
                "mechanism-consistency", claim, REFUTED,
                f"commit {commit['sha']} lowers the limit {delta[0]} -> "
                f"{delta[1]}; a reduction cannot exhaust memory",
                facts.ref("commit")))
        elif (has_pool_signature(text) and delta is not None
              and delta[1] > delta[0]):
            criteria.append(_criterion(
                "mechanism-consistency", claim, SUPPORTED,
                f"commit {commit['sha']} raises the connection-pool limit "
                f"{delta[0]} -> {delta[1]}", facts.ref("commit")))
        else:
            criteria.append(_criterion(
                "mechanism-consistency", claim, UNKNOWN,
                f"commit {commit['sha']} does not match a known "
                "memory-regression signature (a connection-pool limit "
                "raise); the deterministic diagnoser cannot ground it",
                facts.ref("commit"),
                gather="heap and memory profiles for the new revision"))

    return {"id": "H1", "failure_class": "deploy-regression/memory",
            "title": "Deploy regression — a shipped change exhausted pod "
                     "memory",
            "groundable": True, "criteria": criteria}


def _cert_expiry(facts: Facts) -> dict:
    criteria = []

    claim = "TLS handshake failures appear in the alert or pod events"
    alert_marker = any(m in facts.alert_title.lower() for m in _TLS_MARKERS)
    event_marker = any(any(m in reason.lower() for m in _TLS_MARKERS)
                       for reason in facts.reasons)
    if alert_marker or event_marker:
        refs = (facts.ref("k8s-event") if event_marker else []) + \
               (facts.ref("alert") if alert_marker else [])
        criteria.append(_criterion(
            "tls-symptom", claim, SUPPORTED,
            "TLS handshake failure markers present", refs))
    elif facts.events is None:
        criteria.append(_criterion(
            "tls-symptom", claim, UNKNOWN, "pod events were not gathered",
            facts.ref("alert"),
            gather="Kubernetes pod events for the service"))
    else:
        criteria.append(_criterion(
            "tls-symptom", claim, REFUTED,
            "no TLS or handshake markers in the alert or pod events",
            facts.ref("alert", "k8s-event")))

    claim = "No deployment correlates with onset"
    if facts.deploys is None:
        criteria.append(_criterion(
            "no-correlated-change", claim, UNKNOWN,
            "deploy history was not gathered", [],
            gather="recent deployments for the service"))
    elif not facts.deploys:
        criteria.append(_criterion(
            "no-correlated-change", claim, SUPPORTED,
            "no deployment in the lookback window", facts.ref("deploy")))
    else:
        criteria.append(_criterion(
            "no-correlated-change", claim, UNKNOWN,
            "a deployment shipped in the window, so expiry cannot be "
            "isolated from the change", facts.ref("deploy"),
            gather="the certificate's notAfter date"))

    corroborated = "certificate" in facts.runbook_text.lower()
    criteria.append(_criterion(
        "runbook-corroboration",
        "Runbook guidance matches the signature",
        SUPPORTED if corroborated else UNKNOWN,
        "retrieved guidance describes certificate expiry" if corroborated
        else "no retrieved guidance names certificate expiry",
        facts.ref("runbook", "postmortem") if corroborated else [],
        required=False))        # advisory context never decides a diagnosis

    return {"id": "H2", "failure_class": "cert-expiry/tls",
            "title": "TLS certificate expiry",
            "groundable": True, "criteria": criteria}


def _dependency_or_capacity(facts: Facts) -> dict:
    criteria = []

    claim = "No change shipped before symptom onset"
    precedence, note = _precedence(facts)
    if facts.deploys is None:
        criteria.append(_criterion(
            "no-preceding-change", claim, UNKNOWN,
            "deploy history was not gathered", [],
            gather="recent deployments for the service"))
    elif not facts.deploys:
        criteria.append(_criterion(
            "no-preceding-change", claim, SUPPORTED,
            "no deployment in the lookback window", facts.ref("deploy")))
    elif precedence == SUPPORTED:
        criteria.append(_criterion(
            "no-preceding-change", claim, REFUTED,
            f"a change shipped first ({note})",
            facts.ref("deploy", "metrics")))
    elif precedence == REFUTED:
        criteria.append(_criterion(
            "no-preceding-change", claim, SUPPORTED,
            f"the only deploy landed after onset ({note})",
            facts.ref("deploy", "metrics")))
    else:
        criteria.append(_criterion(
            "no-preceding-change", claim, UNKNOWN, note,
            facts.ref("deploy", "metrics")))

    claim = "The service's own pods show no crash signature"
    if facts.events is None:
        criteria.append(_criterion(
            "no-local-crash", claim, UNKNOWN, "pod events were not gathered",
            [], gather="Kubernetes pod events for the service"))
    elif facts.events:
        kinds = ", ".join(sorted(set(facts.reasons)))
        criteria.append(_criterion(
            "no-local-crash", claim, REFUTED,
            f"the fault is localized to this service's pods ({kinds})",
            facts.ref("k8s-event")))
    else:
        criteria.append(_criterion(
            "no-local-crash", claim, SUPPORTED,
            "no abnormal pod events in the window", facts.ref("k8s-event")))

    criteria.append(_criterion(
        "dependency-telemetry",
        "An upstream dependency or traffic surge is degraded",
        UNKNOWN,
        "not collected — no dependency or traffic telemetry is in the "
        "bundle", [],
        gather="upstream latency, saturation and request-rate metrics"))

    return {"id": "H3", "failure_class": "dependency-or-capacity",
            "title": "Upstream dependency or capacity saturation",
            # Not a class the platform can remediate from the Step Catalog:
            # it can stay open, never validate.
            "groundable": False, "criteria": criteria}


def _resolve(hypothesis: dict) -> dict:
    required = [c for c in hypothesis["criteria"] if c["required"]]
    supported = sum(c["status"] == SUPPORTED for c in required)
    if any(c["status"] == REFUTED for c in required):
        status = INVALIDATED
    elif hypothesis["groundable"] and supported == len(required):
        status = VALIDATED
    else:
        status = INCONCLUSIVE
    ruled_out_by = [c["id"] for c in required if c["status"] == REFUTED]
    return {"id": hypothesis["id"],
            "failure_class": hypothesis["failure_class"],
            "title": hypothesis["title"], "status": status,
            "supported": supported, "required": len(required),
            "ruled_out_by": ruled_out_by,
            "criteria": hypothesis["criteria"],
            "gather_next": [c["gather"] for c in hypothesis["criteria"]
                            if "gather" in c]}


_ORDER = {VALIDATED: 0, INCONCLUSIVE: 1, INVALIDATED: 2}


def _pipeline_view(ctx, run) -> dict:
    rca = ctx.results.get("root_cause")
    diagnosed = rca is not None and rca.output.get("status") == "diagnosed"
    error = (getattr(run, "error", None) or "") if run is not None else ""
    escalated = "escalate" in error.lower()
    return {"status": rca.output.get("status") if rca else None,
            "failure_class": rca.output.get("failure_class")
            if diagnosed else None,
            "escalated": escalated,
            # The class the pipeline is actually standing behind: a diagnosis
            # the Reviewer (or anything downstream) escalated does not count.
            "effective_class": rca.output.get("failure_class")
            if diagnosed and not escalated else None}


def build_hypothesis_tree(ctx, run=None) -> dict:
    """Differential diagnosis for one workflow context. `run` (a DagRun) is
    optional; with it, the tree also reports agreement with the pipeline's
    final stance."""
    facts = gather_facts(ctx.evidence)
    hypotheses = [_resolve(h) for h in (_deploy_memory(facts),
                                        _cert_expiry(facts),
                                        _dependency_or_capacity(facts))]
    hypotheses.sort(key=lambda h: (_ORDER[h["status"]],
                                   -h["supported"], h["id"]))

    validated = [h for h in hypotheses if h["status"] == VALIDATED]
    open_ = [h for h in hypotheses if h["status"] == INCONCLUSIVE]
    winner = validated[0] if len(validated) == 1 else None

    if winner is not None:
        conclusion = "validated"
        summary = (f"{winner['title']} holds on all "
                   f"{winner['required']} criteria; "
                   f"{len(hypotheses) - 1} competing cause(s) were ruled "
                   "out or left open.")
    elif len(validated) > 1:
        conclusion = "inconclusive"
        summary = ("More than one cause fits the evidence — the bundle "
                   "cannot separate them. Escalate to a human.")
    elif open_:
        conclusion = "inconclusive"
        summary = (f"No cause is validated. {len(open_)} hypothesis(es) "
                   "remain open for lack of evidence — escalate with the "
                   "partial bundle.")
    else:
        conclusion = "inconclusive"
        summary = ("Every candidate cause is contradicted by the evidence. "
                   "The symptom is real but unexplained — escalate with the "
                   "partial bundle.")

    gather_next: list[str] = []
    for hypothesis in open_:
        for item in hypothesis["gather_next"]:
            if item not in gather_next:
                gather_next.append(item)

    pipeline = _pipeline_view(ctx, run)
    tree_class = winner["failure_class"] if winner else None
    pipeline["agrees"] = (pipeline["status"] is None
                          or tree_class == pipeline["effective_class"])

    return {"conclusion": conclusion,
            "validated": tree_class,
            "summary": summary,
            "hypotheses": hypotheses,
            "gather_next": gather_next if winner is None else [],
            "signals": {"onset": facts.onset,
                        "deploy": facts.deploy,
                        "commit": ({"sha": facts.commit.get("sha"),
                                    "title": facts.commit.get("title")}
                                   if facts.commit else None)},
            "pipeline": pipeline}
