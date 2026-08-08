"""Demo environment: the canonical golden scenario, served through the same
builder the evaluation harness uses (docs/10-evaluation.md) — the demo IS a
replay of golden scenario s1.
"""
from __future__ import annotations

from aetherops.evals.scenarios import build_environment, canonical


def build_demo_environment(audit_path: str | None = None,
                           backends_spec: str | None = None):
    """`backends_spec=None` honors AETHEROPS_BACKENDS (default offline);
    pass "ollama,offline" for live-model mode with graceful fallback."""
    return build_environment(canonical(), audit_path=audit_path,
                             backends_spec=backends_spec)
