"""REST API: auth enforcement, incident lifecycle over HTTP (gate semantics
replayed), change scoring, runbook search (docs/17 acceptance #13–#14)."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from aetherops.api import server as api_server

TOKEN = "aetherops-dev"          # module default; tests pin it explicitly


def _request(port, path, method="GET", body=None, token=TOKEN):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode())


class TestServePreflight(unittest.TestCase):
    """Boot-time credential policy (audit C1). `serve()` must never register a
    publicly-known admin token silently, and never expose the dev token off
    loopback."""

    def test_no_token_no_optin_refuses_to_boot(self):
        with self.assertRaises(SystemExit):
            api_server._preflight({})

    def test_dev_optin_binds_loopback(self):
        self.assertEqual(
            api_server._preflight({"AETHEROPS_ALLOW_DEV_TOKEN": "1"}),
            "127.0.0.1")

    def test_dev_token_refused_on_public_interface(self):
        with self.assertRaises(SystemExit):
            api_server._preflight({"AETHEROPS_ALLOW_DEV_TOKEN": "1",
                                   "AETHEROPS_BIND": "0.0.0.0"})

    def test_real_token_binds_anywhere(self):
        self.assertEqual(
            api_server._preflight({"AETHEROPS_API_TOKEN": "s3cret",
                                   "AETHEROPS_BIND": "0.0.0.0"}),
            "0.0.0.0")

    def test_real_token_defaults_to_loopback(self):
        self.assertEqual(
            api_server._preflight({"AETHEROPS_API_TOKEN": "s3cret"}),
            "127.0.0.1")


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        api_server.STATE = api_server.AppState()      # isolate state
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         api_server.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_operator_console_served_at_root(self):
        for path in ("/", "/app", "/index.html"):
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.port}{path}")   # no auth — public UI
            with urllib.request.urlopen(request, timeout=30) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers["Content-Type"])
                html = resp.read().decode()
            self.assertIn("<title>AetherOps", html)
            # self-contained: no external asset dependencies (offline/CSP safe)
            self.assertNotIn("http-equiv=\"refresh\"", html)
            self.assertNotIn("cdn.", html)
            self.assertNotIn("<script src=", html)

    def test_incident_audit_chain_is_reachable_and_verified(self):
        # audit H3a/H7: the hash-chained ledger must be fetchable and
        # verifiable over the API, not write-only-then-discarded.
        _, created = _request(self.port, "/v1/incidents", "POST", {})
        inc = created["incident_id"]
        status, audit = _request(self.port, f"/v1/incidents/{inc}/audit")
        self.assertEqual(status, 200)
        self.assertTrue(audit["chain_verified"])
        self.assertGreater(audit["count"], 0)
        self.assertEqual(audit["incident_id"], inc)   # correlated by id
        actions = {r["action"] for r in audit["records"]}
        self.assertIn("workflow.start", actions)
        self.assertIn("model.call", actions)

    def test_audit_unknown_incident_is_404(self):
        try:
            _request(self.port, "/v1/incidents/inc_nope/audit")
            self.fail("expected 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_audit_requires_a_token(self):
        try:
            _request(self.port, "/v1/incidents/x/audit", token=None)
            self.fail("expected 401")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 401)

    def test_health_is_open(self):
        status, body = _request(self.port, "/health", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn("version", body)

    def test_mutations_require_bearer_token(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            _request(self.port, "/v1/incidents", method="POST", body={},
                     token=None)
        self.assertEqual(caught.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            _request(self.port, "/v1/incidents", method="POST", body={},
                     token="wrong-token")
        self.assertEqual(caught.exception.code, 401)

    def test_incident_lifecycle_over_http(self):
        status, created = _request(self.port, "/v1/incidents",
                                   method="POST", body={})
        self.assertEqual(status, 201)
        self.assertEqual(created["status"], "PAUSED")
        self.assertEqual(created["pending_gate"], "approval_gate")
        self.assertEqual(created["diagnosis"]["suspect_commit"], "c9a1f42")
        incident_id = created["incident_id"]

        status, fetched = _request(self.port,
                                   f"/v1/incidents/{incident_id}")
        self.assertEqual(fetched["approval_tier"], 2)

        status, resolved = _request(
            self.port, f"/v1/incidents/{incident_id}/approvals",
            method="POST",
            body={"decision": "approve", "fence": created["fence"]})
        self.assertEqual(resolved["status"], "SUCCEEDED")
        self.assertIn("# Postmortem", resolved["postmortem_excerpt"])
        self.assertTrue(resolved["follow_ups"])

        # Gate semantics: a resolved incident cannot be re-approved
        with self.assertRaises(urllib.error.HTTPError) as caught:
            _request(self.port, f"/v1/incidents/{incident_id}/approvals",
                     method="POST",
                     body={"decision": "approve", "fence": resolved["fence"]})
        self.assertEqual(caught.exception.code, 409)

    def test_denial_over_http_never_executes(self):
        _, created = _request(self.port, "/v1/incidents", method="POST",
                              body={})
        _, denied = _request(
            self.port, f"/v1/incidents/{created['incident_id']}/approvals",
            method="POST",
            body={"decision": "deny", "fence": created["fence"]})
        self.assertEqual(denied["status"], "FAILED")
        self.assertIn("denied", denied["error"])
        self.assertNotIn("postmortem_excerpt", denied)

    def test_approval_requires_a_fence(self):
        # audit M6: a decision without the current fence is rejected.
        _, created = _request(self.port, "/v1/incidents", method="POST",
                              body={})
        try:
            _request(self.port,
                     f"/v1/incidents/{created['incident_id']}/approvals",
                     method="POST", body={"decision": "approve"})   # no fence
            self.fail("expected 409")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 409)

    def test_non_object_body_does_not_500(self):
        # audit L2: a JSON array/string body must be coerced, not crash.
        status, _ = _request(self.port, "/v1/changes/score",
                             method="POST", body=[])
        self.assertEqual(status, 200)

    def test_non_ascii_bearer_is_401_not_500(self):
        # audit L2: a non-ASCII token must not raise inside compare_digest.
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/evals",
            headers={"Authorization": "Bearer café-token"})
        try:
            urllib.request.urlopen(request, timeout=30)
            self.fail("expected 401")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 401)
        except (UnicodeEncodeError, UnicodeError):
            self.skipTest("urllib rejects non-ASCII header client-side")

    def test_change_scoring_endpoint(self):
        _, scored = _request(
            self.port, "/v1/changes/score", method="POST",
            body={"service": "orders-service", "sha": "b7e21c9",
                  "title": "Update README copy", "diff": "- a\n+ b",
                  "peak_window": True})
        self.assertEqual(scored["band"], "LOW")
        self.assertFalse(scored["requires_approval"])

    def test_runbook_search_endpoint(self):
        _, body = _request(self.port,
                           "/v1/runbooks/search?q=OOMKilled+pods")
        docs = {hit["doc"] for hit in body["results"]}
        self.assertIn("runbook-oom", docs)
        self.assertTrue(all(hit["ref"].startswith("rag://")
                            for hit in body["results"]))


class TestPersistence(unittest.TestCase):
    """Phase K: with AETHEROPS_DB set, organizational memory is durable —
    incidents write learned episodes to a shared store that survives a
    restart and feeds change-risk scoring (the flywheel)."""

    def test_learning_persists_across_restart(self):
        import os
        import tempfile

        from aetherops.demo import build_demo_environment
        from aetherops.workflows.incident_remediation import \
            run_incident_remediation

        with tempfile.TemporaryDirectory() as directory:
            os.environ["AETHEROPS_DB"] = os.path.join(directory, "aether.db")
            try:
                state = api_server.AppState()
                self.assertEqual(state.db_path,
                                 os.environ["AETHEROPS_DB"])
                self.assertEqual(len(state.memory), 1)          # seeded once

                incident, env = build_demo_environment()
                env["memory"] = state.memory                    # shared/durable
                paused, ctx = run_incident_remediation(incident, **env)
                run_incident_remediation(
                    incident, **env, ctx=ctx,
                    approvals={paused.pending_gate: True},
                    checkpoint=paused.checkpoint)
                self.assertEqual(len(state.memory), 2)          # learned + seed

                restarted = api_server.AppState()               # new process
                self.assertEqual(len(restarted.memory), 2)      # durable
                state.memory.close()
                restarted.memory.close()
            finally:
                os.environ.pop("AETHEROPS_DB", None)

    def test_default_mode_is_in_memory(self):
        state = api_server.AppState()
        self.assertIsNone(state.db_path)
        self.assertEqual(len(state.memory), 1)
        self.assertIsNone(state.audit_dir)

    def test_audit_chain_persists_and_reverifies_after_restart(self):
        import os
        import tempfile

        from aetherops.demo import build_demo_environment
        from aetherops.security.audit import AuditLog
        from aetherops.workflows.incident_remediation import \
            run_incident_remediation

        with tempfile.TemporaryDirectory() as directory:
            os.environ["AETHEROPS_DB"] = os.path.join(directory, "aether.db")
            try:
                state = api_server.AppState()
                incident, env = build_demo_environment()
                env["memory"] = state.memory
                path = os.path.join(state.audit_dir, f"{incident.id}.jsonl")
                env["audit"].attach_path(path)
                paused, ctx = run_incident_remediation(incident, **env)
                run_incident_remediation(
                    incident, **env, ctx=ctx,
                    approvals={paused.pending_gate: True},
                    checkpoint=paused.checkpoint)

                # Reload from disk exactly as GET /audit does post-restart.
                reloaded = AuditLog.load(path)
                self.assertEqual(len(reloaded), len(env["audit"].records))
                self.assertGreater(len(reloaded), 20)
                self.assertTrue(reloaded.verify())      # governance survived
                state.memory.close()
            finally:
                os.environ.pop("AETHEROPS_DB", None)


if __name__ == "__main__":
    unittest.main()
