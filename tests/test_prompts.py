"""Prompt registry: lockfile integrity (edit-without-version-bump fails the
build) and end-to-end prompt-version traceability (docs/17 #11–#12)."""
import unittest

from aetherops.core.types import WorkflowStatus
from aetherops.demo import build_demo_environment
from aetherops.prompts.registry import (REGISTRY, current_locks, get_prompt,
                                        read_lock)
from aetherops.workflows.incident_remediation import run_incident_remediation

EXPECTED_PROMPTS = {"triage", "root_cause", "plan", "review", "verify",
                    "change_risk", "postmortem"}


class TestRegistry(unittest.TestCase):
    def test_registry_covers_every_model_calling_task(self):
        self.assertEqual(set(REGISTRY), EXPECTED_PROMPTS)
        for template in REGISTRY.values():
            self.assertRegex(template.version, r"^\d+\.\d+\.\d+$")

    def test_render_substitutes_placeholders(self):
        rendered = get_prompt("triage").render(
            title="t", service="checkout-service", urgency="high")
        self.assertIn("[triage]", rendered)
        self.assertIn("service=checkout-service", rendered)

    def test_unknown_prompt_rejected(self):
        with self.assertRaises(KeyError):
            get_prompt("jailbreak")

    def test_lockfile_matches_templates(self):
        """Editing a template without bumping its version (and regenerating
        the lock) must fail: the checksum changes but the lock entry for
        id@version does not."""
        lock = read_lock()
        current = current_locks()
        self.assertEqual(
            current, lock,
            "prompt templates diverge from prompt_lock.json — bump the "
            "template's version and regenerate the lock deliberately: "
            "PYTHONPATH=src python3 -m aetherops.prompts.registry")


class TestTraceability(unittest.TestCase):
    def test_prompt_versions_flow_into_audit_and_postmortem(self):
        incident, env = build_demo_environment()
        paused, ctx = run_incident_remediation(incident, **env)
        done, ctx = run_incident_remediation(
            incident, **env, ctx=ctx,
            approvals={paused.pending_gate: True},
            checkpoint=paused.checkpoint)
        self.assertEqual(done.status, WorkflowStatus.SUCCEEDED)

        prompts_in_audit = {record.payload.get("prompt")
                            for record in env["audit"].records
                            if record.action == "model.call"}
        self.assertIn("triage@1.0.0", prompts_in_audit)
        self.assertIn("root_cause@1.0.0", prompts_in_audit)

        doc = done.checkpoint["postmortem"]["markdown"]
        self.assertIn("Prompt versions:", doc)
        self.assertIn("root_cause@1.0.0", doc)


if __name__ == "__main__":
    unittest.main()
