# AetherOps — zero-dependency targets (pure Python stdlib)

PY := PYTHONPATH=src python3

.PHONY: demo demo-pause demo-deny demo-change demo-live serve docker test eval eval-live judge-live web help

help:
	@echo "make demo         run the SEV2 vertical slice with auto-approval"
	@echo "make demo-pause   run until the approval gate and pause"
	@echo "make demo-deny    run, then deny at the approval gate"
	@echo "make demo-change  run the change-intelligence demo (risky vs benign)"
	@echo "make demo-live    run the SEV2 demo on a local Ollama model (free)"
	@echo "make test         run the full unittest suite"
	@echo "make web          build the React + TypeScript operator console (web/)"
	@echo "make eval         run the golden-scenario evaluation (release gate)"
	@echo "make eval-live    + live-model and semantic-retrieval tracks (free, local Ollama)"
	@echo "make judge-live   LLM-as-judge on a real local model + the deterministic anchor"

demo:
	$(PY) -m aetherops --approve

demo-pause:
	$(PY) -m aetherops

demo-deny:
	$(PY) -m aetherops --deny

demo-change:
	$(PY) -m aetherops --change

demo-live:
	$(PY) -m aetherops --approve --live

web:                            # build the React + TypeScript console (web/)
	cd web && [ -d node_modules ] || npm install
	cd web && npm run build
	cp web/dist/index.html src/aetherops/api/static/index.html
	cp web/dist/index.html site/index.html
	@echo "built web/ (React 19 + TS, Vite) -> API-served console + gh-pages"

serve:
	AETHEROPS_ALLOW_DEV_TOKEN=1 AETHEROPS_API_LOG=1 $(PY) -m aetherops.api

docker:
	docker build -t aetherops:latest .
	@echo "run: docker run --rm -p 8080:8080 -e AETHEROPS_API_TOKEN=choose-a-token aetherops:latest"

test:
	$(PY) -m unittest discover -s tests -v

eval:
	$(PY) -m aetherops.evals

eval-live:
	$(PY) -m aetherops.evals --live

judge-live:
	$(PY) -m aetherops.evals.judge_live
