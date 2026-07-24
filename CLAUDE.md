# CLAUDE.md — vrr-agent-open

## What this repo is
Open-source, **fully-local** port of the Databricks "VRR Reasoning & Lineage agent"
(parent repo: `vamshi455/vrr-agent`, a Databricks/Mosaic AI project). Same trust model
— the LLM never computes; deterministic tools with provenance; faithfulness gate;
physics-computed safety-clamped recommendations; human approval; learned ρ feedback —
rebuilt on a free local stack. Design + feasibility: [docs/design.md](docs/design.md).

## Stack (all OSS, all local, zero cloud cost)
- **PostgreSQL + pgvector** — VRR data + compute + the knowledge vector index
- **LangGraph** — the agent tool-loop + faithfulness-gate node (replaces Mosaic ChatAgent)
- **Unity Catalog OSS** — governance catalog-of-record (RBAC + lineage; NOT a query engine)
- **MLflow OSS** — tracing / eval / registry
- **Streamlit** — report + approval UI
- **Ollama** — local LLM narrator + `nomic-embed-text` embeddings (pluggable)
- **docker-compose** — postgres+pgvector · unitycatalog · mlflow

## Current status (2026-07-24)
- ✅ **Deterministic core ported verbatim + tested**: `core/` = physics, recommend,
  anomaly, knowledge, approval. **38 tests pass** (`pytest -q`, no stack needed).
- ✅ Postgres three-schema DDL (`pipeline/schema.sql`, + pgvector).
- ✅ PDF → pgvector ingest path complete (`pipeline/knowledge_ingest.py`):
  register → human approve → chunk → PII-redact → embed → search. Flow doc:
  [docs/knowledge-flow.md](docs/knowledge-flow.md).
- ✅ docker-compose + Makefile + pyproject (installable) + docs (design, running, knowledge-flow).
- 🔶 **Skeletons with `TODO` markers** (not yet wired):
  - `agent/tools.py` — `vrr_decompose` SQL over Postgres (port LMDI from parent repo)
  - `agent/graph.py` — wire `_gate` (faithfulness) + confirm Ollama tool-calling shape
  - `pipeline/seed.py` — **does not exist yet**; `make seed` will fail until written
    (needs: synthetic raw → core.physics → curated → Postgres). This is the next task.
  - `governance/uc_register.py` — column population from information_schema for lineage

## How to run
See [docs/running.md](docs/running.md) (every command commented). Fast path:
`pip install -e ".[dev]" && pytest -q` (logic only, no Docker).

## Key decision — "Unity Catalog on Postgres" feasibility
Feasible as a **catalog-of-record**, NOT query enforcement. UC OSS governs registered
assets (RBAC + lineage + credential vending) but does not intercept live Postgres
queries in OSS (Lakehouse Federation is Databricks-only). The agent resolves names +
permission from UC, then executes against Postgres. Full reasoning in docs/design.md.

## Conventions (carried from the parent repo)
- Be concise; lead with the answer.
- `core/` stays pure (no I/O) so it unit-tests off-DB. Nothing imports `pipeline`.
- Git: commit + push directly to `main`, no feature branches; **after pushing, always
  reply with the GitHub link** (repo: https://github.com/vamshi455/vrr-agent-open).
- All local + free — do NOT introduce cloud/billable resources.

## Next tasks (pick up here)
1. Write `pipeline/seed.py` so `make seed` works (synthetic → core.physics → Postgres).
2. Port `vrr_decompose` SQL into `agent/tools.py`; wire the faithfulness gate in `graph.py`.
3. Add the Postgres `vrr_build` runner (raw → completion_contrib → pattern_vrr_monthly).
4. Verify end-to-end: `docker compose up` → seed → app → agent.
