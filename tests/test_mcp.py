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


if __name__ == "__main__":
    unittest.main()
