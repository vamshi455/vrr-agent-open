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
- **FastAPI + React** (Vite · TypeScript · Tailwind) — the workbench, branded as the
  fictional operator **Meridian Petroleum**: header with identity top-right, 4 views
  (Portfolio · Report · Lineage · **swim-lane approval board**) and a floating chatbot,
  over a 25-endpoint API. One type scale (`micro/label/body/sub/title/display`) and one
  semantic palette (signal/suspect/offtarget + a hue per approval stage) — no ad-hoc
  `text-[11px]`. Streamlit was retired 2026-07-30.
- **OAuth2 password grant + JWT bearer** (`api/auth.py`, since 2026-07-30) — writes and
  `POST /chat` need a token; reads stay public. **The role is a signed claim, never a
  request field** (an earlier cut trusted the body, which meant the caller picked its own
  role — fixed). Accounts live in `vrr_agent.app_user` (bcrypt), seeded by `make users`.
  Verified live: tampered token 401, wrong-role 403, correct role 200, and
  `adjustment_history.approved_by` = the authenticated subject. Deployment checklist
  (TLS, key handling, TTL, token storage, IdP swap) is README §12c; adversarial cases are
  in `tests/test_auth.py`. When touching this layer, do not add exploit strings or
  credentials to docs — describe the control, not the bypass.
- **Ollama** — local LLM narrator/tool-caller (`qwen2.5:7b`) + `nomic-embed-text`
  embeddings (pluggable; everything still runs LLM-free). `VRR_LLM_PROVIDER=openai|anthropic`
  switches the narrator to a hosted model (`agent/providers.py` translates tool calling into
  Anthropic's block format) — **billable, off by default, no key = unavailable**. Local
  stays the default and the tested path; `make llm-check` proves a provider can complete
  AND tool-call before you switch. Keys live in `.env` only (see `.env.example`).
- **docker-compose** — postgres+pgvector · unitycatalog · mlflow

## Current status (2026-07-31)
- ✅ **Deterministic core ported verbatim + tested**: `core/` = physics, recommend,
  anomaly, knowledge, approval, decompose, faithfulness, ids, audit. **159 tests pass**
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
  `agent/tools.py` (16 deterministic tools incl. `VRR_LINEAGE`, `VRR_AUDIT` recompute,
  `PATTERN_LAYOUT`) ·
  `agent/analyst.py` (verify → attribute → classify → propose → draft) · `agent/chat.py`
  (intent router: deterministic by default, `agentic=True` lets the model drive the tool
  loop; both gated) · `agent/graph.py` (**a real LangGraph `StateGraph`** since 2026-07-28:
  plan → tools → gate → repair/budget, append-only reducers on messages/trace/facts, gate on
  every path to END, `InMemorySaver` so `run(..., thread_id=…)` resumes; it was a hand-rolled
  loop before, with langgraph declared but never imported) · `agent/llm.py` (Ollama client) ·
  `pipeline/anomaly_to_queue.py` (`make queue`) · **`api/` FastAPI + `web/` React workbench**
  (portfolio, chart+attribution+draft, lineage+audit recompute, role-gated approval writing
  `adjustment_history`) with the analyst chat as a right-docked drawer beside every view,
  its transcript persisted per pattern in `vrr_agent.chat_history` (`agent/history.py`) and
  shared across users. `make api` · `make web` (dev) · `make app` (build + serve on :8000).
- ✅ **Every pattern draws itself** (2026-07-31): `core/pattern_layout.py` (pure) places
  wells from `pattern_contribution_factor` and names the canonical shape — five-spot,
  seven-spot, nine-spot, line drive, else irregular — and `web/components/PatternDiagram.tsx`
  renders it as SVG in the Report view beside a plain-English fluid balance ("for every
  100 barrels emptied, N were put back"). It flags the two things no other view shows: a
  completion shared across patterns, and extrapolated PVT. **It is a schematic and says
  so on its face** — this database has contribution factors, not coordinates, so distance
  from the injector is allocation and never feet. Drawing a convincing cross-section from
  invented well paths would put the most-trusted figure on screen behind the only
  computation with no provenance. Positions come from `core/`, not React, so the figure
  is unit-tested off-DB (`tests/test_pattern_layout.py`).

- ✅ **`make share` — public access through an ngrok tunnel, with the holes closed**
  (2026-07-31, `api/share.py`). The workbench was built for one laptop and **reads are
  unauthenticated by default** — pointing a tunnel at it as-is serves every pattern,
  trend, lineage and audit to whoever has the link, and `/api/health` hands over the
  Postgres host and MLflow URI. `VRR_SHARE=1` closes reads behind the bearer token
  (router-level dependency, so a read endpoint added later inherits it), redacts those
  hosts, prints a blunt startup banner, and makes a missing `VRR_JWT_SECRET` **fatal**
  rather than a warning. `VRR_PUBLIC_READS=1` re-opens reads — a separate, deliberate
  decision, never implied by turning sharing on. `make share` preflights ngrok's
  authtoken, the JWT secret, and that the running server really is in share mode (env is
  read once at import, so a running process cannot pick the flag up). Verified live:
  anonymous read 401 / valid token 200 / forged token 401 / writes and chat 401 even with
  reads public / health redacted. 10 tests in `tests/test_share.py`. **This is a demo
  posture, not a deployment** — the real checklist is README §12c.

- ✅ **Lineage is a graph, and the type scale is enforced** (2026-07-31).
  `web/components/LineageGraph.tsx` draws the derivation as a six-column DAG — four raw
  tables → `core.physics` → one row per completion → five reservoir terms → two sides →
  one VRR — with the value that actually flowed on every node, hover-to-trace upstream,
  and the `core.physics` formulas small in the bottom-left corner (the hovered term's
  formula lights up). The old text chain and formulas table are gone; the per-completion
  and roll-up tables fold away behind a disclosure. Type/contrast pass alongside it,
  measured not eyeballed: `text-slate-400` was **2.56:1** and the amber `suspect`
  **3.64:1**, both under the 4.5:1 body-text bar → slate-500 (4.76) and a new
  `suspect.text` #946118 (5.27); `code {font-size:.95em}` dragged inline code in an 11px
  caption to 10.45px → 1em; card/banner prose moved from `micro` (11) to `label` (12);
  chart sizes are named (`chartType`) instead of bare 11/12; and the schematic caps its
  width from its own viewBox so captions render at 11.4px on every pattern instead of
  varying with the pattern's extent. Verified in a browser: no HTML text under 11px and
  no horizontal scroll on any of the four views at 375px or 1500px.

- ✅ **UI audited against the `ui-ux-pro-max` rule set** (2026-07-31, skill installed at
  `~/.claude/skills/ui-ux-pro-max`). Fixed, in priority order: **no visible focus ring
  anywhere** (two `focus:` rules across ~19 buttons and four selects — now one
  `:focus-visible` rule in `index.css`, so the approval chain is usable without a mouse),
  **a desktop-only layout** (a fixed 224px rail beside the view at every width shaved the
  four metric cards to ~50px each below 900px, so VRR read as "1" — the shell now stacks
  under `lg:` and the header truncates instead of clipping), **emoji used as icons**
  (✅/⚠️/🛑/⚪ → `StatusIcon` SVG on `currentColor`; a screen reader was announcing "white
  heavy check mark" mid-sentence), and `cursor-pointer`. Take the skill's UX/accessibility
  rules; its *visual style* output is landing-page biased (it proposed
  `clamp(3rem,10vw,12rem)` display type and "massive whitespace" for a data-dense
  workbench) — the existing type scale and semantic palette stay.

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

## How to run — the whole setup, in order

Everything below assumes the repo root as cwd. `make` auto-selects `.venv/bin/python`,
so never invoke `python` directly: on this machine `python`/`python3.12` resolve to conda
base (`/opt/anaconda3`) or homebrew 3.14, neither of which has psycopg installed.

### A. First time on a machine (once)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # framework 3.12, NOT conda base
cp .env.example .env                                            # then edit — see §B for what matters
createuser -s vrr && createdb -O vrr vrr                         # local postgres@18 on 5432
psql vrr -c "CREATE EXTENSION IF NOT EXISTS vector"              # pgvector for the knowledge index
psql "postgresql://vrr:vrr@localhost:5432/vrr" -f src/vrr_agent_open/pipeline/schema.sql
make seed                                                        # 272,880 contrib → 1,440 monthly rows
make queue                                                       # anomalies → action_queue drafts
make users                                                       # API accounts; PRINTS the password it sets
make knowledge                                                   # register docs, then approve + re-run (see below)
```

`make knowledge` is two passes on purpose — registration marks documents
`pending_review` and the human approval gate is not automated:

```bash
psql vrr -c "UPDATE vrr_agent.knowledge_registry SET status='approved'"   # the review step
make knowledge                                                            # now it loads → chunks → redacts → embeds
```

### B. `.env` — set before starting anything

Env vars are read **once at import**, so a process started before `.env` existed (or
before you edited it) keeps the old values no matter how many times the browser is
refreshed. Edit first, start second; after editing, restart the API.

| Key | Value | Why |
|---|---|---|
| `VRR_PG_DSN` | `postgresql://vrr:vrr@localhost:5432/vrr` | repo default; matches §A |
| `MLFLOW_TRACKING_URI` | `http://localhost:5001` | **5000 is macOS AirPlay** and answers 403, so tracing silently stays off |
| `VRR_JWT_SECRET` | 48-byte urlsafe token | unset = a random key per process, so every restart invalidates every session |

### C. Every session (services, in this order)

```bash
brew services list | grep postgres      # 1. Postgres — usually already running
mlflow server --backend-store-uri sqlite:///mlflow.db \
  --default-artifact-root ./mlartifacts --host 127.0.0.1 --port 5001 &   # 2. MLflow on 5001
ollama serve &                          # 3. the narrator; optional — answers are computed without it
make app                                # 4. builds web/ and serves UI + API on :8000
```

Then open **http://localhost:8000** and sign in (reads work signed out; asking the agent
and approving need an account).

Developing the UI instead: `make api` (reload, :8000) + `make web` (Vite, :5173, proxies
`/api`).

### D. Check it came up

```bash
curl -s localhost:8000/api/health | python3 -m json.tool   # want tracing.enabled true, llm.available true
```

`⚠️ NOT TRACED` in the header or `"enabled": false` means the API process predates the
`MLFLOW_TRACKING_URI` you set — restart it, do not debug MLflow.

### E. When something is already on a port

`make app` failing with `address already in use` means a stale server holds :8000:

```bash
lsof -t -nP -iTCP:8000 -sTCP:LISTEN | xargs -r kill    # then `make app` again
```

### F. Evaluation

```bash
make traces && make eval      # ALWAYS in that order, ALWAYS via make — see the rule below
```

**Evaluation rule** — always `make traces` immediately before `make eval`, and only ever
via the Makefile (`make eval` = `--eval-only`, which filters `tags.eval_case != ''`).
Running `evaluate_model.py` bare scores the last 50 traces of *any* origin (the workbench,
`make agent`, older sets), so the run's `*/mean` denominators shift and two `vrr-eval` runs
stop being comparable; it also picks `model_id` from an arbitrary member of the trace set
when versions are mixed. Judges need Ollama up; `--no-judges` skips them (seconds, not
minutes). Where a judge and a deterministic scorer disagree, the deterministic one is right.

## Operating rules learned the hard way (2026-07-28 → 07-31)

These cost real time in earlier sessions. Check them before debugging anything else.

1. **Never run `python`/`python3.12` directly.** Both resolve to conda base or homebrew
   3.14 here, neither of which has psycopg. Use `make <target>`, which selects
   `.venv/bin/python`. A bare `pytest` has the same problem — `make test`.
2. **Env vars are read once at import.** A server started before `.env` existed keeps the
   old values forever; refreshing the browser cannot fix it. After editing `.env`,
   restart the process. Symptom: the header says NOT TRACED while MLflow is demonstrably
   up, or a token 401s that worked a minute ago.
3. **MLflow lives on 5001, never 5000.** macOS AirPlay Receiver holds 5000 and answers
   403; `agent/tracing.py` requires a 200 from `{uri}/health`, so tracing silently stays
   off. `docker-compose.yml` still publishes `5000:5000` and will not bind on this Mac.
4. **A stale server holds :8000 after a crash.** Kill it by pid from `lsof -t -nP
   -iTCP:8000 -sTCP:LISTEN` before `make app`, rather than guessing which terminal it is in.
5. **`web/dist` is gitignored**, so a fresh clone must run `make app` or `make web-build`
   once. `make api` alone serves the API and a blank page.
6. **Verify a write actually landed.** A string-replace on a line that does not exist is a
   silent no-op — that is how the JWT secret got "written" to `.env` without being there.
   Assert, or grep the file back.
7. **Look at the rendered thing, not the source.** Screenshotting the UI caught three bugs
   reading the code did not: unlabelled chart bars, a target band scrolled off the y-axis,
   and a quick-question whose wording routed it to the wrong intent. Rendering the mermaid
   caught a stale diagram. `mermaid-cli` and a puppeteer screenshot both work locally.
8. **The intent router keys on words.** "high"/"low" route to `explain`; "off target" is a
   PORTFOLIO phrase. Changing UI copy can silently change which code path answers.


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
1. **Outcome write-back** — fill `adjustment_history.actual_post_vrr` after the next build
   and EMA-update ρ (`core.recommend.update_response_factor`) into `pattern_memory`. This
   is the last open link in the closed loop: the function exists and is unit-tested, the
   job that feeds it observed outcomes does not.
2. **The 3 LLM judges return ~0.02 with "Not enough information provided"** — find out what
   `{{ trace }}` actually passes to `make_judge`. Until then their means are unmeasured,
   not bad (see the 🔶 above).
3. **A `status` intent** — "are you connected to an LLM?", "which model?", "how many
   patterns?" currently fall through to `explain` and get answered as if they were VRR
   questions. `/api/health` already has the facts; the answer should be deterministic,
   because a model guessing about its own configuration is a bad failure mode.
4. `governance/uc_register.py` — populate columns from information_schema for lineage.
5. Verify end-to-end on Docker (`docker compose up` → seed → queue → app). Note
   `docker-compose.yml` publishes MLflow on `5000:5000`, which will not bind on this Mac —
   change it to `5001:5000` when doing this.
6. Ingest a REAL (non-synthetic) PDF so the knowledge path is exercised on prose nobody
   wrote for it; re-run `make floor` afterwards, since the threshold is corpus-dependent.
