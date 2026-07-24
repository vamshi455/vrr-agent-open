"""Environment config for vrr_agent_open (all-OSS, all-local).

Single source of truth for object names + connection settings. Nothing here has
side effects. The SAME code runs against a local docker-compose stack or any
Postgres/Unity-Catalog/MLflow endpoints — only env vars differ.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Governance / VRR domain constants (ported from the Databricks version).
DEFAULT_TARGET_VRR = 1.0
TARGET_BAND = (0.9, 1.1)          # green "on target" band (anomaly.py imports this)

# Catalog naming — mirrors the three-schema layout, now as Postgres schemas
# registered in Unity Catalog OSS as the governance catalog-of-record.
CATALOG = os.environ.get("VRR_CATALOG", "vrr")          # UC catalog name
RAW_SCHEMA = "vrr_raw"
CURATED_SCHEMA = "vrr_curated"
AGENT_SCHEMA = "vrr_agent"


@dataclass(frozen=True)
class Config:
    # --- PostgreSQL (data + compute + pgvector knowledge index) ---
    pg_dsn: str = os.environ.get(
        "VRR_PG_DSN", "postgresql://vrr:vrr@localhost:5432/vrr")
    # --- Unity Catalog OSS (governance catalog-of-record) ---
    uc_url: str = os.environ.get("VRR_UC_URL", "http://localhost:8080")
    uc_catalog: str = CATALOG
    # --- MLflow OSS (tracing / eval / registry) ---
    mlflow_uri: str = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    # --- LLM narrator (pluggable; Ollama by default, fully local) ---
    llm_provider: str = os.environ.get("VRR_LLM_PROVIDER", "ollama")
    llm_model: str = os.environ.get("VRR_LLM_MODEL", "llama3.1")
    llm_base_url: str = os.environ.get("VRR_LLM_BASE_URL", "http://localhost:11434")

    default_target_vrr: float = DEFAULT_TARGET_VRR

    def table(self, schema: str, name: str) -> str:
        return f"{schema}.{name}"          # Postgres schema-qualified


def load_config() -> Config:
    return Config()
