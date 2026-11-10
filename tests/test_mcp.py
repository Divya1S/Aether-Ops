"""MCP server: raw newline-delimited JSON-RPC 2.0 against a spawned
subprocess — the wire protocol itself, not the internals (docs/17 #18)."""
import json
import os
import subprocess
import sys
import unittest

REPO_SRC = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src")


class TestMcpWireProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = {**os.environ, "PYTHONPATH": REPO_SRC}
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "aetherops.mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, env=env)

    @classmethod
    def tearDownClass(cls):
        cls.proc.stdin.close()
        cls.proc.wait(timeout=10)

    def _rpc(self, message: dict) -> dict | None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        if "id" not in message:
            return None
        return json.loads(self.proc.stdout.readline())

    def test_full_session(self):
        init = self._rpc({"jsonrpc": "2.0", "id": 1,
                          "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18",
                                     "capabilities": {},
                                     "clientInfo": {"name": "test",
                                                    "version": "0"}}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "aetherops")
        self.assertIn("tools", init["result"]["capabilities"])

        self._rpc({"jsonrpc": "2.0",
                   "method": "notifications/initialized"})

        listing = self._rpc({"jsonrpc": "2.0", "id": 2,
                             "method": "tools/list"})
        tools = {t["name"] for t in listing["result"]["tools"]}
        self.assertGreaterEqual(len(tools), 3)
        self.assertLessEqual({"search_runbooks", "list_runbooks",
                              "eval_summary"}, tools)

        call = self._rpc({"jsonrpc": "2.0", "id": 3,
                          "method": "tools/call",
                          "params": {"name": "search_runbooks",
                                     "arguments": {"query":
                                                   "OOMKilled pods"}}})
        self.assertFalse(call["result"]["isError"])
        payload = json.loads(call["result"]["content"][0]["text"])
        self.assertIn("runbook-oom",
                      {hit["doc"] for hit in payload["results"]})

        unknown = self._rpc({"jsonrpc": "2.0", "id": 4,
                             "method": "resources/list"})
        self.assertEqual(unknown["error"]["code"], -32601)

    def _call(self, msg_id: int, name: str, arguments: dict) -> dict:
        reply = self._rpc({"jsonrpc": "2.0", "id": msg_id,
                           "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}})
        self.assertFalse(reply["result"]["isError"],
                         reply["result"]["content"][0]["text"])
        return json.loads(reply["result"]["content"][0]["text"])

    def test_investigate_from_an_ide_stops_at_the_gate(self):
        listing = self._rpc({"jsonrpc": "2.0", "id": 10,
                             "method": "tools/list"})
        tools = {t["name"]: t for t in listing["result"]["tools"]}
        self.assertLessEqual({"investigate", "list_scenarios",
                              "score_change", "service_topology"},
                             set(tools))
        # Read-only by construction: no tool can decide a gate or execute.
        self.assertFalse(any(word in name for name in tools
                             for word in ("approve", "execute", "rollback")))

        scenarios = self._call(11, "list_scenarios", {})["scenarios"]
        self.assertIn("s6-deploy-after-breach",
                      {s["id"] for s in scenarios})

        canonical = self._call(12, "investigate", {})
        self.assertEqual(canonical["status"], "PAUSED")       # never past it
        self.assertEqual(canonical["pending_gate"], "approval_gate")
        self.assertEqual(canonical["hypotheses"]["validated"],
                         "deploy-regression/memory")
        self.assertEqual([s["action"] for s in canonical["plan"]],
                         ["rollback_deployment", "create_revert_pr"])

        adversarial = self._call(13, "investigate",
                                 {"scenario": "s6-deploy-after-breach"})
        self.assertEqual(adversarial["outcome"], "escalated")
        ruled_out = {h["id"]: h["ruled_out_by"]
                     for h in adversarial["hypotheses"]["hypotheses"]}
        self.assertEqual(ruled_out["H1"], ["temporal-precedence"])

        bad = self._rpc({"jsonrpc": "2.0", "id": 14, "method": "tools/call",
                         "params": {"name": "investigate",
                                    "arguments": {"scenario": "nope"}}})
        self.assertTrue(bad["result"]["isError"])

    def test_change_scoring_and_topology_tools(self):
        risky = self._call(20, "score_change", {
            "service": "orders-service",
            "title": "Raise DB connection pool max_size 25 -> 250",
            "diff": "- max_size: 25\n+ max_size: 250", "peak_window": True})
        benign = self._call(21, "score_change", {
            "service": "orders-service", "title": "Update README copy",
            "diff": "- old\n+ new", "peak_window": True})
        self.assertGreater(risky["score"], benign["score"])
        self.assertGreater(risky["components"]["failure_signature"], 0)
        self.assertEqual(benign["components"]["failure_signature"], 0)

        topology = self._call(22, "service_topology",
                              {"service": "payments-service"})
        self.assertEqual(len(topology["focus"]["blast_radius"]), 4)


if __name__ == "__main__":
    unittest.main()
