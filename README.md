# vrr_agent_open

**Open-source, fully-local VRR Reasoning & Lineage agent — LangGraph + PostgreSQL/pgvector + Unity Catalog OSS + MLflow. No cloud, no cost.**

![Python](https://img.shields.io/badge/python-3.10+-blue)
![LangGraph](https://img.shields.io/badge/agent-LangGraph-1C3C3C)
![PostgreSQL](https://img.shields.io/badge/data-PostgreSQL%20%2B%20pgvector-336791)
![Unity Catalog](https://img.shields.io/badge/governance-Unity%20Catalog%20OSS-red)
![MLflow](https://img.shields.io/badge/tracing-MLflow%20OSS-0194E2)
![License](https://img.shields.io/badge/license-Apache%202.0-green)

An open-source port of a Databricks VRR agent. Same trust model — **the LLM never
computes**; every number comes from a deterministic tool with provenance, a
faithfulness gate rejects narration the math doesn't support, recommendations are
physics-computed and safety-clamped, humans approve, and executed outcomes feed a
learned per-pattern response factor ρ — rebuilt on a free local stack.

> **Why this exists:** it shows the *architecture* (deterministic core + agentic
> reasoning + governance + closed-loop learning) is portable off any single vendor.
> The deterministic heart (`core/`) is copied **verbatim** from the Databricks
> version — provider-agnostic Python with its unit tests.

## Stack

| Concern | Tool |
|---|---|
| Data + compute | **PostgreSQL** (VRR is pure SQL) |
| Knowledge index | **pgvector** (same DB) |
| Agent loop | **LangGraph** (tool nodes + faithfulness-gate node) |
| Governance | **Unity Catalog OSS** (catalog-of-record: RBAC + lineage) |
| Tracing / eval / registry | **MLflow OSS** |
| UI | **Streamlit** (report + approval) |
| LLM | **Ollama** (local; pluggable) |

## Quick start (all local, zero cost)

```bash
git clone https://github.com/vamshi455/vrr_agent_open.git && cd vrr_agent_open
pip install -e ".[dev]"

pytest -q                 # 1. pure-logic tests — no stack needed
docker compose up -d      # 2. Postgres+pgvector · Unity Catalog OSS · MLflow
make seed                 # 3. synthetic VRR data (core/physics computes curated)
make register             # 4. register schemas/tables in Unity Catalog OSS
make app                  # 5. Streamlit report + approval queue
make agent                # or ask one question from the CLI
```

## Repository layout

```
vrr_agent_open/
├── docker-compose.yml        # postgres+pgvector · unitycatalog · mlflow (local)
├── pyproject.toml            # real installable package (pip install -e .)
├── Makefile                  # up / seed / register / test / app / agent
├── src/vrr_agent_open/
│   ├── config.py             # Postgres DSN · UC url · MLflow uri · LLM
│   ├── core/                 # PURE logic, ported verbatim + tested
│   │   ├── physics.py        #   PVT ladder + reservoir volumes
│   │   ├── recommend.py      #   ρ-calibrated safety-clamped recommendation + EMA
│   │   ├── anomaly.py        #   detection rules + input veto + draft assembly
│   │   ├── knowledge.py      #   chunking + PII redaction
│   │   └── approval.py       #   draft→analyst→rm→site state machine
│   ├── pipeline/schema.sql   # the three-schema Postgres DDL (+ pgvector)
│   ├── agent/                # LangGraph graph + tools over psycopg
│   ├── app/                  # Streamlit report + approval
│   └── governance/           # Unity Catalog OSS registration
├── tests/                    # the ported pure-logic tests
└── docs/design.md            # architecture + the UC-on-Postgres feasibility verdict
```

## Governance note (honest)

Unity Catalog OSS is a **catalog**, not a query engine — it governs registered
assets (RBAC + lineage + credential vending) but does **not** intercept live
PostgreSQL queries in OSS (Lakehouse Federation is Databricks-only). So here UC is
the **catalog-of-record**: the agent resolves names + permission from UC, then
executes against Postgres. Full reasoning + the alternative all-Delta design:
[docs/design.md](docs/design.md).

## Status

Scaffold + verbatim deterministic core (with tests) + Postgres schema + LangGraph /
tools / app / governance skeletons. `TODO` markers in code flag the remaining ports
(decompose SQL, gate wiring, pgvector ingest, Postgres build runner). Contributions
welcome — Apache-2.0.
