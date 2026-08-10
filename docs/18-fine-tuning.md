# 18 — Fine-tuning the structured-output task (LoRA/PEFT)

The planner is a **deterministic policy**: given a diagnosis it emits a
schema-valid JSON remediation plan drawn only from the vetted action catalog
(`agents/planner.py`, `PLAN_SCHEMA`). That makes it an ideal target for
**distillation** — teach a small open model to reproduce the policy so
structured-output serving can run on a cheap local LoRA adapter instead of
routing every plan to a frontier tier.

This is an **optional extra** (`pip install "aetherops[finetune]"`). The core
never imports it; CI verifies the dataset with no ML dependency.

## Pipeline

```
prompts.registry["plan"]  ─┐
agents.planner catalog     ├─► dataset.py ─► plan_sft.jsonl ─► train_lora.py ─► adapter/
scenario ground truth     ─┘   (stdlib, CI-verified)          (LoRA/PEFT)      (~few MB)
```

### 1. Dataset (`integrations/finetune/dataset.py`)

`build_sft_examples()` emits chat-format `(prompt → JSON)` pairs where:

- the **prompt** is rendered from the *same* production template
  (`prompts.registry` `"plan"`) with the *same* catalog block the live
  `PlannerAgent` renders — so training inputs match serving inputs byte-for-byte;
- the **completion** is a schema-valid plan whose grounded args (`service`,
  `revision`, `sha`) are copied from the prompt — validated against
  `PLAN_SCHEMA`, catalog membership, and required args *before* it enters the
  corpus (a bad label raises).

`test_finetune.py::TestStructuredOutputDataset` re-checks every example in CI
(pure stdlib), so the corpus can never drift from the production contract.

```bash
python -m aetherops.integrations.finetune.dataset plan_sft.jsonl
```

### 2. Train (`integrations/finetune/train_lora.py`)

Standard PEFT: freeze the base weights, train low-rank adapters on the
attention projections (PEFT infers `c_attn` for GPT-2, `q_proj`/`v_proj` for
Llama/SmolLM), save a few-MB adapter.

```bash
python -m aetherops.integrations.finetune.train_lora plan_sft.jsonl \
    --base HuggingFaceTB/SmolLM2-135M-Instruct --out lora-plan-adapter
```

## What is verified here vs. what needs a GPU

| Step | Verification |
|------|--------------|
| Dataset build + contract | **CI**, every example, no ML deps |
| LoRA pipeline runs end-to-end | **CPU smoke test** — `sshleifer/tiny-gpt2`, 2 steps, asserts an adapter is emitted (`test_finetune.py::TestLoraSmokeTrain`) |
| A useful adapter (real quality) | needs a GPU — the smoke test proves the *plumbing*, not model quality |

The smoke test deliberately trains a tiny random-init model for two steps: it
proves the tokenize → LoRA → Trainer → `save_pretrained` path works and emits
`adapter_config.json` + adapter weights. Producing a *useful* adapter is a full
run on a real base model.

## Colab T4 recipe (free tier)

```python
!pip install "transformers>=4.40" "peft>=0.11" "datasets>=2.0" "accelerate>=0.30"
# upload plan_sft.jsonl (or regenerate it from this repo)
from aetherops.integrations.finetune.train_lora import train_lora
train_lora("plan_sft.jsonl",
           base_model="HuggingFaceTB/SmolLM2-135M-Instruct",
           output_dir="lora-plan-adapter", epochs=3)
```

Evaluate the adapter the same way the platform evaluates any planner: generate
on held-out diagnoses and check the output parses and passes `PLAN_SCHEMA` +
catalog membership (the `dataset._validated` contract). Structured-output
accuracy = fraction of generations that are valid, catalog-grounded plans.

## Honesty note

The seed corpus is templated from the platform's own policy across
parametrized services/revisions/commits. It demonstrates the pipeline and the
contract; a production adapter should be augmented with **real** planner
traffic (the audit ledger already records every `model.call` with its
prompt id) and held-out evaluation before it serves.
