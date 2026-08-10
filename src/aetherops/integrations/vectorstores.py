"""Real vector-store backends for the RAG retriever (optional extra).

The default RagStore keeps vectors in memory (stdlib, deterministic, CI-safe).
These back the same document-level retrieval with real vector databases:

  - ChromaVectorStore   in-process ChromaDB (cosine HNSW), bring-your-own
                        embeddings so it needs no model download or network.
  - PgVectorStore       Postgres + the pgvector `<=>` cosine operator (opt-in
                        via DATABASE_URL; requires psycopg + the vector ext).

Both expose `search_docs(query, k) -> [doc_id]`, a drop-in for the retrieval
eval. Embeddings are the project's own TF-IDF, densified over a fixed vocab —
so Chroma runs fully self-contained ($0, no network). Opt-in and additive:
`pip install "aetherops[vectorstores]"`; the core never imports this.
"""
from __future__ import annotations

import os

from aetherops.rag.chunking import get_chunker
from aetherops.rag.corpus import SEED_RUNBOOKS
from aetherops.rag.embeddings import TfidfEmbedder


class _DenseTfidf:
    """Dense TF-IDF vectors over a fixed vocabulary — self-contained (stdlib),
    so a vector store needs no embedding-model download or network."""

    def __init__(self, texts: list[str]):
        self._embedder = TfidfEmbedder()
        self._embedder.fit(texts)
        self._vocab = sorted(self._embedder._idf)          # fixed order
        self._index = {term: i for i, term in enumerate(self._vocab)}

    @property
    def dim(self) -> int:
        return len(self._vocab)

    def embed(self, text: str) -> list[float]:
        sparse = self._embedder.embed(text)                # {term: weight}
        vector = [0.0] * len(self._vocab)
        for term, weight in sparse.items():
            i = self._index.get(term)
            if i is not None:
                vector[i] = weight
        return vector


def _corpus_chunks(chunker: str):
    chunk = get_chunker(chunker)
    chunks = []
    for document in SEED_RUNBOOKS:
        chunks.extend(chunk(document))
    return chunks


def _dedup_docs(doc_ids, k: int) -> list[str]:
    docs, seen = [], set()
    for doc_id in doc_ids:
        if doc_id not in seen:
            seen.add(doc_id)
            docs.append(doc_id)
        if len(docs) >= k:
            break
    return docs


class ChromaVectorStore:
    """RAG retrieval over an in-process ChromaDB collection (cosine)."""

    def __init__(self, chunker: str = "paragraph"):
        import chromadb

        self._chunks = _corpus_chunks(chunker)
        self._dense = _DenseTfidf([c.text for c in self._chunks])
        self._collection = chromadb.EphemeralClient().create_collection(
            "aetherops-runbooks", metadata={"hnsw:space": "cosine"})
        self._collection.add(
            ids=[str(i) for i in range(len(self._chunks))],
            embeddings=[self._dense.embed(c.text) for c in self._chunks],
            metadatas=[{"doc_id": c.doc_id, "ref": c.ref}
                       for c in self._chunks])

    def search_docs(self, query: str, k: int = 5) -> list[str]:
        result = self._collection.query(
            query_embeddings=[self._dense.embed(query)], n_results=k * 2)
        return _dedup_docs(
            (md["doc_id"] for md in result["metadatas"][0]), k)


class PgVectorStore:
    """RAG retrieval over Postgres + pgvector (`<=>` cosine distance).

    Opt-in via DATABASE_URL; requires `psycopg` and a Postgres with the vector
    extension (`CREATE EXTENSION vector`). Real SQL — not a mock — so it drops
    into a production pgvector deployment unchanged."""

    def __init__(self, dsn: str | None = None, chunker: str = "paragraph"):
        import psycopg                                      # guarded import

        self._dsn = dsn or os.environ["DATABASE_URL"]
        self._chunks = _corpus_chunks(chunker)
        self._dense = _DenseTfidf([c.text for c in self._chunks])
        dim = self._dense.dim
        with psycopg.connect(self._dsn) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute("DROP TABLE IF EXISTS aetherops_chunks")
            conn.execute(
                "CREATE TABLE aetherops_chunks (id int PRIMARY KEY, "
                f"doc_id text, ref text, embedding vector({dim}))")
            for i, chunk in enumerate(self._chunks):
                conn.execute(
                    "INSERT INTO aetherops_chunks VALUES (%s, %s, %s, %s)",
                    (i, chunk.doc_id, chunk.ref, self._dense.embed(chunk.text)))
            conn.commit()

    def search_docs(self, query: str, k: int = 5) -> list[str]:
        import psycopg

        embedding = self._dense.embed(query)
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT doc_id FROM aetherops_chunks "
                "ORDER BY embedding <=> %s::vector LIMIT %s",
                (str(embedding), k * 2)).fetchall()
        return _dedup_docs((row[0] for row in rows), k)
