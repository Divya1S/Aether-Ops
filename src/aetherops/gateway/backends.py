"""Model backends and the fallback chain (docs/07-model-gateway.md,
docs/17 §Phase 9 Milestone 6).

A backend is anything with a `name` and
`complete(model_id, prompt, task) -> BackendResult`. The gateway tries
backends in configured order; a backend that raises is skipped (audited as
`backend.fallback`) and the next one serves the call. The offline heuristic
is always a valid last link, so the platform can never lose its brain.

Configuration (environment):
- AETHEROPS_BACKENDS      comma-ordered chain, e.g. "ollama,offline"
                          (default: "offline" — live mode is an explicit
                          opt-in so tests and evals stay deterministic)
- AETHEROPS_OLLAMA_URL    default http://localhost:11434
- AETHEROPS_OLLAMA_MODEL  default llama3.2:3b (a ~2GB free local model;
                          1b works but follows the citation/classification
                          instructions less reliably)

The Ollama client is stdlib-only (urllib). Ollama itself is a free, local,
optional install — no account, no API key, no per-call charges.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


class BackendUnavailable(Exception):
    """The backend cannot serve calls (not installed, not running, died)."""


@dataclass(frozen=True)
class BackendResult:
    text: str
    tokens_in: int
    tokens_out: int
    served_model: str      # what actually produced the text, honestly


class OllamaBackend:
    """Local free model server. First failure marks the backend dead for the
    process lifetime so a missing Ollama costs one connection attempt, not
    one per call."""

    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: float = 120.0):
        self.base_url = (base_url or os.environ.get(
            "AETHEROPS_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.environ.get(
            "AETHEROPS_OLLAMA_MODEL", "llama3.2:3b")
        self.timeout = timeout
        self._dead = False

    def complete(self, model_id: str, prompt: str, task: str) -> BackendResult:
        if self._dead:
            raise BackendUnavailable(f"{self.name}: previously unreachable")
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            # Bound output length so one call's tokens (hence latency and
            # modeled cost) can't run away (audit H6). Diagnoses and plans are
            # short; a runaway generation is never useful here.
            "options": {"num_predict": int(os.environ.get(
                "AETHEROPS_OLLAMA_MAX_TOKENS", "1024"))},
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._dead = True
            raise BackendUnavailable(f"{self.name}: {exc}") from exc
        return BackendResult(
            text=str(data.get("response", "")).strip(),
            tokens_in=int(data.get("prompt_eval_count", 0) or 0),
            tokens_out=int(data.get("eval_count", 0) or 0),
            served_model=f"ollama/{self.model}")


class OfflineHeuristicBackend:
    """Deterministic, evidence-driven stand-in — the guaranteed last link of
    every chain. Contains no incident-specific facts: everything in its
    output is extracted from the prompt (commit SHAs from citation refs,
    [En] indices from the bundle, symptom markers from excerpts)."""

    name = "offline"

    def complete(self, model_id: str, prompt: str, task: str) -> BackendResult:
        from aetherops.gateway.offline import respond
        text = respond(prompt, task)
        return BackendResult(
            text=text,
            tokens_in=max(1, len(prompt) // 4),
            tokens_out=max(1, len(text) // 4),
            served_model="offline-heuristic")


def build_backend_chain(spec: str | None = None) -> list:
    """Build an ordered backend chain from a spec like "ollama,offline".
    With no spec, reads AETHEROPS_BACKENDS; with neither, returns the
    deterministic offline chain."""
    spec = spec or os.environ.get("AETHEROPS_BACKENDS", "offline")
    chain = []
    for raw in spec.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name == "ollama":
            chain.append(OllamaBackend())
        elif name == "offline":
            chain.append(OfflineHeuristicBackend())
        else:
            raise ValueError(f"unknown model backend {name!r} "
                             "(known: ollama, offline)")
    if not chain:
        chain.append(OfflineHeuristicBackend())
    return chain
