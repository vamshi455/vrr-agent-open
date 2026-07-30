# Prefer the project venv when one exists, so `make` works without activating it (and is
# never hijacked by whatever `python` a conda base env happens to put first on PATH).
# Override explicitly with `make <target> PYTHON=...` to use a different interpreter.
PYTHON ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python)

.PHONY: up down install test seed build audit knowledge loaders chunks floor llm-check queue register api web web-build app agent \
        agent-model prompts traces eval judges lint

up:            ## start the local OSS stack (postgres+pgvector, unity catalog, mlflow)
	docker compose up -d

down:          ## stop the stack
	docker compose down

install:       ## editable install + dev deps
	pip install -e ".[dev]"

test:          ## run the pure off-DB unit tests (no stack needed)
	pytest -q

seed:          ## generate + load synthetic VRR data into Postgres
	$(PYTHON) -m vrr_agent_open.pipeline.seed

build:         ## rebuild vrr_curated from vrr_raw only (core.physics; no reseed)
	$(PYTHON) -m vrr_agent_open.pipeline.build

audit:         ## input-audit gate: verdict per pattern (DATA_ARTIFACT vs REAL_SIGNAL)
	$(PYTHON) -m vrr_agent_open.pipeline.input_audit

knowledge:     ## register docs in ./knowledge_uploads, then ingest the APPROVED ones
	$(PYTHON) -m vrr_agent_open.pipeline.knowledge_ingest

loaders:       ## load ./knowledge_uploads (or from=… / a URL) → List[Document] summary
	$(PYTHON) -m vrr_agent_open.pipeline.document_loaders $(or $(from),./knowledge_uploads)

chunks:        ## compare chunking strategies, scored by retrieval (recall@k, MRR)
	$(PYTHON) -m vrr_agent_open.pipeline.text_splitters

floor:         ## measure the retrieval similarity floor (answerable vs off-topic)
	$(PYTHON) scripts/calibrate_floor.py

llm-check:     ## can each provider do a completion AND tool calling? (add p=openai)
	$(PYTHON) scripts/check_llm.py $(p)

queue:         ## run the anomaly → action_queue job (drafts for human approval)
	$(PYTHON) -m vrr_agent_open.pipeline.anomaly_to_queue

agent-model:   ## log + register the agent as an MLflow model (alias: candidate)
	$(PYTHON) scripts/register_model.py

prompts:       ## push prompt templates to the MLflow Prompt Registry (alias: production)
	$(PYTHON) scripts/register_prompt.py

traces:        ## run the agent over data/evaluation questions, logging traces + expectations
	$(PYTHON) scripts/create_traces.py

eval:          ## score recent traces (deterministic scorers + LLM judges if a model is up)
	$(PYTHON) scripts/evaluate_model.py --eval-only

judges:        ## register the LLM judges server-side (add --start for automatic scoring)
	$(PYTHON) scripts/register_judge.py

register:      ## register vrr schemas/tables/functions in Unity Catalog OSS
	$(PYTHON) -m vrr_agent_open.governance.uc_register

api:           ## FastAPI backend (docs at http://localhost:8000/docs)
	$(PYTHON) -m uvicorn vrr_agent_open.api.main:app --reload --port 8000

web:           ## React dev server with hot reload (proxies /api to :8000)
	cd web && npm install && npm run dev

web-build:     ## build the React app; `make api` then serves it at :8000
	cd web && npm install && npm run build

app:           ## one-process workbench: build the UI, then serve it from FastAPI
	$(MAKE) web-build && $(PYTHON) -m uvicorn vrr_agent_open.api.main:app --port 8000

agent:         ## run one agent question from the CLI
	$(PYTHON) -m vrr_agent_open.agent.graph "Why is UNITY's VRR high in April 2026?"

lint:
	ruff check src tests
