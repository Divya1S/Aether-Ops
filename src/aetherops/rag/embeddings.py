"""Embedding providers, swappable by configuration (docs/17 acceptance #7).

- TfidfEmbedder (default): pure-stdlib TF-IDF vectors — deterministic, zero
  dependencies, zero network, so retrieval evaluation runs in CI anywhere.
- OllamaEmbedder: real dense embeddings from a free local model via
  Ollama's /api/embed (opt-in; never used by tests or evals).

Both produce sparse mappings key->weight, L2-normalized, compared by cosine
via `dot`. Production swaps in pgvector/Qdrant per docs/06 — the interface,
not the store, is the design surface here.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have if in into is it its of on "
    "or so that the their then this to was were when which will with".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def _normalize(vector: dict) -> dict:
    norm = math.sqrt(sum(w * w for w in vector.values()))
    if norm == 0:
        return {}
    return {k: w / norm for k, w in vector.items()}


def dot(a: dict, b: dict) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(w * b.get(k, 0.0) for k, w in a.items())


class TfidfEmbedder:
    name = "tfidf"

    def __init__(self):
        self._idf: dict[str, float] = {}

    def fit(self, texts: list[str]) -> None:
        doc_freq: Counter = Counter()
        for text in texts:
            doc_freq.update(set(tokenize(text)))
        n = max(1, len(texts))
        self._idf = {term: math.log((1 + n) / (1 + df)) + 1.0
                     for term, df in doc_freq.items()}

    def embed(self, text: str) -> dict:
        counts = Counter(tokenize(text))
        total = sum(counts.values()) or 1
        # Unseen terms get a neutral IDF so queries with novel words still
        # embed rather than vanishing.
        vector = {term: (count / total) * self._idf.get(term, 1.0)
                  for term, count in counts.items()}
        return _normalize(vector)


class OllamaEmbedder:
    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: float = 60.0):
        self.base_url = (base_url or os.environ.get(
            "AETHEROPS_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.environ.get(
            "AETHEROPS_EMBED_MODEL", "nomic-embed-text")
        self.timeout = timeout

    def fit(self, texts: list[str]) -> None:   # dense models need no fitting
        return None

    def embed(self, text: str) -> dict:
        payload = json.dumps({"model": self.model, "input": text}).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/embed", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vector = data["embeddings"][0]
        return _normalize(dict(enumerate(vector)))


EMBEDDERS = {
    "tfidf": TfidfEmbedder,
    "ollama": OllamaEmbedder,
}


def get_embedder(name: str):
    if name not in EMBEDDERS:
        raise ValueError(f"unknown embedder {name!r} "
                         f"(known: {sorted(EMBEDDERS)})")
    return EMBEDDERS[name]()
