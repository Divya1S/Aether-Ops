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
            method="POST", body={"decision": "approve"})
        self.assertEqual(resolved["status"], "SUCCEEDED")
        self.assertIn("# Postmortem", resolved["postmortem_excerpt"])
        self.assertTrue(resolved["follow_ups"])

        # Gate semantics: a resolved incident cannot be re-approved
        with self.assertRaises(urllib.error.HTTPError) as caught:
            _request(self.port, f"/v1/incidents/{incident_id}/approvals",
                     method="POST", body={"decision": "approve"})
        self.assertEqual(caught.exception.code, 409)

    def test_denial_over_http_never_executes(self):
        _, created = _request(self.port, "/v1/incidents", method="POST",
                              body={})
        _, denied = _request(
            self.port, f"/v1/incidents/{created['incident_id']}/approvals",
            method="POST", body={"decision": "deny"})
        self.assertEqual(denied["status"], "FAILED")
        self.assertIn("denied", denied["error"])
        self.assertNotIn("postmortem_excerpt", denied)

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


if __name__ == "__main__":
    unittest.main()
