"""Retrieval-quality evaluation (docs/17 acceptance #5–#9).

Runs the labeled query set against a store built per chunking strategy and
reports precision@1, precision@5, recall@5, and MRR — document-level, since
surfacing the right document is what the strategies compete on. The release
gate fails when the best strategy's precision@1 drops below the floor.

Always TF-IDF: like golden-scenario replay, retrieval evaluation must be
deterministic and runnable in CI with no network.
"""
from __future__ import annotations

from aetherops.rag.evalset import LABELED_QUERIES
from aetherops.rag.retriever import RagStore

RETRIEVAL_PRECISION_GATE = 0.6          # precision@1 floor, best strategy
STRATEGIES = ("paragraph", "fixed")


def evaluate_strategy(chunker: str) -> dict:
    store = RagStore(chunker=chunker, embedder="tfidf")
    p1 = p5 = r5 = mrr = 0.0
    for query, relevant in LABELED_QUERIES:
        ranked = store.search_docs(query, k=5)
        relevant_set = set(relevant)
        hits = [doc for doc in ranked if doc in relevant_set]
        p1 += 1.0 if ranked and ranked[0] in relevant_set else 0.0
        p5 += len(hits) / 5.0
        r5 += len(hits) / len(relevant_set)
        for rank, doc in enumerate(ranked, start=1):
            if doc in relevant_set:
                mrr += 1.0 / rank
                break
    n = len(LABELED_QUERIES)
    return {
        "chunker": chunker,
        "chunks": len(store),
        "queries": n,
        "precision_at_1": round(p1 / n, 3),
        "precision_at_5": round(p5 / n, 3),
        "recall_at_5": round(r5 / n, 3),
        "mrr": round(mrr / n, 3),
    }


def run_retrieval_eval() -> dict:
    strategies = {name: evaluate_strategy(name) for name in STRATEGIES}
    best = max(strategies,
               key=lambda s: (strategies[s]["precision_at_1"],
                              strategies[s]["mrr"]))
    passed = (strategies[best]["precision_at_1"]
              >= RETRIEVAL_PRECISION_GATE)
    return {
        "strategies": strategies,
        "best": best,
        "gate": {"criterion": ("best-strategy precision_at_1 >= "
                               f"{RETRIEVAL_PRECISION_GATE}"),
                 "passed": passed},
    }
