"""Prompt registry: every model-facing prompt is a versioned, checksummed
artifact (docs/17 M8, acceptance #11–#12).

Governance mechanics:
- Agents reference prompts by ID; the rendered prompt's identity
  (id@version) travels into every model-call audit record and into the
  generated postmortem.
- `prompt_lock.json` pins sha256(template) per id@version. A test recomputes
  the hashes — editing a template without bumping its version (and
  regenerating the lock) fails the build. Regenerate deliberately with:
      PYTHONPATH=src python3 -m aetherops.prompts.registry
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

LOCK_PATH = Path(__file__).with_name("prompt_lock.json")


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    version: str
    template: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()

    def render(self, **kwargs) -> str:
        return self.template.format(**kwargs)


_TEMPLATES = [
    PromptTemplate(
        "triage", "1.0.0",
        "[triage] alert: {title} service={service} urgency={urgency}"),
    PromptTemplate(
        "root_cause", "1.0.0",
        "[root_cause] Produce a causal hypothesis for this incident. "
        "Cite evidence as [En] for every causal claim. The bundle below "
        "is usually sufficient — prefer a cited hypothesis over refusal; "
        "only if no change event correlates with the symptom, begin your "
        "reply with exactly 'Insufficient evidence'. Otherwise end with "
        "exactly one line 'Recommended class: <class>' where <class> is "
        "one of: deploy-regression/memory, unclassified.\n"
        "Incident: {title}\nEvidence bundle:\n{digest}"),
    PromptTemplate(
        "plan", "1.0.0",
        "[plan] Propose remediation as Step Catalog actions only.\n"
        "Diagnosis: {hypothesis}\nCatalog: {catalog}\n"
        "Evidence bundle:\n{digest}"),
    PromptTemplate(
        "review", "1.0.0",
        "[review] checks_passed={passed}/{total} service={service}"),
    PromptTemplate(
        "verify", "1.0.0",
        "[verify] post-remediation p99={p99}ms, baseline={baseline}ms, "
        "oomkilled_last_10m={oomkilled}"),
    PromptTemplate(
        "change_risk", "1.0.0",
        "[change_risk] service={service} matched={matched} "
        "blast_radius={blast_radius} band={band} score={score}"),
    PromptTemplate(
        "postmortem", "1.0.0",
        "[postmortem] service={service} failure_class={failure_class} "
        "suspect={suspect} steps={steps} recovered_p99={p99}"),
]

REGISTRY: dict[str, PromptTemplate] = {t.id: t for t in _TEMPLATES}


def get_prompt(prompt_id: str) -> PromptTemplate:
    if prompt_id not in REGISTRY:
        raise KeyError(f"unknown prompt {prompt_id!r} "
                       f"(known: {sorted(REGISTRY)})")
    return REGISTRY[prompt_id]


def current_locks() -> dict[str, str]:
    return {f"{t.id}@{t.version}": t.checksum for t in _TEMPLATES}


def read_lock() -> dict[str, str]:
    with open(LOCK_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def write_lock() -> None:
    with open(LOCK_PATH, "w", encoding="utf-8") as fh:
        json.dump(current_locks(), fh, indent=2, sort_keys=True)
        fh.write("\n")


if __name__ == "__main__":
    write_lock()
    print(f"wrote {LOCK_PATH} ({len(REGISTRY)} prompts)")
