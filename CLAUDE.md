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
  embeddings (pluggable; everything still runs LLM-free). `VRR_LLM_PROVIDER=openai|anthropic`
  switches the narrator to a hosted model (`agent/providers.py` translates tool calling into
  Anthropic's block format) — **billable, off by default, no key = unavailable**. Local
  stays the default and the tested path; `make llm-check` proves a provider can complete
  AND tool-call before you switch. Keys live in `.env` only (see `.env.example`).
- **docker-compose** — postgres+pgvector · unitycatalog · mlflow

## Current status (2026-07-24)
- ✅ **Deterministic core ported verbatim + tested**: `core/` = physics, recommend,
  anomaly, knowledge, approval, decompose, faithfulness, ids, audit. **107 tests pass**
  (`pytest -q`, no stack needed — incl. `tests/test_graph.py`, which walks every path
  through the LangGraph loop with the model and Postgres stubbed).
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
- ✅ **Knowledge/RAG path complete and ingested** (`pipeline/knowledge_ingest.py`):
  register → human approve → load → chunk → PII-redact → embed → search. The 4 synthetic
  demo PDFs are ingested (**35 chunks**) and both paths verified end to end. Flow doc:
  [docs/knowledge-flow.md](docs/knowledge-flow.md).
  - `pipeline/document_loaders.py` — pdf/txt/md/html/docx/csv/folder/URL → `List[Document]`,
    metadata normalised so every chunk stays citable (`make loaders`).
  - `pipeline/text_splitters.py` — fixed vs recursive vs semantic, each scored by
    `retrieval_check()` recall@k + MRR (`make chunks`). **Chunking is judged by retrieval,
    never by eye**: recall@2 is fixed 0.33 · **recursive 1.00 (the default)** · semantic
    0.67 — semantic over-splits short procedure text.
  - **The agent now abstains.** `search()` applies a similarity floor and
    `chat._knowledge_answer` returns "I don't know" WITHOUT calling the model when nothing
    clears it. The floor is measured, not guessed (`make floor`): answerable questions
    score ≥0.671, off-topic ≤0.564 → **0.62**. This mattered — nomic-embed-text scores
    unrelated text at 0.40-0.56, so the intuitive 0.35 admitted everything and the abstain
    path never fired. `rulebook_unanswerable` in the eval set guards it.
- ✅ docker-compose + Makefile + pyproject (installable) + docs (design, running, knowledge-flow).
- ✅ **Agent + workbench done** (see [docs/agent-flow.md](docs/agent-flow.md)):
  `core/decompose.py` (exact LMDI ΔVRR attribution) · `core/faithfulness.py` (gate) ·
  `agent/tools.py` (15 deterministic tools incl. `VRR_LINEAGE`, `VRR_AUDIT` recompute) ·
  `agent/analyst.py` (verify → attribute → classify → propose → draft) · `agent/chat.py`
  (intent router: deterministic by default, `agentic=True` lets the model drive the tool
  loop; both gated) · `agent/graph.py` (**a real LangGraph `StateGraph`** since 2026-07-28:
  plan → tools → gate → repair/budget, append-only reducers on messages/trace/facts, gate on
  every path to END, `InMemorySaver` so `run(..., thread_id=…)` resumes; it was a hand-rolled
  loop before, with langgraph declared but never imported) · `agent/llm.py` (Ollama client) ·
  `pipeline/anomaly_to_queue.py` (`make queue`) · 4-tab Streamlit app (portfolio, chart+date
  filter+draft, lineage+audit, role-gated approval writing `adjustment_history`) with
  the analyst chat as a right-docked collapsible drawer beside every tab, its transcript
  persisted per pattern in `vrr_agent.chat_history` (`agent/history.py`) and shared across users.
- ✅ **Evaluation harness** (design: [docs/evaluation.md](docs/evaluation.md); plain-English
  step-by-step: [docs/evaluation-walkthrough.md](docs/evaluation-walkthrough.md)): prompts extracted
  + versioned in the MLflow Prompt Registry (`make prompts`), 10-question expectation set
  (`data/evaluation/`), 6 deterministic trace scorers + 3 `make_judge` LLM judges
  (`evaluation/`), `make traces` / `make eval`, RETRIEVER spans for the pgvector path,
  pre-commit. First run found 2 routing gaps, truncated tool spans, 2 false-negative
  classes in the gate, and a figure with no tool span behind it — all fixed. 11 cases now,
  incl. `rulebook_unanswerable` (the negative RAG case: the agent must abstain).
- 🔶 **The 3 LLM judges execute but their verdicts are NOT usable yet.** They had never
  run at all: `make_judge(base_url=…)` was pointed at `{ollama}/v1`, but MLflow POSTs to
  that URL verbatim instead of appending `/chat/completions`, so every judge died on a
  silent `404` and `make eval` reported only the 6 deterministic scorers while claiming 9.
  Fixed in `evaluation/custom_judges.py` (full endpoint + a dummy `OPENAI_API_KEY`, which
  litellm requires even though Ollama ignores it). They now return, but score ~0.02 with
  rationales like *"Not enough information provided"* — the trace content is not reaching
  the judge. **Treat `provenance_cited` / `grounded_in_documents` / `decision_complete`
  as unmeasured** (the deterministic `numbers_grounded` says 0.98 over the same traces,
  and per the rule below the deterministic scorer wins). Open: find out what
  `{{ trace }}` actually passes to the judge.
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
- **"What is X?" questions** (a make target, a script, a module) get a crisp answer: one
  line saying what it maps to, then ≤5 bullets on what it does and why it exists in *this*
  project. No walkthroughs, no code dumps, no caveats unless they change what to run.
- **Every shell block gets inline `#` comments** — one per line, saying what that line does
  and why it is needed here (same style as [docs/running.md](docs/running.md)). Never hand
  over a bare stack of commands to copy blindly.
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
