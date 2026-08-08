"""Backend chain: Ollama wire protocol, fallback semantics, dead-marking,
configuration, and latency/cost metering (docs/17 acceptance #1–4, #17)."""
import http.server
import json
import threading
import unittest

from aetherops.core.types import WorkflowStatus
from aetherops.demo import build_demo_environment
from aetherops.gateway.backends import (BackendResult, BackendUnavailable,
                                        OfflineHeuristicBackend, OllamaBackend,
                                        build_backend_chain)
from aetherops.gateway.model_gateway import ModelGateway, TaskProfile
from aetherops.security.audit import AuditLog
from aetherops.workflows.incident_remediation import run_incident_remediation


class _FakeOllamaHandler(http.server.BaseHTTPRequestHandler):
    """Speaks Ollama's real /api/generate wire format."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reply = json.dumps({
            "model": body["model"],
            "response": f"LIVE:{body['prompt'][:20]}",
            "prompt_eval_count": 42,
            "eval_count": 7,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):        # keep test output clean
        pass


class _DyingBackend:
    """Succeeds never — used to prove the fallback path."""

    name = "dying"

    def __init__(self):
        self.calls = 0

    def complete(self, model_id, prompt, task):
        self.calls += 1
        raise BackendUnavailable("dying: simulated outage")


class TestOllamaProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0),
                                            _FakeOllamaHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_speaks_the_real_wire_format(self):
        backend = OllamaBackend(base_url=f"http://127.0.0.1:{self.port}",
                                model="testmodel")
        result = backend.complete("claude-opus-5", "diagnose this", "root_cause")
        self.assertIsInstance(result, BackendResult)
        self.assertTrue(result.text.startswith("LIVE:"))
        self.assertEqual((result.tokens_in, result.tokens_out), (42, 7))
        self.assertEqual(result.served_model, "ollama/testmodel")

    def test_unreachable_server_marks_backend_dead(self):
        backend = OllamaBackend(base_url="http://127.0.0.1:9", timeout=0.5)
        with self.assertRaises(BackendUnavailable):
            backend.complete("m", "p", "t")
        self.assertTrue(backend._dead)
        with self.assertRaises(BackendUnavailable):   # skipped, no new attempt
            backend.complete("m", "p", "t")


class TestFallbackChain(unittest.TestCase):
    def test_dead_backend_falls_through_with_audit(self):
        audit = AuditLog()
        dying = _DyingBackend()
        gateway = ModelGateway(audit=audit,
                               backends=[dying, OfflineHeuristicBackend()])
        response = gateway.complete("[triage] service=checkout-service",
                                    TaskProfile(task="triage", tier_hint="fast"))
        self.assertEqual(response.backend, "offline")
        self.assertEqual(dying.calls, 1)
        fallbacks = [r for r in audit.records
                     if r.action == "backend.fallback"]
        self.assertEqual(len(fallbacks), 1)
        self.assertEqual(fallbacks[0].payload["failed_backend"], "dying")
        self.assertEqual(fallbacks[0].payload["next_backend"], "offline")

    def test_all_backends_failing_raises(self):
        gateway = ModelGateway(backends=[_DyingBackend(), _DyingBackend()])
        with self.assertRaises(RuntimeError):
            gateway.complete("p", TaskProfile(task="triage"))

    def test_workflow_survives_a_dying_primary_backend(self):
        """Acceptance #1: kill the model mid-incident; the incident still
        resolves on the fallback, audited."""
        incident, env = build_demo_environment()
        env["gateway"] = ModelGateway(
            audit=env["audit"],
            backends=[_DyingBackend(), OfflineHeuristicBackend()])
        paused, ctx = run_incident_remediation(incident, **env)
        done, ctx = run_incident_remediation(
            incident, **env, ctx=ctx,
            approvals={paused.pending_gate: True}, checkpoint=paused.checkpoint)
        self.assertEqual(done.status, WorkflowStatus.SUCCEEDED)
        self.assertTrue(any(r.action == "backend.fallback"
                            for r in env["audit"].records))


class TestConfiguration(unittest.TestCase):
    def test_chain_order_follows_spec(self):
        chain = build_backend_chain("ollama,offline")
        self.assertEqual([b.name for b in chain], ["ollama", "offline"])

    def test_default_is_offline_only(self):
        self.assertEqual([b.name for b in build_backend_chain(None)],
                         ["offline"])

    def test_unknown_backend_rejected(self):
        with self.assertRaises(ValueError):
            build_backend_chain("gpt99")


class TestMetering(unittest.TestCase):
    def test_response_carries_latency_and_cost(self):
        gateway = ModelGateway()
        response = gateway.complete(
            "[triage] service=checkout-service",
            TaskProfile(task="triage", tier_hint="fast"))
        self.assertGreaterEqual(response.latency_ms, 0.0)
        self.assertGreater(response.tokens_in, 0)
        self.assertGreater(response.tokens_out, 0)
        self.assertEqual(response.tokens,
                         response.tokens_in + response.tokens_out)
        # fast tier: $1/M in, $5/M out (docs/13 planning assumptions)
        expected = round(response.tokens_in / 1e6 * 1.0
                         + response.tokens_out / 1e6 * 5.0, 6)
        self.assertEqual(response.est_cost_usd, expected)
        self.assertEqual(gateway.est_cost_usd, expected)

    def test_audit_records_carry_metering(self):
        audit = AuditLog()
        gateway = ModelGateway(audit=audit)
        gateway.complete("p", TaskProfile(task="triage", tier_hint="fast"))
        call = next(r.payload for r in audit.records
                    if r.action == "model.call")
        for key in ("backend", "served_model", "tokens_in", "tokens_out",
                    "latency_ms", "est_cost_usd"):
            self.assertIn(key, call)


if __name__ == "__main__":
    unittest.main()
