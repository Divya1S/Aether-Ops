"""LLM-as-judge with a deterministic anchor (docs/10 §5).

Deterministic metrics (precision@1, calibration, plan accuracy) verify
*facts*; they cannot grade the *quality of generated prose* — is the
root-cause hypothesis well-reasoned, grounded, and clear? A model judge
(frontier tier) scores that. But subjective scores are never trusted blind:
a deterministic citation check computes ground truth — which [En] references
actually exist in the evidence bundle — and OVERRIDES the judge on
faithfulness, flagging any disagreement. The deterministic anchor is what
gates CI; the judge's quality scores are reported.

Offline the judge is a deterministic policy (CI-safe, reproducible); live
(Ollama) it is a real model. A judge that self-certifies is worthless
(docs/10: judges are re-validated, never trusted alone) — here the anchor is
that re-validation, running on every judgement.
"""
from __future__ import annotations

import re

from aetherops.agents.investigation import extract_json
from aetherops.core.schema import validate
from aetherops.gateway.model_gateway import TaskProfile
from aetherops.prompts.registry import get_prompt

_REF = re.compile(r"\[E(\d+)\]")

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["causal_correctness", "grounding", "clarity",
                 "citation_faithfulness", "hallucinated_refs"],
    "properties": {
        "causal_correctness": {"type": "integer"},
        "grounding": {"type": "integer"},
        "clarity": {"type": "integer"},
        "citation_faithfulness": {"type": "integer"},
        "hallucinated_refs": {"type": "array", "items": {"type": "string"}},
    }}


def citation_ground_truth(artifact: str, evidence_count: int) -> list[str]:
    """The deterministic anchor: every [En] the artifact cites must exist in
    the bundle (1 <= n <= evidence_count). Returns the hallucinated refs."""
    refs = sorted({int(n) for n in _REF.findall(artifact)})
    return [f"E{n}" for n in refs if n < 1 or n > evidence_count]


def judge_hypothesis(gateway, artifact: str, evidence_count: int,
                     digest: str) -> dict:
    truth_hallucinated = citation_ground_truth(artifact, evidence_count)
    template = get_prompt("judge")
    response = gateway.complete(
        template.render(artifact=artifact[:1500],
                        evidence_items=evidence_count, digest=digest[:1500]),
        TaskProfile(task="judge", tier_hint="frontier",
                    prompt_id=template.id, prompt_version=template.version))

    parsed = extract_json(response.text)
    if parsed is None or validate(parsed, JUDGE_SCHEMA):
        # Invalid verdict: fall back to a conservative deterministic score
        # rather than trusting an unparseable judgement.
        parsed = {"causal_correctness": 3, "grounding": 3, "clarity": 3,
                  "citation_faithfulness": 1 if truth_hallucinated else 5,
                  "hallucinated_refs": truth_hallucinated}

    # Ground truth overrides the judge on faithfulness; record disagreement.
    judge_claimed_faithful = (parsed["citation_faithfulness"] >= 4
                              and not parsed.get("hallucinated_refs"))
    actually_faithful = not truth_hallucinated
    scores = {k: max(1, min(5, int(parsed[k])))
              for k in ("causal_correctness", "grounding", "clarity")}
    scores["citation_faithfulness"] = 5 if actually_faithful else 1
    return {
        "scores": scores,
        "overall": round(sum(scores.values()) / (len(scores) * 5), 3),
        "hallucinated_refs": truth_hallucinated,      # ground truth wins
        "faithful": actually_faithful,
        "judge_disagreed": judge_claimed_faithful != actually_faithful,
        "tokens": response.tokens,
        "backend": response.backend,
    }


def aggregate(judgements: list[dict]) -> dict:
    if not judgements:
        return {"judged": 0, "mean_overall": None,
                "total_hallucinated_refs": 0, "judge_disagreements": 0,
                "faithful_all": True}
    total_hallucinated = sum(len(j["hallucinated_refs"]) for j in judgements)
    return {
        "judged": len(judgements),
        "mean_overall": round(
            sum(j["overall"] for j in judgements) / len(judgements), 3),
        "mean_causal_correctness": round(
            sum(j["scores"]["causal_correctness"] for j in judgements)
            / len(judgements), 2),
        "mean_grounding": round(
            sum(j["scores"]["grounding"] for j in judgements)
            / len(judgements), 2),
        "mean_clarity": round(
            sum(j["scores"]["clarity"] for j in judgements)
            / len(judgements), 2),
        "total_hallucinated_refs": total_hallucinated,
        "judge_disagreements": sum(j["judge_disagreed"] for j in judgements),
        "faithful_all": total_hallucinated == 0,
    }
