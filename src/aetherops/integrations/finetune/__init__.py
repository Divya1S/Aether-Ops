"""Fine-tuning pipeline for the structured-output task (optional extra).

`dataset.py` distills the platform's deterministic planning policy into a
supervised JSONL corpus — pure stdlib, so it is CI-verified. `train_lora.py`
fine-tunes a small open model on it with LoRA/PEFT (needs the `finetune`
extra + a GPU for a real run). See docs/18-fine-tuning.md.
"""
