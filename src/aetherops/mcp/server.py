"""Model Context Protocol server (docs/04's design made literal; docs/17
acceptance #18).

Speaks MCP's stdio transport: newline-delimited JSON-RPC 2.0. Implements
the core handshake (initialize / notifications/initialized) plus
tools/list and tools/call, exposing read-only platform tools so any MCP
client — Claude Code included — can query AetherOps:

    {"mcpServers": {"aetherops": {"command": "python3",
        "args": ["-m", "aetherops.mcp"],
        "env": {"PYTHONPATH": "src"}}}}

Pure stdlib; tools are read-only by design (the write path stays behind
the governed API and its approval gates).
"""
from __future__ import annotations

import json
import sys

from aetherops import __version__
from aetherops.evals.harness import run_all
from aetherops.evals.retrieval import run_retrieval_eval
from aetherops.rag.corpus import SEED_RUNBOOKS
from aetherops.rag.retriever import RagStore

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "search_runbooks",
        "description": "Hybrid (keyword+vector) search over AetherOps' "
                       "operational runbooks; returns attributed chunks "
                       "(rag://doc#offset).",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"]},
    },
    {
        "name": "list_runbooks",
        "description": "List the runbook corpus: id and title per document.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "eval_summary",
        "description": "Run the golden-scenario and retrieval evaluations "
                       "and return aggregate metrics, trust-ladder verdicts, "
                       "and release-gate status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_STORE: RagStore | None = None


def _store() -> RagStore:
    global _STORE
    if _STORE is None:
        _STORE = RagStore()
    return _STORE


def _call_tool(name: str, arguments: dict) -> dict:
    if name == "search_runbooks":
        hits = [{"doc": r.chunk.doc_id, "title": r.chunk.doc_title,
                 "ref": r.ref, "score": r.score,
                 "excerpt": r.chunk.text[:200]}
                for r in _store().search(arguments.get("query", ""), k=5)]
        return {"results": hits}
    if name == "list_runbooks":
        return {"runbooks": [{"id": d.id, "title": d.title}
                             for d in SEED_RUNBOOKS]}
    if name == "eval_summary":
        report = run_all()
        return {"aggregates": report["aggregates"],
                "trust_ladder": report["trust_ladder"],
                "release_gate": report["release_gate"],
                "retrieval": run_retrieval_eval()}
    raise ValueError(f"unknown tool {name!r}")


def handle(message: dict) -> dict | None:
    """Returns the JSON-RPC response, or None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        params = message.get("params", {})
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": params.get("protocolVersion",
                                          PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aetherops", "version": __version__}}}

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id,
                "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = message.get("params", {})
        try:
            payload = _call_tool(params.get("name", ""),
                                 params.get("arguments", {}) or {})
            result = {"content": [{"type": "text",
                                   "text": json.dumps(payload,
                                                      default=str)}],
                      "isError": False}
        except Exception as exc:
            result = {"content": [{"type": "text", "text": str(exc)}],
                      "isError": True}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if msg_id is not None:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601,
                          "message": f"method not found: {method}"}}
    return None


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0
