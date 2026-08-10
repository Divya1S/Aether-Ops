"""Real HTTP connector adapters (docs/04-connectivity.md).

The fakes serve a frozen Snapshot; these adapters talk to actual systems over
HTTP, so "connectors are pluggable — production swaps in real adapters" is
true in code, not just prose. They are:

  - GitHubConnector       real GitHub API reads (deployments, commit diff);
                          writes are dry-run by default (no side effects, $0).
  - PrometheusConnector   real Prometheus range query for the metrics slot.

They are OPT-IN via environment and default OFF: `build_live_registry` returns
real adapters only where configured and falls back to the fakes otherwise, so
demos, evals, and CI are unchanged (they never set the env and never hit the
network). Reads use only free endpoints; the writes never mutate a real repo.
Unit-tested against mocked HTTP — the request-building and response-mapping
logic is exercised without a network.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from aetherops.agents.base import TransientError
from aetherops.connectors.base import (Connector, ConnectorRegistry,
                                       RateLimitExceeded, ToolResult)
from aetherops.connectors.fakes import (FakeDatadog, FakeGitHub,
                                        FakeKubernetes, FakePagerDuty,
                                        FakeSlack, Snapshot)


class HttpConnector(Connector):
    """Connector that reaches a real system over HTTP. `urlopen` is the single
    seam mocked in tests; errors map onto the platform's retry taxonomy."""

    def __init__(self, audit=None, clock=time.monotonic, timeout: float = 10.0):
        super().__init__(audit=audit, clock=clock)
        self._timeout = timeout

    def _get_json(self, url: str, headers: dict | None = None) -> object:
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:                     # rate limited -> retryable
                raise RateLimitExceeded(f"{self.system}: HTTP 429") from exc
            raise TransientError(f"{self.system}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TransientError(f"{self.system}: {exc}") from exc


class GitHubConnector(HttpConnector):
    """Real GitHub API adapter (system 'github'). Reads deployments and commit
    diffs from the free REST API; the two write tools are dry-run so the
    adapter can never mutate a real repository ($0, side-effect-free)."""

    system = "github"
    TOOLS = FakeGitHub.TOOLS                          # same catalog: drop-in

    API = "https://api.github.com"

    def __init__(self, audit=None, clock=time.monotonic, repo: str | None = None,
                 token: str | None = None):
        super().__init__(audit=audit, clock=clock)
        self.repo = repo or os.environ.get("AETHEROPS_GITHUB_REPO", "")
        self._token = token or os.environ.get("AETHEROPS_GITHUB_TOKEN", "")

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "aetherops"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _invoke(self, tool: str, args: dict) -> ToolResult:
        if tool == "list_recent_deploys":
            deployments = self._get_json(
                f"{self.API}/repos/{self.repo}/deployments?per_page=10",
                self._headers())
            deploys = deployments if isinstance(deployments, list) else []
            latest = deploys[0] if deploys else {}
            data = {"deploys": ([{
                "service": args.get("service", self.repo.rsplit("/", 1)[-1]),
                "revision": latest.get("sha", ""),
                "previous_revision": (deploys[1].get("sha", "")
                                      if len(deploys) > 1 else ""),
                "deployed_at": latest.get("created_at", ""),
                "commits": [latest.get("sha", "")],
            }] if latest else [])}
            return ToolResult(data, self.cite(
                f"github://{self.repo}/deployments",
                f"{len(deploys)} recent deployments for {self.repo}"))

        if tool == "get_commit_diff":
            sha = args.get("sha", "")
            commit = self._get_json(
                f"{self.API}/repos/{self.repo}/commits/{sha}", self._headers())
            message = commit.get("commit", {}).get("message", "")
            files = commit.get("files", []) or []
            diff = "\n".join(f.get("patch", "") for f in files if f.get("patch"))
            data = {
                "sha": sha,
                "title": message.splitlines()[0] if message else "",
                "author": commit.get("commit", {}).get("author", {}).get("name", ""),
                "diff": diff,
            }
            return ToolResult(data, self.cite(
                f"github://commit/{sha}", f"commit {sha}: {data['title']}"))

        # Writes are DRY-RUN: return the intended action + undo, never mutate.
        if tool == "create_revert_pr":
            sha = args.get("sha", "")
            return ToolResult({
                "pr_url": f"github://{self.repo}/pull/dry-run",
                "reverts": sha, "dry_run": True,
                "undo": {"system": "github", "tool": "close_pr",
                         "args": {"pr": "dry-run"}},
            }, self.cite(f"github://{self.repo}/pull/dry-run",
                         f"(dry-run) revert PR for {sha}"))
        if tool == "close_pr":
            pr = args.get("pr", "")
            return ToolResult({"closed": pr, "dry_run": True}, self.cite(
                f"github://{self.repo}/pull/{pr}", f"(dry-run) closed PR {pr}"))
        raise ValueError(tool)


class PrometheusConnector(HttpConnector):
    """Real Prometheus adapter filling the metrics slot (registers as 'datadog'
    so it is a drop-in for FakeDatadog). Runs a range query and maps the
    samples to the {series: [{ts, p99_ms}]} shape the agents expect."""

    TOOLS = FakeDatadog.TOOLS

    def __init__(self, audit=None, clock=time.monotonic, system: str = "datadog",
                 base_url: str | None = None, query: str | None = None):
        super().__init__(audit=audit, clock=clock)
        self.system = system
        self.base_url = (base_url
                         or os.environ.get("AETHEROPS_PROMETHEUS_URL", "")
                         ).rstrip("/")
        self.query = query or os.environ.get(
            "AETHEROPS_PROMETHEUS_QUERY",
            "histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))")

    def _invoke(self, tool: str, args: dict) -> ToolResult:
        promql = args.get("query", self.query)
        now = int(time.time())
        url = (f"{self.base_url}/api/v1/query_range?query="
               f"{urllib.parse.quote(promql)}"
               f"&start={now - 900}&end={now}&step=300")
        payload = self._get_json(url)
        result = payload.get("data", {}).get("result", [])
        values = result[0].get("values", []) if result else []
        series = [{"ts": str(ts), "p99_ms": round(float(v) * 1000, 1)}
                  for ts, v in values]
        return ToolResult(
            {"query": promql, "series": series},
            self.cite(f"prometheus://query/{tool}",
                      f"{len(series)} samples for '{promql[:40]}'"))


def _github_configured() -> bool:
    return bool(os.environ.get("AETHEROPS_GITHUB_REPO"))


def _prometheus_configured() -> bool:
    return bool(os.environ.get("AETHEROPS_PROMETHEUS_URL"))


def connector_roster() -> dict:
    """Which slot is served by a real adapter vs a fake, per current env — for
    a GET /v1/connectors report and for tests."""
    return {
        "github": "real:github" if _github_configured() else "fake",
        "datadog": "real:prometheus" if _prometheus_configured() else "fake",
        "pagerduty": "fake", "kubernetes": "fake", "slack": "fake",
    }


def build_live_registry(audit=None, snapshot: Snapshot | None = None
                        ) -> ConnectorRegistry:
    """Real adapters where env-configured, fakes otherwise (default: all fakes).
    Opt-in and non-breaking — nothing calls this in the demo/eval paths."""
    snapshot = snapshot or Snapshot()
    registry = ConnectorRegistry()
    registry.register(FakePagerDuty(audit=audit, snapshot=snapshot))
    registry.register(FakeKubernetes(audit=audit, snapshot=snapshot))
    registry.register(FakeSlack(audit=audit, snapshot=snapshot))
    registry.register(
        PrometheusConnector(audit=audit) if _prometheus_configured()
        else FakeDatadog(audit=audit, snapshot=snapshot))
    registry.register(
        GitHubConnector(audit=audit) if _github_configured()
        else FakeGitHub(audit=audit, snapshot=snapshot))
    return registry
