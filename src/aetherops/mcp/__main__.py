"""MCP stdio entry point:  PYTHONPATH=src python3 -m aetherops.mcp"""
from aetherops.mcp.server import main

if __name__ == "__main__":
    raise SystemExit(main())
