"""Grounding checks (audit C4/H5): the failure class is deterministic (not
settable by model/injected text), the offline diagnoser declines on a
direction-inconsistent change, and the reviewer's falsifiable checks
(temporal precedence, mechanism consistency) do what they claim."""
import unittest
from types import SimpleNamespace

from aetherops.agents.reviewer import (_diff_raises_resource, _first_breach,
                                       _minute_of_day)
from aetherops.agents.root_cause import RootCauseAgent
from aetherops.gateway.offline import _pool_reduced, respond


def _ev(kind, summary):
    return SimpleNamespace(kind=kind, summary=summary)


class _Ctx:
    def __init__(self, evidence):
        self.evidence = evidence

    def evidence_of_kind(self, *kinds):
        return [e for e in self.evidence if e.kind in kinds]


class TestDeterministicClass(unittest.TestCase):
    def test_injected_class_token_cannot_flip_the_class(self):
        # OOM + pool commit + real deploy => memory regression, regardless of
        # any "cert-expiry/tls" string a model emits or an attacker injects.
        ctx = _Ctx([_ev("k8s-event", "12 OOMKilled events"),
                    _ev("commit", "abc1234: Raise DB connection pool max_size"),
                    _ev("deploy", "deploys: [{revision: v3}]")])
        self.assertEqual(RootCauseAgent._classify(ctx),
                         "deploy-regression/memory")

    def test_cert_requires_tls_markers_and_no_deploy(self):
        cert = _Ctx([_ev("k8s-event", "34 TLSHandshakeError events"),
                     _ev("deploy", "no deployments in the lookback window")])
        self.assertEqual(RootCauseAgent._classify(cert), "cert-expiry/tls")
        # No corroborating markers => unclassified, not a guessed class.
        vague = _Ctx([_ev("metrics", "latency high"),
                      _ev("deploy", "no deployments in the lookback window")])
        self.assertEqual(RootCauseAgent._classify(vague), "unclassified")

    def test_no_deployments_line_is_not_a_deploy(self):
        # A "no deployments" evidence line must not count as a real deploy.
        ctx = _Ctx([_ev("k8s-event", "OOMKilled"),
                    _ev("commit", "abc: connection pool max_size"),
                    _ev("deploy", "no deployments for this service")])
        self.assertEqual(RootCauseAgent._classify(ctx), "unclassified")


class TestOfflineDirection(unittest.TestCase):
    def test_pool_reduced_detects_decrease_only(self):
        self.assertTrue(_pool_reduced("Lower DB connection pool max_size"))
        self.assertTrue(_pool_reduced("connection pool max_size 200 -> 20"))
        self.assertFalse(_pool_reduced("Raise DB connection pool 20 -> 200"))

    def test_diagnose_declines_on_pool_reduction(self):
        digest = (
            "[E1] (metrics, datadog) p99 jumped 100 -> 1900\n"
            "[E2] (deploy, github) deployed v4 | ref: github://x/deploys/v4\n"
            "[E3] (commit, github) abc1234: Lower DB connection pool "
            "max_size 200 -> 20 | ref: github://commit/abc1234\n"
            "[E4] (k8s-event, kubernetes) 9 OOMKilled events\n")
        out = respond("[root_cause]\n" + digest, "root_cause")
        self.assertIn("Insufficient evidence", out)
        self.assertIn("REDUCES", out)

    def test_diagnose_still_grounds_a_genuine_increase(self):
        digest = (
            "[E1] (metrics, datadog) p99 jumped 100 -> 1900\n"
            "[E2] (deploy, github) deployed v4 | ref: github://x/deploys/v4\n"
            "[E3] (commit, github) abc1234: Raise DB connection pool "
            "max_size 20 -> 200 | ref: github://commit/abc1234\n"
            "[E4] (k8s-event, kubernetes) 9 OOMKilled events\n")
        out = respond("[root_cause]\n" + digest, "root_cause")
        self.assertIn("deploy-regression/memory", out)


class TestReviewerHelpers(unittest.TestCase):
    def test_first_breach_is_data_driven(self):
        series = [{"ts": "14:00Z", "p99_ms": 181},
                  {"ts": "14:05Z", "p99_ms": 2140},
                  {"ts": "14:10Z", "p99_ms": 2412}]
        self.assertEqual(_first_breach(series), "14:05Z")
        self.assertIsNone(_first_breach([]))

    def test_minute_of_day_handles_both_formats(self):
        self.assertEqual(_minute_of_day("2026-08-07T14:02:00Z"), 842)
        self.assertEqual(_minute_of_day("14:05Z"), 845)
        self.assertIsNone(_minute_of_day(None))
        # temporal precedence: 14:02 deploy precedes 14:05 breach
        self.assertLess(_minute_of_day("2026-08-07T14:02:00Z"),
                        _minute_of_day("14:05Z"))

    def test_diff_direction(self):
        self.assertTrue(_diff_raises_resource("-  max_size: 20\n+  max_size: 200"))
        self.assertFalse(
            _diff_raises_resource("-  max_size: 200\n+  max_size: 20"))


if __name__ == "__main__":
    unittest.main()
