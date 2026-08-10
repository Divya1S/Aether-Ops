"""Build a supervised fine-tuning corpus for the structured-output task.

The platform's planner is a *deterministic policy*: given a diagnosis it emits
a schema-valid JSON remediation plan drawn only from the vetted action
catalog. This module distills that policy into (prompt -> JSON) pairs so a
small open model can be LoRA-fine-tuned to reproduce it locally — cheaper
structured-output serving than routing every plan to a frontier tier.

Genuine, not synthetic-looking: the prompt is rendered from the *same*
production template (`prompts.registry` "plan") with the *same* catalog
rendering the live PlannerAgent uses, and every completion is validated
against the real PLAN_SCHEMA and catalog before it enters the corpus. Pure
stdlib — so `test_finetune_dataset.py` verifies the whole corpus in CI without
any ML dependency.

    python -m aetherops.integrations.finetune.dataset out.jsonl
"""
from __future__ import annotations

import json
import sys

from aetherops.agents.planner import (PLAN_SCHEMA, REQUIRED_ARGS, STEP_CATALOG)
from aetherops.core.schema import validate
from aetherops.prompts.registry import get_prompt

# The catalog block, rendered byte-for-byte as PlannerAgent renders it, so the
# training prompt matches what the model sees in production.
_CATALOG_LINES = "\n".join(
    f"- {name} (risk {entry['risk']}; args: {', '.join(REQUIRED_ARGS[name])})"
    for name, entry in sorted(STEP_CATALOG.items()))

# Parametric fixtures — real-looking services/revisions/commits so the model
# learns to copy grounded values into args rather than memorize one incident.
_SERVICES = ("payments-api", "checkout-web", "auth-service", "search-api",
             "ledger-worker", "notifications")
_REVISIONS = ("v2025.08.06-8", "v2025.08.07-3", "v2025.08.08-2",
              "v2025.07.30-5", "v2025.08.01-9")
_SHAS = ("c9a1f42", "f3d92ab", "a7710bd", "e2f04c9", "9b3d1aa", "44ce8f0")


def _memory_regression(service: str, revision: str, sha: str):
    hypothesis = (
        f"A recent deploy to {service} (commit {sha}) raised the container "
        f"memory footprint, driving repeated OOMKills [E1]; the previous "
        f"revision {revision} ran within limits [E2].")
    completion = {
        "self_estimate": 0.9,
        "rationale": (f"Roll {service} back to {revision} to stop the OOMKills, "
                      f"then open a revert PR for {sha}."),
        "steps": [
            {"action": "rollback_deployment",
             "args": {"service": service, "revision": revision}},
            {"action": "create_revert_pr", "args": {"sha": sha}}]}
    return hypothesis, completion


def _cert_expiry(service: str, revision: str, sha: str):
    hypothesis = (
        f"TLS handshakes to {service} fail with certificate-expired errors "
        f"[E1]; no deploy correlates with the onset [E2], so this is an "
        f"expiry, not a regression.")
    completion = {
        "self_estimate": 0.88,
        "rationale": f"Rotate the expired TLS certificate for {service}.",
        "steps": [
            {"action": "rotate_certificate", "args": {"service": service}}]}
    return hypothesis, completion


_GENERATORS = (_memory_regression, _cert_expiry)


def _validated(completion: dict) -> dict:
    """Fail loudly if a label would violate the production contract — a bad
    label must never reach the corpus."""
    errors = validate(completion, PLAN_SCHEMA)
    if errors:
        raise ValueError(f"label violates PLAN_SCHEMA: {errors}")
    for step in completion["steps"]:
        if step["action"] not in STEP_CATALOG:
            raise ValueError(f"uncataloged action {step['action']!r}")
        for arg in REQUIRED_ARGS[step["action"]]:
            if arg not in step["args"]:
                raise ValueError(f"missing arg {arg!r} for {step['action']}")
    return completion


def build_sft_examples() -> list[dict]:
    """Chat-format SFT examples: [{messages:[user prompt, assistant JSON]}]."""
    template = get_prompt("plan")
    examples: list[dict] = []
    for generate in _GENERATORS:
        for i, service in enumerate(_SERVICES):
            for j, revision in enumerate(_REVISIONS):
                sha = _SHAS[(i + j) % len(_SHAS)]
                hypothesis, completion = generate(service, revision, sha)
                _validated(completion)
                prompt = template.render(
                    catalog=_CATALOG_LINES, hypothesis=hypothesis,
                    service=service, previous_revision=revision, suspect=sha)
                examples.append({"messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant",
                     "content": json.dumps(completion, separators=(",", ":"))}]})
    return examples


def write_jsonl(path: str) -> int:
    examples = build_sft_examples()
    with open(path, "w", encoding="utf-8") as fh:
        for example in examples:
            fh.write(json.dumps(example) + "\n")
    return len(examples)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "aetherops_plan_sft.jsonl"
    count = write_jsonl(out)
    print(f"wrote {count} structured-output SFT examples -> {out}")
