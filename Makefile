.PHONY: up down install test seed build queue register app agent lint

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

queue:         ## run the anomaly → action_queue job (drafts for human approval)
	python -m vrr_agent_open.pipeline.anomaly_to_queue

register:      ## register vrr schemas/tables/functions in Unity Catalog OSS
	python -m vrr_agent_open.governance.uc_register

app:           ## launch the Streamlit review + approval UI
	streamlit run src/vrr_agent_open/app/streamlit_app.py

agent:         ## run one agent question from the CLI
	python -m vrr_agent_open.agent.graph "Why is UNITY's VRR high in April 2026?"

lint:
	ruff check src tests
