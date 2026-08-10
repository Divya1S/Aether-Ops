"""Offline deterministic response templates, keyed by task and grounded in
what the prompt's evidence digest actually contains — no evidence, no claim
(the same grounding contract hosted models are held to). Used by the
OfflineHeuristicBackend, which is the guaranteed last link of every backend
chain and the sole backend for tests and eval replays.
"""
from __future__ import annotations

import json
import re

_EVIDENCE_LINE = re.compile(r"\[E(\d+)\] \(([\w-]+)")
_COMMIT_REF = re.compile(r"github://commit/([0-9a-f]{7,40})")


def respond(prompt: str, task: str) -> str:
    if task == "triage":
        match = re.search(r"service=(\S+)", prompt)
        service = match.group(1) if match else "the service"
        return (f"Alert maps to service {service} in prod. Sustained, "
                "customer-facing p99 latency breach.")

    if task == "root_cause":
        return _diagnose(prompt)

    if task == "judge":
        return _judge(prompt)

    if task == "investigate":
        return _investigate(prompt)

    if task == "plan":
        return _plan(prompt)

    if task == "postmortem":
        service = re.search(r"service=(\S+)", prompt)
        cls = re.search(r"failure_class=(\S+)", prompt)
        sha = re.search(r"suspect=([0-9a-f]{7,40})", prompt)
        p99 = re.search(r"recovered_p99=(\d+)", prompt)
        if "cert-expiry" in prompt:
            return (f"On {service.group(1) if service else 'the service'}, "
                    "an expired TLS certificate caused client handshake "
                    "failures. The platform correlated error spikes with "
                    "TLSHandshakeError pod events and the absence of any "
                    "deploy, renewed and rotated the certificate under "
                    "approval, and verified recovery at p99 "
                    f"{p99.group(1) if p99 else '?'}ms.")
        return (f"On {service.group(1) if service else 'the service'}, a "
                f"deploy-introduced change "
                f"({sha.group(1) if sha else 'unidentified'}) matching "
                f"class {cls.group(1) if cls else 'unknown'} exhausted pod "
                "memory and breached latency SLOs. The platform correlated "
                "deploy, commit, and runtime evidence, executed a "
                "policy-gated rollback with a fix-forward revert PR, and "
                "verified recovery at p99 "
                f"{p99.group(1) if p99 else '?'}ms.")

    if task == "review":
        checks = re.search(r"checks_passed=(\d+)/(\d+)", prompt)
        done, total = (checks.group(1), checks.group(2)) if checks else ("?", "?")
        return (f"Plan review: {done} of {total} independent safety checks "
                "passed (catalog membership, grounded rollback target, "
                "grounded revert SHA, service scope, failure-class fit).")

    if task == "change_risk":
        matched = re.search(r"matched=(\d+)", prompt)
        blast = re.search(r"blast_radius=(\d+)", prompt)
        band = re.search(r"band=(\w+)", prompt)
        return (f"Change risk {band.group(1) if band else '?'}: matches "
                f"{matched.group(1) if matched else '?'} prior incident "
                f"episode(s) with the same failure signature; blast radius "
                f"{blast.group(1) if blast else '?'} downstream services.")

    if task == "verify":
        p99 = re.search(r"p99=(\d+)", prompt)
        oom = re.search(r"oomkilled_last_10m=(\d+)", prompt)
        return (f"Post-remediation window shows p99 at "
                f"{p99.group(1) if p99 else '?'}ms with "
                f"{oom.group(1) if oom else '?'} OOMKilled events in the "
                "last 10 minutes.")

    return "Summary: " + prompt[:200]


def _judge(prompt: str) -> str:
    """Deterministic judge policy: scores from checkable signals in the
    hypothesis, so CI grades generated-content quality reproducibly. Live
    (Ollama) replaces this with a real model judgement."""
    count_match = re.search(r"evidence_items=(\d+)", prompt)
    evidence_count = int(count_match.group(1)) if count_match else 0
    # The artifact is everything after the "Hypothesis:" marker.
    artifact = prompt.split("Hypothesis:", 1)[-1]
    refs = sorted({int(n) for n in re.findall(r"\[E(\d+)\]", artifact)})
    hallucinated = [f"E{n}" for n in refs
                    if n < 1 or (evidence_count and n > evidence_count)]

    if "Insufficient evidence" in artifact:
        causal = 2
    elif "Causal chain:" in artifact and re.search(r"[0-9a-f]{7,40}", artifact):
        causal = 5
    elif refs:
        causal = 4
    else:
        causal = 3
    grounding = 5 if (refs and not hallucinated) else 2 if hallucinated else 3
    body = artifact.split("Evidence digest:", 1)[0]
    clarity = 5 if 120 <= len(body) <= 1400 else 4
    faithfulness = 1 if hallucinated else 5
    return json.dumps({"causal_correctness": causal, "grounding": grounding,
                       "clarity": clarity,
                       "citation_faithfulness": faithfulness,
                       "hallucinated_refs": hallucinated})


def _investigate(prompt: str) -> str:
    """Deterministic next-action policy over the loop's declared state —
    reproduces the platform's canonical gathering order exactly, so golden
    scenarios stay byte-stable while live models decide for real."""
    def bracket(name: str) -> str:
        match = re.search(name + r"=\[([^\]]*)\]", prompt)
        return match.group(1) if match else ""

    kinds = bracket("kinds_present")
    called = bracket("called")
    pending = re.findall(r"[0-9a-f]{7,40}",
                         bracket("pending_commit_diffs"))

    def decide(action: str, args: dict, rationale: str) -> str:
        return json.dumps({"action": action, "args": args,
                           "rationale": rationale})

    if "metrics" not in kinds:
        return decide("query_metrics", {},
                      "correlate the symptom with the timeline")
    if "deploy" not in kinds:
        return decide("list_recent_deploys", {},
                      "check what changed before symptom onset")
    if pending:
        return decide("get_commit_diff", {"sha": pending[0]},
                      "inspect the change the deploy shipped")
    if "k8s-event" not in kinds:
        return decide("get_events", {}, "check runtime failure signals")
    if "get_thread" not in called:
        return decide("get_thread", {}, "check operator discussion")
    if "search_runbooks" not in called:
        return decide("search_runbooks", {},
                      "pull relevant runbook guidance")
    return decide("finish", {},
                  "sufficient evidence: change event correlates with the "
                  "symptom")


def _plan(prompt: str) -> str:
    """Canonical plan JSON from the grounded values in the prompt."""
    service = re.search(r"service=(\S+)", prompt)
    prev = re.search(r"previous_revision=(\S+)", prompt)
    sha = re.search(r"suspect_commit=([0-9a-f]{7,40})", prompt)
    if "cert-expiry" in prompt and service:
        return json.dumps({
            "self_estimate": 0.9,
            "rationale": "Renew and rotate the expired certificate; no "
                         "code change to revert.",
            "steps": [{"action": "rotate_certificate",
                       "args": {"service": service.group(1)}}]})
    steps = []
    if service and prev:
        steps.append({"action": "rollback_deployment",
                      "args": {"service": service.group(1),
                               "revision": prev.group(1)}})
    if sha:
        steps.append({"action": "create_revert_pr",
                      "args": {"sha": sha.group(1)}})
    return json.dumps({
        "self_estimate": 0.9,
        "rationale": "Reverse the causal trigger, then route the "
                     "permanent fix through review.",
        "steps": steps})


def _pool_reduced(prompt: str) -> bool:
    """Whether the correlated commit *reduces* the pool — detected tightly
    against the pool phrase itself (a decrease keyword immediately preceding
    'pool', or a numeric 'N -> M' with M < N within a few chars of 'pool'), so
    unrelated words elsewhere in the digest can't produce a false positive."""
    if re.search(r"(?i)\b(revert\w*|lower\w*|reduc\w*|decreas\w*|downgrad\w*)"
                 r"\s+(?:\w+\s+){0,3}?pool\b", prompt):
        return True
    for match in re.finditer(
            r"(?i)pool[^\n]{0,40}?(\d+)\s*(?:->|to)\s*(\d+)", prompt):
        if int(match.group(2)) < int(match.group(1)):
            return True
    return False


def _diagnose(prompt: str) -> str:
    kinds: dict[str, int] = {}
    for match in _EVIDENCE_LINE.finditer(prompt):
        kinds.setdefault(match.group(2), int(match.group(1)))

    sha_match = _COMMIT_REF.search(prompt)
    has_oom = "OOMKilled" in prompt
    # Scope pool detection to the COMMIT evidence line(s), not the whole digest
    # (audit A1): a runbook that merely mentions a connection pool must not
    # satisfy the deploy-regression signature, and direction awareness (audit
    # C4) must read the actual change — never assert "raising the pool" unless
    # the correlated commit is an increase; otherwise decline honestly.
    commit_text = " ".join(
        line for line in prompt.splitlines()
        if (m := _EVIDENCE_LINE.match(line)) and m.group(2) == "commit")
    has_pool_change = bool(re.search(
        r"(?i)connection[ _-]?pool|pool[ _-]?size|max_size", commit_text))
    pool_reduced = _pool_reduced(commit_text)
    required = {"metrics", "deploy", "commit", "k8s-event"}

    # Certificate-expiry class: TLS handshake failures with no correlated
    # change event, corroborated by runbook guidance markers.
    if ("TLSHandshakeError" in prompt and not sha_match
            and "certificate" in prompt.lower()
            and {"metrics", "k8s-event"} <= kinds.keys()):
        deploy_note = (f" No deployment correlates with onset "
                       f"[E{kinds['deploy']}]." if "deploy" in kinds else "")
        runbook_note = (f" Runbook guidance matches the signature "
                        f"[E{kinds['runbook']}]." if "runbook" in kinds
                        else "")
        return (
            f"Hypothesis (primary): the service's TLS certificate expired. "
            f"Clients fail the handshake, producing error spikes "
            f"[E{kinds['metrics']}] and TLSHandshakeError pod events "
            f"[E{kinds['k8s-event']}].{deploy_note}{runbook_note} "
            "Remediate by renewing and rotating the certificate. "
            "Recommended class: cert-expiry/tls.")

    if (sha_match and has_oom and has_pool_change
            and required <= kinds.keys() and pool_reduced):
        return ("Insufficient evidence: the correlated commit REDUCES the "
                "connection pool, which is inconsistent with a "
                "memory-exhaustion mechanism — the change does not explain the "
                "symptom. Escalate to a human with the partial bundle.")

    if not (sha_match and has_oom and has_pool_change
            and required <= kinds.keys()):
        return ("Insufficient evidence: the bundle lacks a change event "
                "correlated with the symptom onset. Escalate to a human "
                "with the partial bundle.")

    sha = sha_match.group(1)
    episode_note = (
        f" A prior episode with the same signature supports this class "
        f"[E{kinds['episode']}]." if "episode" in kinds else "")
    return (
        f"Hypothesis (primary): the deploy [E{kinds['deploy']}] shipped "
        f"commit {sha} raising the DB connection pool [E{kinds['commit']}]. "
        f"Pod memory grew past its limit, causing OOMKilled and "
        f"CrashLoopBackOff [E{kinds['k8s-event']}]; surviving pods "
        f"absorbed the load, breaching p99 latency [E{kinds['metrics']}]. "
        f"Causal chain: deploy [E{kinds['deploy']}] -> pool change "
        f"[E{kinds['commit']}] -> memory exhaustion [E{kinds['k8s-event']}] "
        f"-> latency breach [E{kinds['metrics']}].{episode_note} "
        "Recommended class: deploy-regression/memory.")
