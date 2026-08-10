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
    def __init__(self, max_episodes: int | None = None):
        self._episodes: list[dict] = []
        # Bound growth for long-lived, shared instances (audit F9); None keeps
        # the unbounded default the eval/replay paths rely on.
        self._max = max_episodes

    def add(self, episode: dict) -> str:
        episode = dict(episode)
        episode.setdefault("id", new_id("ep"))
        self._episodes.append(episode)
        if self._max is not None and len(self._episodes) > self._max:
            del self._episodes[:len(self._episodes) - self._max]
        return episode["id"]

    def search(self, query: str, k: int = 3) -> list[dict]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        scored = []
        for index, episode in enumerate(self._episodes):
            text = " ".join(
                str(episode.get(field, ""))
                for field in ("summary", "failure_class", "service"))
            overlap = len(query_tokens & _tokens(text))
            if overlap:
                scored.append((overlap, episode, index))
        # Tie-break deliberately (audit F11): equal overlap -> prefer a
        # VERIFIED episode, then the more RECENT one (higher insertion index).
        # The old default was insertion order, which favored stale precedent.
        scored.sort(key=lambda t: (t[0], 1 if t[1].get("verified") else 0,
                                   t[2]), reverse=True)
        return [episode for _, episode, _ in scored[:k]]

    def __len__(self) -> int:
        return len(self._episodes)

    def close(self) -> None:        # no-op; the SQLite subclass releases its conn
        pass
