"""Hybrid retriever with source attribution (docs/06 §3, docs/17 M7).

Scoring blends dense/sparse cosine similarity with lexical keyword overlap
(`alpha * cosine + (1 - alpha) * keyword`) — hybrid because vector-only
misses exact identifiers (commit SHAs, error strings) and keyword-only
misses paraphrase; the retrieval evaluation quantifies the blend.

Configuration (environment, overridable per instance):
- AETHEROPS_RAG_CHUNKER   fixed | paragraph   (default paragraph)
- AETHEROPS_RAG_EMBEDDER  tfidf | ollama      (default tfidf)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from aetherops.rag.chunking import Chunk, get_chunker
from aetherops.rag.corpus import SEED_RUNBOOKS, Document
from aetherops.rag.embeddings import dot, get_embedder, tokenize


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    cosine: float
    keyword: float

    @property
    def ref(self) -> str:
        return self.chunk.ref


class RagStore:
    def __init__(self, documents: list[Document] | None = None,
                 chunker: str | None = None, embedder: str | None = None,
                 alpha: float = 0.7):
        self.chunker_name = chunker or os.environ.get(
            "AETHEROPS_RAG_CHUNKER", "paragraph")
        self.embedder_name = embedder or os.environ.get(
            "AETHEROPS_RAG_EMBEDDER", "tfidf")
        self._chunk = get_chunker(self.chunker_name)
        self._embedder = get_embedder(self.embedder_name)
        self.alpha = alpha
        self._chunks: list[Chunk] = []
        self._vectors: list[dict] = []

        docs = SEED_RUNBOOKS if documents is None else documents
        all_chunks = [c for doc in docs for c in self._chunk(doc)]
        self._embedder.fit([c.text for c in all_chunks])
        for chunk in all_chunks:
            self._chunks.append(chunk)
            self._vectors.append(self._embedder.embed(chunk.text))

    def add_document(self, doc: Document) -> int:
        """Runtime ingestion (e.g. a generated postmortem). TF-IDF keeps its
        fitted vocabulary — new docs embed against it, the standard
        incremental-index trade-off until the next refit."""
        added = self._chunk(doc)
        for chunk in added:
            self._chunks.append(chunk)
            self._vectors.append(self._embedder.embed(chunk.text))
        return len(added)

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        query_vec = self._embedder.embed(query)
        query_tokens = set(tokenize(query))
        scored: list[RetrievedChunk] = []
        for chunk, vector in zip(self._chunks, self._vectors):
            cosine = dot(query_vec, vector)
            if query_tokens:
                overlap = len(query_tokens & set(tokenize(chunk.text)))
                keyword = overlap / len(query_tokens)
            else:
                keyword = 0.0
            score = self.alpha * cosine + (1 - self.alpha) * keyword
            if score > 0:
                scored.append(RetrievedChunk(chunk, round(score, 6),
                                             round(cosine, 6),
                                             round(keyword, 6)))
        scored.sort(key=lambda r: (-r.score, r.chunk.doc_id, r.chunk.index))
        return scored[:k]

    def search_docs(self, query: str, k: int = 5) -> list[str]:
        """Ranked distinct document IDs — the unit the labeled eval scores."""
        seen: list[str] = []
        for result in self.search(query, k=max(k * 3, k)):
            if result.chunk.doc_id not in seen:
                seen.append(result.chunk.doc_id)
            if len(seen) == k:
                break
        return seen

    def __len__(self) -> int:
        return len(self._chunks)
