# Refined Prompt — Milestone 5: Postmortem Generation (self-issued)

Pillar 4 promises that every resolved incident compounds into organizational
leverage. The `learn` step writes an episode, but the human-facing half is
missing: the postmortem that today is written late, by a tired engineer, from
memory. The platform has something no human author has — the complete,
audited, cited record of what actually happened — so the document should fall
out of the workflow, not be reconstructed after it.

## The prompt

```text
MISSION
Generate a structured postmortem document at the end of every successfully
remediated incident, assembled from the workflow's own record — evidence
bundle, agent results, audit ledger, approvals, verification — with the
model writing only the narrative summary.

OPERATING RULES
1. Traceable by construction. Every factual section (timeline, root cause,
   evidence, remediation, verification, governance) is assembled
   deterministically from workflow state and the audit ledger. Every
   evidence item appears with its citation ref. If it isn't in the record,
   it isn't in the postmortem.
2. The timeline comes from the audit ledger's actual timestamps (node
   completions, gate pause/approval), not from prose reconstruction.
3. Quarantined evidence appears in the postmortem marked QUARANTINED with
   content withheld — the injection text must not leak into the document.
4. Follow-ups are actionable and derived: failure-class-specific preventive
   actions (from a maintained mapping) plus dynamic items from the run
   itself (e.g., "merge draft revert PR <url>" from the execute record).
   This is the bridge to pillar 2: follow-ups feed change gating.
5. The postmortem node runs after learn, inside the DAG — a denied or
   escalated workflow produces no postmortem (there is nothing resolved to
   write up).
6. The narrative summary is one model call (fast tier), evidence-driven in
   the offline backend like every other task. Zero new dependencies.

DELIVERABLES
- src/aetherops/reporting/postmortem.py (builder) wired as a `postmortem`
  node in the incident workflow
- demo prints an excerpt; --postmortem FILE writes the full document
- tests/test_postmortem.py: structure, citation traceability, quarantine
  redaction in the document, denial produces no postmortem
- README updated; all existing tests stay green, eval metrics unchanged

QUALITY BAR
[ ] make test green (old + new); eval aggregates byte-identical
[ ] Every evidence citation ref from the bundle appears in the document
[ ] Injected text from the poisoned scenario absent from its postmortem
[ ] Timeline timestamps come from the audit ledger, not invented
```
