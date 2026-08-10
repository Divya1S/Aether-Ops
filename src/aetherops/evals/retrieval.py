"""Retrieval-quality evaluation (docs/17 acceptance #5–#9).

Runs the labeled query set against a store built per chunking strategy and
reports precision@1, precision@5, recall@5, and MRR — document-level, since
surfacing the right document is what the strategies compete on. The release
gate fails when the best strategy's precision@1 drops below the floor.

Always TF-IDF: like golden-scenario replay, retrieval evaluation must be
deterministic and runnable in CI with no network.
"""
from __future__ import annotations

import random

from aetherops.rag.evalset import LABELED_QUERIES
from aetherops.rag.retriever import RagStore

RETRIEVAL_PRECISION_GATE = 0.6          # precision@1 floor, best strategy
STRATEGIES = ("paragraph", "fixed")
_BOOTSTRAP_ITERS = 1000
_BOOTSTRAP_SEED = 42                     # seeded => the CI is reproducible in CI


def _bootstrap_ci(indicators: list[int]) -> tuple[float, float]:
    """95% bootstrap CI for a proportion — so the gate reads a LOWER BOUND, not
    a point estimate that n=~36 can't support (audit F6). Seeded for
    determinism; with no data the interval is [0, 0]."""
    n = len(indicators)
    if n == 0:
        return 0.0, 0.0
    rng = random.Random(_BOOTSTRAP_SEED)
    means = sorted(
        sum(indicators[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(_BOOTSTRAP_ITERS))
    lo = means[int(0.025 * _BOOTSTRAP_ITERS)]
    hi = means[int(0.975 * _BOOTSTRAP_ITERS)]
    return round(lo, 3), round(hi, 3)


def evaluate_strategy(chunker: str) -> dict:
    store = RagStore(chunker=chunker, embedder="tfidf")
    p1_hits: list[int] = []             # per-query precision@1 indicator
    p5 = r5 = mrr = 0.0
    for query, relevant in LABELED_QUERIES:
        ranked = store.search_docs(query, k=5)
        relevant_set = set(relevant)
        hits = [doc for doc in ranked if doc in relevant_set]
        p1_hits.append(1 if ranked and ranked[0] in relevant_set else 0)
        p5 += len(hits) / 5.0
        r5 += len(hits) / len(relevant_set)
        for rank, doc in enumerate(ranked, start=1):
            if doc in relevant_set:
                mrr += 1.0 / rank
                break
    n = len(LABELED_QUERIES)
    ci_low, ci_high = _bootstrap_ci(p1_hits)
    return {
        "chunker": chunker,
        "chunks": len(store),
        "queries": n,
        "precision_at_1": round(sum(p1_hits) / n, 3),
        "precision_at_1_ci95": [ci_low, ci_high],
        "precision_at_5": round(p5 / n, 3),
        "recall_at_5": round(r5 / n, 3),
        "mrr": round(mrr / n, 3),
    }


def run_retrieval_eval() -> dict:
    strategies = {name: evaluate_strategy(name) for name in STRATEGIES}
    best = max(strategies,
               key=lambda s: (strategies[s]["precision_at_1"],
                              strategies[s]["mrr"]))
    # Gate on the CI LOWER BOUND, not the point estimate: a regression has to
    # move the whole interval, so the gate can't be satisfied by noise on a
    # small sample (audit F6).
    ci_low = strategies[best]["precision_at_1_ci95"][0]
    passed = ci_low >= RETRIEVAL_PRECISION_GATE
    return {
        "strategies": strategies,
        "best": best,
        "gate": {"criterion": ("best-strategy precision_at_1 95% CI lower "
                               f"bound >= {RETRIEVAL_PRECISION_GATE}"),
                 "ci_lower_bound": ci_low,
                 "passed": passed},
    }
