"""LoRA/PEFT supervised fine-tuning on the structured-output corpus.

Fine-tunes a small open causal LM to reproduce the platform's schema-valid
JSON plans (see `dataset.py`), so structured-output serving can run on a
cheap local adapter instead of a frontier tier. Standard PEFT: freeze the base
weights, train low-rank adapters, save a few-MB adapter.

    pip install "aetherops[finetune]"
    python -m aetherops.integrations.finetune.dataset plan_sft.jsonl
    python -m aetherops.integrations.finetune.train_lora plan_sft.jsonl \
        --base HuggingFaceTB/SmolLM2-135M-Instruct --out lora-plan-adapter

A real run wants a GPU (docs/18 has a Colab T4 recipe); the CPU smoke path in
the tests proves the pipeline emits an adapter. Needs the `finetune` extra;
the core never imports it.
"""
from __future__ import annotations

import argparse


def format_example(tokenizer, messages: list[dict]) -> str:
    """Render chat messages to a single training string — the model's own chat
    template when it has one, else a simple role-tagged fallback (e.g. GPT-2)."""
    if getattr(tokenizer, "chat_template", None):
        return tokenizer.apply_chat_template(messages, tokenize=False)
    body = "\n".join(f"<|{m['role']}|>\n{m['content']}" for m in messages)
    return body + (tokenizer.eos_token or "")


def train_lora(dataset_path: str,
               base_model: str = "HuggingFaceTB/SmolLM2-135M-Instruct",
               output_dir: str = "lora-plan-adapter", *,
               epochs: float = 1.0, max_steps: int = -1, batch_size: int = 1,
               learning_rate: float = 2e-4, max_length: int = 1024,
               lora_r: int = 8, lora_alpha: int = 16,
               target_modules: list[str] | None = None) -> str:
    """Fine-tune `base_model` on the JSONL corpus and save a LoRA adapter to
    `output_dir`. Returns the adapter directory."""
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              DataCollatorForLanguageModeling, Trainer,
                              TrainingArguments)

    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(base_model)

    # target_modules=None lets PEFT infer the attention projections for the
    # base architecture (c_attn for GPT-2, q_proj/v_proj for Llama/SmolLM).
    model = get_peft_model(model, LoraConfig(
        r=lora_r, lora_alpha=lora_alpha, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM", target_modules=target_modules))

    dataset = load_dataset("json", data_files=dataset_path, split="train")

    def tokenize(example):
        text = format_example(tokenizer, example["messages"])
        return tokenizer(text, truncation=True, max_length=max_length)

    dataset = dataset.map(tokenize, remove_columns=dataset.column_names)

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=output_dir, per_device_train_batch_size=batch_size,
            num_train_epochs=epochs, max_steps=max_steps,
            learning_rate=learning_rate, logging_steps=10,
            save_strategy="no", report_to=[]),
        train_dataset=dataset,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False))
    trainer.train()

    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="JSONL corpus from dataset.py")
    parser.add_argument("--base", default="HuggingFaceTB/SmolLM2-135M-Instruct")
    parser.add_argument("--out", default="lora-plan-adapter")
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--max-steps", type=int, default=-1)
    args = parser.parse_args()
    out = train_lora(args.dataset, base_model=args.base, output_dir=args.out,
                     epochs=args.epochs, max_steps=args.max_steps)
    print(f"saved LoRA adapter -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
