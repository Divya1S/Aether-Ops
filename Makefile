# AetherOps — zero-dependency targets (pure Python stdlib)

PY := PYTHONPATH=src python3

.PHONY: demo demo-pause demo-deny demo-change test eval help

help:
	@echo "make demo         run the SEV2 vertical slice with auto-approval"
	@echo "make demo-pause   run until the approval gate and pause"
	@echo "make demo-deny    run, then deny at the approval gate"
	@echo "make demo-change  run the change-intelligence demo (risky vs benign)"
	@echo "make test         run the full unittest suite"
	@echo "make eval         run the golden-scenario evaluation (release gate)"

demo:
	$(PY) -m aetherops --approve

demo-pause:
	$(PY) -m aetherops

demo-deny:
	$(PY) -m aetherops --deny

demo-change:
	$(PY) -m aetherops --change

test:
	$(PY) -m unittest discover -s tests -v

eval:
	$(PY) -m aetherops.evals
