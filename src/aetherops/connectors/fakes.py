"""Snapshot-driven faked connectors (docs/10-evaluation.md).

Each connector serves a frozen evidence `Snapshot` — the exact pattern the
evaluation replay harness uses in production: real connector contracts,
frozen data. The default Snapshot is the canonical SEV2
(docs/01-architecture.md §5); the golden-scenario set in
`aetherops/evals/scenarios.py` provides others.

Write tools return dry-run results plus an `undo` descriptor — the saga
compensation contract (docs/03-orchestration.md §4).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from aetherops.connectors.base import Connector, ToolResult, ToolSpec
from aetherops.core.types import RiskLevel


@dataclass(frozen=True)
class Snapshot:
    """Frozen evidence state of one incident, as the source systems saw it."""

    service: str = "checkout-service"
    alert_id: str = "P-8842"
    alert_title: str = "checkout-service p99 latency > 2000ms"
    urgency: str = "high"
    customer_impact: bool = True
    triggered_at: str = "2026-08-07T14:09:00Z"
    p99_incident: tuple = (("14:00Z", 181), ("14:05Z", 2140), ("14:10Z", 2412))
    p99_post: tuple = (("14:40Z", 197), ("14:45Z", 182))
    has_deploy: bool = True
    revision: str = "v2025.08.07-3"
    previous_revision: str = "v2025.08.07-2"
    deployed_at: str = "2026-08-07T14:02:00Z"
    sha: str = "c9a1f42"
    commit_title: str = "Raise DB connection pool max_size 20 -> 200"
    commit_author: str = "j.doe@example.com"
    commit_diff: str = "-  max_size: 20\n+  max_size: 200"
    oom_events: tuple = (("OOMKilled", "checkout-7f9c-1", "14:06Z"),
                         ("OOMKilled", "checkout-7f9c-4", "14:07Z"),
                         ("BackOff", "checkout-7f9c-1", "14:08Z"))
    oom_count: int = 12
    slack_messages: tuple = ()      # incident-channel thread, may be poisoned


class SnapshotConnector(Connector):
    def __init__(self, audit=None, clock=time.monotonic,
                 snapshot: Snapshot | None = None):
        super().__init__(audit=audit, clock=clock)
        self.snap = snapshot or Snapshot()


class FakePagerDuty(SnapshotConnector):
    system = "pagerduty"
    TOOLS = {
        "get_incident": ToolSpec("get_incident", "Fetch a PagerDuty incident"),
    }

    def _invoke(self, tool: str, args: dict) -> ToolResult:
        snap = self.snap
        data = {
            "id": snap.alert_id,
            "title": snap.alert_title,
            "service": snap.service,
            "urgency": snap.urgency,
            "customer_impact": snap.customer_impact,
            "triggered_at": snap.triggered_at,
        }
        return ToolResult(data, self.cite(
            f"pagerduty://incidents/{snap.alert_id}",
            f"{snap.alert_id} triggered: {snap.alert_title}, "
            f"urgency {snap.urgency}"
            + (", customer impact reported" if snap.customer_impact else "")))


class FakeDatadog(SnapshotConnector):
    system = "datadog"
    TOOLS = {
        "query_metrics": ToolSpec("query_metrics", "Query a metrics timeseries",
                                  rate_per_min=120),
    }

    def _invoke(self, tool: str, args: dict) -> ToolResult:
        snap = self.snap
        if args.get("window") == "post-remediation":
            series = [{"ts": ts, "p99_ms": value} for ts, value in snap.p99_post]
            data = {
                "query": args.get("query", f"p99{{service:{snap.service}}}"),
                "series": series,
                "oomkilled_events_last_10m": 0,
            }
            return ToolResult(data, self.cite(
                "datadog://query/p99-post-remediation",
                f"post-remediation: p99 recovered to {series[-1]['p99_ms']}ms, "
                "0 OOMKilled events in last 10 minutes"))
        series = [{"ts": ts, "p99_ms": value} for ts, value in snap.p99_incident]
        data = {
            "query": args.get("query", f"p99{{service:{snap.service}}}"),
            "series": series,
            "monitor": f"{snap.service}-p99-latency",
            # Planted credential: proves gateway-level redaction is applied to
            # everything crossing into workflow state.
            "monitor_message": "escalate via runbook, api_key=dd-abc123def456",
        }
        return ToolResult(data, self.cite(
            "datadog://query/p99-incident",
            f"p99 jumped {series[0]['p99_ms']}ms -> {series[-1]['p99_ms']}ms "
            f"on {snap.service}"))


class FakeGitHub(SnapshotConnector):
    system = "github"
    TOOLS = {
        "list_recent_deploys": ToolSpec("list_recent_deploys",
                                        "Recent deployments for a service"),
        "get_commit_diff": ToolSpec("get_commit_diff", "Diff for a commit"),
        "create_revert_pr": ToolSpec("create_revert_pr", "Open a revert PR",
                                     risk=RiskLevel.MEDIUM, cacheable=False,
                                     rate_per_min=30),
        "close_pr": ToolSpec("close_pr", "Close a pull request",
                             risk=RiskLevel.LOW, cacheable=False, rate_per_min=30),
    }

    def _invoke(self, tool: str, args: dict) -> ToolResult:
        snap = self.snap
        if tool == "list_recent_deploys":
            if not snap.has_deploy:
                return ToolResult({"deploys": []}, self.cite(
                    f"github://{snap.service}/deployments",
                    f"no deployments for {snap.service} in the lookback window"))
            data = {"deploys": [{
                "service": snap.service,
                "revision": snap.revision,
                "previous_revision": snap.previous_revision,
                "deployed_at": snap.deployed_at,
                "commits": [snap.sha],
            }]}
            return ToolResult(data, self.cite(
                f"github://{snap.service}/deployments/{snap.revision}",
                f"{snap.service} {snap.revision} deployed {snap.deployed_at} "
                f"(previous: {snap.previous_revision}), includes commit {snap.sha}"))
        if tool == "get_commit_diff":
            sha = args.get("sha", snap.sha)
            data = {
                "sha": sha,
                "title": snap.commit_title,
                "author": snap.commit_author,
                "diff": snap.commit_diff,
            }
            return ToolResult(data, self.cite(
                f"github://commit/{sha}",
                f"commit {sha}: '{snap.commit_title}'"))
        if tool == "create_revert_pr":
            sha = args.get("sha", snap.sha)
            data = {
                "pr_url": f"github://{snap.service}/pull/4127",
                "reverts": sha,
                "dry_run": args.get("dry_run", True),
                "undo": {"system": "github", "tool": "close_pr",
                         "args": {"pr": "4127"}},
            }
            return ToolResult(data, self.cite(
                f"github://{snap.service}/pull/4127",
                f"draft revert PR #4127 opened for {sha}"))
        if tool == "close_pr":
            data = {"closed": args.get("pr", "4127")}
            return ToolResult(data, self.cite(
                f"github://{snap.service}/pull/{data['closed']}",
                f"PR #{data['closed']} closed"))
        raise ValueError(tool)


class FakeSlack(SnapshotConnector):
    system = "slack"
    TOOLS = {
        "get_thread": ToolSpec("get_thread",
                               "Messages from the service's incident channel"),
    }

    def _invoke(self, tool: str, args: dict) -> ToolResult:
        snap = self.snap
        messages = list(snap.slack_messages)
        excerpt = (messages[0][:200] if messages
                   else "no incident-channel discussion in window")
        return ToolResult(
            {"channel": f"#inc-{snap.service}", "messages": messages},
            self.cite(f"slack://inc-{snap.service}/thread", excerpt))


class FakeKubernetes(SnapshotConnector):
    system = "kubernetes"
    TOOLS = {
        "get_events": ToolSpec("get_events", "Recent events for a service's pods"),
        "rollback_deployment": ToolSpec("rollback_deployment",
                                        "Roll a deployment back to a revision",
                                        risk=RiskLevel.HIGH, cacheable=False,
                                        rate_per_min=10),
        "rotate_certificate": ToolSpec("rotate_certificate",
                                       "Renew and rotate a service's TLS "
                                       "certificate",
                                       risk=RiskLevel.MEDIUM, cacheable=False,
                                       rate_per_min=10),
    }

    def _invoke(self, tool: str, args: dict) -> ToolResult:
        snap = self.snap
        if tool == "get_events":
            events = [{"reason": reason, "pod": pod, "ts": ts}
                      for reason, pod, ts in snap.oom_events]
            data = {"events": events, "oomkilled_count": snap.oom_count}
            if snap.oom_count and events:
                reason = events[0]["reason"]
                excerpt = (f"{snap.oom_count} {reason} events across "
                           f"{snap.service} pods"
                           + (", pods in CrashLoopBackOff"
                              if reason == "OOMKilled" else ""))
            else:
                excerpt = (f"no abnormal pod events for {snap.service} "
                           "in window")
            return ToolResult(data, self.cite(
                f"k8s://prod/{snap.service}/events", excerpt))
        if tool == "rotate_certificate":
            service = args.get("service", snap.service)
            data = {
                "service": service,
                "rotated": True,
                "dry_run": args.get("dry_run", True),
                "undo": {"system": "kubernetes", "tool": "rotate_certificate",
                         "args": {"service": service, "restore": "previous"}},
            }
            return ToolResult(data, self.cite(
                f"k8s://prod/{service}/tls",
                f"certificate for {service} renewed and rotated (dry-run)"))
        if tool == "rollback_deployment":
            service = args.get("service", snap.service)
            revision = args.get("revision", snap.previous_revision)
            data = {
                "service": service,
                "rolled_back_to": revision,
                "dry_run": args.get("dry_run", True),
                # No undo descriptor (audit C2): a rollback to a known-good
                # revision is the safe terminal state. Auto-"undoing" it would
                # redeploy the bad revision — the opposite of safe. If a later
                # saga step fails, the platform leaves the service rolled back
                # and escalates to a human rather than reintroducing the fault.
            }
            return ToolResult(data, self.cite(
                f"k8s://prod/{service}/rollout",
                f"{service} rolled back to {revision} (dry-run)"))
        raise ValueError(tool)
