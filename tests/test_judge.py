"""LLM-as-judge with a deterministic anchor (M14): the judge grades prose
quality, but the deterministic citation check is ground truth and overrides
the judge — proven by a lying judge that gets caught."""
import json
import unittest

from aetherops.evals.judge import (aggregate, citation_ground_truth,
                                    judge_hypothesis)
from aetherops.evals.harness import run_all
from aetherops.gateway.backends import BackendResult
from aetherops.gateway.model_gateway import ModelGateway
from aetherops.gateway.offline import respond

FAITHFUL = ("Causal chain: deploy [E3] -> pool change [E4] -> memory "
            "exhaustion [E5] -> latency breach [E2], commit c9a1f42.")
HALLUCINATED = FAITHFUL + " Also see [E99] and [E42]."
DIGEST = "\n".join(f"[E{i}] (kind) evidence" for i in range(1, 6))


class _Backend:
    """Serves a fixed reply for the judge task; delegates otherwise."""
    name = "scripted"

    def __init__(self, judge_reply):
        self.judge_reply = judge_reply

    def complete(self, model_id, prompt, task):
        text = self.judge_reply if task == "judge" else respond(prompt, task)
        return BackendResult(text, 10, 5, "scripted")


def _gateway(judge_reply):
    return ModelGateway(backends=[_Backend(judge_reply)])


class TestAnchor(unittest.TestCase):
    def test_ground_truth_catches_out_of_range_refs(self):
        self.assertEqual(citation_ground_truth(HALLUCINATED, 5),
                         ["E42", "E99"])
        self.assertEqual(citation_ground_truth(FAITHFUL, 5), [])

    def test_faithful_hypothesis_scores_clean(self):
        v = judge_hypothesis(_gateway(respond(
            "Hypothesis:" + FAITHFUL, "judge")), FAITHFUL, 5, DIGEST)
        self.assertTrue(v["faithful"])
        self.assertEqual(v["hallucinated_refs"], [])
        self.assertEqual(v["scores"]["citation_faithfulness"], 5)
        self.assertGreater(v["overall"], 0.7)

    def test_hallucinated_refs_floor_faithfulness_regardless_of_judge(self):
        # A LYING judge: claims perfect faithfulness, empty hallucination list.
        lie = json.dumps({"causal_correctness": 5, "grounding": 5,
                          "clarity": 5, "citation_faithfulness": 5,
                          "hallucinated_refs": []})
        v = judge_hypothesis(_gateway(lie), HALLUCINATED, 5, DIGEST)
        # Ground truth overrides the judge:
        self.assertFalse(v["faithful"])
        self.assertEqual(v["hallucinated_refs"], ["E42", "E99"])
        self.assertEqual(v["scores"]["citation_faithfulness"], 1)
        self.assertTrue(v["judge_disagreed"])

    def test_invalid_judge_reply_falls_back_conservatively(self):
        v = judge_hypothesis(_gateway("not json at all"), FAITHFUL, 5, DIGEST)
        self.assertTrue(v["faithful"])          # anchor still computed
        self.assertIn("causal_correctness", v["scores"])

    def test_offline_judge_is_deterministic(self):
        g1 = judge_hypothesis(_gateway(respond("Hypothesis:" + FAITHFUL,
                                               "judge")), FAITHFUL, 5, DIGEST)
        g2 = judge_hypothesis(_gateway(respond("Hypothesis:" + FAITHFUL,
                                               "judge")), FAITHFUL, 5, DIGEST)
        self.assertEqual(g1["scores"], g2["scores"])


class TestAggregateAndGate(unittest.TestCase):
    def test_aggregate_flags_any_hallucination(self):
        clean = {"overall": 0.9, "hallucinated_refs": [],
                 "judge_disagreed": False,
                 "scores": {"causal_correctness": 5, "grounding": 5,
                            "clarity": 5, "citation_faithfulness": 5}}
        dirty = {**clean, "hallucinated_refs": ["E99"]}
        self.assertTrue(aggregate([clean, clean])["faithful_all"])
        agg = aggregate([clean, dirty])
        self.assertFalse(agg["faithful_all"])
        self.assertEqual(agg["total_hallucinated_refs"], 1)

    def test_empty_is_faithful(self):
        self.assertTrue(aggregate([])["faithful_all"])


class TestHarnessIntegration(unittest.TestCase):
    def test_judge_runs_and_gates_in_the_report(self):
        report = run_all()
        judge = report["aggregates"]["judge"]
        self.assertEqual(judge["judged"], 3)       # 3 diagnosed (s2 escalates)
        self.assertEqual(judge["total_hallucinated_refs"], 0)
        self.assertGreater(judge["mean_overall"], 0.8)
        self.assertTrue(report["judge_gate"]["passed"])
        # Every diagnosed row carries a judgement; the escalation does not.
        judged_rows = [r for r in report["rows"] if r["judge"]]
        self.assertEqual(len(judged_rows), 3)


if __name__ == "__main__":
    unittest.main()
