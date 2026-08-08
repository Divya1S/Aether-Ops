"""Serve the AetherOps API:  PYTHONPATH=src python3 -m aetherops.api"""
import os

from aetherops.api.server import serve

if __name__ == "__main__":
    serve(port=int(os.environ.get("PORT", "8080")))
