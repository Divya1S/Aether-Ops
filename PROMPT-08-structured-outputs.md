# Refined Prompt — Milestone 8: Structured Outputs, Prompt Registry, OWASP Mapping (self-issued)

Three docs-vs-code honesty gaps remain from [docs/17](docs/17-ai-engineer-gap-analysis.md):
docs/02 specifies schema-validated agent outputs the code doesn't enforce;
prompts are inline string literals with no versioning (the "structured
prompts… governance" language in 3/7 postings); and the security work speaks
its own vocabulary instead of OWASP LLM Top 10, the language interviewers
use (LLM06 Excessive Agency being the named senior-vs-mid discriminator).

## The prompt

```text
MISSION
Make agent outputs schema-validated, prompts versioned and traceable
artifacts, and the existing defenses legible in OWASP LLM Top 10 (2025)
vocabulary — all stdlib, all deterministic.

OPERATING RULES
1. A JSON-Schema-subset validator in pure stdlib (~100 lines): type
   (incl. union lists), required, properties, additionalProperties, enum,
   items. Documented as a deliberate subset with jsonschema named as the
   production choice. Beware bool-is-int in Python.
2. Every agent declares an output_schema; the agent-node wrapper validates
   after every run. A violation triggers ONE semantic retry (re-run — for
   live models the retry is meaningful), then PermanentError escalation.
   Proven by a test that injects an agent emitting malformed output and
   counts both attempts.
3. Prompts move to a registry: id + semver version + template, rendered by
   name. A lockfile pins sha256(template) per id@version; a test recomputes
   — editing a template without bumping its version fails the build. The
   offline backend keys off prompt markers, so template text moves
   verbatim.
4. Traceability end to end: model-call audit records carry
   prompt_id@version; the generated postmortem's governance section lists
   the prompt versions that produced it.
5. OWASP LLM Top 10 (2025) mapping section in docs/05: every implemented
   defense mapped to its ID (LLM01 injection quarantine, LLM02 redaction,
   LLM05 output validation, LLM06 Step Catalog + gates, LLM08 attributed
   retrieval, LLM09 citation mandate + calibration, LLM10 rate limits +
   metering), with honest "partial/documented-only" labels where true, and
   the attack tests annotated with the IDs they exercise.

DELIVERABLES
- core/schema.py validator + per-agent output schemas + wrapper enforcement
- prompts/registry.py + prompt_lock.json + regeneration entry point
- gateway TaskProfile carries prompt identity into audit; postmortem lists it
- docs/05 OWASP section; test_security_agents annotated with LLM01/LLM06
- tests: validator cases, malformed-output retry-then-escalate, lockfile
  integrity, registry coverage of every model-calling agent
- README updated; every existing metric byte-identical

QUALITY BAR
[ ] make test green; make eval aggregates unchanged; demos byte-equivalent
    (prompts render to the exact same strings)
[ ] Acceptance criteria #10, #11, #12, #19 from docs/17 all pass
```
