"""LLM-as-judge, live: a REAL local model (Ollama) grades a hypothesis, and
the deterministic citation anchor overrides it where it errs (docs/10 §5).

    make judge-live      # or: python3 -m aetherops.evals.judge_live

The anchor is the whole point: a subjective model judge is never trusted
blind. A deterministic ground-truth check computes which [En] references
actually exist and overrides the model on faithfulness, flagging any
disagreement. This shows it against a live model — which, being a small
local model, often MISSES fabricated citations that the anchor catches. Runs
with graceful fallback (ollama -> offline), so it works with or without
Ollama installed.
"""
from __future__ import annotations

from aetherops.evals.judge import judge_hypothesis
from aetherops.gateway.backends import build_backend_chain
from aetherops.gateway.model_gateway import ModelGateway

RULE = "─" * 72
DIGEST = "\n".join(f"[E{i}] (kind) evidence line {i}" for i in range(1, 6))
FAITHFUL = ("Causal chain: deploy [E3] -> pool change [E4] -> memory "
            "exhaustion [E5] -> latency breach [E2]. Suspect commit c9a1f42.")
HALLUCINATED = FAITHFUL + " Further corroborated by [E42] and [E99]."


def _judge(gateway, label: str, hypothesis: str) -> dict:
    verdict = judge_hypothesis(gateway, hypothesis, evidence_count=5,
                               digest=DIGEST)
    print(f"\n{label}")
    print(f"    judge backend            : {verdict['backend']}")
    print(f"    model quality scores      : "
          f"causal={verdict['scores']['causal_correctness']} "
          f"grounding={verdict['scores']['grounding']} "
          f"clarity={verdict['scores']['clarity']}")
    print(f"    anchor: hallucinated refs : {verdict['hallucinated_refs']}")
    print(f"    anchor: citation_faithful : "
          f"{verdict['scores']['citation_faithfulness']} "
          f"(faithful={verdict['faithful']})")
    print(f"    model disagreed w/ anchor : {verdict['judge_disagreed']}")
    return verdict


def main() -> int:
    print(f"{RULE}\nLLM-as-judge, live — local model quality scores, "
          f"deterministic anchor authoritative\n{RULE}")
    gateway = ModelGateway(backends=build_backend_chain("ollama,offline"))
    _judge(gateway, "FAITHFUL hypothesis (cites E2..E5, all within the bundle):",
           FAITHFUL)
    bad = _judge(gateway,
                 "HALLUCINATED hypothesis (cites E42 and E99 — out of a "
                 "5-item bundle):", HALLUCINATED)
    print(f"\n{RULE}")
    print("The subjective scores are the model's; the citation verdict is the "
          "anchor's.\nA small local model often misses fabricated citations — "
          "here the anchor\ncaught " + str(bad["hallucinated_refs"]) +
          " and floored faithfulness to 1. That is why the\njudge is reported, "
          "never gated, and never trusted blind.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
