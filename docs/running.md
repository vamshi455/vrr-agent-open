# Running vrr_agent_open — every step, commented

Two tiers: **(A) logic only** — instant, no dependencies; **(B) full stack** — needs
Docker (and Ollama for the LLM). Everything is local and free.

## A. Logic only (no Docker, no LLM) — start here

```bash
cd vrr-agent-open

# Install the project as an EDITABLE package + dev tools (pytest, ruff).
#   -e     editable: links to the source, so edits apply instantly (no reinstall)
#   "."    install THIS folder's package (read from pyproject.toml)
#   [dev]  also install the optional dev extras
# After this, `import vrr_agent_open` works anywhere — no PYTHONPATH needed.
pip install -e ".[dev]"

# Run the unit tests. These cover ONLY the pure logic in core/ (physics, recommend,
# anomaly, knowledge, approval) — no DB, no LLM, no Docker. Fast, free, your inner loop.
pytest -q
```

## B. Full local stack

```bash
# Start 3 background containers (needs Docker Desktop running):
#   postgres     — PostgreSQL 16 + pgvector (VRR data + knowledge vector index)
#   unitycatalog — Unity Catalog OSS (governance catalog) on :8080
#   mlflow       — MLflow tracking server on :5000
# First run auto-applies schema.sql (creates vrr_raw / vrr_curated / vrr_agent schemas).
docker compose up -d
docker compose ps                 # confirm all three are "running"/healthy

# Load synthetic VRR data: generates raw data and computes the curated tables
# (completion_contrib → pattern_vrr_monthly) with core/physics. Postgres now has VRR.
make seed

# Register the vrr_* schemas/tables in Unity Catalog OSS as the catalog-of-record
# (governance metadata + RBAC + lineage). Postgres stays the engine.
make register

# Launch the Streamlit UI at http://localhost:8501
#   Report tab        — VRR verdict / trend / attribution (reads vrr_curated)
#   Approval queue tab — move drafts draft→analyst→rm→site→executed (core/approval)
make app                          # Ctrl+C to stop

# (Optional) Ask the agent — needs a local LLM via Ollama:
ollama pull llama3.1              # narrator model (one-time)
ollama pull nomic-embed-text     # 768-dim embeddings for knowledge search
make agent                       # one question through the LangGraph tool loop + gate
                                 # traces stream to MLflow (:5000)

# (Optional) Ingest a PDF into the vector DB (pgvector):
mkdir -p knowledge_uploads && cp your_report.pdf knowledge_uploads/
python -m vrr_agent_open.pipeline.knowledge_ingest   # register → chunk+PII-redact→embed
# approve BEFORE it embeds (human relevance gate):
#   UPDATE vrr_agent.knowledge_registry SET status='approved' WHERE file_name='your_report.pdf';
```

## Teardown

```bash
docker compose down        # stop containers, KEEP data
docker compose down -v     # stop + DELETE data volumes (fresh start next time)
```

## Notes
- `make <target>` just runs the commented commands in the [Makefile](../Makefile).
- The `agent/graph.py` LLM+gate wiring and `agent/tools.py` `vrr_decompose` SQL are
  `TODO`-marked skeletons; the deterministic core and the PDF→pgvector path are complete.
- Ports in use: 5432 (Postgres), 8080 (Unity Catalog), 5000 (MLflow), 8501 (Streamlit).
