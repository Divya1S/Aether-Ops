"""Golden scenarios: frozen evidence snapshots with adjudicated ground truth
(docs/10-evaluation.md §2). Each scenario is exactly what the replay harness
replays: connector contracts unchanged, data frozen at incident time.

Ground-truth outcomes include "escalated" — refusing to diagnose without a
correlated change event is a CORRECT behavior and is scored as such
(escalation-is-success, docs/11-failure-handling.md §8).
"""
from __future__ import annotations

from dataclasses import dataclass

from aetherops.connectors.base import ConnectorRegistry
from aetherops.connectors.fakes import (FakeDatadog, FakeGitHub,
                                        FakeKubernetes, FakePagerDuty,
                                        FakeSlack, Snapshot)
from aetherops.core.types import IncidentEvent, Severity, new_id
from aetherops.gateway.backends import build_backend_chain
from aetherops.gateway.model_gateway import ModelGateway
from aetherops.memory.store import EpisodicMemory
from aetherops.policy.engine import PolicyEngine
from aetherops.rag.retriever import RagStore
from aetherops.security.audit import AuditLog


@dataclass(frozen=True)
class GroundTruth:
    outcome: str                        # "remediated" | "escalated"
    failure_class: str | None = None    # None => no diagnosis is the right answer
    suspect_commit: str | None = None
    expected_steps: tuple = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    snapshot: Snapshot
    truth: GroundTruth
    severity: Severity = Severity.SEV2
    preload_episodes: tuple = ()


def canonical() -> Scenario:
    """Scenario 1 — the canonical SEV2: checkout-service deploy regression."""
    return Scenario(
        id="s1-checkout-pool",
        name="checkout-service pool-size deploy regression",
        snapshot=Snapshot(),
        truth=GroundTruth(
            outcome="remediated",
            failure_class="deploy-regression/memory",
            suspect_commit="c9a1f42",
            expected_steps=("rollback_deployment", "create_revert_pr")),
        preload_episodes=({
            "service": "payments-service",
            "failure_class": "deploy-regression/memory",
            "summary": "Connection pool increase caused OOMKilled cascade; "
                       "rollback restored p99 within 10 minutes",
            "remediation": ["rollback_deployment"],
            "verified": True,
        },))


def uncorrelated_latency() -> Scenario:
    """Scenario 2 — latency spike with NO correlated change event and no OOM
    signature. Ground truth: the platform must refuse to diagnose and
    escalate with the partial bundle."""
    return Scenario(
        id="s2-search-uncorrelated",
        name="search-api latency spike, no correlated change",
        snapshot=Snapshot(
            service="search-api",
            alert_id="P-9017",
            alert_title="search-api p99 latency > 1500ms",
            triggered_at="2026-08-07T09:12:00Z",
            p99_incident=(("09:00Z", 140), ("09:05Z", 1650), ("09:10Z", 1710)),
            p99_post=(("09:40Z", 150), ("09:45Z", 145)),
            has_deploy=False,
            oom_events=(),
            oom_count=0),
        truth=GroundTruth(outcome="escalated"))


def payments_regression() -> Scenario:
    """Scenario 3 — same failure class, different service/commit/revisions:
    proves the pipeline and the offline backend generalize instead of
    pattern-matching one incident."""
    return Scenario(
        id="s3-payments-pool",
        name="payments-service pool-size deploy regression",
        snapshot=Snapshot(
            service="payments-service",
            alert_id="P-9120",
            alert_title="payments-service p99 latency > 1200ms",
            triggered_at="2026-08-06T11:14:00Z",
            p99_incident=(("11:00Z", 96), ("11:05Z", 1890), ("11:10Z", 2050)),
            p99_post=(("11:40Z", 131), ("11:45Z", 118)),
            revision="v2025.08.06-9",
            previous_revision="v2025.08.06-8",
            deployed_at="2026-08-06T11:03:00Z",
            sha="f3d92ab",
            commit_title="Raise DB connection pool max_size 10 -> 150",
            commit_diff="-  max_size: 10\n+  max_size: 150",
            oom_events=(("OOMKilled", "payments-5b21-2", "11:07Z"),
                        ("OOMKilled", "payments-5b21-5", "11:08Z"),
                        ("BackOff", "payments-5b21-2", "11:09Z")),
            oom_count=8),
        truth=GroundTruth(
            outcome="remediated",
            failure_class="deploy-regression/memory",
            suspect_commit="f3d92ab",
            expected_steps=("rollback_deployment", "create_revert_pr")))


def cert_expiry() -> Scenario:
    """Scenario 4 — a second diagnosable failure class: TLS certificate
    expiry. No change event exists; diagnosis grounds on symptom markers
    (error spikes + TLSHandshakeError events) corroborated by runbook
    guidance, and the plan is a certificate rotation, not a rollback."""
    return Scenario(
        id="s4-cert-expiry",
        name="payments-gateway TLS certificate expiry",
        snapshot=Snapshot(
            service="payments-gateway",
            alert_id="P-9433",
            alert_title="payments-gateway TLS handshake failures spiking",
            triggered_at="2026-08-05T09:04:00Z",
            p99_incident=(("09:00Z", 142), ("09:05Z", 168), ("09:10Z", 171)),
            p99_post=(("09:40Z", 139), ("09:45Z", 131)),
            has_deploy=False,
            oom_events=(("TLSHandshakeError", "gateway-2c41-1", "09:02Z"),
                        ("TLSHandshakeError", "gateway-2c41-3", "09:03Z")),
            oom_count=34),
        truth=GroundTruth(
            outcome="remediated",
            failure_class="cert-expiry/tls",
            suspect_commit=None,
            expected_steps=("rotate_certificate",)))


def all_scenarios() -> list[Scenario]:
    return [canonical(), uncorrelated_latency(), payments_regression(),
            cert_expiry()]


def build_environment(scenario: Scenario, audit_path: str | None = None,
                      backends_spec: str | None = None):
    """Build a fresh, isolated environment serving the scenario's snapshot —
    one environment per replay, exactly like the production harness.

    `backends_spec` selects the model-backend chain ("ollama,offline" for
    live mode). The eval harness always passes "offline" explicitly —
    golden-scenario replay must never depend on what's installed."""
    audit = AuditLog(path=audit_path)
    connectors = ConnectorRegistry()
    for connector_cls in (FakePagerDuty, FakeDatadog, FakeGitHub,
                          FakeKubernetes, FakeSlack):
        connectors.register(connector_cls(audit=audit,
                                          snapshot=scenario.snapshot))

    memory = EpisodicMemory()
    for episode in scenario.preload_episodes:
        memory.add(dict(episode))

    incident = IncidentEvent(
        id=new_id("inc"),
        title=scenario.snapshot.alert_title,
        service=scenario.snapshot.service,
        severity=scenario.severity,
        description=f"Golden scenario {scenario.id}: {scenario.name}",
        environment="prod",
        labels={"pagerduty_id": scenario.snapshot.alert_id})

    return incident, {
        "connectors": connectors,
        "gateway": ModelGateway(audit=audit,
                                backends=build_backend_chain(backends_spec)),
        "audit": audit,
        "memory": memory,
        "policy": PolicyEngine(),
        "rag": RagStore(),
    }
