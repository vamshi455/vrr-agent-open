# AGENTS.md

For general project context, conventions, and the full local run guide, see `CLAUDE.md`
and `README.md`. `CLAUDE.md` is written for a **macOS laptop**; the section below records
where this **Cursor Cloud Linux VM** differs.

## Cursor Cloud specific instructions

This environment is **Ubuntu 24.04 (x86_64)**, not macOS. The dependency-install steps in
`CLAUDE.md` §A have already been run and their results are baked into the VM snapshot:
a Python 3.12 virtualenv at `.venv`, PostgreSQL 16 + pgvector installed locally, the `vrr`
role/database created, `schema.sql` applied, and the DB seeded (`make seed` / `make queue` /
`make users`). `web/` npm deps are installed. A `.env` exists (gitignored) with a generated
`VRR_JWT_SECRET` and the default `VRR_PG_DSN=postgresql://vrr:vrr@localhost:5432/vrr`.

The startup script only refreshes dependencies (`.venv` pip install + `npm install`). It does
**not** start services. Each session, start what you need:

- **PostgreSQL is NOT auto-started on boot.** Start it first, or every DB call fails:
  `sudo pg_ctlcluster 16 main start` (check with `sudo pg_lsclusters` → status `online`).
  Postgres runs as a **local apt cluster here, not Docker** — ignore `CLAUDE.md`'s
  docker-compose path and the macOS "port 5000 = AirPlay" notes; neither applies on Linux.
  The seeded data lives in the snapshot, so you normally do **not** need to re-run `make seed`.
- **Run the workbench:** `make app` builds `web/` and serves the API + UI on
  http://localhost:8000. `web/dist` is gitignored but present in the snapshot; `make app`
  rebuilds it anyway. For UI hot-reload dev, use `make api` (:8000) + `make web` (:5173).
  Long-running servers should be started under `tmux` so they outlive a single command.
- **Sign-in:** demo accounts `analyst.demo` / `rm.demo` / `site.demo` / `steward.demo`,
  password `vrr-demo` (all reads are public; writes and `POST /chat` need a signed-in token).
  Approving/advancing a card records the JWT subject as `stage_by`/`approved_by` — verified
  live by advancing a draft card as `analyst.demo`.

Non-obvious gotchas:

- **The LLM is unavailable by default** — Ollama is *not* installed. `/api/health` reports
  `llm.available: false`, and this is expected. The app is designed to run **LLM-free**:
  deterministic intents (portfolio, report, lineage, help, status) and every computed number
  work without a model. Only free-text/agentic chat and the knowledge/RAG path need a model.
  To enable them, install Ollama and pull `qwen2.5:7b` + `nomic-embed-text` (large downloads,
  CPU-only here); `make knowledge` and knowledge upload embedding also need the embedder.
- **MLflow is optional and not running** — `/api/health` shows `tracing.enabled: false` and
  the header reads "NOT TRACED". Fine for developing/running the app. Only start MLflow
  (on :5001, per `.env`) if you need `make traces && make eval`.
- **`make lint` fails with `ruff: No such file or directory`** — the `lint` target calls a
  bare `ruff`, which is only inside the venv, not on PATH. Run it directly instead:
  `.venv/bin/ruff check src tests`. (It currently reports pre-existing findings; that is the
  repo's existing state, unrelated to environment setup.)
- **Tests need no services:** `make test` runs 425+ pure off-DB unit tests. `make` always
  selects `.venv/bin/python`, so use `make <target>` rather than a bare `python`/`pytest`.
