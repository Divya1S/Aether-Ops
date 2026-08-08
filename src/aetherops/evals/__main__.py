"""Run the golden-scenario evaluation:

    python3 -m aetherops.evals             # print report; exit 1 if the
                                           # release gate fails
    python3 -m aetherops.evals --json out.json
"""
from __future__ import annotations

import argparse
import json

from aetherops.evals.harness import run_all
from aetherops.evals.retrieval import run_retrieval_eval

RULE = "─" * 72


def main() -> int:
    parser = argparse.ArgumentParser(prog="aetherops.evals")
    parser.add_argument("--json", metavar="PATH",
                        help="also write the full report as JSON")
    args = parser.parse_args()

    report = run_all()

    print(f"{RULE}\nAetherOps evaluation — golden scenarios\n{RULE}")
    for row in report["rows"]:
        verdict = "PASS" if row["correct"] or (
            row["expected_class"] is None and row["outcome_correct"]) else "FAIL"
        print(f"\n[{verdict}] {row['scenario']} — {row['name']}")
        print(f"    outcome: {row['outcome']} (expected {row['expected_outcome']})")
        if row["expected_class"] is not None:
            print(f"    diagnosis: {row['predicted_class']} / "
                  f"{row['predicted_commit']} "
                  f"(expected {row['expected_class']} / {row['expected_commit']})")
            print(f"    plan: {row['steps']} (expected {row['expected_steps']})")
        print(f"    confidence={row['confidence']} "
              f"calibration_error={row['calibration_error']} "
              f"tool_calls={row['tool_calls']} tokens={row['tokens']} "
              f"audit_verified={row['audit_verified']}")

    print(f"\n{RULE}\nAggregates\n{RULE}")
    for key, value in report["aggregates"].items():
        print(f"    {key:<28} {value}")

    print(f"\n{RULE}\nTrust ladder (per failure class)\n{RULE}")
    for failure_class, verdict in report["trust_ladder"].items():
        print(f"    {failure_class}: {verdict['stage']}")
        print(f"        episodes={verdict['episodes']} "
              f"precision={verdict['precision']} "
              f"mean_calibration_error={verdict['mean_calibration_error']}")

    retrieval = run_retrieval_eval()
    print(f"\n{RULE}\nRetrieval quality (labeled dataset, per chunking "
          f"strategy)\n{RULE}")
    for name, metrics in retrieval["strategies"].items():
        marker = " ← best" if name == retrieval["best"] else ""
        print(f"    {name:<10} chunks={metrics['chunks']:<3} "
              f"P@1={metrics['precision_at_1']:.3f}  "
              f"P@5={metrics['precision_at_5']:.3f}  "
              f"R@5={metrics['recall_at_5']:.3f}  "
              f"MRR={metrics['mrr']:.3f}{marker}")

    gate = report["release_gate"]
    retrieval_gate = retrieval["gate"]
    print(f"\n{RULE}\nRelease gates\n{RULE}")
    print(f"    {gate['criterion']} -> "
          f"{'PASSED' if gate['passed'] else 'FAILED'}")
    print(f"    {retrieval_gate['criterion']} -> "
          f"{'PASSED' if retrieval_gate['passed'] else 'FAILED'}")

    if args.json:
        report = {**report, "retrieval": retrieval}
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"report written to {args.json}")

    return 0 if (gate["passed"] and retrieval_gate["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
