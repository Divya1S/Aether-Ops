"""Episodic memory: structured incident episodes, recalled by similarity
(docs/06-retrieval-and-memory.md §7).

Production: Postgres rows + pgvector embeddings (Qdrant at scale). The
reference implementation uses keyword-overlap scoring — the interface, not
the ranking algorithm, is the design surface here.
"""
from __future__ import annotations

import re

from aetherops.core.types import new_id

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


class EpisodicMemory:
    def __init__(self):
        self._episodes: list[dict] = []

    def add(self, episode: dict) -> str:
        episode = dict(episode)
        episode.setdefault("id", new_id("ep"))
        self._episodes.append(episode)
        return episode["id"]

    def search(self, query: str, k: int = 3) -> list[dict]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        scored = []
        for episode in self._episodes:
            text = " ".join(
                str(episode.get(field, ""))
                for field in ("summary", "failure_class", "service"))
            overlap = len(query_tokens & _tokens(text))
            if overlap:
                scored.append((overlap, episode))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [episode for _, episode in scored[:k]]

    def __len__(self) -> int:
        return len(self._episodes)
