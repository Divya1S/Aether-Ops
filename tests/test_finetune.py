"""Fine-tuning pipeline: the SFT corpus is verified in CI with no ML
dependency; the LoRA smoke-train runs only when the `finetune` extra is
installed (it downloads a tiny model and takes a few seconds)."""
import importlib.util
import json
import os
import tempfile
import unittest

from aetherops.agents.planner import PLAN_SCHEMA, REQUIRED_ARGS, STEP_CATALOG
from aetherops.core.schema import validate
from aetherops.integrations.finetune.dataset import (build_sft_examples,
                                                     write_jsonl)


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


class TestStructuredOutputDataset(unittest.TestCase):
    """Always-on (pure stdlib): every label obeys the production contract."""

    def test_corpus_is_schema_valid_and_grounded(self):
        examples = build_sft_examples()
        self.assertGreaterEqual(len(examples), 40)
        for example in examples:
            user, assistant = example["messages"]
            self.assertEqual(user["role"], "user")
            self.assertIn("[plan]", user["content"])      # real prod template
            plan = json.loads(assistant["content"])       # completion is JSON
            self.assertEqual(validate(plan, PLAN_SCHEMA), [])
            for step in plan["steps"]:
                self.assertIn(step["action"], STEP_CATALOG)   # catalog-grounded
                for arg in REQUIRED_ARGS[step["action"]]:
                    self.assertIn(arg, step["args"])
                    # grounded values are copied from the prompt, not invented
                    self.assertIn(str(step["args"][arg]), user["content"])

    def test_writes_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sft.jsonl")
            count = write_jsonl(path)
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertEqual(count, len(lines))
            self.assertTrue(all(json.loads(line)["messages"] for line in lines))


@unittest.skipUnless(
    _has("torch") and _has("peft") and _has("transformers") and _has("datasets"),
    "finetune extra not installed")
class TestLoraSmokeTrain(unittest.TestCase):
    """Proves the LoRA/PEFT pipeline runs end-to-end and emits an adapter."""

    def test_pipeline_emits_a_lora_adapter(self):
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from aetherops.integrations.finetune.train_lora import train_lora
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "sft.jsonl")
            out = os.path.join(tmp, "adapter")
            write_jsonl(data)
            train_lora(data, base_model="sshleifer/tiny-gpt2",
                       output_dir=out, max_steps=2, batch_size=2)
            produced = os.listdir(out)
            self.assertIn("adapter_config.json", produced)
            self.assertTrue(any(f.startswith("adapter_model") for f in produced))


if __name__ == "__main__":
    unittest.main()
