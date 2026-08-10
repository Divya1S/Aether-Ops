"""Real HTTP connector adapters (Phase Q): request-building + response-mapping
against MOCKED HTTP (no network), the write-safety of dry-run, that real
calls still flow through the gateway (redaction + audit), and that the
factory falls back to fakes when unconfigured — so demos/eval/CI are
unaffected."""
import io
import json
import os
import unittest
from unittest import mock

from aetherops.connectors.adapters import (GitHubConnector,
                                           PrometheusConnector,
                                           build_live_registry,
                                           connector_roster)
from aetherops.connectors.fakes import FakeGitHub
from aetherops.security.audit import AuditLog


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(payload):
    return lambda request, timeout=None: _Resp(json.dumps(payload).encode())


class TestGitHubAdapter(unittest.TestCase):
    def setUp(self):
        self.gh = GitHubConnector(repo="octo/checkout", token="ghp_x")

    def test_deployments_map_to_expected_shape(self):
        payload = [
            {"sha": "c9a1f42abc", "created_at": "2026-08-07T14:02:00Z"},
            {"sha": "prevsha123"},
        ]
        with mock.patch("urllib.request.urlopen", _urlopen(payload)):
            result = self.gh.call("list_recent_deploys", {"service": "checkout"},
                                  principal="planner")
        deploy = result.data["deploys"][0]
        self.assertEqual(deploy["revision"], "c9a1f42abc")
        self.assertEqual(deploy["previous_revision"], "prevsha123")
        self.assertEqual(deploy["deployed_at"], "2026-08-07T14:02:00Z")

    def test_commit_diff_maps_title_author_and_patch(self):
        payload = {
            "sha": "c9a1f42",
            "commit": {"message": "Raise pool 20 -> 200\n\ndetail",
                       "author": {"name": "j.doe"}},
            "files": [{"patch": "-  max: 20\n+  max: 200"}],
        }
        with mock.patch("urllib.request.urlopen", _urlopen(payload)):
            result = self.gh.call("get_commit_diff", {"sha": "c9a1f42"},
                                  principal="planner")
        self.assertEqual(result.data["title"], "Raise pool 20 -> 200")
        self.assertEqual(result.data["author"], "j.doe")
        self.assertIn("+  max: 200", result.data["diff"])

    def test_writes_are_dry_run_and_never_hit_the_network(self):
        # No urlopen patch: if the write tried HTTP, this would raise.
        revert = self.gh.call("create_revert_pr", {"sha": "c9a1f42"},
                              principal="executor")
        self.assertTrue(revert.data["dry_run"])
        self.assertEqual(revert.data["undo"]["tool"], "close_pr")
        closed = self.gh.call("close_pr", {"pr": "dry-run"},
                              principal="compensator")
        self.assertTrue(closed.data["dry_run"])

    def test_real_call_is_redacted_and_audited(self):
        audit = AuditLog()
        gh = GitHubConnector(audit=audit, repo="octo/x")
        payload = {"sha": "s", "commit": {"message": "leak", "author": {}},
                   "files": [{"patch": "+ token=ghp_SUPERSECRETVALUE1234"}]}
        with mock.patch("urllib.request.urlopen", _urlopen(payload)):
            result = gh.call("get_commit_diff", {"sha": "s"}, principal="planner")
        self.assertNotIn("ghp_SUPERSECRETVALUE1234", result.data["diff"])
        self.assertTrue(any(r.action == "tool.call" for r in audit.records))

    def test_is_a_drop_in_for_the_fake(self):
        self.assertEqual(GitHubConnector.system, "github")
        self.assertEqual(set(GitHubConnector.TOOLS), set(FakeGitHub.TOOLS))


class TestPrometheusAdapter(unittest.TestCase):
    def test_range_query_maps_to_series(self):
        payload = {"data": {"result": [
            {"metric": {}, "values": [[1000, "0.181"], [1300, "2.14"]]}]}}
        prom = PrometheusConnector(base_url="http://prom:9090")
        with mock.patch("urllib.request.urlopen", _urlopen(payload)):
            result = prom.call("query_metrics", {}, principal="knowledge")
        self.assertEqual([s["p99_ms"] for s in result.data["series"]],
                         [181.0, 2140.0])           # seconds -> milliseconds

    def test_fills_the_datadog_metrics_slot(self):
        self.assertEqual(PrometheusConnector(base_url="x").system, "datadog")


class TestFactory(unittest.TestCase):
    def tearDown(self):
        for key in ("AETHEROPS_GITHUB_REPO", "AETHEROPS_PROMETHEUS_URL"):
            os.environ.pop(key, None)

    def test_unconfigured_defaults_to_all_fakes(self):
        for key in ("AETHEROPS_GITHUB_REPO", "AETHEROPS_PROMETHEUS_URL"):
            os.environ.pop(key, None)
        registry = build_live_registry()
        self.assertIsInstance(registry.get("github"), FakeGitHub)
        self.assertEqual(connector_roster()["github"], "fake")

    def test_env_config_swaps_in_real_adapters(self):
        os.environ["AETHEROPS_GITHUB_REPO"] = "octo/x"
        os.environ["AETHEROPS_PROMETHEUS_URL"] = "http://prom:9090"
        registry = build_live_registry()
        self.assertIsInstance(registry.get("github"), GitHubConnector)
        self.assertIsInstance(registry.get("datadog"), PrometheusConnector)
        roster = connector_roster()
        self.assertEqual(roster["github"], "real:github")
        self.assertEqual(roster["datadog"], "real:prometheus")
        self.assertEqual(roster["kubernetes"], "fake")


if __name__ == "__main__":
    unittest.main()
