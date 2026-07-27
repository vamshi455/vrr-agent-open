# CLAUDE.md — vrr-agent-open

## What this repo is
Open-source, **fully-local** port of the Databricks "VRR Reasoning & Lineage agent"
(parent repo: `vamshi455/vrr-agent`, a Databricks/Mosaic AI project). Same trust model
— the LLM never computes; deterministic tools with provenance; faithfulness gate;
physics-computed safety-clamped recommendations; human approval; learned ρ feedback —
rebuilt on a free local stack. Design + feasibility: [docs/design.md](docs/design.md); data model: [docs/vrr_data_model.md](docs/vrr_data_model.md).

## Stack (all OSS, all local, zero cloud cost)
- **PostgreSQL + pgvector** — VRR data + compute + the knowledge vector index
- **LangGraph** — the agent tool-loop + faithfulness-gate node (replaces Mosaic ChatAgent)
- **Unity Catalog OSS** — governance catalog-of-record (RBAC + lineage; NOT a query engine)
- **MLflow OSS** — tracing / eval / registry
- **Streamlit** — report + approval UI
- **Ollama** — local LLM narrator/tool-caller (`qwen2.5:7b`) + `nomic-embed-text`
  embeddings (pluggable; everything still runs LLM-free)
- **docker-compose** — postgres+pgvector · unitycatalog · mlflow

## Current status (2026-07-24)
- ✅ **Deterministic core ported verbatim + tested**: `core/` = physics, recommend,
  anomaly, knowledge, approval, decompose, faithfulness, ids, audit. **88 tests pass** (`pytest -q`, no stack needed).
- ✅ **Seed + builder done**: `pipeline/seed.py` (pure, seeded generator → `vrr_raw` +
  `vrr_agent` memory/limits/precedent) and `pipeline/build.py` (`vrr_raw` →
  `vrr_curated` via `core.physics`; `make build` rebuilds curated alone). Verified
  end-to-end against a real Postgres: 4,745 contrib rows → 36 monthly rows, and
  `core.anomaly` fires all three rules — UNITY out_of_band+drift, HORIZON clean,
  MERIDIAN extrapolated_pvt (non-actionable).
- ✅ Postgres three-schema DDL (`pipeline/schema.sql`, + pgvector), **aligned to the
  production VRR data model** (`CreateVRR/src/vrr_sql_builder.sql`): volumes keyed by
  completion only, time-windowed `pattern_contribution_factor` + `pattern_pressure`,
  PVT by (completion, test_date, pressure), derived `Amount_Type`, HAVING gate,
  daily+monthly `pattern_vrr` with vol-weighted avg FVFs, and cumulative VRR.
  Reference + local deviations: [docs/vrr_data_model.md](docs/vrr_data_model.md).
- ✅ PDF → pgvector ingest path complete (`pipeline/knowledge_ingest.py`):
  register → human approve → chunk → PII-redact → embed → search. Flow doc:
  [docs/knowledge-flow.md](docs/knowledge-flow.md).
- ✅ docker-compose + Makefile + pyproject (installable) + docs (design, running, knowledge-flow).
- ✅ **Agent + workbench done** (see [docs/agent-flow.md](docs/agent-flow.md)):
  `core/decompose.py` (exact LMDI ΔVRR attribution) · `core/faithfulness.py` (gate) ·
  `agent/tools.py` (12 deterministic tools incl. `VRR_LINEAGE`, `VRR_AUDIT` recompute) ·
  `agent/analyst.py` (verify → attribute → classify → propose → draft) · `agent/chat.py`
  (intent router: deterministic by default, `agentic=True` lets the model drive the tool
  loop; both gated) · `agent/llm.py` (Ollama client, model auto-detect) ·
  `pipeline/anomaly_to_queue.py` (`make queue`) · 4-tab Streamlit app (portfolio, chart+date
  filter+draft, lineage+audit, role-gated approval writing `adjustment_history`) with
  the analyst chat always present under the tabs rather than as a tab of its own.
- ✅ **Evaluation harness** (design: [docs/evaluation.md](docs/evaluation.md); plain-English
  step-by-step: [docs/evaluation-walkthrough.md](docs/evaluation-walkthrough.md)): prompts extracted
  + versioned in the MLflow Prompt Registry (`make prompts`), 10-question expectation set
  (`data/evaluation/`), 6 deterministic trace scorers + 3 `make_judge` LLM judges
  (`evaluation/`), `make traces` / `make eval`, RETRIEVER spans for the pgvector path,
  pre-commit. First run found 2 routing gaps, truncated tool spans, 2 false-negative
  classes in the gate, and a figure with no tool span behind it — all fixed.
- 🔶 **Skeletons with `TODO` markers** (not yet wired):
  - `governance/uc_register.py` — column population from information_schema for lineage

## How to run
See [docs/running.md](docs/running.md) (every command commented). Fast path:
`pip install -e ".[dev]" && pytest -q` (logic only, no Docker). LLM chat: see [docs/agent-flow.md](docs/agent-flow.md).

**Evaluation rule** — always `make traces` immediately before `make eval`, and only ever
via the Makefile (`make eval` = `--eval-only`, which filters `tags.eval_case != ''`).
Running `evaluate_model.py` bare scores the last 50 traces of *any* origin (Streamlit,
`make agent`, older sets), so the run's `*/mean` denominators shift and two `vrr-eval` runs
stop being comparable; it also picks `model_id` from an arbitrary member of the trace set
when versions are mixed. Judges need Ollama up; `--no-judges` skips them (seconds, not
minutes). Where a judge and a deterministic scorer disagree, the deterministic one is right.

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
  Commit under Vamshi's name only — **no `Co-Authored-By: Claude` trailer**.
- All local + free — do NOT introduce cloud/billable resources.

## Next tasks (pick up here)
1. Outcome write-back: fill `adjustment_history.actual_post_vrr` after the next build and
   EMA-update ρ (`core.recommend.update_response_factor`) into `pattern_memory`.
2. `governance/uc_register.py` — populate columns from information_schema for lineage.
3. Verify end-to-end on Docker (`docker compose up` → seed → queue → app); so far the
   full path is verified against a local Postgres 18 cluster, not the compose stack.
4. Ingest a real PDF through `pipeline/knowledge_ingest.py` so the `general` chat
   intent is grounded in documents (embeddings model is installed).
