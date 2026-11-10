"""The hypothesis tree (docs/20 §2): a differential diagnosis that must agree
with the pipeline on every golden scenario, explain each ruling with the
evidence that decided it, and never mutate the record it reads."""
import dataclasses
import unittest

from aetherops.core.context import WorkflowContext
from aetherops.core.types import AgentResult
from aetherops.evals.scenarios import (all_scenarios, build_environment,
                                       canonical, deploy_after_breach,
                                       heap_oom, reverted_pool,
                                       uncorrelated_latency)
from aetherops.reasoning.hypotheses import (INCONCLUSIVE, INVALIDATED,
                                            VALIDATED, build_hypothesis_tree)
from aetherops.reasoning.signals import (diff_raises_resource,
                                         has_pool_signature, numeric_delta)
from aetherops.workflows.incident_remediation import run_incident_remediation


def _run(scenario):
    incident, env = build_environment(scenario, backends_spec="offline")
    run, ctx = run_incident_remediation(incident, **env)
    return run, ctx, env


def _hypothesis(tree, hid):
    return next(h for h in tree["hypotheses"] if h["id"] == hid)


def _criterion(tree, hid, cid):
    return next(c for c in _hypothesis(tree, hid)["criteria"]
                if c["id"] == cid)


class TestTreeAgreesWithGroundTruth(unittest.TestCase):
    """The release-gate property: on every golden scenario the tree validates
    exactly the adjudicated failure class — and validates NOTHING where the
    right answer is to escalate. A tree that could explain a remediation the
    pipeline refused would be decoration, not evidence."""

    def test_validated_class_matches_truth_on_every_scenario(self):
        for scenario in all_scenarios():
            with self.subTest(scenario=scenario.id):
                run, ctx, _ = _run(scenario)
                tree = build_hypothesis_tree(ctx, run)
                self.assertEqual(tree["validated"],
                                 scenario.truth.failure_class)
                self.assertEqual(
                    tree["conclusion"],
                    "validated" if scenario.truth.failure_class
                    else "inconclusive")

    def test_tree_agrees_with_the_pipeline_on_every_scenario(self):
        for scenario in all_scenarios():
            with self.subTest(scenario=scenario.id):
                run, ctx, _ = _run(scenario)
                self.assertTrue(
                    build_hypothesis_tree(ctx, run)["pipeline"]["agrees"])

    def test_at_most_one_hypothesis_is_ever_validated(self):
        for scenario in all_scenarios():
            run, ctx, _ = _run(scenario)
            statuses = [h["status"] for h in
                        build_hypothesis_tree(ctx, run)["hypotheses"]]
            self.assertLessEqual(statuses.count(VALIDATED), 1)


class TestRulings(unittest.TestCase):
    def test_canonical_validates_on_all_four_criteria(self):
        run, ctx, _ = _run(canonical())
        h1 = _hypothesis(build_hypothesis_tree(ctx, run), "H1")
        self.assertEqual(h1["status"], VALIDATED)
        self.assertEqual((h1["supported"], h1["required"]), (4, 4))
        self.assertEqual(h1["ruled_out_by"], [])

    def test_pool_reduction_is_ruled_out_by_mechanism(self):
        # Adversarial s5: the deploy correlates, precedes onset, and pods
        # were OOMKilled — only the direction of the change exonerates it.
        run, ctx, _ = _run(reverted_pool())
        tree = build_hypothesis_tree(ctx, run)
        h1 = _hypothesis(tree, "H1")
        self.assertEqual(h1["status"], INVALIDATED)
        self.assertEqual(h1["ruled_out_by"], ["mechanism-consistency"])
        self.assertIn("lowers the limit 200 -> 20",
                      _criterion(tree, "H1", "mechanism-consistency")["note"])

    def test_late_deploy_is_ruled_out_by_temporal_precedence(self):
        # Adversarial s6: a textbook pool raise that landed AFTER onset.
        run, ctx, _ = _run(deploy_after_breach())
        h1 = _hypothesis(build_hypothesis_tree(ctx, run), "H1")
        self.assertEqual(h1["status"], INVALIDATED)
        self.assertEqual(h1["ruled_out_by"], ["temporal-precedence"])

    def test_unrecognized_signature_stays_open_and_asks_for_evidence(self):
        # Adversarial s7: a real memory regression the deterministic
        # diagnoser cannot ground. Honest answer: inconclusive, plus what to
        # gather — never a guess.
        run, ctx, _ = _run(heap_oom())
        tree = build_hypothesis_tree(ctx, run)
        self.assertEqual(_hypothesis(tree, "H1")["status"], INCONCLUSIVE)
        self.assertIn("heap and memory profiles for the new revision",
                      tree["gather_next"])

    def test_uncorrelated_latency_names_the_missing_telemetry(self):
        run, ctx, _ = _run(uncorrelated_latency())
        tree = build_hypothesis_tree(ctx, run)
        self.assertEqual(_hypothesis(tree, "H3")["status"], INCONCLUSIVE)
        self.assertEqual(tree["gather_next"],
                         ["upstream latency, saturation and request-rate "
                          "metrics"])

    def test_ungroundable_hypothesis_can_never_validate(self):
        # H3 has no Step Catalog remediation; even with every other
        # criterion supported it may only stay open.
        for scenario in all_scenarios():
            run, ctx, _ = _run(scenario)
            self.assertNotEqual(
                _hypothesis(build_hypothesis_tree(ctx, run), "H3")["status"],
                VALIDATED)

    def test_advisory_runbook_criterion_never_decides(self):
        run, ctx, _ = _run(canonical())
        criterion = _criterion(build_hypothesis_tree(ctx, run), "H2",
                               "runbook-corroboration")
        self.assertFalse(criterion["required"])


class TestEvidenceLinkage(unittest.TestCase):
    def test_every_cited_ref_exists_and_has_a_matching_kind(self):
        expected_kind = {
            "change-correlated": {"deploy"},
            "temporal-precedence": {"deploy", "metrics"},
            "crash-signature": {"k8s-event"},
            "mechanism-consistency": {"commit"},
        }
        for scenario in all_scenarios():
            run, ctx, _ = _run(scenario)
            tree = build_hypothesis_tree(ctx, run)
            for criterion in _hypothesis(tree, "H1")["criteria"]:
                for ref in criterion["evidence"]:
                    index = int(ref[1:])
                    self.assertTrue(1 <= index <= len(ctx.evidence))
                    self.assertIn(ctx.evidence[index - 1].kind,
                                  expected_kind[criterion["id"]])

    def test_signals_expose_onset_and_the_shipped_change(self):
        run, ctx, _ = _run(canonical())
        signals = build_hypothesis_tree(ctx, run)["signals"]
        self.assertEqual(signals["onset"], "14:05Z")
        self.assertEqual(signals["deploy"]["revision"], "v2025.08.07-3")
        self.assertEqual(signals["commit"]["sha"], "c9a1f42")


class TestTreeIsAReadOnlyProjection(unittest.TestCase):
    def test_building_the_tree_touches_nothing(self):
        run, ctx, env = _run(canonical())
        records, evidence = len(env["audit"]), list(ctx.evidence)
        tokens = env["gateway"].tokens_used
        first = build_hypothesis_tree(ctx, run)
        second = build_hypothesis_tree(ctx, run)
        self.assertEqual(len(env["audit"]), records)     # no ledger appends
        self.assertEqual(ctx.evidence, evidence)         # no bundle mutation
        self.assertEqual(env["gateway"].tokens_used, tokens)  # no model call
        self.assertEqual(first, second)                  # deterministic
        self.assertTrue(env["audit"].verify())


class TestGroundingDiscipline(unittest.TestCase):
    def test_quarantined_evidence_proves_nothing(self):
        # A commit flagged as a prompt-injection carrier is withheld from
        # the model; the tree must not lean on it either.
        run, ctx, _ = _run(canonical())
        index = next(i for i, e in enumerate(ctx.evidence)
                     if e.kind == "commit")
        ctx.evidence[index] = dataclasses.replace(
            ctx.evidence[index], classification="QUARANTINED")
        tree = build_hypothesis_tree(ctx, run)
        self.assertIsNone(tree["validated"])
        self.assertEqual(
            _criterion(tree, "H1", "mechanism-consistency")["status"],
            "unknown")

    def test_missing_evidence_is_unknown_never_refuted(self):
        ctx = WorkflowContext(incident=None, connectors=None, gateway=None,
                              audit=None, memory=None)
        tree = build_hypothesis_tree(ctx)
        for hypothesis in tree["hypotheses"]:
            self.assertEqual(hypothesis["status"], INCONCLUSIVE)
            self.assertEqual(hypothesis["ruled_out_by"], [])
        self.assertIsNone(tree["validated"])

    def test_disagreement_with_the_pipeline_is_reported(self):
        # A (live, nondeterministic) model that diagnoses a deploy
        # regression the evidence contradicts must not look confirmed.
        run, ctx, _ = _run(reverted_pool())
        ctx.results["root_cause"] = AgentResult(
            agent="root_cause",
            output={"status": "diagnosed", "hypothesis": "the deploy [E3]",
                    "failure_class": "deploy-regression/memory",
                    "suspect_commit": "d4e55aa"},
            confidence=0.9, citations=[])
        tree = build_hypothesis_tree(ctx, run=None)
        self.assertIsNone(tree["validated"])
        self.assertFalse(tree["pipeline"]["agrees"])


class TestSignals(unittest.TestCase):
    def test_numeric_delta_reads_both_sides_of_a_diff(self):
        self.assertEqual(numeric_delta("-  max_size: 20\n+  max_size: 200"),
                         (20, 200))
        self.assertIsNone(numeric_delta("-  -Xmx512m\n+  -Xmx4g"))
        self.assertIsNone(numeric_delta(""))

    def test_pool_signature(self):
        self.assertTrue(has_pool_signature("Raise DB connection pool"))
        self.assertTrue(has_pool_signature("+  max_size: 200"))
        self.assertFalse(has_pool_signature("Bump JVM heap -Xmx 512m -> 4g"))

    def test_reviewer_predicate_is_the_shared_one(self):
        from aetherops.agents import reviewer
        self.assertIs(reviewer._diff_raises_resource, diff_raises_resource)


if __name__ == "__main__":
    unittest.main()
