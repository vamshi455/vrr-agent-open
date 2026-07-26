.PHONY: up down install test seed build audit knowledge queue register app agent
        prompts traces eval judges lint

up:            ## start the local OSS stack (postgres+pgvector, unity catalog, mlflow)
	docker compose up -d

down:          ## stop the stack
	docker compose down

install:       ## editable install + dev deps
	pip install -e ".[dev]"

test:          ## run the pure off-DB unit tests (no stack needed)
	pytest -q

seed:          ## generate + load synthetic VRR data into Postgres
	python -m vrr_agent_open.pipeline.seed

build:         ## rebuild vrr_curated from vrr_raw only (core.physics; no reseed)
	python -m vrr_agent_open.pipeline.build

audit:         ## input-audit gate: verdict per pattern (DATA_ARTIFACT vs REAL_SIGNAL)
	python -m vrr_agent_open.pipeline.input_audit

knowledge:     ## register PDFs in ./knowledge_uploads, then ingest the APPROVED ones
	python -m vrr_agent_open.pipeline.knowledge_ingest

queue:         ## run the anomaly → action_queue job (drafts for human approval)
	python -m vrr_agent_open.pipeline.anomaly_to_queue

prompts:       ## push prompt templates to the MLflow Prompt Registry (alias: production)
	python scripts/register_prompt.py

traces:        ## run the agent over data/evaluation questions, logging traces + expectations
	python scripts/create_traces.py

eval:          ## score recent traces (deterministic scorers + LLM judges if a model is up)
	python scripts/evaluate_model.py --eval-only

judges:        ## register the LLM judges server-side (add --start for automatic scoring)
	python scripts/register_judge.py

register:      ## register vrr schemas/tables/functions in Unity Catalog OSS
	python -m vrr_agent_open.governance.uc_register

app:           ## launch the Streamlit review + approval UI
	streamlit run src/vrr_agent_open/app/streamlit_app.py

agent:         ## run one agent question from the CLI
	python -m vrr_agent_open.agent.graph "Why is UNITY's VRR high in April 2026?"

lint:
	ruff check src tests
